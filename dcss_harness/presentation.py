"""Full/delta observations, text rendering, and optional snapshot envelopes."""

import difflib
import hashlib
import json

from .changes import (
    compact_report,
    read_changelog as adapter_changelog,
    safe_report as adapter_change_report,
)
from .paths import ROOT


def summary(obs):
    p = obs["player"]
    lines = []
    if p:
        lines.append(f"{p.get('name', '?')} | {p.get('place', '?')}:"
                     f"{p.get('depth', '?')} | HP {p.get('hp', '?')}/"
                     f"{p.get('hp_max', '?')} | turn {p.get('turn', '?')}")
    lines.append(f"Input: {obs['input_mode']} | running: {obs['running']}")
    lines.extend(f"{label}: {p[field]}%" for field, label in
                 (("doom", "Doom"), ("contam", "Contamination")) if field in p)
    lines.extend(obs["map"]["rows"])
    lines.extend(msg["text"] for msg in obs["messages"])
    for menu in obs["ui"]:
        lines.append(json.dumps(menu, ensure_ascii=False))
    lines.extend(value for value in obs["text"].values() if value)
    if not obs["settled"]:
        lines.append("Updates did not settle before timeout; observe again.")
    return "\n".join(lines)


def message_changes(before, after):
    """Account for the rolling history and Crawl replacing repeated messages."""
    if not before:
        return {"messages": after}
    # A rollback replaces the tail; a rolling window drops the head. Find
    # the longest retained suffix of the old history in the new prefix.
    best = (0, 0)
    for end in range(len(before), 0, -1):
        for size in range(min(end, len(after)), best[0], -1):
            if before[end - size:end] == after[:size]:
                best = (size, len(before) - end)
                break
    size, rollback = best
    if not size:
        return {"messages": after, "messages_reset": True}
    result = {"messages": after[size:]}
    if rollback:
        result["messages_rollback"] = rollback
    return result


def compact_observation(snapshot, previous=None, full=False):
    """Bulky fields replace prior values; inventory_delta explicitly patches items."""
    def separate_descriptions(obs):
        result = {k: v for k, v in obs.items() if k != "summary"}
        if "player" not in result:
            return result
        player = dict(result["player"])
        descriptions = {}
        if "doom_desc" in player:
            descriptions["doom_desc"] = player.pop("doom_desc")
        if "status" in player:
            statuses, explanations = [], []
            for index, status in enumerate(player["status"]):
                if isinstance(status, dict):
                    status = dict(status)
                    if "desc" in status:
                        explanations.append({"index": index, "desc": status.pop("desc")})
                statuses.append(status)
            player["status"] = statuses
            if explanations:
                descriptions["statuses"] = explanations
        result["player"], result["player_descriptions"] = player, descriptions
        return result

    snapshot = separate_descriptions(snapshot)
    previous = separate_descriptions(previous) if previous is not None else None
    if full or previous is None:
        return {**snapshot, "observation": "full"}
    # Always show enough current state to assess combat and pending input,
    # even when a caller has not reconstructed the optional larger fields.
    always = {"player", "monsters", "input_mode", "more", "more_text",
              "running", "settled", "settle_reason", "cancel_available", "timing", "keys_sent", "more_pending_reason", "unseen_threat", "combat", "recovery", "ranged", "cast", "hold"}
    result = {k: v for k, v in snapshot.items()
              if k != "messages" and (k in always or previous.get(k) != v)}
    if "visible_features" in result and isinstance(previous.get("visible_features"), list):
        old, new = previous["visible_features"], result["visible_features"]
        # Reverse-ordered splices use offsets into the last complete baseline.
        # All feature details remain intact, including routes and cloud cells.
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        edits = [{"start": a, "delete": b - a, "items": new[c:d]}
                 for op, a, b, c, d in difflib.SequenceMatcher(
                     a=[encode(f) for f in old], b=[encode(f) for f in new], autojunk=False).get_opcodes()
                 if op != "equal"]
        delta = {"splices": list(reversed(edits))}
        if len(encode(delta)) + len("visible_features_delta") < len(encode(new)) + len("visible_features"):
            del result["visible_features"]
            result["visible_features_delta"] = delta
    if "inventory" in result and "inventory" in previous:
        def index(items):
            if not isinstance(items, list):
                return None
            indexed = {}
            for item in items:
                if (not isinstance(item, dict) or type(item.get("slot")) is not int
                        or not isinstance(item.get("letter_namespace"), str)):
                    return None
                key = (item["letter_namespace"], item["slot"])
                if key in indexed:
                    return None
                indexed[key] = item
            return indexed

        def reference(key):
            return {"letter_namespace": key[0], "slot": key[1]}

        before, after = index(previous["inventory"]), index(result["inventory"])
        # Older/malformed identities cannot be patched safely: retain a full
        # replacement. Upserts replace entire entries, including absent fields.
        if before is not None and after is not None:
            delta = {"upsert": [item for key, item in after.items()
                                if before.get(key) != item],
                     "remove": [reference(key) for key in before if key not in after]}
            natural_order = ([key for key in before if key in after]
                             + [key for key in after if key not in before])
            if natural_order != list(after):
                delta["order"] = [reference(key) for key in after]
            result.pop("inventory")
            result["inventory_delta"] = delta
    result.update(message_changes(previous.get("messages", []),
                                  snapshot.get("messages", [])))
    if 'adapter_changes' in snapshot:
        changes = snapshot['adapter_changes']
        result['adapter_changes'] = (compact_report(changes)
                                     if previous.get('adapter_changes') == changes else changes)
    result["observation"] = "delta"
    return result


def observation_text(result):
    p = result["player"]
    lines = [f"{p.get('name', '?')} | {p.get('place', '?')}:"
             f"{p.get('depth', '?')} | HP {p.get('hp', '?')}/"
             f"{p.get('hp_max', '?')} | turn {p.get('turn', '?')}",
             f"Input: {result['input_mode']} | running: {result['running']} | "
             f"settled: {result['settled']} | {result['observation']} #{result['sequence']}"]
    lines.extend(f"{label}: {p[field]}%" for field, label in
                 (("doom", "Doom"), ("contam", "Contamination")) if field in p)
    if "map" in result:
        lines.extend(result["map"]["rows"])
    if result.get("messages_reset"):
        lines.append("Message history reset (the retained windows no longer overlap).")
    if result.get("messages_rollback"):
        lines.append(f"Replace the last {result['messages_rollback']} message(s):")
    lines.extend(msg["text"] for msg in result.get("messages", []))
    for key in ("combat", "recovery", "ranged", "cast", "hold", "spells", "checkpoint", "startup", "level_map", "map_input", "terrain_neighborhood", "player_descriptions", "visible_features", "visible_features_delta", "monsters", "scenery", "unseen_threat", "targeting", "inventory", "inventory_delta", "ui", "text", "text_input", "cancel_available"):
        if key in result:
            lines.append(f"{key}: {json.dumps(result[key], ensure_ascii=False)}")
    if result.get("more_text"):
        lines.append(result["more_text"])
    if result.get("more_pending_reason"):
        lines.append(f"More acknowledgment stopped: {result['more_pending_reason']}")
    if result.get("settle_reason"):
        lines.append(f"Settling: {result['settle_reason']}")
    if result.get("timing"):
        lines.append(f"timing: {json.dumps(result['timing'], separators=(',', ':'))}")
    if result.get('adapter_changes'):
        changes = result['adapter_changes']
        advice = {True: 'yes', False: 'no', None: 'unknown'}[changes['restart_recommended']]
        lines.append(f"Adapter restart recommended: {advice}. {changes['reason']}")
        for entry in changes.get('changes', []) + changes.get('available_changes', []):
            lines.append(f"  {entry['id']}: {entry['title']} ({entry['daemon_restart']})")
    return "\n".join(lines)


def inventory_table(inventory):
    """Lossless full inventory with shared field names for large snapshots."""
    columns = sorted(set.intersection(*(set(item) for item in inventory))) if inventory else []
    table = {"columns": columns, "rows": [[item[key] for key in columns] for item in inventory]}
    extra = {str(index): {key: value for key, value in item.items() if key not in columns}
             for index, item in enumerate(inventory) if set(item) - set(columns)}
    if extra:
        table["extra"] = extra
    return table


def emit_observation(session, snapshot, full=False, text=False, stream="default",
                     snapshot_tags=False, session_name="default"):
    """Called under the output lock, after obtaining the current snapshot.

    Keeping presentation in the short-lived CLI also upgrades live daemons.
    The cache is a cursor, not game state; --full recovers lost output.
    """
    if "player" not in snapshot or "error" in snapshot:
        print(json.dumps(snapshot, ensure_ascii=False))
        return
    cache = session / f"observation-{stream}.json"
    runtime = json.loads((session / "runtime.json").read_text())
    try:
        saved = json.loads(cache.read_text())
        if saved.get("runtime") != runtime:
            saved = {}
        if saved.get("snapshot", {}).get("game_generation") != snapshot.get("game_generation"):
            saved = {}
    except (OSError, ValueError):
        saved = {}
    current_changes = adapter_changelog(ROOT)
    snapshot = {**snapshot, 'adapter_changes': adapter_change_report(session, runtime, current_changes)}
    previous = saved.get("snapshot")
    result = compact_observation(snapshot, previous, full)
    if saved.get("description_format") != 1:
        result["player_descriptions"] = compact_observation(snapshot, full=True)["player_descriptions"]
    result["sequence"] = saved.get("sequence", 0) + 1
    if result["observation"] == "delta":
        result["base_sequence"] = saved["sequence"]
    tagged = ""
    key = "crawl/map"
    identity = hashlib.sha256(
        (str(session.resolve()) + "\0" + session_name).encode()).hexdigest()
    inventory_key = "crawl/inventory"
    # Introduce the new schema and changes of reader format with a complete
    # inventory, without consuming/replaying another reader's message history.
    if (saved.get("inventory_format") != 2
            or (snapshot_tags and (saved.get("inventory_snapshot_key") != inventory_key
                                   or saved.get("snapshot_identity") != identity))
            or (not snapshot_tags and saved.get("inventory_snapshot_key"))):
        if "inventory" in snapshot:
            result["inventory"] = snapshot["inventory"]
            result.pop("inventory_delta", None)
    if snapshot_tags:
        player = snapshot["player"]
        interpretation = {field: player.get(field)
                          for field in ("name", "place", "depth")}
        map_state = {**interpretation, "map": snapshot.get("map")}
        old_player = (previous or {}).get("player", {})
        old_map_state = {field: old_player.get(field)
                         for field in ("name", "place", "depth")}
        old_map_state["map"] = (previous or {}).get("map")
        if (result["observation"] == "full" or saved.get("snapshot_identity") != identity
                or saved.get("snapshot_key") != key
                or map_state != old_map_state):
            payload = {**map_state, "session": identity,
                       "sequence": result["sequence"], "turn": player.get("turn")}
            tagged = (f'<codex_snapshot key="{key}">\n'
                      + json.dumps(payload, ensure_ascii=False, indent=2)
                      + "\n</codex_snapshot>")
            if len(tagged.encode()) > 8192:
                # Preserve the actual map as ordinary output, and explicitly clear
                # any older replaceable terrain rather than silently retaining it.
                result["map"] = snapshot.get("map")
                payload.update(map=None, reason="map exceeds snapshot size limit")
                tagged = (f'<codex_snapshot key="{key}">\n'
                          + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                          + "\n</codex_snapshot>")
            else:
                result.pop("map", None)
        else:
            result.pop("map", None)
    elif saved.get("snapshot_key"):
        # A plain-JSON reader cannot recover the prior tagged map. Re-emit it
        # when switching formats without replaying messages or other fields.
        result["map"] = snapshot.get("map")
    if snapshot_tags and "inventory" in result:
        payload = {"inventory": result["inventory"], "session": identity,
                   "sequence": result["sequence"], "turn": snapshot["player"].get("turn"),
                   "name": snapshot["player"].get("name")}

        def inventory_block():
            return (f'<codex_snapshot key="{inventory_key}">\n'
                    + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                    + "\n</codex_snapshot>")

        block = inventory_block()
        if len(block.encode()) > 8192:
            payload["inventory_table"] = inventory_table(payload.pop("inventory"))
            block = inventory_block()
        if len(block.encode()) > 8192:
            payload.pop("inventory_table", None)
            payload.update(inventory=None, reason="inventory exceeds snapshot size limit")
            block = inventory_block()
        else:
            result.pop("inventory")
        tagged = "\n".join(part for part in (tagged, block) if part)
    temporary = cache.with_suffix(".tmp")
    temporary.write_text(json.dumps({"runtime": runtime,
        "sequence": result["sequence"],
        "snapshot_identity": identity if snapshot_tags else None,
        "snapshot_key": key if snapshot_tags else None,
        "inventory_format": 2,
        "description_format": 1,
        "inventory_snapshot_key": inventory_key if snapshot_tags else None,
        "snapshot": {k: v for k, v in snapshot.items() if k != "summary"}}))
    temporary.replace(cache)
    print(observation_text(result) if text else
          json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    if tagged:
        print(tagged, flush=True)
