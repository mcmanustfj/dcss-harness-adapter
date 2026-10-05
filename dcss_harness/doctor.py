"""Check an existing binary's reported build without starting a game."""

import json
import re
import subprocess

from .paths import ROOT, binary_path


def diagnose(binary=None, source=None, timeout=5):
    expected = json.loads((ROOT / "crawl-version.json").read_text())
    result = {"ok": False, "binary": None, "expected_version": expected["version"],
              "expected_revision": expected["revision"], "reported_version": None,
              "webtiles": None, "status": "unverified"}
    try:
        path = binary_path(binary, source)
        result["binary"] = str(path)
        probe = subprocess.run([str(path), "-version"], cwd=path.parent,
                               stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, errors="replace", timeout=timeout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        result.update(status="probe_failed", reason=str(error))
        return result
    output = probe.stdout + probe.stderr
    result["version_output"] = output[:8192]
    if probe.returncode:
        result.update(status="probe_failed", reason=f"-version exited with code {probe.returncode}")
        return result
    version = re.search(r"^Crawl version (\S+)\s*$", output, re.M)
    result["reported_version"] = version[1] if version else None
    flags = re.search(r"^CFLAGS:\s*(.*)$", output, re.M)
    if flags:
        result["webtiles"] = bool(re.search(r"(?:^|\s)-DUSE_TILE_WEB(?:=1)?(?:\s|$)", flags[1]))
    if not version or result["webtiles"] is None:
        result.update(reason="Version output does not establish the Crawl version and WebTiles build flag")
    elif not result["webtiles"]:
        result.update(status="webtiles_missing", reason="The reported build flags do not enable WebTiles")
    elif result["reported_version"] != result["expected_version"]:
        result.update(status="version_mismatch", reason="The reported build does not match the bundled metadata")
    else:
        result.update(ok=True, status="matched",
                      reason="Reported Crawl version and WebTiles flag match the tested build; "
                             "runtime data and local binary modifications are not verified")
    return result
