"""Bounded adjacent melee using only public observations, never autofight."""

import copy
import math
import re
import time

from .hazards import local_hazard, validate as validate_hazards, HazardWatch
from .safety import RiskWatch, risk_increase


KEYS = {(-1, -1): "y", (0, -1): "k", (1, -1): "u", (-1, 0): "h",
        (1, 0): "l", (-1, 1): "b", (0, 1): "j", (1, 1): "n"}
CHANGE_TEXT = re.compile(r"\b(?:berserk|frenzied|speeds up|moving faster|turns invisible|"
                         r"flickers and vanishes|changes? (?:shape|into)|transforms?)\b", re.I)
RANGED = re.compile(r"\b(?:sling|shortbow|orcbow|longbow|arbalest|crossbow|hand cannon)\b", re.I)
ENEMY_STATUS_ALLOWANCES = {"drain": {"drain"},
                           "poison": {"poison", "more_poison", "max_poison"}}


def policy(options):
    allowed = {"max_actions", "max_seconds", "min_hp_percent", "max_threat", "allow_status", "allow_enemy_status", "assessed_distant_id", "allow_water_with_flight", "allow_cloud"}
    if not isinstance(options, dict) or set(options) - allowed:
        raise ValueError("Unknown combat policy option")
    result = {"max_actions": 8, "max_seconds": 10, "min_hp_percent": 85,
              "max_threat": 1, "allow_status": [], "allow_enemy_status": [], "assessed_distant_id": [], "allow_water_with_flight": False, "allow_cloud": []}
    result.update(options)
    validate_hazards(result)
    ids = result["assessed_distant_id"]
    if not isinstance(ids, list) or any(type(mid) is not int or mid <= 0 for mid in ids) or len(set(ids)) != len(ids):
        raise ValueError("assessed_distant_id must contain unique positive sighting IDs")
    for key, low, high in (("max_actions", 1, 32), ("max_threat", 0, 4)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}")
    for key, low, high in (("max_seconds", .1, 30), ("min_hp_percent", 1, 100)):
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} must be from {low} to {high}")
    statuses = result["allow_status"]
    if not isinstance(statuses, list) or any(not isinstance(s, str) or not s or len(s) > 80 for s in statuses):
        raise ValueError("allow_status must be a list of exact status labels")
    enemy_statuses = result["allow_enemy_status"]
    if not isinstance(enemy_statuses, list) or any(not isinstance(s, str) or s not in ENEMY_STATUS_ALLOWANCES for s in enemy_statuses):
        raise ValueError("allow_enemy_status accepts only drain and poison")
    result.update(movement="adjacent_melee_only", wait=False, approach=False,
                  new_enemies="stop", lost_enemies="stop", prompts="stop",
                  monster_changes="stop", unexpected_movement="stop", resource_use="stop")
    return result


def hostiles(obs):
    return [m for m in obs["monsters"] if m.get("att") == 0]


def signature(mon, allowed_icons=()):
    result = {k: mon.get(k) for k in ("name", "type", "att", "threat", "icons", "visible_weapons")}
    result["icons"] = sorted(set(mon.get("icons") or []) - set(allowed_icons))
    return result


def guard(obs, initial, rules, messages):
    p, old = obs["player"], initial["player"]
    if not obs["running"] or p.get("hp", 0) <= 0 or (obs.get("exit_reason") or {}).get("type", "unknown") != "unknown":
        return "game_exited"
    if not obs["settled"]:
        return "unsettled"
    if obs["input_mode"] != "command" or obs.get("ui") or obs.get("more"):
        return "input_required"
    if obs.get("unseen_threat"):
        return "unseen_threat"
    if risk_increase(p, old):
        return risk_increase(p, old)
    if any(p.get(k) is None for k in ("hp", "hp_max", "pos", "turn", "xl", "place", "depth")) or p["hp_max"] <= 0:
        return "missing_player_state"
    if any(p.get(k) != old.get(k) for k in ("place", "depth")):
        return "depth_changed"
    if p["xl"] != old.get("xl"):
        return "level_changed"
    if p["pos"] != old.get("pos"):
        return "unexpected_movement"
    if 100 * p["hp"] < rules["min_hp_percent"] * p["hp_max"]:
        return "low_hp"
    for status in p.get("status", []):
        label = status.get("light", status.get("text", "")) if isinstance(status, dict) else status
        if label not in rules["allow_status"]:
            return "player_status"
    if p.get("weapon_index") != old.get("weapon_index") or obs["inventory"] != initial["inventory"] or p.get("mp", 0) < old.get("mp", 0):
        return "resource_changed"
    wielded = next((i for i in obs["inventory"] if i["slot"] == p.get("weapon_index")), {})
    if wielded and (not wielded.get("name_current", False) or RANGED.search(wielded.get("name", ""))):
        return "weapon_requires_decision"
    if CHANGE_TEXT.search("\n".join(str(m.get("text", "")) for m in messages)):
        return "monster_change_message"
    monsters = obs["monsters"]
    if any(m.get("location_status") != "visible" for m in monsters):
        return "invisible_marker"
    if any(m.get("att") not in (0, 1, 2, 3, 4) for m in monsters):
        return "unknown_attitude"
    enemies = hostiles(obs)
    if any(not m.get("id") or m.get("threat") is None for m in enemies):
        return "missing_enemy_identity"
    if any(m["threat"] > rules["max_threat"] for m in enemies):
        return "enemy_threat"
    allowed_icons = set().union(*(ENEMY_STATUS_ALLOWANCES[s] for s in rules["allow_enemy_status"]))
    if any(set(m.get("icons") or []) - allowed_icons for m in enemies):
        return "enemy_status"
    originals = {m.get("id"): m for m in hostiles(initial)}
    current = {m["id"]: m for m in enemies}
    if current.keys() - originals.keys():
        return "new_enemy"
    if any(signature(m, allowed_icons) != signature(originals[mid], allowed_icons) for mid, m in current.items()):
        return "enemy_changed"
    if not enemies:
        return "no_visible_hostiles"
    if originals.keys() - current.keys():
        return "enemy_lost"
    assessed = set(rules["assessed_distant_id"])
    if any(mid not in originals or distance(originals[mid]) <= 1 for mid in assessed):
        return "invalid_distant_assessment"
    if any(distance(current[mid]) <= 1 for mid in assessed):
        return "assessed_foe_adjacent"
    if any(distance(m) != 1 for m in enemies):
        if any(w.get("attack_hint") in ("ranged", "reaching") for m in enemies for w in m.get("visible_weapons", [])):
            return "ranged_or_reaching_enemy"
        if any(distance(m) != 1 and m["id"] not in assessed for m in enemies):
            return "distant_enemy"
    if not any(distance(m) == 1 for m in enemies):
        return "no_adjacent_hostiles"
    # Even stationary melee is inappropriate while standing in a public hazard.
    if local_hazard(obs, rules):
        return "local_hazard"
    return None


def distance(monster):
    return max(abs(monster["dx"]), abs(monster["dy"]))


class CombatWatch(RiskWatch):
    def __init__(self, state, previous, assessed, rules):
        super().__init__(state, previous)
        self.assessed = set(assessed)
        self.hazards = HazardWatch(state, rules)

    def __call__(self, state, event):
        super().__call__(state, event)
        self.hazards(state, event)
        self.reason = self.reason or self.hazards.reason
        if not self.reason and self.assessed and event.get("msg") == "map":
            # Latch even an approach that retreats again in the same response.
            for monster in state.observation()["monsters"]:
                if monster.get("id") in self.assessed and distance(monster) <= 1:
                    self.reason = "assessed_foe_adjacent"
                    break


def run(game, options):
    rules = policy(options)
    started, timestamp = time.monotonic(), round(time.time(), 3)
    deadline = started + rules["max_seconds"]
    before = game.state.player.get("turn")
    steps, sent, reason, error = [], 0, None, None
    initial = None
    previous_observer = game.state.guard_observer
    watch = CombatWatch(game.state, previous_observer, rules["assessed_distant_id"], rules)
    game.state.guard_observer = watch
    try:
        # Preflight sends no input. Clear only the helper's message window;
        # retain any genuinely unresolved response from an earlier command.
        game.state.input_messages = []
        game.settle_input(deadline=deadline, context="combat")
        # Do not call Game.observe between steps: it trims the message window.
        def snapshot():
            return {**game.state.observation(), "running": game.process.poll() is None,
                    "settled": game.settled}
        initial = copy.deepcopy(snapshot())
        messages = copy.deepcopy(game.state.input_messages)
        while True:
            obs = snapshot()
            reason = watch.reason or guard(obs, initial, rules, messages)
            if reason:
                break
            if time.monotonic() >= deadline:
                reason = "time_limit"
                break
            if sent >= rules["max_actions"]:
                reason = "action_limit"
                break
            enemy = min((m for m in hostiles(obs) if distance(m) == 1),
                        key=lambda m: (m["dy"], m["dx"], m["id"]))
            key = KEYS[(enemy["dx"], enemy["dy"])]
            turn = game.state.player["turn"]
            game.state.begin_input()
            game.state.inspection = None
            game.send({"msg": "key", "keycode": ord(key)})
            sent += 1
            try:
                game.settle_input(deadline=deadline, require_event=True, context="combat")
            finally:
                step = {"event": "combat_step", "invocation": game.actions.count + 1,
                        "step": sent, "key": key, "enemy_id": enemy["id"],
                        "turn_before": turn, "turn": game.state.player.get("turn"),
                        "settled": game.settled}
                steps.append(step)
                game.actions.write(step)
            messages = copy.deepcopy(game.state.input_messages)
            # Guard the resulting state even on the last action. Never retry a
            # no-turn or unsettled result; the key may have reached the engine.
            reason = watch.reason or guard(snapshot(), initial, rules, messages)
            if reason:
                break
            if game.state.player.get("turn") == turn:
                reason = "no_turn_progress"
                break
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        error = str(exc)
        reason = "action_error"
    finally:
        game.state.guard_observer = previous_observer
        game.actions.record("combat", rules["max_actions"], sent, before,
                            game.state.player, started, timestamp, game.settled,
                            game.process.poll() is None, error)
    result = game.observe()
    result["keys_sent"] = sent
    result["combat"] = {"policy": rules, "stop_reason": reason, "actions": sent,
                        "turns": max(0, game.state.player.get("turn", before or 0) - (before or 0)),
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                        "steps": steps}
    if error:
        result["combat"]["error"] = error
    return result
