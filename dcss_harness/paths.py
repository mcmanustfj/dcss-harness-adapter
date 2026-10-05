"""INI settings and explicit external Crawl paths; never search a surrounding checkout."""

from pathlib import Path
import argparse
import configparser
import os


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ASSETS = ROOT / "assets"


def add_config_argument(parser):
    parser.add_argument("--config", type=Path,
                        help="INI settings file (default: settings.ini beside crawl-agent)")


def read_settings(config=None):
    """Read optional settings; paths are relative to the file, not invocation cwd."""
    selected = config or os.environ.get("CRAWL_AGENT_CONFIG")
    path = Path(selected).expanduser().resolve() if selected else ROOT / "settings.ini"
    if not path.exists() and not selected:
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open() as stream:
            parser.read_file(stream)
        result = {}
        if parser.has_section("crawl"):
            unknown = set(parser["crawl"]) - {"binary", "source"}
            if unknown:
                raise ValueError("Unknown [crawl] settings: " + ", ".join(sorted(unknown)))
            for key, value in parser["crawl"].items():
                if value.strip():
                    entry = Path(value.strip()).expanduser()
                    result[key] = (path.parent / entry).resolve()
        if parser.has_section("output"):
            unknown = set(parser["output"]) - {"snapshot_tags"}
            if unknown:
                raise ValueError("Unknown [output] settings: " + ", ".join(sorted(unknown)))
            if "snapshot_tags" in parser["output"]:
                result["snapshot_tags"] = parser.getboolean("output", "snapshot_tags")
        unknown_sections = set(parser.sections()) - {"crawl", "output"}
        if unknown_sections:
            raise ValueError("Unknown settings sections: " + ", ".join(sorted(unknown_sections)))
        return result
    except (OSError, configparser.Error, ValueError) as error:
        raise ValueError(f"Cannot read settings {path}: {error}") from error


def configured_paths(args, settings=None):
    if settings is None:
        settings = read_settings(args.config)
    return (getattr(args, "binary", None) or os.environ.get("CRAWL_BINARY") or settings.get("binary"),
            getattr(args, "crawl_source", None) or os.environ.get("CRAWL_SOURCE") or settings.get("source"))


def add_source_argument(parser):
    parser.add_argument(
        "--crawl-source", type=Path,
        help="External crawl-ref/source directory (or CRAWL_SOURCE)")


def source_path(value):
    if not value:
        raise ValueError("Set [crawl] source in settings.ini, --crawl-source, or CRAWL_SOURCE to an external "
                         "Crawl crawl-ref/source directory")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Crawl source directory does not exist: {path}")
    return path


def binary_path(binary=None, source=None):
    if binary:
        path = Path(binary).expanduser().resolve()
    elif source:
        path = source_path(source) / "crawl"
    else:
        raise ValueError("Set [crawl] binary in settings.ini, --binary, or CRAWL_BINARY to an existing WebTiles Crawl binary, "
                         "or set --crawl-source / CRAWL_SOURCE")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"Crawl binary is missing or not executable: {path}. "
                         "Select an existing WebTiles-enabled executable")
    return path


def generator_source():
    parser = argparse.ArgumentParser(description="Regenerate adapter metadata from external Crawl")
    add_config_argument(parser)
    add_source_argument(parser)
    args = parser.parse_args()
    try:
        return source_path(configured_paths(args)[1])
    except ValueError as error:
        parser.error(str(error))
