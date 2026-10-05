#!/usr/bin/env python3
"""Generate public enum/appearance constants for this checkout, never live state."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcss_harness.paths import DATA
import re
import subprocess
import tempfile

from dcss_harness.paths import generator_source


def generate(source):
    object_names = list(dict.fromkeys(re.findall(r"\bOBJ_\w+", (source / "object-class-type.h").read_text())))
    # Names conditional on older formats are not needed for the current UI.
    object_names = [s for s in object_names if s not in {"OBJ_FOOD", "OBJ_RODS", "OBJ_RANDOM", "OBJ_DETECTED"}]
    icons = list(dict.fromkeys(re.findall(r"\bTILEI_\w+", (source / "rltiles/tiledef-icons.h").read_text().split("};", 1)[0])))
    weapons = list(dict.fromkeys(re.findall(r"\bTILEP_HAND1_\w+", (source / "rltiles/tiledef-player.h").read_text().split("};", 1)[0])))
    names = object_names + icons + weapons + ["MONS_PLANT", "MONS_FUNGUS", "MONS_BUSH", "MONS_PILE_OF_DEBRIS", "MONS_PETRIFIED_FLOWER",
        "ATT_HOSTILE", "ATT_NEUTRAL", "ATT_GOOD_NEUTRAL", "ATT_FRIENDLY",
        "TILE_FLAG_BEH_MASK", "TILE_FLAG_FLEEING",
        "TILE_RAY", "TILE_RAY_MULTI", "TILE_RAY_OUT_OF_RANGE", "TILE_LANDING"]
    code = '#include <iostream>\n#include <cassert>\n#define ASSERT assert\n#include "object-class-type.h"\n#include "monster-type.h"\n#include "mon-attitude-type.h"\n#include "tile-flags.h"\n#include "rltiles/tiledef-icons.h"\nint main() {\n'
    code += "\n".join(f'std::cout << "{s} " << int({s}) << "\\n";' for s in names)
    code += "\n}\n"
    with tempfile.TemporaryDirectory() as directory:
        binary = str(Path(directory) / "constants")
        subprocess.run(["c++", "-x", "c++", "-I", str(source), "-o", binary, "-"], input=code, text=True, check=True)
        values = dict(line.split() for line in subprocess.check_output([binary], text=True).splitlines())
    (DATA / "crawl_protocol.json").write_text(json.dumps(
        {k: int(v) for k, v in values.items()}, indent=2) + "\n")


if __name__ == "__main__":
    generate(generator_source())
