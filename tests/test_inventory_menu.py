import copy
import json
from pathlib import Path
import unittest

from dcss_harness.state import State
from dcss_harness.presentation import compact_observation


class InventoryMenuTests(unittest.TestCase):
    def test_recorded_live_categories_shrink_and_grow_without_old_rows(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/inventory-categories.json').read_text())
        state, snapshots = State(), []
        for frame in fixture['frames']:
            for event in frame['events']:
                state.apply(event)
            snapshots.append(state.observation())
        menus = [s['ui'][-1] for s in snapshots]
        self.assertEqual([len(m['items']) for m in menus], [5, 2, 4])
        self.assertEqual([m['total_items'] for m in menus], [5, 2, 4])
        self.assertEqual(menus[1]['items'][1]['hotkeys'], [ord('m'), ord('!')])
        self.assertNotIn('worn', json.dumps(menus[1:]))
        self.assertEqual(len(compact_observation(snapshots[1], snapshots[0])['ui'][-1]['items']), 2)
        self.assertEqual(fixture, json.loads((Path(__file__).parent / 'fixtures/inventory-categories.json').read_text()))

    def test_initial_nonzero_chunk_out_of_order_updates_and_size_bound(self):
        state = State()
        state.apply({'msg': 'menu', 'total_items': 5, 'chunk_start': 3,
                     'items': [{'text': 'three'}, {'text': 'four'}]})
        self.assertEqual(state.ui[-1]['items'], [{}, {}, {}, {'text': 'three'}, {'text': 'four'}])
        state.apply({'msg': 'update_menu_items', 'chunk_start': 1, 'items': [{'text': 'one'}]})
        self.assertEqual(state.ui[-1]['items'][3]['text'], 'three')
        state.apply({'msg': 'update_menu', 'total_items': 2})
        self.assertEqual(state.ui[-1]['items'], [{}, {'text': 'one'}])
        state.apply({'msg': 'update_menu_items', 'chunk_start': 3, 'items': [{'text': 'out of bounds'}]})
        self.assertEqual(len(state.ui[-1]['items']), 2)
        state.apply({'msg': 'update_menu', 'total_items': 5})
        self.assertEqual(state.ui[-1]['items'][2:], [{}, {}, {}])
        state.apply({'msg': 'update_menu', 'total_items': 0})
        self.assertEqual(state.ui[-1]['items'], [])

    def test_replaced_row_clears_omitted_keys_but_null_chunk_keeps_row(self):
        state = State()
        state.apply({'msg': 'menu', 'total_items': 3, 'items': [
            {'text': 'item', 'hotkeys': [97], 'colour': 4, 'tiles': [123]},
            {'text': 'other', 'hotkeys': [98]}, {'text': 'last', 'hotkeys': [99]}]})
        event = {'msg': 'update_menu_items', 'chunk_start': 0,
                 'items': [{'text': 'Header', 'level': 1}, None, 'plain string']}
        untouched = copy.deepcopy(event)
        state.apply(event)
        self.assertEqual(event, untouched)
        rows = state.ui[-1]['items']
        self.assertEqual(rows[0], {'text': 'Header', 'level': 1})
        self.assertEqual(rows[1]['hotkeys'], [98])
        self.assertEqual(rows[2], {'type': 2, 'text': 'plain string'})

    def test_same_size_refresh_title_only_and_nested_menu_isolation(self):
        state = State()
        state.apply({'msg': 'menu', 'total_items': 1, 'items': [{'text': 'gear', 'hotkeys': [97]}]})
        state.apply({'msg': 'update_menu', 'title': {'text': 'Potions'}, 'total_items': 1})
        state.apply({'msg': 'update_menu_items', 'chunk_start': 0, 'items': [{'text': 'potion', 'hotkeys': [112]}]})
        state.apply({'msg': 'menu', 'total_items': 2, 'chunk_start': 1, 'items': [{'text': 'description'}]})
        state.apply({'msg': 'update_menu', 'total_items': 0})
        state.apply({'msg': 'close_menu'})
        self.assertEqual(state.ui[-1]['items'], [{'text': 'potion', 'hotkeys': [112]}])


if __name__ == '__main__':
    unittest.main()
