#!/usr/bin/env python3
"""Regenerate public terrain metadata from external Crawl (requires c++).

Only static terrain definitions are read; no game state or save data.
Run after changing Crawl versions, using its default TAG_MAJOR_VERSION.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcss_harness.paths import DATA
import re
import subprocess

from dcss_harness.paths import generator_source


def generate(source):
    definitions = subprocess.run(
        ["c++", "-E", "-P", "-x", "c++", "-I", str(source), "-"],
        input='#include "dungeon-feature-type.h"\n#include "feature-data.h"\n',
        text=True, capture_output=True, check=True).stdout
    enum = re.search(r"enum dungeon_feature_type\s*\{(.*?)\}",
                     definitions, re.S)[1]
    ids, value = {}, 0
    for name, explicit in re.findall(r"(DNGN_\w+)(?:\s*=\s*(\d+))?", enum):
        value = int(explicit) if explicit else value
        ids[name] = value
        value += 1
    features = {}
    for body in re.findall(r"\{\s*(DNGN_.*?)\}", definitions, re.S):
        fields = [field.strip() for field in body.split(",")]
        if fields[0] not in ids:
            continue
        # C macros can emit adjacent string literals.
        name = "".join(json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', fields[1]))
        features[str(ids[fields[0]])] = {
            "id": fields[0][5:].lower(), "name": name,
            "flags": re.findall(r"FFT_(\w+)", fields[-3]),
            "minimap": fields[-2].removeprefix("MF_").lower()}
    if len(features) < 150 or not any(f["id"] == "floor" for f in features.values()):
        raise ValueError("Unrecognized Crawl feature definitions")
    destination = (DATA / "crawl_features.json")
    destination.write_text("{\n" + ",\n".join(
        f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in sorted(features.items(), key=lambda item: int(item[0]))) + "\n}\n")


if __name__ == "__main__":
    generate(generator_source())
