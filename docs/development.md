# Development

The adapter runs directly from its checkout. Core runtime and tests use Python
3.10 or later and the standard library. Linux is tested; Unix sockets and PTYs
are required. DCSS source, binaries, stock WebTiles assets, local settings, and
session files stay outside version control. The small adapter-owned browser
overlay is included under `assets/`.

## Tests

From the adapter root, with no Crawl installation required:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
./crawl-agent --help
git diff --check
```

CI runs this independent suite on Python 3.10 and 3.12. Two additional viewer
checks are optional: the client overlay test needs Node.js and `CRAWL_SOURCE`;
the lobby integration test also needs Tornado, PyYAML, and an external binary
at `$CRAWL_SOURCE/crawl`. These tests use temporary sessions and local sockets.
They do not control existing games.

```sh
CRAWL_SOURCE=/path/to/crawl-ref/source .crawl-agent/venv/bin/python -m unittest discover -s tests -p 'test_watch.py' -v
```

For protocol changes, also use a separate disposable session to exercise the
changed path against the matching binary. Read each observation, save explicitly,
and stop the adapter afterward. Test recovery from uncertain output without
repeating an input whose outcome is unknown.

## Code layout

| Area | Responsibility |
| --- | --- |
| `crawl-agent`, `run.py` | Shell launcher, optional local Python environment, and package entry point |
| `dcss_harness/cli.py`, `client.py` | Public command parsing and requests to the session daemon |
| `dcss_harness/daemon.py`, `worker.py` | Request server and private detached-process entry point |
| `dcss_harness/game.py`, `state.py` | Crawl process/socket lifecycle and accumulated public protocol state |
| `dcss_harness/presentation.py`, `metrics.py`, `keys.py` | Observation deltas/tags, action logs, and key aliases |
| `dcss_harness/paths.py`, `doctor.py` | INI settings, external paths, and binary version diagnostics |
| `dcss_harness/map.py`, `items.py`, `targeting.py` | Public map, appearance, and target interpretation |
| `dcss_harness/combat.py`, `recovery.py`, `ranged.py`, `spells.py`, `hold.py` | Bounded, explicitly selected actions |
| `dcss_harness/safety.py`, `hazards.py` | Shared assessment guards |
| `dcss_harness/checkpoint.py`, `changes.py` | Save/reload verification and completed-change reports |
| `dcss_harness/watch.py`, `assets/crawl_lobby_fields.js` | External stock WebTiles spectator and adapter-owned lobby overlay |
| `scripts/generate_crawl_*.py`, `dcss_harness/tile_metadata.py` | Offline static metadata generation |
| `data/` | Bundled generated JSON tables; no DCSS source |
| `tests/`, `tests/fixtures/` | Unit, protocol, and optional viewer regression coverage |
| `CHANGELOG.md` | Completed behavior changes used by restart reporting |
| `patches/` | Optional Codex integration patch and upstream notices |

Python paths in each row after the first share the `dcss_harness/` prefix unless
another directory is given. Tests import the module that owns the behavior;
there is no monolithic compatibility facade. Resource paths resolve from the
project root, independent of the caller's working directory. Detached startup
uses `python -m dcss_harness.worker`; the public CLI does not expose that internal
JSON configuration interface.

The CLI talks to one persistent adapter per session. The adapter alone sends
game input. Output streams have independent presentation cursors, not independent
game control. Public WebTiles data is the observation boundary; avoid reading
hidden game memory or save contents to make gameplay decisions.

## Crawl version updates

Static IDs and appearances are recorded in [crawl-version.json](../crawl-version.json).
Ordinary users point to an existing compatible binary. Maintaining support for
a new Crawl revision is separate work and requires matching external source
with generated tile headers and definitions.

Set `[crawl] source`, `CRAWL_SOURCE`, or pass `--crawl-source` to each generator:

```sh
python3 scripts/generate_crawl_protocol.py
python3 scripts/generate_crawl_features.py
python3 scripts/generate_crawl_equipment.py
python3 scripts/generate_crawl_clouds.py
```

Protocol and terrain generation also need a C++ compiler/preprocessor. The cloud
and equipment generators share a tile-definition reader. Generated JSON goes to
`data/`; game source remains external. Review table changes, update both the
revision and human-readable version in `crawl-version.json`, run `doctor` and
the independent and viewer suites, and verify live play before claiming
compatibility. Regenerating tables alone is not a protocol compatibility test.

## Changes and restarts

Record completed behavior in the [changelog](../CHANGELOG.md).
Use a new `CA-NNNN` entry for each behavioral correction and mark it complete
only after validation. The session report compares completed changelog IDs;
it does not hash files or prove which code was loaded.

CLI presentation changes take effect on the next invocation. Daemon changes
need a controller-coordinated save, stop, and start. Viewer changes need a
viewer restart. Checkpoint reloads the game while retaining the same adapter,
so it does not load daemon changes. See the [reference](guide.md#adapter-updates)
for the report's uncertainty and restart semantics.
