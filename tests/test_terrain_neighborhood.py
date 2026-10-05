import unittest
from unittest.mock import patch
from dcss_harness.state import State
from dcss_harness.cli import main
from dcss_harness.map import terrain_neighborhood


class TerrainNeighborhoodTests(unittest.TestCase):
    def test_diagonal_names_visibility_and_origin_reset(self):
        s = State()
        s.player = {'pos': {'x': 3, 'y': -3}, 'place': 'Swamp', 'depth': 2}
        s.cells = {(4, -2): {'f': 4, 't': {'bg': 0}},
                   (2, -4): {'f': 7, 't': {'bg': 0x20000}},
                   (3, -3): {'f': 0, 't': {'bg': 0}}}
        r = terrain_neighborhood(s, 0, 0)
        self.assertEqual(len(r['cells']), 9)
        self.assertEqual((r['cells'][8]['name'], r['cells'][8]['visibility']), ('tree', 'visible'))
        self.assertEqual((r['cells'][0]['terrain_id'], r['cells'][0]['visibility']), ('rock_wall', 'remembered'))
        self.assertEqual(r['cells'][1]['visibility'], 'unknown')
        self.assertIsNone(r['cells'][4]['name'])
        self.assertEqual(terrain_neighborhood(s, 1, 1)['cells'][4]['terrain_id'], 'tree')
        s.apply({'msg': 'map', 'clear': True, 'cells': [{'x': 0, 'y': 0, 'f': 7, 't': {'bg': 0}}]})
        s.player['pos'] = {'x': 0, 'y': 0}
        r = terrain_neighborhood(s, 0, 0)
        self.assertEqual(r['cells'][4]['terrain_id'], 'rock_wall')
        self.assertEqual(r['cells'][8]['visibility'], 'unknown')
        s.map_player_on_level = False
        with self.assertRaises(ValueError): terrain_neighborhood(s, 0, 0)

    def test_cli_sends_only_read_query(self):
        with patch('sys.argv', ['crawl-agent', 'terrain', '--dx', '1', '--dy', '-2']), \
                patch('dcss_harness.cli.output_request', return_value=0) as request:
            main()
        self.assertEqual(request.call_args.args[1], {'op': 'terrain', 'dx': 1, 'dy': -2})
