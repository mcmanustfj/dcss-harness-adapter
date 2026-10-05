import copy
import json
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
from dcss_harness.combat import guard, policy, run
from dcss_harness.safety import monster_appearance


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = game = Game.__new__(Game)
        game.session = Path(self.temp.name)
        game.state = State()
        game.state.player = {"name": "Test", "hp": 100, "hp_max": 100,
            "mp": 5, "turn": 10, "xl": 3, "place": "Dungeon", "depth": 2,
            "pos": {"x": 0, "y": 0}, "status": [], "weapon_index": -1}
        game.state.mode = 1
        self.enemy(1, 1, 0)
        game.process = SimpleNamespace(poll=lambda: None)
        game.timeout, game.auto_more, game.settled = 5, True, True
        game.actions = ActionLog(game.session / "actions.jsonl")
        game.actions.start(game.state.player)
        self.addCleanup(game.actions.close)
        game.send = Mock()
        game.settle = Mock()

    def enemy(self, identity, x, y, **fields):
        self.game.state.cells[(x, y)] = {"t": {"bg": 0, "fg": 0},
            "mon": {"id": identity, "att": 0, "threat": 0, "name": "goblin", "type": 1, **fields}}

    def effect(self, callback):
        def settle(**kwargs):
            if kwargs.get("require_event"):
                self.game.state.player["turn"] += 1
                callback()
        self.game.settle.side_effect = settle

    def test_assessed_poison_cloud_damage_still_stops(self):
        self.game.state.cells[(0,0)] = {'f': 33, 't': {'bg': 0, 'cloud': 5262}}
        self.effect(lambda: None)
        self.assertEqual(run(self.game, {})['combat']['actions'], 0)
        rules = {'allow_cloud': ['poison'], 'max_actions': 2}
        self.assertEqual(run(self.game, rules)['combat']['actions'], 2)
        def transient_damage():
            self.game.state.apply({'msg': 'player', 'hp': 99})
            self.game.state.apply({'msg': 'player', 'hp': 100})
        self.effect(transient_damage)
        result = run(self.game, rules)['combat']
        self.assertEqual((result['actions'], result['stop_reason']), (1, 'damage'))

    def test_assessed_water_combat_and_flight_loss(self):
        self.game.state.player['status'] = [{'light': 'Fly'}]
        self.game.state.cells[(0,0)] = {'f': 32, 't': {'bg': 0}}
        rules = {'allow_status': ['Fly'], 'allow_water_with_flight': True, 'max_actions': 2}
        self.effect(lambda: None)
        self.assertEqual(run(self.game, rules)['combat']['actions'], 2)
        def expiry():
            self.game.state.apply({'msg': 'player', 'status': []})
            self.game.state.apply({'msg': 'player', 'status': [{'light': 'Fly'}]})
        self.effect(expiry)
        result = run(self.game, rules)['combat']
        self.assertEqual(result['actions'], 1)
        self.assertEqual(result['stop_reason'], 'hazard_assessment_changed')

    def test_limits_one_decision_metrics_and_all_messages(self):
        def messages():
            turn = self.game.state.player["turn"]
            self.game.state.apply({"msg": "msgs", "messages": [
                {"text": f"{turn}:{n}"} for n in range(80)]})
        before = self.game.observe()
        self.effect(messages)
        result = run(self.game, {"max_actions": 3})
        self.assertEqual((result["combat"]["stop_reason"], result["combat"]["actions"], result["combat"]["turns"]),
                         ("action_limit", 3, 3))
        self.assertEqual(len(result["messages"]), 240)
        self.assertEqual(len(compact_observation(result, before)["messages"]), 240)
        self.assertEqual(len(self.game.state.messages), 100)
        self.assertEqual([c.args[0]["keycode"] for c in self.game.send.call_args_list], [ord("l")] * 3)
        stats = action_stats(self.game.session / "actions.jsonl")
        self.assertEqual((stats["actions"], stats["combat_steps"], stats["keys_sent"], stats["observed_turns"]), (1, 3, 3, 3))

    def test_stop_conditions_after_one_key(self):
        changes = [
            ("low_hp", lambda s: s.player.update(hp=84)),
            ("player_status", lambda s: s.player.update(status=[{"light": "Pois"}])),
            ("level_changed", lambda s: s.player.update(xl=4)),
            ("depth_changed", lambda s: s.player.update(depth=3)),
            ("unexpected_movement", lambda s: s.player.update(pos={"x": 1, "y": 0})),
            ("resource_changed", lambda s: s.player.update(mp=4)),
            ("input_required", lambda s: setattr(s, "mode", 7)),
            ("input_required", lambda s: setattr(s, "ui", [{"type": "menu"}])),
            ("unseen_threat", lambda s: setattr(s, "unseen_threat", {"reason": "unseen_attacker"})),
            ("game_exited", lambda s: setattr(s, "exit_reason", {"type": "dead"})),
            ("enemy_threat", lambda s: s.cells[(1, 0)]["mon"].update(threat=2)),
            ("enemy_changed", lambda s: s.cells[(1, 0)]["mon"].update(name="shapeshifter")),
            ("new_enemy", lambda s: self.enemy(2, 0, 1)),
            ("no_visible_hostiles", lambda s: s.cells.clear()),
            ("invisible_marker", lambda s: s.cells[(1, 0)]["t"].update(bg=0x4000000000)),
            ("monster_change_message", lambda s: s.apply({"msg": "msgs", "messages": [{"text": "The bear goes berserk!"}]})),
        ]
        original = copy.deepcopy(self.game.state)
        for reason, change in changes:
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.game.send.reset_mock()
                self.effect(lambda: change(self.game.state))
                result = run(self.game, {})
                self.assertEqual(result["combat"]["stop_reason"], reason)
                self.assertEqual(result["combat"]["actions"], 1)
                self.assertEqual(self.game.send.call_count, 1)

    def test_enemy_loss_with_survivor_and_attitude_change(self):
        self.enemy(2, 0, 1)
        self.effect(lambda: self.game.state.cells.pop((0, 1)))
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "enemy_lost")
        self.enemy(2, 0, 1)
        self.effect(lambda: self.game.state.cells[(0, 1)]["mon"].update(att=1))
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "enemy_lost")

    def test_unsettled_no_progress_and_error_never_retry(self):
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "no_turn_progress")
        self.game.send.reset_mock()
        self.effect(lambda: setattr(self.game, "settled", False))
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "unsettled")
        self.assertEqual(self.game.send.call_count, 1)
        self.game.settled = True
        self.game.send.reset_mock()
        self.game.send.side_effect = OSError("socket lost")
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "action_error")
        self.assertEqual(self.game.send.call_count, 1)

    def test_time_limit_during_initial_settle_sends_nothing(self):
        with patch("dcss_harness.combat.time.monotonic", side_effect=[0, 31, 31, 31, 31]):
            self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "time_limit")
        self.game.send.assert_not_called()

    def test_more_acknowledgment_then_level_choice_stops(self):
        def settle(**kwargs):
            if kwargs.get("require_event"):
                if self.game.send.call_args.args[0]["keycode"] == 32:
                    self.game.state.mode, self.game.state.more = 7, False
                else:
                    self.game.state.mode, self.game.state.more = 5, True
        self.game.settle.side_effect = settle
        result = run(self.game, {})
        self.assertEqual(result["combat"]["stop_reason"], "input_required")
        self.assertEqual(result["combat"]["actions"], 1)
        self.assertEqual([c.args[0]["keycode"] for c in self.game.send.call_args_list], [ord("l"), 32])

    def test_initial_guards_and_policy_overrides(self):
        initial = self.game.observe()
        cases = [
            ("missing_enemy_identity", lambda o: o["monsters"][0].pop("id")),
            ("enemy_status", lambda o: o["monsters"][0].update(icons=["berserk"])),
            ("unknown_attitude", lambda o: o["monsters"][0].pop("att")),
            ("distant_enemy", lambda o: o["monsters"][0].update(dx=2)),
            ("ranged_or_reaching_enemy", lambda o: o["monsters"][0].update(dx=2, visible_weapons=[{"attack_hint": "reaching"}])),
            ("local_hazard", lambda o: o.update(visible_features=[{"kind": "hazard", "cells": [[0, 0]]}])),
            ("player_status", lambda o: o["player"].update(status=[{"light": "Drain"}])),
            ("low_hp", lambda o: o["player"].update(hp=84)),
        ]
        for reason, change in cases:
            with self.subTest(reason=reason):
                obs = copy.deepcopy(initial)
                change(obs)
                self.assertEqual(guard(obs, obs, policy({}), []), reason)
        obs = copy.deepcopy(initial)
        obs["player"].update(hp=80, status=[{"light": "Drain"}])
        obs["monsters"][0]["threat"] = 2
        self.assertIsNone(guard(obs, obs, policy({"min_hp_percent": 80, "allow_status": ["Drain"], "max_threat": 2}), []))

    def test_allies_and_scenery_not_attacked_wounds_allowed(self):
        self.enemy(2, -1, 0, att=1)
        self.effect(lambda: self.game.state.cells[(1, 0)]["t"].update(fg=0x40000000))
        result = run(self.game, {"max_actions": 2})
        self.assertEqual(result["combat"]["stop_reason"], "action_limit")
        self.assertEqual([c.args[0]["keycode"] for c in self.game.send.call_args_list], [ord("l")] * 2)
        self.game.state.cells[(1, 0)]["mon"]["att"] = 1
        self.assertEqual(run(self.game, {})["combat"]["actions"], 0)

    def test_cli_session_stream_and_policy_validation(self):
        for directory in ("/tmp/first", "/tmp/second"):
            with patch("sys.argv", ["crawl-agent", "--session-dir", directory, "--stream", "review",
                                    "combat", "--max-actions", "2", "--allow-status", "Drain"]), \
                    patch("dcss_harness.cli.output_request", return_value=0) as output:
                self.assertEqual(main(), 0)
                self.assertEqual(output.call_args.args[0], Path(directory))
                self.assertEqual(output.call_args.args[1]["policy"]["max_actions"], 2)
                self.assertEqual(output.call_args.args[2].stream, "review")
        for options in ({"max_actions": 33}, {"max_actions": True}, {"max_seconds": float("nan")},
                        {"min_hp_percent": 0}, {"max_threat": 5}, {"allow_status": "Drain"}, {"approach": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                run(self.game, options)
        self.game.send.assert_not_called()

    def test_null_public_tile_layers(self):
        self.assertEqual(monster_appearance({"t": {"mcache": None, "doll": None, "icons": None}})["visible_weapons"], [])

    def test_assessed_distant_examples_and_adjacent_selection(self):
        # Phantom southeast, assessed sleepcap six cells away. The same rule
        # permits the front yak while explicitly identified herd members wait.
        self.game.state.cells.clear()
        self.enemy(1, 1, 1, name="phantom")
        self.enemy(2, -6, 0, name="sleepcap")
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "distant_enemy")
        self.effect(lambda: None)
        result = run(self.game, {"assessed_distant_id": [2], "max_actions": 2})
        self.assertEqual(result["combat"]["stop_reason"], "action_limit")
        self.assertEqual([s["enemy_id"] for s in result["combat"]["steps"]], [1, 1])
        self.game.state.cells.clear()
        self.enemy(1, 1, -1, name="yak")
        self.enemy(2, 2, -2, name="yak")
        self.enemy(3, 3, -3, name="yak")
        self.enemy(4, -1, 0, name="yak")
        result = run(self.game, {"assessed_distant_id": [2, 3], "max_actions": 2})
        self.assertEqual([s["key"] for s in result["combat"]["steps"]], ["u", "u"])

    def test_distant_allowance_expiry_and_unchanged_guards(self):
        self.enemy(2, 3, 0)
        obs = self.game.observe()
        rules = policy({"assessed_distant_id": [2]})
        cases = [
            ("assessed_foe_adjacent", lambda o: o["monsters"][1].update(dx=-1)),
            ("enemy_lost", lambda o: o["monsters"].pop()),
            ("new_enemy", lambda o: o["monsters"][1].update(id=3)),
            ("enemy_changed", lambda o: o["monsters"][1].update(name="orc")),
            ("enemy_status", lambda o: o["monsters"][1].update(icons=["fleeing"])),
            ("unseen_threat", lambda o: o.update(unseen_threat={"reason": "attacker"})),
            ("low_hp", lambda o: o["player"].update(hp=80)),
            ("input_required", lambda o: o.update(input_mode="prompt")),
        ]
        for reason, change in cases:
            with self.subTest(reason=reason):
                current = copy.deepcopy(obs)
                change(current)
                self.assertEqual(guard(current, obs, rules, []), reason)
        for hint in ("ranged", "reaching"):
            current = copy.deepcopy(obs)
            current["monsters"][1]["visible_weapons"] = [{"attack_hint": hint}]
            self.assertEqual(guard(current, current, rules, []), "ranged_or_reaching_enemy")
        for ids in ([1], [99]):
            self.assertEqual(guard(obs, obs, policy({"assessed_distant_id": ids}), []), "invalid_distant_assessment")
        self.effect(lambda: self.game.state.cells.update({(-1, 0): self.game.state.cells.pop((3, 0))}))
        result = run(self.game, {"assessed_distant_id": [2]})
        self.assertEqual((result["combat"]["stop_reason"], result["combat"]["actions"]), ("assessed_foe_adjacent", 1))

    def test_distant_cli_and_invalid_policy(self):
        with patch("sys.argv", ["crawl-agent", "combat", "--assessed-distant-id", "9"]), \
                patch("dcss_harness.cli.output_request", return_value=0) as output:
            self.assertEqual(main(), 0)
            self.assertEqual(output.call_args.args[1]["policy"]["assessed_distant_id"], [9])
        for ids in (True, [True], [0], [1, 1], ["1"]):
            with self.assertRaises(ValueError):
                policy({"assessed_distant_id": ids})

    def test_transient_assessed_approach_latches(self):
        self.enemy(2, 3, 0)
        def approach_retreat():
            self.game.state.cells[(-1, 0)] = self.game.state.cells.pop((3, 0))
            self.game.state.apply({"msg": "map", "cells": []})
            self.game.state.cells[(3, 0)] = self.game.state.cells.pop((-1, 0))
            self.game.state.apply({"msg": "map", "cells": []})
        self.effect(approach_retreat)
        result = run(self.game, {"assessed_distant_id": [2]})
        self.assertEqual((result["combat"]["actions"], result["combat"]["stop_reason"]), (1, "assessed_foe_adjacent"))

    def test_post_send_error_records_step_and_clears_result_next_observation(self):
        def settle(**kwargs):
            if kwargs.get("require_event"):
                raise OSError("lost connection after sending")
        self.game.settle.side_effect = settle
        result = run(self.game, {})
        self.assertEqual(result["combat"]["stop_reason"], "action_error")
        self.assertEqual(result["combat"]["actions"], 1)
        self.assertEqual(action_stats(self.game.session / "actions.jsonl")["combat_steps"], 1)
        self.assertIsNone(compact_observation(self.game.observe(), result)["combat"])

    def test_settling_message_changes_and_hazard_span_underfoot(self):
        self.game.settle.side_effect = lambda **kw: self.game.state.apply({"msg": "msgs", "messages": [
            {"text": "The goblin speeds up."}]})
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "monster_change_message")
        self.game.send.assert_not_called()
        obs = self.game.observe()
        obs["visible_features"] = [{"kind": "hazard", "dx": -2, "dy": 0, "through": {"dx": 2, "dy": 0}}]
        self.assertEqual(guard(obs, obs, policy({}), []), "local_hazard")

    def test_inventory_resource_change_ranged_weapon_and_scenery(self):
        self.effect(lambda: self.game.state.player.update(inv={"52": {"name": "wand (2)", "quantity": 1}}))
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "resource_changed")
        self.game.state.player.update(weapon_index=0, inv={"0": {"name": "+0 sling", "quantity": 1}})
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "weapon_requires_decision")
        self.game.state.player.update(weapon_index=-1, inv={})
        from dcss_harness.safety import CONSTANTS
        self.game.state.cells[(1, 0)]["mon"].update(type=CONSTANTS["MONS_PLANT"], typedata={"no_exp": True})
        self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "no_visible_hostiles")

    def test_enemy_debuffs_require_explicit_allowance(self):
        from dcss_harness.safety import CONSTANTS
        tiles = self.game.state.cells[(1, 0)]["t"]
        for icons, fg in (([CONSTANTS["TILEI_DRAIN"]], 0), ([], [0, 0x08000000]),
                          ([], [0, 0x10000000]), ([], [0, 0x18000000])):
            with self.subTest(icons=icons, fg=fg):
                tiles.update(icons=icons, fg=fg)
                self.assertEqual(run(self.game, {})["combat"]["stop_reason"], "enemy_status")
        self.game.send.assert_not_called()

    def test_allowed_enemy_application_severity_and_expiry_continue(self):
        from dcss_harness.safety import CONSTANTS
        tiles = self.game.state.cells[(1, 0)]["t"]
        tiles['icons'] = [CONSTANTS['TILEI_DRAIN']]
        changes = iter([([], [0, 0x08000000]), ([], [0, 0x10000000]),
                        ([CONSTANTS['TILEI_DRAIN']], [0, 0x18000000]),
                        ([], 0), ([CONSTANTS['TILEI_DRAIN']], 0)])
        def change():
            icons, fg = next(changes)
            tiles.update(icons=icons, fg=fg)
        self.effect(change)
        result = run(self.game, {'max_actions': 5, 'allow_enemy_status': ['drain', 'poison']})
        self.assertEqual(result['combat']['stop_reason'], 'action_limit')
        self.assertEqual(result['combat']['actions'], 5)
        self.assertEqual(result['combat']['policy']['allow_enemy_status'], ['drain', 'poison'])

    def test_allowed_debuff_does_not_hide_other_enemy_status_or_change(self):
        from dcss_harness.safety import CONSTANTS
        original = copy.deepcopy(self.game.state)
        for dangerous in ('TILEI_HASTED', 'TILEI_BERSERK', 'TILEI_FRENZIED', 'TILEI_INNER_FLAME', None):
            with self.subTest(dangerous=dangerous):
                self.game.state = copy.deepcopy(original)
                tiles = self.game.state.cells[(1, 0)]['t']
                tiles['icons'] = [CONSTANTS['TILEI_DRAIN']]
                self.game.send.reset_mock()
                self.effect(lambda: tiles['icons'].append(CONSTANTS[dangerous] if dangerous else 999999))
                result = run(self.game, {'allow_enemy_status': ['drain', 'poison']})
                self.assertEqual(result['combat']['stop_reason'], 'enemy_status')
                self.assertEqual(self.game.send.call_count, 1)
                # An already combined state is also rejected before any key.
                self.game.send.reset_mock()
                self.assertEqual(run(self.game, {'allow_enemy_status': ['drain', 'poison']})['combat']['actions'], 0)
                self.game.send.assert_not_called()
        cases = [
            ('new_enemy', lambda: self.enemy(2, 0, 1)),
            ('enemy_changed', lambda: self.game.state.cells[(1, 0)]['mon'].update(type=999)),
            ('enemy_changed', lambda: self.game.state.cells[(1, 0)]['mon'].update(name='shapeshifter')),
            ('invisible_marker', lambda: self.game.state.cells[(1, 0)]['t'].update(bg=[0, 64])),
            ('no_visible_hostiles', lambda: self.game.state.cells.clear()),
            ('monster_change_message', lambda: self.game.state.apply({'msg': 'msgs', 'messages': [{'text': 'The goblin goes berserk!'}]})),
        ]
        for reason, change in cases:
            with self.subTest(reason=reason):
                self.game.state = copy.deepcopy(original)
                self.game.state.cells[(1, 0)]['t']['icons'] = [CONSTANTS['TILEI_DRAIN']]
                self.effect(change)
                self.assertEqual(run(self.game, {'allow_enemy_status': ['drain', 'poison']})['combat']['stop_reason'], reason)

    def test_only_chosen_debuff_is_allowed_and_unsafe_allowances_rejected(self):
        from dcss_harness.safety import CONSTANTS
        tiles = self.game.state.cells[(1, 0)]['t']
        tiles.update(icons=[CONSTANTS['TILEI_DRAIN']], fg=[0, 0x08000000])
        for allowance in ('drain', 'poison'):
            self.assertEqual(run(self.game, {'allow_enemy_status': [allowance]})['combat']['stop_reason'], 'enemy_status')
        for invalid in ('drain', ['hasted'], ['berserk'], ['unknown_icon:999999'], ['more_poison'], [None]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                run(self.game, {'allow_enemy_status': invalid})
        self.game.send.assert_not_called()

    def test_poison_public_flag_labels_and_expiry(self):
        from dcss_harness.safety import CONSTANTS
        for level, expected in ((0, []), (1, ['poison']), (2, ['more_poison']), (3, ['max_poison'])):
            with self.subTest(level=level):
                self.assertEqual(monster_appearance({'t': {'fg': [0, level << 27]}})['icons'], expected)
        self.assertEqual(monster_appearance({'t': {'fg': [0, 0x08000000], 'icons': [CONSTANTS['TILEI_POISON']]}})['icons'], ['poison'])

    def test_enemy_allowance_cli_keeps_player_allowance_separate(self):
        with patch('sys.argv', ['crawl-agent', '--session-dir', '/tmp/allowance', '--stream', 'fight',
                'combat', '--allow-status', 'Drain', '--allow-enemy-status', 'drain', '--allow-enemy-status', 'poison']), \
                patch('dcss_harness.cli.output_request', return_value=0) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(output.call_args.args[0], Path('/tmp/allowance'))
        self.assertEqual(output.call_args.args[1]['policy']['allow_enemy_status'], ['drain', 'poison'])
        self.assertEqual(output.call_args.args[1]['policy']['allow_status'], ['Drain'])

    def test_debris_is_never_attacked_after_real_enemy_dies(self):
        from dcss_harness.safety import CONSTANTS
        self.enemy(2, -1, 0, name='pile of debris', type=CONSTANTS['MONS_PILE_OF_DEBRIS'], typedata={'no_exp': True})
        self.effect(lambda: self.game.state.cells.pop((1, 0)))
        result = run(self.game, {})
        self.assertEqual(result['combat']['stop_reason'], 'no_visible_hostiles')
        self.assertEqual(result['combat']['actions'], 1)
        self.game.send.assert_called_once_with({'msg': 'key', 'keycode': ord('l')})
        self.assertEqual(result['scenery'][0]['id'], 2)
        self.game.send.reset_mock()
        self.assertEqual(run(self.game, {})['combat']['actions'], 0)
        self.game.send.assert_not_called()

    def test_flower_is_never_attacked_after_real_enemy_dies(self):
        from dcss_harness.safety import CONSTANTS
        self.enemy(2, -1, 0, name='petrified flower', type=CONSTANTS['MONS_PETRIFIED_FLOWER'], typedata={'no_exp': True})
        self.effect(lambda: self.game.state.cells.pop((1, 0)))
        result = run(self.game, {})
        self.assertEqual(result['combat']['stop_reason'], 'no_visible_hostiles')
        self.assertEqual(result['combat']['actions'], 1)
        self.game.send.assert_called_once_with({'msg': 'key', 'keycode': ord('l')})
        self.assertEqual(result['scenery'][0]['id'], 2)


if __name__ == "__main__":
    unittest.main()
