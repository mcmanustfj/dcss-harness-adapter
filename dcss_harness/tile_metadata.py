"""Read static tile IDs and variation counts for metadata generators."""

import re


def main_tile_definitions(source):
    """Return enum IDs, main-tile counts, and generated main-tile definitions."""
    tiles = source / "rltiles"
    ids = {}
    for family in ("floor", "wall", "feat", "main"):
        body = re.search(r"enum tile_\w+_type\s*\{(.*?)\}",
                         (tiles / f"tiledef-{family}.h").read_text(), re.S)[1]
        value = 0
        for entry in body.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("=")
            if len(parts) == 2:
                token = parts[1].strip()
                value = int(token) if token.isdigit() else ids[token]
            ids[parts[0].strip()] = value
            value += 1
    definitions = (tiles / "tiledef-main.cc").read_text()
    counts = [int(n) for n in re.findall(r"\d+", re.search(
        r"_tile_main_count\[.*?\]\s*=\s*\{(.*?)\}", definitions, re.S)[1])]
    return ids, counts, definitions
