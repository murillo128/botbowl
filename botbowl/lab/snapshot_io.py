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
from . import snapshot_schema as schema
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
    max_work: int = 1000000

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
CODEC_SCHEMA = MappingProxyType(schema.contracts(_CLASSES, CODEC_FIELDS))


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
            'schema': _digest({'fields': dict(CODEC_SCHEMA), 'lazy': schema.LAZY, 'episode': schema.EPISODE,
                              'action_targets': schema.ACTION_TARGETS,
                              'key_work_version': 1, 'semantic_version': 3,
                              'resource_limits_version': 1,
                              'enums': {tag: list(cls.__members__) for tag, cls in _ENUMS.items()}}),
            'adapters': {name: 1 for name in sorted(names)}}


class _Encoder:
    def __init__(self, limits):
        self.limits, self.nodes, self.memo, self.owned = limits, [], {}, []

    def atom(self, value, depth=0):
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
        # Count newly entered wire vertices, not scalar leaves or memo hits.
        # Any simple traversal path has at most D(G) vertices. This early guard
        # therefore cannot reject a graph admitted by the topology preflight.
        if depth >= self.limits.max_depth:
            raise SnapshotLimitError('Graph depth limit exceeded')
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


class _Work:
    """One ledger for validation, semantic sorting and materialization."""
    def __init__(self, limits):
        self.limit, self.used = limits.max_work, 0
        self.limits = limits
        self.retained_bytes = 0

    def spend(self, amount=1):
        self.used += amount
        if self.used > self.limit:
            raise SnapshotLimitError('Graph work limit exceeded')


def _reference_atoms(node):
    """Visit the closed wire grammar's atom slots without recursive expansion."""
    if 'fields' in node:
        for _, value in node['fields']:
            yield value
    elif 'items' in node:
        for value in node['items']:
            if node['type'] in _MAPPINGS:
                yield value[0]
                yield value[1]
            else:
                yield value
    elif node['type'] == 'dice':
        yield node['rng']
        for frame in node['queues']:
            for queue in frame:
                yield from queue
    if 'trajectory' in node:
        yield node['trajectory']


def _topology(nodes, roots, outgoing, edge_count, atom_count, reservation, work, instrument=None):
    """Admit topology with one prepaid iterative Tarjan traversal.

    Work is invariantly prepaid as 6V + E + 2U + 32. SCC weights and successor
    depth are fused into returns and pops, so there is no reverse graph,
    condensation graph or second edge scan.
    """
    vertices, root_slots = len(nodes), len(roots)
    allowance = 6 * vertices + edge_count + 2 * root_slots + 32
    work.spend(allowance)
    discovery = [-1] * vertices
    lowlink = [0] * vertices
    component = [-1] * vertices
    successor_depth = [0] * vertices
    frame_active = bytearray(vertices)
    frame_vertices, frame_edges, members, depths = [], [], [], []
    discovered = child_returns = examined = popped = closed = 0

    def enter(vertex):
        nonlocal discovered
        discovery[vertex] = lowlink[vertex] = discovered
        discovered += 1
        frame_active[vertex] = 1
        frame_vertices.append(vertex)
        frame_edges.append(0)
        members.append(vertex)

    for atom in roots.values():
        if type(atom) is not dict or 'ref' not in atom or discovery[atom['ref']] != -1:
            continue
        enter(atom['ref'])
        while frame_vertices:
            vertex = frame_vertices[-1]
            offset = frame_edges[-1]
            if offset < len(outgoing[vertex]):
                target = outgoing[vertex][offset]
                frame_edges[-1] = offset + 1
                examined += 1
                if discovery[target] == -1:
                    enter(target)
                    continue
                if frame_active[target]:
                    _require(nodes[target]['type'] not in ('tuple', 'frozenset'),
                             'Cyclic immutable container')
                if component[target] == -1:
                    lowlink[vertex] = min(lowlink[vertex], discovery[target])
                else:
                    successor_depth[vertex] = max(successor_depth[vertex], depths[component[target]])
                continue

            frame_active[vertex] = 0
            if lowlink[vertex] == discovery[vertex]:
                label, weight, maximum = len(depths), 0, 0
                while True:
                    member = members.pop()
                    component[member] = label
                    weight += 1
                    popped += 1
                    maximum = max(maximum, successor_depth[member])
                    if member == vertex:
                        break
                depths.append(weight + maximum)
                closed += 1
            frame_vertices.pop()
            frame_edges.pop()
            if frame_vertices:
                parent = frame_vertices[-1]
                child_returns += 1
                if component[vertex] == -1:
                    lowlink[parent] = min(lowlink[parent], lowlink[vertex])
                else:
                    successor_depth[parent] = max(successor_depth[parent], depths[component[vertex]])

    _require(discovered == vertices, 'Unreachable graph nodes')
    depth = max((depths[component[atom['ref']]] for atom in roots.values()
                 if type(atom) is dict and 'ref' in atom), default=0)
    if depth > work.limits.max_depth:
        raise SnapshotLimitError('Graph reference depth exceeded')
    if instrument is not None:
        instrument.update(vertices=vertices, atoms=atom_count, edges=edge_count, roots=root_slots,
                          components=tuple(component), component_depths=tuple(depths), depth=depth,
                          allowance=allowance, examined=examined, child_returns=child_returns,
                          popped=popped, closed=closed, reservation=reservation)
    work.retained_bytes = 256
    return (vertices, atom_count, edge_count, root_slots, depth, reservation, allowance)


def _direct_topology(payload, work, instrument=None):
    """Prepare standalone hashing without trusting validator-owned state."""
    _require(type(payload) is dict and set(payload) == {'roots', 'nodes'}, 'Invalid graph payload')
    nodes, roots = payload['nodes'], payload['roots']
    _require(type(nodes) is list, 'Expected node array')
    _require(type(roots) is dict and set(roots) == {'game', 'episode', 'components'}, 'Invalid roots')
    if len(nodes) > work.limits.max_nodes:
        raise SnapshotLimitError('Graph node limit exceeded')
    for index, node in enumerate(nodes):
        work.spend()
        _require(type(node) is dict and type(node.get('id')) is int and node['id'] == index,
                 'Duplicate, missing or out-of-order node ID')
        _require(type(node.get('type')) is str and node['type'] in
                 (*_CLASSES, *_CONTAINERS, 'array', 'rng', 'dice'), 'Unknown node type or procedure')
        for atom in _reference_atoms(node):
            work.spend()
            if type(atom) is dict and 'ref' in atom:
                _require(type(atom['ref']) is int and 0 <= atom['ref'] < len(nodes),
                         'Dangling or invalid reference')
    for atom in roots.values():
        work.spend()
        if type(atom) is dict and 'ref' in atom:
            _require(type(atom['ref']) is int and 0 <= atom['ref'] < len(nodes),
                     'Dangling or invalid reference')

    atom_count = edge_count = 0
    reservation = 4096 + 512 * len(nodes) + 256 * len(roots)
    if reservation > work.limits.max_bytes:
        raise SnapshotLimitError('Topology workspace byte limit exceeded')
    outgoing = [[] for _ in nodes]
    for index, node in enumerate(nodes):
        work.spend()
        for atom in _reference_atoms(node):
            work.spend()
            atom_count += 1
            if type(atom) is dict and 'ref' in atom:
                reservation += 128
                if reservation > work.limits.max_bytes:
                    raise SnapshotLimitError('Topology workspace byte limit exceeded')
                outgoing[index].append(atom['ref'])
                edge_count += 1
    for _ in roots.values():
        work.spend()
    return outgoing, _topology(nodes, roots, outgoing, edge_count, atom_count,
                               reservation, work, instrument)


class _GraphKeys:
    """Intern shallow signatures and bound expanded immutable key work.

    Child tokens are integers, never nested tuple/frozenset signatures. Reserve
    eight expanded traversals per immutable node for Python hashing/equality in
    decoding and the SIM-02 copies; shared DAG edges still count multiplicity.
    Costs saturate at the budget so neither arithmetic nor recursion expands.
    """
    def __init__(self, nodes, work):
        self.nodes, self.work = nodes, work
        self.memo, self.tokens, self.costs, self.active = {}, {}, {}, set()

    def token(self, signature):
        self.work.spend()
        if signature not in self.tokens:
            self.tokens[signature] = len(self.tokens)
        return self.tokens[signature]

    def cost(self, atom):
        self.work.spend()
        if type(atom) is not dict or 'ref' not in atom:
            return 1
        index = atom['ref']
        if index in self.costs:
            return self.costs[index]
        node = self.nodes[index]
        if node['type'] not in ('tuple', 'frozenset'):
            return 1
        _require(index not in self.active, 'Cyclic immutable container')
        if len(self.active) >= self.work.limits.max_depth:
            raise SnapshotLimitError('Immutable key depth limit exceeded')
        self.active.add(index)
        result = 1
        for value in node['items']:
            result = min(self.work.limit + 1, result + self.cost(value))
        self.active.remove(index)
        self.costs[index] = result
        return result

    def key(self, atom):
        self.work.spend()
        if type(atom) is not dict or 'ref' not in atom:
            # Prepay variable-sized token hashing/comparison before interning.
            scalar = atom.get('bytes', '') if type(atom) is dict else atom
            size = len(scalar) if type(scalar) is str else scalar.bit_length() // 8 if type(scalar) is int else 0
            self.work.spend((size + 63) // 64)
            if type(atom) in (bool, int, float):
                return self.token(('number', atom))  # True == 1 == 1.0 in Python.
            if type(atom) is dict:
                return self.token(('enum', *atom['enum']) if 'enum' in atom else ('bytes', atom['bytes']))
            return self.token((type(atom).__name__, atom))
        index = atom['ref']
        if index in self.memo:
            return self.memo[index]
        node = self.nodes[index]
        tag = node['type']
        if tag in ('tuple', 'frozenset'):
            _require(index not in self.active, 'Cyclic immutable container')
            if len(self.active) >= self.work.limits.max_depth:
                raise SnapshotLimitError('Immutable key depth limit exceeded')
            self.active.add(index)
            parts = [self.key(v) for v in node['items']]
            self.active.remove(index)
            if tag == 'frozenset':
                self.work.spend(len(parts) * max(1, len(parts).bit_length()))
                parts = sorted(set(parts))
            signature = (tag, *parts)
        else:
            _require(tag.startswith('procedure/') or tag in ('Game', 'model/Player', 'model/Team', 'model/Square'),
                     'Unhashable or unsupported graph key')
            attrs = dict(node['fields'])
            identity = ((attrs['player_id'],) if tag == 'model/Player' else
                        (attrs['team_id'],) if tag == 'model/Team' else
                        (attrs['x'], attrs['y']) if tag == 'model/Square' else (index,))
            signature = (tag, *identity)
        result = self.token(signature)
        self.memo[index] = result
        return result

    def validate(self):
        reservation = 0
        for node in self.nodes:
            self.work.spend()
            if node['type'] in ('tuple', 'frozenset'):
                amount = 8 * self.cost({'ref': node['id']})
                reservation += amount
                self.work.spend(amount)
        for node in self.nodes:
            if node['type'] in (*_MAPPINGS, *_SETS):
                keys = [self.key(v[0] if node['type'] in _MAPPINGS else v) for v in node['items']]
                _require(len(keys) == len(set(keys)), 'Duplicate mapping key or set element')
        return reservation


class _Validator:
    """Pure JSON validation. No engine objects, arrays or adapters are allocated."""
    def __init__(self, payload, limits, adapters):
        self.limits, self.adapters = limits, adapters
        self.work = _Work(limits)
        self.matches, self.matching = {}, set()
        self.atom_count = 0
        _require(type(payload) is dict and set(payload) == {'roots', 'nodes'}, 'Invalid graph payload')
        self.nodes = payload['nodes']
        _require(type(self.nodes) is list, 'Expected node array')
        if len(self.nodes) > limits.max_nodes:
            raise SnapshotLimitError('Graph node limit exceeded')
        roots = payload['roots']
        _require(type(roots) is dict and set(roots) == {'game', 'episode', 'components'}, 'Invalid roots')
        self.names = set()
        self.array_bytes = 0
        for index, node in enumerate(self.nodes):
            _require(type(node) is dict and type(node.get('id')) is int and node['id'] == index,
                     'Duplicate, missing or out-of-order node ID')
            _require(type(node.get('type')) is str and node['type'] in
                     (*_CLASSES, *_CONTAINERS, 'array', 'rng', 'dice'), 'Unknown node type or procedure')
        self.topology_bytes = 4096 + 512 * len(self.nodes) + 256 * len(roots)
        if self.topology_bytes > limits.max_bytes:
            raise SnapshotLimitError('Topology workspace byte limit exceeded')
        self.edges = [[] for _ in self.nodes]
        self.edge_count = 0
        for index, node in enumerate(self.nodes):
            self.node(index, node)
        for value in roots.values():
            self.atom(value)
        self.expect(roots['game'], {'Game'})
        self.expect(roots['episode'], {'dict', 'null'})
        self.expect(roots['components'], {'dict'})
        self.topology = _topology(self.nodes, roots, self.edges, self.edge_count,
                                  self.atom_count - len(roots), self.topology_bytes, self.work)
        self.key_reservation = _GraphKeys(self.nodes, self.work).validate()
        for node in self.nodes:
            if node['type'] in _CLASSES:
                self.fields(node['type'], self.attrs(node))
        if roots['episode'] is not None:
            _require(self.match(roots['episode'], schema.record(**schema.EPISODE)), 'Invalid episode field domain')
        self.structure(roots)
        # The retained adjacency is needed only by component validation. It no
        # longer overlaps canonical S+T; all SCC scratch was already released.
        del self.edges
        # Prepay visits for decoding and private SIM-02 copies before any
        # materialization; expanded immutable key work was reserved separately.
        self.work.spend(8 * self.atom_count)

    def kind(self, atom):
        if type(atom) is dict:
            if 'ref' in atom:
                return self.nodes[atom['ref']]['type']
            return 'enum/' + atom['enum'][0] if 'enum' in atom else 'bytes'
        return {type(None): 'null', bool: 'bool', int: 'int', float: 'float', str: 'str'}[type(atom)]

    def expect(self, atom, kinds):
        _require(self.kind(atom) in kinds, 'Invalid field/reference type; expected ' + ','.join(sorted(kinds)))

    def atom(self, atom, owner=None):
        self.atom_count += 1
        self.work.spend()
        cls = type(atom)
        if cls in (type(None), bool, int, float, str):
            _require(cls is not float or math.isfinite(atom), 'Non-finite atom')
            return
        _require(cls is dict and len(atom) == 1, 'Expected primitive, enum, bytes or reference')
        if 'ref' in atom:
            ref = atom['ref']
            _require(type(ref) is int and 0 <= ref < len(self.nodes), 'Dangling or invalid reference')
            if owner is not None:
                self.topology_bytes += 128
                if self.topology_bytes > self.limits.max_bytes:
                    raise SnapshotLimitError('Topology workspace byte limit exceeded')
                self.edges[owner].append(ref)
                self.edge_count += 1
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

    def match(self, atom, spec):
        """Evaluate trusted field data, memoizing shared references per contract."""
        self.work.spend()
        key = (atom['ref'], id(spec)) if type(atom) is dict and 'ref' in atom else None
        if key in self.matches:
            return self.matches[key]
        if key in self.matching:
            return False  # Recursive JSON data cannot contain cycles.
        if key is not None:
            self.matching.add(key)
        result = self.domain(atom, spec)
        if key is not None:
            self.matching.remove(key)
            self.matches[key] = result
        return result

    def domain(self, atom, spec):
        kind = self.kind(atom)
        if type(spec) is str:
            if spec in ('graph', 'component-data'):
                return True  # Closed graph grammar / component_data traversal below.
            if spec == 'procedure':
                return kind.startswith('procedure/')
            if spec == 'number':
                return type(atom) in (int, float)
            if spec == 'json':
                if kind in ('null', 'bool', 'int', 'float', 'str'):
                    return True
                if kind in ('list', 'tuple'):
                    return all(self.match(v, spec) for v in self.items(atom))
                if kind == 'dict':
                    return all(type(k) is str and self.match(v, spec) for k, v in self.items(atom))
                return False
            return kind == spec
        op, *args = spec
        if op == 'or':
            return any(self.match(atom, sub) for sub in args)
        if op in ('integer', 'number'):
            return (type(atom) in ((int,) if op == 'integer' else (int, float)) and
                    (args[0] is None or atom >= args[0]) and (args[1] is None or atom <= args[1]))
        if op == 'literal':
            return any(type(atom) is type(value) and atom == value for value in args)
        if op == 'text':
            return type(atom) is str and re.fullmatch(args[0], atom) is not None
        if op == 'enum-name':
            return type(atom) is str and atom in _ENUMS[args[0]].__members__
        if op == 'suffix':
            return (kind in _SEQUENCES and bool(self.items(atom)) and
                    all(self.match(v, args[0]) for v in self.items(atom)[:-1]) and
                    self.match(self.items(atom)[-1], args[1]))
        if op == 'items':
            return kind in args[0].split() and all(self.match(v, args[1]) for v in self.items(atom))
        if op == 'map':
            return kind in _MAPPINGS and all(self.match(k, args[0]) and self.match(v, args[1])
                                           for k, v in self.items(atom))
        if op == 'record':
            if kind != 'dict':
                return False
            pairs = self.items(atom)
            return (len(pairs) == len(args[0]) and all(type(k) is str and k in args[0] and
                    self.match(v, args[0][k]) for k, v in pairs))
        if op == 'product':
            return kind == 'tuple' and len(self.items(atom)) == len(args) and all(
                self.match(v, sub) for v, sub in zip(self.items(atom), args))
        if op == 'empty':
            return kind in args[0].split() and not self.items(atom)
        if op == 'array':
            return (kind == 'array' and self.nodes[atom['ref']]['dtype'][1] == args[0] and
                    len(self.nodes[atom['ref']]['shape']) == args[1] and
                    all(self.match(v, args[2]) for v in self.items(atom)))
        if op == 'matrix':
            if kind == 'array':
                node = self.nodes[atom['ref']]
                return (node['dtype'][1] == 'U' and len(node['shape']) == 2 and all(node['shape']) and
                        all(type(v) is str and v in args[0] and len(v) == 1 for v in node['items']))
            if kind not in _SEQUENCES or not self.items(atom):
                return False
            rows = self.items(atom)
            return (all(self.kind(row) in _SEQUENCES or self.kind(row) == 'array' and
                        self.nodes[row['ref']]['dtype'][1] == 'U' and len(self.nodes[row['ref']]['shape']) == 1 for row in rows) and
                    len(self.items(rows[0])) > 0 and all(len(self.items(row)) == len(self.items(rows[0])) and
                    all(type(v) is str and len(v) == 1 and v in args[0] for v in self.items(row)) for row in rows))
        raise RuntimeError('Unknown trusted snapshot constraint: ' + op)

    def fields(self, tag, attrs):
        contract = CODEC_SCHEMA[tag]
        lazy = schema.LAZY.get(tag, {})
        _require(set(contract) - set(lazy) <= set(attrs), 'Missing required codec fields: ' + tag)
        for name, phase in lazy.items():
            if phase == 'started':
                _require((name in attrs) == (attrs['started'] is True), 'Invalid lazy field phase: ' + tag + '.' + name)
            elif phase == 'loner-declined':
                declined = (attrs['done'] is True and attrs['success'] is False and
                            self.kind(attrs['reroll']) == 'procedure/Reroll' and
                            not self.object_fields(attrs['reroll']).get('use_reroll'))
                _require((name in attrs) == declined, 'Invalid lazy field phase: ' + tag + '.' + name)
        for name, value in attrs.items():
            spec = contract[name]
            if spec == ('action-targets',):
                self.expect(attrs['action_type'], {'enum/ActionType'})
                spec = schema.ACTION_TARGETS.get(attrs['action_type']['enum'][1], schema.EMPTY_TARGETS)
            _require(self.match(value, spec), 'Invalid field domain: ' + tag + '.' + name)
            if name == '_ignored_keys':
                _require(all(v in contract for v in self.items(value)), 'Invalid reversible ignored field')
        if tag == 'model/ActionChoice' and self.items(attrs['block_dice']):
            _require(attrs['action_type'] == {'enum': ['ActionType', 'BLOCK']},
                     'Nonempty block_dice requires BLOCK')
        if tag == 'ComponentState':
            self.names.add(attrs['adapter'])

    def items(self, atom):
        return self.nodes[atom['ref']]['items']

    def object_fields(self, atom):
        return self.attrs(self.nodes[atom['ref']])

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


class _CanonicalGraph:
    """Exact finite labeling of the projected, typed incidence graph.

    Vertices are wire-object views or temporary mapping/identity/root records.
    Scalars stay in exact byte colors; edges are shallow labeled references.
    Individualization enumerates every unresolved candidate, without heuristic
    pruning. Only the least *complete* encoding is returned, never a partial one.
    """
    def __init__(self, payload, scope, work):
        self.work = work
        self.nodes = payload['nodes']
        if len(self.nodes) > work.limits.max_nodes:
            raise SnapshotLimitError('Graph node limit exceeded')
        self.colors, self.outgoing, self.incoming = [], [], []
        self.storage, self.scratch = work.retained_bytes, 0
        self.views, self.pending, self.labels, self.literals, self.prefixes = {}, [], {}, {}, {}
        roots = payload['roots']
        def attrs(atom):
            pairs = self.nodes[atom['ref']]['fields']
            self.work.spend(1 + len(pairs))
            return dict(pairs)
        game = attrs(roots['game'])
        state = attrs(game['state'])
        self.identities = {}
        for side in ('home', 'away'):
            team = attrs(state[side + '_team'])
            self.identities[team['team_id']] = ('team', side)
            for slot, player in enumerate(self.nodes[team['players']['ref']]['items']):
                self.identities[attrs(player)['player_id']] = ('player', side, slot)
            self.identities[attrs(game[side + '_agent'])['agent_id']] = ('agent', side)
        self.episode = roots['episode']
        root = self.vertex(('roots', scope))
        for name, value in roots.items():
            self.atom(root, ('root', name), value, False)
        while self.pending:
            index, opaque, owner = self.pending.pop()
            node = self.nodes[index]
            tag = node['type']
            if 'fields' in node:
                ignored = {'Game': {'game_id', 'start_time', 'end_time', 'last_request_time', 'last_action_time'},
                           'model/Configuration': {'name', 'arena'},
                           'data/TimelineContext': {'episode_id', 'branch_id'}, 'Clock': {'started_at'}}
                for name, value in node['fields']:
                    if name not in ignored.get(tag, ()):
                        self.atom(owner, ('field', name), value, opaque or tag == 'ComponentState' and name == 'state')
            elif 'items' in node:
                if tag in _MAPPINGS:
                    for key, value in node['items']:
                        if {'ref': index} == self.episode and key == '_manifest':
                            continue
                        entry = self.vertex(('entry',))
                        self.edge(owner, ('entry',), entry)
                        self.atom(entry, ('key',), key, opaque)
                        self.atom(entry, ('value',), value, opaque)
                else:
                    for position, value in enumerate(node['items']):
                        self.atom(owner, ('member',) if tag in _SETS else ('item', position), value, opaque)
                if 'trajectory' in node:
                    self.atom(owner, ('trajectory',), node['trajectory'], opaque)
                if tag == 'array':
                    self.literal(owner, ('array',), self.data([node['dtype'], node['shape']]))
            elif tag == 'dice':
                self.atom(owner, ('rng',), node['rng'], opaque)
                self.literal(owner, ('dice',), self.data([node['queues'], node['strict']]))
            else:  # MT19937's scalar arrays/index/cache are ordered metadata.
                self.literal(owner, ('rng-state',), self.data([
                    node['algorithm'], node['keys'], node['position'], node['has_gauss'], node['cached_gaussian']]))
        # Finishing colors discards literal/field input order, preserving pairs
        # and multiplicity. Graph edges retain incoming and outgoing incidence.
        for index, (prefix, literals) in enumerate(self.colors):
            self.work.spend(1 + len(literals))
            groups = {}
            for label, value in literals:
                groups.setdefault(label, []).append(value)
            pairs = []
            for label in self.byte_order(groups):
                for value in self.byte_order(groups[label]):
                    pairs.append((label, value))
            size = len(prefix) + sum(16 + len(a) + len(b) for a, b in pairs) + 8
            self.space(256 * len(literals) + 3 * size)
            self.allocate(2 * size)
            self.colors[index] = self.pack([prefix] + [self.pack(pair) for pair in pairs])

    def allocate(self, size):
        """Account auxiliary record/buffer storage before allocating it."""
        self.work.spend((size + 63) // 64)
        self.storage += size
        self.space(0)

    def space(self, scratch):
        # S + max(T) is a monotone reservation, not a discovery-order peak.
        # A later storage allocation still includes an earlier scratch maximum.
        self.scratch = max(self.scratch, scratch)
        if self.storage + self.scratch > self.work.limits.max_bytes:
            raise SnapshotLimitError('Canonical graph workspace byte limit exceeded')

    def data(self, value):
        # Variable-sized scalar processing is prepaid in 64-byte work units.
        # The estimate includes worst-case JSON escaping, before encoding.
        pending, size = [value], 0
        while pending:
            item = pending.pop()
            self.work.spend()
            if type(item) is str:
                size += 12 * len(item) + 2
            elif type(item) is int:
                size += item.bit_length() // 3 + 3
            elif type(item) is dict:
                self.work.spend(2 * len(item))
                pending.extend(item.keys())
                pending.extend(item.values())
                size += 2 + 2 * len(item)
            elif type(item) in (list, tuple):
                self.work.spend(len(item))
                pending.extend(item)
                size += 2 + len(item)
            else:
                size += 32
        self.work.spend((size + 63) // 64)
        self.space(size)
        return _json(value)

    @staticmethod
    def pack(parts):
        return b''.join(len(part).to_bytes(8, 'big') + part for part in parts)

    def ordered(self, values, byte_size=0):
        # Full mergesort allowance, independent of existing order or ties.
        # Signatures are shallow tuples of integer pairs, never expanded DAGs.
        n = len(values)
        levels = (n - 1).bit_length() if n else 0
        self.work.spend(n + n * levels + ((byte_size + 63) // 64) * levels)
        return sorted(values)

    def byte_order(self, values):
        # Length is an exact deterministic signature component. Only equal-size
        # buffers need byte comparisons; large scalar colors do not incur the
        # comparison budget of unrelated short colors.
        self.work.spend(len(values))
        groups = {}
        for value in values:
            groups.setdefault(len(value), []).append(value)
        result = []
        for size in self.ordered(list(groups)):
            group = groups[size]
            result.extend(self.ordered(group, size * len(group)) if len(group) > 1 else group)
        return result

    def label(self, value):
        self.work.spend()
        if value not in self.labels:
            encoded = self.data(value)
            self.allocate(64 + len(encoded))
            self.labels[value] = encoded
        return self.labels[value]

    def vertex(self, prefix):
        self.work.spend()
        self.allocate(192)
        index = len(self.colors)
        if prefix not in self.prefixes:
            self.prefixes[prefix] = self.data(prefix)
        self.colors.append((self.prefixes[prefix], []))
        self.outgoing.append([])
        self.incoming.append([])
        return index

    def edge(self, source, label, target):
        self.work.spend()
        encoded = self.label(label)
        self.allocate(128)
        self.outgoing[source].append((encoded, target))
        self.incoming[target].append((encoded, source))

    def literal(self, owner, label, encoded):
        self.work.spend()
        self.allocate(64 + len(encoded))
        self.colors[owner][1].append((self.label(label), encoded))

    def atom(self, owner, label, atom, opaque):
        self.work.spend()
        if type(atom) is not dict or 'ref' not in atom:
            # Membership and literal memo lookup themselves hash the scalar;
            # charge its size before either operation, even on cache hits.
            scalar = atom.get('bytes', '') if type(atom) is dict else atom
            size = len(scalar) if type(scalar) is str else scalar.bit_length() // 8 if type(scalar) is int else 0
            self.work.spend((size + 63) // 64)
            if type(atom) is str and not opaque and atom in self.identities:
                token = ('entity', *self.identities[atom])
            elif type(atom) is dict:
                token = ('enum', *atom['enum']) if 'enum' in atom else ('bytes', atom['bytes'])
            else:
                token = ('literal', type(atom).__name__, atom.hex() if type(atom) is float else atom)
            if token not in self.literals:
                self.literals[token] = self.data(token)
            self.literal(owner, label, self.literals[token])
            return
        index = atom['ref']
        key = (index, opaque)
        if key not in self.views:
            target = self.vertex(('object', self.nodes[index]['type'], opaque))
            self.views[key] = target
            self.pending.append((index, opaque, target))
            other = self.views.get((index, not opaque))
            if other is not None:
                # Both context projections retain a common identity. Which view
                # was discovered first contributes neither color nor edge label.
                identity = self.vertex(('identity',))
                self.edge(target, ('identity',), identity)
                self.edge(other, ('identity',), identity)
        self.edge(owner, label, self.views[key])

    def refine(self, partition, scratch):
        vertices = len(self.colors)
        # Colors, new partition, signatures and sort buffers fit this charged
        # workspace; no ancestor candidates or candidate encodings are copied.
        for _ in range(vertices):
            self.work.spend(1 + vertices)
            active = [v for members in partition if len(members) > 1 for v in members]
            active_edges = sum(len(self.outgoing[v]) + len(self.incoming[v]) for v in active)
            workspace = 32 * vertices + 64 * len(active) + 64 * active_edges
            self.space(scratch + workspace)
            self.work.spend((workspace + 63) // 64)
            colors = [0] * vertices
            for color, members in enumerate(partition):
                for vertex in members:
                    colors[vertex] = color
            refined = []
            for members in partition:
                if len(members) == 1:
                    refined.append(members)
                    continue  # A singleton cannot split; this is exact.
                signatures = []
                widths = 0
                for vertex in members:
                    outgoing, incoming = self.outgoing[vertex], self.incoming[vertex]
                    self.work.spend(1 + len(outgoing) + len(incoming))
                    a = self.ordered([(label, colors[target]) for label, target in outgoing], 16 * len(outgoing))
                    b = self.ordered([(label, colors[source]) for label, source in incoming], 16 * len(incoming))
                    signature = (tuple(a), tuple(b))
                    signatures.append((signature, vertex))
                    widths += 16 * (len(a) + len(b)) + 16
                # Sort exact signatures only. Vertex IDs are never a tie rule.
                self.work.spend(len(members) * len(members).bit_length() +
                                ((widths + 63) // 64) * len(members).bit_length())
                signatures.sort(key=lambda pair: pair[0])
                previous = None
                for signature, vertex in signatures:
                    if signature != previous:
                        refined.append([])
                        previous = signature
                    refined[-1].append(vertex)
            if len(refined) == len(partition):
                return refined
            partition = refined
        raise SnapshotLimitError('Canonical refinement depth limit exceeded')

    def normal_form(self):
        vertices = len(self.colors)
        color_size = sum(map(len, self.colors))
        self.work.spend(vertices + (color_size + 63) // 64)
        self.space(64 * vertices + color_size)
        unique_colors = list(set(self.colors))
        color_palette = self.byte_order(unique_colors)
        label_palette = self.byte_order(list(set(self.labels.values())))
        color_ranks = {value: i for i, value in enumerate(color_palette)}
        label_ranks = {value: i for i, value in enumerate(label_palette)}
        initial = [[] for _ in color_palette]
        self.work.spend(vertices + len(label_palette))
        for vertex, value in enumerate(self.colors):
            initial[color_ranks[value]].append(vertex)
        # Integer edge labels speed exact refinement; their entire palette is
        # included in the final encoding, so labels cannot be erased by ranks.
        for edges in (self.outgoing, self.incoming):
            for i, row in enumerate(edges):
                self.work.spend(len(row))
                edges[i] = [(label_ranks[label], target) for label, target in row]
        prefix_size = 64 + sum(8 + len(v) for v in color_palette + label_palette)
        self.allocate(2 * prefix_size)
        prefix = self.pack([b'SIM-03-semantic-3', self.pack(color_palette), self.pack(label_palette)])
        width = max(1, (max(vertices, len(label_palette), len(color_palette)).bit_length() + 7) // 8)
        edge_count = sum(map(len, self.outgoing))
        size = len(prefix) + 1 + vertices * (width + 8) + 2 * width * edge_count
        candidate_scratch = 128 * (vertices + edge_count) + 3 * size
        partition_bytes = 64 * vertices
        best, stack, partition = None, [], initial
        while True:
            # Invariant, conservative allocation/comparison charges apply even
            # when a candidate does not improve best; traversal order cannot
            # change completion versus a work-limit outcome.
            scratch = (len(stack) + 2) * partition_bytes + candidate_scratch
            self.space(scratch)
            partition = self.refine(partition, scratch)
            ambiguous = next((i for i, members in enumerate(partition) if len(members) > 1), None)
            if ambiguous is not None:
                if len(stack) >= vertices:
                    raise SnapshotLimitError('Canonical search depth limit exceeded')
                stack.append([partition, ambiguous, 0])
            else:
                self.work.spend(1 + vertices + edge_count + (candidate_scratch + 63) // 64)
                ranks = [0] * vertices
                for rank, members in enumerate(partition):
                    ranks[members[0]] = rank
                pieces = [prefix, bytes([width])]
                for members in partition:
                    vertex = members[0]
                    pieces.append(color_ranks[self.colors[vertex]].to_bytes(width, 'big'))
                    row = self.ordered([(label, ranks[target]) for label, target in self.outgoing[vertex]],
                                       16 * len(self.outgoing[vertex]))
                    pieces.append(len(row).to_bytes(8, 'big'))
                    for label, rank in row:
                        pieces.extend((label.to_bytes(width, 'big'), rank.to_bytes(width, 'big')))
                candidate = b''.join(pieces)
                if best is None or candidate < best:
                    best = candidate
            while stack:
                parent, cell, offset = stack[-1]
                members = parent[cell]
                if offset == len(members):
                    stack.pop()
                    continue
                self.work.spend(1 + vertices + (partition_bytes + 63) // 64)
                self.space((len(stack) + 2) * partition_bytes + candidate_scratch)
                chosen = members[offset]
                stack[-1][2] += 1
                partition = parent[:cell] + [[chosen], [v for v in members if v != chosen]] + parent[cell + 1:]
                break
            else:
                return best


def _canonical_hash(payload, scope, work):
    graph = _CanonicalGraph(payload, scope, work)
    normal = graph.normal_form()
    work.spend((len(normal) + 63) // 64)
    return 'sha256:' + hashlib.sha256(normal).hexdigest()


def _semantic_hash(payload, scope, *, limits=SnapshotLimits(), work=None):
    """Hash after standalone bounded preparation; never trust a prior payload."""
    work = _Work(limits) if work is None else work
    _direct_topology(payload, work)
    _GraphKeys(payload['nodes'], work).validate()
    return _canonical_hash(payload, scope, work)


def _validate_and_hash(payload, scope, limits, adapters):
    """Privately reuse one unchanged validator result in the same operation."""
    validator = _Validator(payload, limits, adapters)
    # Preserve the old second immutable materialization reserve, but eliminate
    # the duplicate key computation and its graph scans.
    validator.work.spend(validator.key_reservation)
    semantic = _canonical_hash(payload, scope, validator.work)
    return validator, semantic


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
        graph = _Encoder(limits)
        roots = {name: graph.atom(value) for name, value in (
            ('game', snapshot._game), ('episode', snapshot._episode), ('components', snapshot._components))}
        payload = {'roots': roots, 'nodes': graph.nodes}
        validator, semantic = _validate_and_hash(payload, snapshot.scope, limits, adapters)
        memory.clone_from_snapshot(snapshot, adapters=adapters)
        document = dict(format='SnapshotFileV1', version=1, descriptor=_descriptor(snapshot._game),
                        scope=snapshot.scope, component_versions=_versions(adapters, validator.names),
                        payload=payload, provenance={} if provenance is None else provenance,
                        semantic_state_hash=semantic)
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
        validator, semantic = _validate_and_hash(document['payload'], document['scope'], limits, adapters)
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
        _require(document['semantic_state_hash'] == semantic,
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
