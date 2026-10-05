"""Public command-line interface for the DCSS harness adapter."""

from pathlib import Path
import argparse
import fcntl
import json
import re
import subprocess
import sys
import time

from . import client
from .client import guarded_policy_request, output_request
from .combat import ENEMY_STATUS_ALLOWANCES
from .daemon import serve
from .doctor import diagnose
from .keys import ACTIONS, DIRECTIONS, keycode
from .metrics import action_stats
from .paths import (
    ROOT,
    add_config_argument,
    add_source_argument,
    binary_path,
    configured_paths,
    read_settings,
)


def main():
    parser = argparse.ArgumentParser(prog="crawl-agent", description=__doc__)
    add_config_argument(parser)
    parser.add_argument("--session", default="default", help="Session name")
    parser.add_argument("--session-dir", type=Path, help="Explicit session directory")
    parser.add_argument("--text", action="store_true", help="Print a readable summary instead of JSON")
    parser.add_argument("--full", action="store_true",
                        help="Return a full observation, including unchanged fields")
    parser.add_argument("--stream", default="default",
                        help="Independent output cursor (default: default)")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Check the configured binary's reported version and WebTiles support")
    doctor.add_argument("--binary", type=Path, help="External WebTiles Crawl binary (or CRAWL_BINARY)")
    add_source_argument(doctor)
    start = commands.add_parser("start", help="Start or resume a saved game")
    start.add_argument("--binary", type=Path,
                       help="External WebTiles Crawl binary (or CRAWL_BINARY)")
    add_source_argument(start)
    start.add_argument("--name", default="Agent", help="Character name; agents should pass MODEL-EFFORT for new games")
    start.add_argument("--species", default="Minotaur")
    start.add_argument("--background", default="Berserker")
    start.add_argument("--weapon", default="hand axe")
    start.add_argument("--seed", type=int)
    start.add_argument("--settle-ms", type=int, default=75)
    start.add_argument("--timeout", type=float, default=5)
    start.add_argument("--no-auto-more", action="store_true",
                       help="Leave message pagination for manual acknowledgment")
    start.add_argument("--foreground", action="store_true",
                       help="Keep this command alive (for process-managing harnesses)")
    start.add_argument("--log-events", action="store_true",
                       help="Also log full protocol traffic to events.jsonl (large)")
    commands.add_parser("observe", help="Read state; may acknowledge plain message pages")
    terrain_query = commands.add_parser("terrain", help="Read named terrain in a 3x3 neighborhood; sends no game key")
    terrain_query.add_argument("--dx", type=int, default=0)
    terrain_query.add_argument("--dy", type=int, default=0)
    commands.add_parser("acknowledge-threat", help="Clear unseen-attacker uncertainty after assessing it; sends no game key")
    inspect = commands.add_parser("inspect", help="Open a visible square's description; Escape closes it")
    inspect.add_argument("--dx", required=True, type=int, help="Offset east (negative west)")
    inspect.add_argument("--dy", required=True, type=int, help="Offset south (negative north)")
    target = commands.add_parser("target", help="Move the active targeting cursor without firing")
    target.add_argument("--dx", required=True, type=int)
    target.add_argument("--dy", required=True, type=int)
    hold = commands.add_parser("hold", help="Bounded stationary waits for named allies or active Ramparts")
    hold.add_argument("--monster-id", required=True, type=int)
    hold.add_argument("--allow-friendly-id", action="append", type=int, default=[])
    hold.add_argument("--allow-status", action="append", default=[])
    hold.add_argument("--max-actions", type=int, default=6)
    hold.add_argument("--max-seconds", type=float, default=10)
    hold.add_argument("--stop-distance", type=int, default=2)
    hold.add_argument("--min-hp-percent", type=float, default=85)
    hold.add_argument("--max-threat", type=int, default=1)
    cast = commands.add_parser("cast", help="One chosen spell and optional verified Searing Ray continuations")
    cast.add_argument("--spell-letter", required=True)
    cast.add_argument("--expect-name", required=True)
    cast.add_argument("--monster-id", type=int)
    cast.add_argument("--max-failure-percent", required=True, type=int)
    cast.add_argument("--min-mp-after", required=True, type=int)
    cast.add_argument("--prepare-only", action="store_true")
    cast.add_argument("--channel-actions", type=int, default=0)
    cast.add_argument("--max-casts", type=int, default=1, help="Repeat only Freeze on the same foe; at most 32")
    cast.add_argument("--max-seconds", type=float, default=10)
    cast.add_argument("--allow-status", action="append", default=[])
    ranged = commands.add_parser("ranged", help="Prepare or submit one explicitly selected ranged attack")
    resource = ranged.add_mutually_exclusive_group(required=True)
    resource.add_argument("--wand-letter", help="Letter in the wands namespace")
    resource.add_argument("--current-quiver", help="Exact public quiver_desc for the already-wielded launcher")
    ranged.add_argument("--weapon-letter", help="Already-wielded launcher letter in the equipment namespace")
    ranged.add_argument("--expect-name", required=True, help="Exact current inventory name, including charges/enchantment")
    ranged.add_argument("--monster-id", required=True, type=int)
    ranged.add_argument("--prepare-only", action="store_true")
    ranged.add_argument("--allow-area", action="store_true", help="Allow other hostile monsters in the public preview")
    ranged.add_argument("--allow-self", action="store_true", help="Allow the player in the public preview")
    ranged.add_argument("--allow-friendly", action="store_true", help="Allow friendly/neutral monsters in the public preview")
    ranged.add_argument("--max-seconds", type=float, default=10)
    commands.add_parser("stats", help="Summarize actions.jsonl, including stopped sessions")
    commands.add_parser("checkpoint", help="Native save/exit/reload and verify progress; keeps this adapter process")
    combat = commands.add_parser("combat", help="Bounded adjacent melee; checks after each key")
    combat.add_argument("--max-actions", type=int, default=8)
    combat.add_argument("--max-seconds", type=float, default=10)
    combat.add_argument("--min-hp-percent", type=float, default=85)
    combat.add_argument("--max-threat", type=int, default=1)
    combat.add_argument("--allow-status", action="append", default=[], help="Exact public status light label; repeatable")
    combat.add_argument("--assessed-distant-id", action="append", type=int, default=[],
                        help="Explicitly assessed distant sighting to tolerate for this invocation; repeatable; stops on adjacency")
    combat.add_argument("--allow-enemy-status", action="append", default=[],
                        choices=sorted(ENEMY_STATUS_ALLOWANCES), help="Permit this assessed enemy debuff, including application/expiry; repeatable")
    recover = commands.add_parser("recover", help="Bounded native rest toward full HP/MP")
    recovery_target = recover.add_mutually_exclusive_group()
    recovery_target.add_argument("--clear-statuses", action="store_true", help="Also wait for all allowed statuses to expire")
    recovery_target.add_argument("--clear-status", action="append", default=[], help="Wait for this selected status to expire; must also be allowed; repeatable")
    wait_for = commands.add_parser("wait-for", help="Bounded waits for an explicitly assessed visible foe")
    wait_for.add_argument("--monster-id", required=True, type=int)
    wait_for.add_argument("--assessed-ranged", action="store_true",
                          help="Explicitly tolerate the chosen foe's ranged/reaching weapon hint; all event/damage/status stops remain")
    wait_for.add_argument("--distance", type=int, default=2)
    wait_for.add_argument("--min-hp-percent", type=float, default=85)
    wait_for.add_argument("--max-threat", type=int, default=1)
    for guarded in (combat, recover, wait_for):
        guarded.add_argument("--allow-cloud", action="append", choices=["poison"], default=[],
                             help="Explicitly assessed poison cloud only; does not infer worn resistance")
        guarded.add_argument("--allow-water-with-flight", action="store_true",
                             help="Assessed stationary water allowance requiring current public Fly; changes stop")
    for guarded in (recover, wait_for):
        guarded.add_argument("--allow-status", action="append", default=[],
                             help="Exact public light label, or text when no light exists; repeatable; wait-for still stops on changes")
        guarded.add_argument("--max-actions", type=int, default=6)
        guarded.add_argument("--max-seconds", type=float, default=10)
    act = commands.add_parser("act", help="Send keys and return the resulting state")
    choice = act.add_mutually_exclusive_group(required=True)
    choice.add_argument("--keys", help="Literal key sequence; shell quoting applies")
    choice.add_argument("--key", help="Single named key, e.g. Escape or Ctrl-S")
    choice.add_argument("--action", choices=sorted(ACTIONS))
    choice.add_argument("--move", choices=sorted(DIRECTIONS))
    commands.add_parser("stop", help="Disconnect and stop; retain saves and logs")
    watch = commands.add_parser("watch", help="Serve a tiles spectator in the browser")
    add_source_argument(watch)
    watch.add_argument("--port", type=int, default=8080)
    watch.add_argument("--all", dest="all_sessions", action="store_true",
                       help="Show all running sibling sessions in one lobby")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.session):
        parser.error("Session names may contain letters, numbers, _ and -")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.stream):
        parser.error("Stream names may contain letters, numbers, _ and -")
    session = (args.session_dir or ROOT / ".crawl-agent" / args.session).resolve()
    try:
        # Shutdown and saved statistics need neither paths nor observation output
        # settings; keep them available even if the settings file is broken.
        settings = {} if args.command in ("stop", "stats") else read_settings(args.config)
        args.snapshot_tags = settings.get("snapshot_tags", False)
        if args.command == "doctor":
            result = diagnose(*configured_paths(args, settings))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "watch":
            from .watch import watch
            watch(session, args.port, all_sessions=args.all_sessions, source=configured_paths(args, settings)[1])
            return 0
        if args.command == "start":
            if not 10 <= args.settle_ms <= 5000 or not 0 < args.timeout <= 60:
                raise ValueError("settle-ms must be 10..5000 and timeout 0..60")
            if args.timeout < args.settle_ms / 1000:
                raise ValueError("timeout must exceed the quiet window")
            args.binary = binary_path(*configured_paths(args, settings))
            if "\n" in args.weapon or "\r" in args.weapon:
                raise ValueError("weapon must be one line")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,19}", args.name):
                raise ValueError("name must be 3..20 letters, digits or hyphens, starting with a letter or digit")
            session.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Check the lifetime lock without sending input to another game.
            with (session / "lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Session already running; use observe") from None
            config = {key: getattr(args, key) for key in (
                "name", "species", "background", "weapon", "seed", "settle_ms", "timeout",
                "log_events", "no_auto_more")}
            config["binary"] = str(args.binary.resolve())
            if args.foreground:
                serve(session, config, foreground=True, output_args=args)
                return 0
            with (session / "adapter.log").open("ab") as log:
                process = subprocess.Popen([
                    sys.executable, "-m", "dcss_harness.worker",
                    "--session-dir", str(session), json.dumps(config)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, cwd=ROOT)
            deadline = time.monotonic() + 65
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Startup failed; see {session / 'adapter.log'} "
                                       f"and {session / 'crawl.log'}")
                try:
                    runtime = json.loads((session / "runtime.json").read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    runtime = None
                # The worker publishes its PID only after listening. Ignore old
                # or partially written metadata; send the first request once.
                if isinstance(runtime, dict) and runtime.get("pid") == process.pid:
                    return output_request(session, {"op": "observe"}, args)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Adapter startup timed out; inspect logs and observe before retrying")
                time.sleep(.02)
        elif args.command == "act":
            if args.key is not None:
                keys = [keycode(args.key)]
            else:
                text = (args.keys if args.keys is not None else
                        ACTIONS[args.action] if args.action else DIRECTIONS[args.move])
                keys = [ord(char) for char in text]
            if not keys or len(keys) > 256:
                raise ValueError("Send between 1 and 256 keys")
            action = (args.action if args.action else
                      f"move:{args.move}" if args.move else
                      f"key:{args.key}" if args.key is not None else "keys")
            return output_request(session, {"op": "act", "keys": keys,
                                            "action": action}, args)
        elif args.command == "observe":
            return output_request(session, {"op": "observe"}, args)
        elif args.command == "acknowledge-threat":
            return output_request(session, {"op": "acknowledge-threat"}, args)
        elif args.command in ("inspect", "terrain"):
            return output_request(session, {"op": args.command, "dx": args.dx,
                                            "dy": args.dy}, args)
        elif args.command == "target":
            return output_request(session, {"op": "target", "dx": args.dx,
                                            "dy": args.dy}, args)
        elif args.command == "hold":
            return output_request(session, guarded_policy_request("hold", {
                key: getattr(args,key) for key in ("monster_id", "allow_friendly_id", "allow_status",
                    "max_actions", "max_seconds", "stop_distance", "min_hp_percent", "max_threat")}), args)
        elif args.command == "cast":
            return output_request(session, guarded_policy_request("cast", {
                key: getattr(args, key) for key in ("spell_letter", "expect_name", "monster_id",
                    "max_failure_percent", "min_mp_after", "prepare_only", "channel_actions",
                    "max_seconds", "allow_status", "max_casts")}), args)
        elif args.command == "ranged":
            return output_request(session, guarded_policy_request("ranged", {
                key: getattr(args, key) for key in ("wand_letter", "weapon_letter", "current_quiver",
                    "expect_name", "monster_id", "prepare_only", "allow_area", "allow_self",
                    "allow_friendly", "max_seconds")}), args)
        elif args.command in ("recover", "wait-for"):
            fields = ("max_actions", "max_seconds", "allow_status", "allow_water_with_flight", "allow_cloud") + (("clear_statuses", "clear_status")
                if args.command == "recover" else ("monster_id", "distance", "min_hp_percent", "max_threat", "assessed_ranged"))
            return output_request(session, guarded_policy_request(args.command, {
                key: getattr(args, key) for key in fields}), args)
        elif args.command == "combat":
            return output_request(session, guarded_policy_request("combat", {
                key: getattr(args, key) for key in ("max_actions", "max_seconds",
                    "min_hp_percent", "max_threat", "allow_status", "allow_enemy_status", "assessed_distant_id", "allow_water_with_flight", "allow_cloud")}), args)
        elif args.command == "stats":
            result = action_stats(session / "actions.jsonl")
        elif args.command == "checkpoint":
            return output_request(session, {"op": "checkpoint"}, args)
        else:
            result = client.request(session, {"op": args.command})
        print(result["summary"] if args.text and "summary" in result else
              json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if "error" in result else 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
