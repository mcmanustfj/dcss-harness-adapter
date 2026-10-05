# Adapter reference

This guide describes session control, observations, and bounded commands for
an existing WebTiles-enabled Crawl installation. Start with the [quickstart](../README.md)
and [configuration](configuration.md). All commands below run from the adapter
root and use session `mygame`; substitute your own session consistently.

Choose one action from the current observation, then read its result. When
`settled` is false, observe again before acting. Handle menus and confirmations
before movement. The adapter never treats a timeout as permission to retry a
submitted action.

Contents:

- [Session control](#session-control)
- [Observations and deltas](#observations-and-deltas)
- [Output formats](#output-formats)
- [Bounded combat](#bounded-combat)
- [Recovery and waiting](#recovery-and-waiting)
- [Menus and targeting](#menus-and-targeting)
- [Ranged attacks](#ranged-attacks)
- [Spells and Searing Ray](#spells-and-searing-ray)
- [Stationary summon and Ramparts support](#stationary-summon-and-ramparts-support)
- [Assessed hazards](#assessed-hazards)
- [Skills screen](#skills-screen)
- [Maps and navigation](#maps-and-navigation)
- [Saves and checkpoints](#saves-and-checkpoints)
- [Logs and statistics](#logs-and-statistics)
- [Watch in tiles](#watch-in-tiles)
- [Timing and limitations](#timing-and-limitations)
- [Adapter updates](#adapter-updates)

## Session control

These are command examples, not a batch to run without reading each result.

```sh
./crawl-agent --session mygame start --name astra-high --seed 12345
./crawl-agent --session mygame observe
./crawl-agent --session mygame --full observe
./crawl-agent --session mygame act --action explore
./crawl-agent --session mygame act --move e
./crawl-agent --session mygame act --action wait
./crawl-agent --session mygame act --action inventory
./crawl-agent --session mygame inspect --dx 2 --dy -1
./crawl-agent --session mygame act --key Escape
./crawl-agent --session mygame act --key Enter
./crawl-agent --session mygame act --keys '?'
./crawl-agent --session mygame --text observe
./crawl-agent --session mygame act --action save
./crawl-agent --session mygame stop
```

For new agent-controlled games, explicitly pass `--name MODEL-EFFORT`, e.g.
`--name astra-high`, using the actual controlling model and reasoning effort.
Names accept 3–20 letters, digits or hyphens, starting with a letter or digit.
The CLI cannot infer model/effort; its legacy fallback name remains `Agent`
for compatibility. Resume existing saves with their original names.
The default species/background is a Minotaur Berserker with a hand axe. `start`
also accepts `--name`, `--species`, `--background`, and `--weapon`. Unsupported
combinations may prompt for a choice, which is exposed in the observation.
`start` resumes an existing save with the same character name; use a new
session name for a fresh run. A seed affects new games only.

If your coding harness cleans up background children after each command, run
`./crawl-agent --session mygame start --foreground --name astra-high` in its persistent process/session tool.
The command prints one initial observation and stays alive silently; use
separate CLI calls for `observe`, `act`, and `stop`. This also works in a
dedicated terminal. The plain `start` form detaches for ordinary shells.

`--session NAME` (before the subcommand) selects an independent session;
`--session-dir PATH` chooses a directory explicitly. The default storage is
`.crawl-agent/default/`. Saves, morgues, configuration, and logs stay there.
Game data belongs to the configured external Crawl installation. Commands may
be run from any directory.

By default, `observe` can send Space to acknowledge plain message pagination.
It does not select menu options or intentionally advance gameplay turns. Use
`start --no-auto-more` when acknowledgments must be manual; changing this daemon
option requires a normal save/stop/start. See [timing and limitations](#timing-and-limitations)
for the acknowledgment guards and limits.

`--keys` sends literal, case-sensitive characters without interpreting backslash
escapes. `--key` also accepts `Enter`, `Escape`, `Tab`, `Space`, `Backspace`, and
`Ctrl-X` notation. Key sequences wait after each input but return only the final
observation. Prefer single actions when a prompt or combat could change the
meaning of subsequent keys. Convenience actions are key aliases whose meaning
depends on the current input mode. `down` is `>` while standing on stairs.
For travel to the next level, use `act --keys 'G>'` and inspect the result or
prompt; this may travel and descend automatically.

## Observations and deltas

The first CLI observation in an output stream is a full JSON snapshot containing:

- Player stats, position, inventory letters, and visible monsters.
- A 17 by 17 character map centered on the player. `origin` locates its top
  left corner; the matching `visible` rows mark currently visible cells with
  `v`, distinguishing visibility from remembered terrain. Coordinates use
  WebTiles' level-relative origin, which can change on level transitions.
- `visible_features`: currently visible doors, items, stairs, hazards, and
  important terrain, with relative locations and ordered local routes.
- Recent messages, input mode, more prompts, menus, and text panels.
- Process status and whether updates settled.

Subsequent `act` and `observe` output is compact by default:

- `observation` is `full` or `delta`. `sequence` increases within the stream;
  a delta's `base_sequence` identifies the preceding output.
- Player stats, visible monsters, input mode, more prompts, running, and
  settled are always present, so immediate combat and input state are explicit.
- Long player explanations live in `player_descriptions`, emitted on first/full
  output or change. `doom_desc` holds the Doom tooltip; `statuses` holds
  `{index, desc}` entries for the current `player.status` list. The object is a
  full replacement, including `{}` to clear it; omission means unchanged.
  Dynamic player fields and status light/text remain explicit on every output.
  Description indexes update on status changes/reordering. New streams, cursor
  migration and restart/full recovery restore explanations. Private-socket snapshots retain their native fields.
- Unchanged map, visible features, inventory, menus, text panels, and metadata are omitted.
  Present fields replace their previous value completely, including empty
  lists/objects that clear old state. Omitted fields mean unchanged.
- Inventory changes use a distinct `inventory_delta` field, never a partial
  `inventory` list. `upsert` contains complete changed/new entries; `remove`
  contains `{letter_namespace, slot}` identities. Remove first, replace each
  existing entry in place, then append new entries in upsert order. If `order`
  is present, reorder the result to that complete identity list. Without it,
  retain the resulting order. A removed optional field in an upsert stays absent.
  Letter changes are upserts; slot/namespace changes remove the old identity
  and add the new one. Identical letters in different namespaces are distinct.
  Count/charge/name changes and `name_current: false` transitions are explicit;
  wait for a current name before choosing exact `ranged --expect-name` inputs.
  Present `inventory` still replaces the entire inventory, including `[]`.
  `inventory` and `inventory_delta` are mutually exclusive. Check `base_sequence`
  before applying a delta and recover missing output with `--full observe`.
- Changed visible features can use `visible_features_delta.splices`; see below.
- `messages` contains only newly observed lines. `messages_rollback: N`
  replaces the last N old lines before appending; `messages_reset: true`
  replaces retained message history when the rolling windows no longer
  overlap. New daemons keep all messages received between observations,
  including every automatically acknowledged page, plus up to 100 preceding
  lines for computing deltas. Full snapshots include that entire retained
  history. Trimming happens after the snapshot, never between message pages.
- JSON has no duplicated human-readable `summary`, and uses compact encoding.

Use `--full observe` to recover after losing context/output or to request
unchanged fields. `--full` also works with `act`. `--text` renders the same
compact observation as readable text; use `--full --text observe` for a full
view. These global flags go **before** the subcommand.

Presentation cursors live in `observation-<stream>.json` in the session
directory. A missing/corrupt cursor or new daemon yields a full snapshot.
Requests and cursor updates are locked together within a stream. Use
`--stream review --full observe` for a separate reader without consuming the
controller's output. A stream is only an output cursor, not another game or
permission to control it concurrently. If output delivery fails, recover
with `--full observe`; an action is never resent automatically.

Consumers connecting directly to the private control socket receive full
snapshots. CLI presentation uses independent per-stream cursors.

Changed visible features may use `visible_features_delta.splices` when smaller
than a full list. Apply each splice in its given order to the last complete
feature list: replace `delete` entries starting at `start` with `items`.
Splices are ordered from the end toward the beginning. A present
`visible_features` replaces the entire list (including `[]`). Full/first/reset
observations always supply the complete list. The output's `base_sequence`
must match your last applied observation; use `--full observe` if a baseline
or intervening response was lost. Every route, cloud cell and hazard detail is
preserved. Helpers still evaluate full internal observations.

## Output formats

Output is plain JSON by default. Set `[output] snapshot_tags = true` in
`settings.ini` to emit the optional Codex envelope. Set it to `false` to return
to plain JSON. This option is configured only through the settings file.
`--text` selects readable text and composes with either snapshot mode.
Changing formats establishes the required full map/inventory baseline without
replaying messages. These presentation changes need no daemon restart.

With tags enabled, ordinary JSON (or readable text) is followed by complete
`<codex_snapshot key="crawl/map">` and
`<codex_snapshot key="crawl/inventory">` blocks. Inventory changes between full
baselines remain `inventory_delta` patches outside the blocks. The payloads
include session identity, observation sequence, and turn. No tactical messages,
player stats, threats, or prompts are replaced by snapshots.

Changed maps, full observations, and stream resets emit map blocks. An absent
map emits `map: null` to clear older terrain. All full inventory observations
emit a full list or the equivalent table described below; `[]` means empty.
An oversized payload stays in the ordinary output, accompanied by a small
clearing block with a reason. That clearing block is not an empty inventory.

Large full `crawl/inventory` snapshots use `inventory_table` when ordinary
item objects would exceed 8192 bytes. It is a full replacement: zip `columns`
with each ordered `rows` entry, then merge `extra[str(row_index)]` when present.
This preserves every field, including missing versus null, namespaces, exact
names/charges and `name_current`. Small snapshots still use `inventory` arrays;
plain JSON and ordinary `inventory_delta` entries always retain object form.
If even the table exceeds the limit, full inventory remains in ordinary JSON
and the tagged block explicitly clears its older inventory with a reason.

Map blocks show literal Unicode glyphs and one JSON row per line. Each block
has an 8 KiB limit including its delimiters. For Codex patch installation, key
allowlists, exact syntax, and raw tool-output forwarding, see
[Codex integration](codex.md). Other harnesses can consume the default JSON.

### Player condition fields

Player condition fields are preserved independently of `player.status`:

| Fields | Meaning |
| --- | --- |
| `doom` | Integer Doom percentage; the CLI tooltip is `player_descriptions.doom_desc`. At 100%, Doom gives a Bane and resets. Both Doom and Banes require XP to clear; resting does not cure them. |
| `contam` | Integer contamination percentage exactly as displayed by WebTiles; it may exceed 100. The public packet has no separate contamination-description field. Contam status explanations appear in `player_descriptions.statuses` in CLI output. |
| `poison_survival` | Projected HP after poison runs its course, clamped to zero, used by the spectator HP bar. |
| `real_hp_max`, `dd_real_mp_max` | Maximum HP without drain; legacy Deep Dwarf MP reference (normally zero in the supported Crawl revision). |
| `penance`, `ostracism_pips` | Native 0/1 god-penance flag and number of suppressed piety pips. |
| `form`, `species_display_name` | Public transformation enum ID and displayed species name. No inferred form name. |
| `ac_mod`, `ev_mod`, `sh_mod` | Native signed temporary defence modifiers. AC/EV are scaled by 100; SH retains the native shield-class scale. The spectator uses their sign for highlighting. |
| `lives`, `deaths` | Conditional counters for species with multiple lives; omitted means unavailable, not zero. |
| `offhand_index`, `offhand_weapon`, `unarmed_attack` | Public offhand inventory index, 0/1 offhand-weapon flag and unarmed-attack description. |
| `quiver_item`, `quiver_available` | Public quiver inventory index and 0/1 enabled flag, alongside `quiver_desc`. Negative indices mean no inventory item. |
| `noise`, `adjusted_noise` | Raw noise (`-1` outside wizard mode) and the rescaled spectator noise meter, nominally 0–1000. |
| `wizard`, `explore`, `time_last_input`, `weapon_colour`, `offhand_weapon_colour` | Native mode flags, spectator timing reference and weapon display colours. |

Zero values survive full and compact observations. Missing fields remain
unknown; older daemons cannot expose these additions until their next normal
restart. Doom/contamination are also explicit in text output. The status array
preserves every public status label and text-only entry; descriptions are in
`player_descriptions`. Inventory remains separately filtered to
publicly known identity rather than exposing raw item internals.

Combat/recover/wait-for stop on `doom_increased` or `contam_increased`, even
with no status lights or when a later frame reduces/resets the meter. Status
allowances do not exempt these increases. Existing unchanged values and
decreases alone do not stop an assessed helper; a completed HP/MP recovery
does not mean Doom or contamination cleared. Ranged preparation also stops
if either meter changes before submission.

## Bounded combat

Visible monster `icons` include `fleeing` when the native public behavior flag
shows fleeing. This does not establish the cause or duration of fear. Historical
messages do not preserve the icon; bounded combat still stops on fleeing.

`combat` performs bounded adjacent melee using a single directional key per
step, never Tab/autofight. For example:

```sh
./crawl-agent --session mygame combat
./crawl-agent --session mygame --stream controller combat --max-actions 3 --max-seconds 5
./crawl-agent --session-dir /tmp/mygame combat --allow-status Drain
```

Invoke only after assessing the fight. The policy is stationary melee: no
waiting, approaching, spells, quiver actions, or resource selection. By default, any distant
hostile returns control (with a separate reason for visible ranged/reaching
weapons). Missing weapon hints do not imply a melee-only enemy. Allies and
protocol-verified scenery are not selected. Every resulting observation is
checked before another key; an uncertain or no-turn result is never retried.

For an assessed pack, `combat --assessed-distant-id ID` (repeatable) tolerates
only those current distant sightings for that invocation. It attacks adjacent
foes in `(dy, dx, id)` order. New/lost IDs, assessed foes reaching adjacency
(including transient approach), changed appearance and ordinary guards stop;
ranged/reaching hints remain disallowed. Missing hints never imply melee-only
abilities. Reassess every invocation; the helper never approaches.

Defaults are eight actions, ten seconds, at least 85% HP, threat at most 1,
and no player statuses or enemy status icons. `--max-actions` accepts 1–32,
`--max-seconds` 0.1–30, `--min-hp-percent` 1–100, and `--max-threat` 0–4.
Repeat `--allow-status LABEL` to permit exact public `player.status[].light`
labels, including all severities sharing that label. These are player statuses.
For explicitly assessed enemy debuffs, repeat `--allow-enemy-status drain`
and/or `--allow-enemy-status poison`. Only these two choices are accepted.
`drain` covers the public `drain` icon; `poison` covers `poison`, `more_poison`,
and `max_poison`, including severity changes. The adapter also decodes those
poison icons from public foreground flags, as the native client does.
Allowed icons remain visible in observations and the invocation policy records
the allowances. Application, expiry, and combinations of allowed debuffs do
not stop combat. Any other icon (including unknown icons), haste/berserk,
invisibility, transformations, identity changes, or other changed enemy fields
still stop it. Defaults allow no enemy debuffs. These allowances apply only
to `combat`; `wait-for` retains its strict enemy-status guard.

Other guards cannot be
disabled: new/lost sighting IDs, unknown identity/attitude, unseen-threat
evidence or invisible markers, enemy appearance/status/weapon changes,
relevant transformation/haste/berserk messages, unexpected player movement,
level/depth changes, inventory/weapon changes or MP expenditure, local hazards,
prompts/menus, exit, and unsettled output. Wound changes alone are permitted.
No visible enemies means only that none remain visible, not that the area is
safe. The quiet-window settling heuristic and public appearance information
cannot guarantee tactical safety or reveal hidden abilities.

The returned `combat` object includes the exact `policy`, `stop_reason`,
`actions`, `turns`, `elapsed_ms`, and internal `steps`. It accompanies the final
observation; all accumulated messages survive until output, including long
pagination sequences. Compact streams always include an invocation's result
and clear it with null on a subsequent ordinary observation. Errors after a
possibly sent key return `action_error` and available state; inspect before
deciding how to continue. Bounds cover the whole invocation, including initial
settling and pagination. Plain more acknowledgments remain automatic, while
attribute choices and other real prompts stop the command.

Metrics record one `act` with `action: combat` per invocation (including one
that takes zero actions), plus `combat_step` records for actual attack keys.
Each step's `invocation` refers to the action number within that daemon launch.
`stats.combat_steps` counts these without inflating the high-level action count
or double-counting turns/keys. `auto_more` records remain separate.

## Recovery and waiting

`recover` and `wait-for` use the same session/stream conventions and bounded
invocation/step accounting as combat:

```sh
./crawl-agent --session mygame recover
./crawl-agent --session mygame recover --allow-status Slow --allow-status=-Berserk --clear-statuses
./crawl-agent --session mygame recover --allow-status Fly --allow-status Slow --allow-status=-Berserk --clear-status Slow --clear-status=-Berserk
./crawl-agent --session mygame wait-for --monster-id 7 --distance 2
./crawl-agent --session mygame wait-for --monster-id 7 --allow-status Fly
```

Assess the location before recovery. `recover` sends native `5`, preserving
Crawl's full-health and approaching-monster interrupts. Its default target is
full HP **and** MP; `--clear-statuses` additionally requires no statuses. By
default any status blocks recovery. Repeated `--allow-status LABEL` permits
the exact public `player.status[].light` labels and transitions among them,
including disappearance. Read the actual labels; use `--allow-status=-Berserk`
for a label beginning with a minus sign. Allowing a label does not waive damage
or HP/MP maximum guards, so the maximum-HP change when berserk ends still
returns control. Persistent allowed statuses may exhaust the action bound.

Known visible friendly allies (`ATT_FRIENDLY`, public `att:4` in the supported Crawl revision)
do not block recovery, including ordinary berserk summons. Only understood
friendly/summoned/berserk/unrewarding/minion/slowly-dying icons are tolerated.
Neutral, missing or unknown attitudes and other icons (including confusion,
frenzy and inner flame) remain threats. Per-event guards latch transient
attitude or dangerous-icon changes even if they revert before the final frame.

To leave allowed persistent statuses active, use repeatable `--clear-status`
instead of `--clear-statuses`. The selected labels must also appear in
`--allow-status`; specifying a target does not implicitly authorize a status.
Recovery finishes once HP/MP are full and the selected labels are absent,
regardless of other explicitly allowed statuses. The two target options are
mutually exclusive. With neither option, recovery targets HP/MP only.
`policy.clear_status` records selected targets and `remaining_statuses` lists
those still present (`--clear-statuses` lists every remaining status).

For recovery and waiting, a status with no nonempty `light` uses its public
`text` as the exact selector: e.g. `--allow-status strong-willed`, optionally
`--clear-status strong-willed` for recovery. Colour markup is stripped just as
in observations. A nonempty `light` takes precedence over `text`; unnamed
statuses cannot be allowed. No status is automatically classified as benign.

Another native rest is sent only after an allowed status-set transition or a
recognized native completion (`HP restored.`, `Magic restored.`, `Done waiting.`).
Recognition uses the displayed text after stripping native colour markup,
so a coloured `Magic restored.` continues toward full HP when still injured.
An unexplained interrupt returns `native_interrupt`. Recovery stops for any
visible non-friendly monster or invisible marker, unresolved unseen-threat
evidence, attack/encounter messages, damage, MP spending, unexpected statuses,
HP/MP maximum changes, inventory/weapon changes, local hazards, changed
position/depth/level, menus/prompts, exit, or unsettled/no-turn output. Public
events are watched throughout the native action: observed damage, transient
threats and disallowed statuses remain latched even if the final state looks
recovered. This supplements the native interrupts; it does not replace them
with single-turn rest or inject cancellation keys into an uncertain input mode.

`wait-for` requires a currently visible sighting ID chosen after assessing that
foe as suitable for waiting. It requires one isolated hostile, HP at least 85%,
no unapproved player statuses, and threat at most 1; `--min-hp-percent` (1–100) and
`--max-threat` (0–4) change those thresholds. It sends one `.` at a time, stops
within `--distance` cells (Chebyshev distance, default 2, allowed 1–8), and never
approaches or attacks. HP-only increases are permitted; each wait step records
`hp_before`, `hp_after` and net `hp_change`. Any observed HP loss still stops,
even if healing restores HP later in the same step. Any new message, MP/status change,
new/lost monster ID, changed monster appearance, unknown identity/attitude,
enemy status icon, or positive ranged/reaching weapon hint returns control.
Common position, resource, hazard, prompt, unseen-threat and settling guards
also apply. Absent weapon hints do not establish that the foe has no ranged
abilities; the caller must make that tactical assessment.
Repeated `--allow-status` explicitly permits existing assessed statuses such
as flight. It does not permit changes: new statuses, expiry, and changed
status data still return control, even for allowed labels.

`wait-for --monster-id ID --assessed-ranged` explicitly permits the selected
foe’s public ranged/reaching weapon hint after tactical assessment. Hybrid
abilities without weapon hints were already possible: missing hints never
establish melee-only behavior. The flag does not permit groups, new/lost foes,
messages, damage, MP/status changes, prompts or uncertain output. A quiet
approach can be compressed; the first spit/cast/shout returns control.

`wait-for` accepts definite friendly summons alongside the chosen hostile.
It still stops on messages, ally status/attitude changes, disappearance, new
creatures and every ordinary damage/input guard. This does not permit waiting
through a summon's fight.

Both default to six actions and ten seconds; `--max-actions` accepts 1–32 and
`--max-seconds` 0.1–30. An action is one native rest or one wait, **not one game
turn**. The deadline bounds the harness invocation including settling and
pagination, but cannot cancel an already-running native rest. If it returns
unsettled, observe before sending any further input. No ambiguous action is
retried. Full recovery or distance success is checked before returning a bound.

Both return `recovery: {operation, policy, stop_reason, actions, turns,
elapsed_ms, steps}` with the final observation and all accumulated messages.
Ordinary observations clear this field with null. Metrics use one high-level
`act` per invocation, plus `recover_step`/`wait_for_step`; `stats.recover_steps`
and `stats.wait_for_steps` count internal keys separately. Pagination retains
separate `auto_more` records and never answers a real decision.

Unseen-attacker message evidence requires a combat phrase (for example,
`Something hits you` or `The invisible orc wizard hits you`). A noun such as
`claws` in monster flavour text does not establish an attack. These rules do
not exempt named visible monsters from simultaneous unknown attacks. Existing
`unseen_threat` evidence is never cleared by a benign message or an empty map;
it still requires explicit `acknowledge-threat` after the controller assesses it.

CLI policy compatibility: inactive optional lists and false switches are
omitted from `combat`, `recover`, and `wait-for` requests; numeric zero (such
as `--max-threat 0`) is retained. Policies are validated locally before sending.
Explicitly requested features are never silently dropped. Known legacy
pre-execution unknown-policy/unknown-command rejections return
`error_code: restart_required`, `operation`, `requested_policy_options`, and
the original `daemon_error`, with controller-coordinated save/stop/start
guidance. This uses the single request's rejection rather than probing or
resending the action. Other validation errors and ambiguous timeouts retain
their original meaning. Compatibility errors do not consume an output-stream
cursor, and no daemon is restarted automatically.

## Menus and targeting

Inventory menu `ui.items` uses absolute row positions and is capped at
`total_items`. Unloaded chunks are empty objects, not actionable choices.
Category changes trim old trailing rows; newly received native rows clear
omitted hotkeys/tiles/colour just as WebTiles does. Read the current category's
rows before selecting a key.

While targeting, `targeting` exposes `cursor` (absolute and relative coordinates),
`selected_monster` (only currently visible), `invisible_marker`, `range`,
`line_of_fire`, `preview_cells`, `landing_cells`, `invalid_aim_cells` (offset pairs),
and current-input `feedback`. Cursor, preview and landing cells are distinct;
`impact_point` remains null because the protocol does not identify it separately.
Combined blocked/out-of-range overlays retain that ambiguity. Missing overlays
do not establish a clear shot or valid range. Compact output sends null on exit.
`target --dx N --dy N` sends only a public cursor move; the game may reject it,
so verify the returned cursor before explicitly firing. Escape cancels.
Publicly marked `invalid_aim_cells` are rejected before sending cursor input.
The ranged helper checks the intended monster's square against the same public
list and returns `out_of_range_or_invalid` without attempting that cursor move.
An unmarked square is not proof that an aim is valid.

## Ranged attacks

`ranged` removes the menu/aiming round trips for a caller-selected resource
and hostile monster sighting. It prepares or submits at most one attack:

```sh
./crawl-agent --session mygame ranged --wand-letter c --expect-name 'wand of iceblast (4)' --monster-id 101 --prepare-only
./crawl-agent --session mygame ranged --wand-letter c --expect-name 'wand of iceblast (4)' --monster-id 101 --allow-area
./crawl-agent --session mygame ranged --weapon-letter d --expect-name '+0 sling' --current-quiver 'Fire: d) +0 sling' --monster-id 101
```

Start at a settled command prompt. `--expect-name` must exactly match the
current public inventory name, including charges/enchantment; the wand letter
uses the wands namespace, the weapon letter the equipment namespace. The
current quiver description must exactly match `player.quiver_desc`, identify
the same already-wielded launcher, and be enabled. This version supports
wands and ammunition-free launcher firing. It never equips, cycles resources,
selects a missile stack, or casts spells.

After checking the current evocation menu and selected resource, the helper
resolves the visible hostile ID again and sends the same public cursor move
as `target`. If the native default already selected that monster, it first
recentres on the player to require a fresh, observable movement. Native mouse
aim acceptance checks range/validity; a positive ray/affected preview on the
requested monster supplies additional path evidence. Missing, grey-only,
invalid, blocked, mismatched or uncertain results stop without firing. The
metadata records `aim_evidence`; ordinary `targeting.range`/`line_of_fire`
retain their conservative schema. Preview evidence is not a guaranteed hit
or impact point.

By default no other monster or player may appear in any public preview cell,
including possible-effect/path/landing cells. `--allow-area` permits other
hostiles; `--allow-friendly` permits friendly/neutral creatures; `--allow-self`
permits the player. These are separate permissions for the assessed shot.
Unseen cells/markers always stop because their occupants cannot be checked.
The helper does not decide whether an area attack is tactically desirable.
Native confirmations are returned for a decision, even with these allowances.
Any other preview occupant nearer than the requested monster, including
scenery such as plants, stops with `possible_interception`. Area permission
does not bypass this check. Because the public preview mixes paths and areas,
this can conservatively stop an area or piercing attack that would work;
assess it manually. A positive target overlay alone cannot prove that a
projectile will pass through an occupied square.

`--prepare-only` follows the same checks and leaves targeting open. A safety
stop can also leave a menu or targeting open. Inspect the result before Enter
or Escape; Escape back to command before another `ranged` invocation. A shared
`--max-seconds` deadline defaults to 10 (0.1–30 allowed). Uncertain preparation
stops; uncertain submission is never retried. After an ambiguous delivery,
observe before deciding whether any further input is appropriate.

The final observation retains all intermediate game messages and contains
`ranged`: `phase`, validated `opened_menu`/`selected_item`/`aimed` flags,
`submitted`, `stop_reason`, requested `policy`, `resolved_target`, `exposure`,
actual/attempted `inputs`, `keys_sent`, `turns`, and `elapsed_ms`. `submitted`
means Enter was attempted, not proof that the shot executed. `charges_used`
reports observed wand consumption; `resource_consumed` is null when unknown.
Launchers in the supported Crawl revision consume no ammunition. Damage, statuses and movement
remain in the final player/monster observation; there is no second shot.
Metrics record one `ranged` action and its `ranged_step` events (cursor moves
are inputs but not keyboard keys); `stats` reports `ranged_steps` separately.
Older daemons return restart guidance without retrying or downgrading policy.

## Spells and Searing Ray

`cast --spell-letter b --expect-name 'Searing Ray' --monster-id ID
--max-failure-percent 5 --min-mp-after 6 --channel-actions 3` verifies a fresh
native spell menu and aim, submits one cast, then permits up to three ray pulses.
Default: one cast, zero continuations, 10 seconds (30 maximum), at least 85% HP,
no damage, hazards, unexpected statuses or other public state changes. Current
supported spells are Freeze, Searing Ray and Ozocubu's Armour. Choose exact
letters/names from `spells.entries` while the menu is open; that field clears
when the menu closes. `--allow-status LABEL` permits that exact initial status;
changes still stop. Unsupported spells stop before any input.

Both the failure ceiling and MP reserve are required. The native menu exposes
spell level, not a separate mana cost. The supported Crawl revision's normal mana cost never
exceeds that level, so the helper checks that conservative upper bound before
selection. Targeted spells then publicly reserve their MP before aiming:
`resolved_spell.mp_cost` reports the observed debit, and cancellation refunds it.
`--prepare-only` leaves targeting open without spending a game turn; the temporary
reservation is **not** proof of consumption. Escape cancels. For instant armour,
prepare-only is rejected before selection, and its exact MP cost stays unknown;
the level bound protects the reserve. Existing armour returns `already_active`.

Ray continuation requires this invocation's fresh `Ray`, `Ray+`, `Ray++` public
status sequence and the original target on its validated path. Target movement,
changed/unknown terrain, new occupants, lost foes, channel completion, damage,
MP loss beyond the expected pulse, and uncertainty stop before another wait.
No ray is restarted or retargeted. A miscast gets no continuation. Native safety
prompts are returned, never answered. Doom/contamination and damage are latched
even when later updates undo them. Check `cast.phase`, `stop_reason`, `submitted`,
`actions`, `turns`, `steps`, `channel`, `targeting_open`, starting/ending MP and
nullable `resource_consumed`; automatic pagination is counted separately in
`auto_more`. A timeout never authorizes retrying a submitted action.

`cast ... --expect-name Freeze --max-casts 3` opts into repeated Freeze against
that same visible sighting (hard maximum 32). Each attempt verifies a fresh
spell menu and aim and rechecks the reserve. The whole sequence shares one time
budget. Miscasts, uncertainty, damage, target loss, status/gear changes or a
changed spell letter stop immediately. `cast.attempts` retains each result.
Other spells and prepare-only cannot use a count above 1. Defaults remain one.

## Stationary summon and Ramparts support

`hold --monster-id ID --allow-friendly-id ALLY_ID --stop-distance 2` sends at
most six `.` waits, checking every response. Repeat the friendly ID option for
other explicitly assessed allies; all visible creatures must belong to the
chosen group (scenery remains separate). With no named allies, active Frozen
Ramparts is required, with `--allow-status Ramparts`. `hold` does not cast it.
Current channels are rejected. The hostile must remain beyond `stop-distance`
(default 2, minimum 1); this is a distance check, not an ability classifier.

Default guards require at least 85% HP, threat at most 1, no hazards or unseen
threat, and known friendly attitudes/appearances. The helper permits positive
HP/MP regeneration and selected-foe wound changes. Its small message allowlist
recognizes named ally hits/misses/freezing, the selected foe's wound/resistance
messages, and Ramparts wall damage. Every clause must match. Unknown messages,
ambiguous duplicate participant names, ally attacks received/wound changes,
ally loss, new creatures, player damage/MP loss, status/equipment changes,
movement, expiry, prompts and uncertainty stop. It never approaches or attacks.
Damage and Doom/contamination increases remain latched across all frames.

Statuses are exact initial labels. Both `--allow-status ice-armoured` and
`--allow-status 'ice-armoured (expiring)'` explicitly permit that one native
armour warning transition; armour ending still stops. Other status transitions
remain stops. Read `hold.phase`, `stop_reason`, `submitted`, `actions`, `turns`,
`steps` and all messages. No ambiguous wait is retried. Maximums are 32 actions
and 30 seconds; defaults are 6 and 10. These are bounded conveniences, not tactical
safety guarantees. Existing melee, ranged, recovery and wait-for defaults stay
as documented above.

## Assessed hazards

For independently assessed flight, `combat`, `recover` and `wait-for` accept
`--allow-water-with-flight`. This requires current public `Fly` and typed shallow
or deep water. You must also allow the current player statuses normally. Other
terrain, traps and clouds still block. Status, form, equipment/stat changes and
any observed damage stop before another key, even if restored within the reply.
It does not authorize movement or establish that flight will last a whole rest.

`--allow-cloud poison` is a separate, per-invocation allowance for `combat`,
`recover` and `wait-for`. The controller must assess current worn poison
resistance: inventory possession and cloud appearance do not prove immunity.
The supported Crawl revision's native poison-cloud immunity requires positive poison resistance
(or general cloud immunity); direct breath attacks have separate effects.
Only the exact known poison appearance is accepted; ambiguous types, other
clouds and stacked terrain still block. The allowance stops on any observed
damage, MP loss, status/form/equipment/defensive-stat change or monster change,
including transient events. Public observations cannot certify every hidden
resistance change. No movement or automatic confirmation is authorized.

## Skills screen

Open skills with `act --keys m`. A `ui` entry with `type: "crt"` and
`tag: "skills"` identifies the screen; `text.menu_txt` contains the game's
readable rows, column headings, explanations, and current hotkeys. Read the
displayed keys before changing training: letters can change with the list
of skills shown. `-`, `+`, and `*` mean training disabled, enabled, and
focused. A row without a selection key must not be treated as selectable.

The screen shows one view at a time. Its `!` legend cycles training
percentages, costs, and targets when available; `=` enters target selection.
Numeric target entry is exposed as `input_mode: "prompt"`. Help and skill
descriptions use their ordinary menu layers; follow the displayed prompts
and Escape out of each layer before resuming gameplay.

Selected switch values are bracketed in the text, for example
`[/] auto|[manual] mode` and `[!] [training]|cost|targets`. This preserves
the meaning of the game's white/dark-grey selection colours when HTML is
removed. These annotations apply only to the skills switch legend; skill
rows and explanatory text retain the game's wording. The rest of the CRT
colour styling is omitted. Skills are exposed as readable screen text,
not a separate structured skill database or cached off-screen state.

Unchanged text is omitted from compact observations, and `text: {}` clears
the screen on exit or when a structured description covers it. `--full
observe` recovers all currently displayed text. The usual automatic
pagination acknowledgments still apply unless started with `--no-auto-more`.

New daemons run Crawl on a private 80-by-24 terminal. Crawl's `-headless`
mode skips CRT rendering, which prevents WebTiles from sending skill rows.
The adapter drains terminal output into `crawl.log` (including terminal
escape sequences); it neither parses that output nor sends terminal input.
All observations and actions still use the public WebTiles protocol. This
requires a normal save/stop/start to activate in an existing session; let its
controller perform that restart. No engine rebuild is needed.

## Maps and navigation

Map exclusion radius entry is buffered locally: `act --keys R` exposes
`map_input` and sends no game key. A digit 1–8 submits `R` plus the digit in one
native text-input record; Escape discards the buffer while leaving the map open.
Changed cursor/level context or invalid digits require correction/cancellation.
`keys_sent` counts requested keys submitted; `deferred_keys_sent` separately
counts a buffered R flushed from an earlier call. Known stock map navigation,
feature cycling and exclusion editing use a same-socket, read-only full refresh
after submission, permitting `map_resynchronized` even if nothing changed.
This requires version/UI/full-map/cursor receipt and a completed quiet frame,
with the map still open and turn unchanged. Missing/partial replies remain
unready. Gameplay/travel keys and unanswered native prompts never receive this
allowance; no native key is retried. Existing daemons require a planned restart.

`level_map` is separate from the 17×17 map snapshot and combat `targeting`.
While the native level map is open it reports the public map cursor coordinates,
selected feature ID/name/glyph and visible/remembered/unknown knowledge. It
retains distinct coordinates for repeated identical feature descriptions.
Player-relative offsets and `level` are present/known only with public
`player_on_level: true`; off-level identity is left null rather than inferred.
No route or unseen feature is invented. Map exit emits null; a map reset clears
the old cursor until the next public cursor packet. Ordinary deltas omit an
unchanged selection and `--full` restores it. This requires a daemon restart.

`terrain [--dx N --dy N]` reads the selected square and eight neighbors, relative
to the current player. `terrain_neighborhood.cells` names each terrain ID and
its visible/remembered/unknown state, with exact coordinates. The command sends
no game key; it does not describe cloud safety or predict Rampage movement.
Use settled current-level observations; querying a different map level fails.

### Visible features and routes

Inventory entries include `category`, `letter_namespace`, and `name_current`.
Equipment shares letters across weapons/armour/etc.; consumables use their
category's letters (`potions`, `scrolls`, `wands`, etc.). Use the current menu
when categories are combined. A slot is an internal inventory position, not
a hotkey. Identification may omit a name in native deltas; the adapter requests
a standard spectator refresh when flags/letters change without one. If it
times out, `name_current: false` and `settled: false` preserve the uncertainty.

Monster IDs are public **sighting** IDs. Crawl deliberately resets them when
a creature leaves sight; reappearing IDs must not be correlated with old
creatures. ID-less replacements never inherit an earlier ID. Each monster
has `location_status`: `visible`, `invisible_disturbance`, or
`remembered_invisible`. Invisible markers have unknown navigation, including
when a remembered marker lies under the player. They are not verified attack
targets. `wounds`, `icons`, and `visible_weapons` describe public tile
appearances. Weapon sprites may provide positive `reaching` or `ranged`
`attack_hint` values; absent/unknown hints never establish melee-only behavior
and do not rule out spells, missiles, alternate weapons, or custom sprites.

Verified ordinary plants/fungi/bushes, petrified flowers and harmless piles of debris are listed
in delta-compressed `scenery`,
with coordinates and navigation retained, instead of repeating in `monsters`.
This requires public type/no-XP/threat/icon evidence, not a name heuristic.
All occupied scenery cells continue to block transit routes.
Debris requires the generated public `MONS_PILE_OF_DEBRIS` type, explicit
no-XP metadata and zero threat, a visible location and permitted scenery icons.
Petrified flowers likewise require their generated public type, no-XP metadata,
zero threat, known attitude, visibility and permitted icons. Confusion, berserk,
inner flame and unknown icons prevent scenery classification.
Other threat-zero creatures, statues, similarly named entities, or entries
missing required evidence remain in `monsters`. Guarded combat/recovery/waiting
ignore verified scenery for enemy checks without making its cells traversable.

`unseen_threat` is always present. Reports of unseen attackers remain latched
even if the visible monster list empties or the level changes. Public evidence
is retained in `awareness.json` across normal daemon restarts. After assessing
and resolving the uncertainty, use `acknowledge-threat` to clear it explicitly;
this is logged and sends no game key. Recognition covers common attack text,
not every possible source of danger. An empty list/null flag is not proof of
safety, and ordinary `act` remains available for manual decisions.

`visible_features` uses only currently visible WebTiles cells. It does not
list remembered landmarks or expose an explored-level map. Each entry has
`kind`, `name`, and a location offset: `dx` is positive east and `dy` positive
south. Offsets identify the destination; use `navigation` for movement order.
Visible monsters carry the same offsets and navigation information.

Cloud entries retain `kind: "hazard"` and add `cloud_type`, decoded from the
public tile appearance. Names distinguish flames, poison gas, smoke, mist,
and other supported appearances. Shared sprites have `cloud_type: "unknown"`
and `possible_types`: grey smoke and scalding steam, for example, share a
sprite and cannot be distinguished from this map data. Unrecognized tiles
remain `name: "unknown cloud"` with their `tile` ID. These are appearance
labels, not character-specific safety or damage assessments; custom cloud
sprites may also obscure the underlying type.

Eight-connected clouds with the same appearance are grouped. Each entry's
`cells` lists **all** visible `[dx, dy]` offsets in that group. Its top-level
`dx`, `dy`, and single `navigation` describe an accessible approach to one
representative cell (or an unknown route); they do not authorize traversing
the group. Clouds underfoot remain present. All clouds, including smoke,
remain excluded from transit routes. Visibility changes split/remove groups,
and tile animation does not unnecessarily change their labels. The ASCII
crop remains available for checking the shape.

For example, when north-first hits a wall but east then northeast is clear:

```json
{"kind":"item","name":"potion","dx":2,"dy":-1,
 "navigation":{"status":"visible_route","target":"cell",
   "steps":[{"move":"e","count":1},{"move":"ne","count":1}],
   "text":"1 east, then 1 northeast"}}
```

Every intermediate cell must be visible and have known walking semantics.
Routes consider all eight directions and minimize movement/open-door actions,
then the number of instruction segments. Crawl permits diagonal movement
between corner walls; the destination of every step must be visible and
traversable. Door opening remains a separate action. Routes avoid monsters,
clouds, travel exclusions, invisible disturbances, and uncertain terrain,
including water and traps. They do not infer character abilities or calculate
line of fire. Reassess after each action: a route describes current geometry,
not a guarantee against combat, movement effects, or a new prompt.

- `status: "visible_route"` describes a path through known walkable terrain.
- `status: "requires_open_door"` includes separate
  `{"action":"open_door","direction":"e"}` operations before entering
  closed doors. Follow the game's opening prompts and observe again; special
  door restrictions are not exposed by terrain IDs. Runed and sealed doors
  are never used as transit routes.
- `status: "unknown"` has no movement steps and says "no verified visible
  route". It does not mean the target is unreachable outside current sight.
  When available, `reason` contains a `kind` and named `blockers` with relative
  offsets. `visible_occupancy` means removing visible monsters alone would
  connect an otherwise verified local approach; these hypothetical steps are
  never returned as navigation. `direct_barrier` identifies solid terrain on
  the direct approach, without claiming that every possible route is blocked.
  Insufficient evidence leaves the explanation generic.
- `target: "adjacent"` stops beside a monster, closed door, or hazard;
  `target_direction` points from that endpoint to the target. It does not
  include an attack or entry. A door's `interaction` is separate from its
  approach route. `target: "cell"` ends on the feature itself.

`direct_path_barriers` names visible solid obstacles on an unambiguous
cardinal or diagonal direct approach. It is independent of the proposed
route, which may go around them. Transparent barriers have
`blocks_movement: true`. Eight-connected equal terrain appearances, including barriers and water,
are grouped with exact visible `[dx, dy]` entries in `cells`. Shallow/deep water
remain separate. The entry's `dx,dy` is a chosen anchor with one checked approach,
not the start of a filled rectangle or a route through the group. Occupied and
unknown approaches remain explicit; unseen gaps are never filled.

Ground weapon and armour names use their public tile appearance, for example
`hand axe`, `magic hand axe`, `randart hand axe`, or `unrandart weapon`.
Other items retain generic glyph categories, or simply `item` when obscured.
Appearance alone does not reveal enchantments, brands, quantities, or full
stacks. An unambiguous inspection replaces the appearance name with the
game's exact title, such as `+1 hand axe` or `+1 hand axe of flaming`, in the
existing `name` field; there are no extra equipment fields.

To read the game's player-visible descriptions,
use `inspect --dx N --dy N` at a settled command prompt. It sends the stock
WebTiles right-click operation for that currently visible square and returns
its description or examine menu in `ui`. For multiple objects, follow the
displayed hotkeys to choose one. Escape closes each menu layer; inspection
does not automatically select, pick up, or close anything. It refuses unseen
or remembered-only squares and pending menus/prompts. A timeout returns the
current unsettled observation without retrying the click. `observe` continues
to send no gameplay input. Inspection counts as one action-log entry (`inspect:dx,dy`), with
zero requested/sent keyboard keys.

Inspected equipment names are reused only for a single, unchanged visible
item. Stacks are not associated with exact names. Item/render changes,
obscuring, lost sight, and level/map resets discard the association; an item
underfoot also loses it on the next gameplay turn. Reinspect to restore an
exact name after it falls back to appearance. Names are not saved across
daemon restarts.

The ASCII crop remains in full JSON/text observations, with its existing
omit-if-unchanged behavior in compact output.

Terrain names and equipment appearances come from the checked-in static
metadata for the supported Crawl revision. Unknown terrain is not treated as
walkable; unknown equipment tiles retain generic categories. See
[metadata maintenance](development.md#crawl-version-updates) before changing
Crawl versions. No game memory or save contents are inspected.

These fields are produced by the daemon and become available on its next
launch. An already-running session needs a normal save/stop/start to load
them; coordinate that with its controller. The compact CLI presentation
alone does not upgrade the daemon's map data.

## Saves and checkpoints

`save` sends Ctrl-S, saving and exiting through the normal game interface when
at the main game prompt. `stop` disconnects using SIGHUP (Crawl's normal hangup
save path), then terminates a stuck process if necessary. For an explicit
save, use `act --action save` before `stop`. Stopping retains session files.
After game exit, the adapter remains available to inspect the final state;
`stop` shuts down that adapter before another `start`.

### Saves and morgues

Native Crawl files live inside the session directory:

- `saves/Agent.cs`: the resumable character save (substitute your character name).
- `morgue/Agent.txt`: an on-demand character dump, produced by
  `act --keys '#'`.
- `morgue/morgue-Agent-<timestamp>.txt`: the end-of-run character report.

For example, to save and later resume the existing `Agent` character:

```sh
./crawl-agent --session mygame act --action save
./crawl-agent --session mygame stop
./crawl-agent --session mygame start --foreground --name Agent
```

Use the same `--session` (or `--session-dir`) and `--name` when resuming a
custom character. `start` defaults to the name `Agent`; it does not infer a
custom name from a previous launch. There is no separate `resume` command.
Normal Crawl permadeath applies: ending a run removes that character's save
and leaves its morgue. An abrupt forced kill can lose progress since the last
save; `stop` reports `forced: true` if graceful shutdown failed.

### Checkpoints

Use `./crawl-agent --session mygame checkpoint` at a controller-chosen settled
command prompt to preserve progress. It submits native save/exit once,
confirms the save, reloads the same character/options, and verifies the loaded
turn, public stats/status/equipment and inventory. Read `checkpoint.verified`,
`phase`, `differences` and any error. Coordinates/sighting IDs can reset;
all reader streams receive a full observation after the native game reloads.
The intent and detailed verification persist in `checkpoint.json`; a lost
reply is not permission to repeat input. Inspect that file and observe first.
A failed verification returns a nonzero CLI status and never retries actions.

Checkpoint keeps the persistent Python adapter running and does not load
adapter updates or change its startup changelog baseline. It needs no watcher.
For adapter code updates, use the normal controller-coordinated save/stop/start
at a planned maintenance pause. Progress after the last completed native save
can still be lost if the process disappears; checkpoints are explicit milestones.

Checkpoint compares inventories only after complete read-only public refreshes
before save and after reload. `inventory_comparison` reports
`fresh_public_and_exact_weapon_descriptions`.
Names/properties/charges remain exact; artefact titles are not stripped.
Differences identify changed slots and fields instead of repeating unchanged
items. The detailed before/after audit remains in `checkpoint.json`. An
incomplete refresh is unverified and never causes a save/action retry.

Checkpoint weapon verification reads native item-description titles on both
sides of save/reload. It accepts a shortened artefact display label only when
both exact titles, enchantment and complete property suffix agree; raw display
changes remain in `checkpoint.display_name_changes`. Description uncertainty
stops the checkpoint. The journal preserves raw inventory snapshots.

## Logs and statistics

Every newly started adapter writes `actions.jsonl` in its session directory.
It appends one small JSON record per `act`, plus a `start` record on each
launch/resume. No maps, messages, inventory, or raw key sequences are stored.
Typical action records use about 300 bytes (roughly 3 MB for 10,000 actions).
Existing logs are retained when resuming; action numbering restarts at each
`start` record. Already-running adapters need a save/stop/start to enable this.

Read the log directly or summarize it, even after the adapter has stopped:

```sh
./crawl-agent --session mygame stats
tail -n 5 .crawl-agent/mygame/actions.jsonl
```

Each action record contains:

- `n`, `ts`: action count within this adapter launch and Unix start timestamp
  in seconds.
- `action`: the requested alias (`explore`, `rest`, etc.), `move:e`, a named
  key such as `key:Escape`, or `keys` for a literal sequence. These labels
  describe input, not inferred gameplay outcomes such as attacks.
- `requested`, `keys_sent`: requested and actually sent key counts; a timeout
  or game exit can cut a sequence short. One `act` counts as one action even
  when it sends multiple keys or advances many game turns.
- `turn_before`, `turn`: the game's turn counter before input and after the
  action; menus and no-ops often advance zero turns.
- `place`, `depth`, `xl`: resulting branch, branch-local depth, and character
  experience level, as reported by WebTiles. Unknown values are `null`.
- `latency_ms`: adapter handling time, including settling and observation
  construction, but excluding CLI startup, socket delivery, and JSON output.
  This measures the whole `act`, not each individual engine turn.
- `idle_ms`: time from the previous action's completion (or initial attach)
  to this request, including controller thinking, CLI overhead, and any
  intervening `observe` calls. Both durations use a monotonic clock.
- `settled`, `running`: whether output settled and the game remains running.
  An action that fails during processing also includes a short `error`.
  Requests rejected before input processing are not counted.

`stats` aggregates all launches in the file: action counts by label, keys
sent, observed turn advances, failed/unsettled actions, mean/min/max timing,
and the latest logged turn/depth/level. `observed_turns` sums nonnegative
counter changes within each launch; it excludes turns before attach and
between launches. These are last-observed counters, so timed-out actions or
updates after the last logged action may leave progress unreported.
`observe` and `stop` do not add action records. `stats` reads the file without
contacting or sending input to the game.

Internal pagination writes separate `event: "auto_more"` records with a
`count`, `context` (`act` or `observe`), and `stop_reason`. `stats` totals these
as `more_acknowledgments`; they do not increase action counts or requested
`keys_sent` and are not separate controller decisions.

Full protocol logging is disabled by default. For diagnosis, opt in with
`start --log-events` to also append incoming/outgoing WebTiles traffic to
`events.jsonl`. This file can grow much larger; existing `events.jsonl` files
are left untouched when the option is off. `crawl.log` and `adapter.log`
continue capturing process output.

## Watch in tiles

The viewer needs Tornado, PyYAML, and matching external WebTiles server and
browser assets. Set `[crawl] source` in your INI file, `CRAWL_SOURCE`, or
`watch --crawl-source PATH` before starting it. CLI play needs none of these
optional viewer dependencies.

```sh
python3 -m venv .crawl-agent/venv
.crawl-agent/venv/bin/python -m pip install -r requirements-viewer.txt
./crawl-agent --session mygame watch --crawl-source /path/to/crawl-ref/source
```

The launcher prefers this local environment when it exists. If `venv` cannot
bootstrap pip, install your operating system's Python venv support first.

Open the URL printed by `watch`, normally
`http://localhost:8080/#watch-Agent`. No login is needed. `watch --port 8081`
uses another port. The viewer uses the stock DCSS WebTiles server and browser
client, attached as a spectator to the existing game. Run it in another
terminal while your LLM invokes `act`; Ctrl-C stops the viewer independently.
It listens on localhost only. `watch --all` serves a shared lobby for sibling
session directories; duplicate names use `CHARACTER@SESSION`. New sessions and
restarts are discovered every second, including when the lobby starts empty.
Select a game again after its restart.

The lobby requests one spectator snapshot on attachment, then follows public
player updates for XL, location and turns. Missing values show `Unknown`, and
zero turns/time remain visible. Time means Crawl's reported real played duration,
not the player's in-game time counter or the viewer's uptime. Crawl supplies
that duration in milestone/status reports, not every player update: it remains
unknown until a report arrives and shows `(last report)` when its turn differs
from the current turn. Hover over it for the report's turn. Restarted sessions
begin with fresh metadata; saved files are not treated as current observations.
The small lobby renderer overlay lives at `assets/crawl_lobby_fields.js`; upstream assets stay
unchanged. Restart an existing viewer to load viewer fixes; game daemons need
no restart for these changes.

## Timing and limitations

All polling stays inside the adapter. The default quiet window is 75 ms after
the last update; normal turns should take roughly that window plus game and
CLI startup time. Configure it with `start --settle-ms 150 --timeout 5`.
Startup has a separate 60-second allowance for database and map generation.

`act` and `observe` automatically send Space only for plain message
pagination: input mode `more`, the message-window more flag, no custom more
text, no menu/popup, and a living character with no known exit reason.
They settle again after each acknowledgment and stop at attributes,
targeting, confirmations, item selections, menus, or postgame choices.
An acknowledged page also ends a literal key batch, so its unused keys
cannot spill into a newly exposed decision. `keys_sent` reports how many
requested keys were sent.

Acknowledgments share the request's `--timeout` budget (default five seconds)
and stop after 32 pages. Any remaining prompt is returned as usual, with
`more_pending_reason` explaining `timeout`, `unsettled`, or
`acknowledgment_limit`; this field is otherwise `null`. Use
`start --no-auto-more` for manual control/diagnosis (`disabled` is reported
for a plain more prompt). An existing daemon needs its next normal restart
to acquire this behavior. `observe` can acknowledge pagination but never
selects an option or deliberately takes a gameplay turn.

After input, settling requires a change in public player/message/UI/text/cursor
state or entry into a different input mode, a complete public update frame,
and then the quiet window at an input-ready state. Crawl's `flush_messages`
record closes the frame; complete JSON records or command mode alone do not
establish that the player/map updates have arrived. An unchanged command-mode
round trip and a flush alone do not acknowledge an action. Queued updates are
drained before establishing a manual action's response baseline.
Repeated unchanged packets and spectator redraws alone do not acknowledge
an action. No speculative spectator refresh is sent for a silent command;
refreshes remain available to resolve stale inventory names. A response need not
consume a turn: changed messages, menus, confirmations and distinct prompts count.
The public `ui_state: UI_VIEW_MAP` signal is input-ready and appears as
`input_mode: map`. Native `init_input`/`update_input`/`close_input` records track
line prompts in `text_input` and expose `input_mode: prompt`, even when Crawl's
mouse mode is `normal`. Unknown mode, or `normal` without one of these signals
or a menu, remains unready; quiet travel is not mistaken for a prompt.

For `stash_search` and `travel_depth` prompts, the adapter mirrors the browser's
local text editor. `act --keys '12'` buffers text, reports `keys_buffered: 2`,
`keys_sent: 0`, and `settle_reason: text_buffered`; `text_input.text` shows the
current buffer. Printable characters, Backspace and Ctrl-U edit it. Enter
submits one native `text_input` record with the browser's clear-line prefix;
Escape discards it through native cancellation. Travel's special terminators
(`<>?$^-p` and Ctrl-P) and empty-search `?` retain their native behavior.
Submission still requires response evidence and is never retried. Other prompt
tags are exposed but retain native key handling. Text buffers have normal delta
semantics and are included in full observations; they are cleared when the
native prompt closes. Buffered text is private to this adapter until submission,
so spectators do not see intermediate edits.

A directional key aimed at visible solid non-door terrain without a monster
can return `blocked_move_resynchronized`: one complete public refresh confirms
the unchanged turn/position and blocking square after the key. The movement
is never retried. Missing/partial refresh stays unsettled; observe again.
Doors, monsters, remembered/unknown terrain and ordinary movement receive no
no-op allowance. This does not treat arbitrary timeouts as completed actions.

At the timeout, `settled: false` and `settle_reason` explain `no_response`,
`input_not_ready`, `incomplete_frame`, `inventory_refresh`, `startup_incomplete`,
`level_generation`, `level_refresh`, or `updates_pending`.
The native `progress-bar` popup is noninteractive. While it is open, old player
state and command mode do not establish readiness. After it closes, the adapter
waits for fresh player level/progress data, a nonempty map update and the closing
flush, followed by the ordinary quiet window. Progress redraws do not satisfy
that refresh. This guard persists across timeouts and later observations;
observe again without resending the travel/action key. Popup titles are not
matched as text, and ordinary menus/character choices remain interactive.
Initial readiness requires public name, HP/MP and maxima, turn, XL, place/depth,
position/status, explicit inventory receipt (an empty inventory is valid), a
full-map receipt with a visible player square, and version. Partial startup
state remains unready across later observations and blocks gameplay keys.
The adapter can request one read-only `spectator_joined` refresh per startup;
it never sends a game key to provoke data. `startup` reports missing fields,
whether resynchronization was requested, and recovery guidance. Observe again;
if it remains incomplete, coordinate a normal stop/start. Recognized native
character-creation layouts remain interactive with `startup.status` set to
`character_creation`; leaving those layouts reinstates the completeness guard.
After initialization, `startup` becomes null. Existing daemons require a
normal controller-coordinated restart to load these guards.
An incomplete frame remains pending across observations and idle draining, even
if the updated turn or command mode has already arrived. Unresolved response
evidence survives later `observe` calls and idle socket draining; another action
is blocked until it resolves. One narrow recovery exception is exposed by
`cancel_available: true`: after an unchanged, silent native target-cursor move
or target cycling key (`+`, `=`, `-`, Tab), a single explicit Escape may be sent.
The unresolved operation stays `settled: false`; Enter, further aim changes,
batched Escape-plus-action, and helpers remain blocked. Partial protocol records,
incomplete update frames or changed state disable this exception. Direction-only targeting, firing keys,
and text submission never receive this allowance. No cancellation is automatic.
Native Tab searches for portals; cycle monsters with `+`/`=` (forward) or `-`
(backward). These are Crawl's default targeting bindings.
The adapter never retries input automatically. Successful settling reports
`response_quiet`, `quiet` (observation without pending input), `text_buffered`
(local line edits), or `exited`.
This remains a **heuristic**, not an engine acknowledgment: frame boundaries
carry no request IDs, and unrelated changes in a complete frame can still be
mistaken for an action response. The frame guard specifically prevents quiet
pauses inside a mode/player/map update from returning a partial observation.

Observation-producing CLI commands return a `timing` object with a unique `request_id`:

- `settle_ms`: daemon time spent waiting/draining for quiet input state, summed
  across helper steps and pagination. Included in `daemon_ms`.
- `daemon_ms`: request handling through observation construction, excluding
  response serialization and socket transmission.
- `rpc_ms`: CLI round trip, including daemon queueing, processing, transmission,
  and reply decoding. `outside_daemon_ms` is its nonnegative difference from
  `daemon_ms`; it is not solely network latency.
- `cli_setup_ms`: Python module initialization and CLI setup before the request;
  excludes shell/interpreter launch. `lock_wait_ms` measures stream-lock acquisition.
- `finished_at` and `received_at`: Unix wall-clock timestamps for correlation;
  durations use a monotonic clock.

The daemon appends a compact `request` record to `actions.jsonl`, even if the
client disconnects before receiving its reply. The CLI appends `timings.jsonl`
after stdout flush, adding `emit_ms` (compaction/cache/output), `client_ms`
(request path through emission, excluding `cli_setup_ms`), and `emitted_at`.
A record establishes that the CLI emitted output, **not** that the caller captured
or displayed it. Compare these timestamps with caller/tool timings to locate
external delivery delays. Missing/ambiguous delivery never authorizes a retry.
Optional `--log-events` records now include wall-clock timestamps as well.
Old daemons remain usable: CLI timings appear immediately, while daemon timings
and response-evidence settling need a controller-coordinated normal restart.

The adapter consumes the existing player-facing WebTiles data; it does not
inspect internal game memory or enable wizard mode. The JSON schema is an
initial interface, not a stable public API. It exposes menus and key input
instead of attempting to enumerate every legal game action. It currently
targets Unix systems with local Unix-domain sockets.

The compact action log is enabled by default; full protocol logging requires
`start --log-events` (see above). The public observations omit most rendering
metadata.

See [development](development.md) for tests and metadata maintenance.

## Adapter updates

See the [adapter changelog](../CHANGELOG.md) for completed behavior
changes and restart applicability. Observation output includes
`adapter_changes.restart_recommended` (`true`, `false`, or `null` when unknown)
and a reason. Detailed reports include the startup/current completed entry IDs
and newly completed entries; unchanged reports retain compact advice.

Successful starts record completed changelog IDs in
`.crawl-agent/SESSION/adapter-startup.json`. Each CLI observation reads only the
changelog and compares `Status: complete` IDs with that startup baseline.
`Status: in-progress` entries and adapter file edits are ignored. Editing the
wording of an already completed entry does not create a new feature; use a new
ID for a completed behavioral correction. Newly completed entries advise a
restart only when their `Daemon-restart` metadata says `required`. Entries that
finish after a session started count even if their drafts existed at startup.
No adapter files are hashed, scanned or compared; the `Files` metadata is for
explanation only. This is a completion heuristic, not verification of loaded
code. Unlogged code edits intentionally produce no restart advice.

The next successful start records a new baseline and retains a historical
comparison with the previous start. Checks never advance a running session's
baseline. Older startup records can supply their already-recorded changelog
IDs; their file hashes are ignored and no migration restart is requested.
Unknown/mismatched baselines, malformed/missing changelogs, removed completed
IDs or completion during startup report uncertainty without recommending a
restart merely to repair tracking. The next planned successful start can
establish a fresh baseline.

`restart_recommended` is informational maintenance advice, not a command to
interrupt work. Task switches do not trigger updates or restarts. Finish the
current task and defer optional daemon changes to a planned maintenance pause
unless the running adapter actually blocks progress. The checker never
modifies code, downloads updates, sends keys, saves, reloads or restarts a game.
