import unittest

from dcss_harness.state import State
from dcss_harness.safety import CONSTANTS, monster_appearance
from dcss_harness.combat import guard, policy


class FleeingTests(unittest.TestCase):
    def test_native_behaviour_field_scalar_and_split_words(self):
        for fg in (0x300012, [0x300012, 0], [0x300012, 0x80000000]):
            with self.subTest(fg=fg):
                self.assertIn('fleeing', monster_appearance({'t': {'fg': fg, 'icons': []}})['icons'])
        for fg in (0, 0x100000, 0x200000, 0x400000, 0x500000, 0x700000):
            self.assertNotIn('fleeing', monster_appearance({'t': {'fg': fg}})['icons'])
        icons = monster_appearance({'t': {'fg': 0x300012, 'icons': [CONSTANTS['TILEI_FLEEING']]}})['icons']
        self.assertEqual(icons.count('fleeing'), 1)

    def test_application_expiry_and_reacquired_sighting(self):
        state = State()
        state.player = {'pos': {'x': 0, 'y': 0}}
        def packet(identity, fg):
            state.apply({'msg': 'map', 'cells': [{'x': 1, 'y': 0, 't': {'bg': 0, 'fg': fg},
                'mon': {'id': identity, 'type': 1, 'name': 'black mamba', 'att': 0, 'threat': 1}}]})
        packet(101, 0x300012)
        self.assertIn('fleeing', state.observation()['monsters'][0]['icons'])
        packet(101, 0x12)
        self.assertNotIn('fleeing', state.observation()['monsters'][0]['icons'])
        state.apply({'msg': 'map', 'cells': [{'x': 1, 'y': 0, 'mon': None}]})
        self.assertEqual(state.observation()['monsters'], [])
        packet(102, 0x12)
        self.assertNotIn('fleeing', state.observation()['monsters'][0]['icons'])
        state.apply({'msg': 'msgs', 'messages': [{'text': 'The black mamba looks frightened!'}]})
        self.assertNotIn('fleeing', state.observation()['monsters'][0]['icons'])

    def test_invisible_markers_do_not_gain_current_fleeing_from_old_flags(self):
        for bg in ([0, 64], [0, 128]):
            self.assertNotIn('fleeing', monster_appearance({'t': {'bg': bg, 'fg': 0x300012}})['icons'])

    def test_combat_allowances_do_not_permit_fleeing(self):
        from test_combat import CombatTests
        fixture = CombatTests()
        fixture.setUp()
        try:
            fixture.game.state.cells[(1, 0)]['t']['fg'] = 0x300012
            obs = fixture.game.observe()
            self.assertEqual(guard(obs, obs, policy({'allow_enemy_status': ['drain', 'poison']}), []), 'enemy_status')
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
