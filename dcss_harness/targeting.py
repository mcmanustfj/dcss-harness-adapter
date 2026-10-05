"""Targeting output from public cursor, preview overlays and message feedback."""

import re

from .items import tile_flags
from .safety import CONSTANTS, monster_appearance


OVERLAYS = {CONSTANTS[name]: label for name, label in (
    ("TILE_RAY", "affected_or_path"), ("TILE_RAY_MULTI", "multiple"),
    ("TILE_RAY_OUT_OF_RANGE", "possible_blocked_or_out_of_range"),
    ("TILE_LANDING", "landing"))}


def targeting(state, feedback):
    if (state.mode not in (2, 3, 4) or state.ui
            or (state.exit_reason or {}).get("type", "unknown") != "unknown"):
        return None
    pos = state.player.get("pos", {"x": 0, "y": 0})

    def location(point):
        cell = state.cells.get(point, {})
        return {"x": point[0], "y": point[1], "dx": point[0] - pos["x"],
                "dy": point[1] - pos["y"], "visible": state.visible(cell)}

    cursor = state.cursors.get(0)
    point = (cursor["x"], cursor["y"]) if cursor else None
    cell = state.cells.get(point, {})
    selected = cell.get("mon") if state.visible(cell) else None
    marker = None
    if selected and monster_appearance(cell)["location_status"] != "visible":
        marker = {"name": selected.get("name"),
                  "location_status": monster_appearance(cell)["location_status"]}
        selected = None
    if selected:
        selected = {k: selected[k] for k in ("id", "name", "att", "threat") if k in selected}
    preview, landings, invalid = [], [], []
    for p, c in sorted(state.cells.items(), key=lambda pair: (pair[0][1], pair[0][0])):
        tiles = c.get("t", {})
        if state.visible(c) and tile_flags(tiles.get("bg")) & 0x2000000:
            invalid.append([p[0] - pos["x"], p[1] - pos["y"]])
        for overlay in tiles.get("ov") or []:
            if overlay in OVERLAYS:
                entry = {**location(p), "kind": OVERLAYS[overlay]}
                (landings if entry["kind"] == "landing" else preview).append(entry)
    text = "\n".join(feedback)
    range_status = ("out_of_range" if re.search(r"\bout of range\b", text, re.I) else
                    "marked_invalid" if point and tile_flags(cell.get("t", {}).get("bg")) & 0x2000000 else
                    "unknown")
    blocked = re.search(r"fire blocked by|line of fire.*blocked|no line of fire|something in the way", text, re.I)
    return {"cursor": location(point) if point else None,
            "selected_monster": selected, "invisible_marker": marker,
            "range": range_status, "line_of_fire": "blocked" if blocked else "unknown",
            "preview_cells": preview, "landing_cells": landings,
            "invalid_aim_cells": invalid, "impact_point": None, "feedback": feedback}
