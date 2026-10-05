"""Visible landmarks and conservative local routes from public WebTiles cells."""

import heapq
import json

from .items import equipment_name, tile_flags
from .paths import DATA
from .safety import monster_appearance


# Generated from the same checkout's public terrain definitions. Unknown IDs
# are never treated as floor. Regenerate with generate_crawl_features.py.
FEATURES = {int(key): value for key, value in json.loads(
    (DATA / "crawl_features.json").read_text()).items()}
CLOUDS = {int(key): value for key, value in json.loads(
    (DATA / "crawl_clouds.json").read_text()).items()}
MOVES = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0),
         "ne": (1, -1), "se": (1, 1), "sw": (-1, 1), "nw": (-1, -1)}
WORDS = {"n": "north", "e": "east", "s": "south", "w": "west",
         "ne": "northeast", "se": "southeast", "sw": "southwest", "nw": "northwest"}
OPENABLE = {"closed_door", "closed_clear_door"}
FLOORS = {"floor", "decorative_floor", "runelight", "open_door",
          "open_clear_door", "broken_door", "broken_clear_door",
          "abandoned_shop", "transporter_landing"}
ITEM_GLYPHS = {
    ")": "weapon", "(": "missiles", "[": "armour", "?": "scroll",
    "!": "potion", "=": "ring", '"': "amulet", "/": "wand", "|": "staff",
    ":": "book", "$": "gold", "%": "talisman or food", "}": "miscellaneous item",
    "†": "corpse", "÷": "skeleton", "♦": "gem", "•": "bauble", "φ": "rune",
    "0": "Orb"}


def terrain(cell):
    return FEATURES.get(cell.get("f"), {})


def terrain_neighborhood(state, dx, dy):
    """Nine public terrain cells, including diagonals; never infer safety."""
    if any(type(v) is not int or abs(v) > 80 for v in (dx, dy)):
        raise ValueError("Terrain offsets must be integers from -80 to 80")
    pos = state.player.get("pos") or {}
    if any(type(pos.get(k)) is not int for k in ("x", "y")) or state.map_player_on_level is False:
        raise ValueError("Terrain query requires a known player origin on the current level")
    cells = []
    for oy in range(dy - 1, dy + 2):
        for ox in range(dx - 1, dx + 2):
            x, y = pos["x"] + ox, pos["y"] + oy
            cell = state.cells.get((x, y), {})
            feature = terrain(cell)
            known = bool(feature and feature.get("id") != "unseen")
            cells.append({"dx": ox, "dy": oy, "x": x, "y": y,
                          "visibility": "visible" if state.visible(cell) else "remembered" if known else "unknown",
                          "terrain_id": feature.get("id") if known else None,
                          "name": feature.get("name") if known else None})
    return {"center": {"dx": dx, "dy": dy}, "origin": dict(pos),
            "place": state.player.get("place"), "depth": state.player.get("depth"), "cells": cells}


def level_map(state):
    """Public CURSOR_MAP (2), independent of the combat targeting cursor."""
    if state.ui_state != 2 or (state.exit_reason or {}).get("type", "unknown") != "unknown":
        return None
    cursor = state.cursors.get(2)
    result = {"cursor": None, "selected_feature": None,
              "player_on_level": state.map_player_on_level,
              "level": ({"place": state.player.get("place"), "depth": state.player.get("depth")}
                        if state.map_player_on_level is True else None)}
    if not cursor or any(type(cursor.get(k)) is not int for k in ("x", "y")):
        return result
    cell = state.cells.get((cursor["x"], cursor["y"]), {})
    feature = terrain(cell)
    known = bool(feature and feature.get("id") != "unseen")
    visible = state.visible(cell) and state.map_player_on_level is not False
    result["cursor"] = {"x": cursor["x"], "y": cursor["y"],
                        "visibility": "visible" if visible else "remembered" if known else "unknown"}
    pos = state.player.get("pos") or {}
    if state.map_player_on_level is True and all(type(pos.get(k)) is int for k in ("x", "y")):
        result["cursor"].update(dx=cursor["x"] - pos["x"], dy=cursor["y"] - pos["y"])
    if known:
        result["selected_feature"] = {"id": feature["id"], "name": feature["name"],
                                      "glyph": cell.get("g")}
    return result


def movement(cell, ignore_monsters=False):
    """Only terrain with ordinary walking semantics; abilities aren't inferred."""
    feature = terrain(cell)
    name = feature.get("id", "")
    tiles = cell.get("t", {})
    bg = tile_flags(tiles.get("bg"))
    fg = tile_flags(tiles.get("fg"))
    if ((cell.get("mon") and not ignore_monsters) or tiles.get("cloud") or cell.get("g") == "{"
            or bg & (0xc00000 | 0xc000000000) or fg & 0x4000000000):
        # Travel exclusions and invisible disturbances also invalidate a route.
        return None
    if name in OPENABLE:
        return "door"
    if (name in FLOORS or name.startswith("altar_")
            or feature.get("minimap", "").startswith("stair")):
        return "walk"
    return None


def connected_groups(points):
    """Consume equal-labelled eight-connected public cells, never filling gaps."""
    while points:
        first = min(points, key=lambda p: (p[1], p[0]))
        label = points.pop(first)
        group, pending = [first], [first]
        while pending:
            x, y = pending.pop()
            for dx, dy in MOVES.values():
                point = (x + dx, y + dy)
                if points.get(point) == label:
                    del points[point]
                    group.append(point)
                    pending.append(point)
        yield label, sorted(group, key=lambda p: (p[1], p[0]))


class VisibleMap:
    def __init__(self, cells, pos, visible, item_names=None):
        self.start = (pos["x"], pos["y"])
        self.cells = {point: cell for point, cell in cells.items() if visible(cell)}
        self.walk = {point: movement(cell) for point, cell in self.cells.items()}
        self.paths = self.routes(self.walk)
        self.occupied_paths = None
        self.item_names = item_names or {}

    def offset(self, point):
        return {"dx": point[0] - self.start[0], "dy": point[1] - self.start[1]}

    def routes(self, walk):
        # Crawl permits eight-way movement, including between diagonal walls.
        # Minimize actions (opening a door costs one), then emitted segments.
        # Incoming direction is part of the search state: equal-cost arrivals
        # can produce different segment counts on the rest of the path.
        if self.start not in self.cells:
            return {}
        start = (self.start, "")
        scores, parents = {start: (0, 0)}, {}
        ends = {self.start: start}
        queue = [(0, 0, self.start, "")]
        while queue:
            cost, segments, point, previous = heapq.heappop(queue)
            node = (point, previous)
            if (cost, segments) != scores[node]:
                continue
            for direction, (dx, dy) in MOVES.items():
                target = (point[0] + dx, point[1] + dy)
                access = walk.get(target)
                if access is None:
                    continue
                new_cost = cost + (2 if access == "door" else 1)
                new_segments = segments + (2 if access == "door" else int(previous != direction))
                score = (new_cost, new_segments)
                following = (target, direction)
                if following in scores and scores[following] <= score:
                    continue
                scores[following] = score
                parents[following] = (node, access)
                if target not in ends or score < scores[ends[target]]:
                    ends[target] = following
                heapq.heappush(queue, (*score, target, direction))
        paths = {}
        for point, end in ends.items():
            node, path = end, []
            while node != start:
                parent, access = parents[node]
                path.append((node[1], access))
                node = parent
            paths[point] = (scores[end], list(reversed(path)))
        return paths

    def occupied_reason(self, point, candidates, approach):
        # A counterfactual route is evidence only if removing visible occupancy
        # alone connects the target. Never relax fog, hazards, or unknown terrain,
        # and never return these diagnostic paths as movement instructions.
        if self.occupied_paths is None:
            relaxed = {p: movement(cell, ignore_monsters=True)
                       for p, cell in self.cells.items()}
            self.occupied_paths = self.routes(relaxed)
        choices = sorted((self.occupied_paths[p][0], p, self.occupied_paths[p][1])
                         for p in candidates if p in self.occupied_paths)
        for _, _, path in choices:
            current, blockers = self.start, []
            crosses_target = False
            for direction, _ in path:
                dx, dy = MOVES[direction]
                current = (current[0] + dx, current[1] + dy)
                crosses_target |= approach and current == point
                mon = self.cells[current].get("mon")
                if mon:
                    blockers.append({"name": mon.get("name", "monster"), **self.offset(current)})
            if blockers and not crosses_target:
                return {"kind": "visible_occupancy", "blockers": blockers}
        return None

    def navigation(self, point, approach=False):
        candidates = [point]
        if approach:
            candidates = [(point[0] + dx, point[1] + dy)
                          for dx, dy in MOVES.values()]

        def choices(paths):
            result = []
            for candidate in candidates:
                if candidate in paths:
                    score, path = paths[candidate]
                    result.append((score, candidate, path))
            return result

        found = choices(self.paths)
        if not found:
            result = {"status": "unknown", "text": "no verified visible route"}
            reason = self.occupied_reason(point, candidates, approach)
            if reason:
                result["reason"] = reason
                names = ", ".join(b["name"] for b in reason["blockers"])
                result["text"] += f"; visible approach occupied by {names}"
            return result
        _, end, path = min(found)
        steps = []
        for direction, access in path:
            if access == "door":
                steps.append({"action": "open_door", "direction": direction})
            if steps and steps[-1].get("move") == direction:
                steps[-1]["count"] += 1
            else:
                steps.append({"move": direction, "count": 1})
        needs_open = any("action" in step for step in steps)
        parts = [(f"{step['count']} {WORDS[step['move']]}" if "move" in step
                  else f"open door {WORDS[step['direction']]}") for step in steps]
        text = ", then ".join(parts) if parts else "here"
        result = {"status": "requires_open_door" if needs_open else "visible_route",
                  "target": "adjacent" if approach else "cell",
                  "steps": steps, "text": text}
        if approach:
            direction = next(name for name, delta in MOVES.items()
                             if delta == (point[0] - end[0], point[1] - end[1]))
            result["target_direction"] = direction
            result["text"] += f"; target adjacent {WORDS[direction]}"
        return result

    def location(self, point, approach=False):
        result = {**self.offset(point), "navigation": self.navigation(point, approach)}
        # Only annotate unambiguous straight cardinal/diagonal approaches.
        dx, dy = point[0] - self.start[0], point[1] - self.start[1]
        if dx == 0 or dy == 0 or abs(dx) == abs(dy):
            sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
            barriers = []
            for index in range(1, max(abs(dx), abs(dy))):
                between = (self.start[0] + index * sx, self.start[1] + index * sy)
                feature = terrain(self.cells.get(between, {}))
                if "SOLID" in feature.get("flags", []):
                    barriers.append({"name": feature["name"], **self.offset(between)})
            if barriers:
                result["direct_path_barriers"] = barriers
                nav = result["navigation"]
                if nav["status"] == "unknown" and "reason" not in nav:
                    nav["reason"] = {"kind": "direct_barrier", "blockers": barriers}
                    names = ", ".join(b["name"] for b in barriers)
                    nav["text"] += f"; {names} blocks direct approach"
        return result

    def observations(self):
        features, monsters, areas, clouds = [], [], {}, {}
        for point, cell in sorted(self.cells.items(), key=lambda pair: (pair[0][1], pair[0][0])):
            feature = terrain(cell)
            name = feature.get("id", "")
            flags = feature.get("flags", [])
            group = feature.get("minimap", "")
            mon = cell.get("mon")
            if mon:
                appearance = monster_appearance(cell)
                location = (self.location(point, approach=True)
                            if appearance["location_status"] == "visible" else
                            {**self.offset(point), "navigation": {"status": "unknown",
                             "text": "invisible marker is not a verified attack target"}})
                monsters.append({"x": point[0], "y": point[1], **{
                    key: mon[key] for key in ("id", "name", "att", "threat", "type")
                    if key in mon}, **appearance, **location})
            if "SOLID" in flags and group != "door":
                # Ordinary walls remain in the ASCII crop. Expose transparent
                # barriers and special obstacles, without flooding every turn.
                if "OPAQUE" not in flags or name in {"slimy_wall", "frigid_wall", "spike_launcher"}:
                    areas[point] = ("barrier", feature["name"], name)
            elif name and name not in {"unseen", "floor", "decorative_floor", "runelight"}:
                kind = ("door" if group == "door" or "door" in name else
                        "stairs" if group.startswith("stair") else
                        "hazard" if "TRAP" in flags or group in {"water", "deep_water", "lava", "trap"}
                        or name in {"toxic_bog", "mud", "binding_sigil"} else "terrain")
                if kind == "hazard" and "TRAP" not in flags:
                    areas[point] = (kind, feature["name"], name)
                else:
                    approach = point != self.start and (bool(mon) or self.walk.get(point) != "walk")
                    entry = {"kind": kind, "name": feature["name"], **self.location(point, approach)}
                    if name in OPENABLE:
                        entry["interaction"] = "open_door"
                    elif name.startswith("runed_") and "door" in name:
                        entry["interaction"] = "open_runed_door_requires_confirmation"
                    elif name.startswith("sealed_"):
                        entry["interaction"] = "sealed"
                    features.append(entry)
            cloud = cell.get("t", {}).get("cloud")
            glyph = cell.get("g")
            # mf reports an item even when covered by a monster; an item glyph
            # also reveals items on stairs, which take minimap precedence.
            if (point in self.item_names or cell.get("mf") == 6
                    or (not mon and not cloud and glyph in ITEM_GLYPHS)):
                label = ITEM_GLYPHS.get(glyph, "item") if not mon and not cloud else "item"
                label = self.item_names.get(point) or equipment_name(cell) or label
                features.append({"kind": "item", "name": label,
                                 **self.location(point, point != self.start and
                                                 (bool(mon) or self.walk.get(point) != "walk"))})
            if cloud or glyph == "{":
                label = CLOUDS.get(cloud, {"name": "unknown cloud", "cloud_type": "unknown",
                                           "tile": cloud or None})
                clouds[point] = label
        # Eight-connected clouds with the same public appearance share one
        # approach route. Keep exact cells, including clouds under the player.
        def approach_score(point):
            if point == self.start:
                return (0, 0), point[1], point[0]
            scores = [self.paths[p][0] for dx, dy in MOVES.values()
                      if (p := (point[0] + dx, point[1] + dy)) in self.paths]
            return min(scores, default=(float("inf"), 0)), point[1], point[0]

        for label, group in connected_groups(clouds):
            anchor = min(group, key=approach_score)
            features.append({"kind": "hazard", **label,
                             **self.location(anchor, anchor != self.start),
                             "cells": [[p[0] - self.start[0], p[1] - self.start[1]]
                                       for p in group]})
        # Terrain groups retain exact visible cells and one useful approach.
        # Water depths/terrain names remain distinct; membership is not a route.
        for (kind, name, terrain_id), group in connected_groups(areas):
            anchor = min(group, key=approach_score)
            entry = {"kind": kind, "name": name, "terrain_id": terrain_id,
                     **self.location(anchor, anchor != self.start),
                     "cells": [[p[0] - self.start[0], p[1] - self.start[1]] for p in group]}
            if kind == "barrier":
                entry["blocks_movement"] = True
            features.append(entry)
        return features, monsters
