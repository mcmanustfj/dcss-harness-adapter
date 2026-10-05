"""Bounded stationary support for named allies or active Frozen Ramparts."""

import copy
import re
import time

from .combat import policy as combat_policy, signature
from .hazards import local_hazard, status_labels
from .ranged import Stop
from .recovery import public_text
from .safety import recovery_friendly, risk_increase


ARMOUR = {'ice-armoured', 'ice-armoured (expiring)'}


def policy(options):
    rules = dict(monster_id=None, allow_friendly_id=[], allow_status=[], max_actions=6,
                 max_seconds=10, stop_distance=2, min_hp_percent=85, max_threat=1)
    if not isinstance(options,dict) or set(options)-rules.keys():
        raise ValueError('Unknown hold policy option')
    rules.update(options)
    combat_policy({k:rules[k] for k in ('max_actions','max_seconds','min_hp_percent','max_threat','allow_status')})
    if type(rules['monster_id']) is not int or rules['monster_id']<=0:
        raise ValueError('monster_id must be a positive sighting ID')
    ids=rules['allow_friendly_id']
    if not isinstance(ids,list) or any(type(i) is not int or i<=0 for i in ids) or len(set(ids))!=len(ids) or rules['monster_id'] in ids:
        raise ValueError('allow_friendly_id must list unique friendly sighting IDs distinct from the foe')
    if type(rules['stop_distance']) is not int or not 1<=rules['stop_distance']<=8:
        raise ValueError('stop_distance must be from 1 to 8')
    return rules


def labels(player,rules):
    result=status_labels(player)
    if ARMOUR <= set(rules['allow_status']) and result & ARMOUR:
        result=(result-ARMOUR)|{'ice-armoured'}
    return result


def expected_message(text, target, friends, rules, ramparts):
    """Every clause must match a tested event with unambiguous participants."""
    text=public_text(text)
    if text in ('HP restored.','Magic restored.'):
        return True
    if text=='Your icy armour starts to melt.' and ARMOUR<=set(rules['allow_status']):
        return True
    enemy=re.escape(target['name'])
    target_name=rf'(?:the )?{enemy}'
    ally='(?:'+'|'.join(re.escape(m['name']) for m in friends)+')'
    clauses=re.split(r'(?<=[.!])\s+',text)
    patterns=[rf'{target_name} (?:is (?:lightly|moderately|heavily|severely) (?:wounded|damaged)|is almost (?:dead|destroyed)|resists)[.!]']
    if friends:
        patterns += [rf'Your {ally} (?:hits|bites|claws|stings|misses|barely misses|closely misses|freezes|kills) {target_name}(?: but does no damage)?[.!]+']
    if ramparts:
        patterns += [rf'The wall freezes {target_name}(?: but does no damage)?[.!]+']
    return bool(text) and all(any(re.fullmatch(p,c,re.I) for p in patterns) for c in clauses)


def run(game,options):
    rules=policy(options)
    started,timestamp=time.monotonic(),time.time()
    deadline=started+rules['max_seconds']
    old_observer=game.state.guard_observer
    initial=None
    previous=copy.deepcopy(game.state.player)
    reason=None
    target=None
    friends=[]
    ramparts=False
    info={'operation':'hold','policy':rules,'phase':'preflight','submitted':False,'actions':0,
          'inputs':[],'steps':[],'auto_more':0,'resource_consumed':None,
          'expected_target':rules['monster_id'],'resolved_target':None,
          'starting_mp':previous.get('mp')}

    def check(obs):
        p=obs['player']
        if game.process.poll() is not None or p.get('hp',0)<=0:
            return 'game_exited'
        if obs.get('unseen_threat'): return 'unseen_threat'
        if local_hazard(obs): return 'local_hazard'
        if any(p.get(k) is None for k in ('hp','hp_max','mp','mp_max','pos','turn','status','xl','place','depth')):
            return 'missing_player_state'
        if p['hp']*100 < rules['min_hp_percent']*p['hp_max']: return 'low_hp'
        current_labels=status_labels(p)
        if any(re.fullmatch(r'(?:Ray\+*|Wave\+*|Winding\.*|Charge[-/|\\])',s) for s in current_labels):
            return 'active_channel_requires_cast'
        if current_labels-set(rules['allow_status']): return 'unexpected_status'
        if initial:
            if labels(p,rules)!=labels(initial['player'],rules): return 'status_changed'
            if any(p.get(k)!=initial['player'].get(k) for k in (
                    'pos','place','depth','xl','hp_max','mp_max','form','weapon_index','offhand_index',
                    'str','int','dex','ac','ev','sh')): return 'player_changed'
            if obs['inventory']!=initial['inventory']: return 'resource_changed'
        monsters={m.get('id'):m for m in obs['monsters']}
        expected={rules['monster_id'],*rules['allow_friendly_id']}
        if monsters.keys()-expected: return 'unexpected_creature'
        if rules['monster_id'] not in monsters: return 'target_lost_or_dead'
        enemy=monsters[rules['monster_id']]
        if enemy.get('att')!=0 or enemy.get('location_status')!='visible': return 'target_changed'
        if enemy.get('threat') is None or enemy['threat']>rules['max_threat']: return 'enemy_threat'
        if enemy.get('icons'): return 'enemy_status'
        if max(abs(enemy['dx']),abs(enemy['dy']))<=rules['stop_distance']: return 'foe_in_range'
        if target and signature(enemy)!=signature(target): return 'target_changed'
        for mid in rules['allow_friendly_id']:
            friend=monsters.get(mid)
            if not friend: return 'friendly_lost'
            if not recovery_friendly(friend): return 'friendly_changed'
            old=next((m for m in friends if m['id']==mid),None)
            if old and (signature(friend)!=signature(old) or friend.get('wounds')!=old.get('wounds')):
                return 'friendly_changed'
        return None

    def watch(state,event):
        nonlocal reason,previous
        if old_observer: old_observer(state,event)
        if initial is None or reason: return
        p=state.player
        reason=risk_increase(p,previous)
        if p.get('hp',0)<previous.get('hp',0): reason=reason or 'damage'
        if p.get('mp',0)<previous.get('mp',0): reason=reason or 'resource_spent'
        reason=reason or check(state.observation())
        if not reason and event.get('msg')=='msgs':
            for m in event.get('messages',[]):
                if not expected_message(m.get('text',''),target,friends,rules,ramparts):
                    reason='unrecognized_message'
                    info['unrecognized_message']=public_text(m.get('text',''))
                    break
        previous=copy.deepcopy(p)

    try:
        game.settle_input(deadline=deadline,context='hold')
        obs=game.state.observation()
        if not game.settled: raise Stop('unsettled')
        if obs['input_mode']!='command' or obs['ui'] or obs['more']: raise Stop('command_prompt_required')
        issue=check(obs)
        if issue: raise Stop(issue)
        current={m['id']:m for m in obs['monsters']}
        target=copy.deepcopy(current[rules['monster_id']])
        friends=[copy.deepcopy(current[i]) for i in rules['allow_friendly_id']]
        ramparts='Ramparts' in status_labels(obs['player'])
        if not friends and not ramparts: raise Stop('ally_or_active_ramparts_required')
        names=[m['name'].lower() for m in [target,*friends]]
        if len(set(names))!=len(names): raise Stop('ambiguous_participant_names')
        initial=copy.deepcopy(obs)
        previous=copy.deepcopy(game.state.player)
        game.state.guard_observer=watch
        info.update(phase='ready',expected_target=rules['monster_id'],resolved_target=target,
                    friend_ids=rules['allow_friendly_id'],starting_mp=obs['player']['mp'])
        while info['actions']<rules['max_actions']:
            if reason: raise Stop(reason)
            if time.monotonic()>=deadline: raise Stop('time_limit')
            obs=game.state.observation()
            issue=check(obs)
            if issue: raise Stop(issue)
            if not game.settled: raise Stop('submission_uncertain')
            if obs['input_mode']!='command' or obs['ui'] or obs['more']: raise Stop('input_required')
            turn=obs['player']['turn']
            info.update(submitted=True,phase='submitted')
            info['actions']+=1
            key={'msg':'key','keycode':ord('.')}
            info['inputs'].append(key)
            game.state.begin_input(key)
            game.settled=False
            game.send(key)
            info['auto_more']+=game.settle_input(deadline=deadline,require_event=True,context='hold') or 0
            step={'turn':game.state.player.get('turn'),'hp':game.state.player.get('hp'),
                  'mp':game.state.player.get('mp'),'settled':game.settled,'guard':reason}
            info['steps'].append(step)
            game.actions.write({'event':'hold_step',**step})
            if not game.settled: raise Stop('submission_uncertain')
            if reason: raise Stop(reason)
            if game.state.mode!=1 or game.state.ui or game.state.more: raise Stop('input_required')
            if game.state.player.get('turn')==turn: raise Stop('no_turn_progress')
            info['phase']='resolved'
        raise Stop('action_limit')
    except Stop as exc:
        info['stop_reason']=str(exc)
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        info.update(stop_reason='submission_uncertain' if info['submitted'] else 'action_error',error=str(exc))
    finally:
        game.state.guard_observer=old_observer
    before=(initial or {'player':previous})['player'].get('turn') or 0
    info.update(turns=max(0,(game.state.player.get('turn') or before)-before),settled=game.settled,
                ending_mp=game.state.player.get('mp'),elapsed_ms=round((time.monotonic()-started)*1000))
    if info['turns'] and game.settled and game.state.mode==1 and not game.state.ui:
        info['phase']='resolved'
    if info['stop_reason']=='resource_spent': info['resource_consumed']=True
    sent=info['actions']+info['auto_more']
    game.actions.record('hold',sent,sent,before,game.state.player,started,timestamp,
                        game.settled,game.process.poll() is None,info.get('error'))
    result=game.observe()
    result.update(hold=info,keys_sent=sent)
    return result
