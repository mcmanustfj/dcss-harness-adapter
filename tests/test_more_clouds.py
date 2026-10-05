import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.metrics import ActionLog, action_stats
from dcss_harness.game import Game
from dcss_harness.state import MODES, State
from dcss_harness.presentation import compact_observation
from dcss_harness.map import VisibleMap


class MoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.game = Game.__new__(Game)
        game = self.game
        game.session = Path(directory.name)
        game.state = State()
        game.state.player = {"hp": 20, "turn": 42}
        game.state.apply({"msg": "exit_reason", "type": "unknown"})
        game.state.mode = MODES.index("command")
        game.process = SimpleNamespace(poll=lambda: None)
        game.timeout, game.auto_more, game.settled = 5, True, True
        game.actions = ActionLog(game.session / "actions.jsonl")
        self.addCleanup(game.actions.close)
        game.actions.start(game.state.player)
        game.send = Mock()
        game.drain = Mock()
        game.decoder = SimpleNamespace(pending=b'')
        game.settle = Mock()

    def frame(self, mode="more", more=True, **kwargs):
        self.game.state.apply({"msg": "input_mode", "mode": MODES.index(mode)})
        self.game.state.apply({"msg": "msgs", "more": more, **kwargs})

    def test_pages_over_history_limit_preserved_in_full_and_compact_output(self):
        game = self.game
        self.frame("command", False, messages=[{"text": "old", "turn": 42}])
        before = game.observe()
        pages = iter(range(4))

        def settle(**kwargs):
            page = next(pages)
            self.frame("more" if page < 3 else "command", page < 3,
                       messages=[{"text": f"{page}:{n}", "turn": 42} for n in range(80)])

        game.settle.side_effect = settle
        result = game.act([ord(".")])
        self.assertEqual(game.send.call_count, 4)
        self.assertEqual(result["keys_sent"], 1)
        self.assertEqual(result["player"]["turn"], 42)
        delta = compact_observation(result, before)
        self.assertEqual([m["text"] for m in delta["messages"]],
                         [f"{p}:{n}" for p in range(4) for n in range(80)])
        self.assertEqual(len(compact_observation(result, full=True)["messages"]), 321)
        self.assertEqual(len(game.state.messages), 100)
        self.assertEqual(compact_observation(game.observe(), result)["messages"], [])
        stats = action_stats(game.session / "actions.jsonl")
        self.assertEqual((stats["actions"], stats["keys_sent"], stats["more_acknowledgments"]),
                         (1, 1, 3))
        self.assertEqual(stats["observed_turns"], 0)

    def test_level_up_stops_at_stat_prompt_and_drops_remaining_batch(self):
        frames = iter([("more", True), ("prompt", False)])

        def settle(**kwargs):
            mode, more = next(frames)
            self.frame(mode, more, messages=[{"text": "Choose Str, Int or Dex"}])

        self.game.settle.side_effect = settle
        result = self.game.act([ord("o"), ord("s")])
        self.assertEqual(result["input_mode"], "prompt")
        self.assertEqual(result["keys_sent"], 1)
        self.assertEqual([c.args[0]["keycode"] for c in self.game.send.call_args_list], [111, 32])

    def test_observe_handles_delayed_more_without_an_action_record(self):
        frames = iter([("more", True), ("command", False)])
        self.game.settle.side_effect = lambda **kw: self.frame(*next(frames))
        self.game.settle_input()
        self.assertEqual(self.game.observe()["input_mode"], "command")
        stats = action_stats(self.game.session / "actions.jsonl")
        self.assertEqual((stats["actions"], stats["more_acknowledgments"]), (0, 1))

    def test_real_decisions_and_postgame_are_not_acknowledged(self):
        for mode in ("normal", "command", "prompt", "yes_no", "target", "target_direction", "target_path"):
            with self.subTest(mode=mode):
                self.frame(mode)
                self.game.settle_input()
                self.game.send.assert_not_called()
        self.frame()
        for attr, value in (("ui", [{"type": "menu"}]), ("ui", [{"type": "game-over"}]),
                            ("more_text", "CTRL"), ("exit_reason", {"type": "dead"})):
            with self.subTest(attr=attr, value=value):
                old = getattr(self.game.state, attr)
                setattr(self.game.state, attr, value)
                self.game.settle_input()
                self.game.send.assert_not_called()
                setattr(self.game.state, attr, old)
        self.game.state.player["hp"] = 0
        self.game.settle_input()
        self.game.send.assert_not_called()
        self.game.state.player["hp"] = 20
        self.frame(more=False)
        self.game.settle_input()
        self.game.send.assert_not_called()

    def test_disable_count_bound_timeout_unsettled_and_exit(self):
        game = self.game
        self.frame()
        game.auto_more = False
        game.settle_input()
        self.assertEqual(game.observe()["more_pending_reason"], "disabled")
        game.send.assert_not_called()
        game.auto_more = True
        game.settle_input()
        self.assertEqual(game.send.call_count, 32)
        self.assertEqual(game.observe()["more_pending_reason"], "acknowledgment_limit")
        game.send.reset_mock()
        with patch("dcss_harness.metrics.time.monotonic", return_value=10):
            game.settle_input(deadline=10)
        self.assertEqual(game.observe()["more_pending_reason"], "timeout")
        game.send.assert_not_called()
        game.settle.side_effect = lambda **kw: setattr(game, "settled", False)
        game.settle_input()
        self.assertEqual(game.observe()["more_pending_reason"], "unsettled")
        game.send.assert_not_called()
        game.settle.side_effect = lambda **kw: setattr(game.process, "poll", lambda: 0)
        game.settle_input()
        self.assertFalse(game.observe()["running"])
        game.send.assert_not_called()

    def test_acknowledgments_share_deadline_and_do_not_retry_after_timeout(self):
        game = self.game
        self.frame()
        calls = []

        def settle(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                game.settled = False

        game.settle.side_effect = settle
        with patch("dcss_harness.metrics.time.monotonic", side_effect=[10, 12]):
            game.settle_input(deadline=15)
        self.assertEqual([call["timeout"] for call in calls], [5, 3])
        game.send.assert_called_once_with({"msg": "key", "keycode": 32})
        self.assertFalse(game.observe()["settled"])

    def test_message_rollback_across_pages(self):
        self.frame("command", False, messages=[{"text": "old"}, {"text": "hit"}])
        before = self.game.observe()
        self.frame(messages=[{"text": "page"}])
        self.frame("command", False, rollback=2, messages=[{"text": "hit x2"}])
        result = compact_observation(self.game.observe(), before)
        self.assertEqual(result["messages_rollback"], 1)
        self.assertEqual(result["messages"], [{"text": "hit x2"}])

    def test_live_welcome_pages_preserve_turn_and_stop_at_command(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/more-live.json").read_text())
        frames = iter(fixture["frames"])

        def settle(**kwargs):
            for event in next(frames):
                self.game.state.apply(event)

        self.game.settle.side_effect = settle
        self.game.settle_input()
        obs = self.game.observe()
        self.assertEqual(self.game.send.call_count, 3)
        self.assertEqual(obs["player"]["turn"], 0)
        self.assertEqual(obs["input_mode"], "command")
        self.assertFalse(obs["more"])
        self.assertEqual([m["text"] for m in obs["messages"]], [
            "Welcome back, Check the Minotaur Fighter.",
            "Game seed: 12345 (custom seed)",
            "Press ? for a list of commands and other information."])


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.frames = json.loads((Path(__file__).parent / "fixtures/clouds.json").read_text())["frames"]
        self.state = State()

    def frame(self, n):
        for event in self.frames[n]:
            self.state.apply(event)
        return self.state.observation()

    def test_visible_types_ambiguity_unknowns_and_group_coordinates(self):
        obs = self.frame(0)
        groups = obs["visible_features"]
        self.assertEqual(len(groups), 6)
        smoke = next(g for g in groups if g["cloud_type"] == "blue_smoke")
        self.assertEqual(smoke["cells"], [[2, 0], [2, 1], [3, 1]])
        self.assertEqual(smoke["navigation"]["steps"], [{"move": "e", "count": 1}])
        self.assertEqual(smoke["navigation"]["target"], "adjacent")
        fire = next(g for g in groups if g["cloud_type"] == "fire")
        self.assertEqual(fire["cells"], [[3, 2], [4, 2]])
        self.assertEqual(fire["navigation"]["status"], "unknown")
        steam = next(g for g in groups if "possible_types" in g)
        self.assertEqual(steam["cloud_type"], "unknown")
        self.assertEqual(steam["possible_types"], ["grey_smoke", "steam"])
        self.assertEqual(len([g for g in groups if g.get("tile") in (65000, 65001)]), 2)
        self.assertEqual(len(obs["map"]["rows"]), 17)

    def test_visibility_removal_animation_and_delta_clear(self):
        first = self.frame(0)
        self.state.apply({"msg": "map", "cells": [{"x": 3, "y": 2, "t": {"cloud": 5257}}]})
        self.assertNotIn("visible_features", compact_observation(self.state.observation(), first))
        second = self.frame(1)
        from test_feature_deltas import apply
        groups = apply(first["visible_features"], compact_observation(second, first))
        smoke = next(g for g in groups if g["cloud_type"] == "blue_smoke")
        self.assertEqual(smoke["cells"], [[2, 0]])
        self.assertFalse(any(g["cloud_type"] == "fire" for g in groups))
        self.assertEqual(compact_observation(self.frame(2), second)["visible_features"], [])

    def test_cloud_under_player_and_diagonals_do_not_allow_cloud_transit(self):
        self.frame(0)
        self.state.apply({"msg": "map", "cells": [{"x": 0, "y": 0, "t": {"cloud": 5269}},
            {"x": 1, "y": 0, "t": {"cloud": 5269}}]})
        obs = self.state.observation()
        smoke = next(g for g in obs["visible_features"] if g["cloud_type"] == "blue_smoke")
        self.assertIn([0, 0], smoke["cells"])
        self.assertEqual(smoke["navigation"]["text"], "here")
        visible = VisibleMap(self.state.cells, self.state.player["pos"], self.state.visible)
        self.assertNotIn((1, 0), visible.paths)
        self.assertNotIn((2, 0), visible.paths)

    def test_invisible_gap_splits_same_type_groups(self):
        self.state.apply({"msg": "map", "cells": [
            {"x": x, "y": 0, "f": 33, "g": "§",
             "t": {"bg": 0x40000 if x == 2 else 0, "cloud": 5269}}
            for x in range(1, 4)]})
        groups = self.state.observation()["visible_features"]
        self.assertEqual([g["cells"] for g in groups], [[[1, 0]], [[3, 0]]])


if __name__ == "__main__":
    unittest.main()
