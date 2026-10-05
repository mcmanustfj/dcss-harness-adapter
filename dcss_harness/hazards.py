"""Explicit stationary terrain allowances; never infer immunity from inventory."""

import copy
import html
import re


def status_labels(player):
    return {html.unescape(re.sub(r'</?[a-zA-Z][^>]*>', '', str(
        s.get('light') or s.get('text') or '' if isinstance(s, dict) else s))).strip()
            for s in player.get('status', [])}


def validate(options):
    if type(options.get('allow_water_with_flight', False)) is not bool:
        raise ValueError('allow_water_with_flight must be boolean')
    clouds = options.get('allow_cloud', [])
    if not isinstance(clouds, list) or any(c != 'poison' for c in clouds):
        raise ValueError('allow_cloud accepts only explicitly assessed poison')


def standing_on(feature):
    if (feature.get('dx'), feature.get('dy')) == (0, 0) or [0, 0] in feature.get('cells', []):
        return True
    if 'through' in feature:
        end = feature['through']
        return (min(feature['dx'], end['dx']) <= 0 <= max(feature['dx'], end['dx'])
                and min(feature['dy'], end['dy']) <= 0 <= max(feature['dy'], end['dy']))
    return False


def local_hazard(obs, rules=None):
    rules = rules or {}
    for f in obs.get('visible_features', []):
        if f.get('kind') not in ('hazard', 'cloud') or not standing_on(f):
            continue
        if (f.get('cloud_type') == 'poison' and not f.get('possible_types')
                and 'poison' in rules.get('allow_cloud', []) and not f.get('terrain_id')):
            continue
        if (rules.get('allow_water_with_flight') and 'Fly' in status_labels(obs['player'])
                and f.get('terrain_id') in ('shallow_water', 'deep_water')
                and not f.get('cloud_type')):
            continue
        return True
    return False


class HazardWatch:
    """Latch changes to the assessment before any further automatic input."""
    def __init__(self, state, rules):
        self.rules = rules
        self.active = bool(rules.get('allow_water_with_flight') or rules.get('allow_cloud'))
        self.previous = copy.deepcopy(state.player)
        self.monsters = self.monster_signatures(state) if self.active else {}
        self.reason = None

    @staticmethod
    def monster_signatures(state):
        return {m.get('id'): {k: m.get(k) for k in ('name', 'type', 'att', 'icons', 'visible_weapons', 'location_status')}
                for m in state.observation()['monsters']}

    def __call__(self, state, event):
        if not self.active or self.reason:
            return
        old, p = self.previous, state.player
        if p.get('hp', 0) < old.get('hp', 0):
            self.reason = 'damage'
        elif p.get('mp', 0) < old.get('mp', 0):
            self.reason = 'resource_spent'
        elif any(p.get(k) != old.get(k) for k in (
                'status', 'form', 'species', 'weapon_index', 'offhand_index', 'inv',
                'equip', 'equipment', 'hp_max', 'mp_max',
                'str', 'int', 'dex', 'ac', 'ev', 'sh', 'ac_mod', 'ev_mod', 'sh_mod')):
            self.reason = 'hazard_assessment_changed'
        elif event.get('msg') == 'map' and local_hazard(state.observation(), self.rules):
            self.reason = 'local_hazard'
        elif event.get('msg') == 'map' and self.monster_signatures(state) != self.monsters:
            self.reason = 'monster_changed'
        self.previous = copy.deepcopy(p)
