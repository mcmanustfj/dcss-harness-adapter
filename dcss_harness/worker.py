"""Private daemon entry point used only by detached start."""

import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("daemon_config")
    args = parser.parse_args(argv)
    from pathlib import Path
    from .changes import read_changelog
    from .paths import ROOT

    baseline = read_changelog(ROOT)
    from . import daemon

    daemon.STARTUP_CHANGELOG = baseline
    daemon.serve(Path(args.session_dir).resolve(), json.loads(args.daemon_config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
