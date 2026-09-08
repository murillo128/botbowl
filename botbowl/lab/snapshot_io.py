"""Bounded data-only SnapshotFileV1 persistence over the SIM-02 graph.

Tags select only this module's closed registry, never import names or reducers.
See docs/lab/snapshot-files.md for the wire grammar and compatibility boundary.
"""
from dataclasses import asdict, dataclass, fields
from enum import Enum
import hashlib
from importlib.metadata import version as package_version
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType

import numpy as np

from botbowl.core import model, procedure, table
from botbowl.core.forward_model import Reversible, ReversibleDict, ReversibleList, ReversibleSet, Trajectory
from botbowl.core.game import Game
from botbowl.core.util import Stack
from . import snapshots as memory
from .observations import ObservationControl
from .rules import (CAPABILITIES_VERSION, RULESET_IMPLEMENTATION_VERSION,
                    RulesDescriptor, _backend_id, describe_rules)
from .timeline import Timeline


class SnapshotFileError(memory.SnapshotError):
    code = 'snapshot_file_invalid'


class SnapshotIncompatibleError(SnapshotFileError):
    code = 'snapshot_file_incompatible'


class SnapshotLimitError(SnapshotFileError):
    code = 'snapshot_file_limit'


@dataclass(frozen=True)
class SnapshotLimits:
    max_bytes: int = 32 * 1024 * 1024
    max_nodes: int = 100000
    max_depth: int = 128

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise SnapshotLimitError('Limits must be positive integers')
        if self.max_depth > 256:
            raise SnapshotLimitError('Depth cannot exceed the implementation ceiling of 256')


@dataclass(frozen=True)
class SnapshotFileV1:
    format: str
    version: int
    descriptor: dict
    scope: str
    component_versions: dict
    payload: dict
    provenance: dict
    semantic_state_hash: str
    payload_digest: str


# Freeze named, code-owned procedure classes; adding a new procedure to the
# engine alone must NOT silently extend the persistent format.
_PROCEDURE_NAMES = (
    'Procedure Regeneration Apothecary Armor Stab FoulAppearance Block Bounce '
    'Casualty Catch Intercept CoinTossFlip CoinTossKickReceive Ejection Foul '
    'ResetHalf Half Injury Interception Touchback LandKick Fans Kickoff GetTheRef '
    'Riot HighKick CheeringFans BrilliantCoaching ThrowARock PitchInvasionRoll '
    'KickoffTable KnockDown KnockOut Leap Shadowing Tentacles Move GFI Dodge '
    'TurnoverIfPossessionLost Handoff Explode Land PassAttempt Pickup StandUp '
    'PlaceBall EndPlayerTurn JumpUpToBlock EscapeBeingEaten AlwaysHungry '
    'UndoPlayerAction MoveAction HandoffAction PassAction ThrowBombAction '
    'FoulAction BlockAction Frenzy BlitzAction StartGame EndGame Pregame '
    'PreKickoff FollowUp Push Scatter ClearBoard Setup ThrowIn Turnover Touchdown '
    'TurnStunned EndTurn Turn WeatherTable Negatrait Bonehead ReallyStupid '
    'WildAnimal TakeRoot BloodLustBlockOrMove BloodLust Reroll Pro Loner EatThrall '
    'HypnoticGaze'
).split()
_ENUM_NAMES = ('Tile BBDieResult RollType OutcomeType PlayerActionType PhysicalState '
               'CasualtyEffect CasualtyType ActionType WeatherType SkillCategory Skill PassDistance').split()
_ENUMS = MappingProxyType({name: getattr(table, name) for name in _ENUM_NAMES})
_CLASSES = MappingProxyType({
    **{'model/' + cls.__name__: cls for cls in memory._MODEL_TYPES},
    **{'procedure/' + name: getattr(procedure, name) for name in _PROCEDURE_NAMES},
    **{'data/' + cls.__name__: cls for cls in memory._DATA_TYPES},
    'Game': Game, 'Clock': model.Clock, 'Agent': memory._ExternalAgent,
    'Trajectory': Trajectory, 'Stack': Stack, 'Timeline': Timeline,
    'ObservationControl': ObservationControl, 'LogicalTime': memory.LogicalTime,
    'ComponentState': memory.ComponentState,
})
_TAGS = MappingProxyType({cls: tag for tag, cls in _CLASSES.items()})
_CONTAINERS = MappingProxyType({
    'list': list, 'tuple': tuple, 'dict': dict, 'set': set, 'frozenset': frozenset,
    'rlist': ReversibleList, 'rdict': ReversibleDict, 'rset': ReversibleSet,
})
_CONTAINER_TAGS = {cls: tag for tag, cls in _CONTAINERS.items()}
_SEQUENCES = {'list', 'tuple', 'rlist'}
_MAPPINGS = {'dict', 'rdict'}
_SETS = {'set', 'frozenset', 'rset'}
_SPECIAL_FIELDS = {
    Game: set(memory._GAME_DATA + ('seed', 'home_agent', 'away_agent', 'dice',
                                 'trajectory', 'timeline', 'time_source')),
    model.Clock: set('seconds started_at _started_at paused_at paused_seconds is_primary team time_source'.split()),
    memory._ExternalAgent: {'name', 'human', 'agent_id'},
    Trajectory: {'enabled'},
}


def _allowed_fields(cls):
    if cls in _SPECIAL_FIELDS:
        return _SPECIAL_FIELDS[cls]
    if cls in (*memory._DATA_TYPES, memory.ComponentState):
        return {field.name for field in fields(cls)}
    result = {name for base in cls.__mro__
              for name in memory._INSTANCE_FIELDS.get(base.__name__, '').split()}
    if issubclass(cls, Reversible):
        result.update(('_trajectory', '_ignored_keys'))
    if cls in (model.ActionChoice, model.Outcome, model.Square):
        result.add('__setattr__')
    return result


CODEC_FIELDS = MappingProxyType({tag: tuple(sorted(_allowed_fields(cls)))
                               for tag, cls in _CLASSES.items()})


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def _digest(value):
    return 'sha256:' + hashlib.sha256(_json(value)).hexdigest()


def _require(condition, message):
    if not condition:
        raise SnapshotFileError(message)


def _versions(adapters, names=()):
    registry = {} if adapters is None else adapters._by_name
    if any(name not in registry for name in names):
        raise SnapshotIncompatibleError('Missing registered episode data adapter')
    return {'graph': 1, 'snapshot': memory.VERSION, 'rng': 'MT19937-v1',
            'numpy': np.__version__, 'engine': package_version('botbowl'),
            'backend': _backend_id(),
            'schema': _digest({'fields': dict(CODEC_FIELDS), 'types': _FIELD_TYPES,
                              'procedure_fields': _PROCEDURE_FIELD_TYPES, 'items': _ITEM_TYPES,
                              'enums': {tag: list(cls.__members__) for tag, cls in _ENUMS.items()}}),
            'adapters': {name: 1 for name in sorted(names)}}


class _Encoder:
    def __init__(self, limits):
        self.limits, self.nodes, self.memo, self.owned = limits, [], {}, []

    def atom(self, value, depth=0):
        if depth > self.limits.max_depth:
            raise SnapshotLimitError('Graph depth limit exceeded')
        cls = type(value)
        if cls in (type(None), bool, int, float, str):
            _require(cls is not float or math.isfinite(value), 'Non-finite number')
            return value
        if isinstance(value, Enum):
            _require(_ENUMS.get(cls.__name__) is cls, 'Unknown enum type')
            return {'enum': [cls.__name__, value.name]}
        if cls is bytes:
            return {'bytes': value.hex()}
        if isinstance(value, np.generic) and value.dtype.kind in 'biufUS':
            return self.atom(value.item(), depth)
        if id(value) in self.memo:
            return {'ref': self.memo[id(value)]}
        if len(self.nodes) >= self.limits.max_nodes:
            raise SnapshotLimitError('Graph node limit exceeded')
        index = len(self.nodes)
        self.memo[id(value)] = index
        self.owned.append(value)  # Retain temporary dice frame data until done.
        node = {'id': index}
        self.nodes.append(node)
        atom = lambda item: self.atom(item, depth + 1)
        if cls in _CONTAINER_TAGS:
            tag = _CONTAINER_TAGS[cls]
            node.update(type=tag, items=([[atom(k), atom(v)] for k, v in value.items()]
                                        if tag in _MAPPINGS else [atom(v) for v in value]))
            if isinstance(value, Reversible):
                node['trajectory'] = atom(value._trajectory)
        elif cls is np.ndarray:
            node.update(type='array', dtype=value.dtype.str, shape=list(value.shape),
                        items=[atom(item) for item in value.flat])
        elif cls is np.random.RandomState:
            name, keys, pos, gauss, cached = value.get_state()
            node.update(type='rng', algorithm=name, keys=[int(key) for key in keys],
                        position=pos, has_gauss=gauss, cached_gaussian=cached)
        elif cls is model.DiceSource:
            node.update(type='dice', rng=atom(value.rng),
                        queues=[[[atom(v) for v in frame[die]]
                                 for die in (model.D3, model.D6, model.D8, model.BBDie)]
                                for frame in value._queues], strict=list(value._strict))
        else:
            _require(cls in _TAGS, 'Unknown reachable object or procedure type')
            attrs = vars(value)
            if cls is Game:
                attrs = {key: val for key, val in attrs.items() if key in _SPECIAL_FIELDS[Game]}
            elif cls is Trajectory:
                _require(not value.action_log, 'Undo history is outside SnapshotFileV1')
                attrs = {'enabled': value.enabled}
            _require(not set(attrs) - set(CODEC_FIELDS[_TAGS[cls]]), 'Unknown codec field')
            node.update(type=_TAGS[cls], fields=[[key, atom(val)] for key, val in attrs.items()])
        return {'ref': index}


def _bounded_json(raw, limits):
    if len(raw) > limits.max_bytes:
        raise SnapshotLimitError('File byte limit exceeded')
    # Scan nesting before json.loads (including ignored provenance). Strings and
    # escaped quotes do not contribute. JSON's parser still checks the grammar.
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > limits.max_depth:
                raise SnapshotLimitError('JSON depth limit exceeded')
        elif byte in (93, 125):
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise SnapshotFileError('Non-finite JSON number')

    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                              parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SnapshotFileError('Invalid UTF-8 JSON snapshot') from error
    # Count data values too: max_nodes also bounds hostile inline arrays/maps.
    pending, count = [document], 0
    while pending:
        value = pending.pop()
        count += 1
        if count > limits.max_nodes * 64:
            raise SnapshotLimitError('JSON value budget exceeded')
        if type(value) is dict:
            pending.extend(value.values())
        elif type(value) is list:
            pending.extend(value)
        elif type(value) is float:
            _require(math.isfinite(value), 'Non-finite JSON number')
    return document


class _Validator:
    """Pure JSON validation. No engine objects, arrays or adapters are allocated."""
    def __init__(self, payload, limits, adapters):
        self.limits, self.adapters = limits, adapters
        _require(type(payload) is dict and set(payload) == {'roots', 'nodes'}, 'Invalid graph payload')
        self.nodes = payload['nodes']
        _require(type(self.nodes) is list, 'Expected node array')
        if len(self.nodes) > limits.max_nodes:
            raise SnapshotLimitError('Graph node limit exceeded')
        self.edges = [[] for _ in self.nodes]
        self.names = set()
        self.array_bytes = 0
        for index, node in enumerate(self.nodes):
            _require(type(node) is dict and type(node.get('id')) is int and node['id'] == index,
                     'Duplicate, missing or out-of-order node ID')
            _require(type(node.get('type')) is str and node['type'] in
                     (*_CLASSES, *_CONTAINERS, 'array', 'rng', 'dice'), 'Unknown node type or procedure')
        for index, node in enumerate(self.nodes):
            self.node(index, node)
        roots = payload['roots']
        _require(type(roots) is dict and set(roots) == {'game', 'episode', 'components'}, 'Invalid roots')
        for value in roots.values():
            self.atom(value)
        self.expect(roots['game'], {'Game'})
        self.expect(roots['episode'], {'dict', 'null'})
        self.expect(roots['components'], {'dict'})
        self.graph_depth(roots)
        self.structure(roots)

    def kind(self, atom):
        if type(atom) is dict:
            if 'ref' in atom:
                return self.nodes[atom['ref']]['type']
            return 'enum/' + atom['enum'][0] if 'enum' in atom else 'bytes'
        return {type(None): 'null', bool: 'bool', int: 'int', float: 'float', str: 'str'}[type(atom)]

    def expect(self, atom, kinds):
        _require(self.kind(atom) in kinds, 'Invalid field/reference type; expected ' + ','.join(sorted(kinds)))

    def atom(self, atom, owner=None):
        cls = type(atom)
        if cls in (type(None), bool, int, float, str):
            _require(cls is not float or math.isfinite(atom), 'Non-finite atom')
            return
        _require(cls is dict and len(atom) == 1, 'Expected primitive, enum, bytes or reference')
        if 'ref' in atom:
            ref = atom['ref']
            _require(type(ref) is int and 0 <= ref < len(self.nodes), 'Dangling or invalid reference')
            if owner is not None:
                self.edges[owner].append(ref)
        elif 'enum' in atom:
            pair = atom['enum']
            _require(type(pair) is list and len(pair) == 2 and all(type(v) is str for v in pair),
                     'Invalid enum record')
            _require(pair[0] in _ENUMS and pair[1] in _ENUMS[pair[0]].__members__, 'Unknown enum')
        elif 'bytes' in atom:
            _require(type(atom['bytes']) is str and re.fullmatch(r'(?:[0-9a-f]{2})*', atom['bytes']) is not None,
                     'Invalid bytes record')
        else:
            raise SnapshotFileError('Unknown atom tag')

    def node(self, index, node):
        tag = node['type']
        expected = {'id', 'type'}
        if tag in _CONTAINERS or tag == 'array':
            expected.add('items')
            _require(type(node.get('items')) is list, 'Invalid container items')
            if tag.startswith('r'):
                expected.add('trajectory')
                self.atom(node.get('trajectory'), index)
                self.expect(node.get('trajectory'), {'Trajectory', 'null'})
            for item in node['items']:
                if tag in _MAPPINGS:
                    _require(type(item) is list and len(item) == 2, 'Expected mapping pairs')
                    for value in item:
                        self.atom(value, index)
                else:
                    self.atom(item, index)
            if tag == 'array':
                expected.update(('dtype', 'shape'))
                self.array(node)
        elif tag == 'rng':
            expected.update(('algorithm', 'keys', 'position', 'has_gauss', 'cached_gaussian'))
            _require(node.get('algorithm') == 'MT19937' and type(node.get('keys')) is list and
                     len(node['keys']) == 624 and all(type(v) is int and 0 <= v < 2**32 for v in node['keys']),
                     'Invalid MT19937 keys')
            _require(type(node.get('position')) is int and 0 <= node['position'] <= 624 and
                     type(node.get('has_gauss')) is int and node['has_gauss'] in (0, 1) and
                     type(node.get('cached_gaussian')) in (int, float) and
                     math.isfinite(node['cached_gaussian']), 'Invalid RNG index or Gaussian cache')
        elif tag == 'dice':
            expected.update(('rng', 'queues', 'strict'))
            self.atom(node.get('rng'), index)
            self.expect(node.get('rng'), {'rng'})
            queues, strict = node.get('queues'), node.get('strict')
            _require(type(queues) is list and queues and type(strict) is list and len(queues) == len(strict)
                     and all(type(v) is bool for v in strict), 'Invalid dice frames')
            for frame in queues:
                _require(type(frame) is list and len(frame) == 4, 'Expected four dice queues')
                for side, values in zip((3, 6, 8, None), frame):
                    _require(type(values) is list, 'Invalid dice queue')
                    for value in values:
                        self.atom(value, index)
                        if side is None:
                            self.expect(value, {'enum/BBDieResult'})
                        else:
                            _require(type(value) is int and 1 <= value <= side, 'Invalid forced roll')
        else:
            expected.add('fields')
            attrs = self.attrs(node)
            _require(not set(attrs) - set(CODEC_FIELDS[tag]), 'Unknown codec field')
            for value in attrs.values():
                self.atom(value, index)
            self.fields(tag, attrs)
        _require(set(node) == expected, 'Unknown or missing node members')

    @staticmethod
    def attrs(node):
        pairs = node.get('fields')
        _require(type(pairs) is list and all(type(p) is list and len(p) == 2 and type(p[0]) is str for p in pairs),
                 'Invalid fields')
        attrs = dict(pairs)
        _require(len(attrs) == len(pairs), 'Duplicate field')
        return attrs

    def array(self, node):
        dtype, shape = node.get('dtype'), node.get('shape')
        _require(type(dtype) is str and re.fullmatch(r'(?:[<>][iuf](?:2|4|8)|\|[biu]1|\|O|[<>]U[0-9]+|\|S[0-9]+)', dtype)
                 is not None, 'Unsupported array dtype')
        _require(type(shape) is list and len(shape) <= 32 and all(type(v) is int and 0 <= v <= self.limits.max_bytes
                                                               for v in shape), 'Invalid array dimensions')
        count = math.prod(shape)
        _require(count == len(node['items']), 'Array shape/data mismatch')
        size = 8 if dtype == '|O' else int(dtype[2:]) * (4 if dtype[1] == 'U' else 1)
        self.array_bytes += count * max(1, size)
        if size > self.limits.max_bytes or self.array_bytes > self.limits.max_bytes:
            raise SnapshotLimitError('Array allocation budget exceeded')
        for value in node['items']:
            code = dtype[1]
            if code == 'O':
                continue
            if code in 'iu':
                bits = size * 8
                lo, hi = (0, 2**bits) if code == 'u' else (-2**(bits - 1), 2**(bits - 1))
                _require(type(value) is int and lo <= value < hi, 'Array integer overflow')
            elif code == 'f':
                _require(type(value) in (int, float) and math.isfinite(value) and
                         abs(value) <= {2: 65504, 4: 3.4028234663852886e38, 8: 1.7976931348623157e308}.get(size, 0),
                         'Array float overflow')
            elif code == 'b':
                _require(type(value) is bool, 'Array boolean required')
            elif code == 'U':
                _require(type(value) is str and len(value) <= size // 4, 'Array string overflow')
            elif code == 'S':
                self.expect(value, {'bytes'})
                _require(len(value['bytes']) // 2 <= size, 'Array byte string overflow')

    def fields(self, tag, attrs):
        cls = _CLASSES[tag]
        if tag.startswith('procedure/'):
            _require({'game', 'context', 'started', 'done', '_trajectory', '_ignored_keys'} <= set(attrs),
                     'Missing procedure continuation base fields')
            self.expect(attrs['game'], {'Game'})
            for name in ('started', 'done'):
                self.expect(attrs[name], {'bool'})
            for name, value in attrs.items():
                if name in _PROCEDURE_FIELD_TYPES:
                    self.expect(value, set(_PROCEDURE_FIELD_TYPES[name].split()))
        elif cls in (*memory._DATA_TYPES, memory.ComponentState) or cls in _SPECIAL_FIELDS:
            required = set(CODEC_FIELDS[tag]) - ({'seed'} if cls is Game else set())
            _require(required <= set(attrs), 'Missing required codec fields')
        else:
            # These models have fixed fields; procedure-specific lazy fields are
            # separately inventoried above, never filled with guessed defaults.
            required = set(CODEC_FIELDS[tag]) - {'__setattr__'}
            _require(required <= set(attrs), 'Missing model fields: ' + tag)
        for name, value in attrs.items():
            if name == '__setattr__':
                self.expect(value, {'null'})
            elif name == '_trajectory':
                self.expect(value, {'Trajectory', 'null'})
            elif name == '_ignored_keys':
                self.expect(value, {'set'})
                _require(all(type(v) is str and v in CODEC_FIELDS[tag] for v in self.items(value)),
                         'Invalid reversible ignored field')
        if tag == 'ComponentState':
            self.expect(attrs['adapter'], {'str'})
            self.names.add(attrs['adapter'])
        for name, kinds in _FIELD_TYPES.get(tag, {}).items():
            if name in attrs:
                self.expect(attrs[name], set(kinds.split()))
        for name, kinds in _ITEM_TYPES.get(tag, {}).items():
            if name in attrs:
                self.expect(attrs[name], _SEQUENCES | _SETS)
                for item in self.items(attrs[name]):
                    self.expect(item, set(kinds.split()))

    def items(self, atom):
        return self.nodes[atom['ref']]['items']

    def object_fields(self, atom):
        return self.attrs(self.nodes[atom['ref']])

    def graph_depth(self, roots):
        seen, active = set(), set()
        def visit(index, depth):
            if index in active:
                _require(self.nodes[index]['type'] not in ('tuple', 'frozenset'), 'Cyclic immutable container')
                return  # Mutable/object cycles are explicit references.
            if depth > self.limits.max_depth:
                raise SnapshotLimitError('Graph reference depth exceeded')
            if index in seen:
                return
            seen.add(index)
            active.add(index)
            for ref in self.edges[index]:
                visit(ref, depth + 1)
            active.remove(index)
        for atom in roots.values():
            if type(atom) is dict:
                visit(atom['ref'], 0)
        _require(len(seen) == len(self.nodes), 'Unreachable graph nodes')
        # Container keys/elements are restricted to safe hashable engine types;
        # never invoke a user-defined hash/equality function during allocation.
        def key(atom, pending):
            if type(atom) is not dict or 'ref' not in atom:
                if type(atom) in (bool, int, float):
                    return ('number', atom)  # Python keys: True == 1 == 1.0.
                if type(atom) is dict:
                    return ('enum', *atom['enum']) if 'enum' in atom else ('bytes', atom['bytes'])
                return (type(atom).__name__, atom)
            index = atom['ref']
            _require(index not in pending, 'Cyclic immutable container')
            node = self.nodes[index]
            tag = node['type']
            if tag in ('tuple', 'frozenset'):
                pending.add(index)
                parts = [key(v, pending) for v in node['items']]
                pending.remove(index)
                return (tag, frozenset(parts) if tag == 'frozenset' else tuple(parts))
            _require(tag.startswith('procedure/') or tag in ('Game', 'model/Player', 'model/Team', 'model/Square'),
                     'Unhashable or unsupported graph key')
            attrs = self.object_fields(atom)
            identity = (attrs['player_id'] if tag == 'model/Player' else
                        attrs['team_id'] if tag == 'model/Team' else
                        [attrs['x'], attrs['y']] if tag == 'model/Square' else index)
            return (tag, tuple(identity) if type(identity) is list else identity)
        for node in self.nodes:
            if node['type'] in (*_MAPPINGS, *_SETS):
                keys = [key(v[0] if node['type'] in _MAPPINGS else v, set()) for v in node['items']]
                _require(len(keys) == len(set(keys)), 'Duplicate mapping key or set element')

    def structure(self, roots):
        game = self.object_fields(roots['game'])
        state = self.object_fields(game['state'])
        pitch = self.object_fields(state['pitch'])
        width, height = pitch['width'], pitch['height']
        _require(type(width) is int and type(height) is int and 3 <= width <= 100 and 3 <= height <= 100,
                 'Invalid pitch dimensions')
        teams = self.items(state['teams'])
        _require(teams == [state['home_team'], state['away_team']], 'Invalid canonical teams')
        ids, players = set(), {}
        for team in teams:
            attrs = self.object_fields(team)
            _require(attrs['team_id'] not in ids, 'Duplicate team identity')
            ids.add(attrs['team_id'])
            for player in self.items(attrs['players']):
                self.expect(player, {'model/Player'})
                data = self.object_fields(player)
                _require(data['team'] == team and data['player_id'] not in players, 'Invalid player/team alias')
                players[data['player_id']] = player
        for name in ('board', 'squares'):
            rows = self.items(pitch[name])
            _require(len(rows) == height, 'Pitch row count mismatch')
            for y, row in enumerate(rows):
                self.expect(row, _SEQUENCES)
                cells = self.items(row)
                _require(len(cells) == width, 'Pitch column count mismatch')
                for x, value in enumerate(cells):
                    self.expect(value, {'model/Square'} if name == 'squares' else {'model/Player', 'null'})
                    if value is not None:
                        cell = self.object_fields(value)
                        if name == 'squares':
                            _require((cell['x'], cell['y']) == (x, y), 'Invalid square index')
                        else:
                            _require(players.get(cell['player_id']) == value and cell['position'] is not None,
                                     'Noncanonical board player')
                            pos = self.object_fields(cell['position'])
                            _require((pos['x'], pos['y']) == (x, y), 'Board position mismatch')
        for player in players.values():
            pos = self.object_fields(player)['position']
            if pos is not None:
                pos = self.object_fields(pos)
                _require(0 <= pos['x'] < width and 0 <= pos['y'] < height, 'Player index out of bounds')
                row = self.items(pitch['board'])[pos['y']]
                _require(self.items(row)[pos['x']] == player, 'Player missing from board')
        for node in self.nodes:
            if node['type'].startswith('procedure/'):
                _require(self.attrs(node)['game'] == roots['game'], 'Foreign procedure Game')
        # A component state may share owned RNG streams, but may never smuggle
        # an engine object or registered constructor through a reference alias.
        visited = set()
        def component_data(atom):
            if type(atom) is not dict or 'ref' not in atom or atom['ref'] in visited:
                return
            index = atom['ref']
            visited.add(index)
            record = self.nodes[index]
            _require(record['type'] in ('list', 'dict', 'set', 'tuple', 'frozenset',
                                        'array', 'rng', 'ComponentState'), 'Engine reference in component data')
            for ref in self.edges[index]:
                component_data({'ref': ref})
        for record in self.nodes:
            if record['type'] == 'ComponentState':
                component_data({'ref': record['id']})
        stack = self.object_fields(state['stack'])
        for value in self.items(stack['items']):
            _require(self.kind(value).startswith('procedure/'), 'Non-procedure on stack')
        episode = roots['episode']
        components = self.items(roots['components'])
        _require(all(type(k) is str and k in ('policy-home', 'policy-away', 'scenario', 'agent-home', 'agent-away')
                     and self.kind(v) == 'ComponentState' for k, v in components), 'Invalid component roots')
        if episode is not None:
            attrs = dict(self.items(episode))
            _require(set(attrs) == set(memory._EPISODE_DATA), 'Invalid episode fields')
            self.expect(attrs['_control'], {'ObservationControl'})
            self.expect(attrs['_streams'], {'dict'})
            for name in ('decisions', '_max_decisions', '_max_steps'):
                _require(type(attrs[name]) is int and attrs[name] >= (1 if name == '_max_steps' else 0),
                         'Invalid episode budget')
            _require(set(dict(self.items(attrs['_streams']))) == {'scenario', 'policy-home', 'policy-away', 'observation'},
                     'Invalid episode stream inventory')
            for _, value in self.items(attrs['_streams']):
                self.expect(value, {'rng'})
        else:
            _require(not components, 'Engine Game cannot contain episode components')


_FIELD_TYPES = {
    'Game': {'state': 'model/GameState', 'config': 'model/Configuration', 'arena': 'model/TwoPlayerArena',
             'ruleset': 'model/RuleSet', 'action': 'model/Action null', 'home_agent': 'Agent', 'away_agent': 'Agent',
             'dice': 'dice', 'trajectory': 'Trajectory', 'timeline': 'Timeline null', 'time_source': 'LogicalTime',
             '_initialized': 'bool', '_closed': 'bool', '_end_notified': 'bool', 'external_control': 'bool'},
    'model/GameState': {'pitch': 'model/Pitch', 'stack': 'Stack', 'teams': 'list rlist',
                        'home_team': 'model/Team', 'away_team': 'model/Team', 'half': 'int', 'round': 'int',
                        'game_over': 'bool', 'available_actions': 'list rlist', 'weather': 'enum/WeatherType',
                        'reports': 'list rlist', 'dugouts': 'dict rdict', 'clocks': 'list rlist',
                        'player_by_id': 'dict rdict', 'team_by_id': 'dict rdict', 'team_by_player_id': 'dict rdict'},
    'model/Pitch': {'width': 'int', 'height': 'int', 'board': 'list rlist', 'squares': 'list rlist',
                    'balls': 'list rlist', 'bomb': 'model/Bomb null'},
    'model/Player': {'player_id': 'str', 'team': 'model/Team null', 'position': 'model/Square null',
                     'role': 'model/Role', 'state': 'model/PlayerState'},
    'model/Team': {'team_id': 'str', 'players': 'list rlist', 'state': 'model/TeamState'},
    'model/Square': {'x': 'int', 'y': 'int', '_out_of_bounds': 'bool null'},
    'model/Action': {'action_type': 'enum/ActionType', 'player': 'model/Player null', 'position': 'model/Square null'},
    'model/ActionChoice': {'action_type': 'enum/ActionType', 'team': 'model/Team', 'players': 'list rlist',
                          'positions': 'list rlist', 'paths': 'list rlist'},
    'model/Outcome': {'outcome_type': 'enum/OutcomeType'},
    'Stack': {'items': 'list rlist'},
    'Trajectory': {'enabled': 'bool'},
    'Agent': {'name': 'str', 'agent_id': 'str', 'human': 'bool'},
    'Clock': {'seconds': 'int float', 'started_at': 'int float', '_started_at': 'int float',
              'paused_at': 'null int float', 'paused_seconds': 'int float', 'is_primary': 'bool',
              'team': 'model/Team', 'time_source': 'LogicalTime'},
    'LogicalTime': {'value': 'int float'},
    'ObservationControl': {'_game': 'Game'},
    'Timeline': {'_game': 'Game', '_entities': 'ObservationControl', '_context': 'data/TimelineContext',
                 '_events': 'list', '_decisions': 'list', '_pending': 'null'},
}

_PROCEDURE_FIELD_TYPES = {
    **{name: 'model/Player null' for name in (
        'player attacker defender inflictor passer interceptor fouler catcher pusher '
        'shadower tentacler delicious_player hungry_player victim '
        'target_player selected_player diving_tackler player_chain').split()},
    **{name: 'model/Team null' for name in ('team receiving_team').split()},
    **{name: 'model/Square null' for name in (
        'position from_position pos_to follow_to push_to victim_pos').split()},
    'ball': 'model/Ball null', 'piece': 'model/Ball model/Bomb model/Player null',
    'reroll': 'procedure/Reroll null', 'skill': 'enum/Skill null',
    'roll_type': 'enum/RollType null', 'pass_distance': 'enum/PassDistance null',
    'orig_action_type': 'enum/ActionType null', 'player_action_type': 'enum/PlayerActionType null',
}

_ITEM_TYPES = {
    'model/GameState': {'teams': 'model/Team', 'clocks': 'Clock', 'reports': 'model/Outcome',
                        'available_actions': 'model/ActionChoice', 'rerolled_procs':
                        ' '.join('procedure/' + name for name in _PROCEDURE_NAMES)},
    'model/Team': {'players': 'model/Player'},
    'model/ActionChoice': {'players': 'model/Player', 'positions': 'model/Square null'},
    'model/Dugout': {key: 'model/Player' for key in ('reserves', 'kod', 'casualties', 'dungeon')},
    'model/Pitch': {'balls': 'model/Ball'},
}


class _Decoder:
    def __init__(self, validator):
        self.nodes, self.memo, self.active = validator.nodes, {}, set()

    def atom(self, atom):
        if type(atom) is not dict:
            return atom
        if 'enum' in atom:
            tag, name = atom['enum']
            return _ENUMS[tag][name]
        if 'bytes' in atom:
            return bytes.fromhex(atom['bytes'])
        index = atom['ref']
        if index in self.memo:
            return self.memo[index]
        _require(index not in self.active, 'Cyclic immutable container')
        self.active.add(index)
        node = self.nodes[index]
        tag = node['type']
        if tag in ('tuple', 'frozenset'):
            value = _CONTAINERS[tag](self.atom(v) for v in node['items'])
        elif tag in _CONTAINERS:
            cls = _CONTAINERS[tag]
            value = cls() if tag in ('list', 'dict', 'set') else cls(())
            self.memo[index] = value
            if tag in _MAPPINGS:
                for key, item in node['items']:
                    dict.__setitem__(value, self.atom(key), self.atom(item))
            elif tag in _SETS:
                for item in node['items']:
                    set.add(value, self.atom(item))
            else:
                for item in node['items']:
                    list.append(value, self.atom(item))
            if tag.startswith('r'):
                object.__setattr__(value, '_trajectory', self.atom(node['trajectory']))
        elif tag == 'array':
            value = np.empty(tuple(node['shape']), dtype=node['dtype'])
            self.memo[index] = value
            for i, item in enumerate(node['items']):
                value.flat[i] = self.atom(item)
        elif tag == 'rng':
            value = np.random.RandomState(0)
            value.set_state(('MT19937', node['keys'], node['position'], node['has_gauss'], node['cached_gaussian']))
        elif tag == 'dice':
            value = object.__new__(model.DiceSource)
            self.memo[index] = value
            value.rng = self.atom(node['rng'])
            value._queues = [dict(zip((model.D3, model.D6, model.D8, model.BBDie),
                                      ([self.atom(v) for v in queue] for queue in frame)))
                             for frame in node['queues']]
            value._strict = list(node['strict'])
            value._scopes = [object() for _ in node['strict'][1:]]
        else:
            value = object.__new__(_CLASSES[tag])
            self.memo[index] = value
            # These are the only value-hashed engine keys. Initialize their
            # validated scalar identities before following cyclic references.
            attrs = dict(node['fields'])
            identity_fields = {'model/Player': ('player_id',), 'model/Team': ('team_id',),
                               'model/Square': ('x', 'y')}.get(tag, ())
            for key in identity_fields:
                object.__setattr__(value, key, attrs[key])
            decoded = {}
            for key, item in node['fields']:
                decoded[key] = self.atom(item)
                object.__setattr__(value, key, decoded[key])
            object.__setattr__(value, '__dict__', decoded)
            if tag == 'Trajectory':
                value.action_log = []
        self.memo[index] = value
        self.active.remove(index)
        return value


def _semantic_hash(payload, scope):
    """Canonical traversal normalizes node IDs and roster-local entity IDs.

    Opaque adapter state remains causal; provenance is never part of this root.
    This is a state identity, not proof of equivalence for arbitrary policies.
    """
    nodes = payload['nodes']
    roots = payload['roots']
    attrs = lambda atom: dict(nodes[atom['ref']]['fields'])
    game = attrs(roots['game'])
    state = attrs(game['state'])
    identities = {}
    for side in ('home', 'away'):
        team = attrs(state[side + '_team'])
        identities[team['team_id']] = ['team', side]
        for slot, player in enumerate(nodes[team['players']['ref']]['items']):
            identities[attrs(player)['player_id']] = ['player', side, slot]
        identities[attrs(game[side + '_agent'])['agent_id']] = ['agent', side]
    ignored = {'Game': {'game_id', 'start_time', 'end_time', 'last_request_time', 'last_action_time'},
               'model/Configuration': {'name', 'arena'},
               'data/TimelineContext': {'episode_id', 'branch_id'},
               'Clock': {'started_at'}}
    episode = roots['episode']
    memo = {}
    def sort_key(atom, active=()):
        if type(atom) is dict and 'ref' in atom:
            node = nodes[atom['ref']]
            if atom['ref'] in active or node['type'] == 'Game':
                return _json(['cycle', node['type']])
            active = (*active, atom['ref'])
            if node['type'] in ('model/Player', 'model/Team'):
                key = 'player_id' if node['type'] == 'model/Player' else 'team_id'
                return _json(identities.get(dict(node['fields'])[key]))
            if node['type'] == 'model/Square':
                data = dict(node['fields'])
                return _json(['square', data['x'], data['y']])
            if node['type'] in ('tuple', 'frozenset'):
                return b'[' + b','.join(sort_key(v, active) for v in node['items']) + b']'
            if 'fields' in node:
                return b'[' + b','.join(_json(k) + sort_key(v, active) for k, v in sorted(node['fields'])
                                       if k not in ignored.get(node['type'], ()) and k != '_trajectory') + b']'
            if 'items' in node:
                items = node['items']
                if node['type'] in _MAPPINGS:
                    return b'[' + b','.join(sorted(sort_key(k, active) + sort_key(v, active) for k, v in items)) + b']'
                return b'[' + b','.join(sort_key(v, active) for v in items) + b']'
            return _json({k: v for k, v in node.items() if k != 'id'})
        return _json(identities.get(atom, atom) if type(atom) is str else atom)
    def visit(atom, opaque=False):
        if type(atom) is str and not opaque and atom in identities:
            return {'entity': identities[atom]}
        if type(atom) is not dict or 'ref' not in atom:
            return atom
        index = atom['ref']
        if index in memo:
            return {'ref': memo[index]}
        memo[index] = len(memo)
        node = nodes[index]
        tag = node['type']
        result = {'id': memo[index], 'type': tag}
        if 'fields' in node:
            result['fields'] = [[key, visit(value, opaque or tag == 'ComponentState' and key == 'state')]
                                for key, value in sorted(node['fields']) if key not in ignored.get(tag, ())]
        elif 'items' in node:
            items = node['items']
            if tag in _MAPPINGS:
                if atom == episode:
                    items = [pair for pair in items if pair[0] != '_manifest']
                result['items'] = [[visit(k, opaque), visit(v, opaque)] for k, v in
                                   sorted(items, key=lambda pair: sort_key(pair[0]))]
            else:
                result['items'] = [visit(v, opaque) for v in
                                   (sorted(items, key=sort_key) if tag in _SETS else items)]
            for key in ('shape', 'dtype'):
                if key in node:
                    result[key] = node[key]
            if 'trajectory' in node:
                result['trajectory'] = visit(node['trajectory'])
        elif tag == 'dice':
            result.update(rng=visit(node['rng']), queues=node['queues'], strict=node['strict'])
        else:
            result.update({key: value for key, value in node.items() if key not in ('id', 'type')})
        return result
    return _digest([scope, {key: visit(value) for key, value in sorted(roots.items())}])


def _descriptor(game):
    return describe_rules(game.config, game.ruleset, game.arena,
                          game.state.home_team, game.state.away_team).to_json()


def write_snapshot(path, snapshot, *, adapters=None, limits=SnapshotLimits(), provenance=None):
    """Atomically persist a SIM-02 Snapshot; return its data-only file envelope.

    The destination is replaced only after complete encoding/validation, flush
    and fsync. A failed write leaves a previous file intact. No legacy fallback.
    """
    temporary = None
    try:
        _require(type(snapshot) is memory.Snapshot, 'Expected capture_snapshot() result')
        # Reuse SIM-02 validation and detached reconstruction, including adapter
        # validation. The original and its live forced scopes remain untouched.
        memory.clone_from_snapshot(snapshot, adapters=adapters)
        graph = _Encoder(limits)
        roots = {name: graph.atom(value) for name, value in (
            ('game', snapshot._game), ('episode', snapshot._episode), ('components', snapshot._components))}
        payload = {'roots': roots, 'nodes': graph.nodes}
        validator = _Validator(payload, limits, adapters)
        document = dict(format='SnapshotFileV1', version=1, descriptor=_descriptor(snapshot._game),
                        scope=snapshot.scope, component_versions=_versions(adapters, validator.names),
                        payload=payload, provenance={} if provenance is None else provenance,
                        semantic_state_hash=_semantic_hash(payload, snapshot.scope))
        _require(type(document['provenance']) is dict, 'Provenance must be JSON data')
        document['payload_digest'] = _digest(document)
        raw = _json(document) + b'\n'
        _bounded_json(raw, limits)
        destination = Path(path)
        with tempfile.NamedTemporaryFile(mode='wb', dir=destination.parent,
                                         prefix='.' + destination.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        return SnapshotFileV1(**document)
    except memory.SnapshotError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        raise SnapshotFileError('Cannot encode snapshot data') from error
    finally:
        if temporary is not None:
            os.unlink(temporary)


def read_snapshot(path, *, adapters=None, limits=SnapshotLimits()):
    """Validate a bounded UTF-8 file, then return a private SIM-02 Snapshot.

    Use clone_from_snapshot or restore_snapshot with the same trusted adapters.
    File adapter names are inert keys; this function never imports file names.
    """
    try:
        with open(path, 'rb') as stream:
            raw = stream.read(limits.max_bytes + 1)
        document = _bounded_json(raw, limits)
        _require(type(document) is dict and set(document) == {f.name for f in fields(SnapshotFileV1)},
                 'Invalid file envelope')
        if document['format'] != 'SnapshotFileV1' or type(document['version']) is not int or document['version'] != 1:
            raise SnapshotIncompatibleError('Unsupported snapshot file format/version')
        supplied = document['payload_digest']
        _require(type(supplied) is str and supplied == _digest({k: v for k, v in document.items() if k != 'payload_digest'}),
                 'Snapshot payload digest mismatch')
        _require(document['scope'] in ('engine', 'episode') and type(document['provenance']) is dict,
                 'Invalid scope or provenance')
        validator = _Validator(document['payload'], limits, adapters)
        if _json(document['component_versions']) != _json(_versions(adapters, validator.names)):
            raise SnapshotIncompatibleError('Incompatible engine, codec, RNG, backend or adapter version')
        descriptor = document['descriptor']
        _require(type(descriptor) is dict and set(descriptor) == {f.name for f in fields(RulesDescriptor)}
                 and all(type(v) is str for v in descriptor.values()), 'Invalid RulesDescriptor')
        if (descriptor['ruleset_id'] != 'BB2016' or
                not descriptor['ruleset_version'].startswith(RULESET_IMPLEMENTATION_VERSION + '+sha256.') or
                descriptor['engine_version'] != package_version('botbowl') or
                descriptor['capabilities_version'] != CAPABILITIES_VERSION or descriptor['backend_id'] != _backend_id()):
            raise SnapshotIncompatibleError('Incompatible ruleset or descriptor version')
        roots = document['payload']['roots']
        _require(document['scope'] != 'episode' or roots['episode'] is not None, 'Episode scope requires episode data')
        _require(document['scope'] != 'engine' or not validator.items(roots['components']), 'Engine scope contains policies')
        _require(document['semantic_state_hash'] == _semantic_hash(document['payload'], document['scope']),
                 'Semantic state hash mismatch')
        decoder = _Decoder(validator)
        game, episode, components = (decoder.atom(roots[name]) for name in ('game', 'episode', 'components'))
        # No Game/Procedure/Clock/Episode init, rule execution or random draw.
        game.square_shortcut = game.state.pitch.squares
        game.ff_map = game.replay = None
        game.finalization_errors = []
        game._snapshot_busy, game._snapshot_ready = 0, True
        if _descriptor(game) != descriptor:
            raise SnapshotIncompatibleError('RulesDescriptor disagrees with graph resources')
        compatibility = memory._identity(game)
        scope = document['scope']
        payload = (game, episode, components, compatibility)
        snapshot = memory.Snapshot(memory.VERSION, scope, 0, os.getpid(), game, episode, components,
                                   (), compatibility, memory._fingerprint((scope, (), payload)))
        # SIM-02 rebuilds/checks derived indexes, trajectory and path caches on a
        # private graph before this API publishes even an in-memory Snapshot.
        memory.clone_from_snapshot(snapshot, adapters=adapters)
        return snapshot
    except SnapshotFileError:
        raise
    except memory.SnapshotError as error:
        raise SnapshotFileError('Invalid executable snapshot graph') from error
    except (TypeError, ValueError, KeyError, AttributeError, IndexError, OverflowError, RecursionError) as error:
        raise SnapshotFileError('Malformed snapshot file') from error
