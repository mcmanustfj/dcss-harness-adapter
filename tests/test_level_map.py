import unittest

from dcss_harness.state import State
from dcss_harness.presentation import compact_observation
from dcss_harness.map import FEATURES


class LevelMapTests(unittest.TestCase):
    def setUp(self):
        self.state = s = State()
        s.apply({'msg': 'player', 'pos': {'x': 0, 'y': 0}, 'place': 'Dungeon', 'depth': 1})
        stair = next(k for k, v in FEATURES.items() if v['id'] == 'stone_stairs_up_i')
        s.apply({'msg': 'map', 'clear': True, 'player_on_level': True, 'cells': [
            {'x': 0, 'y': 0, 'g': '<', 'f': stair, 't': {'bg': 0}},
            {'x': 9, 'y': 3, 'g': '<', 'f': stair, 't': {'bg': 0x20000}}]})
        # Native X sends the map cursor before ui_state, with input mode zero.
        s.apply({'msg': 'input_mode', 'mode': 0})
        s.apply({'msg': 'cursor', 'id': 2, 'loc': {'x': 0, 'y': 0}})
        s.apply({'msg': 'ui_state', 'state': 2})

    def test_identical_stairs_distinguished_without_new_messages(self):
        before = self.state.observation()
        self.state.apply({'msg': 'cursor', 'id': 2, 'loc': {'x': 9, 'y': 3}})
        after = self.state.observation()
        delta = compact_observation(after, before)
        selected = delta['level_map']
        self.assertEqual(selected['cursor'], {'x': 9, 'y': 3, 'dx': 9, 'dy': 3, 'visibility': 'remembered'})
        self.assertEqual(selected['selected_feature'], before['level_map']['selected_feature'])
        self.assertEqual(delta['messages'], [])
        self.assertIsNone(after['targeting'])
        self.assertNotIn('level_map', compact_observation(after, after))
        self.assertEqual(compact_observation(after, after, full=True)['level_map'], selected)

    def test_exit_reentry_never_reuses_old_selection(self):
        self.state.apply({'msg': 'ui_state', 'state': 0})
        self.assertIsNone(self.state.observation()['level_map'])
        self.state.apply({'msg': 'ui_state', 'state': 2})
        self.assertIsNone(self.state.observation()['level_map']['cursor'])
        self.state.apply({'msg': 'cursor', 'id': 2, 'loc': {'x': 0, 'y': 0}})
        self.assertEqual(self.state.observation()['level_map']['cursor']['visibility'], 'visible')

    def test_full_map_and_depth_reset_clear_previous_coordinates(self):
        self.state.apply({'msg': 'player', 'depth': 2})
        self.assertIsNone(self.state.observation()['level_map']['cursor'])
        self.state.apply({'msg': 'cursor', 'id': 2, 'loc': {'x': 9, 'y': 3}})
        self.state.apply({'msg': 'map', 'clear': True, 'player_on_level': True, 'cells': []})
        self.assertIsNone(self.state.observation()['level_map']['cursor'])
        self.state.apply({'msg': 'cursor', 'id': 2, 'loc': {'x': 1, 'y': 1}})
        selected = self.state.observation()['level_map']
        self.assertEqual(selected['cursor']['visibility'], 'unknown')
        self.assertIsNone(selected['selected_feature'])

    def test_offlevel_view_never_implies_player_relative_route_or_visibility(self):
        self.state.apply({'msg': 'map', 'player_on_level': False})
        selected = self.state.observation()['level_map']
        self.assertEqual(selected['cursor']['visibility'], 'remembered')
        self.assertIsNone(selected['level'])
        self.assertNotIn('dx', selected['cursor'])
        self.assertNotIn('navigation', selected['selected_feature'])

    def test_description_and_targeting_cursor_never_invent_map_selection(self):
        self.state.apply({'msg': 'cursor', 'id': 2})
        self.state.apply({'msg': 'cursor', 'id': 0, 'loc': {'x': 9, 'y': 3}})
        self.state.apply({'msg': 'msgs', 'messages': [{'text': 'A staircase leading upwards.'}]})
        selected = self.state.observation()['level_map']
        self.assertIsNone(selected['cursor'])
        self.assertIsNone(selected['selected_feature'])


if __name__ == '__main__':
    unittest.main()
