"""Reconstruct public inventory from ordinary deltas and full tagged snapshots."""

import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

from dcss_harness.presentation import compact_observation, emit_observation, observation_text


def item(slot, namespace="equipment", **updates):
    return {"slot": slot, "letter_namespace": namespace, "letter": "a",
            "category": "weapons", "name": "+0 axe", "name_current": True,
            "quantity": 1, **updates}


def snapshot(inventory):
    return {"player": {"name": "test", "turn": 1}, "inventory": inventory,
            "messages": [], "input_mode": "command", "running": True,
            "settled": True, "map": {"origin": [0, 0], "rows": ["@"]}}


def apply_inventory(inventory, output):
    """Consumer written independently of the encoder; keep exact list order."""
    if "inventory" in output:
        return copy.deepcopy(output["inventory"])
    if "inventory_table" in output:
        table = output["inventory_table"]
        return [{**dict(zip(table["columns"], row)), **table.get("extra", {}).get(str(index), {})}
                for index, row in enumerate(table["rows"])]
    result = copy.deepcopy(inventory)
    delta = output.get("inventory_delta", {})
    def key(entry):
        return entry["slot"], entry["letter_namespace"]
    for removed in delta.get("remove", []):
        result = [entry for entry in result if key(entry) != key(removed)]
    for updated in delta.get("upsert", []):
        position = next((i for i, entry in enumerate(result) if key(entry) == key(updated)), len(result))
        result[position:position + 1] = [copy.deepcopy(updated)]
    if "order" in delta:
        result = [next(entry for entry in result if key(entry) == key(ref))
                  for ref in delta["order"]]
    return result


class InventoryDeltaTests(unittest.TestCase):
    def test_pickup_use_recharge_names_letters_and_namespace_changes(self):
        states = [[item(0), item(52, "potions", name="2 potions of curing", quantity=2)],
                  [item(0), item(52, "potions", name="3 potions of curing", quantity=3),
                   item(53, "wands", name="wand of flame (3)")]]
        for change in ({"name": "wand of flame (2)"}, {"name": "wand of flame (5)"},
                       {"letter": "z"}, {"name_current": False},
                       {"name_current": True, "name": "wand of flame (4)"},
                       {"slot": 54}, {"letter_namespace": "other"}):
            after = copy.deepcopy(states[-1])
            after[-1].update(change)
            states.append(after)
        after = copy.deepcopy(states[-1])
        after[-1].pop("letter")  # Removed fields must not survive an upsert.
        states.extend([after, after[1:], [], [item(0)]])
        reconstructed = states[0]
        for before, after in zip(states, states[1:]):
            delta = compact_observation(snapshot(after), snapshot(before))
            self.assertNotIn("inventory", delta)
            reconstructed = apply_inventory(reconstructed, delta)
            self.assertEqual(reconstructed, after)
            self.assertNotIn(item(0), delta["inventory_delta"]["upsert"] if before and after else [])
        same = compact_observation(snapshot(after), snapshot(after))
        self.assertNotIn("inventory_delta", same)

    def test_reorder_reused_slots_and_duplicate_letters(self):
        before = [item(0), item(52, "potions"), item(52, "scrolls")]
        after = [before[2], item(0, name="+2 sling"), item(53, "wands"), before[1]]
        delta = compact_observation(snapshot(after), snapshot(before))
        self.assertIn("order", delta["inventory_delta"])
        self.assertEqual(apply_inventory(before, delta), after)
        reordered = compact_observation(snapshot(before[::-1]), snapshot(before))
        self.assertEqual(reordered["inventory_delta"]["upsert"], [])
        self.assertEqual(apply_inventory(before, reordered), before[::-1])

    def test_full_and_unknown_identity_replacements(self):
        current = snapshot([item(0)])
        for previous in (None, snapshot([{"name": "old"}]), snapshot([item(0), item(0)])):
            self.assertEqual(compact_observation(current, previous)["inventory"], current["inventory"])
        self.assertEqual(compact_observation(current, current, full=True)["inventory"], current["inventory"])
        delta = compact_observation(snapshot([]), current)
        self.assertEqual(delta["inventory_delta"]["remove"], [{"slot": 0, "letter_namespace": "equipment"}])
        delta["sequence"] = 2
        self.assertIn("inventory_delta:", observation_text(delta))

    def test_randomized_reconstruction(self):
        rng = random.Random(124)
        before = []
        reconstructed = []
        for _ in range(150):
            after = [item(slot, quantity=rng.randint(1, 5)) for slot in rng.sample(range(12), rng.randint(0, 12))]
            output = compact_observation(snapshot(after), snapshot(before))
            reconstructed = apply_inventory(reconstructed, output)
            self.assertEqual(reconstructed, after)
            before = after



class InventorySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Path(self.directory.name)
        self.runtime = self.session / "runtime.json"
        self.runtime.write_text('{"socket":"one"}')
        self.state = snapshot([item(0), item(52, "wands", name="wand of flame (3)")])

    def emit(self, **options):
        options.setdefault("snapshot_tags", True)
        output = io.StringIO()
        with redirect_stdout(output):
            emit_observation(self.session, self.state, **options)
        lines = output.getvalue().splitlines()
        ordinary = json.loads(lines[0])
        blocks = {}
        i = 1
        while i < len(lines):
            key = lines[i].split('"')[1]
            end = lines.index("</codex_snapshot>", i + 1)
            blocks[key] = json.loads("\n".join(lines[i + 1:end]))
            i = end + 1
        return ordinary, blocks

    def test_tagged_base_deltas_and_full_fetch(self):
        full, blocks = self.emit()
        self.assertEqual(set(blocks), {"crawl/map", "crawl/inventory"})
        self.assertNotIn("inventory", full)
        inventory = blocks["crawl/inventory"]["inventory"]
        self.assertEqual(blocks["crawl/inventory"]["sequence"], full["sequence"])
        self.state["inventory"][1]["name"] = "wand of flame (2)"
        delta, blocks = self.emit()
        self.assertEqual(blocks, {})
        self.assertEqual(delta["base_sequence"], full["sequence"])
        self.assertEqual(len(delta["inventory_delta"]["upsert"]), 1)
        self.assertEqual(apply_inventory(inventory, delta), self.state["inventory"])
        unchanged, blocks = self.emit()
        self.assertNotIn("inventory_delta", unchanged)
        self.assertEqual(blocks, {})
        recovered, blocks = self.emit(full=True)
        self.assertEqual(blocks["crawl/inventory"]["inventory"], self.state["inventory"])
        self.assertNotIn("inventory_delta", recovered)
        self.state["inventory"] = []
        empty, blocks = self.emit()
        self.assertEqual(apply_inventory(inventory, empty), [])
        self.assertEqual(blocks, {})
        self.assertEqual(self.emit(full=True)[1]["crawl/inventory"]["inventory"], [])

    def test_streams_migration_and_format_changes_preserve_messages(self):
        self.state["messages"] = [{"text": "hello", "turn": 1}]
        self.emit()
        cache = self.session / "observation-default.json"
        saved = json.loads(cache.read_text())
        saved.pop("inventory_format")
        saved.pop("inventory_snapshot_key")
        cache.write_text(json.dumps(saved))
        migrated, blocks = self.emit()
        self.assertEqual(set(blocks), {"crawl/inventory"})
        self.assertEqual(migrated["messages"], [])
        for options, tagged in [({"snapshot_tags": False}, False), ({}, True)]:
            ordinary, blocks = self.emit(**options)
            self.assertEqual(ordinary["messages"], [])
            self.assertEqual((blocks["crawl/inventory"] if tagged else ordinary)["inventory"], self.state["inventory"])
            self.assertNotIn("inventory_delta", ordinary)
        reviewer, blocks = self.emit(stream="review")
        self.assertEqual(reviewer["messages"], self.state["messages"])
        self.assertIn("crawl/inventory", blocks)
        self.assertEqual(self.emit()[1], {})
        self.assertIn("crawl/inventory", self.emit(session_name="other")[1])

    def test_restart_corrupt_cursor_and_plain_json_recovery(self):
        self.emit(snapshot_tags=False)
        self.state["inventory"].pop()
        delta, blocks = self.emit(snapshot_tags=False)
        self.assertIn("inventory_delta", delta)
        self.assertEqual(blocks, {})
        self.runtime.write_text('{"socket":"two"}')
        restarted, blocks = self.emit()
        self.assertEqual(restarted["sequence"], 1)
        self.assertEqual(blocks["crawl/inventory"]["inventory"], self.state["inventory"])
        (self.session / "observation-default.json").write_text('invalid')
        recovered, blocks = self.emit(snapshot_tags=False)
        self.assertEqual(recovered["inventory"], self.state["inventory"])
        self.assertEqual(recovered["observation"], "full")

    def test_oversized_inventory_preserved_and_old_snapshot_cleared(self):
        self.emit()
        self.state["inventory"][0]["name"] = "x" * 9000
        ordinary, blocks = self.emit(full=True)
        self.assertEqual(ordinary["inventory"], self.state["inventory"])
        self.assertIsNone(blocks["crawl/inventory"]["inventory"])
        self.assertIn("size limit", blocks["crawl/inventory"]["reason"])
        self.state["inventory"][0]["name"] = "+1 axe"
        delta, blocks = self.emit()
        self.assertEqual(blocks, {})
        self.assertEqual(apply_inventory(ordinary["inventory"], delta), self.state["inventory"])

    def test_large_full_table_and_deltas_preserve_namespaces_and_order(self):
        self.state["inventory"] = [item(n, name=f'+7 armour of the Long Named Hero {n}') for n in range(52)]
        self.state["inventory"] += [item(52, "potions"), item(52, "scrolls", name="identify")]
        for options in ({}, {"full": True}, {"stream": "startup"}):
            ordinary, blocks = self.emit(**options)
            self.assertNotIn("inventory", ordinary)
            table = blocks["crawl/inventory"]
            self.assertIn("inventory_table", table)
            inventory = apply_inventory([], table)
            self.assertEqual(inventory, self.state["inventory"])
            self.assertLess(len(json.dumps(table, ensure_ascii=True, separators=(",", ":")).encode()) + 60, 8192)
        self.state["inventory"][0]["quantity"] = 3
        self.state["inventory"][-1]["name_current"] = False
        delta, blocks = self.emit()
        self.assertEqual(blocks, {})
        self.assertEqual(apply_inventory(inventory, delta), self.state["inventory"])
        self.assertEqual(self.emit(snapshot_tags=False)[0]["inventory"], self.state["inventory"])

    def test_table_preserves_missing_extra_and_null_fields(self):
        from dcss_harness.presentation import inventory_table
        items = [item(0), item(1, name=None, future={"x": 1}), item(52, "wands")]
        items[2].pop("letter")
        self.assertEqual(apply_inventory([], {"inventory_table": inventory_table(items)}), items)

    def test_table_encoder_upgrade_refreshes_existing_fallback(self):
        self.emit()
        cache = self.session / "observation-default.json"
        saved = json.loads(cache.read_text())
        saved["inventory_format"] = 1
        cache.write_text(json.dumps(saved))
        ordinary, blocks = self.emit()
        self.assertEqual(apply_inventory([], blocks["crawl/inventory"]), self.state["inventory"])
        self.assertEqual(ordinary["messages"], [])


if __name__ == "__main__":
    unittest.main()
