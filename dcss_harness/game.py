"""Crawl process, public socket, and bounded input/readiness lifecycle."""

from pathlib import Path
import copy
import errno
import fcntl
import json
import os
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import termios
import time
import uuid

from .checkpoint import signature as checkpoint_signature
from .metrics import ActionLog, progress
from .presentation import summary
from .state import Decoder, MODES, State, plain


class Game:
    def __init__(self, session, runtime, config):
        self.session = session
        self.game_generation = uuid.uuid4().hex
        self.checkpoint_result = None
        self.state = State()
        self.startup_pending = True
        self.startup_refresh_sent = False
        self.character_name = config["name"]
        try:
            awareness = json.loads((session / "awareness.json").read_text())
            if awareness.get("name") == self.character_name:
                self.state.unseen_threat = awareness.get("unseen_threat")
        except (OSError, ValueError):
            pass
        self.decoder = Decoder()
        self.quiet = config["settle_ms"] / 1000
        self.timeout = config["timeout"]
        self.auto_more = not config.get("no_auto_more", False)
        self.more_pending_reason = None
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self.sock.bind(str(runtime / "adapter.sock"))
        self.game_socket = runtime / f"{config['name']}:agent.sock"
        self.sock.setblocking(False)
        self.events = ((session / "events.jsonl").open("a", buffering=1)
                       if config.get("log_events", False) else None)
        self.actions = ActionLog(session / "actions.jsonl")
        self.log = (session / "crawl.log").open("ab", buffering=0)
        rc = session / "agent.rc"
        rc.write_text("restart_after_game = false\nrestart_after_save = false\n"
                      "default_manual_training = true\n"
                      f"weapon = {config['weapon']}\n")
        command = [config["binary"], "-await-connection",
                   "-webtiles-socket", str(self.game_socket), "-rc", str(rc),
                   "-dir", str(session), "-macro", str(session / "macros"),
                   "-name", config["name"], "-species", config["species"],
                   "-background", config["background"]]
        if config["seed"] is not None:
            command += ["-seed", str(config["seed"])]
        # Headless mode skips CRT rendering (including the skills menu), so
        # use a private terminal like the stock WebTiles server. Input and
        # observations still travel exclusively over the WebTiles socket.
        self.terminal, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            os.set_blocking(self.terminal, False)
            env = {**os.environ, "TERM": "linux", "COLUMNS": "80", "LINES": "24"}
            self.process = subprocess.Popen(command, cwd=Path(config["binary"]).parent, env=env,
                                            stdin=slave, stdout=slave,
                                            stderr=self.log, start_new_session=True)
        except BaseException:
            os.close(self.terminal)
            self.sock.close()
            self.log.close()
            if self.events is not None:
                self.events.close()
            self.actions.close()
            raise
        finally:
            os.close(slave)
        self.last_event = time.monotonic()
        self.settled = False

    def attach(self):
        deadline = time.monotonic() + 60
        while not self.game_socket.exists():
            if self.process.poll() is not None:
                raise RuntimeError("Crawl exited during startup; see crawl.log")
            if time.monotonic() >= deadline:
                raise TimeoutError("Crawl socket startup timed out")
            time.sleep(.02)
        self.send({"msg": "attach", "primary": True})
        self.settle(timeout=60, require_event=True, startup=True)
        self.actions.start(self.state.player)

    def send(self, message):
        if self.events is not None:
            self.events.write(json.dumps({"direction": "in", "ts": round(time.time(), 6),
                                          "data": message}) + "\n")
        self.sock.sendto(json.dumps(message).encode(), str(self.game_socket))

    def drain_terminal(self):
        # Drain while idle and while settling: a full PTY would block Crawl
        # before it could send its next protocol update. Never parse ANSI.
        while self.terminal is not None:
            try:
                data = os.read(self.terminal, 65536)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                data = b""  # Linux PTYs report EIO when the slave closes.
            if not data:
                os.close(self.terminal)
                self.terminal = None
                return
            self.log.write(data)

    def readers(self):
        return [self.sock] + ([self.terminal] if self.terminal is not None else [])

    def drain(self):
        self.drain_terminal()
        while True:
            try:
                data = self.sock.recv(128 * 1024)
            except BlockingIOError:
                break
            self.last_event = time.monotonic()
            for event in self.decoder.feed(data):
                if self.events is not None:
                    self.events.write(json.dumps({"direction": "out", "ts": round(time.time(), 6),
                                                  "data": event}) + "\n")
                self.state.apply(event)
        if self.state.awareness_changed:
            self.persist_awareness()

    def persist_awareness(self):
        path = self.session / "awareness.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"name": self.character_name,
                                        "unseen_threat": self.state.unseen_threat}))
        temporary.replace(path)
        self.state.awareness_changed = False

    def settle(self, timeout=None, require_event=False, startup=False):
        started = time.monotonic()
        try:
            self._settle(timeout, require_event, startup)
        finally:
            self.request_settle_ms = getattr(self, "request_settle_ms", 0) + (time.monotonic() - started) * 1000

    def _settle(self, timeout=None, require_event=False, startup=False):
        start = time.monotonic()
        deadline = start + (timeout if timeout is not None else self.timeout)
        before = self.state.events
        refreshed = False
        self.settled = False
        if startup:
            self.startup_pending = True
        if require_event and not startup and self.state.input_baseline is None:
            self.state.input_baseline = copy.deepcopy(self.state.response_state())
        while True:
            self.drain()
            now = time.monotonic()
            if self.process.poll() is not None:
                self.settled = True
                self.settle_reason = "exited"
                self.state.input_baseline = None
                self.state.cancel_only = False
                return
            received = self.state.events > before
            missing = (self.state.initial_state_missing()
                       if getattr(self, "startup_pending", False) else [])
            creation = bool(missing and self.state.character_creation())
            initialized = not missing or creation
            quiet_since = max(start, self.last_event)
            if (missing and not creation and not getattr(self, "startup_refresh_sent", False)
                    and not self.state.frame_open and not self.decoder.pending
                    and now - quiet_since >= self.quiet):
                # One bounded, read-only full public refresh across startup and
                # later observes. No gameplay key is used to provoke data.
                self.send({"msg": "spectator_joined"})
                self.startup_refresh_sent = True
                self.last_event = now
                quiet_since = now
            if self.state.inventory_refresh and not refreshed:
                self.send({"msg": "spectator_joined"})
                refreshed = True
                self.last_event = now
                quiet_since = now
            pending = self.state.input_baseline is not None
            responded = (not pending or self.state.response_state() != self.state.input_baseline
                         or self.state.map_probe_ready() or self.state.blocked_move_ready())
            input_ready = self.state.input_ready()
            ready = (initialized and (received or not require_event) and not self.decoder.pending
                     and not self.state.frame_open
                     and responded and input_ready and not self.state.inventory_refresh
                     and (self.state.public_refresh is None or self.state.public_refresh["complete"]))
            if ready:
                if now - quiet_since >= self.quiet:
                    self.settled = True
                    self.settle_reason = ("blocked_move_resynchronized" if self.state.blocked_move_ready() else
                                          "map_resynchronized" if self.state.map_probe_ready() else
                                          "response_quiet" if pending else "quiet")
                    if not missing:
                        self.startup_pending = False
                    self.state.input_baseline = None
                    self.state.cancel_only = False
                    self.state.map_probe = None
                    self.state.blocked_move_probe = None
                    self.state.public_refresh = None
                    return
            self.settle_reason = ("level_generation" if self.state.generation_active() else
                                  "level_refresh" if self.state.generation_refresh is not None else
                                  "startup_incomplete" if not initialized else
                                  "no_response" if not responded else
                                  "inventory_refresh" if self.state.inventory_refresh else
                                  "incomplete_frame" if self.state.frame_open or self.decoder.pending else
                                  "public_refresh" if self.state.public_refresh is not None else
                                  "input_not_ready" if not input_ready else "updates_pending")
            if now >= deadline:
                return
            delay = self.quiet
            if ready:
                delay = max(.001, self.quiet - (now - quiet_since))
            select.select(self.readers(), [], [], min(delay, deadline - now))

    def refresh_observation(self):
        """One bounded full public refresh; no gameplay key or action retry."""
        self.state.begin_refresh()
        self.send({"msg": "spectator_joined"})
        # The refresh has its own complete-frame guard. It is read-only and
        # may legitimately repeat every field; do not create an action baseline.
        # Do not auto-acknowledge pagination encountered by this read-only call.
        self.settle()
        if not self.settled or self.state.public_refresh is not None:
            raise RuntimeError("Full public refresh incomplete; observe before further input")
        return self.observe()

    def checkpoint_weapon_titles(self, observation):
        """Read native non-terse weapon descriptions without using item actions."""
        titles = {}
        baseline = checkpoint_signature(observation)
        turn = observation["player"].get("turn")
        deadline = time.monotonic() + 15
        for item in observation["inventory"]:
            if item.get("category") != "weapons":
                continue
            if (time.monotonic() >= deadline or not self.settled or self.state.mode != 1
                    or self.state.ui or self.state.more or self.state.text_input is not None):
                raise RuntimeError("Checkpoint description requires a settled command prompt")
            slot, letter = item["slot"], item.get("letter")
            if item.get("letter_namespace") != "equipment" or not letter:
                raise RuntimeError("Checkpoint weapon has no unambiguous equipment identity")
            self.state.begin_input()
            self.send({"msg": "inv_item_describe", "slot": slot})
            self.settle(timeout=min(self.timeout, max(0, deadline - time.monotonic())), require_event=True)
            if (not self.settled or len(self.state.ui) != 1
                    or self.state.ui[-1].get("type") != "describe-item"):
                raise RuntimeError("Checkpoint item description incomplete; inspect before further input")
            title = plain(self.state.ui[-1].get("title", "")).strip()
            if not title.startswith(letter + " - ") or self.state.player.get("turn") != turn:
                raise RuntimeError("Checkpoint description identity or turn changed")
            titles[str(slot)] = title
            self.act([27], "checkpoint_close_description")
            if not self.settled or self.state.ui or self.state.mode != 1 or self.state.player.get("turn") != turn:
                raise RuntimeError("Checkpoint description did not return to the same command turn")
            if checkpoint_signature(self.observe()) != baseline:
                raise RuntimeError("Checkpoint state changed while reading item descriptions")
        return titles

    def observe(self):
        result = self.state.observation()
        self.state.messages = self.state.messages[-100:]
        result.update(session=str(self.session),
                      game_generation=getattr(self, "game_generation", None),
                      checkpoint=getattr(self, "checkpoint_result", None),
                      startup=({"status": "character_creation" if self.state.character_creation() else "incomplete",
                                "missing": self.state.initial_state_missing(),
                                "resync_requested": getattr(self, "startup_refresh_sent", False),
                                "recovery": "Observe again; if incomplete, coordinate a normal stop/start."}
                               if getattr(self, "startup_pending", False) else None),
                      combat=None,
                      recovery=None,
                      ranged=None,
                      cast=None,
                      hold=None,
                      terrain_neighborhood=None,
                      running=self.process.poll() is None,
                      settled=self.settled, synchronization="quiet_window",
                      settle_reason=getattr(self, "settle_reason", None),
                      cancel_available=(not self.settled and self.state.can_cancel()
                                        and not self.state.frame_open and not self.decoder.pending),
                      more_pending_reason=getattr(self, "more_pending_reason", None))
        result["summary"] = summary(result)
        return result

    def plain_more(self):
        """Require the message-window acknowledgment, never a popup's more."""
        return (self.state.mode == MODES.index("more") and self.state.more
                and self.state.text_input is None and self.state.ui_state != 2
                and not self.state.ui and not self.state.more_text
                and (self.state.exit_reason or {}).get("type", "unknown") == "unknown"
                and self.state.player.get("hp", 0) > 0)

    def settle_input(self, deadline=None, require_event=False, context="observe"):
        """Settle and acknowledge pages within one shared time/count budget."""
        if deadline is None:
            deadline = time.monotonic() + self.timeout
        count = 0
        self.more_pending_reason = None
        try:
            self.settle(timeout=max(0, deadline - time.monotonic()),
                        require_event=require_event)
            while self.process.poll() is None and self.plain_more():
                if not self.auto_more:
                    self.more_pending_reason = "disabled"
                    break
                if not self.settled:
                    self.more_pending_reason = "unsettled"
                    break
                if count >= 32:
                    self.more_pending_reason = "acknowledgment_limit"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.more_pending_reason = "timeout"
                    break
                self.state.input_baseline = copy.deepcopy(self.state.response_state())
                self.state.cancel_only = False
                self.send({"msg": "key", "keycode": 32})
                count += 1
                self.settle(timeout=remaining, require_event=True)
            return count
        finally:
            if count:
                self.actions.write({"event": "auto_more", "context": context,
                                    "count": count, **progress(self.state.player),
                                    "settled": self.settled,
                                    "stop_reason": self.more_pending_reason})

    def act(self, keys, action="keys"):
        started = time.monotonic()
        timestamp = round(time.time(), 3)
        if self.process.poll() is not None:
            raise RuntimeError("Game has exited; stop the session before restarting")
        # Consume already queued public updates before establishing the action's
        # response baseline. They cannot acknowledge a key we have not sent.
        before_drain = self.state.events
        self.drain()
        if self.state.events != before_drain or self.decoder.pending:
            self.settled = False
        if not self.settled:
            if not (keys == [27] and self.state.can_cancel()
                    and not self.state.frame_open and not self.decoder.pending):
                self.settle()
            if not self.settled and not (keys == [27] and self.state.can_cancel()
                                         and not self.state.frame_open and not self.decoder.pending):
                raise RuntimeError("Game is still updating; observe before acting")
        completed = 0
        buffered = 0
        radius_buffered_here = False
        deferred_keys_sent = 0
        if not self.state.ui:
            self.state.inspection = None
        before = self.state.player.get("turn")
        error = None
        try:
            for key in keys:
                message = self.text_entry_key(key)
                if message is None:
                    buffered += 1
                    radius_buffered_here = (radius_buffered_here or
                                            (key == ord('R') and self.state.map_radius_context is not None))
                    continue
                self.state.begin_input(message)
                if self.state.blocked_move_probe is not None:
                    self.state.begin_refresh()
                self.send(message)
                completed += 1
                if message.get("msg") == "text_input" and re.fullmatch(r"R[1-8]", message.get("text", "")):
                    if radius_buffered_here:
                        completed += 1
                        buffered -= 1
                        radius_buffered_here = False
                    else:
                        deferred_keys_sent += 1
                if self.state.map_probe is not None or self.state.blocked_move_probe is not None:
                    # Same-socket ordering: this read-only full refresh is
                    # handled after the complete map command. It supplies a
                    # frame even when cycling/editing had no visible effect.
                    self.send({"msg": "spectator_joined"})
                acknowledged = self.settle_input(
                    deadline=started + self.timeout, require_event=True, context="act")
                # Automatic input may expose a new decision (e.g. attributes).
                # Do not spill the remainder of a literal batch into that UI.
                if (acknowledged or not self.settled or self.process.poll() is not None
                        or time.monotonic() >= started + self.timeout):
                    break
            result = self.observe()
            result["keys_sent"] = completed
            if buffered:
                result["keys_buffered"] = buffered
            if deferred_keys_sent:
                result["deferred_keys_sent"] = deferred_keys_sent
            return result
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            if self.state.input_baseline is not None:
                self.settled = False
            error = exc
            raise
        finally:
            self.actions.record(action, len(keys), completed, before,
                                self.state.player, started, timestamp,
                                self.settled, self.process.poll() is None, error)

    def text_entry_key(self, key):
        """Mirror the browser's local editor for the two native line prompts.

        Crawl does not echo line edits over WebTiles. Buffer them locally and
        submit one text_input record, including the native browser's clear-line
        prefix, only on explicit Enter (or a documented prompt terminator).
        """
        if self.state.map_radius_context is not None:
            if key == 27:
                self.state.map_radius_context = None
                self.settle_reason = "map_input_cancelled"
                return None
            if (self.state.ui_state != 2 or self.state.ui or self.state.text_input is not None
                    or self.state.radius_context() != self.state.map_radius_context):
                raise ValueError("Map selection changed; Escape to cancel buffered radius")
            if key not in range(ord('1'), ord('8') + 1):
                raise ValueError("Exclusion radius requires one digit 1-8, or Escape")
            self.state.map_radius_context = None
            return {"msg": "text_input", "text": "R" + chr(key)}
        if (key == ord('R') and self.state.ui_state == 2 and not self.state.ui
                and self.state.text_input is None):
            if self.state.cursors.get(2) is None:
                raise ValueError("A known level-map cursor is required to set an exclusion radius")
            self.state.map_radius_context = self.state.radius_context()
            self.settle_reason = "map_input_buffered"
            return None
        prompt = self.state.text_input
        if not prompt or prompt.get("tag") not in ("travel_depth", "stash_search"):
            return {"msg": "key", "keycode": key}
        if key == 27:
            return {"msg": "key", "keycode": key}
        value = prompt["text"]
        terminator = key == 13 or (prompt["tag"] == "travel_depth"
                                   and key in map(ord, "<>?$^-p\x10"))
        if terminator:
            return {"msg": "text_input", "text": "\x15\x0b" + value + chr(key)}
        if prompt["tag"] == "stash_search" and key == ord('?') and not value:
            return {"msg": "key", "keycode": key}
        if key not in (8, 127, 21) and not 32 <= key <= 0x10ffff:
            raise ValueError("Text entry supports printable text, Backspace, Ctrl-U, Enter and Escape")
        if prompt.get("select_prefill"):
            value = ""
            prompt["select_prefill"] = False
        if key in (8, 127):
            value = value[:-1]
        elif key == 21:
            value = ""
        else:
            value += chr(key)
        prompt["text"] = value[:prompt.get("maxlen", 1024)]
        self.settle_reason = "text_buffered"
        return None

    def target(self, dx, dy):
        """Move the public targeting cursor; never select/fire."""
        if type(dx) is not int or type(dy) is not int:
            raise ValueError("Target offsets must be integers")
        self.settle_input()
        if (self.process.poll() is not None or not self.settled or self.state.ui
                or self.state.text_input is not None or self.state.ui_state == 2
                or self.state.mode not in (2, 3, 4)):
            raise RuntimeError("Target requires an active, settled targeting prompt")
        pos = self.state.player.get("pos")
        if pos is None or (pos["x"] + dx, pos["y"] + dy) not in self.state.cells:
            raise ValueError("Target requires a known map square")
        aim = self.state.observation().get("targeting")
        if aim and [dx, dy] in aim["invalid_aim_cells"]:
            raise ValueError("Target square is publicly marked invalid; no cursor input sent")
        started, timestamp = time.monotonic(), round(time.time(), 3)
        before, error = self.state.player.get("turn"), None
        try:
            message = {"msg": "target_cursor", "x": pos["x"] + dx, "y": pos["y"] + dy}
            self.state.begin_input(message)
            self.send(message)
            self.settle_input(deadline=started + self.timeout, require_event=True, context="target")
            return self.observe()
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            error = exc
            raise
        finally:
            self.actions.record(f"target:{dx},{dy}", 0, 0, before, self.state.player,
                                started, timestamp, self.settled, self.process.poll() is None, error)

    def inspect(self, dx, dy):
        """Open Crawl's public right-click description for a visible square."""
        if type(dx) is not int or type(dy) is not int:
            raise ValueError("Inspection offsets must be integers")
        if self.process.poll() is not None:
            raise RuntimeError("Game has exited; stop the session before restarting")
        self.settle()
        if self.process.poll() is not None:
            raise RuntimeError("Game exited while waiting to inspect")
        if (not self.settled or self.state.mode != MODES.index("command")
                or self.state.text_input is not None or self.state.ui_state == 2
                or self.state.more or self.state.ui):
            raise RuntimeError("Inspect requires a settled command prompt with no menu or more prompt")
        pos = self.state.player.get("pos")
        if pos is None:
            raise RuntimeError("Player position is unknown")
        x, y = pos["x"] + dx, pos["y"] + dy
        if not self.state.visible(self.state.cells.get((x, y), {})):
            raise ValueError("Inspect requires a currently visible square")
        started, timestamp = time.monotonic(), round(time.time(), 3)
        before, error = self.state.player.get("turn"), None
        self.state.begin_inspection((x, y))
        try:
            self.state.begin_input()
            self.send({"msg": "click_cell", "x": x, "y": y, "button": 3})
            self.settle(require_event=True)
            result = self.observe()
            result["keys_sent"] = 0
            return result
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            error = exc
            raise
        finally:
            self.actions.record(f"inspect:{dx},{dy}", 0, 0, before,
                                self.state.player, started, timestamp,
                                self.settled, self.process.poll() is None, error)

    def close(self):
        forced = False
        if self.process.poll() is None:
            # SIGHUP is Crawl's normal disconnected-terminal save path.
            self.process.send_signal(signal.SIGHUP)
            deadline = time.monotonic() + 5
            while self.process.poll() is None and time.monotonic() < deadline:
                self.drain()
                time.sleep(.02)
            if self.process.poll() is None:
                forced = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        self.drain_terminal()
        if self.terminal is not None:
            os.close(self.terminal)
            self.terminal = None
        self.sock.close()
        self.log.close()
        if self.events is not None:
            self.events.close()
        self.actions.close()
        return {"forced": forced, "exit_code": self.process.returncode,
                "exit_reason": self.state.exit_reason}
