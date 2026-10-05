"""Controller-requested native save/reload; never copy or edit a Crawl save."""

import copy
import json
import re
import time


PLAYER_FIELDS = ("name", "species", "god", "place", "depth", "turn", "xl", "progress",
                 "hp", "hp_max", "real_hp_max", "mp", "mp_max", "gold", "piety_rank",
                 "ac", "ev", "sh", "str", "int", "dex", "penance", "form",
                 "doom", "contam", "weapon_index", "offhand_index", "quiver_item", "quiver_desc", "status")
REQUIRED = ("name", "place", "depth", "turn", "xl", "hp", "hp_max", "mp", "mp_max")


def matching_weapon_details(left, right):
    # Limit reconciliation to the recorded artefact-label shape. Enchantment
    # and the complete property/inscription suffix must remain byte-for-byte equal.
    pattern = r"^([+-]\d+) .+ (\{[^{}]*\})$"
    a, b = re.fullmatch(pattern, left or ""), re.fullmatch(pattern, right or "")
    return bool(a and b and a.groups() == b.groups())


def signature(observation):
    player = observation["player"]
    return {"player": {key: copy.deepcopy(player[key]) for key in PLAYER_FIELDS if key in player},
            "inventory": copy.deepcopy(observation["inventory"])}


def differences(before, after, before_titles=None, after_titles=None):
    changes = []
    for key in sorted(before["player"].keys() | after["player"].keys()):
        if before["player"].get(key) != after["player"].get(key):
            changes.append({"field": "player." + key, "before": before["player"].get(key),
                            "after": after["player"].get(key)})
    old, new = before["inventory"], after["inventory"]
    def key(item):
        return item.get("letter_namespace"), item.get("slot")
    old_items, new_items = {key(i): i for i in old}, {key(i): i for i in new}
    if len(old_items) != len(old) or len(new_items) != len(new):
        changes.append({"field": "inventory.identity", "reason": "duplicate inventory identities"})
        return changes
    identities = list(dict.fromkeys([key(i) for i in old + new]))
    for identity in identities:
        ref = {"letter_namespace": identity[0], "slot": identity[1]}
        if identity not in old_items or identity not in new_items:
            changes.append({"field": "inventory.item", **ref,
                            "before": old_items.get(identity), "after": new_items.get(identity)})
            continue
        left, right = old_items[identity], new_items[identity]
        for field in sorted(left.keys() | right.keys()):
            if field not in left or field not in right or left[field] != right[field]:
                # Only an independently read exact native description can
                # reconcile a cropped weapon label. Never strip arbitrary names.
                slot = str(identity[1])
                if (field == "name" and identity[0] == "equipment"
                        and left.get("category") == right.get("category") == "weapons"
                        and matching_weapon_details(left.get("name"), right.get("name"))
                        and before_titles and after_titles
                        and before_titles.get(slot) and before_titles[slot] == after_titles.get(slot)):
                    continue
                change = {"field": "inventory." + field, **ref,
                          "before": left.get(field), "after": right.get(field)}
                if field not in left or field not in right:
                    change.update(before_present=field in left, after_present=field in right)
                changes.append(change)
    if old_items.keys() == new_items.keys() and [key(i) for i in old] != [key(i) for i in new]:
        changes.append({"field": "inventory.order",
                        "before": [{"letter_namespace": key(i)[0], "slot": key(i)[1]} for i in old],
                        "after": [{"letter_namespace": key(i)[0], "slot": key(i)[1]} for i in new]})
    if before_titles is not None and after_titles is not None:
        for slot in sorted(before_titles.keys() | after_titles.keys()):
            if before_titles.get(slot) != after_titles.get(slot):
                changes.append({"field": "inventory.description_title", "letter_namespace": "equipment", "slot": int(slot),
                                "before": before_titles.get(slot), "after": after_titles.get(slot)})
    return changes


def write_record(session, record):
    path = session / "checkpoint.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    temporary.replace(path)


def public_record(record):
    result = {key: value for key, value in record.items() if key not in ("before", "after")}
    for when in ("before", "after"):
        if when in record:
            state = record[when]
            result[when] = {"player": state["player"], "inventory_count": len(state["inventory"])}
    return result


def run(game, runtime, config, factory):
    """Return (current game or None, response). None requires server shutdown.

    The Python daemon and its changelog baseline stay alive; only its native
    child is replaced. A failed save is never repeated. A failed reload never
    triggers another launch. The journal survives loss of the requesting CLI.
    """
    record = {"verified": False, "phase": "preflight", "started_at": time.time(),
              "adapter_reloaded": False, "save_submitted": False}
    session = game.session
    try:
        game.settle_input()
        before = game.observe()
        if (not before["running"] or not before["settled"] or before.get("startup")
                or before["input_mode"] != "command" or before.get("ui") or before.get("more")
                or (before.get("exit_reason") or {}).get("type", "unknown") != "unknown"):
            raise RuntimeError("Checkpoint requires a fully initialized, settled command prompt")
        record["phase"] = "refreshing_before_save"
        before = game.refresh_observation()
        if (not before["running"] or not before["settled"] or before.get("startup")
                or before["input_mode"] != "command" or before.get("ui") or before.get("more")
                or (before.get("exit_reason") or {}).get("type", "unknown") != "unknown"):
            raise RuntimeError("Refreshed checkpoint state is not a complete command prompt")
        if (any(before["player"].get(key) is None for key in REQUIRED)
                or before["player"]["name"] != config["name"]
                or any(not item.get("name_current") for item in before["inventory"])):
            raise RuntimeError("Checkpoint requires complete player/inventory state and the configured character name")
        record["phase"] = "describing_before_save"
        record["before_weapon_titles"] = game.checkpoint_weapon_titles(before)
        record.update(before=signature(before), inventory_comparison="fresh_public_and_exact_weapon_descriptions", phase="saving", save_submitted=True)
        # Record intent before sending input, so a lost reply cannot be read as
        # evidence that it is safe to repeat the operation.
        write_record(session, record)
        game.act([19], "checkpoint_save")
        deadline = time.monotonic() + 5
        while game.process.poll() is None and time.monotonic() < deadline:
            game.drain()
            time.sleep(.02)
        game.drain()
        if game.process.poll() is None or (game.state.exit_reason or {}).get("type") != "saved":
            raise RuntimeError("Native save/exit was not confirmed; inspect before any further action")
        if game.process.returncode != 0:
            raise RuntimeError("Native save exited unsuccessfully; no reload attempted")
        save = session / "saves" / (config["name"] + ".cs")
        if not save.is_file() or not save.stat().st_size:
            raise RuntimeError("Native save file is missing or empty; no reload attempted")
        record.update(phase="reloading", native_save_confirmed=True,
                      save_file=str(save), save_bytes=save.stat().st_size)
        write_record(session, record)
        old_game, game = game, None
        old_game.close()
        # Both endpoints belong to the exited native child/closed adapter socket.
        (runtime / "adapter.sock").unlink(missing_ok=True)
        (runtime / (config["name"] + ":agent.sock")).unlink(missing_ok=True)
        game = factory(session, runtime, config)
        game.attach()
        after = game.observe()
        record["phase"] = "verifying"
        if (not after["running"] or not after["settled"] or after.get("startup")
                or after["input_mode"] != "command" or after.get("ui") or after.get("more")):
            raise RuntimeError("Reload did not reach a complete command prompt; checkpoint is unverified")
        record["phase"] = "refreshing_after_reload"
        after = game.refresh_observation()
        if (not after["running"] or not after["settled"] or after.get("startup")
                or after["input_mode"] != "command" or after.get("ui") or after.get("more")
                or (after.get("exit_reason") or {}).get("type", "unknown") != "unknown"
                or any(not item.get("name_current") for item in after["inventory"])):
            raise RuntimeError("Refreshed reload state is incomplete; checkpoint is unverified")
        record["phase"] = "verifying"
        record["after_weapon_titles"] = game.checkpoint_weapon_titles(after)
        record.update(after=signature(after), differences=differences(record["before"], signature(after),
            record["before_weapon_titles"], record["after_weapon_titles"]))
        record["display_name_changes"] = [d for d in differences(record["before"], record["after"])
                                           if d not in record["differences"]]
        if record["differences"]:
            raise RuntimeError("Reloaded public state differs from the pre-save checkpoint; inspect discrepancies")
        record.update(verified=True, phase="complete")
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        record.update(error=str(exc), failed_phase=record["phase"], phase="failed")
    record["finished_at"] = time.time()
    try:
        write_record(session, record)
    except OSError as exc:
        record.update(verified=False, journal_error=str(exc))
    if game is None:
        return None, {"error": "Checkpoint reload failed; session stopped", "checkpoint": public_record(record)}
    game.checkpoint_result = public_record(record)
    game.actions.write({"event": "checkpoint", **{key: value for key, value in record.items()
                                                  if key not in ("before", "after", "differences")}})
    result = game.observe()
    result["checkpoint"] = game.checkpoint_result
    return game, result
