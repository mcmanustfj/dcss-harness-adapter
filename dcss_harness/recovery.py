"""Native recovery and assessed-foe waiting; public observations only."""

import copy
import html
import re
import time

from .combat import hostiles, policy as combat_policy, signature
from .hazards import local_hazard, validate as validate_hazards, HazardWatch
from .safety import monster_appearance, scenery, risk_increase, recovery_friendly


ATTACK = re.compile(r"\b(?:hits?|bites?|claws?|stings?|shoots?|attacks?|casts?|misses?|"
                    r"barely misses|closely misses|engulfs?|constricts?|burns?)\b.*\byou\b|"
                    r"\byou (?:are hit|feel sick|take damage|are engulfed)\b|"
                    r"\b(?:comes? into view|encounter|sense a monster|too close for your liking)\b", re.I)


def public_text(value):
    """Match the plain text presented in observations, not WebTiles colours."""
    return html.unescape(re.sub(r"</?[a-zA-Z][^>]*>", "", str(value))).strip()


def labels(player):
    # Use the short light label when available; text-only effects (such as
    # strong-willed) must still have an explicit, observable selector.
    return {public_text(s.get("light") or s.get("text") or "") if isinstance(s, dict)
            else public_text(s) for s in player.get("status", [])}


def remaining_statuses(player, rules):
    current = labels(player)
    return current if rules["clear_statuses"] else current & set(rules["clear_status"])


def policy(operation, options):
    if operation not in ("recover", "wait-for"):
        raise ValueError("Unknown recovery operation")
    defaults = {"max_actions": 6, "max_seconds": 10, "allow_status": [], "allow_water_with_flight": False, "allow_cloud": []}
    defaults.update({"clear_statuses": False, "clear_status": []} if operation == "recover" else
                    {"monster_id": None, "distance": 2, "min_hp_percent": 85, "max_threat": 1, "assessed_ranged": False})
    if not isinstance(options, dict) or set(options) - defaults.keys():
        raise ValueError("Unknown recovery policy option")
    defaults.update(options)
    validate_hazards(defaults)
    # Share the combat command's hard bounds and numeric/status validation.
    combat_policy({k: v for k, v in defaults.items() if k in
                   ("max_actions", "max_seconds", "min_hp_percent", "max_threat", "allow_status")})
    if operation == "recover":
        if type(defaults["clear_statuses"]) is not bool:
            raise ValueError("clear_statuses must be boolean")
        targets = defaults["clear_status"]
        if not isinstance(targets, list) or any(not isinstance(s, str) or not s for s in targets):
            raise ValueError("clear_status must be a list of exact public status labels")
        if defaults["clear_statuses"] and targets:
            raise ValueError("Use clear_status or clear_statuses, not both")
        if set(targets) - set(defaults["allow_status"]):
            raise ValueError("Each clear_status target must also be explicitly included in allow_status")
        defaults["target"] = ("full_hp_mp_and_no_statuses" if defaults["clear_statuses"] else
                              "full_hp_mp_and_selected_statuses_cleared" if targets else "full_hp_mp")
        defaults["input"] = "native_rest"
    else:
        if type(defaults["assessed_ranged"]) is not bool:
            raise ValueError("assessed_ranged must be boolean")
        if type(defaults["monster_id"]) is not int or defaults["monster_id"] <= 0:
            raise ValueError("monster_id must be a positive visible sighting ID")
        if type(defaults["distance"]) is not int or not 1 <= defaults["distance"] <= 8:
            raise ValueError("distance must be from 1 to 8")
        defaults["target"] = "chosen_foe_within_distance"
        defaults["input"] = "single_wait"
    return defaults


class Watch:
    """Latch public changes even if later frames restore HP/status/visibility.

    Native rest retains its own interrupts. This watcher prevents another rest
    after an observed danger; it never injects a key during native execution.
    """
    def __init__(self, state, operation, rules):
        self.operation, self.rules = operation, rules
        self.hazards = HazardWatch(state, rules)
        self.previous = copy.deepcopy(state.player)
        self.known_ids = {c["mon"].get("id") for c in state.cells.values()
                          if state.visible(c) and c.get("mon") and not scenery(c)}
        self.friends = {c["mon"].get("id"): signature({**c["mon"], **monster_appearance(c)})
                        for c in state.cells.values() if state.visible(c) and c.get("mon")
                        and recovery_friendly({**c["mon"], **monster_appearance(c)})}
        self.reason = None

    def __call__(self, state, event):
        if self.reason:
            return
        self.hazards(state, event)
        if self.hazards.reason:
            self.reason = self.hazards.reason
            return
        p, old = state.player, self.previous
        self.reason = risk_increase(p, old)
        if self.reason:
            return
        for key, reason in (("pos", "unexpected_movement"), ("place", "depth_changed"),
                            ("depth", "depth_changed"), ("xl", "level_changed"),
                            ("hp_max", "resource_max_changed"), ("mp_max", "resource_max_changed")):
            if key in old and p.get(key) != old[key]:
                self.reason = reason
        if p.get("hp", 0) < old.get("hp", 0):
            self.reason = "damage"
        if p.get("mp", 0) < old.get("mp", 0):
            self.reason = "resource_spent"
        if self.operation == "recover":
            if labels(p) - set(self.rules["allow_status"]):
                self.reason = "unexpected_status"
            if event.get("msg") == "map":
                for cell in state.cells.values():
                    if state.visible(cell) and cell.get("mon") and not scenery(cell):
                        if not recovery_friendly({**cell["mon"], **monster_appearance(cell)}):
                            self.reason = "visible_threat"
                            break
        else:
            if not self.reason and any(p.get(k) != old.get(k) for k in ("mp", "status")):
                self.reason = "player_changed"
            if event.get("msg") == "map":
                visible = [c for c in state.cells.values() if state.visible(c) and c.get("mon") and not scenery(c)]
                if any(c["mon"].get("id") not in self.known_ids for c in visible):
                    self.reason = "new_monster"
                if any(monster_appearance(c)["location_status"] != "visible" for c in visible):
                    self.reason = "invisible_marker"
                current = {c["mon"].get("id"): {**c["mon"], **monster_appearance(c)} for c in visible}
                if self.friends.keys() - current.keys():
                    self.reason = "monster_lost"
                elif any(signature(current[mid]) != sig for mid, sig in self.friends.items()):
                    self.reason = "monster_changed"
        if event.get("msg") == "msgs" and event.get("messages"):
            if self.operation == "wait-for":
                self.reason = "message"
            elif any(ATTACK.search(public_text(m.get("text", ""))) for m in event["messages"]):
                self.reason = "threat_message"
        self.previous = copy.deepcopy(p)


def guard(obs, initial, operation, rules, messages, watch):
    p, old = obs["player"], initial["player"]
    if not obs["running"] or p.get("hp", 0) <= 0 or (obs.get("exit_reason") or {}).get("type", "unknown") != "unknown":
        return "game_exited"
    if not obs["settled"]:
        return "unsettled"
    if obs["input_mode"] != "command" or obs.get("ui") or obs.get("more"):
        return "input_required"
    if obs.get("unseen_threat"):
        return "unseen_threat"
    if watch.reason:
        return watch.reason
    if risk_increase(p, old):
        return risk_increase(p, old)
    if any(p.get(k) is None for k in ("hp", "hp_max", "mp", "mp_max", "pos", "turn", "xl", "place", "depth", "status")) or p["hp_max"] <= 0:
        return "missing_player_state"
    for keys, reason in ((("place", "depth"), "depth_changed"), (("xl",), "level_changed"),
                         (("pos",), "unexpected_movement"), (("hp_max", "mp_max"), "resource_max_changed")):
        if any(p.get(k) != old.get(k) for k in keys):
            return reason
    if obs["inventory"] != initial["inventory"] or p.get("weapon_index") != old.get("weapon_index"):
        return "resource_changed"
    if p["hp"] < old["hp"]:
        return "damage"
    if p["mp"] < old["mp"]:
        return "resource_spent"
    if local_hazard(obs, rules):
        return "local_hazard"
    if any(m.get("location_status") != "visible" for m in obs["monsters"]):
        return "invisible_marker"
    if operation == "recover":
        if any(not recovery_friendly(m) for m in obs["monsters"]):
            return "visible_threat"
        if labels(p) - set(rules["allow_status"]):
            return "unexpected_status"
        if any(ATTACK.search(public_text(m.get("text", ""))) for m in messages):
            return "threat_message"
        if p["hp"] >= p["hp_max"] and p["mp"] >= p["mp_max"] and not remaining_statuses(p, rules):
            return "recovered"
        return None
    if labels(p) - set(rules["allow_status"]) or any(p.get(k) != old.get(k) for k in ("mp", "status")):
        return "player_changed"
    if 100 * p["hp"] < rules["min_hp_percent"] * p["hp_max"]:
        return "low_hp"
    if messages:
        return "message"
    if any(not m.get("id") or (m.get("att") not in (0, 1) and not recovery_friendly(m)) for m in obs["monsters"]):
        return "unknown_monster"
    current = {m["id"]: m for m in obs["monsters"]}
    original = {m.get("id"): m for m in initial["monsters"]}
    if current.keys() - original.keys():
        return "new_monster"
    if original.keys() - current.keys():
        return "monster_lost"
    enemies = hostiles(obs)
    if len(enemies) != 1 or enemies[0]["id"] != rules["monster_id"]:
        return "chosen_foe_not_isolated"
    enemy = enemies[0]
    if enemy.get("threat") is None or enemy["threat"] > rules["max_threat"]:
        return "enemy_threat"
    if enemy.get("icons"):
        return "enemy_status"
    if not rules["assessed_ranged"] and any(w.get("attack_hint") in ("ranged", "reaching") for w in enemy.get("visible_weapons", [])):
        return "ranged_or_reaching_enemy"
    if any(signature(m) != signature(original[mid]) for mid, m in current.items()):
        return "monster_changed"
    if max(abs(enemy["dx"]), abs(enemy["dy"])) <= rules["distance"]:
        return "foe_in_range"
    return None


def run(game, operation, options):
    rules = policy(operation, options)
    started, timestamp = time.monotonic(), round(time.time(), 3)
    deadline = started + rules["max_seconds"]
    before = game.state.player.get("turn")
    steps, sent, reason, error = [], 0, None, None
    watch = Watch(game.state, operation, rules)
    previous_observer = game.state.guard_observer
    game.state.guard_observer = watch
    def snapshot():
        return {**game.state.observation(), "running": game.process.poll() is None, "settled": game.settled}
    try:
        # Preflight sends no input. Clear only the helper's message window;
        # retain any genuinely unresolved response from an earlier command.
        game.state.input_messages = []
        game.settle_input(deadline=deadline, context=operation)
        initial = snapshot()
        previous = initial
        while True:
            obs = snapshot()
            messages = copy.deepcopy(game.state.input_messages)
            reason = guard(obs, initial, operation, rules, messages, watch)
            if reason:
                break
            if sent and obs["player"]["turn"] == previous["player"]["turn"]:
                reason = "no_turn_progress"
                break
            if sent and operation == "recover":
                # Restart only a recognized completion or an explicitly allowed
                # status transition. An unexplained native interrupt is a decision.
                changed = labels(obs["player"]) != labels(previous["player"])
                completion = any(public_text(m.get("text", "")) in ("HP restored.", "Magic restored.", "Done waiting.") for m in messages)
                if not changed and not completion:
                    reason = "native_interrupt"
                    break
            if time.monotonic() >= deadline:
                reason = "time_limit"
                break
            if sent >= rules["max_actions"]:
                reason = "action_limit"
                break
            previous = obs
            key = "5" if operation == "recover" else "."
            game.state.begin_input()
            game.state.inspection = None
            game.send({"msg": "key", "keycode": ord(key)})
            sent += 1
            try:
                game.settle_input(deadline=deadline, require_event=True, context=operation)
            finally:
                step = {"event": operation.replace("-", "_") + "_step", "invocation": game.actions.count + 1,
                        "step": sent, "key": key, "turn_before": previous["player"]["turn"],
                        "turn": game.state.player.get("turn"), "settled": game.settled}
                if operation == "wait-for":
                    step["hp_before"] = previous["player"].get("hp")
                    step["hp_after"] = game.state.player.get("hp")
                    step["hp_change"] = (step["hp_after"] - step["hp_before"]
                                         if all(type(step[k]) is int for k in ("hp_before", "hp_after"))
                                         else None)
                steps.append(step)
                game.actions.write(step)
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        error, reason = str(exc), "action_error"
    finally:
        game.state.guard_observer = previous_observer
        game.actions.record(operation, rules["max_actions"], sent, before, game.state.player,
                            started, timestamp, game.settled, game.process.poll() is None, error)
    result = game.observe()
    result["keys_sent"] = sent
    result["recovery"] = {"operation": operation, "policy": rules, "stop_reason": reason,
                          "actions": sent, "turns": max(0, game.state.player.get("turn", before or 0) - (before or 0)),
                          "elapsed_ms": round((time.monotonic() - started) * 1000), "steps": steps}
    if operation == "recover":
        result["recovery"]["remaining_statuses"] = sorted(remaining_statuses(result["player"], rules))
    if error:
        result["recovery"]["error"] = error
    return result
