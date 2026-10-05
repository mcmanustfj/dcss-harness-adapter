"""Control-socket requests, policy validation, and response timing."""

import fcntl
import json
import socket
import sys
import time
import uuid

from . import CLI_STARTED
from .combat import policy as combat_policy
from .hold import policy as hold_policy
from .presentation import emit_observation
from .ranged import policy as ranged_policy
from .recovery import policy as recovery_policy
from .spells import policy as cast_policy


def guarded_policy_request(operation, options):
    """Validate locally, then omit optional fields with no requested effect.

    Keep numeric zero (e.g. max_threat=0); do not drop requested features to
    accommodate an older daemon. Its own defaults cover absent empty options.
    """
    if operation == "combat":
        combat_policy(options)
    elif operation == "ranged":
        ranged_policy(options)
    elif operation == "cast":
        cast_policy(options)
    elif operation == "hold":
        hold_policy(options)
    else:
        recovery_policy(operation, options)
    return {"op": operation, "policy": {key: value for key, value in options.items()
            if value is not False and value is not None and value != []
            and not (key == "max_casts" and value == 1)}}


def compatibility_error(message, result):
    """Explain known legacy pre-execution rejections; never resend input."""
    operation = message.get("op")
    if operation not in ("combat", "recover", "wait-for", "ranged", "cast", "hold", "checkpoint"):
        return result
    expected = "Unknown combat policy option" if operation == "combat" else "Unknown recovery policy option"
    if operation == "ranged":
        expected = "Unknown ranged policy option"
    if operation == "cast":
        expected = "Unknown cast policy option"
    if operation == "hold":
        expected = "Unknown hold policy option"
    original = result.get("error")
    if original not in (expected, "Unknown operation") or "player" in result:
        return result
    options = sorted(message.get("policy", {}))
    optional = ["--" + key.replace("_", "-") for key, value in message.get("policy", {}).items()
                if value is True or isinstance(value, list) and value]
    detail = (f"requested {operation} policy" if original == expected else f"{operation} command")
    if optional and original == expected:
        detail += " (optional flags: " + ", ".join(optional) + ")"
    return {**result, "error": f"The running daemon rejected the {detail}. "
            "A controller-coordinated save/stop/start is required to load support. "
            "This rejection occurred before gameplay input; no automatic retry was sent.",
            "error_code": "restart_required", "operation": operation,
            "requested_policy_options": options, "daemon_error": original}


def output_request(session, message, args):
    # Serialize both the request and its cursor update. Separate streams let
    # an inspector observe without consuming the controller's output.
    started = time.monotonic()
    request_id = uuid.uuid4().hex
    with (session / f"observation-{args.stream}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        locked = time.monotonic()
        try:
            request_args = {"timeout": 90} if message["op"] == "checkpoint" else {}
            result = compatibility_error(message, request(session, {**message, "request_id": request_id}, **request_args))
        except (OSError, RuntimeError, ValueError) as exc:
            # Keep correlation data even when delivery is ambiguous. Never
            # reissue the request, including on an empty/truncated reply.
            result = {"error": str(exc)}
        received = time.monotonic()
        timing = result.setdefault("timing", {})
        timing.update(request_id=request_id,
                      cli_setup_ms=round((started - CLI_STARTED) * 1000, 3),
                      lock_wait_ms=round((locked - started) * 1000, 3),
                      rpc_ms=round((received - locked) * 1000, 3),
                      received_at=round(time.time(), 6))
        if "daemon_ms" in timing:
            timing["outside_daemon_ms"] = round(max(0, timing["rpc_ms"] - timing["daemon_ms"]), 3)
        emit_observation(session, result, args.full, args.text, args.stream,
                         snapshot_tags=getattr(args, "snapshot_tags", False),
                         session_name=getattr(args, "session", "default"))
        # Written only after stdout has flushed. This cannot measure when a
        # caller captures/displays that output, nor guarantee they received it.
        record = {**timing, "op": message["op"], "stream": args.stream,
                  "emitted_at": round(time.time(), 6),
                  "emit_ms": round((time.monotonic() - received) * 1000, 3),
                  "client_ms": round((time.monotonic() - started) * 1000, 3)}
        try:
            with (session / "timings.jsonl").open("a", encoding="utf-8") as metrics:
                metrics.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"Cannot write timing log: {exc}", file=sys.stderr)
    return 1 if "error" in result or (message["op"] == "checkpoint" and not result.get("checkpoint", {}).get("verified")) else 0


def request(session, message, timeout=65):
    try:
        runtime = json.loads((session / "runtime.json").read_text())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(runtime["socket"])
            conn.sendall(json.dumps(message).encode() + b"\n")
            with conn.makefile("rb") as stream:
                line = stream.readline()
            if not line:
                raise RuntimeError("Adapter exited; see adapter.log")
            return json.loads(line)
    except (FileNotFoundError, ConnectionRefusedError):
        raise RuntimeError("No running session; use start first") from None
