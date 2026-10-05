"""Initial public-state readiness across packet order, timeouts and menus."""
import json
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dcss_harness.state import Decoder, State
from dcss_harness.game import Game


class StartupTests(unittest.TestCase):
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
        g.session, g.actions = Path('/tmp/startup-test'), Mock()
        g.send, g.auto_more, g.settled = Mock(), False, False
        g.startup_pending, g.startup_refresh_sent = True, False
        self.player = {"msg": "player", "name": "Saved", "hp": 19, "hp_max": 19,
                       "mp": 0, "mp_max": 0, "turn": 21250, "xl": 1,
                       "place": "Dungeon", "depth": 1, "pos": {"x": 0, "y": 0},
                       "status": [], "inv": {}}
        self.map = {"msg": "map", "clear": True, "cells": [
            {"x": 0, "y": 0, "g": "@", "t": {"bg": 0}}]}
        self.version = {"msg": "version", "text": "Crawl test"}
        self.mode = {"msg": "input_mode", "mode": 1}

    def frame(self, *events):
        self.writer.send(('\n'.join(json.dumps(e) for e in
                                  (*events, {"msg": "flush_messages"})) + '\n').encode())

    def test_status_only_is_unready_on_start_observe_and_action(self):
        self.frame({"msg": "player", "status": []}, self.mode)
        self.game.settle(startup=True, require_event=True)
        self.assertFalse(self.game.settled)
        self.assertEqual(self.game.settle_reason, 'startup_incomplete')
        self.assertIn('inventory', self.game.observe()['startup']['missing'])
        self.assertIn('player.hp', self.game.observe()['startup']['missing'])
        self.game.settle()  # No later observe may bypass initialization.
        self.assertFalse(self.game.settled)
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            self.game.act([ord('.')])
        self.game.send.assert_called_once_with({'msg': 'spectator_joined'})

    def test_reordered_delayed_fields_and_legitimate_empty_inventory(self):
        self.frame(self.map, self.mode, {"msg": "player", "status": []})
        def later():
            time.sleep(.02)  # Beyond the quiet window; still incomplete.
            self.frame(self.player)
            time.sleep(.02)
            self.frame(self.version)
        thread = threading.Thread(target=later)
        thread.start()
        try:
            self.game.settle(timeout=.2, startup=True, require_event=True)
        finally:
            thread.join()
        self.assertTrue(self.game.settled)
        self.assertFalse(self.game.startup_pending)
        self.assertEqual(self.game.observe()['inventory'], [])
        self.assertEqual(self.game.state.player['turn'], 21250)
        self.assertEqual(self.game.state.initial_state_missing(), [])

    def test_one_read_only_refresh_can_restore_startup(self):
        self.frame(self.mode, {"msg": "player", "status": []})
        self.game.send.side_effect = lambda message: self.frame(self.player, self.map, self.version)
        self.game.settle(timeout=.2, startup=True, require_event=True)
        self.assertTrue(self.game.settled)
        self.game.send.assert_called_once_with({'msg': 'spectator_joined'})

    def test_timeout_then_late_complete_frame_recovers_without_input(self):
        self.frame(self.player, self.version, self.mode)  # Missing map.
        self.game.settle(startup=True, require_event=True)
        self.assertFalse(self.game.settled)
        self.assertEqual(self.game.observe()['startup']['missing'], ['map'])
        self.frame(self.map)
        self.game.settle()
        self.assertTrue(self.game.settled)
        self.assertIsNone(self.game.observe()['startup'])
        self.game.send.assert_called_once_with({'msg': 'spectator_joined'})

    def test_creation_menu_is_ready_but_does_not_release_gameplay_gate(self):
        self.frame({"msg": "ui-push", "type": "newgame-choice"})
        self.game.settle(startup=True, require_event=True)
        self.assertTrue(self.game.settled)
        self.assertTrue(self.game.startup_pending)
        self.assertEqual(self.game.observe()['startup']['status'], 'character_creation')
        self.game.send.assert_not_called()
        self.frame({"msg": "ui-pop"}, self.mode, {"msg": "player", "status": []})
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.frame(self.player, self.map, self.version)
        self.game.settle()
        self.assertTrue(self.game.settled)

    def test_arbitrary_menu_does_not_mask_missing_state(self):
        self.frame({"msg": "menu", "type": "inventory"})
        self.game.settle(startup=True, require_event=True)
        self.assertFalse(self.game.settled)

    def test_inventory_receipt_map_receipt_and_frame_boundary_required(self):
        without_inventory = {k: v for k, v in self.player.items() if k != 'inv'}
        self.frame(without_inventory, self.version, self.mode,
                   {"msg": "map", "clear": True, "cells": []})
        self.game.settle(startup=True, require_event=True)
        self.assertEqual(self.game.state.initial_state_missing(), ['inventory', 'map'])
        self.writer.send((json.dumps(self.map) + '\n' +
                          json.dumps({'msg': 'player', 'inv': {}}) + '\n').encode())
        self.game.settle()
        self.assertFalse(self.game.settled)
        self.assertEqual(self.game.settle_reason, 'incomplete_frame')
        self.frame()
        self.game.settle()
        self.assertTrue(self.game.settled)


if __name__ == '__main__':
    unittest.main()
