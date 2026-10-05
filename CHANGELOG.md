# Crawl adapter changelog

Changes to the separate CLI adapter, not Crawl itself. Established2026-10-02
with selected, verified recent history; this is not an exhaustive record of
earlier implementation. Older entries describe behavior at the time of each
change; use the [current reference](docs/guide.md) for present defaults and
commands. Historical `Files` lists retain the paths used at completion;
see the [code layout](docs/development.md#code-layout) for current locations.

The session change detector uses
completed entries as its only update heuristic. It does not inspect adapter
file contents or prove which code a running daemon has loaded.

## Entry contract

Add new entries at the top with a unique increasing `CA-NNNN` ID, ISO date,
short title and the metadata fields below. `Status` is `in-progress` while
work or validation remains, and `complete` only after the feature is finished
and validated. Add an entry at completion, or change its draft to `complete`
as the final implementation step. Drafts never trigger restart advice.

Use `Scope` values `daemon`, `client`, `viewer`, or `documentation`
(comma-separated if needed). `Daemon-restart` is `required` only if loading the
finished feature needs a daemon restart; otherwise use `not-required`. `Files`
is an explanatory list of repository-relative paths, not an inspection list.
Completed IDs are immutable completion markers. Fix wording freely, but record
behavioral corrections, reversions and further completed features under new IDs.
Do not delete/reuse completed IDs or move them back to in-progress. Dates record
completion (or labeled historical backfill), not session deployment.

Only newly completed entries with `Daemon-restart: required` recommend a restart.
Unfinished edits, unlogged file changes and task switches produce no advice.
Recommendations are deferred to planned maintenance, never automatic updates.

## CA-0039 — 2026-10-05 — Package layout, binary diagnostics, and reliable detached startup

Status: complete
Scope: client, daemon, viewer, documentation
Daemon-restart: required
Files: dcss_harness/, run.py, crawl-agent, scripts/, tests/, data/, assets/, docs/, README.md

Separate the core into protocol state, game lifecycle, daemon, client,
presentation, metrics, and CLI modules. Runtime code lives in `dcss_harness/`;
tests, generators, metadata, and the browser overlay have dedicated directories.
Keep the changelog at the project root. Launch detached workers through a private
module instead of exposing an internal command in public help.

Detached startup now waits for metadata identifying its new worker before
sending the first observation. Previously a missing-session error could be
printed before the worker was ready. Partial and stale startup metadata are
ignored; requests whose delivery is uncertain are never retried.

Add `doctor` to compare an existing binary's `-version` output with the tested
version and WebTiles build flag. It starts no game and reports uncertainty;
runtime data and local binary modifications remain unverified. Clarify automatic
pagination near the first observation examples, identify the adapter-owned
browser overlay, and document the Codex integration test commands and skip limits.

Validation: 368 Python tests successful (two optional viewer skips); all six
viewer tests passed with external assets. All four generators reproduced their
tables byte-for-byte from another working directory. A disposable live game
started detached from `/tmp`, verified checkpoint save/reload, switched output
through INI settings, saved/stopped, and resumed in the foreground at the same
turn before saving/stopping again. The Codex patch checksum is unchanged; its
integration commands were checked against source, not executed in this change.

## CA-0038 — 2026-10-05 — Config-only snapshot output and standalone documentation

Status: complete
Scope: client, documentation
Daemon-restart: not-required
Files: tools/crawl_agent.py, tools/crawl_paths.py, settings.ini.example, docs/, patches/

Plain JSON is now the default. `[output] snapshot_tags` in the INI settings
file alone controls optional Codex map/inventory envelopes; both former CLI
snapshot flags are removed. Settings are reloaded for each invocation and work
with `--text`. A mode change refreshes its baseline without replaying messages.
Shutdown and saved statistics remain usable with a broken settings file.

Centralize path precedence and distinguish the worker's JSON argument from the
CLI settings-file argument. Extract the common tile-definition reader used by
cloud and equipment generators. Organize the current reference by topic, correct
inventory/checkpoint descriptions, and provide setup/troubleshooting guidance.
Include the current Codex snapshot patch, its pinned base, upstream notices,
and apply/build/configuration instructions separately from ordinary CLI setup.

Validation: 362 Python tests successful (two optional viewer skips); all six
viewer tests passed with external assets. Live JSON/tagged/JSON switching kept
the same turn. All four generators reproduced identical tables. Patch forward
and reverse checks passed against its base; ten focused tests passed with the
existing Rust test build. The complete Codex package was not rebuilt. Local
Markdown file and heading links were checked.

## CA-0037 — 2026-10-05 — Standalone project and existing binary settings

Status: complete
Scope: client, daemon, viewer, documentation
Daemon-restart: required
Files: tools/crawl_agent.py, tools/crawl_paths.py, tools/crawl_watch.py, settings.ini.example, README.md

Extract the adapter into its own repository without Crawl source or assets.
Read an existing WebTiles executable from an optional INI settings file using
Python's standard-library configparser. Explicit flags override environment
variables, which override settings. Relative settings paths resolve beside the
INI file. Game processes launch from the configured executable's directory.
The optional spectator and metadata generators use explicit external assets.

Validation: clean export ran 354 tests successfully (two optional viewer skips);
all six viewer tests passed with external assets/dependencies. A disposable game
started from binary-only settings, accepted an action, verified a checkpoint
save/reload with no differences, then saved and stopped gracefully. Regenerating
all four metadata tables from the recorded revision produced identical files.

## CA-0036 — 2026-10-05 — Consistent live spectator lobby metadata

Status: complete
Scope: viewer
Daemon-restart: not-required
Files: tools/crawl_watch.py, tools/crawl_lobby_fields.js, tools/test_watch.py

Viewer attachment requests one public spectator snapshot. The lobby follows
player deltas for XL, location and turns, with explicit unknowns before state
arrives and during character creation. It uses per-connection metadata instead
of old .where files or shared-lobby aliases as character filenames. Milestone
reports supply played duration; missing duration stays Unknown, zero is shown,
and older reports are labeled with their source turn. In-game time is never
substituted for real played duration. A temporary static-asset overlay renders
these fields without modifying Crawl source. New sessions, duplicate names,
returning lobby clients and restarted sockets keep distinct, fresh metadata.

Validation: all 344 tests passed with viewer dependencies enabled. The six
viewer tests include real HTTP/WebSocket lifecycle coverage and JavaScript
rendering checks. A disposable native Fighter also passed attachment, turn
updates, save/stop/start, restored-state display and cleanup. Restart the viewer
to load the fix; no game adapter restart is required.

## CA-0035 — 2026-10-05 — Hold position for named allies or active Ramparts

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_hold.py, tools/crawl_spells.py, tools/crawl_agent.py

Completes stationary support after the validated cast/ray and repeated-Freeze phases. hold sends at most6 waits by default (32 maximum, shared10s default/30s maximum), requiring one chosen foe and named definite allies or active Ramparts. Positive regeneration and selected-foe wounds are allowed. Every message clause must match a narrow named-ally attack, selected-foe wound/resistance or Ramparts wall event; ambiguous names and unknown messages stop. Ally harm/loss, new creatures, distance thresholds, player damage/MP loss, equipment/status changes, hazards, prompts and uncertainty stop per event. An explicit pair of exact armour labels permits only its normal-to-expiring warning transition; expiry still stops. Active channels are rejected. Native ally fixture:six waits/six turns in one call (~0.66s), versus six manual CLI calls (~1.10s); local illustrative samples. Source-backed Ramparts fixtures, transient guards, friendly changes, timeout and missing-state tests pass. Cast stat/failure invalidation was tightened during final review. Full suite:342 tests,341 passed,1 optional viewer skip. All four implemented phases remain bounded; area/cloud/summon casting itself stays manual as proposed.

## CA-0034 — 2026-10-05 — Repeat explicitly chosen Freeze with per-cast checks

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_spells.py, tools/crawl_agent.py

Adds opt-in --max-casts for Freeze only, default1 and maximum32, with one shared
deadline and a fixed sighting ID. Fresh menu identity/failure, current adjacency,
native aim and MP reserve are verified before every cast. Cross-attempt guards
preserve player/equipment/monster state; miscasts and uncertain submissions are
never retried. Each attempt remains in the result; ordinary clients omit the
inactive option for older daemons. Native disposable fixture:three casts/three
turns in one request (~1.9s). Tests cover budget exhaustion, remapped second menu,
miscast, damage, timeout and shared deadline. Full suite:330 tests,329 passed,1 skip.

## CA-0033 — 2026-10-05 — Chosen casts and verified Searing Ray continuations

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_spells.py, tools/crawl_agent.py

Implements spell proposal phases1–2: one chosen Freeze, Searing Ray or Ozocubu's
Armour, fresh menu identity/failure/level verification, required failure ceiling
and MP reserve, fresh native aim and existing conservative exposure checks.
Public level bounds MP before selection; targeted spells expose their exact
refundable reservation before firing. Instant prepare-only sends no input.
Optional ray continuation requires this invocation's live Ray progression and
unchanged validated path; at most three pulses, never an extra ordinary wait.
Per-event guards latch damage/risk/resource/monster changes, retain evidence,
stop at confirmations and never retry uncertain submissions. Native fixtures
cover all three spells; mutations exercise remapped letters, reserve/failure,
two adjacent targets, channel loss, damage, risk, MP loss, movement, prompts and
timeouts. Native ray:four turns in one CLI call (~1.0s helper execution), versus
nine manual-equivalent CLI calls/four turns (1.69s total); illustrative local
samples, not model/billing benchmarks. Full suite:327 tests,326 passed,1 skip.

## CA-0032 — 2026-10-05 — Allow explicitly assessed poison clouds with change guards

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_hazards.py, tools/crawl_combat.py, tools/crawl_recovery.py, tools/crawl_agent.py

Added --allow-cloud poison to combat/recover/wait-for. Native cloud.cc confirms poison-cloud immunity at positive poison resistance (or general cloud immunity); the adapter requires controller assessment and never infers worn resistance from inventory. Exact known poison groups may pass, including player membership away from the anchor. Other/ambiguous clouds and stacked terrain remain blocked. Shared watcher also latches MP loss and monster changes alongside damage, status/form/equipment/stat changes. Public observations cannot certify hidden resistance changes. Tests exercise all three helpers, exact/unknown types, independent water permission, grouped cells and transient damage. Full suite:316 tests,315 passed,1 optional viewer skip.

## CA-0031 — 2026-10-05 — Permit explicitly assessed flight over typed water

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_hazards.py, tools/crawl_map.py, tools/crawl_combat.py, tools/crawl_recovery.py, tools/crawl_agent.py

combat/recover/wait-for accept --allow-water-with-flight, requiring current public Fly and exact shallow_water/deep_water terrain IDs. Terrain groups now retain those IDs. Status permission alone is insufficient; clouds, traps, unknown terrain and stacked hazards still block. A shared watcher latches damage and changes in status, form, equipment/inventory or defensive stats before another input. Tests cover all three helpers, grouped water away from the anchor, stacked hazards, missing flight/ID and transient flight loss. Full suite:313 tests,312 passed,1 optional viewer skip.

## CA-0030 — 2026-10-05 — Recognize confirmed friendly summons during chosen-foe waiting

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_recovery.py

wait-for recognizes definite friendly summons using the existing conservative recovery predicate. Chosen-hostile isolation remains required; no fighting messages are allowed. Ally disappearance and status/attitude changes latch even if restored in a later frame. Tests cover allowed allies, transient hostility/confusion/disappearance and friendly combat messages. Full suite:309 tests,308 passed,1 optional viewer skip.

## CA-0029 — 2026-10-05 — Patch changed visible features without repeating unchanged groups

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

Ordinary observations emit lossless reverse-ordered visible_features_delta.splices when smaller than a full replacement. Full/initial/reset observations retain the complete array, and empty clears remain explicit. Routes, grouped cells, ordering and all hazard details reconstruct exactly; helper inputs remain full. Swamp-shaped 12-water-group fixture over40 stationary mist changes:161140→4780 cumulative feature bytes;20 movement outputs:83016→9456. These are fixture serialization measurements, not billing estimates. Randomized300-step reconstruction, full recovery, cloud visibility and stream tests pass. Full suite:309 tests,308 passed,1 optional viewer skip.

## CA-0028 — 2026-10-05 — Read exact local terrain neighborhoods

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_map.py, tools/crawl_agent.py

Added terrain --dx N --dy N (default center:player), a read-only 3x3 named public terrain query. Includes diagonal cells, absolute/relative coordinates, level/origin and explicit visible/remembered/unknown distinctions, without safety claims or game input. Rejects unknown origin and other-level maps. Query output clears on the next ordinary observation. Tests cover the reported diagonal tree, walls, unseen cells, alternate centers, origin reset and CLI request. Full suite:306 tests,305 passed,1 optional viewer skip.

## CA-0027 — 2026-10-05 — Render readable Unicode map snapshots

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

The crawl/map JSON payload uses literal Unicode and indented rows, retaining origin, visibility, session identity and the existing byte limit/fallback. Inventory snapshots and plain JSON retain their formats. Validated generated native 17x17 map and inventory output against the actual blocks function extracted unchanged from the local Codex context_manager/snapshots.rs and compiled with rustc; both blocks accepted. Existing stream, overflow and recovery tests pass. Full suite:304 tests,303 passed,1 optional viewer skip.

## CA-0026 — 2026-10-05 — Verify cropped weapon names with independent native descriptions

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_checkpoint.py, tools/crawl_agent.py

Checkpoint reads exact non-terse native weapon description titles before saving and after reload. Only matching titles plus identical enchantment and complete property suffix can reconcile a raw display-name difference. Raw labels remain in the journal and display_name_changes; every other inventory/player field remains exact. Description uncertainty, identity changes, or any state change stops without retry. Native terse weapon names depend on the WebTiles control flag, which spectator attachment can change; a full refresh alone therefore cannot fix this. Reproduced with a disposable artefact eveningstar, then repeated after 1,200 native resting turns and spectator attachment: the shortened/full label pair verifies with matching full titles. Main gameplay sessions were untouched. Full suite:304 tests,303 passed,1 optional viewer skip.

## CA-0025 — 2026-10-02 — Refresh checkpoint inventories and report precise mismatches

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_checkpoint.py, tools/crawl_agent.py

Checkpoint obtains complete read-only public refreshes before save and after
reload, then compares exact names and exported inventory fields. Cached cropped
artefact names can refresh to authoritative display names; no title/property
normalization hides genuine changes. Refresh failure remains unverified and
never retries save/input. Differences report only changed namespaced slots and
fields, additions/removals and ordering; full snapshots remain in the journal.
The recorded eveningstar mismatch falls19674→216 bytes. Exact fixture, failure,
namespace and substantive-change tests plus a disposable live checkpoint pass.
Full suite:302 tests,301 passed,1 optional viewer skip.

## CA-0024 — 2026-10-02 — Recover readiness after known blocked movement

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py

Directional input aimed at visible solid non-door terrain without a monster
requests one ordered read-only full refresh. Unchanged turn/place/position,
blocking terrain and a command prompt can settle as blocked_move_resynchronized.
Require complete version/player/inventory/map/UI/mode and closing-frame evidence;
partial or missing replies remain uncertain. Ordinary movement, doors and
unknown/remembered cells gain no exception; no key is retried. Five focused
tests and a live disposable tree bump passed without advancing turn2. Full
suite:295 tests,294 passed,1 optional viewer skip.

## CA-0023 — 2026-10-02 — Keep dungeon generation and level refresh unsettled

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py

Native progress popups no longer count as input-ready menus. After a popup
closes, wait for fresh player level/progress data, a nonempty map update and
their closing flush before normal settling can finish. Timeouts report
`level_generation` or `level_refresh`; later observations retain the guard,
and no gameplay input is retried. Five protocol/settling regressions cover
stale command mode, redraws, delayed/split state, stack updates, action blocking
and interactive prompts. Full suite: 290 tests, 289 passed, one optional viewer
skip. No live session was restarted during validation.

## CA-0022 — 2026-10-02 — Compact connected water and terrain features

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_map.py

Water and barrier features group equal eight-connected appearances using exact
visible `cells` and one checked anchor approach. Water depths remain distinct;
unseen gaps, underfoot hazards, occupied bottlenecks and unknown routes remain
explicit. Group membership never implies safe transit. Geometry and guard
tests pass. A public Swamp-derived stationary fixture reduced feature output
from25 entries/1928 tokens to14/1239; eight simulated stationary observations
with a moving foe reduced feature tokens15424→9860 (36.1%). Full suite:285 tests,
284 passed,1 optional viewer skip.

## CA-0021 — 2026-10-02 — Fit ordinary large inventories in replacement snapshots

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

Full tagged inventories that exceed8192 bytes now use a lossless shared-column
`inventory_table`. Small inventories, plain JSON and ordinary delta objects
retain their formats. Tables preserve namespaces, order, optional/null fields
and exact names/charges. Still-oversized tables keep the explicit full JSON
fallback and tagged clearing marker. An encoder upgrade refreshes the inventory
baseline without replaying messages. The actual63-item gameplay inventory
round-trips at less than half its previous payload bytes. Full suite:283 tests,
282 passed,1 optional viewer skip.

## CA-0020 — 2026-10-02 — Verified native save checkpoints

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_checkpoint.py, tools/crawl_agent.py

`checkpoint` confirms a native save/exit, reloads the same character/options,
and verifies public progress, stats/status/equipment and inventory. It retains
the persistent adapter process, loaded code and changelog baseline. Native-game
generations reset every reader to full observations. `checkpoint.json` records
intent and verification for lost-reply recovery. Failed saves/reloads or state
discrepancies are reported without retrying input; missing saves never start a
new character. Eight focused tests and two live turn2 disposable checkpoints
passed; full suite:280 tests,279 passed,1 optional viewer skip.

## CA-0019 — 2026-10-02 — Explicit assessment for ranged waiting

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_recovery.py, tools/crawl_agent.py

`wait-for --assessed-ranged` permits only the chosen foe's ranged/reaching
weapon hint. All other guards and single-foe isolation remain. Hybrid abilities
without a weapon hint were already possible and still require assessment.
Any new message, damage, MP/status transition, new/lost foe, prompt or uncertain
output stops. No group allowance. Hint/quiet-wait/event/CLI tests pass; full
suite:272 tests,271 passed,1 optional viewer skip.

## CA-0018 — 2026-10-02 — Bounded melee with explicitly assessed distant foes

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_combat.py, tools/crawl_agent.py

Repeat `combat --assessed-distant-id ID` to tolerate chosen distant sightings
for one invocation. Attack only adjacent foes in `(dy, dx, id)` order. Stop
on assessed adjacency, including transient approach, new/lost sightings,
appearance changes and existing safety guards. Ranged/reaching hints remain
blocked. Empty options are omitted for older daemons; an unsupported explicit
option reports restart_required before input. Phantom/sleepcap and yak-herd
fixtures, expiry, guard and CLI tests pass. Full suite:269 tests,268 passed,
1 optional viewer skip.

## CA-0017 — 2026-10-02 — Expose current public fleeing behavior

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_safety.py, tools/crawl_protocol.json, tools/generate_crawl_protocol.py

Visible monsters report the native foreground fleeing flag in `icons`. Decode
its exclusive behavior mask without mistaking other states for fleeing. Old
messages and remembered/invisible tiles do not create an enduring status.
Combat continues to reject fleeing; no enemy allowance was added. Four focused
tests cover encodings, transitions, sightings and guards. Full suite:265 tests,
264 passed,1 optional viewer skip.

## CA-0016 — 2026-10-02 — Emit player explanations only on change

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

CLI player output retains dynamic stats/status labels but moves long Doom and
status explanations to player_descriptions, a full replacement emitted only
on change or initial/full/reset output. Status descriptions reference current
list indexes; empty objects clear old explanations. Migration/new-stream/full
recovery is explicit. Private socket snapshots and helper guards are unchanged.
Validated reconstruction and live output without restart;260 tests passed,
one optional viewer test skipped. Sample player payload fell392→281 tokens.

## CA-0015 — 2026-10-02 — Settle empty map feature cycles

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py

Known map navigation/feature-cycle/exclusion inputs request one ordered,
read-only full refresh. Full version/UI/map/cursor receipt in a quiet frame
with unchanged turn and an open map permits map_resynchronized for no-op keys.
Absent/partial replies remain uncertain; travel/gameplay keys get no exception
and no action is retried. Live empty Tab cycling and Escape passed at unchanged
turn2; full suite:256 passed, one optional viewer test skipped.

## CA-0014 — 2026-10-02 — Buffer and complete map exclusion-radius input

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py

Map R is buffered locally until a validated digit1–8 completes the command.
The pair is sent atomically through native text_input. map_input describes the
buffer; Escape cancels without a game key. Invalid digits and changed selection
send no input. Requested/deferred key counts are explicit. Silent exclusion
updates use the bounded map refresh path. Unit tests and live R/Escape/R2/e
checks passed without advancing a turn.

## CA-0013 — 2026-10-02 — Expose level-map cursor and selected terrain

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py, tools/crawl_map.py

`level_map` reports the dedicated public map cursor, selected terrain and
visible/remembered/unknown knowledge separately from targeting. Repeated
identical descriptions retain distinct coordinates. Exit/reset clears stale
selection; full observations restore it. Off-level views do not imply visible
terrain, player-relative routes or a known level identity. Validated by five
new tests and live X/move/exit/reentry at unchanged turn2; full suite:248 passed,
one optional viewer test skipped.

## CA-0012 — 2026-10-02 — Base restart advice on completed changelog entries

Status: complete
Scope: daemon, client
Daemon-restart: not-required
Files: tools/crawl_changes.py, tools/crawl_agent.py

Replaces the file-hash implementation in CA-0006 with a completion heuristic.
Observations compare completed entry IDs against the session startup record;
only newly completed entries marked restart-required advise a planned restart.
Drafts and arbitrary file edits are ignored. New starts persist only completed
IDs, without source scanning or hashes. Existing startup records reuse their
frozen changelog IDs, so the new CLI check works without a migration restart.
Missing/uncertain metadata reports unknown instead of recommending a restart.
No automatic code updates, game keys, save or restart. New daemons use the new
baseline format on their next planned start; no restart is needed for the new
observation check itself.

## CA-0011 — 2026-10-02 — Classify verified petrified flowers as scenery

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_safety.py, tools/crawl_protocol.json, tools/generate_crawl_protocol.py

The generated public petrified-flower type joins scenery only with explicit
no-XP, zero-threat, known-attitude, visible-location and permitted-icon evidence.
Occupied cells still block navigation. Dangerous statuses, unknown types and
missing evidence remain in monsters. Combat and recovery checks are covered.

## CA-0010 — 2026-10-02 — Permit HP-only healing during assessed waiting

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_recovery.py

`wait-for` continues through HP-only gains and records hp_before, hp_after and
net hp_change per step. Observed damage, maxima changes, MP/status changes,
messages and all existing enemy/input guards still stop it. Transient damage
followed by healing remains latched; no action is retried.

## CA-0009 — 2026-10-02 — Recover around known friendly summons

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_recovery.py, tools/crawl_safety.py, tools/crawl_protocol.json, tools/generate_crawl_protocol.py

Recovery uses the generated ATT_FRIENDLY value (4), replacing the incorrect
neutral-attitude check. Known visible friendly berserk summons no longer block
recovery. Neutral/unknown attitudes, confusion, frenzy, inner flame and unknown
icons still stop it. Per-event checks latch unsafe transitions and retain all
player, hazard, message, unseen-threat and native-rest interruption guards.

## CA-0008 — 2026-10-02 — Require complete initial public state

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py

Startup requires core player fields, explicit inventory receipt, a full-map
receipt with a visible player square, version and a completed quiet frame.
Missing state remains unready across observe calls and blocks gameplay input.
One read-only spectator refresh may restore it; startup metadata reports missing
fields and recovery guidance. Recognized character-creation layouts remain
interactive without releasing the later playable-state check. Empty inventory
and zero MP are valid. Tested reordered/delayed packets, failed startup, menu
transitions, late recovery and a live saved-game resume.

These changes load on a normal controller-coordinated restart; the checker is
informational and never performs updates. A task switch alone is not a reason
to restart; defer optional loading to a planned maintenance pause.

## CA-0007 — 2026-10-02 — Inventory deltas and full inventory snapshots

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

Ordinary inventory updates now emit `inventory_delta.upsert` and `remove`,
keyed by letter namespace and slot, plus explicit ordering when needed. Each
upsert is a complete entry preserving names, charges, counts and refresh state.
The `inventory` field retains full-replacement semantics. Initial/full/reset
observations and format switches provide complete recovery baselines; default
tagged output wraps them in `crawl/inventory`. Item deltas stay outside tags.
Oversized full inventories remain in ordinary output with a clearing block.
Add `crawl/inventory` to the harness snapshot allowlist to replace old full
inventories in context. No game restart or rebuild is required. Tests cover
reconstruction, namespace/letter/order changes, resets and both output modes.

## CA-0006 — 2026-10-02 — Report session changes and restart advice

Status: complete
Scope: daemon, client
Daemon-restart: required
Files: tools/crawl_changes.py, tools/crawl_agent.py

Successful adapter starts persist a session-specific content manifest.
Observation output compares current code/data with that startup baseline and
includes `adapter_changes.restart_recommended`, reasons, changed files and
relevant changelog entries. Unchanged reports retain a compact status; `--full`
restores details. Changes since the previous successful start are retained.
Unlogged code changes are still detected. The shared CLI/daemon module is
partitioned conservatively to avoid restart advice for client-only functions.

Existing daemons without a matching manifest report `baseline_unknown` and
recommend a normal restart to establish tracking, without asserting they are
outdated. The CLI can report this immediately; loading startup tracking requires
one normal daemon restart. Checks never send game keys, reload, save or restart.
Unavailable/malformed metadata produces an explicit uncertainty report.

## CA-0005 — 2026-10-02 — Enable snapshot tags by default

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

Observation-producing CLI calls now use the tagged map envelope by default,
including foreground startup. `--no-snapshot-tags` selects plain JSON (or
untagged text with `--text`); `--snapshot-tags` remains an explicit enable.
Changing formats re-emits the map without replaying messages. This supersedes
the opt-in default recorded in CA-0001. Client presentation upgrades existing
sessions without a daemon restart. Output/parser tests cover both formats.

## CA-0004 — 2026-10-02 — Establish adapter change history

Status: complete
Scope: documentation
Daemon-restart: not-required
Files: tools/CHANGELOG-crawl-agent.md, tools/README-crawl-agent.md

Created this changelog and the open task for per-session startup manifests and
change reports. No runtime detection, daemon lifecycle or gameplay behavior
changed. Seeded the three verified historical entries below.

## CA-0003 — 2026-09-30 — Wait for complete public update frames

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py

Backfilled2026-10-02. Settling waits for the closing `flush_messages` record and
keeps incomplete frames pending across timeout/observe. An unchanged command
mode cycle is no longer response evidence. Manual actions drain queued updates
before establishing their response baseline. Added `incomplete_frame`; targeting
cancellation remains blocked while a frame is incomplete. No timer increase or
automatic retry. Frame completion is not a request-correlated acknowledgment.

Validation recorded at implementation:202 tests passed, one optional viewer
test skipped; live disposable-game checks.

## CA-0002 — 2026-09-30 — Recognize map/text prompts and recover silent targeting

Status: complete
Scope: daemon
Daemon-restart: required
Files: tools/crawl_agent.py, tools/crawl_ranged.py

Backfilled2026-10-02. Recognizes native level-map and line-entry UI signals.
Search and travel-depth text edits are buffered until explicit submission;
observations expose `text_input` and `keys_buffered`. Silent non-firing target
navigation can expose `cancel_available` for one explicit Escape; uncertain
shots cannot use that exception. No automatic cancellation or submission.

Validation recorded at implementation:195 tests passed, one optional viewer
test skipped; live map, search, depth-entry and cancellation checks.

## CA-0001 — 2026-09-30 — Replaceable map snapshots

Status: complete
Scope: client
Daemon-restart: not-required
Files: tools/crawl_agent.py

Backfilled2026-10-02; date is the recorded live verification. `--snapshot-tags`
emits the complete map in a `crawl/map` tagged block on changes or full output.
A compatible harness can replace older map blocks in model context. Ordinary
JSON remains the default; tagged output is a text envelope, not pure JSON.
The key is shared across sessions in one conversation. Inventory is not tagged.

Live replacement and tokenizer savings were verified; billing and latency
savings were not measured.
