"""Closed reference policy catalog. No policy is a model of human play.

Seeds/state are provenance, never standard act inputs. See docs/lab/policies.md.
"""
from copy import deepcopy
import hashlib
from types import MappingProxyType

from .channels import PRIMARY_PROFILE, project_inputs
from .randomness import SeedSpec, capture_stream
from .records import decode_json, encode_json


POLICIES = ('random', 'scripted', 'possession', 'cautious', 'risk_taking')
_CONTROL_FIELDS = ('primary.players[].id', 'primary.players[].team',
                   'primary.balls[].carrier.value', 'primary.decision.active_player.value')


def policy_inputs(primary):
    """Copy authorized primary features and the identity control needed to act."""
    projected = project_inputs({'primary': primary}, PRIMARY_PROFILE)
    identities = projected['metadata']['channels']['primary']['fields']
    return projected['features'], {key: identities[key] for key in _CONTROL_FIELDS}


def _config(name, config):
    if type(config) is not dict:
        raise ValueError('Policy config must be a JSON object')
    if name in ('random', 'scripted'):
        if config:
            raise ValueError('Version 1 baseline policies have no parameters')
        return {}
    if set(config) - {'error_rate'}:
        raise ValueError('Unknown policy parameter')
    rate = config.get('error_rate', 0.0)
    if type(rate) not in (int, float) or not 0 <= rate <= 1:
        raise ValueError('error_rate must be finite and in [0, 1]')
    return {'error_rate': float(rate)}


class PolicySpecV1:
    """Defensively copied, data-only factory identity and private seed recipe."""

    def __init__(self, name, seed, config=None, version='1'):
        if type(name) is not str or name not in POLICIES or version != '1' or type(version) is not str:
            raise ValueError('Unknown policy name/version')
        if type(seed) is not SeedSpec or seed.purpose not in ('policy-home', 'policy-away'):
            raise ValueError('Expected a private policy SeedSpec')
        normalized = _config(name, {} if config is None else config)
        self._encoded = encode_json({
            'schema_version': 1, 'name': name, 'version': version,
            'config': normalized,
            'config_digest': 'sha256:' + hashlib.sha256(encode_json(normalized)).hexdigest(),
            'action_schema': 'ActionV1-1',
            'input_profile': {'features': PRIMARY_PROFILE.to_json(),
                              'control_fields': list(_CONTROL_FIELDS),
                              'legal_actions': 'LegalActionsV1-1', 'privileged': False},
            'seed': seed.to_json(), 'restoration': 'PolicyStateV1-1',
        })

    def to_json(self):
        return decode_json(self._encoded)

    @classmethod
    def from_json(cls, data):
        try:
            seed_data = data['seed']
            if type(seed_data) is not dict or seed_data.get('master_seed') is None:
                raise ValueError('A materialized policy seed is required')
            result = cls(data['name'], SeedSpec(**seed_data), data['config'], data['version'])
            if encode_json(data) != result._encoded:
                raise ValueError('Policy spec does not match the closed catalog')
            return result
        except (KeyError, TypeError) as error:
            raise ValueError('Malformed policy spec') from error


class ReferencePolicy:
    """One episode/seat owns one instance; act accepts copied public data only."""

    def __init__(self, policy_id, seed, config=None):
        self.spec = PolicySpecV1(policy_id, seed, config)
        self.policy_id = policy_id
        self._config = self.spec.to_json()['config']
        self.rng = seed.generator()
        self.closed = False
        self._last_type = None

    def act(self, features, legal, control=None):
        if type(features) is not dict:
            raise ValueError('Expected copied policy features')
        if self.closed or not legal.actions or features.get('primary.match.game_over', False):
            raise ValueError('Policy has no live legal decision')
        # Fail closed on accidentally forwarding a target/provenance channel.
        if type(features) is not dict or set(features) - dict(PRIMARY_PROFILE.fields).keys():
            raise ValueError('Unauthorized policy features')
        control = {} if control is None else control
        if type(control) is not dict or set(control) - set(_CONTROL_FIELDS):
            raise ValueError('Unauthorized policy control')
        encode_json(features)
        encode_json(control)
        if self.policy_id == 'random':
            return legal.actions[int(self.rng.randint(len(legal.actions)))]
        if self.policy_id == 'scripted':
            return self._scripted(legal.actions)
        if self._config['error_rate'] and self.rng.random_sample() < self._config['error_rate']:
            action = legal.actions[int(self.rng.randint(len(legal.actions)))]
        else:
            # First offered breaks equal scores; ordering is part of ActionV1's
            # controller contract. No tactical helper or engine query is used.
            action = max(legal.actions, key=lambda a: self._score(a, features, control))
        self._last_type = action.type
        return action

    def _scripted(self, actions):
        # Preserve the DATA-01 lifecycle policy, including its first-offered ties.
        if self._last_type not in ('SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE'):
            for action in actions:
                if action.type in ('SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE'):
                    self._last_type = action.type
                    return action
        for name in ('START_GAME', 'HEADS', 'RECEIVE', 'END_SETUP',
                     'SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE', 'END_TURN'):
            for action in actions:
                if action.type == name:
                    self._last_type = action.type
                    return action
        self._last_type = actions[0].type
        return actions[0]

    def _score(self, action, features, control):
        name = action.type
        formation = name in ('SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE')
        if formation:
            return (1000 if self._last_type not in
                    ('SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE') else -10)
        priorities = {'START_GAME': 900, 'HEADS': 900, 'RECEIVE': 900, 'END_SETUP': 800,
                      'SELECT_DEFENDER_DOWN': 90, 'SELECT_DEFENDER_STUMBLES': 80,
                      'SELECT_PUSH': 70, 'SELECT_BOTH_DOWN': -10,
                      'SELECT_ATTACKER_DOWN': -20, 'STAND_UP': 60,
                      'END_TURN': -100, 'END_PLAYER_TURN': -50, 'UNDO': -200}
        if name in priorities:
            return priorities[name]
        risk = {'cautious': -1, 'possession': 0, 'risk_taking': 1}[self.policy_id]
        if name == 'USE_REROLL':
            return 60 - 20 * risk
        if name == 'DONT_USE_REROLL':
            return 30 + 20 * risk
        if name in ('START_BLOCK', 'START_BLITZ', 'BLOCK', 'START_PASS', 'PASS', 'LEAP', 'FOUL'):
            return 10 + 35 * risk
        ids = control.get('primary.players[].id', [])
        xs = features.get('primary.players[].position.value.x', [])
        ys = features.get('primary.players[].position.value.y', [])
        positions = {pid: (x, y) for pid, x, y in zip(ids, xs, ys) if x is not None and y is not None}
        carrier = next((pid for pid in control.get('primary.balls[].carrier.value', []) if pid), None)
        active = control.get('primary.decision.active_player.value')
        ball_positions = list(zip(features.get('primary.balls[].position.value.x', []),
                                  features.get('primary.balls[].position.value.y', [])))
        ball = next(((x, y) for x, y in ball_positions if x is not None and y is not None), None)
        if name == 'START_MOVE':
            if action.player_id == carrier:
                return 55
            pos = positions.get(action.player_id)
            return 40 - min(20, abs(pos[0] - ball[0]) + abs(pos[1] - ball[1])) if pos and ball else 20
        if name == 'MOVE' and action.position is not None:
            x, y = action.position.x, action.position.y
            if active is not None and active == carrier:
                pos = positions.get(active)
                # Home attacks x=1, away attacks width-2 in the shipped arenas.
                progress = (pos[0] - x if action.actor_id == 'home' else x - pos[0]) if pos else 0
                return 50 + progress
            if ball:
                return 45 - min(30, abs(x - ball[0]) + abs(y - ball[1]))
            return 20
        return 0

    def capture_state(self):
        name, keys, position, has_gauss, gaussian = capture_stream(self.rng)
        return {'schema_version': 1, 'spec': self.spec.to_json(), 'closed': self.closed,
                'last_type': self._last_type,
                'rng': [name, list(keys), position, has_gauss, gaussian]}

    def restore_state(self, state):
        # Validate into a candidate before changing this instance. No pickle.
        if (type(state) is not dict or set(state) !=
                {'schema_version', 'spec', 'closed', 'last_type', 'rng'} or
                type(state['schema_version']) is not int or state['schema_version'] != 1 or
                encode_json(state['spec']) != encode_json(self.spec.to_json()) or
                type(state['closed']) is not bool):
            raise ValueError('Incompatible policy state')
        from botbowl.core.table import ActionType
        if state['last_type'] is not None and (type(state['last_type']) is not str or
                                                state['last_type'] not in ActionType.__members__):
            raise ValueError('Invalid last action')
        rng = state['rng']
        if (type(rng) is not list or len(rng) != 5 or rng[0] != 'MT19937' or
                type(rng[1]) is not list or len(rng[1]) != 624 or
                any(type(v) is not int or not 0 <= v < 2**32 for v in rng[1]) or
                type(rng[2]) is not int or not 0 <= rng[2] <= 624 or
                type(rng[3]) is not int or rng[3] not in (0, 1) or
                type(rng[4]) not in (int, float) or not -1e308 <= rng[4] <= 1e308):
            raise ValueError('Invalid private RNG state')
        candidate = SeedSpec(**self.spec.to_json()['seed']).generator()
        candidate.set_state(tuple(rng))
        self.rng = candidate
        self._last_type = state['last_type']
        self.closed = state['closed']

    def close(self):
        self.closed = True


# Immutable registry of shipped trusted factories; manifest strings never import.
_FACTORIES = MappingProxyType({name: ReferencePolicy for name in POLICIES})


def create_policy(spec):
    spec = PolicySpecV1.from_json(spec.to_json() if type(spec) is PolicySpecV1 else spec)
    data = spec.to_json()
    return _FACTORIES[data['name']](data['name'], SeedSpec(**data['seed']), data['config'])


class LegacyRandomAdapter:
    """Explicit privileged adapter of the shipped RandomBot; external-action replay only.

    A detached Game copy and a private dice source prevent even helper queries
    from consuming the live engine RNG. This is deliberately outside the standard
    catalog and receives substantially more information than primary policies.
    """

    metadata = MappingProxyType({'id': 'legacy_random', 'version': '1',
                                 'input_profile': 'privileged-game-copy',
                                 'restoration': 'external-actions-only'})

    def __init__(self, seed):
        if type(seed) is not SeedSpec or seed.purpose not in ('policy-home', 'policy-away'):
            raise ValueError('Expected a private policy SeedSpec')
        from botbowl.ai.bots.random_bot import RandomBot
        self.bot = RandomBot('lab-legacy-random', seed.seed_words())
        self._seed = seed
        self.closed = False

    def act(self, features, legal, control=None):
        raise ValueError('Legacy policy requires explicit act_game adapter access')

    def act_game(self, game, action_control):
        from botbowl.core.model import DiceSource
        if self.closed:
            raise ValueError('Policy is closed')
        copy = deepcopy(game)
        copy.dice = DiceSource(self._seed.seed_words())
        copy.dice.rng = self.bot.rnd
        # new_game on the detached roster prevents persistent live Game aliases.
        team = copy.state.home_team if action_control.legal_actions().actor_id == 'home' else copy.state.away_team
        self.bot.new_game(copy, team)
        if not any(not c.disabled and c.action_type.name != 'PLACE_PLAYER'
                   for c in copy.get_available_actions()):
            raise ValueError('Legacy RandomBot has no supported action')
        action = self.bot.act(copy)
        # Map copied engine entities through semantic identities, then validate
        # the resulting action against the live controller without giving it away.
        from .actions import ActionControl
        semantic = ActionControl(copy).encode(action)
        if semantic not in action_control.legal_actions().actions:
            raise ValueError('Legacy bot selected an unsupported semantic action')
        return semantic

    def close(self):
        self.closed = True
