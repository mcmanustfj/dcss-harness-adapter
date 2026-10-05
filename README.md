# DCSS Harness Adapter

Control an existing Dungeon Crawl Stone Soup game through its WebTiles protocol.
A persistent adapter owns the game process; each CLI action returns public player
state, messages, menus, inventory, and a map for your harness. The adapter does
not call a model or modify Crawl.

The project contains adapter code, static metadata, tests, and documentation,
including a small adapter-owned browser overlay. No DCSS source, binaries, or
stock WebTiles assets are bundled. Local settings, saves, and logs are ignored
by Git.

## Requirements

- Python 3.10 or later on Unix, with Unix sockets and PTYs. Linux is tested.
- An existing **WebTiles-enabled** Crawl executable with matching runtime data.
  Terminal-only and desktop-tiles executables do not provide this protocol.

CLI gameplay and core tests use only Python's standard library. Run the adapter
directly from the checkout; there is no package installation or game build step.
The tested Crawl version is **0.35-a0-1095-g7c31f6e797**; its revision and metadata
are recorded in [crawl-version.json](crawl-version.json).

## Quickstart

Clone this repository, then create your local settings:

```sh
git clone https://github.com/mcmanustfj/dcss-harness-adapter.git
cd dcss-harness-adapter
cp settings.ini.example settings.ini
```

Edit `settings.ini` to select your executable:

```ini
[crawl]
binary = /absolute/path/to/crawl

[output]
snapshot_tags = false
```

Paths are unquoted; relative paths resolve beside the settings file. The adapter
uses Python's standard-library `configparser`. See [configuration](docs/configuration.md)
for file selection, environment overrides, compatibility, and troubleshooting.

Check the configured executable before starting a game:

```sh
./crawl-agent doctor
```

This runs Crawl's version command and checks its reported version and WebTiles
build flag. A successful check reports `status: matched`; it does not verify
runtime data or local binary modifications. See [compatibility](docs/configuration.md#game-compatibility)
for other results.

Start a new game in a persistent terminal or your harness's process tool:

```sh
./crawl-agent --session demo start --foreground --name astra-high --seed 12345
```

For an agent-controlled character, substitute the actual model name and reasoning
effort. The default character is a Minotaur Berserker. Keep this process alive;
it prints the initial observation, then waits silently. Send subsequent commands
from another terminal or tool call:

```sh
./crawl-agent --session demo --full observe
```

By default, `observe` can send Space to acknowledge plain message pages. Start
with `--no-auto-more` if those acknowledgments must be manual. Observation does
not choose menu options or intentionally advance gameplay turns.

Read the result and choose one action, for example:

```sh
./crawl-agent --session demo act --action explore
```

Read every returned observation before acting again. If `settled` is false,
observe again. Handle menus and confirmations before movement. A timeout does
not establish whether input took effect. The [reference](docs/guide.md) explains
commands, targeting, bounded helpers, and how to reconstruct observation deltas.

Output is plain JSON by default. `--text` selects readable text. Optional Codex
snapshot tags are controlled by `[output] snapshot_tags` in `settings.ini`; see
[Codex integration](docs/codex.md) for the included patch and activation instructions.

## Save and resume

At the normal game prompt:

```sh
./crawl-agent --session demo act --action save
./crawl-agent --session demo stop
```

Resume with the same `start` command, session, and character name. Data stays
under `.crawl-agent/demo/` beside the launcher. To use an existing session
elsewhere, select `--session-dir /path/to/session` before the subcommand after
its original controller has saved and stopped. Keep one controller per session.

## Browser spectator

The optional viewer needs Tornado, PyYAML, and matching stock WebTiles server and
browser assets from an external Crawl distribution. Add `source` to your existing
`[crawl]` section first:

```ini
[crawl]
binary = /absolute/path/to/crawl
source = /absolute/path/to/crawl-ref/source
```

Then install the viewer dependencies and start it alongside a running game:

```sh
python3 -m venv .crawl-agent/venv
.crawl-agent/venv/bin/python -m pip install -r requirements-viewer.txt
./crawl-agent --session demo watch --port 8080
```

The launcher automatically uses that environment. Open the printed URL, normally
`http://localhost:8080/#watch-astra-high`. `./crawl-agent watch --all` shows a lobby
of running sibling sessions. The viewer binds to localhost; stopping it leaves
the games running. CLI gameplay does not require these viewer assets or packages.

## Documentation and development

- [Configuration](docs/configuration.md): binary path, settings, output, and troubleshooting.
- [Adapter reference](docs/guide.md): observations, commands, checkpoints, and timing.
- [Codex integration](docs/codex.md): optional patch, build, and snapshot replacement.
- [Development](docs/development.md): tests, code layout, and metadata maintenance.
- [Changelog](CHANGELOG.md): completed behavior changes and restarts.

Run the independent suite without Crawl installed:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

## License

The adapter uses the [MIT license](LICENSE). Crawl is a separate project with its
own license. The optional Codex patch includes upstream Apache 2.0 notices;
see [NOTICE](NOTICE) for provenance and license locations.
