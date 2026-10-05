#!/usr/bin/env python3
"""Generate visible equipment labels from external Crawl public tile assets."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcss_harness.paths import DATA
import re

from dcss_harness.paths import generator_source
from dcss_harness.tile_metadata import main_tile_definitions


def generate(source):
    ids, counts, definitions = main_tile_definitions(source)
    offset = ids["TILE_FEAT_MAX"]
    pairs = re.findall(r"tile_variation\((\d+) \+ TILE_FEAT_MAX, ([1-4])\), (\d+) \+ TILE_FEAT_MAX", definitions)
    variants = {}
    for base, appearance, variant in pairs:
        variants.setdefault(int(base) + offset, []).append((int(appearance), int(variant) + offset))
    picker = (source / "tilepick.cc").read_text()
    labels = {}

    def add(tile, category, base, quality):
        for number in range(tile, tile + counts[tile - offset]):
            labels.setdefault(number, set()).add((category, base, quality))

    for category, function, prefix in (("weapon", "weapon", "WPN"), ("armour", "armour", "ARM")):
        body = picker.split(f"static tileidx_t _tileidx_{function}_base", 1)[1].split(
            f"static tileidx_t _tileidx_{function}(", 1)[0]
        for subtype, tile in re.findall(rf"case {prefix}_(\w+):\s*return (TILE_\w+);", body):
            name = subtype.lower().replace("_", " ")
            if "dragon armour" in name:
                name = name.replace("dragon armour", "dragon scales")
            name = {"blessed blade": "eudemon blade",
                    "executioners axe": "executioner's axe"}.get(name, name)
            base = ids[tile]
            add(base, category, name, "plain")
            for appearance, variant in variants.get(base, []):
                add(variant, category, name, "randart" if appearance == 4 else "magic")
    # Distinct public unrand sprites reveal artefact appearance, not its hidden
    # name, brand, or enchantment. The map glyph supplies weapon versus armour.
    for symbol, tile in ids.items():
        if symbol.startswith("TILE_UNRAND_"):
            add(tile, "equipment", "", "unrandart")
    result = {}
    for tile, candidates in sorted(labels.items()):
        kinds = {c[0] for c in candidates}
        bases = {c[1] for c in candidates}
        qualities = {c[2] for c in candidates}
        if len(kinds) != 1 or len(bases) != 1:
            continue  # Ambiguous reused sprites retain their generic glyph label.
        quality = next(iter(qualities)) if len(qualities) == 1 else "unknown"
        result[str(tile)] = {"kind": next(iter(kinds)), "base": next(iter(bases)), "appearance": quality}
    if len(result) < 200:
        raise ValueError("Unrecognized equipment tile definitions")
    (DATA / "crawl_equipment.json").write_text("{\n" + ",\n".join(
        f"  {json.dumps(key)}: {json.dumps(value)}" for key, value in result.items()) + "\n}\n")


if __name__ == "__main__":
    generate(generator_source())
