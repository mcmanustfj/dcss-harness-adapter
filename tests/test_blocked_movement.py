import copy
import json
import unittest

from dcss_harness.map import FEATURES


class BlockedMovementTests(unittest.TestCase):
    def setUp(self):
        from test_reliability import SettlingTests
        fixture = SettlingTests()
        self.addCleanup(fixture.doCleanups)
        self.game, self.writer = fixture.interactive_game()
        self.ids = {v['id']: k for k, v in FEATURES.items()}
        self.game.state.player.update(place='Swamp', depth=1, inv={}, name='Test')
        self.game.state.cells = {(0, 0): {'f': self.ids['floor'], 'g': '@', 't': {'bg': 0}},
                                 (0, -1): {'f': self.ids['tree'], 'g': '♣', 't': {'bg': 0}}}

    def frame(self, events, flush=True):
        if flush:
            events += [{'msg': 'flush_messages'}]
        self.writer.send(('\n'.join(json.dumps(e) for e in events) + '\n').encode())

    def refresh(self, flush=True, changed=None):
        player = copy.deepcopy(self.game.state.player)
        player.update(changed or {})
        self.frame([{'msg': 'version', 'text': 'test'},
                    {'msg': 'player', **player},
                    {'msg': 'map', 'clear': True, 'cells': [
                        {'x': x, 'y': y, **copy.deepcopy(c)} for (x, y), c in self.game.state.cells.items()]},
                    {'msg': 'ui-stack', 'items': []}, {'msg': 'input_mode', 'mode': 1}], flush)

    def test_tree_and_wall_noops_recover_without_retry_or_turn(self):
        for feature in ('tree', 'rock_wall', 'clear_rock_wall'):
            with self.subTest(feature=feature):
                self.game.state.cells[(0, -1)]['f'] = self.ids[feature]
                self.game.send.reset_mock()
                self.game.send.side_effect = lambda m: self.refresh() if m['msg'] == 'spectator_joined' else None
                result = self.game.act([ord('k')])
                self.assertTrue(result['settled'])
                self.assertEqual(result['settle_reason'], 'blocked_move_resynchronized')
                self.assertEqual((result['keys_sent'], result['player']['turn']), (1, 100))
                self.assertEqual([c.args[0] for c in self.game.send.call_args_list],
                                 [{'msg': 'key', 'keycode': ord('k')}, {'msg': 'spectator_joined'}])

    def test_silent_or_partial_probe_stays_unsettled_until_complete(self):
        result = self.game.act([ord('k')])
        self.assertFalse(result['settled'])
        self.frame([{'msg': 'map', 'cells': []}])
        self.game.settle()
        self.assertFalse(self.game.settled)
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            self.game.act([ord('.')])
        self.refresh(flush=False)
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.frame([])
        self.game.settle()
        self.assertTrue(self.game.settled)
        self.assertEqual(self.game.send.call_count, 2)

    def test_full_probe_needs_inventory_map_stack_and_mode(self):
        cells = copy.deepcopy(self.game.state.cells)
        self.game.act([ord('k')])
        self.frame([{'msg': 'version', 'text': 'test'}, {'msg': 'player', 'turn': 100},
                    {'msg': 'map', 'clear': True, 'cells': []}])
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.game.state.cells = cells
        self.refresh()
        self.game.settle()
        self.assertTrue(self.game.settled)

    def test_ordinary_moves_doors_monsters_and_remembered_cells_get_no_allowance(self):
        cases = [{'f': self.ids['floor']}, {'f': self.ids['closed_door']},
                 {'f': 999999}, {'t': {'bg': 0x40000}},
                 {'mon': {'id': 1, 'att': 0, 'name': 'orc', 'type': 1, 'threat': 0}}]
        for change in cases:
            with self.subTest(change=change):
                self.game.state.input_baseline = None
                self.game.state.public_refresh = None
                self.game.settled = True
                self.game.state.cells[(0, -1)] = {'f': self.ids['tree'], 't': {'bg': 0}, **change}
                self.game.send.reset_mock()
                result = self.game.act([ord('k')])
                self.assertFalse(result['settled'])
                self.game.send.assert_called_once_with({'msg': 'key', 'keycode': ord('k')})

    def test_turn_advance_is_reported_as_action_not_noop(self):
        self.game.send.side_effect = lambda m: self.refresh(changed={'turn': 101}) if m['msg'] == 'spectator_joined' else None
        result = self.game.act([ord('k')])
        self.assertTrue(result['settled'])
        self.assertEqual(result['player']['turn'], 101)
        self.assertNotEqual(result['settle_reason'], 'blocked_move_resynchronized')

    def test_public_refresh_requires_complete_frame_without_sending_keys(self):
        self.game.send.side_effect = lambda m: self.refresh() if m['msg'] == 'spectator_joined' else None
        result = self.game.refresh_observation()
        self.assertTrue(result['settled'])
        self.game.send.assert_called_once_with({'msg': 'spectator_joined'})
        self.game.send.reset_mock()
        self.game.send.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'refresh incomplete'):
            self.game.refresh_observation()
        self.assertFalse(self.game.settled)
        self.game.send.assert_called_once_with({'msg': 'spectator_joined'})

    def test_public_refresh_never_acknowledges_new_pagination(self):
        self.game.state.player['hp'] = 10
        def respond(message):
            self.assertEqual(message, {'msg': 'spectator_joined'})
            self.refresh()
            self.frame([{'msg': 'input_mode', 'mode': 5}, {'msg': 'msgs', 'more': True, 'messages': []}])
        self.game.send.side_effect = respond
        result = self.game.refresh_observation()
        self.assertTrue(result['more'])
        self.game.send.assert_called_once_with({'msg': 'spectator_joined'})


if __name__ == '__main__':
    unittest.main()
