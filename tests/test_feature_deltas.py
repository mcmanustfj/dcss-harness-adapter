import copy
import json
import random
import unittest
from dcss_harness.presentation import compact_observation


def apply(old, output):
    if 'visible_features' in output: return copy.deepcopy(output['visible_features'])
    out = copy.deepcopy(old)
    for e in output.get('visible_features_delta', {}).get('splices', []):
        out[e['start']:e['start'] + e['delete']] = e['items']
    return out


class FeatureDeltaTests(unittest.TestCase):
    def test_stationary_cloud_changes_preserve_terrain_and_routes(self):
        water = [{'kind': 'hazard', 'name': 'some deep water', 'dx': i, 'dy': 2,
                  'cells': [[i, j] for j in range(15)],
                  'navigation': {'status': 'visible_route', 'steps': [{'move': 'se', 'count': 3}], 'text': '3 southeast; target adjacent east'}} for i in range(12)]
        before = {'player': {}, 'visible_features': water, 'messages': []}
        state = copy.deepcopy(water)
        raw_bytes = delta_bytes = 0
        for i in range(40):
            new = copy.deepcopy(water) + ([{'kind': 'cloud', 'name': 'thin mist', 'cells': [[0,0]], 'dx': 0, 'dy': 0}] if i % 2 == 0 else [])
            obs = {**before, 'visible_features': new}
            delta = compact_observation(obs, before)
            state = apply(state, delta)
            self.assertEqual(state, new)
            raw_bytes += len(json.dumps({'visible_features': new}))
            delta_bytes += len(json.dumps({k:v for k,v in delta.items() if k.startswith('visible_features')}))
            before = obs
        self.assertLess(delta_bytes, raw_bytes / 10)
        print('stationary feature bytes:', raw_bytes, '->', delta_bytes)
        self.assertEqual(compact_observation(before, before, full=True)['visible_features'], state)
        self.assertEqual(compact_observation({**before, 'visible_features': []}, before)['visible_features'], [])
        movement_raw = movement_delta = 0
        for i in range(20):
            new = copy.deepcopy(before['visible_features'])
            for f in new:
                f['dx'] -= 1
                f['cells'] = [[x-1,y] for x,y in f['cells']]
            after = {**before, 'visible_features': new}
            out = compact_observation(after, before)
            self.assertEqual(apply(before['visible_features'], out), new)
            movement_raw += len(json.dumps({'visible_features': new}))
            movement_delta += len(json.dumps({k:v for k,v in out.items() if k.startswith('visible_features')}))
            before = after
        self.assertLessEqual(movement_delta, movement_raw)
        print('moving feature bytes:', movement_raw, '->', movement_delta)

    def test_random_changes_movement_and_resets_reconstruct_exactly(self):
        rng = random.Random(43)
        old = []
        for step in range(300):
            new = copy.deepcopy(old)
            if rng.randrange(2) and new: new.pop(rng.randrange(len(new)))
            new.insert(rng.randrange(len(new)+1), {'kind': rng.choice(['hazard','cloud','item']), 'dx': rng.randrange(-8,9), 'cells': [[0,0]], 'name': 'a'*80})
            if step % 3 == 0: rng.shuffle(new)
            before, after = ({'player': {}, 'messages': [], 'visible_features': v} for v in (old, new))
            output = compact_observation(after, before)
            self.assertEqual(apply(old, output), new)
            self.assertEqual(apply([], compact_observation(after, None)), new)
            old = new
