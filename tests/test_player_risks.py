import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dcss_harness.game import Game
from dcss_harness.state import State
from dcss_harness.presentation import compact_observation, observation_text
from dcss_harness.combat import run as combat
from dcss_harness.recovery import run as recovery
from dcss_harness.ranged import run as ranged
from test_ranged import Replay, OPTIONS


class PlayerRiskTests(unittest.TestCase):
    def test_public_fixture_values_updates_clearing_and_unknown(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/player-risks.json').read_text())
        state = State()
        state.mode = 1
        self.assertNotIn('doom', state.observation()['player'])
        previous = None
        for frame in fixture['frames']:
            state.apply(copy.deepcopy(frame['event']))
            obs = state.observation()
            for field, expected in frame['expected'].items():
                self.assertEqual(obs['player'][field], expected)
            delta = compact_observation(obs, previous)
            self.assertEqual(delta['player'], {k: v for k, v in obs['player'].items() if k != 'doom_desc'})
            if previous and previous['player'].get('doom_desc') == obs['player'].get('doom_desc'):
                self.assertNotIn('player_descriptions', delta)
            elif 'doom_desc' in obs['player']:
                self.assertEqual(delta['player_descriptions']['doom_desc'], obs['player']['doom_desc'])
            previous = obs
        result = dict(previous, running=True, settled=True, observation='delta', sequence=4)
        self.assertIn('Doom: 0%', observation_text(result))
        self.assertIn('Contamination: 0%', observation_text(result))

    def game(self, operation, doom=45, contam=0):
        g = Game.__new__(Game)
        g.state = State()
        g.state.player = dict(hp=90, hp_max=100, mp=5, mp_max=5, turn=10,
                             xl=3, place='Dungeon', depth=2, pos={'x':0,'y':0},
                             status=[], weapon_index=-1, doom=doom, contam=contam)
        g.state.mode = 1
        if operation != 'recover':
            g.state.cells[(1 if operation == 'combat' else 4, 0)] = {
                't': {'bg':0}, 'mon':{'id':1,'name':'goblin','att':0,'threat':0,'type':1}}
        g.process = SimpleNamespace(poll=lambda: None)
        g.settled, g.auto_more, g.timeout = True, True, 5
        g.session, g.actions, g.send = Path('/tmp/risk-test'), Mock(count=0), Mock()
        return g

    def invoke(self, g, operation):
        if operation == 'combat':
            return combat(g, {'max_actions':2})['combat']
        rules = {'max_actions':2}
        if operation == 'wait-for': rules['monster_id'] = 1
        return recovery(g, operation, rules)['recovery']

    def test_helpers_stop_on_gains_even_if_meter_clears_before_settling(self):
        for operation in ('combat','recover','wait-for'):
            for field in ('doom','contam'):
                for clear in (False,True):
                    with self.subTest(operation=operation,field=field,clear=clear):
                        g = self.game(operation)
                        old = g.state.player[field]
                        def settle(**kw):
                            if kw.get('require_event'):
                                g.state.apply({'msg':'player','turn':11,field:old+10})
                                if clear:g.state.apply({'msg':'player',field:0})
                        g.settle = Mock(side_effect=settle)
                        result = self.invoke(g,operation)
                        self.assertEqual(result['stop_reason'],field+'_increased')
                        self.assertEqual(result['actions'],1)
                        self.assertEqual(g.send.call_count,1)
                        self.assertEqual(g.state.player['status'],[])
                        self.assertIsNone(g.state.guard_observer)

    def test_existing_doom_does_not_trigger_a_rest_to_clear_it(self):
        g = self.game('recover')
        g.state.player.update(hp=100)
        g.settle = Mock()
        result = self.invoke(g,'recover')
        self.assertEqual(result['stop_reason'],'recovered')
        g.send.assert_not_called()
        self.assertEqual(g.observe()['player']['doom'],45)

    def test_decrease_then_gain_below_initial_value_still_stops(self):
        g=self.game('combat')
        def settle(**kw):
            if kw.get('require_event'):
                for value in (20,30):g.state.apply({'msg':'player','doom':value,'turn':11})
        g.settle=Mock(side_effect=settle)
        self.assertEqual(self.invoke(g,'combat')['stop_reason'],'doom_increased')

    def test_ranged_preparation_stops_on_meter_change(self):
        for field in ('doom','contam'):
            def mutate(s,n):
                if n==2:s.apply({'msg':'player',field:10})
            g=Replay(mutate=mutate)
            result=ranged(g,OPTIONS)
            self.assertEqual(result['ranged']['stop_reason'],'player_changed_during_preparation')
            self.assertFalse(result['ranged']['submitted'])

    def test_other_public_metadata_and_zero_values_survive(self):
        fields = dict(poison_survival=12,real_hp_max=120,dd_real_mp_max=0,penance=1,
                      ostracism_pips=2,form=3,ac_mod=400,ev_mod=-200,sh_mod=0,
                      lives=2,deaths=1,species_display_name='Minotaur',offhand_index=-1,
                      offhand_weapon=0,unarmed_attack='Nothing wielded',quiver_item=0,
                      quiver_available=1,noise=-1,adjusted_noise=333,wizard=0,explore=0,
                      time_last_input=100,weapon_colour=10,offhand_weapon_colour=0)
        s=State();s.mode=1;s.apply({'msg':'player',**fields})
        self.assertEqual(s.observation()['player'],fields)
        cleared={k:0 for k,v in fields.items() if isinstance(v,int)}
        s.apply({'msg':'player',**cleared})
        self.assertEqual(s.observation()['player'],{**fields,**cleared})
