"""Native progress-popup frames must not acknowledge completed level travel."""
import json
import unittest

from dcss_harness.state import State


class GenerationReadinessTests(unittest.TestCase):
    def setUp(self):
        # Use real datagram frames and the production settling loop.
        from test_reliability import SettlingTests
        fixture = SettlingTests()
        self.addCleanup(fixture.doCleanups)
        self.game, self.writer = fixture.interactive_game()
        self.game.state.player.update(place="Lair", depth=3)

    def frame(self, *events, flush=True):
        if flush:
            events += ({"msg": "flush_messages"},)
        self.writer.send(("\n".join(json.dumps(e) for e in events) + "\n").encode())

    def progress(self):
        self.frame({"msg": "ui-push", "type": "progress-bar",
                    "title": "Generating dungeon...\n\n", "bar_text": " /o/ ", "status": ""})

    def player(self):
        return {"msg": "player", "place": "Swamp", "depth": 1, "turn": 101, "pos": {"x": 0, "y": 0}}

    def map(self):
        return {"msg": "map", "clear": True, "cells": [{"x": 0, "y": 0, "g": "@", "t": {"bg": 0}}]}

    def test_progress_flush_and_updates_never_settle_or_send_second_key(self):
        self.game.send.side_effect = lambda _: self.progress()
        result = self.game.act([ord('S'), ord('.')])
        self.assertFalse(result['settled'])
        self.assertEqual(result['settle_reason'], 'level_generation')
        self.assertEqual(result['keys_sent'], 1)
        self.assertEqual(result['player']['place'], 'Lair')
        baseline = self.game.state.input_baseline
        self.frame({'msg': 'ui-state', 'type': 'progress-bar', 'status': 'building another lair branch', 'bar_text': ' /o/ '})
        self.game.settle_input()
        self.assertFalse(self.game.settled)
        self.assertIs(self.game.state.input_baseline, baseline)
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            self.game.act([ord('.')])
        self.game.send.assert_called_once()

    def test_popup_close_waits_for_both_fresh_components_and_closing_flush(self):
        self.progress()
        self.game.settle()
        self.frame({'msg': 'ui-pop'}, {'msg': 'input_mode', 'mode': 1})
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.assertEqual(self.game.settle_reason, 'level_refresh')
        self.frame(self.player())
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.frame(self.map(), flush=False)
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.frame()
        self.game.settle()
        self.assertTrue(self.game.settled)
        self.assertEqual(self.game.observe()['player']['place'], 'Swamp')
        self.game.send.assert_not_called()

    def test_stack_sync_and_redraws_while_open_do_not_count_as_completion(self):
        self.frame({'msg': 'ui-stack', 'items': [{'type': 'progress-bar', 'title': 'Generating dungeon'}]},
                   self.player(), self.map())
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.frame({'msg': 'ui-stack', 'items': []})
        self.game.settle()
        self.assertFalse(self.game.settled)
        # Status-only packets cannot stand in for fresh level/progress data.
        self.frame({'msg': 'player', 'status': []}, self.map())
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.frame(self.player())
        self.game.settle()
        self.assertTrue(self.game.settled)

    def test_progress_below_other_popup_and_cancel_exception_remain_blocked(self):
        state = State()
        state.mode = 2
        state.ui = [{'type': 'progress-bar'}, {'type': 'menu'}]
        state.begin_input({'msg': 'target_cursor', 'dx': 1, 'dy': 0})
        state.cancel_only = True
        self.assertFalse(state.input_ready())
        self.assertFalse(state.can_cancel())
        state.ui = [{'type': 'menu'}]
        self.assertTrue(state.input_ready())
        state.ui = [{'type': 'newgame-choice'}]
        self.assertTrue(state.character_creation())
        self.assertTrue(state.input_ready())

    def test_fast_generation_and_followup_prompt_settle_after_fresh_frame(self):
        def response(_):
            self.progress()
            self.frame({'msg': 'ui-pop'}, self.map(), self.player(),
                       {'msg': 'input_mode', 'mode': 7})
        self.game.send.side_effect = response
        result = self.game.act([ord('S')])
        self.assertTrue(result['settled'])
        self.assertEqual(result['input_mode'], 'prompt')
        self.assertEqual(result['player']['turn'], 101)
        self.assertIsNone(self.game.state.generation_refresh)
        self.game.send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
