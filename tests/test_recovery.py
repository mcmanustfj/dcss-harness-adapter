import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.metrics import ActionLog, action_stats
from dcss_harness.game import Game
from dcss_harness.state import State
from dcss_harness.presentation import compact_observation
from dcss_harness.cli import main
from dcss_harness.recovery import run


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = g = Game.__new__(Game)
        g.session, g.state = Path(temp.name), State()
        g.state.player = {"hp": 50, "hp_max": 100, "mp": 1, "mp_max": 5,
            "xl": 3, "turn": 20, "place": "Dungeon", "depth": 2,
            "pos": {"x": 0, "y": 0}, "status": [], "weapon_index": -1}
        g.state.mode = 1
        g.settled, g.timeout, g.auto_more = True, 5, True
        g.process = SimpleNamespace(poll=lambda: None)
        g.actions = ActionLog(g.session / "actions.jsonl")
        g.actions.start(g.state.player)
        self.addCleanup(g.actions.close)
        g.send, g.settle = Mock(), Mock()

    def effects(self, *callbacks):
        pending = iter(callbacks)
        def settle(**kw):
            if kw.get("require_event"):
                self.game.state.apply({"msg": "player", "turn": self.game.state.player["turn"] + 10})
                next(pending)()
        self.game.settle.side_effect = settle

    def player(self, **fields):
        self.game.state.apply({"msg": "player", **fields})

    def messages(self, *texts):
        self.game.state.apply({"msg": "msgs", "messages": [{"text": t} for t in texts]})

    def enemy(self, identity=7, x=4, **fields):
        self.game.state.apply({"msg": "map", "cells": [{"x": x, "y": 0, "t": {"bg": 0},
            "mon": {"id": identity, "name": "goblin", "type": 1, "att": 0, "threat": 0, **fields}}]})

    def test_assessed_poison_cloud_recovery_and_waiting(self):
        self.game.state.cells[(0,0)] = {'f': 33, 't': {'bg': 0, 'cloud': 5262}}
        rules = {'allow_cloud': ['poison'], 'max_actions': 1}
        self.effects(lambda: self.player(hp=100, mp=5))
        self.assertEqual(run(self.game, 'recover', rules)['recovery']['actions'], 1)
        self.enemy()
        self.effects(lambda: None)
        self.assertEqual(run(self.game, 'wait-for', {**rules, 'monster_id': 7})['recovery']['actions'], 1)

    def test_assessed_water_recovery_and_waiting(self):
        self.player(status=[{'light': 'Fly'}])
        self.game.state.cells[(0,0)] = {'f': 31, 't': {'bg': 0}}
        rules = {'allow_status': ['Fly'], 'allow_water_with_flight': True, 'max_actions': 1}
        self.effects(lambda: self.player(hp=100, mp=5))
        self.assertEqual(run(self.game, 'recover', rules)['recovery']['actions'], 1)
        self.enemy()
        self.effects(lambda: None)
        self.assertEqual(run(self.game, 'wait-for', {**rules, 'monster_id': 7})['recovery']['actions'], 1)

    def test_native_hp_then_mp_completion_messages_and_metrics(self):
        before = self.game.observe()
        def hp():
            self.player(hp=100, mp=3)
            self.messages("HP restored.", *[f"hp:{n}" for n in range(110)])
        def mp():
            self.player(mp=5)
            self.messages("Magic restored.", *[f"mp:{n}" for n in range(110)])
        self.effects(hp, mp)
        result = run(self.game, "recover", {})
        self.assertEqual((result["recovery"]["stop_reason"], result["recovery"]["actions"], result["recovery"]["turns"]), ("recovered", 2, 20))
        self.assertEqual([c.args[0]["keycode"] for c in self.game.send.call_args_list], [ord("5")] * 2)
        self.assertEqual(len(compact_observation(result, before)["messages"]), 222)
        self.assertIsNone(compact_observation(self.game.observe(), result)["recovery"])
        stats = action_stats(self.game.session / "actions.jsonl")
        self.assertEqual((stats["actions"], stats["recover_steps"], stats["keys_sent"], stats["observed_turns"]), (1, 2, 2, 20))
        self.assertIsNone(self.game.state.guard_observer)

    def test_allowed_transitions_clear_statuses_and_full_resource_target(self):
        self.player(hp=100, mp=5, status=[{"light": "Slow"}, {"light": "-Berserk"}])
        self.assertEqual(run(self.game, "recover", {"allow_status": ["Slow", "-Berserk"]})["recovery"]["actions"], 0)
        self.effects(lambda: self.player(status=[{"light": "-Berserk"}]), lambda: self.player(status=[]))
        result = run(self.game, "recover", {"allow_status": ["Slow", "-Berserk"], "clear_statuses": True})
        self.assertEqual((result["recovery"]["stop_reason"], result["recovery"]["actions"]), ("recovered", 2))

    def test_danger_latched_even_if_damage_status_or_foe_disappears(self):
        callbacks = [
            ("damage", lambda: (self.player(hp=40), self.player(hp=100))),
            ("unexpected_status", lambda: (self.player(status=[{"light": "Pois"}]), self.player(status=[]))),
            ("visible_threat", lambda: (self.enemy(), self.game.state.apply({"msg": "map", "clear": True}))),
            ("threat_message", lambda: self.messages("Something misses you.")),
        ]
        original = copy.deepcopy(self.game.state)
        for reason, callback in callbacks:
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.game.send.reset_mock()
                self.effects(callback)
                result = run(self.game, "recover", {})
                expected = "unseen_threat" if reason == "threat_message" else reason
                self.assertEqual(result["recovery"]["stop_reason"], expected)
                self.assertEqual(self.game.send.call_count, 1)

    def test_recovery_stop_conditions(self):
        cases = [
            ("unexpected_movement", lambda: self.player(pos={"x": 2, "y": 0})),
            ("depth_changed", lambda: self.player(depth=3)),
            ("level_changed", lambda: self.player(xl=4)),
            ("resource_max_changed", lambda: self.player(hp_max=90)),
            ("resource_spent", lambda: self.player(mp=0)),
            ("input_required", lambda: setattr(self.game.state, "mode", 7)),
            ("unsettled", lambda: setattr(self.game, "settled", False)),
            ("game_exited", lambda: setattr(self.game.state, "exit_reason", {"type": "dead"})),
            ("native_interrupt", lambda: self.messages("You hear a distant noise.")),
        ]
        original = copy.deepcopy(self.game.state)
        for reason, change in cases:
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.game.settled = True
                self.game.send.reset_mock()
                self.effects(change)
                self.assertEqual(run(self.game, "recover", {})["recovery"]["stop_reason"], reason)
                self.assertEqual(self.game.send.call_count, 1)

    def test_no_progress_limits_and_unknown_initial_safety(self):
        self.assertEqual(run(self.game, "recover", {})["recovery"]["stop_reason"], "no_turn_progress")
        self.effects(lambda: self.messages("Done waiting."))
        self.assertEqual(run(self.game, "recover", {"max_actions": 1})["recovery"]["stop_reason"], "action_limit")
        self.game.send.reset_mock()
        self.game.state.unseen_threat = {"reason": "unseen_attacker"}
        self.assertEqual(run(self.game, "recover", {})["recovery"]["stop_reason"], "unseen_threat")
        self.game.send.assert_not_called()

    def waiting(self):
        self.player(hp=100, mp=5)
        self.enemy()
        return {"monster_id": 7}

    def test_wait_until_distance_never_moves_or_attacks(self):
        rules = self.waiting()
        def approach():
            self.game.state.apply({"msg": "map", "cells": [{"x": 4, "y": 0, "mon": None}]})
            self.enemy(x=2)
        self.effects(approach)
        result = run(self.game, "wait-for", rules)
        self.assertEqual((result["recovery"]["stop_reason"], result["recovery"]["actions"]), ("foe_in_range", 1))
        self.game.send.assert_called_once_with({"msg": "key", "keycode": ord(".")})
        self.assertEqual(action_stats(self.game.session / "actions.jsonl")["wait_for_steps"], 1)

    def test_assessed_ranged_wait_keeps_event_guards(self):
        rules = {**self.waiting(), "assessed_ranged": True}
        original = copy.deepcopy(self.game.state)
        for reason, change in (
                ("message", lambda: self.messages("The naga spits poison.")),
                ("damage", lambda: self.player(hp=90)),
                ("player_changed", lambda: self.player(status=[{"light": "Slow"}])),
                ("new_monster", lambda: self.enemy(8, 3)),
                ("monster_lost", lambda: self.game.state.apply({"msg": "map", "clear": True})),
                ("input_required", lambda: setattr(self.game.state, "mode", 7))):
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.effects(change)
                result = run(self.game, "wait-for", rules)
                self.assertEqual((result["recovery"]["stop_reason"], result["recovery"]["actions"]), (reason, 1))

    def test_assessed_ranged_hint_only_and_single_foe(self):
        rules = self.waiting()
        original = self.game.state.observation
        def snapshot():
            obs = original()
            obs["monsters"][0]["visible_weapons"] = [{"attack_hint": "ranged"}]
            return obs
        self.game.state.observation = snapshot
        self.assertEqual(run(self.game, "wait-for", rules)["recovery"]["stop_reason"], "ranged_or_reaching_enemy")
        self.effects(lambda: None, lambda: self.messages("The archer shoots."))
        result = run(self.game, "wait-for", {**rules, "assessed_ranged": True})
        self.assertEqual((result["recovery"]["stop_reason"], result["recovery"]["actions"]), ("message", 2))
        self.enemy(8, 3)
        self.assertEqual(run(self.game, "wait-for", {**rules, "assessed_ranged": True})["recovery"]["stop_reason"], "chosen_foe_not_isolated")

    def test_assessed_ranged_cli_validation(self):
        from dcss_harness.recovery import policy
        with self.assertRaises(ValueError):
            policy("wait-for", {"monster_id": 7, "assessed_ranged": 1})
        with patch("sys.argv", ["crawl-agent", "wait-for", "--monster-id", "7", "--assessed-ranged"]), \
                patch("dcss_harness.cli.output_request", return_value=0) as output:
            self.assertEqual(main(), 0)
            self.assertIs(output.call_args.args[1]["policy"]["assessed_ranged"], True)

    def test_wait_healing_continues_and_records_each_step(self):
        rules = self.waiting()
        rules.update(min_hp_percent=75, max_actions=2)
        self.player(hp=80)
        self.effects(lambda: self.player(hp=81), lambda: self.player(hp=82))
        result = run(self.game, 'wait-for', rules)
        self.assertEqual(result['recovery']['stop_reason'], 'action_limit')
        self.assertEqual(result['recovery']['actions'], 2)
        self.assertEqual([(s['hp_before'], s['hp_after'], s['hp_change'])
                          for s in result['recovery']['steps']], [(80, 81, 1), (81, 82, 1)])

    def test_wait_healing_never_hides_damage_maximum_or_other_changes(self):
        rules = self.waiting()
        self.player(hp=90)
        original = copy.deepcopy(self.game.state)
        cases = [('damage', lambda: (self.player(hp=95), self.player(hp=94))),
                 ('damage', lambda: (self.player(hp=89), self.player(hp=95))),
                 ('resource_max_changed', lambda: self.player(hp=95, hp_max=105)),
                 ('player_changed', lambda: (self.player(hp=95, mp=6), self.player(mp=5))),
                 ('player_changed', lambda: self.player(hp=95, status=[{'light': 'Slow'}])),
                 ('message', lambda: (self.player(hp=95), self.messages('HP restored.')))]
        for reason, effect in cases:
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.game.send.reset_mock()
                self.effects(effect)
                result = run(self.game, 'wait-for', rules)
                self.assertEqual(result['recovery']['stop_reason'], reason)
                self.assertEqual(result['recovery']['actions'], 1)

    def friendly_summon(self, attitude=4, icons=('SUMMONED', 'BERSERK')):
        from dcss_harness.safety import CONSTANTS
        self.enemy(9, 2, att=attitude, threat=2, name='skyshark')
        self.game.state.apply({'msg': 'map', 'cells': [{'x': 2, 'y': 0,
            't': {'icons': [CONSTANTS['TILEI_' + icon] for icon in icons]}}]})

    def test_friendly_berserk_summon_allows_recovery_and_expiry(self):
        self.friendly_summon()
        def heal():
            self.friendly_summon(icons=('FRIENDLY', 'SUMMONED'))
            self.game.state.apply({'msg': 'map', 'cells': [{'x': 2, 'y': 0, 'mon': None}]})
            self.player(hp=100, mp=5)
        self.effects(heal)
        result = run(self.game, 'recover', {})
        self.assertEqual(result['recovery']['stop_reason'], 'recovered')
        self.assertEqual(result['recovery']['actions'], 1)

    def test_wait_with_confirmed_friend_and_transient_friend_changes(self):
        self.player(hp=100, mp=5)
        self.enemy()
        self.friendly_summon()
        self.effects(lambda: None)
        result = run(self.game, 'wait-for', {'monster_id': 7, 'max_actions': 1})
        self.assertEqual(result['recovery']['actions'], 1)
        for change, reason in ((lambda: self.friendly_summon(0), 'monster_changed'),
                               (lambda: self.friendly_summon(icons=('CONFUSED',)), 'monster_changed'),
                               (lambda: self.game.state.apply({'msg': 'map', 'cells': [{'x': 2, 'y': 0, 'mon': None}]}), 'monster_lost'),
                               (lambda: self.messages('Your ice beast hits the goblin.'), 'message')):
            self.friendly_summon()
            self.effects(lambda: (change(), self.friendly_summon()))
            result = run(self.game, 'wait-for', {'monster_id': 7})
            self.assertEqual(result['recovery']['actions'], 1)
            self.assertEqual(result['recovery']['stop_reason'], reason)

    def test_recovery_rejects_neutral_unknown_or_dangerous_allies(self):
        for attitude in (0, 1, 2, 3, 5, None, 999):
            with self.subTest(attitude=attitude):
                self.friendly_summon(attitude)
                self.assertEqual(run(self.game, 'recover', {})['recovery']['stop_reason'], 'visible_threat')
        for icon in ('CONFUSED', 'FRENZIED', 'INNER_FLAME'):
            self.friendly_summon(icons=(icon,))
            self.assertEqual(run(self.game, 'recover', {})['recovery']['stop_reason'], 'visible_threat')
        self.game.send.assert_not_called()

    def test_transient_ally_attitude_and_danger_are_latched(self):
        for change in (lambda: self.friendly_summon(0),
                       lambda: self.friendly_summon(icons=('CONFUSED',))):
            self.friendly_summon()
            self.effects(lambda: (change(), self.friendly_summon(), self.player(hp=100, mp=5)))
            result = run(self.game, 'recover', {})
            self.assertEqual(result['recovery']['stop_reason'], 'visible_threat')
            self.assertEqual(result['recovery']['actions'], 1)
            self.player(hp=50, mp=1)

    def test_petrified_flowers_allow_recovery_but_changed_status_stops(self):
        from dcss_harness.safety import CONSTANTS
        self.enemy(50, 2, type=CONSTANTS['MONS_PETRIFIED_FLOWER'], typedata={'no_exp': True})
        self.effects(lambda: self.player(hp=100, mp=5))
        self.assertEqual(run(self.game, 'recover', {})['recovery']['stop_reason'], 'recovered')
        self.player(hp=50, mp=1)
        self.effects(lambda: self.game.state.apply({'msg': 'map', 'cells': [{'x': 2, 'y': 0,
            't': {'icons': [CONSTANTS['TILEI_INNER_FLAME']]}}]}))
        self.assertEqual(run(self.game, 'recover', {})['recovery']['stop_reason'], 'visible_threat')

    def test_wait_message_change_ids_and_state_stop(self):
        rules = self.waiting()
        cases = [
            ("message", lambda: self.messages("The goblin shouts!")),
            ("new_monster", lambda: self.enemy(8, 3)),
            ("monster_lost", lambda: self.game.state.apply({"msg": "map", "clear": True})),
            ("player_changed", lambda: self.player(mp=6)),
            ("player_changed", lambda: self.player(status=[{"light": "Slow"}])),
            ("monster_changed", lambda: self.enemy(name="shapeshifter")),
        ]
        original = copy.deepcopy(self.game.state)
        for reason, change in cases:
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.game.send.reset_mock()
                self.effects(change)
                self.assertEqual(run(self.game, "wait-for", rules)["recovery"]["stop_reason"], reason)
                self.assertEqual(self.game.send.call_count, 1)

    def test_wait_rejects_wrong_id_ranged_reaching_status_and_threat(self):
        rules = self.waiting()
        self.assertEqual(run(self.game, "wait-for", {"monster_id": 99})["recovery"]["stop_reason"], "chosen_foe_not_isolated")
        from dcss_harness.safety import CONSTANTS
        self.game.state.cells[(4, 0)]["t"]["icons"] = [CONSTANTS["TILEI_HASTED"]]
        self.assertEqual(run(self.game, "wait-for", rules)["recovery"]["stop_reason"], "enemy_status")
        self.game.state.cells[(4, 0)]["t"]["icons"] = []
        self.enemy(threat=2)
        self.assertEqual(run(self.game, "wait-for", rules)["recovery"]["stop_reason"], "enemy_threat")
        self.enemy(threat=0)
        for hint in ("ranged", "reaching"):
            with patch.object(self.game.state, "observation", wraps=self.game.state.observation) as observe:
                obs = observe()
                obs["monsters"][0]["visible_weapons"] = [{"attack_hint": hint}]
                observe.return_value = obs
                self.assertEqual(run(self.game, "wait-for", rules)["recovery"]["stop_reason"], "ranged_or_reaching_enemy")
        self.game.send.assert_not_called()

    def test_transient_new_wait_enemy_and_recovery_more_prompt(self):
        rules = self.waiting()
        def transient():
            self.enemy(8, 3)
            self.game.state.apply({"msg": "map", "cells": [{"x": 3, "y": 0, "mon": None}]})
        self.effects(transient)
        self.assertEqual(run(self.game, "wait-for", rules)["recovery"]["stop_reason"], "new_monster")
        self.game.state.cells.clear()
        self.player(hp=50)
        self.game.send.reset_mock()
        def settle(**kw):
            if kw.get("require_event"):
                if self.game.send.call_args.args[0]["keycode"] == 32:
                    self.game.state.mode, self.game.state.more = 7, False
                else:
                    self.game.state.mode, self.game.state.more = 5, True
        self.game.settle.side_effect = settle
        result = run(self.game, "recover", {})
        self.assertEqual(result["recovery"]["stop_reason"], "input_required")
        self.assertEqual([c.args[0]["keycode"] for c in self.game.send.call_args_list], [ord("5"), 32])

    def test_cli_independent_session_paths_and_invalid_policies(self):
        for directory, operation in (("/tmp/recover-a", "recover"), ("/tmp/wait-b", "wait-for")):
            args = ["crawl-agent", "--session-dir", directory, "--stream", "control", operation]
            if operation == "wait-for":
                args += ["--monster-id", "7"]
            with patch("sys.argv", args), patch("dcss_harness.cli.output_request", return_value=0) as output:
                self.assertEqual(main(), 0)
                self.assertEqual(output.call_args.args[0], Path(directory))
                self.assertEqual(output.call_args.args[1]["op"], operation)
                self.assertEqual(output.call_args.args[2].stream, "control")
        for operation, options in (("wait-for", {}), ("wait-for", {"monster_id": True}),
                                   ("recover", {"max_actions": 33}), ("recover", {"clear_statuses": "yes"}),
                                   ("recover", {"max_seconds": float("inf")}), ("recover", {"approach": True})):
            with self.subTest(options=options), self.assertRaises(ValueError):
                run(self.game, operation, options)
        self.game.send.assert_not_called()

    def test_time_bound_and_error_cleanup_no_retry(self):
        with patch("dcss_harness.recovery.time.monotonic", side_effect=[0, 31, 31, 31, 31]):
            self.assertEqual(run(self.game, "recover", {})["recovery"]["stop_reason"], "time_limit")
        self.game.send.assert_not_called()
        self.effects(lambda: (_ for _ in ()).throw(OSError("timeout after key")))
        self.assertEqual(run(self.game, "recover", {})["recovery"]["stop_reason"], "action_error")
        self.assertEqual(self.game.send.call_count, 1)
        self.assertIsNone(self.game.state.guard_observer)
        self.assertEqual(action_stats(self.game.session / "actions.jsonl")["recover_steps"], 1)

    def test_signed_wound_scalar_is_visible_and_invisibility_uses_background(self):
        from dcss_harness.safety import monster_appearance
        from dcss_harness.items import tile_flags
        self.assertEqual(tile_flags(-2147483600), 0x80000030)
        wounded = monster_appearance({"t": {"fg": -2147483600, "bg": 0}})
        self.assertEqual((wounded["location_status"], wounded["wounds"]), ("visible", "moderately wounded"))
        self.assertEqual(monster_appearance({"t": {"fg": [0, 64], "bg": 0}})["location_status"], "visible")
        self.assertEqual(monster_appearance({"t": {"fg": 0, "bg": [0, 64]}})["location_status"], "invisible_disturbance")

    def test_selected_expiry_targets_preserve_flight_in_both_expiry_orders(self):
        rules = {'allow_status': ['Fly', 'Slow', '-Berserk'], 'clear_status': ['Slow', '-Berserk']}
        for surviving in ('Slow', '-Berserk'):
            with self.subTest(surviving=surviving):
                self.player(hp=100, mp=5, status=[{'light': s} for s in rules['allow_status']])
                self.effects(lambda: self.player(status=[{'light': 'Fly'}, {'light': surviving}]),
                             lambda: self.player(status=[{'light': 'Fly'}]))
                result = run(self.game, 'recover', rules)
                self.assertEqual((result['recovery']['stop_reason'], result['recovery']['actions']), ('recovered', 2))
                self.assertEqual(result['player']['status'], [{'light': 'Fly'}])
                self.assertEqual(result['recovery']['remaining_statuses'], [])
                self.assertEqual(result['recovery']['policy']['target'], 'full_hp_mp_and_selected_statuses_cleared')
        self.game.send.reset_mock()
        self.assertEqual(run(self.game, 'recover', rules)['recovery']['actions'], 0)
        self.game.send.assert_not_called()

    def test_target_does_not_imply_permission_and_all_vs_selected_is_explicit(self):
        self.player(hp=100, mp=5, status=[{'light': 'Fly'}, {'light': 'Slow'}])
        for rules in ({'clear_status': ['Slow']}, {'allow_status': ['Slow'], 'clear_status': ['Slow'], 'clear_statuses': True},
                      {'clear_status': 'Slow'}, {'clear_status': [None]}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                run(self.game, 'recover', rules)
        self.assertEqual(run(self.game, 'recover', {'allow_status': ['Slow'], 'clear_status': ['Slow']})['recovery']['stop_reason'], 'unexpected_status')
        self.game.send.assert_not_called()
        self.effects(lambda: self.player(status=[{'light': 'Fly'}]))
        result = run(self.game, 'recover', {'allow_status': ['Fly', 'Slow'], 'clear_statuses': True, 'max_actions': 1})
        self.assertEqual(result['recovery']['stop_reason'], 'action_limit')
        self.assertEqual(result['recovery']['remaining_statuses'], ['Fly'])

    def test_text_only_statuses_exact_fallback_empty_light_and_unknown(self):
        for status in ({'text': 'strong-willed'}, {'light': '', 'text': 'strong-willed'},
                       {'text': '<lightblue>strong-willed</lightblue>'}):
            with self.subTest(status=status):
                self.player(hp=100, mp=5, status=[status, {'light': 'Fly'}])
                rules = {'allow_status': ['Fly', 'strong-willed'], 'clear_status': ['strong-willed']}
                self.effects(lambda: self.player(status=[{'light': 'Fly'}]))
                self.assertEqual(run(self.game, 'recover', rules)['recovery']['stop_reason'], 'recovered')
        self.game.send.reset_mock()
        for status in ({}, {'light': 'Will', 'text': 'strong-willed'}, {'text': 'strong-willed and poisoned'}):
            self.player(status=[status])
            self.assertEqual(run(self.game, 'recover', {'allow_status': ['strong-willed']})['recovery']['stop_reason'], 'unexpected_status')
        self.game.send.assert_not_called()

    def test_coloured_magic_completion_continues_to_full_hp(self):
        self.player(hp=89, hp_max=102, mp=9, mp_max=10, status=[{'light': 'Fly'}])
        def magic():
            self.player(hp=92, mp=10)
            self.messages('<lightgrey>You start resting.', '<lightblue>Magic restored.<lightgrey>')
        def hp():
            self.player(hp=102)
            self.messages('<lightblue>HP restored.</lightblue>')
        self.effects(magic, hp)
        result = run(self.game, 'recover', {'allow_status': ['Fly']})
        self.assertEqual((result['recovery']['stop_reason'], result['recovery']['actions']), ('recovered', 2))
        self.assertEqual([m['text'] for m in result['messages']], ['You start resting.', 'Magic restored.', 'HP restored.'])

    def test_completion_and_selected_expiry_never_override_danger(self):
        cases = [
            ('damage', lambda: self.player(hp=40)),
            ('unexpected_status', lambda: self.player(status=[{'light': 'Fly'}, {'light': 'Pois'}])),
            ('visible_threat', lambda: self.enemy()),
            ('input_required', lambda: setattr(self.game.state, 'mode', 7)),
            ('unsettled', lambda: setattr(self.game, 'settled', False)),
            ('threat_message', lambda: self.messages('<red>The orc hits <white>you</white>!')),
        ]
        original = copy.deepcopy(self.game.state)
        for expected, danger in cases:
            with self.subTest(expected=expected):
                self.game.state = copy.deepcopy(original)
                self.game.settled = True
                self.player(status=[{'light': 'Fly'}, {'light': 'Slow'}])
                self.game.send.reset_mock()
                def finish():
                    self.player(mp=5, status=[{'light': 'Fly'}])
                    self.messages('<lightblue>Magic restored.<lightgrey>')
                    danger()
                self.effects(finish)
                result = run(self.game, 'recover', {'allow_status': ['Fly', 'Slow'], 'clear_status': ['Slow']})
                self.assertEqual(result['recovery']['stop_reason'], expected)
                self.assertEqual(self.game.send.call_count, 1)

    def test_completion_is_exact_and_persistent_selected_status_stays_bounded(self):
        self.effects(lambda: self.messages('You hear someone say: Magic restored.'))
        self.assertEqual(run(self.game, 'recover', {})['recovery']['stop_reason'], 'native_interrupt')
        self.player(hp=100, mp=5, status=[{'light': 'Fly'}])
        self.effects(lambda: self.messages('<lightgrey>Done waiting.'), lambda: self.messages('Done waiting.'))
        result = run(self.game, 'recover', {'allow_status': ['Fly'], 'clear_status': ['Fly'], 'max_actions': 2})
        self.assertEqual(result['recovery']['stop_reason'], 'action_limit')
        self.assertEqual(result['recovery']['remaining_statuses'], ['Fly'])

    def test_wait_allows_assessed_unchanged_status_but_stops_all_changes(self):
        rules = self.waiting()
        self.player(status=[{'light': 'Fly'}, {'text': 'strong-willed'}])
        self.assertEqual(run(self.game, 'wait-for', rules)['recovery']['stop_reason'], 'player_changed')
        rules.update(allow_status=['Fly', 'strong-willed'], max_actions=1)
        self.effects(lambda: None)
        self.assertEqual(run(self.game, 'wait-for', rules)['recovery']['stop_reason'], 'action_limit')
        original = copy.deepcopy(self.game.state)
        for statuses in ([{'light': 'Fly'}], [{'light': 'Fly'}, {'text': 'strong-willed'}, {'light': 'Pois'}], []):
            with self.subTest(statuses=statuses):
                self.game.state = copy.deepcopy(original)
                self.effects(lambda: self.player(status=statuses))
                self.assertEqual(run(self.game, 'wait-for', rules)['recovery']['stop_reason'], 'player_changed')
        self.game.state = copy.deepcopy(original)
        self.effects(lambda: self.messages('The goblin shouts!'))
        self.assertEqual(run(self.game, 'wait-for', rules)['recovery']['stop_reason'], 'message')

    def test_selective_status_cli_forwarding(self):
        cases = [('recover', ['--allow-status', 'Fly', '--allow-status', 'Slow', '--clear-status', 'Slow']),
                 ('wait-for', ['--monster-id', '7', '--allow-status', 'Fly', '--allow-status', 'strong-willed'])]
        for operation, flags in cases:
            with patch('sys.argv', ['crawl-agent', '--session-dir', '/tmp/selective', '--stream', 'test', operation, *flags]), \
                    patch('dcss_harness.cli.output_request', return_value=0) as output:
                self.assertEqual(main(), 0)
                actual = output.call_args.args[1]['policy']
                self.assertIn('Fly', actual['allow_status'])
                if operation == 'recover':
                    self.assertEqual(actual['clear_status'], ['Slow'])
                    self.assertNotIn('clear_statuses', actual)

    def test_debris_does_not_block_recovery_but_real_foes_do(self):
        from dcss_harness.safety import CONSTANTS
        def debris():
            self.enemy(50, 2, name='pile of debris', type=CONSTANTS['MONS_PILE_OF_DEBRIS'], typedata={'no_exp': True})
        debris()
        def heal():
            debris()  # Exercise the per-event threat watcher as well.
            self.player(hp=100, mp=5)
            self.messages('HP restored.')
        self.effects(heal)
        result = run(self.game, 'recover', {})
        self.assertEqual(result['recovery']['stop_reason'], 'recovered')
        self.assertEqual(result['recovery']['actions'], 1)
        self.assertEqual(result['scenery'][0]['id'], 50)
        self.game.send.reset_mock()
        self.player(hp=50)
        self.enemy()
        self.assertEqual(run(self.game, 'recover', {})['recovery']['stop_reason'], 'visible_threat')
        self.game.send.assert_not_called()

    def test_debris_arrival_during_wait_is_not_a_new_enemy(self):
        from dcss_harness.safety import CONSTANTS
        rules = self.waiting()
        self.effects(lambda: self.enemy(50, 2, name='pile of debris',
            type=CONSTANTS['MONS_PILE_OF_DEBRIS'], typedata={'no_exp': True}))
        result = run(self.game, 'wait-for', {**rules, 'max_actions': 1})
        self.assertEqual(result['recovery']['stop_reason'], 'action_limit')
        self.assertEqual([m['id'] for m in result['monsters']], [7])
        self.assertEqual([m['id'] for m in result['scenery']], [50])


if __name__ == "__main__":
    unittest.main()
