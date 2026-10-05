"""Configure the stock WebTiles server to spectate adapter-owned games."""

from pathlib import Path
from urllib.parse import quote
import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile

from .paths import ASSETS, source_path


class LiveLobbyMetadata:
    """Use this connection's public state, never an old character .where file."""

    def __init__(self, *args, **kwargs):
        self.lobby_metadata = {}
        self.lobby_player = {}
        super().__init__(*args, **kwargs)

    def check_where(self):
        # Shared-lobby usernames are aliases, and saved files can predate this
        # process. The socket is the authoritative source for this generation.
        pass

    def _on_socket_message(self, message):
        # A datagram can contain several newline-delimited public records.
        for line in message.splitlines():
            try:
                record = json.loads(line.removeprefix("*"))
            except ValueError:
                super()._on_socket_message(line)
                continue
            if isinstance(record, dict):
                if record.get("msg") == "player":
                    self.lobby_player.update(record)
                    if record.get("name") == "":
                        # Native chargen snapshots contain placeholder stats.
                        self.lobby_metadata.clear()
                elif record.get("msg") == "milestone":
                    if record.get("status") == "chargen":
                        self.lobby_metadata.clear()
                        self.lobby_player.clear()
                    elif record.get("status") != "milestone_only":
                        for key in self.interesting_info:
                            if key in record:
                                self.lobby_metadata[key] = record[key]
                        if "dur" in record:
                            self.lobby_metadata["duration_turn"] = record.get("turn")
            super()._on_socket_message(line)

    def lobby_entry(self):
        entry = super().lobby_entry()
        # Explicit nulls clear old cells and expose unknown initial state.
        for key in self.interesting_info + ("duration_turn",):
            entry[key] = self.lobby_metadata.get(key)
        if self.lobby_player.get("name"):
            for key in ("xl", "turn", "god", "title"):
                if key in self.lobby_player:
                    entry[key] = self.lobby_player[key]
            place = self.lobby_player.get("place")
            depth = self.lobby_player.get("depth")
            if place is not None and depth is not None:
                entry["place"] = f"{place}:{depth}" if depth else place
        return entry


def prepare_viewer_assets(source, destination):
    """Overlay only lobby field rendering; leave the upstream checkout intact."""
    shutil.copytree(source / "webserver/static", destination)
    client = destination / "scripts/client.js"
    script = client.read_text()
    start = '        set("xl", data.xl);'
    end = '        set("god", data.god || "");'
    if script.count(start) != 1 or script.count(end) != 1:
        raise RuntimeError("Stock lobby renderer changed; review the viewer overlay")
    first, last = script.index(start), script.index(end)
    replacement = (ASSETS / "crawl_lobby_fields.js").read_text()
    client.write_text(script[:first] + replacement + "\n" + script[last:])


def discover_sessions(session, all_sessions=False):
    """Read ready game sockets, tolerating sessions being started or stopped."""
    candidates = sorted(session.parent.iterdir()) if all_sessions else [session]
    found = {}
    for candidate in candidates:
        try:
            runtime = json.loads((candidate / "runtime.json").read_text())
            launch = json.loads((candidate / "config.json").read_text())
            game_socket = Path(runtime["game_socket"])
            if (not game_socket.is_absolute() or not game_socket.is_socket()
                    or not isinstance(launch["name"], str)
                    or not isinstance(launch["binary"], str)):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        found[str(game_socket)] = (candidate, launch)
    return found


def game_settings(session, binary, source, watch_dir, socket_dir):
    return dict(
        name=f"Agent game ({session.name})", crawl_binary=binary,
        rcfile_path=str(session), macro_path=str(session / "macros"),
        morgue_path=str(session / "morgue"),
        inprogress_path=str(watch_dir), ttyrec_path=str(watch_dir),
        dir_path=str(session), socket_path=str(socket_dir),
        client_path=str(source / "webserver" / "game_data"),
        show_save_info=False, send_json_options=False)


class SessionLobby:
    """Reconcile the live session registry without sending controller input."""

    def __init__(self, session, all_sessions, source, watch_dir, config, handlers):
        self.session = session
        self.all_sessions = all_sessions
        self.source = source
        self.watch_dir = watch_dir
        self.config = config
        self.handlers = handlers
        self.attached = {}
        self.aliases = {}
        self.entries = {}
        if handlers is not None:
            self.process_class = type("SessionProcessHandler",
                                      (LiveLobbyMetadata, handlers.CrawlProcessHandler), {})

    def username(self, session, name):
        if not self.all_sessions:
            return name
        key = (session, name)
        if key not in self.aliases:
            # Stock lobby links interpolate this label into an HTML href.
            alias = f"{quote(name, safe='')}@{quote(session.name, safe='')}"
            used = {value.lower() for value in self.aliases.values()}
            if alias.lower() in used:
                alias += "-" + hashlib.sha256(str(session).encode()).hexdigest()
            self.aliases[key] = alias
        return self.aliases[key]

    def detach(self, socket_path):
        process = self.attached.pop(socket_path)
        self.entries.pop(socket_path, None)
        if self.handlers.processes.get(socket_path) is process:
            process.handle_process_end()
            self.handlers.remove_in_lobbys(process)
            del self.handlers.processes[socket_path]
        self.config.games.pop(process.game_params.id, None)

    def refresh(self):
        found = discover_sessions(self.session, self.all_sessions)
        for socket_path in list(self.attached):
            if (socket_path not in found or
                    self.handlers.processes.get(socket_path) is not self.attached[socket_path]):
                self.detach(socket_path)
        for socket_path, (session, launch) in found.items():
            if socket_path in self.attached:
                continue
            # WebTiles displays the game ID verbatim in the lobby's Game column.
            game_id = "session-" + quote(session.name, safe="")
            params = game_settings(session, launch["binary"], self.source,
                                   self.watch_dir, Path(socket_path).parent)
            game = self.config.GameConfig(params, game_id=game_id)
            self.config.games[game_id] = game
            process = self.process_class(
                game, self.username(session, launch["name"]),
                self.handlers.unowned_process_logger)
            self.handlers.processes[socket_path] = process
            self.attached[socket_path] = process
            try:
                process.connect(socket_path)
                # Attach alone subscribes only to future updates. One ordinary
                # spectator refresh seeds an already-running game's metadata.
                process.conn.send_message('{"msg":"spectator_joined"}')
            except OSError:
                # A socket may outlive its process, or disappear during discovery.
                self.detach(socket_path)
                continue
        # Stock periodic lobby updates skip externally owned processes (their
        # .process is None). Publish changed entries ourselves so browsers
        # already in the lobby see new sessions and refreshed game details.
        for socket_path, process in self.attached.items():
            entry = process.lobby_entry()
            if self.entries.get(socket_path) != entry:
                self.entries[socket_path] = entry
                for client in list(self.handlers.ws_handler.sockets):
                    if client.is_in_lobby():
                        client.send_message("lobby_entry", **entry)

    def close(self):
        for socket_path in list(self.attached):
            self.detach(socket_path)


def watch(session, port, all_sessions=False, source=None):
    source = source_path(source)
    if not (source / "webserver/config.py").is_file():
        raise ValueError(f"Crawl WebTiles server assets are missing under {source}")
    try:
        import tornado  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        raise RuntimeError("Tiles spectating needs tornado and PyYAML. Run watch "
                           "using a Python environment with these installed.") from None
    if not 1 <= port <= 65535:
        raise ValueError("port must be 1..65535")
    session.parent.mkdir(parents=True, exist_ok=True)
    sessions = discover_sessions(session, all_sessions)
    if not all_sessions and not sessions:
        raise RuntimeError("No running game to watch")
    sys.path.insert(0, str(source / "webserver"))
    import config as settings
    from webtiles import config, process_handler, server, userdb
    from tornado.ioloop import PeriodicCallback

    watch_dir = session / ("watch-all" if all_sessions else "watch")
    watch_dir.mkdir(parents=True, exist_ok=True)
    settings.server_path = str(watch_dir)
    settings.bind_address = "127.0.0.1"
    settings.bind_port = port
    settings.dgl_mode = True
    settings.enable_ttyrecs = False
    settings.allow_anon_spectate = True
    settings.use_game_yaml = False
    # Adapter restarts use fresh socket directories. Scan session metadata so
    # both new sessions and restarted games appear without restarting the viewer.
    settings.watch_socket_dirs = False
    settings.password_db = str(watch_dir / "users.db3")
    settings.settings_db = str(watch_dir / "settings.db3")
    settings.dgl_status_file = str(watch_dir / "status")
    settings.server_socket_path = None
    settings.server_id = "Local agent spectator"
    # WebTiles requires one game definition even when the lobby starts empty.
    binary = (next(iter(sessions.values()))[1]["binary"] if sessions
              else str(source / "crawl"))
    settings.games = {"agent": game_settings(
        session, binary, source, watch_dir, watch_dir)}
    # Relative assets in the upstream configuration assume this cwd.
    os.chdir(source)
    config.init_config_from_module(settings)
    config.server_path = str(watch_dir)
    logging.basicConfig(level=logging.INFO)
    config.load_game_data()
    config.validate()
    userdb.init_db_connections()
    nonsecure, secure = server.bind_server_sockets()
    url = f"http://localhost:{port}/"
    if not all_sessions:
        launch = next(iter(sessions.values()))[1]
        url += f"#watch-{quote(launch['name'])}"
    print(f"Watch in your browser: {url}\nCtrl-C stops this viewer, not the game.",
          flush=True)

    async def run():
        # Short private socket paths also work with long session directory names.
        with tempfile.TemporaryDirectory(prefix="crawl-watch-") as socket_dir:
            config.set("server_socket_path", socket_dir)
            assets = Path(socket_dir) / "static"
            prepare_viewer_assets(source, assets)
            config.set("static_path", str(assets))
            lobby = SessionLobby(session, all_sessions, source, watch_dir,
                                 config, process_handler)
            poll = PeriodicCallback(lobby.refresh, 1000)
            try:
                lobby.refresh()
                poll.start()
                await server.async_run_server(nonsecure, secure)
            finally:
                poll.stop()
                lobby.close()

    try:
        asyncio.run(run())
    finally:
        for group in nonsecure + secure:
            for sock in group:
                sock.close()
