"""Persistent adapter server and request dispatch."""

from pathlib import Path
import fcntl
import json
import os
import select
import shutil
import signal
import socket
import tempfile
import time

from .changes import read_changelog as adapter_changelog, record_start as record_adapter_start
from .checkpoint import run as run_checkpoint
from .combat import run as run_combat
from .game import Game
from .hold import run as run_hold
from .map import terrain_neighborhood
from .metrics import progress
from .paths import ROOT
from .presentation import emit_observation
from .ranged import run as run_ranged
from .recovery import run as run_recovery
from .spells import run as run_cast


STARTUP_CHANGELOG = None


def read_request(conn):
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            raise ValueError("Incomplete request")
        data += chunk
        if len(data) > 65536:
            raise ValueError("Request too large")
    return json.loads(data)


def serve(session, config, foreground=False, output_args=None):
    launch_changelog = STARTUP_CHANGELOG or adapter_changelog(ROOT)
    os.umask(0o077)
    session.mkdir(parents=True, exist_ok=True)
    with (session / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime = Path(tempfile.mkdtemp(prefix="crawl-agent-"))
        def terminate(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, terminate)
        game = None
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(runtime / "control.sock"))
            server.listen(4)
            (session / "runtime.json").write_text(json.dumps({
                "socket": str(runtime / "control.sock"), "pid": os.getpid(),
                "game_socket": str(runtime / f"{config['name']}:agent.sock")}))
            (session / "config.json").write_text(json.dumps(config, indent=2))
            game = Game(session, runtime, config)
            game.attach()
            if game.settled and game.process.poll() is None and (game.state.player.get('hp') is not None or game.state.ui):
                try:
                    record_adapter_start(session, json.loads((session / 'runtime.json').read_text()),
                                         launch_changelog, adapter_changelog(ROOT))
                except (OSError, ValueError) as exc:
                    game.actions.write({'event': 'adapter_changelog_error', 'error': str(exc)})
            if foreground:
                stream = output_args.stream if output_args else "default"
                with (session / f"observation-{stream}.lock").open("a") as output_lock:
                    fcntl.flock(output_lock, fcntl.LOCK_EX)
                    emit_observation(session, game.observe(), full=True,
                                     text=output_args.text if output_args else False,
                                     stream=stream,
                                     snapshot_tags=output_args.snapshot_tags if output_args else False,
                                     session_name=output_args.session if output_args else "default")
            stopping = False
            while not stopping:
                ready, _, _ = select.select([server, *game.readers()], [], [], 1)
                if game.terminal in ready:
                    game.drain_terminal()
                if game.sock in ready:
                    game.drain()
                    game.settled = False
                if server not in ready:
                    continue
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(65)
                    request_started = None
                    try:
                        request = read_request(conn)
                        request_started = time.monotonic()
                        game.request_settle_ms = 0
                        if request["op"] == "stop":
                            stopped = game.close()
                            game = None
                            stopping = True
                            response = {"ok": True, "stopped": True,
                                        "session": str(session), **stopped}
                        elif request["op"] == "observe":
                            game.settle_input()
                            response = game.observe()
                        elif request["op"] == "checkpoint":
                            game, response = run_checkpoint(game, runtime, config, Game)
                            stopping = game is None
                        elif request["op"] == "act":
                            keys = request["keys"]
                            if (not isinstance(keys, list) or not keys
                                    or len(keys) > 256
                                    or any(type(key) is not int for key in keys)):
                                raise ValueError("Expected 1 to 256 integer keycodes")
                            action = request.get("action", "keys")
                            if not isinstance(action, str) or len(action) > 64:
                                raise ValueError("Expected an action label of at most 64 characters")
                            response = game.act(keys, action)
                        elif request["op"] == "combat":
                            response = run_combat(game, request.get("policy", {}))
                        elif request["op"] == "hold":
                            response = run_hold(game, request.get("policy", {}))
                        elif request["op"] == "cast":
                            response = run_cast(game, request.get("policy", {}))
                        elif request["op"] == "ranged":
                            response = run_ranged(game, request.get("policy", {}))
                        elif request["op"] in ("recover", "wait-for"):
                            response = run_recovery(game, request["op"], request.get("policy", {}))
                        elif request["op"] == "inspect":
                            response = game.inspect(request["dx"], request["dy"])
                        elif request["op"] == "terrain":
                            game.settle()
                            response = game.observe()
                            response["terrain_neighborhood"] = terrain_neighborhood(game.state, request["dx"], request["dy"])
                        elif request["op"] == "target":
                            response = game.target(request["dx"], request["dy"])
                        elif request["op"] == "acknowledge-threat":
                            game.state.acknowledge_threat()
                            game.persist_awareness()
                            game.actions.write({"event": "acknowledge_threat", **progress(game.state.player)})
                            response = game.observe()
                        else:
                            raise ValueError("Unknown operation")
                    except (ValueError, KeyError, OSError, RuntimeError) as exc:
                        response = {"error": str(exc)}
                    if request_started is not None:
                        response["timing"] = {
                            "request_id": request.get("request_id"),
                            "daemon_ms": round((time.monotonic() - request_started) * 1000, 3),
                            "settle_ms": round(game.request_settle_ms, 3) if game else None,
                            "finished_at": round(time.time(), 6)}
                        if game:
                            game.actions.write({"event": "request", "op": request.get("op"),
                                                **response["timing"],
                                                "settled": game.settled,
                                                "settle_reason": getattr(game, "settle_reason", None),
                                                **progress(game.state.player)})
                    try:
                        conn.sendall(json.dumps(response, ensure_ascii=False).encode() + b"\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
        finally:
            if game is not None:
                game.close()
            server.close()
            (session / "runtime.json").unlink(missing_ok=True)
            shutil.rmtree(runtime)
