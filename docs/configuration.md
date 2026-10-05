# Configuration

Copy [settings.ini.example](../settings.ini.example) to `settings.ini` beside
`crawl-agent`, then point it at an existing WebTiles-enabled Crawl executable:

```ini
[crawl]
binary = /absolute/path/to/crawl

[output]
snapshot_tags = false
```

The adapter reads this INI file with Python's standard-library `configparser`.
No configuration package or game build is required. `settings.ini` is local
and ignored by Git.

## Settings and overrides

| Setting | Purpose | Environment | CLI override |
| --- | --- | --- | --- |
| `[crawl] binary` | Existing executable with WebTiles support and matching runtime data | `CRAWL_BINARY` | `start --binary PATH`, `doctor --binary PATH` |
| `[crawl] source` | Optional external `crawl-ref/source` directory for the viewer and metadata generators | `CRAWL_SOURCE` | `watch --crawl-source PATH`, also accepted by `start`, `doctor`, and generators |
| `[output] snapshot_tags` | Emit Codex snapshot envelopes; default `false` | None | None; settings file only |

Path settings use the command-line value first, then their environment variable,
then the settings file. Snapshot tags are controlled only by the INI setting.
If no binary is set at any level,
`source` supplies `<source>/crawl`. A binary setting takes precedence over that
fallback even if `source` came from a higher-precedence location.

Boolean values accept `true`/`false`, `yes`/`no`, `on`/`off`, or `1`/`0`, without
quotes. Unknown options and invalid booleans report a configuration error.

Output settings are read on each CLI invocation; changing them needs no game
restart. `--text` chooses readable text in place of JSON. It can be combined
with either snapshot setting. Tags are useful only with a harness that
understands them; see [Codex integration](codex.md).

## File selection and path rules

The settings file is selected in this order:

1. `./crawl-agent --config /path/to/settings.ini ...`
2. `CRAWL_AGENT_CONFIG`
3. `settings.ini` beside the adapter launcher

A missing default file is allowed so flags or environment variables can supply
the paths. An explicitly selected missing or unreadable file is an error.
The default location does not change with the shell's working directory.

Paths in INI files are **unquoted**. Spaces and literal `%` are supported,
`~` expands to the user's home directory, and relative paths resolve beside
the INI file. Shell variable syntax such as `$HOME` is not expanded inside it.
Paths supplied as flags or environment variables resolve from the invoking
directory. Use absolute paths when several tools share the same settings.

Global options such as `--config`, `--session`, and `--text` go
before the subcommand. Command options such as `--binary` and `--port` go after it:

```sh
./crawl-agent --config /path/to/settings.ini --session demo start --foreground --name astra-high
./crawl-agent --session demo --full observe
./crawl-agent --session demo watch --crawl-source /path/to/crawl-ref/source --port 8080
```

## Game compatibility

CLI play needs the binary and its runtime data. Ordinary terminal-only and
desktop-tiles binaries do not support the WebTiles socket protocol required by
this adapter. The executable must accept `-await-connection` and
`-webtiles-socket`; an executable file check alone cannot establish compatibility.

The tested version is **0.35-a0-1095-g7c31f6e797**, recorded with its full revision
in [crawl-version.json](../crawl-version.json). Use an existing WebTiles build of
this version and its matching runtime data. The adapter does not download or
build Crawl. Other releases, including other 0.35 development builds, can change
public enum and tile IDs; selecting a different binary does not update the
bundled metadata.

After setting `binary`, run:

```sh
./crawl-agent doctor
```

`doctor` invokes only the executable's `-version` command, with a five-second
timeout. It does not start a game, create a session, or contact a running adapter.
It prints JSON and exits successfully only when both the reported version and
the `USE_TILE_WEB` build flag match expectations:

| Status | Meaning and next step |
| --- | --- |
| `matched` | Reported version and WebTiles flag match. Proceed with the quickstart; runtime data and local modifications are still unverified. |
| `version_mismatch` | Choose the tested build, or treat support for this version as [development work](development.md#crawl-version-updates). |
| `webtiles_missing` | Choose a WebTiles-enabled executable. Terminal-only and desktop-tiles builds are unsuitable. |
| `unverified` | The version output omits required information. Inspect `version_output` and confirm the build with its provider. |
| `probe_failed` | Check the binary path, permissions, reported error, and runtime dependencies. |

To inspect another candidate without editing settings, use
`./crawl-agent doctor --binary /path/to/crawl`. You can also run
`/path/to/crawl -version` yourself: look for the version above and
`-DUSE_TILE_WEB` in `CFLAGS`. The check identifies the reported build; it is not
a complete protocol or runtime-data test. `start` does not enforce this probe,
so run it when setting up or changing binaries.

The optional browser viewer also needs matching stock WebTiles server files,
generated browser assets, Tornado, and PyYAML. This is separate from CLI play:
`source` may stay unset when only controlling the game through the CLI.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Missing or nonexecutable binary | Check `[crawl] binary`, its resolved path, and executable permissions. |
| Startup exits or times out | Read the session's `adapter.log` and `crawl.log`; verify WebTiles support and runtime data. Observe before resubmitting an uncertain start or action. |
| Viewer reports missing assets | Set `source` to the matching external `crawl-ref/source` directory, not the checkout root. |
| Viewer cannot import Tornado or YAML | Install [requirements-viewer.txt](../requirements-viewer.txt) into `.crawl-agent/venv`; the launcher prefers that interpreter when present. |
| JSON parser encounters XML-like tags | Set `[output] snapshot_tags = false` in the settings file. |
| Session already running | Use `observe` with the same session; keep one controller per session. |
| Settings error prevents observation | Fix the INI file. `stop` and `stats` remain usable without loading it. |
