"""Bootstrap the public CLI with a changelog baseline before daemon imports."""

import sys


def main():
    from .changes import read_changelog
    from .paths import ROOT

    baseline = read_changelog(ROOT)
    from . import daemon
    from .cli import main as run

    daemon.STARTUP_CHANGELOG = baseline
    return run()


if __name__ == "__main__":
    sys.exit(main())
