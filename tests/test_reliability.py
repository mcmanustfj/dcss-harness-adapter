import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.state import Decoder, State
from dcss_harness.game import Game
from dcss_harness.presentation import compact_observation
from dcss_harness.cli import main
from dcss_harness.client import output_request
from dcss_harness.safety import CONSTANTS
from dcss_harness.safety import monster_appearance


class ReliabilityTests(unittest.TestCase):
    def test_petrified_flowers_are_scenery_but_still_block_routes(self):
        from dcss_harness.map import FEATURES, VisibleMap
        floor = next(k for k, v in FEATURES.items() if v['id'] == 'floor')
        state = State()
        state.player = {'pos': {'x': 0, 'y': 0}}
        flower = {'id': 7, 'name': 'petrified flower', 'type': CONSTANTS['MONS_PETRIFIED_FLOWER'],
                  'att': CONSTANTS['ATT_HOSTILE'], 'threat': 0, 'typedata': {'no_exp': True}}
        state.apply({'msg': 'map', 'cells': [
            {'x': x, 'y': 0, 'f': floor, 'g': '.', 't': {'bg': 0},
             'mon': flower if x == 1 else None} for x in range(3)]})
        self.assertEqual(state.observation()['monsters'], [])
        self.assertEqual(state.observation()['scenery'][0]['id'], 7)
        route = VisibleMap(state.cells, state.player['pos'], state.visible).navigation((2, 0))
        self.assertEqual(route['status'], 'unknown')
        state.apply({'msg': 'map', 'cells': [{'x': 1, 'y': 0,
                      't': {'icons': [CONSTANTS['TILEI_INNER_FLAME']]}}]})
        self.assertEqual(state.observation()['scenery'], [])
        self.assertEqual(state.observation()['monsters'][0]['id'], 7)

    def test_flower_names_and_zero_threat_do_not_establish_scenery(self):
        from dcss_harness.safety import scenery
        import copy
        base = {'mon': {'type': CONSTANTS['MONS_PETRIFIED_FLOWER'], 'name': 'petrified flower',
                       'att': 0, 'threat': 0, 'typedata': {'no_exp': True}}, 't': {'bg': 0}}
        self.assertTrue(scenery(base))
        for update in ({'type': 9999}, {'type': None}, {'typedata': {}},
                       {'typedata': {'no_exp': False}}, {'att': None}, {'att': 999}, {'threat': 1}):
            cell = copy.deepcopy(base)
            cell['mon'].update(update)
            self.assertFalse(scenery(cell), update)
        for icons in ([999999], [CONSTANTS['TILEI_BERSERK']], [CONSTANTS['TILEI_CONFUSED']]):
            cell = copy.deepcopy(base)
            cell['t']['icons'] = icons
            self.assertFalse(scenery(cell))

    def test_debris_scenery_keeps_position_and_blocks_navigation(self):
        from dcss_harness.map import FEATURES, VisibleMap
        floor = next(k for k, v in FEATURES.items() if v['id'] == 'floor')
        state = State()
        state.player = {'pos': {'x': 0, 'y': 0}}
        debris = {'id': 1, 'name': 'pile of debris', 'type': CONSTANTS['MONS_PILE_OF_DEBRIS'],
                  'att': 0, 'threat': 0, 'typedata': {'no_exp': True}}
        self.assertEqual(CONSTANTS['MONS_PILE_OF_DEBRIS'], 725)
        state.apply({'msg': 'map', 'cells': [
            {'x': x, 'y': 0, 'f': floor, 'g': '.', 't': {'bg': 0},
             'mon': debris if x == 1 else None} for x in range(3)]})
        obs = state.observation()
        self.assertEqual(obs['monsters'], [])
        self.assertEqual([(m['name'], m['dx'], m['dy']) for m in obs['scenery']], [('pile of debris', 1, 0)])
        self.assertEqual(obs['scenery'][0]['navigation']['target'], 'adjacent')
        route = VisibleMap(state.cells, state.player['pos'], state.visible).navigation((2, 0))
        self.assertEqual(route['status'], 'unknown')
        self.assertNotIn('steps', route)
        state.apply({'msg': 'map', 'cells': [{'x': 1, 'y': 0, 'mon': None}]})
        self.assertEqual(compact_observation(state.observation(), obs)['scenery'], [])

    def test_debris_scenery_does_not_hide_other_types_missing_evidence_or_danger(self):
        from dcss_harness.safety import scenery
        import copy
        base = {'mon': {'name': 'pile of debris', 'type': CONSTANTS['MONS_PILE_OF_DEBRIS'],
                        'threat': 0, 'typedata': {'no_exp': True}}, 't': {'bg': 0}}
        self.assertTrue(scenery(base))
        mutations = [lambda c: c['mon'].update(type=9999), lambda c: c['mon'].pop('type'),
                     lambda c: c['mon'].pop('typedata'), lambda c: c['mon'].update(typedata={'no_exp': False}),
                     lambda c: c['mon'].update(threat=1), lambda c: c['mon'].pop('threat'),
                     lambda c: c['t'].update(icons=[CONSTANTS['TILEI_BERSERK']]),
                     lambda c: c['t'].update(bg=[0, 64])]
        for mutate in mutations:
            cell = copy.deepcopy(base)
            mutate(cell)
            self.assertFalse(scenery(cell), cell)

    def test_public_weapon_sprites_give_positive_reach_and_ranged_hints_only(self):
        cell = {"t": {"mcache": [[CONSTANTS["TILEP_HAND1_SPEAR"], 0, 0],
                                 [CONSTANTS["TILEP_HAND1_ARBALEST"], 0, 0]]}}
        weapons = monster_appearance(cell)["visible_weapons"]
        self.assertEqual([w["attack_hint"] for w in weapons], ["reaching", "ranged"])
        cell["t"]["bg"] = [0, 128]
        self.assertEqual(monster_appearance(cell)["visible_weapons"], [])

    def test_live_identification_and_letter_namespace(self):
        frames = json.loads((Path(__file__).parent / "fixtures/inventory-identification.json").read_text())["frames"]
        state = State()
        state.apply(frames[0])
        state.apply(frames[1])
        self.assertIn("53", state.inventory_refresh)
        self.assertFalse(next(i for i in state.observation()["inventory"] if i["slot"] == 53)["name_current"])
        state.apply(frames[2])
        self.assertFalse(state.inventory_refresh)
        self.assertEqual(state.player["inv"]["53"]["name"], "2 scrolls of identify")
        state.apply(frames[3])
        self.assertIn("54", state.inventory_refresh)
        state.apply(frames[4])
        obs = state.observation()
        item = next(i for i in obs["inventory"] if i["slot"] == 54)
        self.assertEqual((item["name"], item["letter"], item["letter_namespace"]),
                         ("scroll of teleportation", "t", "scrolls"))
        self.assertEqual(obs["inventory"][0]["letter_namespace"], "equipment")
        self.assertTrue(all(i["name_current"] for i in obs["inventory"]))
        state.apply({"msg": "player", "inv": {"54": {"quantity": 0}}})
        self.assertFalse(any(i["slot"] == 54 for i in state.observation()["inventory"]))

    def test_idless_replacement_does_not_inherit_identity(self):
        state = State()
        state.apply({"msg": "map", "cells": [{"x": 0, "y": 0, "g": "o", "t": {"bg": 0},
            "mon": {"id": 154, "name": "orc", "att": 0}}]})
        state.apply({"msg": "map", "cells": [{"x": 0, "y": 0,
            "mon": {"name": "invisible orc wizard", "type": 10}, "t": {"bg": [0, 128]}}]})
        mon = state.observation()["monsters"][0]
        self.assertNotIn("id", mon)
        self.assertEqual(mon["location_status"], "remembered_invisible")
        self.assertNotIn("steps", mon["navigation"])
        # A newly assigned ID must not be correlated with the earlier sighting.
        state.apply({"msg": "map", "cells": [{"x": 0, "y": 0, "mon": None},
            {"x": 2, "y": 0, "mon": {"id": 180, "name": "orc"}, "t": {"bg": 0}}]})
        self.assertEqual([m["id"] for m in state.observation()["monsters"]], [180])

    def test_current_and_remembered_invisible_markers_not_attack_routes(self):
        for tiles, expected in (({"bg": [0, 64], "fg": 0}, "invisible_disturbance"),
                                ({"bg": [0, 128]}, "remembered_invisible"),
                                ({"bg": 0, "icons": [CONSTANTS["TILEI_UNSEEN_INVIS_REMEMBERED"]]}, "remembered_invisible")):
            state = State()
            state.apply({"msg": "player", "pos": {"x": 0, "y": 0}})
            state.apply({"msg": "map", "cells": [{"x": 0, "y": 0, "mon": {"name": "invisible orc"}, "t": tiles}]})
            mon = state.observation()["monsters"][0]
            self.assertEqual(mon["location_status"], expected)
            self.assertEqual(mon["navigation"]["status"], "unknown")

    def test_scenery_requires_public_type_and_no_exp_not_just_name(self):
        state = State()
        plant = {"id": 1, "name": "plant", "type": CONSTANTS["MONS_PLANT"], "threat": 0,
                 "typedata": {"no_exp": True}}
        state.apply({"msg": "map", "cells": [
            {"x": 0, "y": 0, "mon": plant, "t": {"bg": 0}},
            {"x": 1, "y": 0, "mon": {"id": 2, "name": "plant", "threat": 0}, "t": {"bg": 0}},
            {"x": 2, "y": 0, "mon": {**plant, "id": 3}, "t": {"bg": 0, "icons": [CONSTANTS["TILEI_BERSERK"]]}}]})
        obs = state.observation()
        self.assertEqual([m["id"] for m in obs["scenery"]], [1])
        self.assertEqual([m["id"] for m in obs["monsters"]], [2, 3])
        self.assertNotIn("scenery", compact_observation(obs, obs))
        state.apply({"msg": "map", "clear": True})
        self.assertEqual(compact_observation(state.observation(), obs)["scenery"], [])

    def test_unseen_attacker_uncertainty_survives_empty_map_rollbacks_and_levels(self):
        state = State()
        state.apply({"msg": "msgs", "messages": [{"text": "Something hits you!", "turn": 5}]})
        state.apply({"msg": "msgs", "rollback": 1, "messages": []})
        state.apply({"msg": "map", "clear": True})
        state.apply({"msg": "player", "depth": 2})
        self.assertEqual(state.observation()["unseen_threat"]["turn"], 5)
        state.acknowledge_threat()
        self.assertIsNone(state.observation()["unseen_threat"])

    def test_uncertainty_persisted_as_public_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Game.__new__(Game)
            game.session, game.character_name, game.state = Path(directory), "Test", State()
            game.state.apply({"msg": "msgs", "messages": [{"text": "Something bites you."}]})
            game.persist_awareness()
            saved = json.loads((game.session / "awareness.json").read_text())
            self.assertEqual(saved["unseen_threat"]["reason"], "unseen_attacker")
            game.state.acknowledge_threat()
            game.persist_awareness()
            self.assertIsNone(json.loads((game.session / "awareness.json").read_text())["unseen_threat"])

    def test_acknowledgment_cli_is_session_and_stream_aware(self):
        with patch("sys.argv", ["crawl-agent", "--session-dir", "/tmp/reliable", "--stream", "test", "acknowledge-threat"]), \
                patch("dcss_harness.cli.output_request", return_value=0) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(output.call_args.args[0], Path("/tmp/reliable"))
        self.assertEqual(output.call_args.args[1], {"op": "acknowledge-threat"})


class SettlingTests(unittest.TestCase):
    def game(self):
        reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        reader.setblocking(False)
        game = Game.__new__(Game)
        game.sock, game.state, game.decoder = reader, State(), Decoder()
        game.terminal, game.events = None, io.StringIO()
        game.quiet, game.timeout, game.last_event = .01, .3, time.monotonic()
        game.process = SimpleNamespace(poll=lambda: None)
        game.send = Mock()
        return game, writer

    @staticmethod
    def send_frame(writer, data):
        """Send a native response frame; raw writer.send models split frames."""
        writer.send(data + (b'{"msg":"flush_messages"}\n' if data.endswith(b'\n') else b''))

    def interactive_game(self):
        game, writer = self.game()
        game.settled, game.auto_more, game.timeout = True, True, .04
        game.session, game.actions = Path('/tmp/readiness-test'), Mock()
        game.state.mode = 1
        game.state.player = {"turn": 100, "mp": 5, "pos": {"x": 0, "y": 0}}
        return game, writer

    def test_live_command_mode_precedes_delayed_player_frame(self):
        frame = json.loads((Path(__file__).parent / 'fixtures/settling-command-live.json').read_text())
        game, writer = self.game()
        game.state.mode = frame['initial']['mode']
        game.state.player = frame['initial']['player']
        game.state.begin_input(frame['input'])
        prefix, suffix = frame['events'][:2], frame['events'][2:]
        writer.send(('\n'.join(map(json.dumps, prefix)) + '\n').encode())

        def finish():
            time.sleep(.06)  # Command mode has arrived, but the frame is incomplete.
            writer.send(('\n'.join(map(json.dumps, suffix)) + '\n').encode())

        thread = threading.Thread(target=finish)
        thread.start()
        try:
            game.settle(require_event=True)
        finally:
            thread.join()
        self.assertTrue(game.settled)
        self.assertEqual(game.state.player['turn'], 1)
        game.send.assert_not_called()

    def test_completed_mode_cycle_and_flush_are_not_action_acknowledgments(self):
        game, writer = self.interactive_game()
        game.send.side_effect = lambda _: self.send_frame(writer,
            b'{"msg":"input_mode","mode":0}\n{"msg":"input_mode","mode":1}\n')
        result = game.act([ord('.'), ord('.')])
        self.assertFalse(result['settled'])
        self.assertEqual(result['settle_reason'], 'no_response')
        self.assertEqual(result['keys_sent'], 1)
        baseline = game.state.input_baseline
        game.settle(timeout=.02)
        self.assertFalse(game.settled)
        self.assertIs(game.state.input_baseline, baseline)
        self.send_frame(writer, b'{"msg":"player","turn":101}\n')
        game.settle()
        self.assertTrue(game.settled)
        self.assertEqual(game.send.call_count, 1)

    def test_timeout_retains_incomplete_frame_through_observe_and_idle_draining(self):
        game, writer = self.interactive_game()
        game.send.side_effect = lambda _: writer.send(b'{"msg":"player","turn":101}\n')
        result = game.act([ord('.')])
        self.assertFalse(result['settled'])
        self.assertEqual(result['settle_reason'], 'incomplete_frame')
        self.assertFalse(result['cancel_available'])
        baseline = game.state.input_baseline
        game.settle(timeout=.02)
        self.assertFalse(game.settled)
        self.assertIs(game.state.input_baseline, baseline)
        writer.send(b'{"msg":"flush_messages"}\n')
        game.drain()
        game.settle()
        self.assertTrue(game.settled)
        self.assertIsNone(game.state.input_baseline)
        self.assertEqual(game.send.call_count, 1)

    def test_old_queued_messages_are_drained_before_new_action_baseline(self):
        game, writer = self.interactive_game()
        self.send_frame(writer, b'{"msg":"msgs","messages":[{"text":"Earlier response"}]}\n')
        game.send.side_effect = lambda _: self.send_frame(writer,
            b'{"msg":"input_mode","mode":0}\n{"msg":"input_mode","mode":1}\n')
        result = game.act([ord('.')])
        self.assertFalse(result['settled'])
        self.assertEqual(result['settle_reason'], 'no_response')
        self.assertEqual(game.send.call_count, 1)

    def test_queued_partial_record_blocks_new_action_before_input(self):
        game, writer = self.interactive_game()
        writer.send(b'{"msg":"player","turn":')
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            game.act([ord('.')])
        game.send.assert_not_called()

    def test_flush_before_new_records_does_not_close_their_frame(self):
        game, writer = self.interactive_game()
        game.send.side_effect = lambda _: writer.send(
            b'{"msg":"flush_messages"}\n{"msg":"player","turn":101}\n')
        result = game.act([ord('.')])
        self.assertFalse(result['settled'])
        self.assertEqual(result['settle_reason'], 'incomplete_frame')
        self.assertEqual(game.send.call_count, 1)

    def test_changed_player_does_not_release_helper_before_delayed_monster_removal(self):
        from dcss_harness.combat import run
        game, writer = self.helper_game('combat')
        thread = None

        def reply(message):
            nonlocal thread
            if game.send.call_count > 1:
                self.fail('A second attack was sent before the first kill frame finished')
            writer.send(b'{"msg":"input_mode","mode":0}\n'
                        b'{"msg":"input_mode","mode":1}\n{"msg":"player","turn":11}\n')
            def finish():
                time.sleep(.06)
                writer.send(b'{"msg":"map","cells":[{"x":1,"y":0,"mon":null}]}\n'
                            b'{"msg":"msgs","messages":[{"text":"You kill the goblin!"}]}\n'
                            b'{"msg":"flush_messages"}\n')
            thread = threading.Thread(target=finish)
            thread.start()

        game.send.side_effect = reply
        try:
            result = run(game, {'max_actions': 2, 'max_seconds': .3})
        finally:
            if thread:
                thread.join()
        self.assertTrue(result['settled'])
        self.assertEqual(result['monsters'], [])
        self.assertEqual(result['keys_sent'], 1)
        self.assertEqual(game.send.call_count, 1)

    def test_map_entry_and_exit_are_ready_without_command_mode(self):
        game, writer = self.interactive_game()
        replies = iter([
            b'{"msg":"input_mode","mode":0}\n{"msg":"ui_state","state":2}\n',
            b'{"msg":"ui_state","state":0}\n{"msg":"input_mode","mode":1}\n'])
        game.send.side_effect = lambda _: self.send_frame(writer, next(replies))
        opened = game.act([ord('X')])
        self.assertTrue(opened['settled'])
        self.assertEqual(opened['input_mode'], 'map')
        closed = game.act([27])
        self.assertTrue(closed['settled'])
        self.assertEqual(closed['input_mode'], 'command')
        self.assertEqual(closed['player']['turn'], 100)

    def test_native_line_prompts_buffer_edits_and_submit_exactly_once(self):
        for tag, opening in [('travel_depth', ord('D')), ('stash_search', 6)]:
            with self.subTest(tag=tag):
                game, writer = self.interactive_game()
                def reply(message):
                    if message == {'msg': 'key', 'keycode': opening}:
                        events = [{'msg': 'input_mode', 'mode': 0},
                                  {'msg': 'init_input', 'type': 'messages', 'tag': tag,
                                   'prefill': '9', 'select_prefill': True, 'maxlen': 20}]
                    else:
                        self.assertEqual(message, {'msg': 'text_input', 'text': '\x15\x0b12\r'})
                        events = [{'msg': 'close_input'}, {'msg': 'input_mode', 'mode': 1}]
                    self.send_frame(writer, ('\n'.join(map(json.dumps, events)) + '\n').encode())
                game.send.side_effect = reply
                opened = game.act([opening])
                self.assertTrue(opened['settled'])
                self.assertEqual(opened['input_mode'], 'prompt')
                edited = game.act([ord('1'), ord('3'), 8, ord('2')])
                self.assertEqual(edited['text_input']['text'], '12')
                self.assertEqual((edited['keys_sent'], edited['keys_buffered']), (0, 4))
                self.assertEqual(game.send.call_count, 1)
                submitted = game.act([13])
                self.assertTrue(submitted['settled'])
                self.assertIsNone(submitted['text_input'])
                self.assertEqual(game.send.call_count, 2)

    def test_line_cancel_and_update_do_not_leak_buffer_into_next_prompt(self):
        game, writer = self.interactive_game()
        game.state.apply({'msg': 'init_input', 'tag': 'stash_search', 'prefill': ''})
        game.act(list(map(ord, 'potion')))
        game.state.apply({'msg': 'update_input', 'input_text': 'scroll', 'select': True})
        game.act([ord('a')])
        self.assertEqual(game.state.text_input['text'], 'a')
        game.send.side_effect = lambda _: self.send_frame(writer, b'{"msg":"close_input"}\n')
        self.assertTrue(game.act([27])['settled'])
        game.send.assert_called_once_with({'msg': 'key', 'keycode': 27})
        game.state.apply({'msg': 'init_input', 'tag': 'travel_depth', 'prefill': '1'})
        self.assertEqual(game.state.text_input['text'], '1')

    def test_silent_target_navigation_only_allows_explicit_escape(self):
        for message in ({'msg': 'key', 'keycode': 9}, {'msg': 'key', 'keycode': ord('+')},
                        {'msg': 'target_cursor', 'x': 1, 'y': 0}):
            with self.subTest(message=message):
                game, writer = self.interactive_game()
                game.state.mode = 4
                game.state.cells[(1, 0)] = {'t': {'bg': 0}}
                if message['msg'] == 'key':
                    result = game.act([message['keycode'], 13])
                    self.assertEqual(result['keys_sent'], 1)
                else:
                    result = game.target(1, 0)
                self.assertFalse(result['settled'])
                self.assertTrue(result['cancel_available'])
                game.send.assert_called_once_with(message)
                with self.assertRaisesRegex(RuntimeError, 'still updating'):
                    game.act([13])
                game.send.side_effect = lambda _: self.send_frame(writer, b'{"msg":"input_mode","mode":1}\n')
                cancelled = game.act([27])
                self.assertTrue(cancelled['settled'])
                self.assertFalse(cancelled['cancel_available'])
                self.assertEqual((cancelled['player']['turn'], cancelled['player']['mp']), (100, 5))
                self.assertEqual(game.send.call_count, 2)

    def test_silent_fire_direction_and_text_submission_never_allow_cancel_or_retry(self):
        for mode, key in [(4, 13), (4, ord('f')), (4, ord('.')), (3, ord('l')),
                          (4, ord(' ')), (4, ord('!')), (4, ord('@')), (4, ord('5'))]:
            with self.subTest(mode=mode, key=key):
                game, writer = self.interactive_game()
                game.state.mode = mode
                result = game.act([key])
                self.assertFalse(result['settled'])
                self.assertFalse(result['cancel_available'])
                with self.assertRaisesRegex(RuntimeError, 'still updating'):
                    game.act([27])
                self.assertEqual(game.send.call_count, 1)
        game, writer = self.interactive_game()
        game.state.apply({'msg': 'init_input', 'tag': 'travel_depth', 'prefill': '1'})
        result = game.act([13])
        self.assertFalse(result['settled'])
        self.assertFalse(result['cancel_available'])
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            game.act([13])
        self.assertEqual(game.send.call_count, 1)

    def test_cancel_requires_unchanged_state_and_complete_protocol_records(self):
        for change in ('resource', 'partial_record', 'mode'):
            with self.subTest(change=change):
                game, writer = self.interactive_game()
                game.state.mode = 4
                self.assertTrue(game.act([9])['cancel_available'])
                if change == 'partial_record':
                    self.send_frame(writer, b'{"msg":"player","turn":')
                elif change == 'resource':
                    self.send_frame(writer, b'{"msg":"player","mp":4}\n{"msg":"input_mode","mode":0}\n')
                else:
                    self.send_frame(writer, b'{"msg":"input_mode","mode":0}\n')
                with self.assertRaisesRegex(RuntimeError, 'still updating'):
                    game.act([27])
                self.assertEqual(game.send.call_count, 1)

    def test_text_terminators_and_unknown_prompts_are_not_silent_edits(self):
        for tag, key in [('travel_depth', ord('?')), ('travel_depth', ord('-')),
                         ('stash_search', ord('?')), ('unknown', ord('1'))]:
            game, writer = self.interactive_game()
            game.state.apply({'msg': 'init_input', 'tag': tag, 'prefill': ''})
            result = game.act([key])
            self.assertFalse(result['settled'])
            self.assertFalse(result['cancel_available'])
            self.assertEqual(result['keys_sent'], 1)
            self.assertEqual(game.send.call_count, 1)

    def test_text_submission_transport_failure_retains_pending_uncertainty(self):
        game, writer = self.interactive_game()
        game.state.apply({'msg': 'init_input', 'tag': 'travel_depth', 'prefill': '1'})
        game.send.side_effect = OSError('delivery uncertain')
        with self.assertRaises(OSError):
            game.act([13])
        self.assertFalse(game.settled)
        self.assertIsNotNone(game.state.input_baseline)
        self.assertFalse(game.state.can_cancel())
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            game.act([13])
        self.assertEqual(game.send.call_count, 1)

    def test_unchanged_redraw_does_not_acknowledge_action(self):
        game, writer = self.game()
        game.state.mode = 1
        game.state.player = {"turn": 100}
        game.state.begin_input()
        self.send_frame(writer, b'{"msg":"player","turn":100}\n{"msg":"input_mode","mode":1}\n'
                    b'{"msg":"flush_messages"}\n')
        game.settle(timeout=.03, require_event=True)
        self.assertFalse(game.settled)
        self.assertEqual(game.settle_reason, "no_response")
        game.send.assert_not_called()
        # An observe, including one after idle draining, must retain uncertainty.
        game.settle(timeout=.02)
        self.assertFalse(game.settled)
        self.send_frame(writer, b'{"msg":"player","turn":101}\n')
        game.drain()
        game.settle()
        self.assertTrue(game.settled)
        self.assertEqual(game.settle_reason, "response_quiet")

    def test_stale_redraw_waits_for_delayed_kill(self):
        game, writer = self.game()
        game.state.mode = 1
        game.state.player = {"turn": 100}
        game.state.begin_input()
        self.send_frame(writer, b'{"msg":"player","turn":100}\n{"msg":"input_mode","mode":1}\n')

        def finish():
            time.sleep(.06)
            self.send_frame(writer, b'{"msg":"player","turn":101}\n'
                        b'{"msg":"msgs","messages":[{"text":"You kill the rat!"}]}\n')

        thread = threading.Thread(target=finish)
        thread.start()
        try:
            game.settle(require_event=True)
        finally:
            thread.join()
        self.assertTrue(game.settled)
        self.assertEqual(game.state.player["turn"], 101)
        game.send.assert_not_called()

    def test_explicit_nonturn_responses(self):
        for response in (b'{"msg":"msgs","messages":[{"text":"You cannot move there."}]}\n',
                         b'{"msg":"menu","title":"Inventory"}\n',
                         b'{"msg":"input_mode","mode":7}\n'):
            game, writer = self.game()
            game.state.mode = 1
            game.state.player = {"turn": 100}
            game.state.begin_input()
            self.send_frame(writer, response)
            game.settle(require_event=True)
            self.assertTrue(game.settled, response)
            self.assertEqual(game.state.player["turn"], 100)

    def test_unanswered_batch_sends_only_first_key(self):
        game, writer = self.game()
        game.settled, game.auto_more, game.timeout = True, True, .03
        game.session, game.actions = Path('/tmp/test'), Mock()
        game.state.mode = 1
        game.state.player = {"turn": 100}
        game.send.side_effect = lambda _: self.send_frame(writer, b'{"msg":"input_mode","mode":1}\n')
        result = game.act([ord('j'), ord('j')])
        self.assertEqual(result['keys_sent'], 1)
        self.assertFalse(result['settled'])
        game.send.assert_called_once_with({'msg': 'key', 'keycode': ord('j')})
        with self.assertRaisesRegex(RuntimeError, 'still updating'):
            game.act([ord('j')])
        self.assertEqual(game.send.call_count, 1)

    def helper_game(self, operation):
        game, writer = self.game()
        game.settled, game.auto_more = True, True
        game.session, game.actions = Path('/tmp/helper-test'), Mock()
        game.actions.count = 0
        game.state.mode = 1
        game.state.player = {"hp": 100, "hp_max": 100, "mp": 5, "mp_max": 5,
                             "turn": 10, "xl": 3, "place": "Dungeon", "depth": 2,
                             "pos": {"x": 0, "y": 0}, "status": [], "weapon_index": -1}
        if operation == 'recover':
            game.state.player['hp'] = 90
        else:
            game.state.apply({'msg': 'map', 'cells': [
                {'x': 1 if operation == 'combat' else 3, 'y': 0, 't': {'bg': 0},
                 'mon': {'id': 7, 'name': 'goblin', 'type': 1, 'att': 0, 'threat': 0}}]})
        game.state.apply({'msg': 'flush_messages'})
        return game, writer

    def run_helper(self, game, operation):
        from dcss_harness.combat import run as combat
        from dcss_harness.recovery import run as recovery
        options = {'max_actions': 1, 'max_seconds': .1}
        if operation == 'combat':
            return combat(game, options)
        if operation == 'wait-for':
            options['monster_id'] = 7
        return recovery(game, operation, options)

    def test_helper_preflight_reaches_first_input_with_real_settling(self):
        for operation, key in [('combat', 'l'), ('recover', '5'), ('wait-for', '.')]:
            with self.subTest(operation=operation):
                game, writer = self.helper_game(operation)
                game.send.side_effect = lambda _: self.send_frame(writer, b'{"msg":"player","turn":11}\n')
                result = self.run_helper(game, operation)
                game.send.assert_called_once_with({'msg': 'key', 'keycode': ord(key)})
                self.assertTrue(result['settled'])
                self.assertEqual(result['player']['turn'], 11)
                self.assertEqual(result['keys_sent'], 1)

    def test_helper_preflight_preserves_real_pending_response(self):
        for operation in ('combat', 'recover', 'wait-for'):
            with self.subTest(operation=operation):
                game, writer = self.helper_game(operation)
                game.state.begin_input()  # A previous, actually sent key is unresolved.
                baseline = game.state.input_baseline
                result = self.run_helper(game, operation)
                game.send.assert_not_called()
                self.assertFalse(result['settled'])
                self.assertEqual(result['settle_reason'], 'no_response')
                self.assertEqual(result['keys_sent'], 0)
                self.assertIs(game.state.input_baseline, baseline)

    def test_helper_preflight_accepts_delayed_prior_response_without_replacing_baseline(self):
        for operation in ('combat', 'recover', 'wait-for'):
            with self.subTest(operation=operation):
                game, writer = self.helper_game(operation)
                game.state.begin_input()
                # Idle draining received the earlier result before helper preflight.
                self.send_frame(writer, b'{"msg":"player","turn":11}\n')
                game.drain()
                game.send.side_effect = lambda _: self.send_frame(writer, b'{"msg":"player","turn":12}\n')
                result = self.run_helper(game, operation)
                self.assertTrue(result['settled'])
                self.assertEqual(result['player']['turn'], 12)
                self.assertEqual(result['keys_sent'], 1)

    def test_ranged_public_invalid_aim_with_real_settling_sends_no_cursor_or_shot(self):
        from dcss_harness.ranged import run
        frames = json.loads((Path(__file__).parent / 'fixtures/ranged-live.json').read_text())['frames']
        game, writer = self.game()
        game.session, game.actions = Path('/tmp/ranged-settle-test'), Mock(count=0)
        game.settled, game.auto_more = True, True
        for event in frames[0]['events']:
            game.state.apply(event)
        game.state.apply({'msg': 'flush_messages'})
        pending = iter(frames[1:3])

        def reply(message):
            frame = next(pending)  # A third input must fail this test.
            self.assertEqual(message, frame['input'])
            events = frame['events']
            if message['keycode'] == ord('a'):
                events = events + [{'msg': 'map', 'cells': [
                    {'x': 3, 'y': -6, 't': {'bg': 0x2000000}}]},
                    {'msg': 'cursor', 'id': 0, 'loc': {'x': 0, 'y': 0}}]
            self.send_frame(writer, ('\n'.join(json.dumps(e) for e in events) + '\n').encode())

        game.send.side_effect = reply
        result = run(game, {'wand_letter': 'a', 'expect_name': 'wand of flame (15)',
                            'monster_id': 1, 'max_seconds': .5})
        self.assertEqual(game.send.call_count, 2)
        self.assertTrue(result['settled'])
        self.assertIsNone(game.state.input_baseline)
        self.assertEqual(result['ranged']['stop_reason'], 'out_of_range_or_invalid')
        self.assertFalse(result['ranged']['submitted'])
        self.assertEqual(result['ranged']['turns'], 0)
        self.assertEqual(result['ranged']['charges_used'], 0)

    def test_travel_pause_longer_than_quiet_waits_for_final_floor(self):
        game, writer = self.game()
        self.send_frame(writer, b'{"msg":"input_mode","mode":0}\n{"msg":"player","turn":1,"depth":1}\n')

        def arrive():
            time.sleep(.06)
            self.send_frame(writer, b'{"msg":"player","turn":20,"depth":2}\n{"msg":"input_mode","mode":1}\n')

        thread = threading.Thread(target=arrive)
        thread.start()
        try:
            game.settle(require_event=True)
        finally:
            thread.join()
        self.assertTrue(game.settled)
        self.assertEqual(game.state.player["depth"], 2)
        game.send.assert_not_called()

    def test_real_prompts_and_menus_return_without_waiting_for_command(self):
        for event in ({"msg": "input_mode", "mode": 7}, {"msg": "input_mode", "mode": 8},
                      {"msg": "menu", "title": "Choose item"}):
            game, writer = self.game()
            game.state.mode = 0
            self.send_frame(writer, (json.dumps(event) + "\n").encode())
            game.settle(require_event=True)
            self.assertTrue(game.settled)

    def test_unknown_normal_state_times_out_without_retrying_input(self):
        game, writer = self.game()
        self.send_frame(writer, b'{"msg":"input_mode","mode":0}\n')
        game.settle(timeout=.03, require_event=True)
        self.assertFalse(game.settled)
        game.send.assert_not_called()

    def test_refresh_updates_stale_name_without_gameplay_key(self):
        game, writer = self.game()
        game.state.mode = 1
        game.state.apply({"msg": "player", "inv": {"52": {"name": "white potion", "quantity": 1, "letter": 97}}})
        game.state.apply({"msg": "player", "inv": {"52": {"letter": 104}}})

        def refresh(event):
            self.assertEqual(event, {"msg": "spectator_joined"})
            self.send_frame(writer, b'{"msg":"player","inv":{"52":{"name":"potion of haste"}}}\n')

        game.send.side_effect = refresh
        game.settle()
        self.assertTrue(game.settled)
        self.assertFalse(game.state.inventory_refresh)
        self.assertEqual(game.state.player["inv"]["52"]["name"], "potion of haste")
        game.send.assert_called_once()

    def test_failed_inventory_refresh_is_bounded_and_not_claimed_current(self):
        game, writer = self.game()
        game.state.mode = 1
        game.state.inventory_refresh.add("52")
        game.settle(timeout=.03)
        self.assertFalse(game.settled)
        game.send.assert_called_once_with({"msg": "spectator_joined"})


class TimingTests(unittest.TestCase):
    def test_response_and_persisted_emit_share_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            args = SimpleNamespace(stream='timing', full=False, text=False)
            def reply(path, message):
                return {'ok': True, 'timing': {'request_id': message['request_id'],
                                             'daemon_ms': 1, 'settle_ms': .5}}
            with patch('dcss_harness.client.request', side_effect=reply) as request, \
                    patch('dcss_harness.client.emit_observation') as emit:
                self.assertEqual(output_request(session, {'op': 'observe'}, args), 0)
            request.assert_called_once()
            result = emit.call_args.args[1]
            record = json.loads((session / 'timings.jsonl').read_text())
            self.assertEqual(result['timing']['request_id'], record['request_id'])
            self.assertEqual(record['request_id'], request.call_args.args[1]['request_id'])
            self.assertGreaterEqual(record['emitted_at'], record['received_at'])
            self.assertGreaterEqual(record['client_ms'], record['rpc_ms'])
            self.assertEqual(record['settle_ms'], .5)
            self.assertGreaterEqual(record['outside_daemon_ms'], 0)
