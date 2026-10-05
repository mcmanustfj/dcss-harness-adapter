import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.presentation import emit_observation
from dcss_harness.cli import main
from dcss_harness.checkpoint import run, signature, differences


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.session = Path(directory.name)
        self.runtime = self.session / "runtime"
        self.runtime.mkdir()
        (self.session / "saves").mkdir()
        (self.session / "saves/Test.cs").write_bytes(b"native-save-fixture")
        self.config = {"name": "Test", "seed": 123, "weapon": "war axe"}
        self.obs = {"running": True, "settled": True, "startup": None, "input_mode": "command",
                    "ui": [], "more": False, "exit_reason": {"type": "unknown"},
                    "monsters": [], "messages": [], "inventory": [],
                    "player": {"name": "Test", "place": "Dungeon", "depth": 1, "turn": 37,
                               "xl": 1, "hp": 19, "hp_max": 19, "mp": 0, "mp_max": 0}}
        self.game = self.make_game()
        def save(*args):
            self.game.process.poll.return_value = 0
            self.game.process.returncode = 0
            self.game.state.exit_reason = {"type": "saved"}
        self.game.act.side_effect = save
        self.new = self.make_game()
        self.factory = Mock(return_value=self.new)

    def make_game(self):
        return SimpleNamespace(session=self.session, settle_input=Mock(), observe=Mock(side_effect=lambda: copy.deepcopy(self.obs)),
                               process=SimpleNamespace(poll=Mock(return_value=None), returncode=None),
                               state=SimpleNamespace(exit_reason={"type": "unknown"}), act=Mock(),
                               drain=Mock(), close=Mock(), attach=Mock(), actions=Mock(),
                               checkpoint_weapon_titles=Mock(return_value={}),
                               refresh_observation=Mock(side_effect=lambda: copy.deepcopy(self.obs)))

    def test_save_reload_verified_same_options_no_action_retry(self):
        game, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertIs(game, self.new)
        self.game.act.assert_called_once_with([19], "checkpoint_save")
        self.game.close.assert_called_once()
        self.factory.assert_called_once_with(self.session, self.runtime, self.config)
        self.new.attach.assert_called_once()
        self.assertTrue(result["checkpoint"]["verified"])
        self.assertFalse(result["checkpoint"]["adapter_reloaded"])
        self.assertEqual(result["checkpoint"]["differences"], [])
        journal = json.loads((self.session / "checkpoint.json").read_text())
        self.assertEqual(journal["before"]["player"]["turn"], 37)
        self.assertEqual(journal["phase"], "complete")

    def test_preflight_rejects_prompt_uncertainty_and_incomplete_state(self):
        for change in ({"input_mode": "targeting"}, {"settled": False}, {"running": False},
                       {"startup": {"missing": ["inventory"]}}, {"more": True}, {"ui": [{}]}):
            with self.subTest(change=change):
                original = self.obs
                self.obs = {**self.obs, **change}
                game, result = run(self.game, self.runtime, self.config, self.factory)
                self.assertFalse(result["checkpoint"]["save_submitted"])
                self.assertIs(game, self.game)
                self.obs = original
        self.game.act.assert_not_called()
        self.factory.assert_not_called()

    def test_save_failure_never_restarts_or_resubmits(self):
        self.game.act.side_effect = OSError("lost response")
        game, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertIs(game, self.game)
        self.assertFalse(result["checkpoint"]["verified"])
        self.assertEqual(result["checkpoint"]["failed_phase"], "saving")
        self.game.act.assert_called_once()
        self.game.close.assert_not_called()
        self.factory.assert_not_called()

    def test_missing_save_and_wrong_exit_never_launch_new_character(self):
        (self.session / "saves/Test.cs").unlink()
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertIn("missing", result["checkpoint"]["error"])
        self.factory.assert_not_called()
        self.game.act.side_effect = lambda *args: setattr(self.game.state, "exit_reason", {"type": "dead"})
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertIn("not confirmed", result["checkpoint"]["error"])
        self.factory.assert_not_called()

    def test_reload_failure_and_rollback_are_reported(self):
        self.factory.side_effect = OSError("cannot launch")
        game, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertIsNone(game)
        self.assertEqual(result["checkpoint"]["failed_phase"], "reloading")
        self.assertIn("error", result)
        self.factory.side_effect = None
        self.new.observe.side_effect = lambda: {**copy.deepcopy(self.obs), "player": {**self.obs["player"], "turn": 20}}
        self.new.refresh_observation.side_effect = self.new.observe.side_effect
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertFalse(result["checkpoint"]["verified"])
        self.assertEqual(result["checkpoint"]["differences"], [{"field": "player.turn", "before": 37, "after": 20}])

    def test_origin_reset_allowed_inventory_change_detected(self):
        before = copy.deepcopy(self.obs)
        after = copy.deepcopy(self.obs)
        before["player"]["pos"] = {"x": 5, "y": 8}
        after["player"]["pos"] = {"x": 0, "y": 0}
        self.assertEqual(differences(signature(before), signature(after)), [])
        after["inventory"] = [{"slot": 1, "name": "changed"}]
        self.assertEqual(differences(signature(before), signature(after))[0]["field"], "inventory.item")

    def test_native_generation_resets_all_reader_baselines(self):
        (self.session / "runtime.json").write_text('{}')
        def emit(generation, stream):
            output = io.StringIO()
            with redirect_stdout(output):
                emit_observation(self.session, {**self.obs, "game_generation": generation}, stream=stream, snapshot_tags=False)
            return json.loads(output.getvalue())
        for stream in ("default", "review"):
            self.assertEqual(emit("first", stream)["observation"], "full")
            self.assertEqual(emit("first", stream)["observation"], "delta")
            self.assertEqual(emit("second", stream)["observation"], "full")

    def test_cli_uses_observation_path(self):
        with patch("sys.argv", ["crawl-agent", "--session", "check", "checkpoint"]), \
                patch("dcss_harness.cli.output_request", return_value=0) as output:
            self.assertEqual(main(), 0)
            self.assertEqual(output.call_args.args[1], {"op": "checkpoint"})

    def test_authoritative_refresh_resolves_recorded_name_variation_before_save(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/checkpoint-name-variation.json').read_text())
        self.obs['inventory'] = [fixture['before']]
        refreshed = {**copy.deepcopy(self.obs), 'inventory': [fixture['after']]}
        self.game.refresh_observation.return_value = refreshed
        self.game.refresh_observation.side_effect = None
        self.new.refresh_observation.return_value = refreshed
        self.new.refresh_observation.side_effect = None
        game, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertTrue(result['checkpoint']['verified'])
        self.assertEqual(result['checkpoint']['inventory_comparison'], 'fresh_public_and_exact_weapon_descriptions')
        self.game.refresh_observation.assert_called_once()
        self.new.refresh_observation.assert_called_once()
        self.game.act.assert_called_once_with([19], 'checkpoint_save')
        self.assertEqual(json.loads((self.session / 'checkpoint.json').read_text())['before']['inventory'], [fixture['after']])

    def test_names_properties_charges_counts_and_equipment_still_fail_exactly(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/checkpoint-name-variation.json').read_text())
        self.obs['inventory'] = [fixture['after']]
        before = signature(self.obs)
        for name in (fixture['before']['name'], '+7 eveningstar of Another Name {chaos}',
                     fixture['after']['name'].replace('rC++', 'rC+'), 'wand of flame (2)'):
            after = copy.deepcopy(before)
            after['inventory'][0]['name'] = name
            changed = differences(before, after)
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]['field'], 'inventory.name')
            self.assertEqual(changed[0]['slot'], 24)
        after = copy.deepcopy(before)
        after['inventory'][0]['quantity'] += 1
        after['player']['weapon_index'] = 24
        self.assertEqual({c['field'] for c in differences(before, after)}, {'inventory.quantity', 'player.weapon_index'})

    def test_description_proof_reconciles_only_display_name(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/checkpoint-name-variation.json').read_text())
        before = {'player': {}, 'inventory': [fixture['before']]}
        after = {'player': {}, 'inventory': [fixture['after']]}
        titles = {'24': 'y - ' + fixture['after']['name'] + '.'}
        self.assertEqual(differences(before, after, titles, titles), [])
        for proof in ({}, {'24': 'y - a different weapon.'}):
            self.assertTrue(differences(before, after, titles, proof))
        for field, value in [('name', fixture['after']['name'].replace('+7', '+8')),
                             ('name', fixture['after']['name'].replace('rC++', 'rC+')),
                             ('quantity', 2), ('slot', 25), ('name_current', False)]:
            changed = copy.deepcopy(after)
            changed['inventory'][0][field] = value
            self.assertTrue(differences(before, changed, titles, titles))

    def test_description_failure_stops_before_save(self):
        self.game.checkpoint_weapon_titles.side_effect = RuntimeError('Description incomplete')
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertFalse(result['checkpoint']['save_submitted'])
        self.assertEqual(result['checkpoint']['failed_phase'], 'describing_before_save')
        self.game.act.assert_not_called()

    def test_one_changed_slot_does_not_dump_unchanged_inventory(self):
        self.obs['inventory'] = [{'slot': i, 'letter_namespace': 'equipment', 'name': f'item {i}', 'quantity': 1,
                                  'name_current': True} for i in range(63)]
        after = copy.deepcopy(self.obs)
        after['inventory'][24]['name'] = 'different item'
        self.new.refresh_observation.side_effect = lambda: copy.deepcopy(after)
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertFalse(result['checkpoint']['verified'])
        self.assertEqual(result['checkpoint']['differences'], [{'field': 'inventory.name', 'slot': 24,
            'letter_namespace': 'equipment', 'before': 'item 24', 'after': 'different item'}])
        self.assertLess(len(json.dumps(result['checkpoint']['differences'])), 200)

    def test_namespace_collisions_missing_fields_and_order_remain_explicit(self):
        self.obs['inventory'] = [{'slot': 52, 'letter_namespace': ns, 'name': ns, 'quantity': 1}
                                 for ns in ('potions', 'scrolls')]
        before = signature(self.obs)
        after = copy.deepcopy(before)
        after['inventory'][1]['name'] = 'changed scroll'
        self.assertEqual(differences(before, after)[0]['letter_namespace'], 'scrolls')
        after = copy.deepcopy(before)
        after['inventory'][0]['letter'] = None
        self.assertFalse(differences(before, after)[0]['before_present'])
        after = copy.deepcopy(before)
        after['inventory'].reverse()
        self.assertEqual(differences(before, after)[0]['field'], 'inventory.order')

    def test_incomplete_refresh_never_saves_or_retries(self):
        self.game.refresh_observation.side_effect = RuntimeError('Full public refresh incomplete')
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertFalse(result['checkpoint']['save_submitted'])
        self.assertEqual(result['checkpoint']['failed_phase'], 'refreshing_before_save')
        self.game.act.assert_not_called()
        self.factory.assert_not_called()
        self.game.refresh_observation.side_effect = lambda: copy.deepcopy(self.obs)
        self.new.refresh_observation.side_effect = RuntimeError('Full public refresh incomplete')
        _, result = run(self.game, self.runtime, self.config, self.factory)
        self.assertFalse(result['checkpoint']['verified'])
        self.assertEqual(result['checkpoint']['failed_phase'], 'refreshing_after_reload')
        self.game.act.assert_called_once()


if __name__ == "__main__":
    unittest.main()
