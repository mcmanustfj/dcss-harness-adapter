"""One caller-selected ranged attack, driven exclusively by public UI evidence."""

import copy
import math
import re
import time

from .recovery import public_text


def policy(options):
    rules = dict(wand_letter=None, weapon_letter=None, current_quiver=None,
                 expect_name=None, monster_id=None, prepare_only=False,
                 allow_area=False, allow_self=False, allow_friendly=False,
                 max_seconds=10)
    if not isinstance(options, dict) or set(options) - rules.keys():
        raise ValueError("Unknown ranged policy option")
    rules.update(options)
    if bool(rules["wand_letter"]) == bool(rules["current_quiver"]):
        raise ValueError("Choose wand_letter or current_quiver")
    if bool(rules["weapon_letter"]) != bool(rules["current_quiver"]):
        raise ValueError("current_quiver requires weapon_letter; wands cannot select a weapon")
    for key in ("wand_letter", "weapon_letter"):
        if rules[key] is not None and (not isinstance(rules[key], str)
                                      or not re.fullmatch("[a-zA-Z]", rules[key])):
            raise ValueError(f"{key} must be one inventory letter")
    for key in ("expect_name", "current_quiver"):
        value = rules[key]
        if key == "current_quiver" and value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError(f"{key} requires the exact current public text")
    if type(rules["monster_id"]) is not int or rules["monster_id"] <= 0:
        raise ValueError("monster_id must be a positive visible sighting ID")
    for key in ("prepare_only", "allow_area", "allow_self", "allow_friendly"):
        if type(rules[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    seconds = rules["max_seconds"]
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not .1 <= seconds <= 30:
        raise ValueError("max_seconds must be from 0.1 to 30")
    return rules


class Stop(Exception):
    pass


def resource(obs, state, rules):
    wand = bool(rules["wand_letter"])
    matches = [item for item in obs["inventory"]
               if item.get("letter") == (rules["wand_letter"] or rules["weapon_letter"])
               and item["letter_namespace"] == ("wands" if wand else "equipment")
               and item["category"] == ("wands" if wand else "weapons")]
    if len(matches) != 1 or not matches[0].get("name_current"):
        raise Stop("resource_unavailable")
    item = matches[0]
    if item.get("name") != rules["expect_name"]:
        raise Stop("resource_identity_mismatch")
    raw = state.player["inv"][str(item["slot"])]
    if wand:
        if type(raw.get("plus")) is not int or raw["plus"] <= 0:
            raise Stop("charges_unknown_or_empty")
    elif (obs["player"].get("weapon_index") != item["slot"]
          or public_text(raw.get("action_verb", "")).lower() != "fire"
          or obs["player"].get("quiver_desc") != rules["current_quiver"]
          or not rules["current_quiver"].startswith(f"Fire: {item['letter']}) ")
          or state.player.get("quiver_item") != item["slot"]
          or state.player.get("quiver_available") != 1):
        # This initial interface supports the wielded, ammunition-free launcher
        # action. Throwing, spells, and an arbitrary quivered wand are distinct.
        raise Stop("launcher_or_quiver_mismatch")
    return copy.deepcopy(item), copy.deepcopy(raw)


def monster(obs, monster_id):
    matches = [m for m in obs["monsters"] if m.get("id") == monster_id
               and m.get("location_status") == "visible"]
    if len(matches) != 1:
        raise Stop("target_lost")
    if matches[0].get("att") != 0:
        raise Stop("target_not_hostile")
    return matches[0]


def wand_menu(obs, item):
    if len(obs["ui"]) != 1:
        return False
    menu = obs["ui"][0]
    if menu.get("tag") != "use_item" or public_text(menu.get("title", {}).get("text", "")) != "Evoke which item?":
        return False
    category, matches = None, []
    for row in menu.get("items", []):
        if row.get("level") == 1:
            category = public_text(row.get("text", ""))
        if ord(item["letter"]) in row.get("hotkeys", []):
            text = public_text(row.get("text", ""))
            text = re.sub(r"^[a-zA-Z] - (?:an? )?", "", text)
            text = re.sub(r" \(quivered\)$", "", text)
            matches.append(category == "Wands" and text == item["name"])
    return matches == [True]


def check_aim(obs, target):
    aim = obs.get("targeting")
    if not aim:
        raise Stop("unexpected_prompt")
    cursor, selected = aim.get("cursor") or {}, aim.get("selected_monster") or {}
    if (cursor.get("x"), cursor.get("y")) != (target["x"], target["y"]) or selected.get("id") != target["id"]:
        raise Stop("target_mismatch")
    if aim["range"] != "unknown":
        raise Stop("out_of_range_or_invalid")
    if aim["line_of_fire"] == "blocked":
        raise Stop("line_of_fire_blocked")
    if any("cannot control your aim" in line.lower() for line in aim["feedback"]):
        raise Stop("uncontrolled_aim")
    # A fresh accepted native mouse aim enforces valid_aim/range. A positive
    # public ray on the chosen monster is additional path/affected evidence;
    # absent, grey-only, or contradictory previews are never assumed clear.
    preview = aim["preview_cells"] + aim["landing_cells"]
    on_target = [p["kind"] for p in preview if (p["x"], p["y"]) == (target["x"], target["y"])]
    if (not set(on_target) & {"affected_or_path", "multiple"}
            or "possible_blocked_or_out_of_range" in on_target):
        raise Stop("target_not_in_positive_preview")
    cells = {(p["x"], p["y"]) for p in preview}
    pos = obs["player"]["pos"]
    exposure = {"self": (pos["x"], pos["y"]) in cells,
                "friendly_or_neutral": [], "other_hostiles": [],
                "intervening_occupants": [],
                "unseen_cells": sum(not p["visible"] for p in preview)}
    distance = max(abs(target["x"] - pos["x"]), abs(target["y"] - pos["y"]))
    for m in obs["monsters"] + obs.get("scenery", []):
        if (m["x"], m["y"]) in cells and m.get("id") != target["id"]:
            if m.get("location_status") != "visible":
                raise Stop("unseen_preview")
            # Positive target overlays can extend past an occupied square:
            # a projectile may hit a plant instead. The public preview mixes
            # paths and areas, so conservatively stop for any nearer occupant,
            # even when collateral exposure was authorized. Never infer pierce.
            if max(abs(m["x"] - pos["x"]), abs(m["y"] - pos["y"])) < distance:
                exposure["intervening_occupants"].append(m.get("id"))
            exposure["other_hostiles" if m.get("att") == 0 else "friendly_or_neutral"].append(m.get("id"))
    return exposure


def run(game, options):
    rules = policy(options)
    started, timestamp = time.monotonic(), round(time.time(), 3)
    deadline = started + rules["max_seconds"]
    before = game.state.player.get("turn")
    info = {"policy": rules, "phase": "initial", "opened_menu": False,
            "selected_item": False, "aimed": False, "submitted": False,
            "resolved_target": None, "inputs": [], "resource_consumed": None}
    item = raw = initial = None
    changed = False
    previous_observer = game.state.guard_observer

    def snapshot():
        return game.state.observation()  # Never trim intermediate messages.

    def watch(state, event):
        nonlocal changed
        if previous_observer:
            previous_observer(state, event)
        if initial and any(state.player.get(k) != initial.get(k) for k in
                           ("turn", "time", "hp", "mp", "status", "doom", "contam", "pos", "place", "depth", "weapon_index")):
            changed = True

    def ready():
        if game.process.poll() is not None or game.state.player.get("hp", 0) <= 0:
            raise Stop("game_exited")
        if (type(game.state.player.get("turn")) is not int
                or not game.state.player.get("pos")):
            raise Stop("missing_player_state")
        if not game.settled:
            raise Stop("unsettled")
        if time.monotonic() >= deadline:
            raise Stop("time_limit")
        if changed:
            raise Stop("player_changed_during_preparation")
        if game.state.more or game.state.unseen_threat:
            raise Stop("input_or_unseen_threat")
        obs = snapshot()
        if item:
            current, current_raw = resource(obs, game.state, rules)
            if current != item or current_raw != raw:
                raise Stop("resource_changed")
        monster(obs, rules["monster_id"])
        return obs

    def send(message):
        if time.monotonic() >= deadline:
            raise Stop("time_limit")
        game.state.begin_input(message)
        game.state.inspection = None
        # Mark attempted submission before I/O: transport failure is ambiguous.
        if message == {"msg": "key", "keycode": 13}:
            info.update(phase="submitted", submitted=True)
        info["inputs"].append(message)
        game.settled = False
        game.send(message)
        game.settle_input(deadline=deadline, require_event=True, context="ranged")
        game.actions.write({"event": "ranged_step", "invocation": game.actions.count + 1,
                            "phase": info["phase"], "input": message,
                            "turn": game.state.player.get("turn"), "settled": game.settled})

    def key(char):
        send({"msg": "key", "keycode": ord(char)})

    def move(x, y):
        send({"msg": "target_cursor", "x": x, "y": y})

    try:
        game.settle_input(deadline=deadline, context="ranged")
        obs = ready()
        if obs["input_mode"] != "command" or obs["ui"]:
            raise Stop("command_prompt_required")
        item, raw = resource(obs, game.state, rules)
        initial = copy.deepcopy(game.state.player)
        game.state.guard_observer = watch
        if rules["wand_letter"]:
            info["phase"] = "opening_menu"
            key("V")
            obs = ready()
            if not wand_menu(obs, item):
                raise Stop("item_menu_mismatch")
            info.update(phase="opened_menu", opened_menu=True)
            key(item["letter"])
        else:
            info["phase"] = "opening_targeting"
            key("f")
        obs = ready()
        if not obs.get("targeting"):
            raise Stop("unexpected_prompt")
        if rules["current_quiver"] and rules["current_quiver"] not in obs["targeting"]["feedback"]:
            raise Stop("targeting_resource_mismatch")
        info.update(phase="selected_item", selected_item=True)
        target = monster(obs, rules["monster_id"])
        pos = obs["player"]["pos"]
        info["resolved_target"] = {k: target[k] for k in ("id", "name", "x", "y")}
        if [target["x"] - pos["x"], target["y"] - pos["y"]] in obs["targeting"]["invalid_aim_cells"]:
            # Native mouse targeting silently ignores invalid squares. Do not
            # create an unanswered cursor request when public UI already says no.
            raise Stop("out_of_range_or_invalid")
        # Force an observable cursor transition even when the native default
        # already chose this monster. Recentring never submits an attack.
        cursor = obs["targeting"].get("cursor") or {}
        if (cursor.get("x"), cursor.get("y")) == (target["x"], target["y"]):
            pos = obs["player"]["pos"]
            move(pos["x"], pos["y"])
            obs = ready()
            cursor = (obs.get("targeting") or {}).get("cursor") or {}
            if (cursor.get("x"), cursor.get("y")) != (pos["x"], pos["y"]):
                raise Stop("recenter_rejected")
        target = monster(obs, rules["monster_id"])
        point = (target["x"], target["y"])
        info["resolved_target"] = {k: target[k] for k in ("id", "name", "x", "y")}
        move(*point)
        obs = ready()
        target = monster(obs, rules["monster_id"])
        if (target["x"], target["y"]) != point:
            raise Stop("target_moved")
        info["exposure"] = check_aim(obs, target)
        info.update(phase="aimed", aimed=True,
                    aim_evidence="fresh_native_cursor_and_positive_target_preview")
        if info["exposure"]["intervening_occupants"]:
            raise Stop("possible_interception")
        for field, allowed, reason in (("self", "allow_self", "self_exposure"),
                                      ("friendly_or_neutral", "allow_friendly", "friendly_exposure"),
                                      ("other_hostiles", "allow_area", "area_exposure")):
            if info["exposure"][field] and not rules[allowed]:
                raise Stop(reason)
        if info["exposure"]["unseen_cells"]:
            raise Stop("unseen_preview")
        if rules["prepare_only"]:
            raise Stop("prepared")
        # No intervening UI input or automatic confirmation. This is the only
        # submission site; even a timeout/confirmation returns without retry.
        ready()
        game.state.guard_observer = previous_observer
        key("\r")
        if not game.settled:
            raise Stop("submission_uncertain")
        result = snapshot()
        if result["input_mode"] != "command" or result["ui"] or result["more"]:
            raise Stop("input_required_after_submission")
        raise Stop("shot_resolved" if game.state.player.get("turn", before) > before else "submission_unconfirmed")
    except Stop as exc:
        info["stop_reason"] = str(exc)
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        info.update(stop_reason="submission_uncertain" if info["submitted"] else "action_error", error=str(exc))
    finally:
        game.state.guard_observer = previous_observer
        sent = sum(m["msg"] == "key" for m in info["inputs"])
        game.actions.record("ranged", sent, sent, before, game.state.player, started,
                            timestamp, game.settled, game.process.poll() is None, info.get("error"))
    if raw and rules["wand_letter"]:
        current = game.state.player.get("inv", {}).get(str(item["slot"]), {})
        # Only report observed charge deltas, never infer success from Enter.
        if info["submitted"] and raw["plus"] == 1 and current.get("quantity") == 0:
            info.update(charges_used=1, resource_consumed=True)
        elif (current.get("base_type"), current.get("sub_type"), current.get("letter")) == (
                raw.get("base_type"), raw.get("sub_type"), raw.get("letter")) and type(current.get("plus")) is int:
            info["charges_used"] = max(0, raw["plus"] - current["plus"])
            if info["charges_used"] or game.settled:
                info["resource_consumed"] = bool(info["charges_used"])
    elif raw:
        info.update(resource_consumed=False, consumption_kind="ammunition_free_launcher")
    result = game.observe()
    info.update(keys_sent=sent, turns=max(0, game.state.player.get("turn", before or 0) - (before or 0)),
                elapsed_ms=round((time.monotonic() - started) * 1000))
    result.update(ranged=info, keys_sent=sent)
    return result
