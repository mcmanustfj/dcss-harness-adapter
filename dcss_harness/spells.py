"""One chosen spell and optional bounded ray continuation; public UI only."""

import copy
import math
import re
import time

from .combat import signature
from .hazards import local_hazard, status_labels
from .ranged import Stop, monster, check_aim
from .recovery import public_text
from .safety import risk_increase


CONTRACTS = {'Freeze': 'target', 'Searing Ray': 'target', "Ozocubu's Armour": 'instant'}
RAY = {'Ray', 'Ray+', 'Ray++', 'Ray+++'}
ARMOUR = {'ice-armoured', 'ice-armoured (expiring)'}


def spell_menu(ui):
    if len(ui) != 1 or ui[0].get('tag') != 'spell':
        return None
    menu = ui[0]
    title = public_text(menu.get('title', {}).get('text', ''))
    if 'Failure' not in title or 'Level' not in title:
        return None
    rows = []
    for row in menu.get('items', []):
        if row.get('level') != 2:
            continue
        # Native _spell_base_description uses a 32-character name column.
        m = re.fullmatch(r'\s*([a-zA-Z])\s+[-+]\s(.{32})(.*?)\s+(\d+)%\s+([1-9])\s*', public_text(row.get('text', '')))
        if not m or ord(m[1]) not in row.get('hotkeys', []):
            return None
        level = int(m[5])
        rows.append({'letter': m[1], 'name': m[2].strip(), 'schools': m[3].strip(),
                     'failure_percent': int(m[4]), 'level': level, 'mp_cost': None,
                     'mp_cost_upper_bound': level, 'cost_basis': 'native_spell_level_upper_bound'})
    return {'mode': 'cast' if 'Your spells (cast)' in title else 'describe', 'entries': rows}


def policy(options):
    rules = dict(spell_letter=None, expect_name=None, monster_id=None, max_failure_percent=None,
                 min_mp_after=None, prepare_only=False, channel_actions=0, max_casts=1, max_seconds=10, allow_status=[])
    if not isinstance(options, dict) or set(options) - rules.keys():
        raise ValueError('Unknown cast policy option')
    rules.update(options)
    if not isinstance(rules['spell_letter'], str) or not re.fullmatch('[a-zA-Z]', rules['spell_letter']):
        raise ValueError('spell_letter must be one spell letter')
    if not isinstance(rules['expect_name'], str) or not rules['expect_name']:
        raise ValueError('expect_name requires the exact spell name')
    for k, high in (('max_failure_percent', 100), ('min_mp_after', 1000), ('channel_actions', 3)):
        if type(rules[k]) is not int or not 0 <= rules[k] <= high:
            raise ValueError(f'{k} must be an integer from 0 to {high}')
    if type(rules['prepare_only']) is not bool:
        raise ValueError('prepare_only must be boolean')
    if rules['monster_id'] is not None and (type(rules['monster_id']) is not int or rules['monster_id'] <= 0):
        raise ValueError('monster_id must be a positive sighting ID')
    if rules['channel_actions'] and (rules['expect_name'] != 'Searing Ray' or rules['prepare_only']):
        raise ValueError('channel_actions requires a submitted Searing Ray')
    if type(rules['max_casts']) is not int or not 1 <= rules['max_casts'] <= 32:
        raise ValueError('max_casts must be an integer from 1 to 32')
    if rules['max_casts'] != 1 and (rules['expect_name'] != 'Freeze' or rules['prepare_only']):
        raise ValueError('Repeated casting supports only submitted Freeze')
    if not isinstance(rules['allow_status'], list) or any(not isinstance(s, str) or not s for s in rules['allow_status']):
        raise ValueError('allow_status must list exact status labels')
    seconds = rules['max_seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not .1 <= seconds <= 30:
        raise ValueError('max_seconds must be from 0.1 to 30')
    return rules


def run_once(game, options, shared_guard=None):
    rules = policy(options)
    started, timestamp = time.monotonic(), time.time()
    deadline = started + rules['max_seconds']
    before = game.state.player.get('turn')
    old_observer = game.state.guard_observer
    baseline = None
    latched = None
    expected_status = set()
    pending_debit = False
    preparation_mp = None
    previous_mp = None
    allowed_mp_loss = 0
    observed_player = None
    info = {'operation': 'cast', 'policy': rules, 'phase': 'preflight', 'submitted': False,
            'actions': 0, 'inputs': [], 'steps': [], 'auto_more': 0, 'resource_consumed': None,
            'expected_spell': rules['expect_name'], 'expected_target': rules['monster_id'],
            'resolved_spell': None, 'resolved_target': None, 'channel': {'pulses': 0, 'evidence': []}}

    def snapshot():
        return game.state.observation()

    def watch(state, event):
        nonlocal latched, previous_mp, allowed_mp_loss, observed_player
        if old_observer:
            old_observer(state, event)
        if baseline is None or latched:
            return
        p, old = state.player, baseline['player']
        previous = observed_player or old
        latched = risk_increase(p, previous)
        if p.get('hp', 0) < previous['hp']:
            latched = latched or 'damage'
        observed_player = copy.deepcopy(p)
        if info['submitted'] and previous_mp is not None and p.get('mp', previous_mp) < previous_mp:
            allowed_mp_loss -= previous_mp - p['mp']
            if allowed_mp_loss < 0:
                latched = latched or 'unexpected_mp_loss'
        previous_mp = p.get('mp')
        if any(p.get(k) != old.get(k) for k in ('pos', 'place', 'depth', 'xl', 'hp_max', 'mp_max', 'form',
                                              'weapon_index', 'offhand_index', 'str', 'int', 'dex', 'ev', 'sh',
                                              'god', 'piety_rank', 'penance')):
            latched = latched or 'player_changed'
        if (rules['expect_name'] != "Ozocubu's Armour" or not info['submitted']) and any(
                p.get(k) != old.get(k) for k in ('ac', 'ac_mod')):
            latched = latched or 'player_changed'
        current_status = status_labels(p) - expected_status
        if current_status != status_labels(old):
            latched = latched or 'status_changed'
        obs = snapshot()
        if obs['inventory'] != baseline['inventory']:
            latched = latched or 'resource_changed'
        current = {m.get('id'): signature(m) for m in obs['monsters']}
        original = {m.get('id'): signature(m) for m in baseline['monsters']}
        if current != original:
            latched = latched or 'monster_changed'
        if local_hazard(obs):
            latched = latched or 'local_hazard'
        if not info['submitted'] and any(p.get(k) != old.get(k) for k in ('turn', 'time', 'hp', 'ac')):
            latched = latched or 'player_changed_during_preparation'
        if not info['submitted'] and p.get('mp') != preparation_mp:
            ceiling = (info['resolved_spell'] or {}).get('mp_cost_upper_bound', 0)
            if not (pending_debit and 0 <= old['mp'] - p.get('mp', -10000) <= ceiling):
                latched = latched or 'unexpected_mp_change'
        if info['resolved_spell'] and p.get('mp', -1) < rules['min_mp_after']:
            latched = latched or 'mp_reserve_crossed'

    def ready(command=False):
        if shared_guard and shared_guard():
            raise Stop(shared_guard())
        if game.process.poll() is not None or game.state.player.get('hp', 0) <= 0:
            raise Stop('game_exited')
        if not game.settled:
            raise Stop('submission_uncertain' if info['submitted'] else 'unsettled')
        if latched:
            raise Stop(latched)
        if time.monotonic() >= deadline:
            raise Stop('time_limit')
        obs = snapshot()
        p = obs['player']
        if any(p.get(k) is None for k in ('hp','hp_max','mp','mp_max','pos','turn','status','place','depth','xl')):
            raise Stop('missing_player_state')
        if p['hp'] * 100 < p['hp_max'] * 85:
            raise Stop('low_hp')
        if obs.get('unseen_threat') or obs.get('more'):
            raise Stop('input_or_unseen_threat')
        if local_hazard(obs):
            raise Stop('local_hazard')
        if command and (obs['input_mode'] != 'command' or obs['ui']):
            raise Stop('command_prompt_required')
        return obs

    def send(message, spends=False):
        nonlocal allowed_mp_loss, previous_mp
        ready()
        if spends:
            allowed_mp_loss = (1 if message.get('keycode') == ord('.') else
                               info['resolved_spell']['mp_cost_upper_bound'] if CONTRACTS[rules['expect_name']] == 'instant' else 0)
            previous_mp = game.state.player.get('mp')
            info['submitted'] = True
            info['actions'] += 1
            info['phase'] = 'submitted'
        info['inputs'].append(message)
        game.state.begin_input(message)
        game.settled = False
        game.send(message)
        acknowledgments = game.settle_input(deadline=deadline, require_event=True, context='cast')
        info['auto_more'] += acknowledgments or 0
        step = {'input': message, 'may_spend_turn': spends, 'turn': game.state.player.get('turn'),
                'mp': game.state.player.get('mp'), 'settled': game.settled, 'guard': latched}
        info['steps'].append(step)
        game.actions.write({'event': 'cast_step', **step})

    def key(char, spends=False):
        send({'msg':'key', 'keycode':ord(char)}, spends)

    def target():
        return monster(ready(), rules['monster_id'])

    def budget(cost):
        if game.state.player['mp'] - cost < rules['min_mp_after']:
            raise Stop('insufficient_mp_reserve')

    try:
        game.settle_input(deadline=deadline, context='cast')
        obs = ready(command=True)
        info['starting_mp'] = obs['player']['mp']
        contract = CONTRACTS.get(rules['expect_name'])
        if contract is None:
            raise Stop('unsupported_spell')
        if status_labels(obs['player']) & (RAY | (ARMOUR if contract == 'instant' else set())):
            raise Stop('already_active')
        if status_labels(obs['player']) - set(rules['allow_status']):
            raise Stop('unexpected_status')
        if contract == 'instant':
            if rules['monster_id'] is not None:
                raise Stop('instant_spell_has_no_target')
            if rules['prepare_only']:
                raise Stop('cannot_prepare_instant')
        elif rules['monster_id'] is None:
            raise Stop('target_required')
        else:
            target()
        baseline = copy.deepcopy(obs)
        preparation_mp = obs['player']['mp']
        game.state.guard_observer = watch
        info['phase'] = 'opening_menu'
        key('z')
        obs = ready()
        if not spell_menu(obs['ui']):
            if obs['ui'] or obs['input_mode'] != 'prompt':
                raise Stop('spell_menu_mismatch')
            key('?')
            obs = ready()
        menu = spell_menu(obs['ui'])
        if not menu or menu['mode'] != 'cast':
            raise Stop('spell_menu_mismatch')
        entries = [r for r in menu['entries'] if r['letter'] == rules['spell_letter']]
        if len(entries) != 1 or entries[0]['name'] != rules['expect_name']:
            raise Stop('spell_identity_mismatch')
        spell = entries[0]
        info['resolved_spell'] = spell
        if spell['failure_percent'] > rules['max_failure_percent']:
            raise Stop('failure_ceiling')
        budget(spell['mp_cost_upper_bound'])
        if contract == 'instant':
            expected_status.update(ARMOUR)
            key(rules['spell_letter'], spends=True)
        else:
            chosen = target()
            if rules['expect_name'] == 'Freeze' and max(abs(chosen['dx']),abs(chosen['dy'])) != 1:
                raise Stop('out_of_range_or_invalid')
            pending_debit = True
            key(rules['spell_letter'])
            pending_debit = False
            preparation_mp = game.state.player.get('mp')
            obs = ready()
            aim = obs.get('targeting')
            if not aim:
                raise Stop('target_not_unambiguous')
            # Native targeted spells reserve MP before opening targeting and
            # refund it on cancellation. This free public debit gives the exact
            # current cost; it is not evidence that a spell was cast.
            spell['mp_cost'] = baseline['player']['mp'] - preparation_mp
            spell['cost_basis'] = 'public_targeting_mp_reservation'
            info['mp_reserved'] = spell['mp_cost']
            chosen = target()
            point = chosen['x'], chosen['y']
            pos = obs['player']['pos']
            if [chosen['dx'], chosen['dy']] in aim['invalid_aim_cells']:
                raise Stop('out_of_range_or_invalid')
            cursor = aim.get('cursor') or {}
            if (cursor.get('x'), cursor.get('y')) == point:
                send({'msg':'target_cursor','x':pos['x'],'y':pos['y']})
                cursor = (ready().get('targeting') or {}).get('cursor') or {}
                if (cursor.get('x'), cursor.get('y')) != (pos['x'],pos['y']):
                    raise Stop('recenter_rejected')
            send({'msg':'target_cursor','x':point[0],'y':point[1]})
            obs = ready()
            chosen = target()
            if (chosen['x'],chosen['y']) != point:
                raise Stop('target_moved')
            exposure = check_aim(obs, chosen)
            info['resolved_target'] = {k:chosen[k] for k in ('id','name','x','y')}
            info['exposure'] = exposure
            for key_, reason in (('intervening_occupants','possible_interception'),('self','self_exposure'),
                                 ('friendly_or_neutral','friendly_exposure'),('other_hostiles','area_exposure'),('unseen_cells','unseen_preview')):
                if exposure[key_]: raise Stop(reason)
            info['phase'] = 'prepared'
            if rules['prepare_only']:
                raise Stop('prepared')
            budget(0)  # Native targeting has already reserved this cast's MP.
            expected_status.update(RAY if rules['expect_name'] == 'Searing Ray' else ())
            path = {(p['x'],p['y']) for p in obs['targeting']['preview_cells'] + obs['targeting']['landing_cells']}
            def path_signature():
                return {p: (game.state.cells.get(p,{}).get('f'),
                            game.state.visible(game.state.cells.get(p,{})),
                            game.state.cells.get(p,{}).get('t',{}).get('cloud')) for p in path}
            original_path = path_signature()
            key('\r', spends=True)
        obs = ready(command=True)
        if obs['player']['turn'] <= before:
            raise Stop('submission_unconfirmed')
        info['phase'] = 'resolved'
        if not rules['channel_actions']:
            raise Stop('cast_resolved')
        for pulse in range(rules['channel_actions']):
            obs = ready(command=True)
            lights = status_labels(obs['player']) & RAY
            info['channel']['evidence'].append(sorted(lights))
            if lights != {'Ray' + '+' * pulse}:
                raise Stop('channel_not_active' if pulse == 0 else 'channel_complete_or_changed')
            chosen = target()
            if (chosen['x'], chosen['y']) != point:
                raise Stop('target_moved')
            if path_signature() != original_path:
                raise Stop('channel_path_changed')
            # Any occupant other than the chosen target entering a proven beam
            # requires reassessment. Reopening targeting would cancel the ray.
            if any((m['x'],m['y']) in path and m.get('id') != chosen['id']
                   for m in obs['monsters'] + obs.get('scenery', [])):
                raise Stop('possible_interception')
            budget(1)
            previous_turn = obs['player']['turn']
            info['phase'] = 'channel'
            key('.', spends=True)
            info['channel']['pulses'] += 1
            obs = ready(command=True)
            if obs['player']['turn'] <= previous_turn:
                raise Stop('submission_unconfirmed')
        info['phase'] = 'resolved'
        raise Stop('channel_complete' if not status_labels(game.state.player) & RAY else 'channel_limit')
    except Stop as exc:
        info['stop_reason'] = str(exc)
    except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
        info.update(stop_reason='submission_uncertain' if info['submitted'] else 'action_error', error=str(exc))
    finally:
        game.state.guard_observer = old_observer
    end_mp = game.state.player.get('mp')
    if info['submitted'] and isinstance(end_mp, int) and end_mp < info.get('starting_mp', end_mp):
        info['resource_consumed'] = True
    info.update(ending_mp=end_mp, settled=game.settled,
                turns=max(0, (game.state.player.get('turn') or before or 0) - (before or 0)),
                elapsed_ms=round((time.monotonic()-started)*1000))
    if info['submitted'] and info['turns'] and game.settled and game.state.mode == 1 and not game.state.ui:
        info['phase'] = 'resolved'
    info['channel']['active_labels'] = sorted(status_labels(game.state.player) & RAY)
    info['targeting_open'] = snapshot().get('targeting') is not None
    if any('miscast' in public_text(m.get('text', '')).lower() for m in game.state.input_messages):
        info['outcome'] = 'miscast'
    sent = sum(m['msg']=='key' for m in info['inputs']) + info['auto_more']
    game.actions.record('cast', sent, sent, before, game.state.player, started, timestamp,
                        game.settled, game.process.poll() is None, info.get('error'))
    result = game.observe()
    result.update(cast=info, keys_sent=sent)
    return result


def run(game, options):
    rules = policy(options)
    if rules['max_casts'] == 1:
        return run_once(game, options)
    started = time.monotonic()
    deadline = started + rules['max_seconds']
    initial = copy.deepcopy(game.state.observation())
    previous = copy.deepcopy(game.state.player)
    observer = game.state.guard_observer
    reason = None
    attempts = []

    def watch(state, event):
        nonlocal previous, reason
        if observer:
            observer(state, event)
        p = state.player
        reason = reason or risk_increase(p, previous)
        if p.get('hp', 0) < previous.get('hp', 0):
            reason = reason or 'damage'
        if any(p.get(k) != initial['player'].get(k) for k in (
                'status', 'pos', 'place', 'depth', 'xl', 'form', 'hp_max', 'mp_max',
                'weapon_index', 'offhand_index', 'str', 'int', 'dex', 'ac', 'ev', 'sh', 'god', 'piety_rank', 'penance')):
            reason = reason or 'player_changed'
        obs = state.observation()
        if obs['inventory'] != initial['inventory']:
            reason = reason or 'resource_changed'
        if {m.get('id'):signature(m) for m in obs['monsters']} != {
                m.get('id'):signature(m) for m in initial['monsters']}:
            reason = reason or 'monster_changed'
        previous = copy.deepcopy(p)

    game.state.guard_observer = watch
    try:
        for _ in range(rules['max_casts']):
            remaining = deadline - time.monotonic()
            if remaining < .1 or reason:
                reason = reason or 'time_limit'
                break
            result = run_once(game, {**rules, 'max_casts':1, 'max_seconds':remaining}, lambda: reason)
            attempt = result['cast']
            attempts.append(attempt)
            if attempt.get('outcome') == 'miscast':
                reason = 'miscast'
                break
            if attempt['stop_reason'] != 'cast_resolved':
                reason = attempt['stop_reason']
                break
    finally:
        game.state.guard_observer = observer
    result = game.observe()
    info = copy.deepcopy(attempts[-1]) if attempts else {'phase':'preflight','submitted':False}
    info.update(operation='cast', policy=rules, stop_reason=reason or 'cast_limit', attempts=attempts,
                submitted=any(a['submitted'] for a in attempts),
                actions=sum(a['actions'] for a in attempts),
                inputs=[i for a in attempts for i in a['inputs']],
                steps=[s for a in attempts for s in a['steps']],
                auto_more=sum(a['auto_more'] for a in attempts),
                starting_mp=initial['player'].get('mp'), ending_mp=game.state.player.get('mp'),
                turns=max(0, (game.state.player.get('turn') or 0)-(initial['player'].get('turn') or 0)),
                settled=game.settled, elapsed_ms=round((time.monotonic()-started)*1000),
                resource_consumed=True if any(a['resource_consumed'] is True for a in attempts) else None)
    sent = sum(i['msg']=='key' for i in info['inputs']) + info['auto_more']
    game.actions.write({'event':'cast_repeat','actions':info['actions'],'stop_reason':info['stop_reason']})
    result.update(cast=info,keys_sent=sent)
    return result
