import copy
import unittest
from unittest.mock import patch
from test_spells import Replay
from dcss_harness.hold import run, policy, expected_message
from dcss_harness.cli import main

OPTIONS={'monster_id':3,'allow_friendly_id':[1],'stop_distance':1}


class HoldTests(unittest.TestCase):
    def game(self,**kw): return Replay('hold-live.json',**kw)
    def hold(self,g,**kw): return run(g,{**OPTIONS,**kw})['hold']

    def test_native_six_waits_allow_named_pet_attacks_and_regeneration(self):
        g=self.game(); info=self.hold(g)
        self.assertEqual((info['actions'],info['turns'],info['stop_reason']),(6,6,'action_limit'),info)
        self.assertGreater(info['ending_mp'],info['starting_mp'])
        self.assertTrue(all(m=={'msg':'key','keycode':46} for m in g.sent))
        self.assertIsNone(g.state.guard_observer)

    def test_unknown_messages_and_friendly_damage_death_or_attitude_stop(self):
        def msg(s,n):
            if n==1: s.apply({'msg':'msgs','messages':[{'text':'The statue hits your ice beast.'}]})
        def wound(s,n):
            if n==1:
                from dcss_harness.items import tile_flags
                fg=tile_flags(s.cells[(-2,0)]['t'].get('fg')) | (2 << 30)
                s.apply({'msg':'map','cells':[{'x':-2,'y':0,'t':{'fg':fg}}]})
        def lost(s,n):
            if n==1: s.apply({'msg':'map','cells':[{'x':-2,'y':0,'mon':None}]})
        def hostile(s,n):
            if n==1:
                original=copy.deepcopy(s.cells[(-2,0)]['mon'])
                s.apply({'msg':'map','cells':[{'x':-2,'y':0,'mon':{**original,'att':0}}]})
                s.apply({'msg':'map','cells':[{'x':-2,'y':0,'mon':original}]})
        for mutate,reason in ((msg,'unrecognized_message'),(wound,'friendly_changed'),(lost,'friendly_lost'),(hostile,'friendly_changed')):
            g=self.game(mutate=mutate);info=self.hold(g)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(len(g.sent),1)

    def test_transient_damage_doom_and_distance_stop(self):
        def damage(s,n):
            if n==1:
                s.apply({'msg':'player','hp':49});s.apply({'msg':'player','hp':50})
        def doom(s,n):
            if n==1:
                s.apply({'msg':'player','doom':1});s.apply({'msg':'player','doom':0})
        def approach(s,n):
            if n==1:
                m=copy.deepcopy(s.cells[(-2,1)]['mon'])
                s.apply({'msg':'map','cells':[{'x':-2,'y':1,'mon':None},{'x':-1,'y':1,'t':{'bg':0},'mon':m}]})
        for mutate,reason in ((damage,'damage'),(doom,'doom_increased'),(approach,'foe_in_range')):
            g=self.game(mutate=mutate);info=self.hold(g)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(info['actions'],1)

    def test_target_loss_new_creature_and_expiry_stop(self):
        def target_lost(s,n):
            if n==1: s.apply({'msg':'map','cells':[{'x':-2,'y':1,'mon':None}]})
        def creature(s,n):
            if n==1: s.apply({'msg':'map','cells':[{'x':1,'y':0,'t':{'bg':0},'mon':{'id':44,'name':'rat','att':0,'type':1,'threat':0}}]})
        for mutate,reason in ((target_lost,'target_lost_or_dead'),(creature,'unexpected_creature')):
            g=self.game(mutate=mutate);info=self.hold(g)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(info['actions'],1)
        g=self.game(mutate=lambda s,n:s.apply({'msg':'player','status':[]}) if n==1 else None)
        g.state.player['status']=[{'light':'Ramparts'}]
        info=self.hold(g,allow_status=['Ramparts'])
        self.assertEqual(info['stop_reason'],'status_changed')
        self.assertEqual(info['actions'],1)

    def test_active_ramparts_without_friend_and_exact_wall_messages(self):
        g=self.game()
        g.state.cells[(-2,0)]['mon']=None
        g.state.player['status']=[{'light':'Ramparts'}]
        for frame in g.frames:
            for event in frame['events']:
                if event['msg']=='msgs':
                    event['messages']=[{'text':'The wall freezes the statue but does no damage.'}]
        info=self.hold(g,allow_friendly_id=[],allow_status=['Ramparts'],max_actions=3)
        self.assertEqual((info['stop_reason'],info['actions']),('action_limit',3),info)
        g=self.game()
        g.state.cells[(-2,0)]['mon']=None
        info=self.hold(g,allow_friendly_id=[])
        self.assertEqual(info['stop_reason'],'ally_or_active_ramparts_required')
        self.assertFalse(g.sent)

    def test_mp_loss_and_inventory_change_are_latched(self):
        def mp(s,n):
            if n==1:
                value=s.player['mp'];s.apply({'msg':'player','mp':value-1});s.apply({'msg':'player','mp':value})
        def inventory(s,n):
            if n==1: s.apply({'msg':'player','inv':{'0':{'name':'+9 changed robe'}}})
        for mutate,reason in ((mp,'resource_spent'),(inventory,'resource_changed')):
            g=self.game(mutate=mutate);info=self.hold(g)
            self.assertEqual(info['stop_reason'],reason,info)
            self.assertEqual(info['actions'],1)

    def test_exact_armour_transition_requires_both_labels(self):
        def expire(s,n):
            if n==1: s.apply({'msg':'player','status':[{'text':'ice-armoured (expiring)'}]})
        for allowed,count in ((['ice-armoured'],1),(['ice-armoured','ice-armoured (expiring)'],2)):
            g=self.game(mutate=expire);g.state.player['status']=[{'text':'ice-armoured'}]
            info=self.hold(g,allow_status=allowed,max_actions=2)
            self.assertEqual(info['actions'],count,info)

    def test_preflight_distance_unknown_allies_and_channel_are_rejected(self):
        for kw,reason in (({'stop_distance':2},'foe_in_range'),({'allow_friendly_id':[]},'unexpected_creature')):
            g=self.game();info=self.hold(g,**kw)
            self.assertEqual(info['stop_reason'],reason)
            self.assertFalse(g.sent)
        g=self.game();g.state.player['status']=[{'light':'Ray'}]
        info=self.hold(g,allow_status=['Ray'])
        self.assertEqual(info['stop_reason'],'active_channel_requires_cast')
        self.assertFalse(g.sent)

    def test_message_allowlist_is_exact_and_cannot_hide_extra_clauses(self):
        target={'name':'orc warrior'};friends=[{'name':'ice beast'}]
        rules={'allow_status':[]}
        for text in ('Your ice beast hits the orc warrior. Your ice beast freezes the orc warrior.',
                     'The orc warrior is heavily wounded.','The wall freezes the orc warrior!'):
            self.assertTrue(expected_message(text,target,friends,rules,True),text)
        for text in ('Your ice beast hits the orc warrior. Something hits you!',
                     'Your ice beast hits the orc priest.','The orc warrior hits your ice beast.',
                     'Your ice beast dies!','Your icy armour starts to melt.','A new ice beast appears.'):
            self.assertFalse(expected_message(text,target,friends,rules,True),text)

    def test_timeout_never_repeats_uncertain_wait_and_cli(self):
        g=self.game(timeout=1);info=self.hold(g)
        self.assertEqual(info['stop_reason'],'submission_uncertain')
        self.assertEqual(len(g.sent),1)
        with self.assertRaises(ValueError): policy({**OPTIONS,'stop_distance':0})
        with patch('sys.argv',['crawl-agent','hold','--monster-id','3','--allow-friendly-id','1']), \
                patch('dcss_harness.cli.output_request',return_value=0) as request:
            self.assertEqual(main(),0)
        self.assertEqual(request.call_args.args[1]['op'],'hold')

    def test_missing_turn_returns_preflight_result_without_input(self):
        g=self.game();g.state.player['turn']=None
        info=self.hold(g)
        self.assertEqual(info['stop_reason'],'missing_player_state')
        self.assertEqual(info['turns'],0)
        self.assertFalse(g.sent)
