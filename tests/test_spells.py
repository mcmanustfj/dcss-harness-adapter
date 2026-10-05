import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from test_ranged import Replay as RangedReplay
from dcss_harness.spells import policy, run, spell_menu
from dcss_harness.safety import CONSTANTS
from dcss_harness.cli import main

BASE = Path(__file__).parent / 'fixtures'
OPTIONS = {'spell_letter':'b','expect_name':'Searing Ray','monster_id':2,
           'max_failure_percent':5,'min_mp_after':0,'channel_actions':3,'allow_status':['ice-armoured']}


class Replay(RangedReplay):
    def __init__(self, fixture='spell-ray-live.json', **kw):
        super().__init__(frames=json.loads((BASE/fixture).read_text())['frames'], **kw)

    def settle_input(self, **kw):
        mutate, self.mutate = self.mutate, None
        super().settle_input(**kw)
        count = 0
        while self.frames and self.frames[0].get('input') == {'msg':'key','keycode':32}:
            frame = self.frames.pop(0)
            for event in frame['events']: self.state.apply(event)
            count += 1
        self.mutate = mutate
        if mutate and self.sent:
            mutate(self.state, len(self.sent))
        return count


class SpellTests(unittest.TestCase):
    def cast(self, game, **changes):
        return run(game, {**OPTIONS, **changes})['cast']

    def test_native_ray_exactly_three_pulses_and_public_reservation(self):
        game = Replay()
        info = self.cast(game)
        self.assertEqual(info['stop_reason'], 'channel_complete', info)
        self.assertEqual(info['actions'], 4)
        self.assertEqual(info['channel']['pulses'], 3)
        self.assertEqual(info['channel']['evidence'], [['Ray'], ['Ray+'], ['Ray++']])
        self.assertEqual(info['resolved_spell']['mp_cost'], 2)
        self.assertEqual(info['auto_more'], 1)
        self.assertEqual(sum(m.get('keycode') == 46 for m in game.sent), 3)
        self.assertIsNone(game.state.guard_observer)

    def test_prepare_only_freeze_and_two_adjacent_targets(self):
        game = Replay()
        info = self.cast(game, channel_actions=0, prepare_only=True)
        self.assertEqual(info['stop_reason'], 'prepared', info)
        self.assertFalse(info['submitted'])
        self.assertEqual(info['turns'], 0)
        self.assertTrue(info['targeting_open'])
        game = Replay('spell-freeze-live.json')
        game.state.apply({'msg':'map','cells':[{'x':2,'y':1,'t':{'bg':0},
            'mon':{'id':900,'type':1,'name':'goblin','att':0,'threat':0}}]})
        info = self.cast(game, spell_letter='c', expect_name='Freeze', channel_actions=0, allow_status=[])
        self.assertEqual(info['stop_reason'], 'cast_resolved', info)
        self.assertEqual(info['resolved_target']['id'], 2)
        self.assertEqual(info['actions'], 1)
        self.assertEqual(info['resolved_spell']['mp_cost'], 1)

    def test_instant_prepare_active_and_unsupported_send_no_input(self):
        for opts, reason in (({'expect_name':"Ozocubu's Armour", 'monster_id':None, 'prepare_only':True, 'allow_status':[]}, 'cannot_prepare_instant'),
                             ({'expect_name':'Hailstorm'}, 'unsupported_spell'),
                             ({'expect_name':'Frozen Ramparts'}, 'unsupported_spell'),
                             ({'expect_name':'Mephitic Cloud'}, 'unsupported_spell')):
            game = Replay()
            game.state.player['status'] = []
            info = self.cast(game, channel_actions=0, **opts)
            self.assertEqual(info['stop_reason'], reason)
            self.assertFalse(game.sent)
        game = Replay()
        info = self.cast(game, expect_name="Ozocubu's Armour", monster_id=None, channel_actions=0)
        self.assertEqual(info['stop_reason'], 'already_active')
        self.assertFalse(game.sent)

    def test_native_instant_buff_selects_once_without_enter(self):
        game = Replay('spell-armour-live.json')
        info = self.cast(game, spell_letter='d', expect_name="Ozocubu's Armour", monster_id=None,
                         channel_actions=0, allow_status=[])
        self.assertEqual(info['stop_reason'], 'cast_resolved', info)
        self.assertEqual(info['actions'], 1)
        self.assertEqual(info['turns'], 1)
        self.assertFalse(any(m.get('keycode')==13 for m in game.sent))

    def test_friendly_crossing_target_motion_and_mp_loss_stop_channel(self):
        def friend(s):
            s.apply({'msg':'map','cells':[{'x':1,'y':1,'mon':{'id':99,'att':4,'name':'ice beast','type':1,'threat':0}}]})
        def moved(s):
            m=copy.deepcopy(s.cells[(2,2)]['mon'])
            s.apply({'msg':'map','cells':[{'x':2,'y':2,'mon':None},{'x':2,'y':3,'t':{'bg':0},'mon':m}]})
        def mp(s):
            s.apply({'msg':'player','mp':s.player['mp']-2})
        for change,reason in ((friend,'monster_changed'),(moved,'target_moved'),(mp,'unexpected_mp_loss')):
            game=Replay(mutate=lambda s,n: change(s) if n==6 else None)
            info=self.cast(game)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(info['channel']['pulses'],0)

    def test_damage_after_regeneration_and_risk_rise_after_decrease(self):
        def damage(s,n):
            if n==6:
                s.apply({'msg':'player','hp':48})
                s.apply({'msg':'player','hp':47})
        game=Replay(mutate=damage)
        game.state.player.update(hp=45,hp_max=50)
        self.assertEqual(self.cast(game)['stop_reason'],'damage')
        def risk(s,n):
            if n==6:
                s.apply({'msg':'player','contam':10})
                s.apply({'msg':'player','contam':11})
        game=Replay(mutate=risk)
        game.state.player['contam']=20
        self.assertEqual(self.cast(game)['stop_reason'],'contam_increased')

    def test_stale_letter_failure_and_budget_stop_before_selection(self):
        for opts, reason in (({'spell_letter':'f'},'spell_identity_mismatch'),
                             ({'min_mp_after':8},'insufficient_mp_reserve')):
            game = Replay()
            info = self.cast(game, **opts)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(len(game.sent),2)
        def failure(s,n):
            if n==2:
                for r in s.ui[-1]['items']:
                    if 'Searing Ray' in r['text']: r['text']=r['text'].replace('0%', '99%')
        info=self.cast(Replay(mutate=failure))
        self.assertEqual(info['stop_reason'],'failure_ceiling',info)
        def stats(s,n):
            if n==2: s.apply({'msg':'player','int':s.player['int']-1})
        game=Replay(mutate=stats)
        info=self.cast(game)
        self.assertEqual(info['stop_reason'],'player_changed')
        self.assertEqual(len(game.sent),2)

    def test_dangers_and_missing_channel_evidence_prevent_extra_inputs(self):
        def no_ray(s):
            s.apply({'msg':'player','status':[{'text':'ice-armoured'}]})
        def damage(s):
            hp=s.player['hp'];s.apply({'msg':'player','hp':hp-1});s.apply({'msg':'player','hp':hp})
        def doom(s):
            s.apply({'msg':'player','doom':1});s.apply({'msg':'player','doom':0})
        def lost(s):
            s.apply({'msg':'map','cells':[{'x':2,'y':2,'mon':None}]})
        def path(s):
            s.apply({'msg':'map','cells':[{'x':1,'y':1,'f':7}]})
        def miscast(s):
            no_ray(s);s.apply({'msg':'msgs','messages':[{'text':'You miscast Searing Ray.'}]})
        for mutate, reason in ((no_ray,'channel_not_active'),(damage,'damage'),(doom,'doom_increased'),
                               (lost,'monster_changed'),(path,'channel_path_changed'),(miscast,'channel_not_active')):
            game=Replay(mutate=lambda s,n: mutate(s) if n==6 else None)
            info=self.cast(game)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(info['channel']['pulses'],0)
            self.assertEqual(info['actions'],1)
        game=Replay(mutate=lambda s,n: no_ray(s) if n==7 else None)
        info=self.cast(game)
        self.assertEqual(info['channel']['pulses'],1)
        self.assertEqual(info['stop_reason'],'channel_complete_or_changed')

    def test_timeout_and_transport_failure_never_retry(self):
        for at in (3,6,7):
            game=Replay(timeout=at)
            info=self.cast(game)
            self.assertEqual(len(game.sent),at)
            self.assertIn(info['stop_reason'], ('unsettled','submission_uncertain'))
        for at in (3,6,7):
            game=Replay(fail_send=at)
            info=self.cast(game)
            self.assertEqual(len(game.sent),at)
            self.assertEqual(info['submitted'],at>=6)

    def test_invalid_preview_and_native_confirmation_never_submit(self):
        def invalid(s,n):
            if n==5: s.cells[(2,2)]['t']['ov']=[CONSTANTS['TILE_RAY_OUT_OF_RANGE']]
        game=Replay(mutate=invalid)
        info=self.cast(game)
        self.assertFalse(info['submitted'])
        self.assertEqual(info['stop_reason'],'target_not_in_positive_preview')
        def prompt(s,n):
            if n==6: s.apply({'msg':'input_mode','mode':8})
        game=Replay(mutate=prompt)
        info=self.cast(game)
        self.assertEqual(info['channel']['pulses'],0)
        self.assertEqual(len(game.sent),6)

    def test_policy_requires_failure_reserve_and_limits_channel(self):
        for changes in ({'max_failure_percent':None},{'min_mp_after':None},{'channel_actions':4},
                        {'channel_actions':1,'expect_name':'Freeze'},{'max_seconds':float('nan')}):
            with self.assertRaises(ValueError): policy({**OPTIONS,**changes})
        with patch('sys.argv',['crawl-agent','cast','--spell-letter','b','--expect-name','Searing Ray',
                               '--monster-id','2','--max-failure-percent','5','--min-mp-after','0']), \
                patch('dcss_harness.cli.output_request',return_value=0) as request:
            self.assertEqual(main(),0)
        self.assertEqual(request.call_args.args[1]['op'],'cast')

    def test_native_repeat_freeze_and_reserve_rechecked(self):
        options = dict(spell_letter='c',expect_name='Freeze',channel_actions=0,max_casts=3)
        game=Replay('spell-repeat-live.json')
        info=self.cast(game,**options)
        self.assertEqual((info['stop_reason'],info['actions'],info['turns']),('cast_limit',3,3),info)
        self.assertEqual(len(info['attempts']),3)
        game=Replay('spell-repeat-live.json')
        info=self.cast(game,**options,min_mp_after=14)
        self.assertEqual(info['stop_reason'],'insufficient_mp_reserve',info)
        self.assertEqual(info['actions'],1)
        self.assertEqual(len(game.sent),8)

    def test_repeat_stops_on_miscast_damage_and_stale_second_menu(self):
        options=dict(spell_letter='c',expect_name='Freeze',channel_actions=0,max_casts=3)
        def miscast(s,n):
            if n==6: s.apply({'msg':'msgs','messages':[{'text':'You miscast Freeze.'}]})
        def damage(s,n):
            if n==6: s.apply({'msg':'player','hp':49})
        def remap(s,n):
            if n==8:
                for row in s.ui[-1]['items']:
                    if 'Freeze' in row['text']:
                        row['text']=row['text'].replace(' c ', ' f ')
                        row['hotkeys']=[ord('f')]
        for mutate,reason,inputs in ((miscast,'miscast',6),(damage,'damage',6),(remap,'spell_identity_mismatch',8)):
            game=Replay('spell-repeat-live.json',mutate=mutate)
            info=self.cast(game,**options)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(info['actions'],1)
            self.assertEqual(len(game.sent),inputs)
        game=Replay('spell-repeat-live.json',timeout=6)
        info=self.cast(game,**options)
        self.assertEqual(len(game.sent),6)
        self.assertEqual(info['stop_reason'],'submission_uncertain')

    def test_repeat_has_one_deadline_and_rejects_other_spells(self):
        with self.assertRaises(ValueError): policy({**OPTIONS,'max_casts':2})
        clock=[100.0]
        def expire(s,n):
            if n==6: clock[0]+=11
        game=Replay('spell-repeat-live.json',mutate=expire)
        with patch('dcss_harness.spells.time.monotonic',side_effect=lambda:clock[0]):
            info=self.cast(game,spell_letter='c',expect_name='Freeze',channel_actions=0,max_casts=3)
        self.assertEqual(info['stop_reason'],'time_limit')
        self.assertEqual(info['actions'],1)
        self.assertEqual(len(game.sent),6)

    def test_missing_turn_never_submits_single_or_repeated_cast(self):
        for count in (1,3):
            game=Replay('spell-repeat-live.json');game.state.player['turn']=None
            info=self.cast(game,spell_letter='c',expect_name='Freeze',channel_actions=0,max_casts=count)
            self.assertEqual(info['stop_reason'],'missing_player_state')
            self.assertEqual(info['turns'],0)
            self.assertFalse(game.sent)
