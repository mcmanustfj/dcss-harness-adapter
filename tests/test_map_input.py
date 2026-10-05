import json
from pathlib import Path
import socket
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock

from dcss_harness.state import Decoder, State
from dcss_harness.game import Game


class MapInputTests(unittest.TestCase):
    def setUp(self):
        reader, self.writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(reader.close)
        self.addCleanup(self.writer.close)
        reader.setblocking(False)
        self.game = g = Game.__new__(Game)
        g.sock, g.state, g.decoder = reader, State(), Decoder()
        g.terminal, g.events = None, None
        g.quiet, g.timeout, g.last_event = .005, .035, time.monotonic()
        g.process = SimpleNamespace(poll=lambda: None)
        g.session, g.actions = Path('/tmp/map-input-test'), Mock()
        g.send, g.auto_more, g.settled = Mock(), False, True
        g.state.player = {'turn': 20, 'place': 'Dungeon', 'depth': 1, 'pos': {'x': 0, 'y': 0}}
        g.state.mode, g.state.ui_state = 0, 2
        g.state.map_player_on_level = True
        g.state.cursors[2] = {'x': 0, 'y': 0}

    def refresh(self, *, complete=True, state=2):
        events = [{'msg': 'version', 'text': 'test'}, {'msg': 'ui_state', 'state': state},
                  {'msg': 'map', 'clear': True, 'player_on_level': True, 'cells': []},
                  {'msg': 'cursor', 'id': 2, 'loc': {'x': 0, 'y': 0}}]
        if complete:
            events.append({'msg': 'flush_messages'})
        self.writer.send(('\n'.join(json.dumps(e) for e in events) + '\n').encode())

    def respond_to_probe(self, message):
        if message['msg'] == 'spectator_joined':
            self.refresh()

    def test_radius_buffer_and_atomic_submit(self):
        first = self.game.act([ord('R')])
        self.assertEqual(first['input_mode'], 'prompt')
        self.assertEqual(first['keys_sent'], 0)
        self.assertFalse(first['map_input']['native_command_sent'])
        self.game.send.assert_not_called()
        self.game.send.side_effect = self.respond_to_probe
        result = self.game.act([ord('2')])
        self.assertTrue(result['settled'])
        self.assertEqual(result['input_mode'], 'map')
        self.assertEqual(result['deferred_keys_sent'], 1)
        self.assertEqual([c.args[0] for c in self.game.send.call_args_list],
                         [{'msg': 'text_input', 'text': 'R2'}, {'msg': 'spectator_joined'}])

    def test_radius_literal_pair_reports_both_requested_keys(self):
        self.game.send.side_effect = self.respond_to_probe
        result = self.game.act([ord('R'), ord('8')])
        self.assertTrue(result['settled'])
        self.assertEqual(result['keys_sent'], 2)
        self.assertNotIn('keys_buffered', result)

    def test_radius_cancel_invalid_digit_and_selection_change_send_no_key(self):
        self.game.act([ord('R')])
        with self.assertRaisesRegex(ValueError, '1-8'):
            self.game.act([ord('9')])
        self.game.state.cursors[2] = {'x': 1, 'y': 0}
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            self.game.act([ord('2')])
        result = self.game.act([27])
        self.assertIsNone(result['map_input'])
        self.assertEqual(result['input_mode'], 'map')
        self.game.send.assert_not_called()

    def test_noop_cycle_and_exclusion_edit_accept_complete_ordered_refresh(self):
        for key in (9, ord('e'), ord('<'), ord('h')):
            with self.subTest(key=key):
                self.game.send.reset_mock()
                self.game.send.side_effect = self.respond_to_probe
                result = self.game.act([key])
                self.assertTrue(result['settled'])
                self.assertEqual(result['keys_sent'], 1)
                self.assertEqual(result['player']['turn'], 20)
                self.assertEqual(self.game.send.call_count, 2)

    def test_no_reply_remains_uncertain_and_key_is_not_retried(self):
        result = self.game.act([9])
        self.assertFalse(result['settled'])
        self.assertEqual(result['settle_reason'], 'no_response')
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            self.game.act([27])
        self.assertEqual(self.game.send.call_count, 2)

    def test_partial_refresh_remains_unready_until_closing_frame(self):
        self.game.send.side_effect = lambda m: self.refresh(complete=False) if m['msg'] == 'spectator_joined' else None
        result = self.game.act([9])
        self.assertFalse(result['settled'])
        self.writer.send(b'{"msg":"flush_messages"}\n')
        self.game.settle()
        self.assertTrue(self.game.settled)
        self.assertEqual(self.game.send.call_count, 2)

    def test_old_flush_or_partial_map_does_not_prove_refresh(self):
        def incomplete(message):
            if message['msg'] == 'spectator_joined':
                self.writer.send(b'{"msg":"map","cells":[]}\n{"msg":"flush_messages"}\n')
        self.game.send.side_effect = incomplete
        self.assertFalse(self.game.act([9])['settled'])

    def test_gameplay_and_travel_commands_do_not_get_map_noop_allowance(self):
        for mode, ui_state, key in ((1, 0, ord('.')), (0, 2, 13), (0, 2, ord('.'))):
            with self.subTest(key=key, ui_state=ui_state):
                self.game.state.mode, self.game.state.ui_state = mode, ui_state
                self.game.state.input_baseline = None
                self.game.settled = True
                self.game.send.reset_mock()
                self.assertFalse(self.game.act([key])['settled'])
                self.game.send.assert_called_once_with({'msg': 'key', 'keycode': key})


if __name__ == '__main__':
    unittest.main()
