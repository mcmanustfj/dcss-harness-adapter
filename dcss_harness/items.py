"""Equipment appearances decoded from the player's public ground-item tiles."""

import json

from .paths import DATA


EQUIPMENT = {int(key): value for key, value in json.loads(
    (DATA / "crawl_equipment.json").read_text()).items()}
STACK_FLAGS = 0x40000 | 0x100000000000000 | 0x200000000000000


def tile_flags(value):
    if isinstance(value, list):
        return (value[0] & 0xffffffff) | ((value[1] & 0xffffffff) << 32)
    # WebTiles emits a signed 32-bit scalar when the high word is zero.
    # A negative scalar is not a sign-extended 64-bit flag set.
    return value & 0xffffffff if isinstance(value, int) and value < 0 else value or 0


def equipment_name(cell):
    if cell.get("mon") or cell.get("t", {}).get("cloud"):
        return None
    tile = tile_flags(cell.get("t", {}).get("fg")) & 0xffff
    equipment = EQUIPMENT.get(tile)
    if not equipment:
        return None
    kind = {"(": "missiles", ")": "weapon", "[": "armour"}.get(cell.get("g"))
    if kind not in {"weapon", "armour"}:
        return None  # Never interpret a monster/player or obscured item sprite.
    if equipment["kind"] not in {kind, "equipment"}:
        return None
    base = equipment["base"] or kind
    appearance = equipment["appearance"]
    return f"{appearance} {base}" if appearance in {"magic", "randart", "unrandart"} else base


def item_signature(cell):
    """Association guard, not an item identity; never usable for hidden stacks."""
    tiles = cell.get("t", {})
    fg = tile_flags(tiles.get("fg"))
    if (not tiles or tile_flags(tiles.get("bg")) & 0x60000
            or cell.get("mon") or tiles.get("cloud") or fg & STACK_FLAGS):
        return None
    return (cell.get("g"), cell.get("mf"), cell.get("f"), fg)
