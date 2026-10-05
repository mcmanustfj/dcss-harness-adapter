import unittest
from dcss_harness.state import State
from dcss_harness.hazards import local_hazard, HazardWatch
from dcss_harness.combat import policy


class HazardTests(unittest.TestCase):
    def test_flight_requires_opt_in_live_status_and_typed_water(self):
        water = {'kind': 'hazard', 'terrain_id': 'shallow_water', 'dx': 2, 'dy': 0, 'cells': [[2,0],[0,0]]}
        obs = {'player': {'status': [{'light': 'Fly'}]}, 'visible_features': [water]}
        rules = {'allow_water_with_flight': True}
        self.assertTrue(local_hazard(obs))
        self.assertFalse(local_hazard(obs, rules))
        obs['player']['status'] = []
        self.assertTrue(local_hazard(obs, rules))
        obs['player']['status'] = [{'light': 'Fly'}]
        for terrain in ('deep_water', 'lava', 'unknown', None):
            water['terrain_id'] = terrain
            self.assertEqual(local_hazard(obs, rules), terrain != 'deep_water')
        water['terrain_id'] = 'shallow_water'
        for feature in ({'kind': 'hazard', 'name': 'trap', 'dx': 0, 'dy': 0},
                        {'kind': 'hazard', 'cloud_type': 'poison', 'dx': 0, 'dy': 0}):
            obs['visible_features'] = [water, feature]
            self.assertTrue(local_hazard(obs, rules))
        with self.assertRaises(ValueError): policy({'allow_water_with_flight': 'true'})

    def test_assessed_cloud_is_exact_and_independent_of_water(self):
        cloud = {'kind': 'hazard', 'cloud_type': 'poison', 'dx': -1, 'dy': 0, 'cells': [[-1,0],[0,0]]}
        obs = {'player': {'status': [{'light': 'Fly'}]}, 'visible_features': [cloud]}
        rules = {'allow_cloud': ['poison']}
        self.assertTrue(local_hazard(obs))
        self.assertFalse(local_hazard(obs, rules))
        for kind in ('fire', 'cold', 'mephitic', 'unknown'):
            cloud['cloud_type'] = kind
            self.assertTrue(local_hazard(obs, rules))
        cloud['cloud_type'] = 'poison'
        cloud['possible_types'] = ['poison', 'unknown']
        self.assertTrue(local_hazard(obs, rules))
        del cloud['possible_types']
        obs['visible_features'].append({'kind':'hazard', 'terrain_id':'deep_water', 'dx':0, 'dy':0})
        self.assertTrue(local_hazard(obs, rules))
        self.assertFalse(local_hazard(obs, {**rules, 'allow_water_with_flight':True}))
        for value in (['fire'], ['all'], 'poison', None):
            with self.assertRaises(ValueError): policy({'allow_cloud': value})

    def test_transient_flight_loss_form_gear_and_damage_latch(self):
        for change, reason in (({'status': []}, 'hazard_assessment_changed'),
                               ({'form': 2}, 'hazard_assessment_changed'),
                               ({'weapon_index': 1}, 'hazard_assessment_changed'),
                               ({'hp': 99}, 'damage')):
            s = State()
            s.player = {'status': [{'light': 'Fly'}], 'form': 0, 'hp': 100, 'weapon_index': 0}
            old = dict(s.player)
            watch = HazardWatch(s, {'allow_water_with_flight': True})
            s.guard_observer = watch
            s.apply({'msg': 'player', **change})
            s.apply({'msg': 'player', **old})
            self.assertEqual(watch.reason, reason)
