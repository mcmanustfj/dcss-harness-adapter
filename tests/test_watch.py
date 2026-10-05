"""Spectator discovery and real WebTiles lobby lifecycle regressions."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import unittest

from dcss_harness.watch import LiveLobbyMetadata, SessionLobby, discover_sessions, prepare_viewer_assets


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ["CRAWL_SOURCE"]).expanduser().resolve() if os.environ.get("CRAWL_SOURCE") else None


class MetadataTests(unittest.TestCase):
    def test_live_deltas_milestones_and_generation_reset(self):
        class Base:
            interesting_info = ("xl", "char", "place", "turn", "dur", "god", "title")

            def _on_socket_message(self, message):
                pass

            def lobby_entry(self):
                return {"id": 7, "xl": "stale", "dur": "stale"}

        class Handler(LiveLobbyMetadata, Base):
            pass

        handler = Handler()
        self.assertIsNone(handler.lobby_entry()["xl"])
        handler._on_socket_message('{"msg":"player","name":"","xl":1,"place":"Dungeon","depth":0}\n')
        self.assertIsNone(handler.lobby_entry()["xl"])
        self.assertIsNone(handler.lobby_entry()["place"])
        handler._on_socket_message(
            '{"msg":"player","name":"Agent","xl":1,"place":"D","depth":1,"turn":0,"time":0}\n'
            '*{"msg":"milestone","xl":"1","place":"D:1","dur":"0","turn":"0"}\n')
        self.assertEqual(handler.lobby_entry()["dur"], "0")
        handler._on_socket_message('{"msg":"player","xl":2,"depth":2,"turn":30,"time":315}\n')
        entry = handler.lobby_entry()
        self.assertEqual((entry["xl"], entry["place"], entry["turn"]), (2, "D:2", 30))
        self.assertEqual((entry["dur"], entry["duration_turn"]), ("0", "0"))
        handler._on_socket_message('*{"msg":"milestone","status":"milestone_only","turn":"9"}\n')
        self.assertEqual(handler.lobby_entry()["turn"], 30)
        handler._on_socket_message('{"msg":"player","place":"Temple","depth":0}\n')
        self.assertEqual(handler.lobby_entry()["place"], "Temple")
        handler._on_socket_message('*{"msg":"milestone","status":"chargen"}\n')
        self.assertIsNone(handler.lobby_entry()["xl"])
        self.assertIsNone(handler.lobby_entry()["dur"])

    @unittest.skipUnless(shutil.which("node") and SOURCE, "needs Node and external CRAWL_SOURCE")
    def test_rendering_unknown_zero_last_report_and_html_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "static"
            prepare_viewer_assets(SOURCE, assets)
            script = (assets / "scripts/client.js").read_text()
            # Execute the actual generated renderer with a small DOM facade.
            fields = script[script.index("        // Adapter lobby overlay:"):
                            script.index('        set("god", data.god || "");')]
            formatter = script[script.index("    function format_duration(seconds)"):
                               script.index("    function format_idle_time(seconds)")]
            js = r'''
const assert = require('node:assert/strict');
const cells = {};
function cell(key) {
    return cells[key] ||= {value: '', text(v) {
        if (arguments.length) { this.value = String(v); return this; }
        return this.value;
    }, attr(k, v) { this[k] = v; return this; }};
}
const entry = {find: cell}, new_list = {removeClass() {}}, $ = () => cell('span');
const render = new Function('data', 'entry', 'new_list', '$', FORMATTER + FIELDS);
function draw(data) { render(data, entry, new_list, $); }
draw({xl: 1, place: 'D:1', turn: 0, dur: '0', duration_turn: '0'});
assert.equal(cells['.turn'].value, '0');
assert.equal(cells['.dur'].value, '0s');
draw({xl: 2, place: '<Dungeon>', turn: 10, dur: '75', duration_turn: '9'});
assert.equal(cells['.place'].value, '<Dungeon>');
assert.equal(cells['.dur'].value, '1m (last report)');
assert.match(cells['.dur'].title, /turn 9/);
draw({});
for (const key of ['xl', 'place', 'turn', 'dur'])
    assert.equal(cells['.' + key].value, 'Unknown');
'''.replace("FORMATTER", json.dumps(formatter)).replace("FIELDS", json.dumps(fields))
            subprocess.run(["node", "-e", js], check=True, capture_output=True, text=True)


class DiscoveryTests(unittest.TestCase):
    def test_watch_cli_exposes_all_sessions_option(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "run.py"), "watch", "--help"],
            check=True, capture_output=True, text=True)
        self.assertIn("--all", result.stdout)

    def test_aliases_are_stable_distinct_and_safe_in_stock_lobby_links(self):
        lobby = SessionLobby(None, True, None, None, None, None)
        first = lobby.username(Path("/games/one"), "Agent")
        second = lobby.username(Path("/games/ONE"), "Agent")
        self.assertNotEqual(first.lower(), second.lower())
        self.assertEqual(lobby.username(Path("/games/one"), "Agent"), first)
        unusual = lobby.username(Path("/games/a'b <c>"), "Agent")
        self.assertEqual(unusual, "Agent@a%27b%20%3Cc%3E")
        lobby.all_sessions = False
        self.assertEqual(lobby.username(Path("/games/one"), "Agent"), "Agent")

    def test_skips_partial_stale_and_non_socket_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, runtime in [("partial", "{"), ("missing", "{}"),
                                  ("stale", '{"game_socket":"/no/such/socket"}')]:
                session = root / name
                session.mkdir()
                (session / "runtime.json").write_text(runtime)
                (session / "config.json").write_text('{"name":"Agent","binary":"crawl"}')
            (root / "unrelated-file").touch()
            session = root / "valid"
            session.mkdir()
            path = root / "game.sock"
            path.touch()
            (session / "runtime.json").write_text(json.dumps({"game_socket": str(path)}))
            (session / "config.json").write_text('{"name":"Agent","binary":"crawl"}')
            self.assertEqual(discover_sessions(session, True), {})
            path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as game:
                game.bind(str(path))
                self.assertEqual(list(discover_sessions(session, True)), [str(path)])
                self.assertEqual(list(discover_sessions(session)), [str(path)])
                self.assertEqual(discover_sessions(root / "missing"), {})


@unittest.skipUnless(importlib.util.find_spec("tornado") and
                     importlib.util.find_spec("yaml") and
                     SOURCE and (SOURCE / "crawl").is_file(),
                     "needs viewer dependencies and a built Crawl binary")
class LobbyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_lobby_discovers_duplicates_removal_and_restart(self):
        from tornado.httpclient import AsyncHTTPClient
        from tornado.websocket import websocket_connect

        with tempfile.TemporaryDirectory(prefix="crawl-watch-test-") as tmp:
            root = Path(tmp)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            with (root / "stderr").open("w+") as log:
                viewer = await asyncio.create_subprocess_exec(
                    sys.executable, str(ROOT / "run.py"),
                    "--session-dir", str(root / "anchor"),
                    "watch", "--crawl-source", str(SOURCE), "--all", "--port", str(port),
                    stdout=asyncio.subprocess.PIPE, stderr=log)
                games = []
                ws = None
                try:
                    ready = await asyncio.wait_for(viewer.stdout.readline(), 15)
                    if not ready:
                        log.seek(0)
                        self.fail(log.read())
                    self.assertIn(f"http://localhost:{port}/", ready.decode())
                    response = await AsyncHTTPClient().fetch(
                        f"http://127.0.0.1:{port}/static/scripts/client.js")
                    self.assertIn(b"Adapter lobby overlay", response.body)
                    ws = await websocket_connect(f"ws://127.0.0.1:{port}/socket",
                                                 subprotocols=["no-compression"])
                    entries = {}

                    async def until(predicate):
                        async def read():
                            while True:
                                raw = await ws.read_message()
                                self.assertIsNotNone(raw)
                                for msg in json.loads(raw)["msgs"]:
                                    if msg["msg"] == "ping":
                                        await ws.write_message('{"msg":"pong"}')
                                    elif msg["msg"] == "lobby_clear":
                                        entries.clear()
                                    elif msg["msg"] == "lobby_entry":
                                        entries[msg["id"]] = msg
                                    elif msg["msg"] == "lobby_remove":
                                        entries.pop(msg["id"], None)
                                if predicate():
                                    return
                        await asyncio.wait_for(read(), 8)

                    async def until_names(names):
                        await until(lambda: {e["username"] for e in entries.values()} == names)

                    def named(name):
                        return next(e for e in entries.values() if e["username"] == name)

                    def add_game(session_name, generation):
                        session = root / session_name
                        session.mkdir(exist_ok=True)
                        path = root / f"{session_name}-{generation}.sock"
                        game = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                        games.append(game)
                        game.bind(str(path))
                        game.setblocking(False)
                        (session / "config.json").write_text(json.dumps({
                            "name": "Agent", "binary": str(SOURCE / "crawl")}))
                        (session / "runtime.json").write_text(json.dumps({"game_socket": str(path)}))
                        return game

                    async def check_spectator(game):
                        loop = asyncio.get_running_loop()
                        raw, peer = await asyncio.wait_for(loop.sock_recvfrom(game, 4096), 3)
                        self.assertEqual(json.loads(raw), {"msg": "attach", "primary": False})
                        raw, _ = await asyncio.wait_for(loop.sock_recvfrom(game, 4096), 3)
                        self.assertEqual(json.loads(raw), {"msg": "spectator_joined"})
                        return peer

                    async def send(game, peer, data, special=False):
                        raw = (("*" if special else "") + json.dumps(data) + "\n").encode()
                        await asyncio.get_running_loop().sock_sendto(game, raw, peer)

                    await until_names(set())
                    first = add_game("one", 1)
                    second = add_game("two", 1)
                    await until_names({"Agent@one", "Agent@two"})
                    one_peer = await check_spectator(first)
                    two_peer = await check_spectator(second)
                    self.assertIsNone(named("Agent@one")["xl"])
                    await send(first, one_peer, {"msg": "player", "name": "Agent", "xl": 3,
                                                "place": "D", "depth": 4, "turn": 200})
                    await send(second, two_peer, {"msg": "player", "name": "Agent", "xl": 1,
                                                 "place": "D", "depth": 1, "turn": 0})
                    await until(lambda: named("Agent@one")["xl"] == 3 and
                                named("Agent@two")["xl"] == 1)
                    self.assertEqual(named("Agent@one")["place"], "D:4")
                    self.assertIsNone(named("Agent@one")["dur"])
                    await send(first, one_peer, {"msg": "milestone", "status": "active",
                                                "dur": "60", "turn": "200"}, True)
                    await until(lambda: named("Agent@one")["dur"] == "60")
                    await send(first, one_peer, {"msg": "player", "depth": 5, "turn": 220})
                    await until(lambda: named("Agent@one")["turn"] == 220)
                    self.assertEqual(named("Agent@one")["place"], "D:5")
                    self.assertEqual(named("Agent@one")["duration_turn"], "200")
                    # A dead adapter may leave a real but unresponsive socket.
                    stale = add_game("stale", 1)
                    stale.close()
                    # Duplicate character names still route to different processes.
                    for name in ["Agent@one", "Agent@two"]:
                        await ws.write_message(json.dumps({"msg": "watch", "username": name}))
                        async def watching():
                            while True:
                                raw = await ws.read_message()
                                for msg in json.loads(raw)["msgs"]:
                                    if msg["msg"] == "watching_started":
                                        return msg["username"]
                        self.assertEqual(await asyncio.wait_for(watching(), 4), name)
                    await ws.write_message('{"msg":"go_lobby"}')
                    old_id = named("Agent@one")["id"]
                    await send(first, one_peer, {"msg": "milestone", "status": "saved",
                                                "dur": "75", "turn": "220"}, True)
                    (root / "one/runtime.json").unlink()
                    await until_names({"Agent@two"})
                    replacement = add_game("one", 2)
                    await until_names({"Agent@one", "Agent@two"})
                    replacement_peer = await check_spectator(replacement)
                    self.assertNotEqual(named("Agent@one")["id"], old_id)
                    self.assertIsNone(named("Agent@one")["dur"])
                    self.assertIsNone(named("Agent@one")["xl"])
                    await send(replacement, replacement_peer, {"msg": "player", "name": "Agent", "xl": 3,
                                                               "place": "D", "depth": 5, "turn": 220})
                    await until(lambda: named("Agent@one")["xl"] == 3)
                    (root / "one/runtime.json").unlink()
                    (root / "two/runtime.json").unlink()
                    await until_names(set())
                finally:
                    if ws:
                        ws.close()
                    if viewer.returncode is None:
                        viewer.terminate()
                    await asyncio.wait_for(viewer.wait(), 10)
                    for game in games:
                        game.close()
                log.seek(0)
                output = log.read()
                self.assertNotIn("Traceback", output, output)
                self.assertEqual(viewer.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
