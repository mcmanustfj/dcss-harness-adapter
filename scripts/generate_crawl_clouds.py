#!/usr/bin/env python3
"""Generate cloud appearance labels from public tile assets and static names.

No save data or live game internals are read. Shared sprites retain all
possible types, particularly the indistinguishable grey smoke/steam tile.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcss_harness.paths import DATA
import re

from dcss_harness.paths import generator_source
from dcss_harness.tile_metadata import main_tile_definitions


def generate(source):
    ids, counts, _ = main_tile_definitions(source)
    table = (source / "cloud.cc").read_text().split(
        "static const cloud_data clouds[] = {", 1)[1].split("COMPILE_CHECK", 1)[0]
    entries = re.split(r"// CLOUD_(\w+),", table)
    labels = {}
    for kind, body in zip(entries[1::2], entries[2::2]):
        name = re.search(r'\{\s*"([^"]+)"', body)[1]
        symbols = re.findall(r"TILE_CLOUD_\w+", body)
        if "CTVARY_MUTAGENIC" in body:
            symbols = [f"TILE_CLOUD_MUTAGENIC_{n}" for n in range(3)]
        elif "CTVARY_VORTEX" in body:
            symbols = [f"TILE_CLOUD_FREEZING_WINDS_{n}" for n in range(2)]
        for symbol in symbols:
            base = ids[symbol]
            for tile in range(base, base + counts[base - ids["TILE_FEAT_MAX"]]):
                labels.setdefault(tile, {})[kind.lower()] = name
    result = {}
    for tile, candidates in sorted(labels.items()):
        entry = {"name": " or ".join(dict.fromkeys(candidates.values())),
                 "cloud_type": next(iter(candidates)) if len(candidates) == 1 else "unknown"}
        if len(candidates) > 1:
            entry["possible_types"] = list(candidates)
        result[str(tile)] = entry
    if len(result) < 70:
        raise ValueError("Unrecognized cloud tile definitions")
    (DATA / "crawl_clouds.json").write_text(
        json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    generate(generator_source())
