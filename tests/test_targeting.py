import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.game import Game
from dcss_harness.state import State
from dcss_harness.presentation import compact_observation
from dcss_harness.cli import main
from dcss_harness.safety import CONSTANTS


class TargetingTests(unittest.TestCase):
    def test_live_iceblast_preview_moves_and_cancellation_without_turn(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/targeting-live.json").read_text())
        state, snapshots = State(), []
        for frame in fixture["frames"]:
            if frame["input"]:
                state.begin_input()
            for event in frame["events"]:
                state.apply(event)
            snapshots.append(state.observation())
        self.assertEqual([s["player"]["turn"] for s in snapshots], [0] * 6)
        self.assertIsNone(snapshots[0]["targeting"])
        self.assertEqual(len(snapshots[2]["targeting"]["preview_cells"]), 4)
        self.assertEqual(snapshots[3]["targeting"]["cursor"]["dy"], -3)
        self.assertEqual(len(snapshots[3]["targeting"]["preview_cells"]), 8)
        self.assertEqual(snapshots[4]["targeting"]["cursor"]["dy"], -4)
        self.assertIsNone(snapshots[5]["targeting"])
        self.assertIsNone(compact_observation(snapshots[5], snapshots[4])["targeting"])

    def setUp(self):
        self.state = State()
        self.state.apply({"msg": "player", "pos": {"x": 10, "y": 10}, "hp": 20, "turn": 5})
        self.state.apply({"msg": "input_mode", "mode": 4})

    def cursor(self, x, y):
        self.state.apply({"msg": "cursor", "id": 0, "loc": {"x": x, "y": y}})

    def test_cursor_empty_monster_cycling_and_visibility(self):
        self.state.apply({"msg": "map", "cells": [
            {"x": 11, "y": 10, "t": {"bg": 0}},
            {"x": 12, "y": 10, "t": {"bg": 0}, "mon": {"id": 7, "name": "orc"}},
            {"x": 13, "y": 10, "t": {"bg": 0x40000}, "mon": {"id": 8, "name": "gnoll"}}]})
        self.cursor(11, 10)
        before = self.state.observation()
        self.assertEqual(before["targeting"]["cursor"], {"x": 11, "y": 10, "dx": 1, "dy": 0, "visible": True})
        self.assertIsNone(before["targeting"]["selected_monster"])
        self.cursor(12, 10)
        after = self.state.observation()
        self.assertEqual(compact_observation(after, before)["targeting"]["selected_monster"]["id"], 7)
        self.cursor(13, 10)
        target = self.state.observation()["targeting"]
        self.assertFalse(target["cursor"]["visible"])
        self.assertIsNone(target["selected_monster"])

    def test_area_preview_uncertainty_landing_and_impact_are_distinct(self):
        self.state.apply({"msg": "map", "cells": [
            {"x": 11, "y": 10, "t": {"bg": 0, "ov": [CONSTANTS["TILE_RAY"]]}},
            {"x": 12, "y": 10, "t": {"bg": 0, "ov": [CONSTANTS["TILE_RAY_OUT_OF_RANGE"]]}},
            {"x": 12, "y": 11, "t": {"bg": 0, "ov": [CONSTANTS["TILE_RAY_MULTI"]]}},
            {"x": 11, "y": 11, "t": {"bg": 0, "ov": [CONSTANTS["TILE_LANDING"]]}}]})
        self.cursor(12, 10)
        t = self.state.observation()["targeting"]
        self.assertEqual([p["kind"] for p in t["preview_cells"]],
                         ["affected_or_path", "possible_blocked_or_out_of_range", "multiple"])
        self.assertEqual(len(t["landing_cells"]), 1)
        self.assertIsNone(t["impact_point"])
        self.assertEqual(t["range"], "unknown")

    def test_explicit_feedback_and_invalid_aim_are_not_guessed_from_missing_ray(self):
        self.state.apply({"msg": "map", "cells": [{"x": 11, "y": 10, "t": {"bg": 0x2000000}}]})
        self.cursor(11, 10)
        t = self.state.observation()["targeting"]
        self.assertEqual(t["range"], "marked_invalid")
        self.assertEqual(t["line_of_fire"], "unknown")
        self.state.apply({"msg": "msgs", "messages": [{"text": "Aim: orc (fire blocked by a wall)"},
                                                       {"text": "Out of range."}]})
        t = self.state.observation()["targeting"]
        self.assertEqual((t["range"], t["line_of_fire"]), ("out_of_range", "blocked"))
        self.state.begin_input()
        self.cursor(10, 11)
        self.assertEqual(self.state.observation()["targeting"]["line_of_fire"], "unknown")

    def test_exit_cancel_fire_and_new_target_do_not_reuse_preview(self):
        self.state.apply({"msg": "map", "cells": [{"x": 11, "y": 10,
            "t": {"bg": 0, "ov": [CONSTANTS["TILE_RAY"]]}}]})
        self.cursor(11, 10)
        before = self.state.observation()
        self.state.apply({"msg": "input_mode", "mode": 1})
        self.assertIsNone(compact_observation(self.state.observation(), before)["targeting"])
        self.state.apply({"msg": "input_mode", "mode": 4})
        self.assertIsNone(self.state.observation()["targeting"]["cursor"])
        self.assertEqual(self.state.observation()["targeting"]["preview_cells"], [])
        self.cursor(11, 10)
        self.state.apply({"msg": "cursor", "id": 0})
        self.assertIsNone(self.state.observation()["targeting"]["cursor"])

    def test_other_cursors_menus_and_invisible_markers_are_not_selected_monsters(self):
        self.state.apply({"msg": "cursor", "id": 2, "loc": {"x": 11, "y": 10}})
        self.assertIsNone(self.state.observation()["targeting"]["cursor"])
        self.cursor(11, 10)
        self.state.apply({"msg": "map", "cells": [{"x": 11, "y": 10,
            "t": {"bg": [0, 128]}, "mon": {"name": "invisible orc"}}]})
        t = self.state.observation()["targeting"]
        self.assertIsNone(t["selected_monster"])
        self.assertEqual(t["invisible_marker"]["location_status"], "remembered_invisible")
        self.state.ui = [{"type": "help"}]
        self.assertIsNone(self.state.observation()["targeting"])

    def test_spectator_full_map_refresh_preserves_cursor_sent_before_map(self):
        self.cursor(11, 10)
        self.state.apply({"msg": "map", "clear": True, "cells": [{"x": 11, "y": 10, "t": {"bg": 0}}]})
        self.assertEqual(self.state.observation()["targeting"]["cursor"]["x"], 11)

    def test_null_overlay_layer_and_exit_clear_targeting(self):
        self.state.cells[(10, 10)] = {"t": {"bg": 0, "ov": None}}
        self.cursor(10, 10)
        self.assertEqual(self.state.observation()["targeting"]["preview_cells"], [])
        self.state.apply({"msg": "input_mode", "mode": 1})
        self.state.mode = 4
        self.state.exit_reason = {"type": "dead"}
        self.assertIsNone(self.state.observation()["targeting"])

    def test_target_cli_respects_session_stream_and_offsets(self):
        with patch("sys.argv", ["crawl-agent", "--session-dir", "/tmp/target", "--stream", "test",
                                "target", "--dx", "2", "--dy", "-1"]), \
                patch("dcss_harness.cli.output_request", return_value=0) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(output.call_args.args[1], {"op": "target", "dx": 2, "dy": -1})
        self.assertEqual(output.call_args.args[0], Path("/tmp/target"))

    def test_target_operation_moves_cursor_without_selection_or_gameplay_key(self):
        game = Game.__new__(Game)
        game.state, game.session, game.timeout = self.state, Path("/tmp/test-target"), 5
        game.process, game.settled = SimpleNamespace(poll=lambda: None), True
        game.actions, game.send, game.settle_input = Mock(), Mock(), Mock()
        self.state.cells[(11, 10)] = {"t": {"bg": 0}}
        game.target(1, 0)
        game.send.assert_called_once_with({"msg": "target_cursor", "x": 11, "y": 10})
        game.send.reset_mock()
        self.state.cells[(11, 10)]['t']['bg'] = 0x2000000
        with self.assertRaisesRegex(ValueError, 'publicly marked invalid'):
            game.target(1, 0)
        game.send.assert_not_called()
        self.state.mode = 1
        with self.assertRaises(RuntimeError):
            game.target(1, 0)
        game.send.assert_not_called()
