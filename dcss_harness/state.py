"""Public WebTiles decoding, state merging, and observation construction."""

import copy
import html
import json
import re

from .items import EQUIPMENT, equipment_name, item_signature, tile_flags
from .keys import DIRECTIONS
from .map import MOVES, VisibleMap, level_map, terrain
from .safety import ITEM_CLASSES, UNSEEN_ATTACK, scenery
from .spells import spell_menu
from .targeting import OVERLAYS, targeting


MODES = ["normal", "command", "target", "target_direction", "target_path",
         "more", "macro", "prompt", "yes_no"]


def plain(text):
    return html.unescape(re.sub(r"</?[a-zA-Z][^>]*>", "", str(text)))


def merge_menu(menu, update, native_rows=False):
    """Merge public menu chunks at absolute indices and honor their size.

    Native rows omit cleared colour/tiles/hotkeys, matching WebTiles menu.js.
    Missing chunks remain empty placeholders, never rows from an older size.
    """
    data = copy.deepcopy(update)
    chunk = data.pop("items", None)
    menu.update(data)
    if chunk is None and "items" not in menu and "total_items" not in menu:
        return
    rows = menu.setdefault("items", [])
    total = menu.get("total_items")
    if type(total) is int and total >= 0:
        del rows[total:]
        rows.extend({} for _ in range(total - len(rows)))
    if chunk is None:
        return
    start = data.get("chunk_start", 0)
    for index, item in enumerate(chunk, start):
        if index < 0 or (type(total) is int and index >= total):
            continue
        while len(rows) <= index:
            rows.append({})
        if item is None:
            continue
        if isinstance(item, str):
            item = {"type": 2, "text": item}
        rows[index].update(item)
        if native_rows:
            for field in ("colour", "tiles", "hotkeys"):
                if field not in item:
                    rows[index].pop(field, None)


def skills_text(line):
    """Keep selected switch values visible when stripping CRT HTML colours.

    SkillMenuSwitch renders its selected option in white (fg15), with other
    options in dark grey. Only annotate the switch legend, not white skill
    levels, aptitude values, or arbitrary help text.
    """
    if re.search(r"\[[/!*_|]\]", plain(line)) and "|" in line:
        line = re.sub(r'(<span class="fg15 bg\d+">)([^<]+)',
                      lambda m: (m[1] + "[" + m[2].rstrip() + "]"
                                 + m[2][len(m[2].rstrip()):]), line)
    return plain(line)


def clean_ui(value):
    if isinstance(value, str):
        return plain(value)
    if isinstance(value, list):
        return [clean_ui(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_ui(item) for key, item in value.items()
                if key not in {"tiles", "tile", "colour", "col", "tex"}}
    return value


class Decoder:
    """Datagrams can split a JSON record, including inside a UTF-8 character."""

    def __init__(self):
        self.pending = b""

    def feed(self, data):
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if line:
                yield json.loads(line.lstrip(b"*"))


class State:
    def __init__(self):
        self.player = {}
        self.cells = {}
        self.monsters = {}
        self.last_x = self.last_y = 0
        self.messages = []
        self.text = {}
        self.ui = []
        self.mode = None
        self.ui_state = None
        self.text_input = None
        self.more = False
        self.more_text = ""
        self.exit_reason = None
        self.version = None
        self.events = 0
        self.frame_open = False
        self.item_names = {}
        self.inspection = None
        self.inventory_refresh = set()
        self.initial_inventory_received = False
        self.initial_map_received = False
        self.map_player_on_level = None
        self.unseen_threat = None
        self.awareness_changed = False
        self.cursors = {}
        self.input_messages = []
        self.guard_observer = None
        self.input_baseline = None
        self.cancel_only = False
        self.map_radius_context = None
        self.map_probe = None
        self.generation_refresh = None
        self.public_refresh = None
        self.blocked_move_probe = None

    def begin_input(self, message=None):
        """Prepare for actual input, not a helper's read-only preflight."""
        self.input_messages = []
        self.input_baseline = copy.deepcopy(self.response_state())
        self.map_probe = None
        self.blocked_move_probe = None
        if (self.mode == 1 and self.ui_state != 2 and not self.ui and not self.more
                and self.text_input is None and message and message.get("msg") == "key"):
            direction = next((name for name, key in DIRECTIONS.items()
                              if ord(key) == message.get("keycode")), None)
            pos = self.player.get("pos") or {}
            if direction and all(type(pos.get(k)) is int for k in ("x", "y")):
                dx, dy = MOVES[direction]
                point = (pos["x"] + dx, pos["y"] + dy)
                cell = self.cells.get(point, {})
                feature = terrain(cell)
                # Doors, monsters, unknown/remembered terrain and ordinary
                # movement never gain a no-op allowance. A complete ordered
                # refresh must still confirm this unchanged blocking cell.
                if (self.visible(cell) and not cell.get("mon")
                        and "SOLID" in feature.get("flags", [])
                        and feature.get("minimap") != "door"):
                    self.blocked_move_probe = {"point": point, "feature": cell.get("f"),
                        "player": copy.deepcopy({k: self.player.get(k) for k in ("turn", "place", "depth", "pos")})}
        # These stock map inputs never travel or leave an unanswered native
        # confirmation. A radius command is submitted as one complete R+digit.
        if (self.ui_state == 2 and not self.ui and self.text_input is None and message
                and ((message.get("msg") == "key" and message.get("keycode") in
                      set(map(ord, "hjklyubn<>^_EIWe\t")))
                     or (message.get("msg") == "text_input"
                         and re.fullmatch(r"R[1-8]", message.get("text", ""))))):
            self.map_probe = {"seen": set(), "complete": False,
                              "turn": self.player.get("turn")}
        # These native target operations cannot select/fire. Do not extend this
        # to direction-only targeting, movement, arbitrary keys, or submission.
        self.cancel_only = (self.mode in (2, 4) and not self.ui and message is not None
                            and (message.get("msg") == "target_cursor"
                                 or (message.get("msg") == "key"
                                     and message.get("keycode") in (9, 43, 45, 61))))

    def response_state(self):
        # Compare public content, not packet counts: a spectator redraw can
        # repeat the old command state before a queued key takes effect.
        # Entering a different prompt counts; a round trip back to the same
        # mode does not. Command mode can precede the action's player/map data.
        return (self.player, self.messages[-100:], self.text, self.ui, self.mode,
                self.more, self.more_text, self.cursors,
                self.ui_state, self.text_input)

    def input_ready(self):
        if self.generation_active() or self.generation_refresh is not None:
            return False
        return (self.mode in range(1, len(MODES)) or bool(self.ui)
                or self.ui_state == 2 or self.text_input is not None)

    def generation_active(self):
        # Native progress_popup is not an input prompt, even when an old
        # command mode (or another popup beneath it) remains in public state.
        return any(item.get("type") == "progress-bar" for item in self.ui)

    def begin_refresh(self):
        self.public_refresh = {"seen": set(), "complete": False}

    def blocked_move_ready(self):
        probe = self.blocked_move_probe
        if not (probe and self.public_refresh and self.public_refresh["complete"]
                and self.mode == 1 and self.ui_state != 2 and not self.ui and not self.more
                and self.text_input is None):
            return False
        cell = self.cells.get(probe["point"], {})
        return (all(self.player.get(k) == v for k, v in probe["player"].items())
                and self.visible(cell) and not cell.get("mon")
                and cell.get("f") == probe["feature"])

    def initial_state_missing(self):
        """Receipt of public startup data, not nonempty monsters/inventory."""
        missing = ["player." + field for field in
                   ("name", "hp", "hp_max", "mp", "mp_max", "turn", "xl", "place", "depth", "status")
                   if self.player.get(field) is None]
        pos = self.player.get("pos") or {}
        if any(type(pos.get(key)) is not int for key in ("x", "y")):
            missing.append("player.pos")
        if not self.initial_inventory_received:
            missing.append("inventory")
        cell = self.cells.get((pos.get("x"), pos.get("y")), {})
        if not self.initial_map_received or not cell.get("g") or not self.visible(cell):
            missing.append("map")
        if not self.version:
            missing.append("version")
        return missing

    def character_creation(self):
        # Explicit native layouts, not arbitrary menus or a command-mode packet.
        return bool(self.ui and self.ui[-1].get("type") in
                    {"newgame-choice", "newgame-random-combo", "seed-selection"})

    def can_cancel(self):
        return (not self.generation_active() and self.generation_refresh is None
                and self.cancel_only and self.input_baseline is not None
                and self.response_state() == self.input_baseline)

    def map_probe_ready(self):
        return (self.map_probe is not None and self.map_probe["complete"]
                and self.ui_state == 2 and not self.ui and self.text_input is None
                and self.player.get("turn") == self.map_probe["turn"])

    def radius_context(self):
        return (self.player.get("place"), self.player.get("depth"), self.player.get("turn"),
                copy.deepcopy(self.cursors.get(2)))

    def acknowledge_threat(self):
        self.unseen_threat = None
        self.awareness_changed = True

    def begin_inspection(self, point):
        signature = item_signature(self.cells.get(point, {}))
        self.inspection = ({"point": point, "signature": signature,
                            "single": True, "opened": False} if signature else None)

    def _capture_item_name(self):
        context = self.inspection
        if not context or not self.ui:
            return
        point = context["point"]
        cell = self.cells.get(point, {})
        if item_signature(cell) != context["signature"]:
            self.inspection = None
            return
        # The square's examine menu can include terrain as well as loot. Only
        # a complete menu with exactly one item makes the association unambiguous.
        for menu in self.ui:
            if menu.get("tag") == "pickup":
                items, in_items = [], False
                rows = menu.get("items", [])
                for row in rows:
                    if row.get("level") == 1:
                        in_items = plain(row.get("text", "")).strip() == "Items"
                    elif in_items and row.get("level") == 2:
                        items.append(row)
                context["single"] = (len(rows) == menu.get("total_items") and len(items) == 1)
        description = self.ui[-1]
        if description.get("type") != "describe-item" or not context["single"]:
            return
        tiles = description.get("tiles", [])
        described_tiles = [tile.get("t") for tile in tiles if tile.get("t") in EQUIPMENT]
        if len(described_tiles) != 1:
            return
        described = described_tiles[0]
        pos = self.player.get("pos", {})
        under_player = point == (pos.get("x"), pos.get("y"))
        ground_tile = tile_flags(cell.get("t", {}).get("fg")) & 0xffff
        if not under_player and (described != ground_tile or not equipment_name(cell)):
            return
        title = plain(description.get("title", "")).strip()
        title = re.sub(r"^(?:a|an|the)\s+", "", title, flags=re.I)
        title = title.removesuffix(".")
        if title:
            self.item_names[point] = (context["signature"], title)

    def apply(self, event):
        self.events += 1
        kind = event.get("msg")
        # Crawl's flush_messages closes a public update frame. JSON records
        # alone are not complete observations: mode/player/map are sent apart.
        # A flush is a frame boundary, never an acknowledgment of our input.
        self.frame_open = kind != "flush_messages"
        data = {key: value for key, value in event.items() if key != "msg"}
        if kind == "player":
            if isinstance(data.get("inv"), dict):
                self.initial_inventory_received = True
                self.player.setdefault("inv", {})
            if any(key in data and key in self.player and data[key] != self.player[key]
                   for key in ("place", "depth")):
                self.item_names.clear()
                self.inspection = None
                self.cursors.clear()
            if "turn" in data and data["turn"] != self.player.get("turn"):
                # Pickup/drop can replace loot underfoot with an identical tile.
                # There is no public ground-item ID, so expire those associations.
                for pos in (self.player.get("pos", {}), data.get("pos", {})):
                    self.item_names.pop((pos.get("x"), pos.get("y")), None)
                self.inspection = None
            for slot, diff in data.pop("inv", {}).items():
                item = self.player.setdefault("inv", {}).setdefault(slot, {})
                # Global identification can change the displayed name without
                # changing the engine's cached item.name() comparison. A full
                # spectator refresh supplies the authoritative public name.
                if (item.get("quantity", 0) > 0 and "name" not in diff
                        and any(k in diff for k in ("flags", "letter", "base_type", "sub_type"))):
                    self.inventory_refresh.add(slot)
                item.update(diff)
                if "name" in diff or item.get("quantity", 0) == 0:
                    self.inventory_refresh.discard(slot)
            self.player.update(data)
        elif kind == "map":
            if data.get("clear"):
                self.initial_map_received = True
                self.cursors.pop(2, None)
                self.map_player_on_level = None
                self.cells.clear()
                self.monsters.clear()
                self.item_names.clear()
                self.inspection = None
            if "player_on_level" in data:
                self.map_player_on_level = data["player_on_level"]
            for diff in data.get("cells", []):
                x = diff.get("x", self.last_x + 1)
                y = diff.get("y", self.last_y)
                self.last_x, self.last_y = x, y
                cell = self.cells.setdefault((x, y), {})
                point = (x, y)
                before = item_signature(cell)
                for key, value in diff.items():
                    if key == "mon" and value is not None:
                        identity = value.get("id")
                        old = self.monsters.get(identity, cell.get("mon") or {})
                        monster = {**old, **value}
                        if not identity:
                            monster.pop("id", None)
                        cell[key] = monster
                        if identity:
                            self.monsters[identity] = monster
                    elif key == "t":
                        cell.setdefault("t", {}).update(value)
                    else:
                        cell[key] = value
                if (item_signature(cell) != before or "g" in diff or "mf" in diff
                        or "fg" in diff.get("t", {})):
                    self.item_names.pop(point, None)
                    if self.inspection and self.inspection["point"] == point:
                        self.inspection = None
        elif kind == "msgs":
            rollback = data.get("rollback", 0)
            if rollback:
                del self.messages[-rollback:]
                del self.input_messages[-rollback:]
            self.messages.extend(data.get("messages", []))
            self.input_messages.extend(data.get("messages", []))
            for message in data.get("messages", []):
                text = plain(message.get("text", ""))
                if UNSEEN_ATTACK.search(text):
                    self.unseen_threat = {"reason": "unseen_attacker", "text": text,
                                          "turn": message.get("turn")}
                    self.awareness_changed = True
            # Trim only after returning an observation. A single action can
            # cross many pages; rolling here would silently lose its beginning.
            self.more = data.get("more", self.more)
            self.more_text = data.get("more_text", self.more_text)
        elif kind == "txt":
            area = data["id"]
            if data.get("clear"):
                self.text[area] = {}
            self.text.setdefault(area, {}).update(data.get("lines", {}))
        elif kind in ("menu", "ui-push"):
            if data.get("replace") and self.ui:
                self.ui.pop()
            if kind == "menu":
                menu = {}
                merge_menu(menu, data, native_rows=True)
                self.ui.append(menu)
            else:
                self.ui.append(copy.deepcopy(data))
        elif kind in ("close_menu", "ui-pop"):
            if self.ui:
                self.ui.pop()
        elif kind == "close_all_menus":
            self.ui.clear()
        elif kind == "ui-stack":
            self.ui = copy.deepcopy(data.get("items", []))
        elif kind in ("ui-state", "update_menu", "update_menu_items",
                      "menu_scroll", "title_prompt") and self.ui:
            merge_menu(self.ui[-1], data, native_rows=kind == "update_menu_items")
        elif kind == "input_mode":
            self.mode = data["mode"]
            if self.mode == MODES.index("command"):
                self.cursors.clear()
                # Preview overlays belong to this targeting interaction only.
                for cell in self.cells.values():
                    tiles = cell.get("t", {})
                    if "ov" in tiles:
                        tiles["ov"] = [o for o in (tiles["ov"] or []) if o not in OVERLAYS]
        elif kind == "cursor":
            if "loc" in data:
                self.cursors[data["id"]] = copy.deepcopy(data["loc"])
            else:
                self.cursors.pop(data["id"], None)
        elif kind == "ui_state":
            self.ui_state = data.get("state")
            if self.ui_state != 2:
                self.cursors.pop(2, None)
        elif kind == "init_input":
            self.text_input = copy.deepcopy(data)
            self.text_input["text"] = data.get("prefill", "")
        elif kind == "update_input" and self.text_input is not None:
            self.text_input["text"] = data.get("input_text", "")
            self.text_input["select_prefill"] = data.get("select", False)
        elif kind == "close_input":
            self.text_input = None
        elif kind == "exit_reason":
            self.exit_reason = data
        elif kind == "version" or kind == "client_path":
            self.version = data.get("version", data.get("text", self.version))
        if self.generation_active():
            # Redraws during generation still describe the previous level.
            self.generation_refresh = set()
        elif self.generation_refresh is not None:
            # Popping the progress UI flushes its own frame before the native
            # level transition publishes player/map state. Keep waiting across
            # that quiet gap, including timeouts and later observe requests.
            if kind == "player" and any(key in data for key in ("place", "depth", "turn", "pos")):
                self.generation_refresh.add("player")
            if kind == "map" and data.get("cells"):
                self.generation_refresh.add("map")
            if kind == "flush_messages" and self.generation_refresh >= {"player", "map"}:
                self.generation_refresh = None
        if self.inspection and kind in {"menu", "ui-push", "ui-state", "ui-stack",
                                        "update_menu", "update_menu_items", "close_menu",
                                        "ui-pop", "close_all_menus"}:
            if self.ui:
                self.inspection["opened"] = True
                self._capture_item_name()
            elif self.inspection["opened"]:
                self.inspection = None
        if self.guard_observer is not None:
            self.guard_observer(self, event)
        if self.public_refresh is not None:
            refresh = self.public_refresh
            if kind == "version":
                refresh.update(seen={"version"}, complete=False)
            elif "version" in refresh["seen"]:
                if kind in ("ui-stack", "input_mode"):
                    refresh["seen"].add(kind)
                if kind == "player" and isinstance(event.get("inv"), dict) and "pos" in event:
                    refresh["seen"].add("player")
                if kind == "map" and data.get("clear") and data.get("cells"):
                    refresh["seen"].add("map")
                if kind == "flush_messages" and refresh["seen"] >= {"version", "player", "map", "ui-stack", "input_mode"}:
                    refresh["complete"] = True
        if self.map_probe is not None:
            if kind in ("version", "ui_state"):
                self.map_probe["seen"].add(kind)
            if kind == "map" and data.get("clear"):
                self.map_probe["seen"].add("map")
            if kind == "cursor" and data.get("id") == 2:
                self.map_probe["seen"].add("cursor")
            if kind == "flush_messages" and self.map_probe["seen"] >= {"version", "ui_state", "map", "cursor"}:
                self.map_probe["complete"] = True

    @staticmethod
    def visible(cell):
        # Matches WebTiles map_knowledge.visible and tile-flags.h.
        if "t" not in cell:
            return False
        bg = cell["t"].get("bg", 0)
        if isinstance(bg, list):
            bg = bg[0]
        return not (bg & 0x60000)

    def observation(self):
        fields = ("name species title hp hp_max mp mp_max ac ev sh str int dex "
                  "xl progress god piety_rank gold place depth turn time pos "
                  "status doom doom_desc contam weapon_index quiver_desc "
                  "poison_survival real_hp_max dd_real_mp_max penance ostracism_pips "
                  "form ac_mod ev_mod sh_mod lives deaths species_display_name "
                  "offhand_index offhand_weapon unarmed_attack quiver_item quiver_available "
                  "noise adjusted_noise wizard explore time_last_input weapon_colour "
                  "offhand_weapon_colour").split()
        player = clean_ui({key: self.player[key] for key in fields
                           if key in self.player})
        inventory = []
        for slot, item in self.player.get("inv", {}).items():
            if item.get("quantity", 0) > 0:
                entry = {key: item[key] for key in ("name", "quantity")
                         if key in item}
                entry["slot"] = int(slot)
                entry["category"] = ITEM_CLASSES.get(item.get("base_type"), "unknown")
                entry["letter_namespace"] = "equipment" if int(slot) < 52 else entry["category"]
                entry["name_current"] = slot not in self.inventory_refresh
                letter = item.get("letter")
                if isinstance(letter, int) and letter > 0:
                    entry["letter"] = chr(letter)
                inventory.append(clean_ui(entry))
        pos = self.player.get("pos", {"x": 0, "y": 0})
        x0, y0 = pos["x"] - 8, pos["y"] - 8
        rows, visibility = [], []
        for y in range(y0, y0 + 17):
            row, mask = "", ""
            for x in range(x0, x0 + 17):
                cell = self.cells.get((x, y), {})
                row += cell.get("g") or " "
                visible = self.visible(cell)
                mask += "v" if visible else " "
            rows.append(row)
            visibility.append(mask)
        names = {point: name for point, (signature, name) in self.item_names.items()
                 if item_signature(self.cells.get(point, {})) == signature}
        features, monsters = VisibleMap(self.cells, pos, self.visible, names).observations()
        scenery_entries, active_monsters = [], []
        for mon in monsters:
            cell = self.cells[(mon["x"], mon["y"])]
            (scenery_entries if scenery(cell) else active_monsters).append(mon)
        mode = MODES[self.mode] if self.mode in range(len(MODES)) else self.mode
        if self.text_input is not None or self.map_radius_context is not None:
            mode = "prompt"
        elif self.ui_state == 2:
            mode = "map"
        skills_open = (self.ui and self.ui[-1].get("type") == "crt"
                       and self.ui[-1].get("tag") == "skills")
        text = {area: "\n".join((skills_text if skills_open and area == "menu_txt"
                                else plain)(lines[key]) for key in
                               sorted(lines, key=int)).rstrip()
                for area, lines in self.text.items()}
        # CRT text belongs to the top CRT popup, not a later structured menu.
        if not self.ui or self.ui[-1].get("type") != "crt":
            text.pop("menu_txt", None)
        return {"player": player, "inventory": inventory, "level_map": level_map(self),
                "map_input": ({"kind": "exclusion_radius", "source": "adapter_buffer",
                               "choices": "1-8", "cancel": "Escape", "native_command_sent": False}
                              if self.map_radius_context is not None else None),
                "map": {"origin": [x0, y0], "rows": rows,
                        "visible": visibility},
                "monsters": active_monsters, "scenery": scenery_entries,
                "unseen_threat": copy.deepcopy(self.unseen_threat),
                "spells": spell_menu(self.ui),
                "targeting": targeting(self, [plain(m.get("text", "")) for m in self.input_messages]),
                "visible_features": features,
                "messages": [clean_ui(msg) for msg in self.messages],
                "input_mode": mode, "text_input": clean_ui(self.text_input), "more": self.more,
                "more_text": plain(self.more_text) if self.more else "",
                "ui": clean_ui(self.ui), "text": text,
                "exit_reason": self.exit_reason, "version": self.version}
