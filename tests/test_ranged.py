import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.game import Game
from dcss_harness.state import State
from dcss_harness.cli import main
from dcss_harness.presentation import compact_observation
from dcss_harness.client import compatibility_error
from dcss_harness.ranged import policy, run
from dcss_harness.safety import CONSTANTS

FIXTURE = Path(__file__).parent / 'fixtures/ranged-live.json'
OPTIONS = dict(wand_letter='a', expect_name='wand of flame (15)', monster_id=1)


class Replay(Game):
    def __init__(self, frames=None, mutate=None, timeout=None, fail_send=None):
        self.frames = copy.deepcopy(frames or json.loads(FIXTURE.read_text())['frames'])
        self.state = State()
        for event in self.frames.pop(0)['events']:
            self.state.apply(event)
        self.session = Path('/tmp/ranged-test')
        self.process = SimpleNamespace(poll=lambda: None)
        self.actions = Mock(count=0)
        self.settled = True
        self.sent = []
        self.pending = None
        self.mutate, self.timeout_at, self.fail_send = mutate, timeout, fail_send

    def send(self, message):
        self.sent.append(message)
        if self.fail_send == len(self.sent):
            raise OSError('ambiguous transport failure')
        self.pending = self.frames.pop(0)
        assert message == self.pending['input'], (message, self.pending['input'])

    def settle_input(self, **kwargs):
        if self.pending:
            for event in self.pending['events']:
                self.state.apply(event)
            self.pending = None
            if self.mutate:
                self.mutate(self.state, len(self.sent))
            self.settled = len(self.sent) != self.timeout_at


class RangedTests(unittest.TestCase):
    def check(self, game, reason, submitted=False, **options):
        result = run(game, {**OPTIONS, **options})
        self.assertEqual(result['ranged']['stop_reason'], reason, result['ranged'])
        self.assertEqual(result['ranged']['submitted'], submitted)
        self.assertEqual(sum(m.get('keycode') == 13 for m in game.sent), int(submitted))
        return result

    def test_recorded_shot_once_preserves_messages_and_resulting_movement(self):
        game = Replay()
        result = self.check(game, 'shot_resolved', True)
        info = result['ranged']
        self.assertEqual((info['charges_used'], info['turns'], info['keys_sent']), (1, 1, 3))
        self.assertTrue(info['resource_consumed'])
        self.assertEqual(info['resolved_target']['y'], -6)
        self.assertEqual(result['monsters'][0]['y'], -5)
        self.assertTrue(any('Aiming:' in m['text'] for m in result['messages']))
        self.assertTrue(any('misses the kobold' in m['text'] for m in result['messages']))
        game.actions.record.assert_called_once()
        self.assertIsNone(compact_observation(game.observe(), result)['ranged'])

    def test_prepare_only_recenters_even_when_default_target_matches(self):
        game = Replay()
        result = self.check(game, 'prepared', prepare_only=True)
        self.assertEqual([m['msg'] for m in game.sent], ['key', 'key', 'target_cursor', 'target_cursor'])
        self.assertEqual(result['ranged']['turns'], 0)
        self.assertFalse(result['ranged']['resource_consumed'])
        self.assertEqual(result['targeting']['selected_monster']['id'], 1)

    def test_stale_name_namespace_and_missing_target_send_nothing(self):
        for options, reason in [({'expect_name': 'wand of flame (14)'}, 'resource_identity_mismatch'),
                                ({'wand_letter': 'b'}, 'resource_unavailable'),
                                ({'monster_id': 99}, 'target_lost')]:
            with self.subTest(options=options):
                game = Replay()
                self.check(game, reason, **options)
                self.assertEqual(game.sent, [])
        game = Replay()
        game.state.inventory_refresh.add('52')
        self.check(game, 'resource_unavailable')
        self.assertEqual(game.sent, [])

    def test_menu_category_letter_and_identity_changes_stop_before_selection(self):
        for field, value in [('title', {'text': 'Quaff which potion?'}),
                             ('items', [{'level': 1, 'text': 'Potions'},
                                        {'level': 2, 'hotkeys': [97], 'text': ' a - a wand of flame (15)'}]),
                             ('items', [{'level': 1, 'text': 'Wands'},
                                        {'level': 2, 'hotkeys': [97], 'text': ' a - a wand of iceblast (15)'}])]:
            with self.subTest(field=field, value=value):
                def mutate(s, n):
                    if n == 1:
                        s.ui[-1][field] = value
                game = Replay(mutate=mutate)
                self.check(game, 'item_menu_mismatch')
                self.assertEqual(len(game.sent), 1)

    def test_target_rejection_blocked_out_of_range_and_unknown_preview(self):
        def update(s, kind):
            if kind == 'target_mismatch':
                s.cursors[0] = {'x': 0, 'y': 0}
            elif kind == 'target_lost':
                s.cells[(3, -6)]['mon'] = None
            elif kind == 'out_of_range_or_invalid':
                s.cells[(3, -6)]['t']['bg'] = 0x2000000
            elif kind == 'line_of_fire_blocked':
                s.apply({'msg': 'msgs', 'messages': [{'text': 'Aim: kobold (fire blocked by a wall)'}]})
            elif kind == 'target_not_in_positive_preview':
                s.cells[(3, -6)]['t']['ov'] = [CONSTANTS['TILE_RAY_OUT_OF_RANGE']]
        for reason in ('target_mismatch', 'target_lost', 'out_of_range_or_invalid',
                       'line_of_fire_blocked', 'target_not_in_positive_preview'):
            with self.subTest(reason=reason):
                game = Replay(mutate=lambda s, n: update(s, reason) if n == 4 else None)
                self.check(game, reason)

    def test_public_invalid_square_stops_before_any_cursor_move(self):
        def invalid(s, n):
            if n == 2:
                s.cells[(3, -6)]['t']['bg'] = 0x2000000
                s.cursors[0] = {'x': 0, 'y': 0}  # Current cursor itself is valid.
        game = Replay(mutate=invalid)
        result = self.check(game, 'out_of_range_or_invalid')
        self.assertEqual([m['keycode'] for m in game.sent], [ord('V'), ord('a')])
        self.assertEqual(result['ranged']['turns'], 0)
        self.assertEqual(result['ranged']['charges_used'], 0)

    def test_preview_exposure_requires_separate_explicit_allowances(self):
        for kind, flag, reason in [('self', 'allow_self', 'self_exposure'),
                                   ('friend', 'allow_friendly', 'friendly_exposure'),
                                   ('hostile', 'allow_area', 'area_exposure')]:
            def mutate(s, n):
                if n != 4:
                    return
                if kind == 'self':
                    s.cells[(0, 0)]['t']['ov'] = [CONSTANTS['TILE_RAY']]
                else:
                    s.cells[(2, -6)] = {'t': {'bg': 0, 'ov': [CONSTANTS['TILE_RAY']]},
                                       'mon': {'id': 44, 'name': 'orc', 'att': 1 if kind == 'friend' else 0}}
            with self.subTest(kind=kind):
                self.check(Replay(mutate=mutate), reason, prepare_only=True)
                self.check(Replay(mutate=mutate), 'prepared', prepare_only=True, **{flag: True})

    def test_intervening_plant_blocks_even_with_area_permission(self):
        def mutate(s, n):
            if n == 4:
                s.cells[(1, -1)]['mon'] = {'id': 44, 'name': 'plant', 'att': 0,
                    'type': CONSTANTS['MONS_PLANT'], 'threat': 0, 'typedata': {'no_exp': True}}
        result = self.check(Replay(mutate=mutate), 'possible_interception', allow_area=True)
        self.assertEqual(result['scenery'][0]['id'], 44)
        self.assertEqual(result['ranged']['exposure']['intervening_occupants'], [44])

    def test_unseen_preview_is_not_covered_by_area_allowance(self):
        def mutate(s, n):
            if n == 4:
                s.cells[(1, -1)]['t']['bg'] = 0x40000
        self.check(Replay(mutate=mutate), 'unseen_preview', allow_area=True, allow_self=True, allow_friendly=True)

    def test_unexpected_prompt_and_transient_damage_stop_preparation(self):
        def prompt(s, n):
            if n == 2:
                s.apply({'msg': 'input_mode', 'mode': 8})
        self.check(Replay(mutate=prompt), 'unexpected_prompt')
        def damage(s, n):
            if n == 2:
                hp = s.player['hp']
                s.apply({'msg': 'player', 'hp': hp - 1})
                s.apply({'msg': 'player', 'hp': hp})
        self.check(Replay(mutate=damage), 'player_changed_during_preparation')

    def test_each_preparation_timeout_stops_without_firing(self):
        for n in range(1, 5):
            with self.subTest(n=n):
                self.check(Replay(timeout=n), 'unsettled')

    def test_submission_timeout_and_send_error_are_never_retried(self):
        frames = json.loads(FIXTURE.read_text())['frames']
        frames[-1]['events'] = []
        game = Replay(frames=frames, timeout=5)
        result = self.check(game, 'submission_uncertain', True)
        self.assertIsNone(result['ranged']['resource_consumed'])
        game = Replay(fail_send=5)
        result = self.check(game, 'submission_uncertain', True)
        self.assertIsNone(result['ranged']['resource_consumed'])

    def test_deadline_before_enter_does_not_claim_submission(self):
        game = Replay()
        with patch('dcss_harness.ranged.time.monotonic', side_effect=[0] * 11 + [11, 11]):
            self.check(game, 'time_limit')
        self.assertEqual(len(game.sent), 4)

    def test_post_submission_confirmation_is_returned_without_answering(self):
        def mutate(s, n):
            if n == 5:
                s.apply({'msg': 'input_mode', 'mode': 8})
        self.check(Replay(mutate=mutate), 'input_required_after_submission', True)

    def test_last_charge_disappearing_wand_is_reported_consumed(self):
        frames = json.loads(FIXTURE.read_text())['frames']
        frames = json.loads(json.dumps(frames).replace('flame (15)', 'flame (1)'))
        frames[0]['events'][0]['inv']['52']['plus'] = 1
        frames[-1]['events'].append({'msg': 'player', 'inv': {'52': {'quantity': 0}}})
        result = self.check(Replay(frames=frames), 'shot_resolved', True, expect_name='wand of flame (1)')
        self.assertTrue(result['ranged']['resource_consumed'])
        self.assertEqual(result['ranged']['charges_used'], 1)

    def launcher(self):
        frames = json.loads(FIXTURE.with_name('ranged-launcher-live.json').read_text())['frames']
        game = Replay(frames=frames)
        return game, dict(wand_letter=None, current_quiver='Fire: a) +0 shortbow',
                          weapon_letter='a', expect_name='+0 shortbow')

    def test_current_launcher_requires_wielded_enabled_same_quiver(self):
        game, options = self.launcher()
        self.check(game, 'prepared', prepare_only=True, **options)
        self.assertEqual(game.sent[0], {'msg': 'key', 'keycode': 102})
        game, options = self.launcher()
        result = self.check(game, 'shot_resolved', True, **options)
        self.assertFalse(result['ranged']['resource_consumed'])
        for field, value in [('weapon_index', 1), ('quiver_item', 52), ('quiver_available', 0),
                             ('quiver_desc', 'Zap: wand of flame (15)')]:
            game, options = self.launcher()
            game.state.player[field] = value
            self.check(game, 'launcher_or_quiver_mismatch', **options)
            self.assertEqual(game.sent, [])

    def test_cli_and_legacy_daemon_validation(self):
        with patch('sys.argv', ['crawl-agent', '--session-dir', '/tmp/ranged', 'ranged', '--wand-letter', 'a',
                               '--expect-name', 'wand of flame (15)', '--monster-id', '1', '--prepare-only']), \
                patch('dcss_harness.cli.output_request', return_value=0) as output:
            self.assertEqual(main(), 0)
        request = output.call_args.args[1]
        self.assertEqual(request['op'], 'ranged')
        self.assertNotIn('weapon_letter', request['policy'])
        self.assertTrue(request['policy']['prepare_only'])
        self.assertEqual(compatibility_error(request, {'error': 'Unknown operation'})['error_code'], 'restart_required')
        for changes in ({'max_seconds': float('nan')}, {'monster_id': True}, {'expect_name': ''},
                        {'allow_self': 1}, {'weapon_letter': 'a'}, {'wand_letter': 'ab'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                policy({**OPTIONS, **changes})


if __name__ == '__main__':
    unittest.main()
