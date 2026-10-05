import json
from pathlib import Path
import unittest

from dcss_harness.state import State


class UnseenMessageTests(unittest.TestCase):
    def test_public_message_regressions(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/unseen-messages.json').read_text())
        for case in fixture['cases']:
            with self.subTest(text=case['text']):
                state = State()
                state.apply({'msg': 'msgs', 'messages': [{'text': case['text'], 'turn': 12048}]})
                self.assertEqual(state.unseen_threat is not None, case['unseen'])
                self.assertEqual(state.awareness_changed, case['unseen'])
                if case['unseen']:
                    self.assertEqual(state.unseen_threat['turn'], 12048)

    def test_flavour_never_clears_existing_evidence_and_acknowledgment_remains_explicit(self):
        state = State()
        state.apply({'msg': 'msgs', 'messages': [{'text': 'Something misses you.', 'turn': 8}]})
        threat = state.unseen_threat.copy()
        state.apply({'msg': 'msgs', 'messages': [{'text': "Something foul drips from Amaemon's claws.", 'turn': 9}]})
        state.apply({'msg': 'msgs', 'rollback': 2, 'messages': []})
        state.apply({'msg': 'map', 'clear': True})
        state.apply({'msg': 'player', 'depth': 11})
        self.assertEqual(state.observation()['unseen_threat'], threat)
        state.acknowledge_threat()
        self.assertIsNone(state.unseen_threat)
        state.apply({'msg': 'msgs', 'messages': [{'text': 'Something claws you.', 'turn': 10}]})
        self.assertEqual(state.unseen_threat['turn'], 10)

    def test_visible_named_monster_does_not_exempt_an_unknown_attacker(self):
        state = State()
        state.cells[(1, 0)] = {'t': {'bg': 0}, 'mon': {'id': 1, 'name': 'Amaemon', 'att': 0}}
        state.apply({'msg': 'msgs', 'messages': [
            {'text': "Something foul drips from Amaemon's claws."},
            {'text': 'Something hits you.', 'turn': 11}]})
        self.assertEqual(state.unseen_threat['text'], 'Something hits you.')


if __name__ == '__main__':
    unittest.main()
