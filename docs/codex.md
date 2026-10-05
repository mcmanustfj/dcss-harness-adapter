# Codex snapshot integration

Codex integration is optional. The adapter works with other harnesses through
plain JSON. The included patch lets a compatible Codex build replace older
tagged map and full-inventory blocks in model requests while preserving raw
transcripts and review evidence. It does not automate gameplay.

Two independent settings are required: the adapter must emit the tags, and
the patched Codex process must allow their keys. Enabling only one side does
not replace old observations.

## Patch and supported base

The patch is [codex-replaceable-snapshots.patch](../patches/codex-replaceable-snapshots.patch).
It targets the public `openai/codex` commit
`b1e72963c3b71a9265a551e54beff078384efed9`, with both exact-key and trailing-prefix
matching. This custom setting is added by the patch; do not assume that an
unpatched or different Codex release implements it.

Base revision and checksum are recorded in
[codex-version.json](../patches/codex-version.json). The patch includes focused
Rust tests, configuration schema changes, compaction accounting, and protections
for review evidence. Upstream license and notice files accompany it. No Codex
binary is bundled or installed by the adapter.

## Apply to an external Codex checkout

Start from the adapter root. Use a new sibling directory for the Codex checkout:

```sh
HARNESS_ROOT="$PWD"
git clone https://github.com/openai/codex.git ../codex-snapshots-source
cd ../codex-snapshots-source
git checkout --detach b1e72963c3b71a9265a551e54beff078384efed9
git apply --check "$HARNESS_ROOT/patches/codex-replaceable-snapshots.patch"
git apply "$HARNESS_ROOT/patches/codex-replaceable-snapshots.patch"
```

For an existing checkout, preserve its work and check its revision first. If
the forward check fails, this read-only check tells you whether the patch is
already applied:

```sh
git apply --reverse --check "$HARNESS_ROOT/patches/codex-replaceable-snapshots.patch"
```

If neither check passes, reconcile the source version and local changes before
applying. Do not force the patch or reset unrelated work.

## Build a separate Codex package

Applying source changes does not change an installed or running Codex executable.
Use the Rust toolchain pinned by the target checkout (`1.95.0`), Python, and the
native build dependencies described in its
[installation guide](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/docs/install.md).
The package builder also builds or downloads runtime helpers; network access
and the platform's native development libraries are required.

For a Linux x86-64 glibc host, run this from the patched Codex root:

```sh
python3 scripts/build_codex_package.py \
  --target x86_64-unknown-linux-gnu \
  --cargo-profile dev-small \
  --package-version 0.0.0-snapshots \
  --package-dir ../codex-snapshots-package
```

Use `python3 scripts/build_codex_package.py --help` for other platforms and
explicit helper paths. The checkout's
[package builder guide](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/scripts/codex_package/README.md)
describes these options. Keep the whole output package: `bin/`,
`codex-resources/`, `codex-path/`, and `codex-package.json` belong together.
Copying just the CLI can omit the code-mode host and sandbox helpers.

Relevant source tests, from the patched `codex-rs` directory:

```sh
cargo test -p codex-core --lib context_manager::history::snapshots::tests
cargo test -p codex-core --lib compact_remote_history::metadata_tests::snapshot_preflight
cargo test -p codex-core --test all replaceable_snapshots
cargo test -p codex-core --test all client_websockets::snapshot_websocket_tests
```

The integration tests cover model-request projection, preserved review evidence,
and WebSocket request reuse. Both integration suites use network-availability
skip guards: a skipped case does not validate its behavior. Check the test output
and run them in an environment where their local test servers can start.

Packaging verification for this repository checked forward/reverse application
against the base and exact equality with the current local implementation.
Ten focused tests passed using the existing Rust test build. The full Codex
package and broader test suite were not rebuilt during that verification.

## Enable the adapter output

In the adapter's `settings.ini`, alongside the existing `[crawl]` section:

```ini
[output]
snapshot_tags = true
```

This takes effect on the next CLI invocation without a game restart. Run
`./crawl-agent --session mygame --full observe` to establish a fresh baseline.
Set `snapshot_tags = false` to return to plain JSON. Snapshot tags have no CLI
flag; `--config PATH` can select a different settings file when needed.

The actual delimiters use `codex_snapshot`; `crawl/map` and `crawl/inventory`
are keys, not XML element names:

```text
<codex_snapshot key="crawl/map">
{...complete map snapshot...}
</codex_snapshot>
<codex_snapshot key="crawl/inventory">
{...complete inventory snapshot...}
</codex_snapshot>
```

## Launch the patched Codex process

From the adapter root, start a new Codex session with the package you built:

```sh
../codex-snapshots-package/bin/codex \
  -c 'experimental_replaceable_tool_output_keys=["crawl/map","crawl/inventory"]'
```

Or add this top-level setting to the configuration used by the patched binary:

```toml
experimental_replaceable_tool_output_keys = ["crawl/map", "crawl/inventory"]
```

Codex supports user settings and command-line overrides as described in
[OpenAI's configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic).
The setting above itself belongs to this patch. An empty list disables replacement.

`["crawl/*"]` also works with the included patch. One trailing `*` matches a
prefix, including nested keys; other glob or regex syntax is unsupported.
Each full key is independent, so a map never replaces inventory. Prefer the
explicit two-key list when no additional producers are needed.

## Forward output and recover state

Pass raw tool-output text to Codex. For code mode:

```javascript
const result = await tools.exec_command({
  cmd: "./crawl-agent --session mygame --full observe"
});
text(result.output);
```

Serializing the whole shell result as JSON escapes the delimiters. The patch
recognizes delimiters on complete LF or CRLF lines, keys of at most 128 ASCII
characters, blocks up to 8 KiB, and at most 32 blocks per tool result. Malformed
or truncated blocks cannot supersede complete observations. Only older blocks
with the same allowed key become short supersession notices in model requests.

Full inventory blocks establish baselines; ordinary `inventory_delta` patches
remain outside them. Reconstruct state from the latest full baseline and its
later deltas. The [reference](guide.md#output-formats) describes large-inventory
tables and fallback output.

All games in one conversation share these keys. Use separate conversations for
simultaneous games, and request `--full observe` after switching
sessions or losing context. Summaries do not preserve exact maps or inventories.
Replacing earlier request content can reduce prompt-prefix cache reuse; fewer
input tokens do not establish lower latency or cost.
