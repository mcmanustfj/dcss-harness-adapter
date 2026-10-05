"""Public appearance/uncertainty helpers; no hidden identities or safety claims."""

import json
import re

from .items import tile_flags
from .paths import DATA


CONSTANTS = json.loads((DATA / "crawl_protocol.json").read_text())
ITEM_CLASSES = {v: k[4:].lower() for k, v in CONSTANTS.items() if k.startswith("OBJ_")}
ICONS = {v: k[6:].lower() for k, v in CONSTANTS.items() if k.startswith("TILEI_")}
SCENERY = {CONSTANTS[k] for k in ("MONS_PLANT", "MONS_FUNGUS", "MONS_BUSH", "MONS_PILE_OF_DEBRIS", "MONS_PETRIFIED_FLOWER")}
WEAPON_TILES = {v: k.removeprefix("TILEP_HAND1_").lower() for k, v in CONSTANTS.items()
                if k.startswith("TILEP_HAND1_") and not k.endswith(("FIRST", "LAST", "OFFSET"))}
_UNSEEN_SUBJECT = (
    r"(?:something(?: unseen)?|(?:an?|the) unseen (?:attacker|horror|creature)|"
    r"(?:an?|the) invisible [a-z][a-z'-]*(?:\s+[a-z][a-z'-]*){0,5}?)")
# Match the subject's combat predicate, not an attack-shaped noun later in
# flavour text (e.g. "Something foul drips from Amaemon's claws."). Sentence
# boundaries still allow a real attack after an unrelated sentence in a line.
UNSEEN_ATTACK = re.compile(
    r"(?:^|[.!?])\s*(?:" + _UNSEEN_SUBJECT + r"\s+(?:"
    r"(?:(?:barely|closely|completely)\s+)?"
    r"(?:hits?|bites?|attacks?|miss(?:es)?|claws?|stings?|touch(?:es)?|engulfs?|"
    r"kicks?|pecks?|headbutts?|gore[sd]?|tramples?|punch(?:es)?|constricts?|"
    r"tentacle-slaps?|tail-slaps?|trunk-slaps?)\s+you\b|(?:shoots?|casts?)\b)"
    r"|you are hit by\b|you (?:block|dodge) something(?: unseen)?['’]s attack\b)", re.I)


def monster_appearance(cell):
    tiles = cell.get("t", {})
    icons = [ICONS.get(i, f"unknown_icon:{i}") for i in (tiles.get("icons") or [])]
    fg, bg = tile_flags(tiles.get("fg")), tile_flags(tiles.get("bg"))
    # Native WebTiles draws poison icons from the foreground's two-bit field;
    # these are not necessarily repeated in the separate tile.icons array.
    poison = {1: "poison", 2: "more_poison", 3: "max_poison"}.get((fg >> 59) & 3)
    if poison and poison not in icons:
        icons.append(poison)
    location = ("remembered_invisible" if bg & 0x8000000000 or "unseen_invis_remembered" in icons else
                "invisible_disturbance" if bg & 0x4000000000 or "unseen_invis_known" in icons else "visible")
    # The native browser draws fleeing from a mutually exclusive foreground
    # behaviour field, not necessarily from tile.icons. It indicates current
    # fleeing, not its cause or a durable fear effect inferred from messages.
    if (location == "visible" and fg & CONSTANTS["TILE_FLAG_BEH_MASK"] == CONSTANTS["TILE_FLAG_FLEEING"]
            and "fleeing" not in icons):
        icons.append("fleeing")
    wounds = {0: "unhurt", 1: "lightly wounded", 2: "moderately wounded", 3: "heavily wounded",
              4: "severely wounded", 7: "almost dead"}.get((fg & 0x1c0000000) >> 30, "unknown")
    weapons = []
    if location == "visible":
        for part in (tiles.get("mcache") or []) + (tiles.get("doll") or []):
            label = WEAPON_TILES.get(part[0]) if part else None
            if not label:
                continue
            hint = ("reaching" if re.match(r"(?:spear|trident|demon_trident|trishula|partisan|halberd|glaive|bardiche)(?:_|\d|$)", label) else
                    "ranged" if re.match(r"(?:sling|shortbow|orcbow|longbow|arbalest|triple_crossbow|hand_cannon)(?:_|\d|$)", label) else "unknown")
            entry = {"name": label.replace("_", " "), "attack_hint": hint, "source": "visible_sprite"}
            if entry not in weapons:
                weapons.append(entry)
    return {"location_status": location, "wounds": wounds, "icons": icons,
            "visible_weapons": weapons}


def risk_increase(player, previous):
    """Public percentage meters are independent of status lights.

    Missing fields remain unknown. A first nonzero report also requires a
    decision; unchanged existing risk and decreases do not imply new danger.
    """
    for field in ("doom", "contam"):
        value, old = player.get(field), previous.get(field)
        if type(value) is int and value > (old if type(old) is int else 0):
            return field + "_increased"
    return None


class RiskWatch:
    """Latch an increase even if later public frames reduce/reset the meter."""
    def __init__(self, state, observer=None):
        self.previous = {key: state.player.get(key) for key in ("doom", "contam")}
        self.reason = None
        self.observer = observer

    def __call__(self, state, event):
        if self.observer:
            self.observer(state, event)
        self.reason = self.reason or risk_increase(state.player, self.previous)
        self.previous = {key: state.player.get(key) for key in ("doom", "contam")}


def scenery(cell):
    mon = cell.get("mon") or {}
    return (mon.get("type") in SCENERY and mon.get("typedata", {}).get("no_exp") is True
            and (mon.get("type") != CONSTANTS["MONS_PETRIFIED_FLOWER"]
                 or mon.get("att") in (CONSTANTS["ATT_HOSTILE"], CONSTANTS["ATT_NEUTRAL"],
                                       CONSTANTS["ATT_GOOD_NEUTRAL"], CONSTANTS["ATT_FRIENDLY"]))
            and mon.get("threat") == 0
            and monster_appearance(cell)["location_status"] == "visible"
            and not set(monster_appearance(cell)["icons"]) - {"slowly_dying"})


def recovery_friendly(mon):
    """Known allies with understood appearances; neutral is not friendly.

    Ordinary berserk summons remain aligned. Frenzy, confusion, inner flame
    and any unrecognized status still require controller assessment.
    """
    return (mon.get("att") == CONSTANTS["ATT_FRIENDLY"]
            and mon.get("location_status") == "visible"
            and not set(mon.get("icons", [])) -
            {"friendly", "summoned", "berserk", "unrewarding", "minion", "slowly_dying"})
