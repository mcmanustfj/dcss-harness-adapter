import json
import io
import os
import pty
from contextlib import redirect_stdout
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dcss_harness.metrics import ActionLog, action_stats
from dcss_harness.state import Decoder, State
from dcss_harness.game import Game
from dcss_harness.keys import keycode
from dcss_harness.presentation import compact_observation, emit_observation, message_changes, observation_text
from dcss_harness.cli import main
from dcss_harness.client import output_request
from dcss_harness.map import FEATURES, MOVES
from dcss_harness.items import EQUIPMENT


class SkillsMenuTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).with_name("fixtures") / "skills-menu.json"
        self.frames = json.loads(fixture.read_text())["frames"]
        self.state = State()

    def frame(self, index):
        for event in self.frames[index]["events"]:
            self.state.apply(event)
        return self.state.observation()

    def test_live_rows_training_focus_targets_and_prompt(self):
        first = self.frame(0)
        self.assertEqual(first["ui"][-1]["tag"], "skills")
        self.assertIn("c + Axes             2.4", first["text"]["menu_txt"])
        self.assertIn("Maces & Flails", first["text"]["menu_txt"])
        self.assertIn("[manual]", first["text"]["menu_txt"])
        self.assertIn("training|[cost]|targets", first["text"]["menu_txt"])
        self.frame(1)
        training = self.frame(2)
        self.assertIn("c + Axes             2.4  25%", training["text"]["menu_txt"])
        focused = self.frame(3)
        self.assertIn("c * Axes             2.4  40%", focused["text"]["menu_txt"])
        self.assertIn("j - Spellcasting", focused["text"]["menu_txt"])
        self.frame(4)
        prompt = self.frame(5)
        self.assertEqual(prompt["input_mode"], "prompt")
        self.assertIn("[a-z] set skill target", prompt["text"]["menu_txt"])
        self.frame(6)
        target = self.frame(7)
        self.assertEqual(target["input_mode"], "normal")
        self.assertIn("c * Axes             2.4  8.0", target["text"]["menu_txt"])
        self.assertIn("[targets]", target["text"]["menu_txt"])

    def test_all_skills_nested_description_and_exit(self):
        for index in range(8):
            self.frame(index)
        all_skills = self.frame(8)
        self.assertIn("Shapeshifting", all_skills["text"]["menu_txt"])
        self.assertIn("useful|[all]", all_skills["text"]["menu_txt"])
        auto = self.frame(9)
        self.assertIn("[auto]|manual", auto["text"]["menu_txt"])
        self.frame(10)
        description = self.frame(11)
        self.assertEqual(description["ui"][-1]["title"], "Axes")
        self.assertEqual(description["text"], {})
        restored = self.frame(12)
        self.assertIn("skill descriptions", restored["text"]["menu_txt"])
        self.frame(13)
        closed = self.frame(14)
        self.assertEqual(closed["ui"], [])
        self.assertEqual(closed["text"], {})
        self.assertEqual(compact_observation(closed, restored)["text"], {})

    def test_compact_updates_and_colour_only_mode_changes(self):
        first = self.frame(0)
        self.assertNotIn("text", compact_observation(self.state.observation(), first))
        # A switch can change only colour: the plain legend would be identical.
        legend = self.state.text["menu_txt"]["22"]
        changed = legend.replace('fg8 bg0">auto', 'fg15 bg0">auto')
        changed = changed.replace('fg15 bg0">manual', 'fg8 bg0">manual')
        self.state.apply({"msg": "txt", "id": "menu_txt", "lines": {"22": changed}})
        delta = compact_observation(self.state.observation(), first)
        self.assertIn("[auto]|manual", delta["text"]["menu_txt"])
        self.assertNotIn("[manual]", delta["text"]["menu_txt"])
        # Replacement CRT content must not retain any skill rows.
        self.state.apply({"msg": "txt", "id": "menu_txt", "clear": True,
                          "lines": {"0": "Nothing available"}})
        self.assertEqual(self.state.observation()["text"]["menu_txt"], "Nothing available")

    def test_nonselectable_rows_and_other_crt_menus_are_not_rewritten(self):
        self.state.apply({"msg": "menu", "type": "crt", "tag": "skills"})
        self.state.apply({"msg": "txt", "id": "menu_txt", "lines": {
            "0": '    <span class="fg8 bg0">  Unavailable       0.0',
            "1": '<span class="fg15 bg0">  c * Axes          27.0'}})
        self.assertEqual(self.state.observation()["text"]["menu_txt"],
                         '      Unavailable       0.0\n  c * Axes          27.0')
        self.state.apply({"msg": "menu", "type": "crt", "tag": "unrelated"})
        self.state.apply({"msg": "txt", "id": "menu_txt", "clear": True,
                          "lines": {"0": '[!] <span class="fg15 bg0">one</span>|two'}})
        self.assertEqual(self.state.observation()["text"]["menu_txt"], '[!] one|two')


class TerminalTests(unittest.TestCase):
    def test_terminal_drain_and_eof_do_not_change_observations(self):
        game = Game.__new__(Game)
        master, slave = pty.openpty()
        self.addCleanup(lambda: os.close(master) if game.terminal is not None else None)
        game.terminal = master
        os.set_blocking(master, False)
        game.log = io.BytesIO()
        game.sock = object()
        try:
            os.write(slave, b'\x1b[2Jprivate terminal output')
            game.drain_terminal()
            self.assertIn(b'private terminal output', game.log.getvalue())
            self.assertEqual(game.readers(), [game.sock, master])
        finally:
            os.close(slave)
        game.drain_terminal()
        self.assertIsNone(game.terminal)
        self.assertEqual(game.readers(), [game.sock])
        game.drain_terminal()  # Repeated drains after exit are harmless.


class EquipmentNameTests(unittest.TestCase):
    AXE = 3766  # Public TILE_WPN_HAND_AXE in this checkout.
    MAGIC_AXE = 3767
    RANDART_AXE = 3768
    FLOOR = next(key for key, value in FEATURES.items() if value["id"] == "floor")

    def setUp(self):
        self.state = State()
        self.state.apply({"msg": "player", "pos": {"x": 0, "y": 0},
                          "turn": 1, "place": "Dungeon", "depth": 1})
        self.put(self.MAGIC_AXE)

    def put(self, tile, glyph=")", **extra):
        self.state.apply({"msg": "map", "cells": [
            {"x": 1, "y": 0, "g": glyph, "f": self.FLOOR, "mf": 6,
             "t": {"bg": 0, "fg": tile}, **extra}]})

    def name(self):
        return next(item["name"] for item in self.state.observation()["visible_features"]
                    if item["kind"] == "item")

    def describe(self, title="A +1 hand axe.", tile=None):
        self.state.apply({"msg": "ui-push", "type": "describe-item", "title": title,
                          "tiles": [{"t": 3096, "tex": 2},
                                    {"t": tile or self.MAGIC_AXE, "tex": 4}]})

    def test_visible_equipment_appearance_names(self):
        for tile, glyph, expected in (
                (self.AXE, ")", "hand axe"), (self.MAGIC_AXE, ")", "magic hand axe"),
                (self.RANDART_AXE, ")", "randart hand axe"),
                (3990, "[", "ring mail"), (3991, "[", "magic ring mail"),
                (3992, "[", "randart ring mail"), (4086, "[", "magic helmet"),
                (4089, "[", "randart helmet"), (4010, "[", "fire dragon scales")):
            with self.subTest(tile=tile):
                self.put(tile, glyph)
                self.assertEqual(self.name(), expected)
        unrand = next(tile for tile, info in EQUIPMENT.items() if info["appearance"] == "unrandart")
        self.put(unrand)
        self.assertEqual(self.name(), "unrandart weapon")
        self.put(unrand, "[")
        self.assertEqual(self.name(), "unrandart armour")

    def test_stack_artefact_marker_does_not_classify_the_top_item(self):
        self.put([self.MAGIC_AXE, 0x2000000])
        self.assertEqual(self.name(), "magic hand axe")
        self.state.begin_inspection((1, 0))
        self.describe()
        self.assertEqual(self.name(), "magic hand axe")

    def test_obscured_or_unknown_tiles_stay_generic(self):
        self.put(65000)
        self.assertEqual(self.name(), "weapon")
        self.put(self.MAGIC_AXE, "o", mon={"id": 1, "name": "orc"})
        self.assertEqual(self.name(), "item")
        self.put(self.MAGIC_AXE, mon=None, t={"bg": 0, "fg": self.MAGIC_AXE, "cloud": 12})
        self.assertEqual(self.name(), "item")

    def test_inspection_replaces_magic_with_known_enchantment_and_brand(self):
        before = self.state.observation()
        self.state.begin_inspection((1, 0))
        self.describe("<white>A +1 hand axe.</white>")
        self.assertEqual(self.name(), "+1 hand axe")
        self.state.apply({"msg": "ui-pop"})
        self.assertEqual(self.name(), "+1 hand axe")
        # Ordinary background updates and turns elsewhere don't discard knowledge.
        self.state.apply({"msg": "map", "cells": [{"x": 1, "y": 0, "t": {"bg": 0x80000}}]})
        self.state.apply({"msg": "player", "turn": 2})
        self.assertEqual(self.name(), "+1 hand axe")
        self.state.begin_inspection((1, 0))
        self.describe("A +1 hand axe of flaming.")
        self.assertEqual(self.name(), "+1 hand axe of flaming")
        after = self.state.observation()
        self.assertEqual(before["map"], after["map"])
        self.assertIn("visible_features", compact_observation(after, before))

    def test_artefact_and_armour_titles_are_preserved(self):
        self.put(self.RANDART_AXE)
        self.state.begin_inspection((1, 0))
        self.describe('The +4 hand axe "North Star" {freeze, Str+2}.', self.RANDART_AXE)
        self.assertEqual(self.name(), '+4 hand axe "North Star" {freeze, Str+2}')
        self.state.apply({"msg": "close_all_menus"})
        self.put(3991, "[")
        self.state.begin_inspection((1, 0))
        self.describe("A +2 ring mail of fire resistance.", 3991)
        self.assertEqual(self.name(), "+2 ring mail of fire resistance")

    def test_unrelated_or_wrong_item_description_does_not_rename_loot(self):
        self.describe()
        self.assertEqual(self.name(), "magic hand axe")
        self.state.apply({"msg": "close_all_menus"})
        self.state.begin_inspection((1, 0))
        self.describe("A +2 ring mail.", 3991)
        self.assertEqual(self.name(), "magic hand axe")

    def test_single_item_examine_menu_and_ambiguous_stacks(self):
        for count in (1, 2):
            self.setUp()
            self.state.begin_inspection((1, 0))
            rows = [{"level": 1, "text": "Items"}] + [
                {"level": 2, "text": "a - a hand axe"} for _ in range(count)]
            rows += [{"level": 1, "text": "Features"}, {"level": 2, "text": "b - stairs"}]
            self.state.apply({"msg": "menu", "tag": "pickup", "items": rows, "total_items": len(rows)})
            self.describe()
            self.assertEqual(self.name(), "+1 hand axe" if count == 1 else "magic hand axe")

    def test_changes_visibility_and_resets_invalidate_inspected_names(self):
        for change in ({"t": {"bg": 0x40000}}, {"t": {"fg": self.AXE}},
                       {"t": {"fg": self.MAGIC_AXE}}, {"g": ")"},
                       {"mon": {"id": 1, "name": "orc"}},
                       {"t": {"cloud": 9}}):
            with self.subTest(change=change):
                self.setUp()
                self.state.begin_inspection((1, 0))
                self.describe()
                self.state.apply({"msg": "map", "cells": [{"x": 1, "y": 0, **change}]})
                self.assertEqual(self.state.item_names, {})
        for change in ({"msg": "map", "clear": True}, {"msg": "player", "depth": 2}):
            self.setUp()
            self.state.begin_inspection((1, 0))
            self.describe()
            self.state.apply(change)
            self.assertEqual(self.state.item_names, {})

    def test_underfoot_inspection_expires_on_gameplay_turn(self):
        self.state.player["pos"] = {"x": 1, "y": 0}
        self.put(0, "@")
        self.state.begin_inspection((1, 0))
        self.describe()
        self.assertEqual(self.name(), "+1 hand axe")
        self.state.apply({"msg": "player", "turn": 2})
        self.assertEqual(self.state.item_names, {})

    def test_changed_cell_cancels_inflight_inspection(self):
        self.state.begin_inspection((1, 0))
        self.put(self.MAGIC_AXE)  # Same appearance can be a different item.
        self.describe()
        self.assertEqual(self.name(), "magic hand axe")


class VisibleMapTests(unittest.TestCase):
    IDS = {value["id"]: key for key, value in FEATURES.items()}

    def setUp(self):
        self.state = State()
        self.state.player["pos"] = {"x": 0, "y": 0}
        self.cell(0, 0)

    def cell(self, x, y, feature="floor", **extra):
        self.state.apply({"msg": "map", "cells": [
            {"x": x, "y": y, "f": self.IDS[feature], "g": ".",
             "t": {"bg": 0}, **extra}]})

    def item(self, x, y):
        self.cell(x, y, mf=6, g="!")

    def feature(self, kind="item"):
        return next(f for f in self.state.observation()["visible_features"]
                    if f["kind"] == kind)

    def assert_route(self, navigation, end):
        point = (0, 0)
        opened = set()
        for step in navigation["steps"]:
            direction = step.get("move", step.get("direction"))
            dx, dy = MOVES[direction]
            if step.get("action") == "open_door":
                opened.add((point[0] + dx, point[1] + dy))
                continue
            for _ in range(step["count"]):
                point = (point[0] + dx, point[1] + dy)
                cell = self.state.cells[point]
                self.assertTrue(self.state.visible(cell))
                self.assertFalse(cell.get("mon"))
                self.assertFalse(cell["t"].get("cloud"))
                if "SOLID" in FEATURES[cell["f"]]["flags"]:
                    self.assertIn(point, opened)
        self.assertEqual(point, end)

    def test_navigation_orders_axes_around_walls(self):
        self.cell(0, -1, "rock_wall")
        self.cell(1, 0)
        self.cell(2, 0)
        self.item(2, -1)
        target = self.feature()
        self.assertEqual((target["dx"], target["dy"]), (2, -1))
        self.assertEqual(target["navigation"]["text"], "1 east, then 1 northeast")
        self.assert_route(target["navigation"], (2, -1))
        self.cell(1, 0, "rock_wall")
        self.cell(0, -1)
        self.cell(1, -1)
        target = self.feature()
        self.assertEqual(target["navigation"]["text"], "1 northeast, then 1 east")
        self.assert_route(target["navigation"], (2, -1))

    def test_checks_intermediate_cells_and_allows_multiple_turns(self):
        for point in [(0, 1), (0, 2), (1, 2), (2, 2), (2, 1)]:
            self.cell(*point)
        self.cell(1, 0, "rock_wall")
        self.cell(1, 1, "rock_wall")
        self.item(2, 0)
        route = self.feature()["navigation"]
        # Four actions take precedence over the old six-action, three-segment route.
        self.assertEqual(sum(s["count"] for s in route["steps"]), 4)
        self.assertEqual(len(route["steps"]), 4)
        self.assert_route(route, (2, 0))

    def test_glass_blocks_direct_route_but_not_a_visible_detour(self):
        self.cell(1, 0, "clear_rock_wall")
        self.item(2, 0)
        target = self.feature()
        self.assertEqual(target["navigation"]["status"], "unknown")
        self.assertEqual(target["direct_path_barriers"], [
            {"name": "translucent rock wall", "dx": 1, "dy": 0}])
        for x in range(3):
            self.cell(x, 1)
        target = self.feature()
        self.assertEqual(target["navigation"]["status"], "visible_route")
        self.assert_route(target["navigation"], (2, 0))

    def test_translucent_door_requires_separate_opening_and_updates(self):
        self.cell(1, 0, "closed_clear_door")
        self.item(2, 0)
        route = self.feature()["navigation"]
        self.assertEqual(route["status"], "requires_open_door")
        self.assertEqual(route["steps"], [
            {"action": "open_door", "direction": "e"}, {"move": "e", "count": 2}])
        self.assert_route(route, (2, 0))
        door = self.feature("door")
        self.assertEqual(door["navigation"]["steps"], [])
        self.assertEqual(door["navigation"]["target"], "adjacent")
        self.assertEqual(door["interaction"], "open_door")
        self.cell(1, 0, "open_clear_door")
        target = self.feature()
        self.assertNotIn("direct_path_barriers", target)
        self.assertEqual(target["navigation"]["text"], "2 east")

    def test_sealed_and_runed_doors_are_not_transit_routes(self):
        self.item(2, 0)
        for name in ("sealed_clear_door", "runed_clear_door"):
            with self.subTest(name=name):
                self.cell(1, 0, name)
                self.assertEqual(self.feature()["navigation"]["status"], "unknown")

    def test_unknown_hazard_cloud_occupancy_and_exclusion_block_transit(self):
        self.item(2, 0)
        obstacles = [{"f": 999}, {"f": self.IDS["deep_water"]},
                     {"f": self.IDS["trap_web"]}, {"t": {"bg": 0, "cloud": 123}},
                     {"mon": {"id": 1, "name": "orc"}},
                     {"t": {"bg": 0x400000}}, {"t": {"bg": [0, 0x40]}}]
        for extra in obstacles:
            with self.subTest(extra=extra):
                self.state.cells.pop((1, 0), None)
                self.cell(1, 0, **extra)
                self.assertEqual(self.feature()["navigation"]["status"], "unknown")

    def test_visible_monster_route_stops_adjacent_without_attacking(self):
        self.cell(1, 0)
        self.cell(2, 0, mon={"id": 1, "name": "orc", "att": 0})
        monster = self.state.observation()["monsters"][0]
        self.assertEqual(monster["dx"], 2)
        self.assertEqual(monster["navigation"]["target"], "adjacent")
        self.assertEqual(monster["navigation"]["target_direction"], "e")
        self.assert_route(monster["navigation"], (1, 0))

    def test_all_directions(self):
        for direction, (x, y) in MOVES.items():
            with self.subTest(direction=direction):
                self.setUp()
                self.item(x, y)
                route = self.feature()["navigation"]
                self.assertEqual(route["steps"], [{"move": direction, "count": 1}])
                self.assert_route(route, (x, y))

    def test_diagonals_minimize_actions_then_segments(self):
        for x in range(5):
            for y in range(5):
                self.cell(x, y)
        self.item(4, 4)
        route = self.feature()["navigation"]
        self.assertEqual(route["steps"], [{"move": "se", "count": 4}])
        self.assert_route(route, (4, 4))
        self.state.cells[(4, 4)]["mf"] = 1
        self.state.cells[(4, 4)]["g"] = "."
        self.item(4, 2)
        route = self.feature()["navigation"]
        self.assertEqual(sum(s["count"] for s in route["steps"]), 4)
        self.assertEqual(len(route["steps"]), 2)
        self.assert_route(route, (4, 2))

    def test_diagonal_door_opening_is_separate_and_counted(self):
        self.cell(1, 1, "closed_clear_door")
        self.item(2, 2)
        route = self.feature()["navigation"]
        self.assertEqual(route["steps"], [
            {"action": "open_door", "direction": "se"}, {"move": "se", "count": 2}])
        self.assert_route(route, (2, 2))
        # A longer-looking open route can use fewer actions than opening a door.
        self.state.cells[(2, 2)].update(mf=1, g=".")
        self.item(3, 2)
        self.cell(1, 0)
        self.cell(2, 1)
        route = self.feature()["navigation"]
        self.assertEqual(route["status"], "visible_route")
        self.assertEqual(sum(s["count"] for s in route["steps"]), 3)
        self.assert_route(route, (3, 2))

    def test_occupied_bottleneck_reason_is_evidence_not_a_route(self):
        self.cell(1, 0, mon={"id": 1, "name": "hound"})
        self.cell(2, 0, mon={"id": 2, "name": "adder"})
        adder = self.state.observation()["monsters"][1]
        route = adder["navigation"]
        self.assertEqual(route["status"], "unknown")
        self.assertNotIn("steps", route)
        self.assertEqual(route["reason"], {"kind": "visible_occupancy", "blockers": [
            {"name": "hound", "dx": 1, "dy": 0}]})
        self.cell(1, 0, mon=None)
        route = self.state.observation()["monsters"][0]["navigation"]
        self.assertEqual(route["status"], "visible_route")
        self.assertNotIn("reason", route)

    def test_unknown_reason_does_not_guess_across_fog_or_hazards(self):
        self.cell(1, 0, mon={"id": 1, "name": "hound"})
        self.item(3, 0)
        # The gap, rather than just the hound, prevents verification.
        self.assertNotIn("reason", self.feature()["navigation"])
        self.cell(2, 0, "deep_water")
        self.assertNotIn("reason", self.feature()["navigation"])
        self.cell(2, 0, t={"bg": 0x40000})
        self.assertNotIn("reason", self.feature()["navigation"])

    def test_glass_reason_only_claims_to_block_direct_approach(self):
        self.cell(1, 0, "clear_rock_wall")
        self.item(2, 0)
        route = self.feature()["navigation"]
        self.assertEqual(route["reason"]["kind"], "direct_barrier")
        self.assertIn("direct approach", route["text"])
        self.assertNotIn("steps", route)

    def test_ascii_crop_remains_in_full_json_and_text(self):
        self.cell(1, 0, g="#", feature="rock_wall")
        self.item(1, 1)
        snapshot = self.state.observation()
        self.assertEqual(len(snapshot["map"]["rows"]), 17)
        self.assertTrue(all(len(row) == 17 for row in snapshot["map"]["rows"]))
        snapshot.update(running=True, settled=True, observation="full", sequence=1)
        self.assertIn("\n".join(snapshot["map"]["rows"]), observation_text(snapshot))

    def test_visibility_movement_clear_and_compact_replacements(self):
        self.item(0, 0)
        first = self.state.observation()
        self.assertEqual(self.feature()["navigation"]["text"], "here")
        self.assertNotIn("visible_features", compact_observation(first, first))
        self.state.apply({"msg": "map", "cells": [
            {"x": 0, "y": 0, "t": {"bg": 0x40000}}]})
        # A level-map UI must not promote remembered terrain into visibility.
        self.state.ui = [{"type": "map"}]
        after = self.state.observation()
        self.assertEqual(compact_observation(after, first)["visible_features"], [])
        self.cell(0, 0)
        self.cell(1, 0)
        self.state.player["pos"] = {"x": 1, "y": 0}
        self.assertEqual(self.feature()["dx"], -1)
        self.assertEqual(self.feature()["navigation"]["text"], "1 west")
        self.state.apply({"msg": "map", "clear": True})
        self.assertEqual(self.state.observation()["visible_features"], [])

    def test_remembered_route_is_not_used(self):
        self.cell(1, 0, t={"bg": 0x20000})
        self.item(2, 0)
        self.assertEqual(self.feature()["navigation"]["status"], "unknown")

    def test_items_on_stairs_and_under_monsters(self):
        self.cell(1, 0, "stone_stairs_down_i", mf=13, g="!")
        obs = self.state.observation()
        self.assertEqual({f["kind"] for f in obs["visible_features"]}, {"item", "stairs"})
        self.cell(1, 0, mf=6, g="o", mon={"id": 1, "name": "orc"})
        self.assertEqual(self.feature()["name"], "item")

    def test_default_unicode_item_glyphs_and_cloud_overlay(self):
        for glyph, name in (("†", "corpse"), ("φ", "rune"), ("|", "staff"),
                            ('"', "amulet"), ("0", "Orb")):
            self.cell(1, 0, "stone_stairs_down_i", mf=13, g=glyph)
            self.assertEqual(self.feature()["name"], name)
        self.cell(1, 0, mf=1, g="0", t={"bg": 0, "cloud": 123})
        self.assertFalse(any(f["kind"] == "item"
                             for f in self.state.observation()["visible_features"]))

    def test_water_groups_do_not_include_unseen_gaps(self):
        for y in range(3):
            self.cell(1, y, "deep_water")
        self.cell(1, 1, "deep_water", t={"bg": 0x40000})
        hazards = self.state.observation()["visible_features"]
        self.assertEqual(len(hazards), 2)
        self.assertEqual([hazard["cells"] for hazard in hazards], [[[1, 0]], [[1, 2]]])

    def test_barrier_groups_and_text_output(self):
        for y in range(-1, 2):
            self.cell(1, y, "clear_rock_wall")
        barrier = self.feature("barrier")
        self.assertEqual((barrier["dx"], barrier["dy"]), (1, -1))
        self.assertEqual(barrier["cells"], [[1, -1], [1, 0], [1, 1]])
        self.assertEqual(len(self.state.observation()["visible_features"]), 1)
        snapshot = self.state.observation()
        snapshot.update(running=True, settled=True, observation="full", sequence=1)
        self.assertIn("translucent rock wall", observation_text(snapshot))

    def test_water_groups_preserve_depth_shapes_and_underfoot_hazards(self):
        from dcss_harness.map import VisibleMap
        from dcss_harness.recovery import local_hazard
        shallow = [(0, 0), (1, 0), (1, 1), (2, 2)]
        deep = [(2, 0), (3, 0), (3, 1)]
        for point in shallow:
            self.cell(*point, "shallow_water")
        for point in deep:
            self.cell(*point, "deep_water")
        obs = self.state.observation()
        water = obs["visible_features"]
        self.assertEqual(len(water), 2)
        self.assertEqual(water[0]["cells"], [list(p) for p in shallow])
        self.assertEqual(water[1]["cells"], [list(p) for p in deep])
        self.assertNotEqual(water[0]["name"], water[1]["name"])
        self.assertTrue(local_hazard(obs))
        self.assertNotIn([0, 1], water[0]["cells"])
        visible = VisibleMap(self.state.cells, self.state.player["pos"], self.state.visible)
        self.assertNotIn((1, 0), visible.paths)

    def test_terrain_group_uses_reachable_anchor_and_retains_occupied_unknown(self):
        self.cell(1, 0, "rock_wall")
        self.cell(2, 0, "shallow_water")
        self.cell(2, 1, "shallow_water")
        self.cell(2, 2, "shallow_water")
        self.cell(1, 1)
        group = self.feature("hazard")
        self.assertEqual(group["navigation"]["target"], "adjacent")
        self.assert_route(group["navigation"], (1, 1))
        self.cell(1, 1, mon={"id": 20, "att": 0, "threat": 0, "name": "yak", "type": 1})
        group = self.feature("hazard")
        self.assertEqual(group["navigation"]["status"], "unknown")
        self.assertEqual(group["navigation"]["reason"]["kind"], "visible_occupancy")
        self.assertEqual(group["navigation"]["reason"]["blockers"][0]["name"], "yak")


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.game = Game.__new__(Game)
        self.game.state = State()
        self.game.state.player = {"pos": {"x": 10, "y": -3}, "turn": 42}
        self.game.state.mode = 1  # command
        self.game.state.cells[(12, -4)] = {"g": ")", "t": {"bg": 0}}
        self.game.process = SimpleNamespace(poll=lambda: None)
        self.game.session = Path("/tmp/inspect-test")
        self.game.settled = True
        self.game.settle = Mock()
        self.game.send = Mock()
        self.game.actions = Mock()

    def test_inspection_opens_public_description_without_gameplay_keys(self):
        def send(message):
            self.game.state.apply({"msg": "ui-push", "type": "describe-item",
                                   "title": "+0 hand axe", "body": "A small axe."})
        self.game.send.side_effect = send
        result = self.game.inspect(2, -1)
        self.game.send.assert_called_once_with(
            {"msg": "click_cell", "x": 12, "y": -4, "button": 3})
        self.assertEqual(result["ui"][0]["title"], "+0 hand axe")
        self.assertEqual(result["player"]["turn"], 42)
        self.assertEqual(result["keys_sent"], 0)
        self.assertEqual(self.game.actions.record.call_args.args[:4], ("inspect:2,-1", 0, 0, 42))
        self.game.state.apply({"msg": "ui-pop"})
        self.assertEqual(compact_observation(self.game.observe(), result)["ui"], [])

    def test_inspection_rejects_unseen_squares_and_pending_input(self):
        for blocked in ("remembered", "menu", "more", "target", "normal", "unsettled", "exited"):
            with self.subTest(blocked=blocked):
                self.setUp()
                if blocked == "remembered":
                    self.game.state.cells[(12, -4)]["t"]["bg"] = 0x40000
                elif blocked == "menu":
                    self.game.state.ui = [{"title": "Inventory"}]
                elif blocked == "more":
                    self.game.state.more = True
                elif blocked in {"target", "normal"}:
                    self.game.state.mode = 2 if blocked == "target" else 0
                elif blocked == "unsettled":
                    self.game.settled = False
                else:
                    self.game.process.poll = lambda: 0
                with self.assertRaises((ValueError, RuntimeError)):
                    self.game.inspect(2, -1)
                self.game.send.assert_not_called()
        with self.assertRaises(ValueError):
            self.game.inspect(True, -1)

    def test_inspection_timeout_returns_state_without_retry(self):
        def settle(**kwargs):
            if kwargs.get("require_event"):
                self.game.settled = False
        self.game.settle.side_effect = settle
        result = self.game.inspect(2, -1)
        self.assertFalse(result["settled"])
        self.game.send.assert_called_once()

    def test_inspection_rechecks_exit_after_settling(self):
        self.game.settle.side_effect = lambda: setattr(self.game.process, "poll", lambda: 0)
        with self.assertRaises(RuntimeError):
            self.game.inspect(2, -1)
        self.game.send.assert_not_called()

    def test_inspection_cli_respects_session_directory_and_stream(self):
        with patch("sys.argv", ["crawl-agent", "--session-dir", "/tmp/inspect-game",
                                "--stream", "test", "inspect", "--dx", "2", "--dy", "-1"]), \
                patch("dcss_harness.cli.output_request", return_value=0) as output:
            self.assertEqual(main(), 0)
        session, request, args = output.call_args.args
        self.assertEqual(session, Path("/tmp/inspect-game"))
        self.assertEqual(request, {"op": "inspect", "dx": 2, "dy": -1})
        self.assertEqual(args.stream, "test")


class ProtocolTests(unittest.TestCase):
    def test_fragmented_utf8_and_server_messages(self):
        decoder = Decoder()
        wire = ('{"msg":"txt","text":"é"}\n'
                '*{"msg":"flush_messages"}\n').encode()
        split = wire.index(b"\xc3") + 1
        self.assertEqual(list(decoder.feed(wire[:split])), [])
        self.assertEqual(list(decoder.feed(wire[split:])), [
            {"msg": "txt", "text": "é"}, {"msg": "flush_messages"}])

    def test_map_coordinates_monster_movement_and_visibility(self):
        state = State()
        state.apply({"msg": "map", "clear": True, "cells": [
            {"x": -1, "y": 2, "g": "g", "t": {"bg": 0},
             "mon": {"id": 12, "name": "goblin", "att": 0}},
            {"g": ".", "t": {"bg": 0x40000}},
        ]})
        self.assertEqual(state.cells[(0, 2)]["g"], ".")
        state.apply({"msg": "map", "cells": [
            {"x": -1, "y": 2, "g": ".", "mon": None},
            {"g": "g", "mon": {"id": 12}, "t": {"bg": 0}},
        ]})
        obs = state.observation()
        self.assertEqual(len(obs["monsters"]), 1)
        self.assertEqual(obs["monsters"][0]["name"], "goblin")
        self.assertEqual(obs["monsters"][0]["x"], 0)
        state.apply({"msg": "map", "cells": [
            {"x": 0, "y": 2, "t": {"bg": [0x20000, 0]}}]})
        self.assertEqual(state.observation()["monsters"], [])
        state.apply({"msg": "map", "clear": True})
        self.assertEqual(state.cells, {})

    def test_inventory_deltas_and_removed_item(self):
        state = State()
        state.apply({"msg": "player", "hp": 20, "inv": {
            "0": {"name": "a hand axe", "quantity": 1, "letter": 97},
            "1": {"name": "a potion", "quantity": 2, "letter": 98}}})
        state.apply({"msg": "player", "hp": 18, "inv": {
            "1": {"quantity": 1}}})
        self.assertEqual(state.observation()["inventory"][1], {
            "name": "a potion", "quantity": 1, "slot": 1, "letter": "b",
            "category": "unknown", "letter_namespace": "equipment", "name_current": True})
        state.apply({"msg": "player", "inv": {"1": {"quantity": 0}}})
        self.assertEqual(len(state.observation()["inventory"]), 1)
        self.assertEqual(state.observation()["player"]["hp"], 18)

    def test_message_rollback_and_menu_updates(self):
        state = State()
        state.apply({"msg": "msgs", "messages": [
            {"text": "You wait.", "turn": 1}]})
        state.apply({"msg": "msgs", "rollback": 1, "messages": [
            {"text": "<lightgrey>You wait. x2</lightgrey>", "turn": 2}]})
        state.apply({"msg": "menu", "items": [{"text": "first"}]})
        state.apply({"msg": "update_menu_items", "chunk_start": 1,
                     "items": [{"text": "second"}]})
        self.assertEqual(state.observation()["ui"][0]["items"][1]["text"], "second")
        state.apply({"msg": "menu", "type": "crt", "tag": "test"})
        state.apply({"msg": "txt", "id": "menu_txt", "lines": {
            "10": "last", "2": "&lt; first"}})
        obs = state.observation()
        self.assertEqual(len(obs["messages"]), 1)
        self.assertEqual(obs["messages"][0]["text"], "You wait. x2")
        self.assertEqual(obs["text"]["menu_txt"], "< first\nlast")
        json.dumps(obs)  # public observations must always be JSON serializable
        state.apply({"msg": "close_menu"})
        self.assertNotIn("menu_txt", state.observation()["text"])

    def test_named_keys(self):
        self.assertEqual(keycode("Ctrl-S"), 19)
        self.assertEqual(keycode("Escape"), 27)
        self.assertEqual(keycode("Enter"), 13)
        self.assertEqual(keycode("o"), 111)
        with self.assertRaises(ValueError):
            keycode("not a key")

    def test_slow_action_does_not_return_stale_state(self):
        reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        reader.setblocking(False)
        game = Game.__new__(Game)
        game.sock, game.state, game.decoder = reader, State(), Decoder()
        game.terminal = None
        game.quiet, game.timeout = .02, 1
        game.events = io.StringIO()
        game.last_event = time.monotonic()
        game.process = SimpleNamespace(poll=lambda: None)
        game.state.mode = 1
        probes = []
        game.send = probes.append

        def delayed_turn():
            time.sleep(.1)  # Longer than the quiet window.
            writer.send(b'{"msg":"player","turn":1}\n{"msg":"flush_messages"}\n')

        thread = threading.Thread(target=delayed_turn)
        thread.start()
        try:
            game.settle(require_event=True)
            self.assertTrue(game.settled)
            self.assertEqual(game.state.player["turn"], 1)
            self.assertEqual(probes, [])
        finally:
            thread.join()


def snapshot_body(output):
    lines = output.splitlines()
    return json.loads("\n".join(lines[2:lines.index("</codex_snapshot>")]))


class ObservationTests(unittest.TestCase):
    def test_snapshot_tags_preserve_combat_and_stream_cursors(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            (session / "runtime.json").write_text('{"socket":"first"}')
            snapshot = self.snapshot()
            def emit(**kwargs):
                output = io.StringIO()
                with redirect_stdout(output):
                    emit_observation(session, snapshot, **kwargs)
                return output.getvalue()
            default = json.loads(emit(snapshot_tags=False))
            self.assertEqual(default["map"], snapshot["map"])
            tagged = emit(snapshot_tags=True)
            lines = tagged.splitlines()
            end = lines.index("</codex_snapshot>")
            ordinary, opening, body, closing = lines[0], lines[1], "\n".join(lines[2:end]), lines[end]
            self.assertEqual(opening, '<codex_snapshot key="crawl/map">')
            payload = json.loads(body)
            self.assertNotIn("map", json.loads(ordinary))
            self.assertEqual(payload["map"], snapshot["map"])
            self.assertEqual(closing, "</codex_snapshot>")
            self.assertNotIn("codex_snapshot", emit(snapshot_tags=True))
            snapshot["player"].update(depth=2, turn=2)
            snapshot["messages"].append({"text": "An orc hits you for 9!", "turn": 2})
            changed = emit(snapshot_tags=True)
            lines = changed.splitlines()
            ordinary, body = lines[0], "\n".join(lines[2:lines.index("</codex_snapshot>")])
            self.assertEqual(json.loads(ordinary)["messages"], snapshot["messages"][-1:])
            self.assertEqual(json.loads(body)["map"], snapshot["map"])
            self.assertEqual(json.loads(body)["depth"], 2)
            snapshot.pop("map")
            self.assertIsNone(snapshot_body(emit(snapshot_tags=True))["map"])
            self.assertNotIn("codex_snapshot", emit(snapshot_tags=True))
            self.assertIn("codex_snapshot", emit(snapshot_tags=True, full=True))
            inspector = emit(snapshot_tags=True, stream="review")
            self.assertEqual(inspector.splitlines()[1], opening)
            self.assertEqual(json.loads(inspector.splitlines()[0])["messages"], snapshot["messages"])
            renamed = emit(snapshot_tags=True, session_name="other")
            self.assertEqual(renamed.splitlines()[1], opening)
            self.assertNotEqual(snapshot_body(renamed)["session"], payload["session"])
            snapshot["map"] = default["map"]
            text = emit(snapshot_tags=True, text=True, full=True)
            self.assertIn("An orc hits you for 9!", text)
            self.assertIn("<codex_snapshot", text)
            (session / "runtime.json").write_text('{"socket":"reset"}')
            self.assertEqual(snapshot_body(emit(snapshot_tags=True))["sequence"], 1)

    def test_snapshot_session_directories_and_oversized_maps(self):
        keys = []
        identities = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as directory:
                session = Path(directory)
                (session / "runtime.json").write_text('{}')
                snapshot = self.snapshot()
                snapshot["map"]["rows"] = ["x" * 9000]
                output = io.StringIO()
                with redirect_stdout(output):
                    emit_observation(session, snapshot, snapshot_tags=True)
                ordinary, key, body, _ = output.getvalue().splitlines()[:4]
                self.assertEqual(json.loads(ordinary)["map"], snapshot["map"])
                self.assertIsNone(json.loads(body)["map"])
                self.assertLess(len(body.encode()), 8192)
                keys.append(key)
                identities.append(json.loads(body)["session"])
        self.assertEqual(keys, ['<codex_snapshot key="crawl/map">'] * 2)
        self.assertNotEqual(*identities)

    def test_snapshot_key_migration_reemits_map_without_replaying_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            (session / "runtime.json").write_text('{}')
            snapshot = self.snapshot()
            with redirect_stdout(io.StringIO()):
                emit_observation(session, snapshot, snapshot_tags=True)
            cache = session / "observation-default.json"
            saved = json.loads(cache.read_text())
            saved.pop("snapshot_key")  # Cursor written by the session-specific-key CLI.
            cache.write_text(json.dumps(saved))
            output = io.StringIO()
            with redirect_stdout(output):
                emit_observation(session, snapshot, snapshot_tags=True)
            lines = output.getvalue().splitlines()
            ordinary, opening, body, closing = lines[0], lines[1], "\n".join(lines[2:-1]), lines[-1]
            self.assertEqual(opening, '<codex_snapshot key="crawl/map">')
            self.assertEqual(closing, '</codex_snapshot>')
            self.assertEqual(json.loads(body)["map"], snapshot["map"])
            self.assertEqual(json.loads(ordinary)["messages"], [])
            self.assertEqual(json.loads(ordinary)["sequence"], saved["sequence"] + 1)
            output = io.StringIO()
            with redirect_stdout(output):
                emit_observation(session, snapshot, snapshot_tags=True)
            self.assertNotIn("codex_snapshot", output.getvalue())

    def test_cli_defaults_to_plain_json_and_can_switch_to_tagged_maps(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            (session / 'runtime.json').write_text('{}')
            for settings, tagged in [({}, False), ({'snapshot_tags': True}, True),
                                     ({'snapshot_tags': False}, False)]:
                output = io.StringIO()
                with patch('sys.argv', ['crawl-agent', '--session-dir', directory,
                                        'observe']), \
                        patch('dcss_harness.client.request', return_value=self.snapshot()) as request, \
                        patch('dcss_harness.cli.read_settings', return_value=settings), \
                        redirect_stdout(output):
                    self.assertEqual(main(), 0)
                request.assert_called_once()
                lines = output.getvalue().splitlines()
                ordinary = json.loads(lines[0])
                if tagged:
                    self.assertEqual(lines[1], '<codex_snapshot key="crawl/map">')
                    self.assertEqual(json.loads('\n'.join(lines[2:lines.index('</codex_snapshot>')]))['map'], self.snapshot()['map'])
                    self.assertNotIn('map', ordinary)
                else:
                    self.assertEqual(len(lines), 1)
                    self.assertEqual(ordinary['map'], self.snapshot()['map'])
                self.assertEqual(ordinary['messages'], self.snapshot()['messages'] if not settings else [])

    def snapshot(self):
        state = State()
        state.player = {"name": "Agent", "turn": 1, "hp": 20, "hp_max": 20}
        state.messages = [{"text": "Hello", "turn": 1}]
        return {**state.observation(), "running": True, "settled": True,
                "summary": "Duplicate presentation"}

    def test_unchanged_observation_omits_bulk_but_keeps_safety_state(self):
        before = self.snapshot()
        result = compact_observation(before, before)
        self.assertEqual(result["observation"], "delta")
        self.assertEqual(result["messages"], [])
        for key in ("map", "inventory", "ui", "text", "summary"):
            self.assertNotIn(key, result)
        for key in ("player", "monsters", "input_mode", "more", "running", "settled"):
            self.assertEqual(result[key], before[key])
        self.assertLess(len(json.dumps(result)), len(json.dumps(before)) // 2)

    def test_changes_and_clears_are_explicit_replacements(self):
        before = self.snapshot()
        before.update(inventory=[{"name": "axe"}], ui=[{"title": "Inventory"}],
                      text={"menu_txt": "axe"})
        after = self.snapshot()
        after["map"]["origin"] = [1, 2]
        result = compact_observation(after, before)
        for key in ("inventory", "ui", "text", "map"):
            self.assertEqual(result[key], after[key])

    def test_rolling_messages_rollback_and_same_turn_additions(self):
        a, b, c = [{"text": text, "turn": 1} for text in ("a", "b", "c")]
        self.assertEqual(message_changes([a, b], [b, c]), {"messages": [c]})
        self.assertEqual(message_changes([a, b], [a, c]),
                         {"messages": [c], "messages_rollback": 1})
        self.assertEqual(message_changes([a], [a, a]), {"messages": [a]})
        self.assertEqual(message_changes([a, b], [c]),
                         {"messages": [c], "messages_reset": True})
        self.assertEqual(message_changes([a], []),
                         {"messages": [], "messages_reset": True})

    def test_full_recovery_and_long_actions(self):
        before = self.snapshot()
        after = self.snapshot()
        after["messages"] += [{"text": str(n), "turn": n + 2} for n in range(30)]
        delta = compact_observation(after, before)
        self.assertEqual(len(delta["messages"]), 30)
        full = compact_observation(after, before, full=True)
        self.assertEqual(full["observation"], "full")
        self.assertEqual(len(full["messages"]), 31)
        self.assertIn("map", full)
        self.assertNotIn("summary", full)

    def test_output_streams_restart_recovery_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            runtime = session / "runtime.json"
            runtime.write_text('{"socket":"first"}')
            snapshot = self.snapshot()
            def emit(**kwargs):
                output = io.StringIO()
                with redirect_stdout(output):
                    emit_observation(session, snapshot, snapshot_tags=False, **kwargs)
                return json.loads(output.getvalue())
            self.assertEqual(emit()["observation"], "full")
            self.assertEqual(emit(stream="inspector")["sequence"], 1)
            second = emit()
            self.assertEqual((second["sequence"], second["base_sequence"]), (2, 1))
            self.assertEqual(second["messages"], [])
            self.assertEqual(emit(full=True)["observation"], "full")
            args = SimpleNamespace(full=False, text=False, stream="default")
            with patch("dcss_harness.client.request", return_value={"error": "no action"}), redirect_stdout(io.StringIO()):
                self.assertEqual(output_request(session, {"op": "act"}, args), 1)
            self.assertEqual(emit()["sequence"], 4)  # Error did not consume cursor.
            runtime.write_text('{"socket":"second"}')
            reset = emit()
            self.assertEqual((reset["observation"], reset["sequence"]), ("full", 1))
            (session / "observation-default.json").write_text('invalid')
            self.assertEqual(emit()["observation"], "full")


class ActionLogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "actions.jsonl"
        self.game = Game.__new__(Game)
        self.game.state = State()
        self.game.state.player = {"name": "Agent", "turn": 100, "place": "D",
                                  "depth": 2, "xl": 3,
                                  "inv": {"0": {"name": "payload", "quantity": 1}}}
        self.game.process = SimpleNamespace(poll=lambda: None)
        self.game.session = self.path.parent
        self.game.settled = True
        self.game.timeout = 5
        self.game.auto_more = True
        self.game.send = Mock()
        self.game.drain = Mock()
        self.game.decoder = SimpleNamespace(pending=b'')
        self.game.actions = ActionLog(self.path)
        self.addCleanup(self.game.actions.close)

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_action_metrics_include_multi_turn_progress_and_separate_idle_time(self):
        def settle(**kwargs):
            self.game.state.player.update(turn=120, depth=3, xl=4)
        self.game.settle = settle
        with patch("dcss_harness.metrics.time.monotonic", side_effect=[10, 12, 12, 12.125, 12.125]):
            self.game.actions.start(self.game.state.player)
            result = self.game.act([ord("o")], "explore")
        self.assertEqual(result["keys_sent"], 1)
        start, record = self.records()
        self.assertEqual(start["turn"], 100)
        self.assertEqual(record["turn_before"], 100)
        self.assertEqual(record["turn"], 120)
        self.assertEqual((record["place"], record["depth"], record["xl"]), ("D", 3, 4))
        self.assertEqual(record["latency_ms"], 125)
        self.assertEqual(record["idle_ms"], 2000)
        self.assertEqual(record["action"], "explore")
        self.assertNotIn("payload", self.path.read_text())
        self.assertLess(len(self.path.read_bytes()), 500)
        stats = action_stats(self.path)
        self.assertEqual(stats["observed_turns"], 20)
        self.assertEqual(stats["action_counts"], {"explore": 1})
        self.assertEqual(stats["latency_ms"], {"min": 125, "mean": 125, "max": 125})

    def test_timeout_logs_only_sent_keys_and_no_turn_advance(self):
        def settle(**kwargs):
            self.game.settled = False
        self.game.settle = settle
        self.game.actions.start(self.game.state.player)
        result = self.game.act([ord("i"), 27, ord(".")])
        self.assertEqual(result["keys_sent"], 1)
        self.game.send.assert_called_once_with({"msg": "key", "keycode": ord("i")})
        record = self.records()[-1]
        self.assertEqual((record["requested"], record["keys_sent"]), (3, 1))
        self.assertFalse(record["settled"])
        stats = action_stats(self.path)
        self.assertEqual(stats["unsettled_actions"], 1)
        self.assertEqual(stats["observed_turns"], 0)

    def test_failure_after_send_is_recorded_but_exited_game_is_not_an_action(self):
        self.game.actions.start(self.game.state.player)
        self.game.settle = Mock(side_effect=OSError("connection lost"))
        with self.assertRaises(OSError):
            self.game.act([ord(".")], "wait")
        record = self.records()[-1]
        self.assertEqual(record["keys_sent"], 1)
        self.assertEqual(record["error"], "connection lost")
        self.assertEqual(action_stats(self.path)["failed_actions"], 1)
        self.game.process.poll = lambda: 0
        with self.assertRaises(RuntimeError):
            self.game.act([ord(".")], "wait")
        self.assertEqual(len(self.records()), 2)

    def test_append_on_resume_and_stats_dont_count_turns_before_start(self):
        self.game.actions.start(self.game.state.player)
        self.game.settle = Mock()
        self.game.act([ord("i")], "inventory")
        self.game.actions.close()
        self.game.actions = ActionLog(self.path)
        self.addCleanup(self.game.actions.close)
        self.game.state.player["turn"] = 200
        self.game.actions.start(self.game.state.player)
        self.game.settle = lambda **kwargs: self.game.state.player.update(turn=201)
        self.game.act([ord(".")], "wait")
        stats = action_stats(self.path)
        self.assertEqual(stats["starts"], 2)
        self.assertEqual(stats["actions"], 2)
        self.assertEqual(stats["keys_sent"], 2)
        self.assertEqual(stats["action_counts"], {"inventory": 1, "wait": 1})
        self.assertEqual(stats["observed_turns"], 1)
        self.assertEqual(stats["latest"]["turn"], 201)
        self.assertEqual([r["n"] for r in self.records() if "n" in r], [1, 1])

    def test_stats_before_first_action_and_during_partial_write(self):
        self.game.actions.start(self.game.state.player)
        stats = action_stats(self.path)
        self.assertEqual(stats["actions"], 0)
        self.assertIsNone(stats["latency_ms"]["mean"])
        with self.path.open("a") as stream:
            stream.write('{"event":"act"')
        stats = action_stats(self.path)
        self.assertEqual(stats["starts"], 1)
        self.assertEqual(stats["actions"], 0)

    def test_protocol_logging_is_opt_in(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                session = Path(directory)
                config = {"settle_ms": 75, "timeout": 5, "name": "Agent",
                          "weapon": "hand axe", "binary": "crawl", "seed": None,
                          "species": "Minotaur", "background": "Berserker"}
                if enabled:
                    config["log_events"] = True
                with patch("dcss_harness.game.socket.socket"), patch("dcss_harness.game.subprocess.Popen") as launch:
                    game = Game(session, session, config)
                args, kwargs = launch.call_args
                self.assertNotIn("-headless", args[0])
                self.assertIn("-await-connection", args[0])
                self.assertEqual(kwargs["env"]["TERM"], "linux")
                self.assertEqual(kwargs["stdin"], kwargs["stdout"])
                game.process.poll.return_value = 0
                try:
                    game.send({"msg": "key", "keycode": 46})
                    game.sock.recv.side_effect = [b'{"msg":"player","turn":1}\n',
                                                  BlockingIOError()]
                    game.drain()
                    self.assertEqual(game.state.player["turn"], 1)
                    self.assertEqual((session / "events.jsonl").exists(), enabled)
                    if enabled:
                        records = (session / "events.jsonl").read_text().splitlines()
                        self.assertEqual([json.loads(r)["direction"] for r in records],
                                         ["in", "out"])
                finally:
                    game.close()


if __name__ == "__main__":
    unittest.main()
