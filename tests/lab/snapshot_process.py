"""Spawned A/B acceptance worker. The only shared simulator input is the JSON file."""
from dataclasses import asdict
from enum import Enum
import json
import os
from pathlib import Path
import sys

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.randomness import capture_stream
from botbowl.lab.snapshot_io import read_snapshot, write_snapshot
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot
from botbowl.lab.timeline import Timeline
from tests.baseline import progress_action
from tests.lab.test_reproducibility import episode
from tests.lab.test_snapshots import (CounterPolicy, RandomWrapper, assert_references,
                                      boundary, executable, logical, registry)
from tests.lab.test_timeline import fresh, players, turn, until


def actions(case, game):
    if case in ('foul', 'stab-block', 'stab-blitz', 'frenzy-stakes', 'path-handoff', 'path-foul'):
        target = bb.ActionType.HANDOFF if case == 'path-handoff' else bb.ActionType.FOUL if 'foul' in case else bb.ActionType.STAB
        return [lambda g: bb.Action(target, position=next(c for c in g.state.available_actions
                                                          if c.action_type == target).positions[0]), progress_action]
    if case in ('reroll', 'route'):
        return [lambda g: bb.Action(bb.ActionType.USE_REROLL),
                lambda g: bb.Action(bb.ActionType.END_PLAYER_TURN)]
    if case == 'interception':
        return [lambda g: bb.Action(bb.ActionType.SELECT_PLAYER, player=g.get_procedure().interceptors[0]),
                progress_action]
    if case == 'apothecary':
        return [lambda g: bb.Action(bb.ActionType.USE_APOTHECARY),
                lambda g: bb.Action(bb.ActionType.SELECT_SECOND_ROLL)]
    if case == 'terminal':
        return []
    return [progress_action, progress_action]


def create(case, size, fm, side):
    if case in ('foul', 'stab-block', 'stab-blitz', 'frenzy-stakes', 'path-handoff', 'path-foul'):
        return armor_boundary(case, fm, side)
    if case in ('reroll', 'push', 'interception', 'apothecary'):
        return boundary(case, fm, side)[0]
    if case == 'episode':
        policy = CounterPolicy()
        context = episode(size=size, max_decisions=3,
                          policies={'home': policy, 'away': RandomWrapper(policy)})
        logical(context.game)
        Timeline(context.game)
        for rng in context._streams.values():
            rng.normal()
        return context
    if case == 'route':
        game = logical(turn(pathfinding=True, rounds=2))
        player, _ = players(game, [(3, 3)], [(4, 3)], ball=(3, 3))
        player.extra_skills = []
        player.team.state.rerolls = 1
        if fm:
            game.enable_forward_model()
        game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
        game.dice.fix(bb.D6, 1, 6, 6, 6)
        game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 5)))
        assert type(game.get_procedure()) is proc.Reroll
        return game
    game = logical(fresh(size))
    if fm:
        game.enable_forward_model()
    if case == 'setup':
        until(game, lambda g: isinstance(g.get_procedure(), proc.Setup) and
              g.active_team is getattr(g.state, side + '_team'))
    elif case == 'terminal':
        until(game, lambda g: g.state.game_over)
    game.rng.normal()
    return game


def trace(subject, case, scope):
    game = subject.game if case == 'episode' else subject
    assert_references(game)
    before = executable(game)
    events, checkpoints = [], []
    if case == 'episode':
        for _ in range(3):
            if scope == 'episode':
                result = subject.act()
            else:
                result = subject.step({'action_type': subject.decision_control()['choices'][0]['action_type']})
            events.append(asdict(result))
            checkpoints.append(executable(game))
        extra = {'streams': {key: capture_stream(value) for key, value in subject._streams.items()},
                 'decisions': subject.decisions, 'truncated': subject.truncated}
        if scope == 'episode':
            extra['count'] = subject._policies['home'].count
            extra['wrapper_rng'] = capture_stream(subject._policies['away'].rng)
            assert subject._policies['home'] is subject._policies['away'].policy
    else:
        for action in actions(case, game):
            result = game.advance(action(game))
            events.append([event.to_json() for event in result.events])
            checkpoints.append(executable(game))
        extra = None
    assert_references(game)
    paths = [[(tuple((s.x, s.y) for s in p.steps), p.rolls, p.prob,
               p.block_dice, p.handoff_roll, p.foul_roll) for p in choice.paths]
             for choice in game.state.available_actions]
    return {'before': before, 'checkpoints': checkpoints, 'events': events,
            'after': executable(game), 'paths': paths, 'episode': extra}


def main():
    if sys.argv[1] == 'resave':
        directory = Path(sys.argv[2])
        clone = clone_from_snapshot(read_snapshot(directory / 'snapshot.json'))
        envelope = write_snapshot(directory / 'resaved.json', capture_snapshot(clone))
        (directory / 'resave-evidence.json').write_text(json.dumps({
            'pid': os.getpid(), 'hash_seed': os.environ.get('PYTHONHASHSEED'),
            'semantic_state_hash': envelope.semantic_state_hash}), encoding='utf-8')
        return
    mode, directory, case, size, fm, side, scope = sys.argv[1:]
    directory = Path(directory)
    codecs = registry()
    if mode == 'A':
        subject = create(case, int(size), fm == '1', side)
        saved = capture_snapshot(subject, scope=scope, adapters=codecs)
        envelope = write_snapshot(directory / 'snapshot.json', saved, adapters=codecs)
        if case == 'episode' and scope == 'engine':
            subject._policies = {}
        evidence = {'pid': os.getpid(), 'nodes': len(envelope.payload['nodes']),
                    'procedure_types': sorted({node['type'] for node in envelope.payload['nodes']
                                               if node['type'].startswith('procedure/')})}
    else:
        # A fresh interpreter never constructs the source fixture or calls init.
        def forbidden(*args, **kwargs):
            raise AssertionError('Game.init was called while reading a snapshot')
        bb.Game.init = forbidden
        subject = clone_from_snapshot(read_snapshot(directory / 'snapshot.json', adapters=codecs), adapters=codecs)
        evidence = {'pid': os.getpid()}
    result = trace(subject, case, scope)
    def scalar(value):
        if isinstance(value, Enum):
            return {'enum': [type(value).__name__, value.name]}
        raise TypeError(type(value).__name__)
    (directory / (mode + '-trace.json')).write_text(json.dumps(result, sort_keys=True, default=scalar), encoding='utf-8')
    (directory / (mode + '-evidence.json')).write_text(json.dumps(evidence, sort_keys=True), encoding='utf-8')




# Finite design-decision fixtures. These are direct supported context graphs,
# not claims that every identity-key topology arises from ordinary gameplay.
def armor_boundary(case, fm=False, side='home'):
    game = logical(turn(size=3, rounds=2, pathfinding=case.startswith('path-')))
    until(game, lambda g: type(g.get_procedure()) is proc.Turn and
          g.active_team is getattr(g.state, side + '_team'))
    own = [(3, 3), (4, 3)] if case == 'path-handoff' else [(3, 3)]
    placed = players(game, own, [(6, 5)] if case == 'path-handoff' else [(4, 3)], ball=(3, 3))
    attacker, defender = placed[0], placed[-1]
    for team in game.state.teams:
        team.state.rerolls = 0
        team.state.apothecaries = 0
    if 'foul' in case:
        defender.state.up = False
        start = bb.ActionType.START_FOUL
    elif case == 'path-handoff':
        start = bb.ActionType.START_HANDOFF
    else:
        attacker.extra_skills.append(bb.Skill.STAB)
        start = bb.ActionType.START_BLITZ if case == 'stab-blitz' else bb.ActionType.START_BLOCK
    if case == 'frenzy-stakes':
        attacker.extra_skills.extend((bb.Skill.FRENZY, bb.Skill.STAKES))
        defender.team.race = 'Undead'
        role = next(r for r in game.ruleset.races if r.name == 'Undead').roles[0]
        for player in defender.team.players:
            player.role = role
    if fm:
        game.enable_forward_model()
    game.advance(bb.Action(start, player=attacker))
    if case == 'frenzy-stakes':
        game.dice.fix(bb.BBDie, *([bb.BBDieResult.PUSH] * 6))
        game.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position))
        game.advance(bb.Action(bb.ActionType.SELECT_PUSH))
        game.advance(bb.Action(bb.ActionType.PUSH, position=bb.Square(5, 3)))
        assert type(game.get_procedure()) is proc.Frenzy
    target = bb.ActionType.HANDOFF if case == 'path-handoff' else bb.ActionType.FOUL if 'foul' in case else bb.ActionType.STAB
    choice = next(c for c in game.state.available_actions if c.action_type == target)
    assert choice.rolls
    if case.startswith('path-'):
        assert all(type(v) is int for v in choice.rolls) and choice.paths
    game.dice.fix(bb.D6, *([6] * 12 if case == 'path-handoff' else [1, 2] * 6))
    return game


def identity_graph(family, reverse=False):
    from botbowl.core.forward_model import ReversibleSet
    game = logical(fresh())
    count = 3 if family in ('cycle3', 'symmetric3') else 10 if family == 'symmetric10' else 2
    keys = []
    for _ in range(count):
        keys.append(proc.Procedure(game))
        game.state.stack.pop()
    shared = ['shared']
    values = [{'equal': 7} for _ in keys]
    if family == 'primitive':
        values = ['first', 'second']
    elif family.startswith('symmetric'):
        values = [7] * count
    order = list(reversed(range(count))) if reverse else list(range(count))
    mapping = {keys[i]: values[i] for i in order}
    context = {'map': mapping}
    if family == 'anchored':
        context.update(anchors=keys, values=values)
    elif family.startswith('cycle'):
        for i, key in enumerate(keys):
            key.context = {'mapping': mapping, 'self': key, 'next': keys[(i + 1) % count], 'shared': shared}
            values[i].update(owner=key, shared=shared)
        context.update(anchors=keys, shared=shared)
    elif family in ('set', 'rset', 'frozenset'):
        cls = {'set': set, 'rset': ReversibleSet, 'frozenset': frozenset}[family]
        context.update(anchors=keys, members=cls(keys[i] for i in order), shared=shared,
                       composites={('ordered', frozenset(keys)): shared, (keys[0], keys[1]): shared},
                       key_values={keys[i]: keys[i] for i in order})
    game.get_procedure().context = context
    return game


def identity_receipt(game, family):
    context = game.get_procedure().context
    mapping = context['map']
    keys = list(mapping)
    count = 3 if family in ('cycle3', 'symmetric3') else 2
    assert len(keys) == count and len({id(k) for k in keys}) == count
    assert all(type(k) is proc.Procedure and k.game is game for k in keys)
    receipt = {'family': family, 'cardinality': count}
    if family == 'primitive':
        assert sorted(mapping.values()) == ['first', 'second']
        assert all(k.context is None for k in keys)
        receipt['values'] = sorted(mapping.values())
    elif family == 'mutable':
        assert all(v == {'equal': 7} for v in mapping.values())
        assert len({id(v) for v in mapping.values()}) == count
        receipt['distinct_equal_values'] = count
    elif family.startswith('symmetric'):
        assert list(mapping.values()) == [7] * count
        assert all(k.context is None for k in keys)
        receipt['interchangeable'] = count
    else:
        anchors = context['anchors']
        assert set(anchors) == set(keys)
        if family == 'anchored':
            assert all(mapping[key] is context['values'][i] for i, key in enumerate(anchors))
            receipt['associations'] = list(range(count))
        elif family.startswith('cycle'):
            for i, key in enumerate(anchors):
                assert key.context['self'] is key
                assert key.context['next'] is anchors[(i + 1) % count]
                assert key.context['mapping'] is mapping
                assert key.context['shared'] is context['shared'] is mapping[key]['shared']
                assert mapping[key]['owner'] is key
            receipt['next'] = list(range(1, count)) + [0]
        else:
            assert set(context['members']) == set(anchors)
            assert type(context['members']).__name__ == {'rset': 'ReversibleSet'}.get(family, family)
            composites = context['composites']
            assert composites[('ordered', frozenset(anchors))] is context['shared']
            assert composites[tuple(anchors)] is context['shared']
            assert all(context['key_values'][key] is key for key in anchors)
            receipt['members'] = list(range(count))
            receipt['ordered_composite'] = list(range(count))
    return receipt


class SharedPolicy:
    def __init__(self, state):
        self.state = state

    def act(self, view, control, rng):
        return {'action_type': control['choices'][0]['action_type']}


def graph_registry():
    return registry().register('shared-data-v1', SharedPolicy, lambda p, r: p.state,
                               lambda state, r: SharedPolicy(state))


def projection_graph():
    context = episode(size=1, policies={'home': SharedPolicy(None)})
    logical(context.game)
    Timeline(context.game)
    shared = [context.game.state.home_team.team_id, {'module': 'inert.never.import'}]
    shared.append(shared)
    # Install inert shared fixture data without asking the forward-model setter
    # to recursively convert a cyclic plain list into reversible containers.
    object.__setattr__(context.game.get_procedure(), 'context', shared)
    context._policies['home'].state = shared
    return context


def graph_process(mode, directory, family, reverse=False):
    codecs = graph_registry()
    if mode == 'graph-produce':
        subject = projection_graph() if family == 'projection' else identity_graph(family, reverse)
        envelope = write_snapshot(directory / 'snapshot.json', capture_snapshot(
            subject, scope='episode' if family == 'projection' else 'engine', adapters=codecs), adapters=codecs)
    else:
        def forbidden(*args, **kwargs):
            raise AssertionError('Consumer constructed a game/source fixture')
        bb.Game.init = forbidden
        subject = clone_from_snapshot(read_snapshot(directory / 'snapshot.json', adapters=codecs), adapters=codecs)
        envelope = write_snapshot(directory / 'resaved.json', capture_snapshot(
            subject, scope='episode' if family == 'projection' else 'engine', adapters=codecs), adapters=codecs)
    if family == 'projection':
        shared = subject.game.get_procedure().context
        assert shared is subject._policies['home'].state and shared[2] is shared
        receipt = {'family': family, 'literal': shared[0], 'shared_cyclic_views': True}
    else:
        receipt = identity_receipt(subject, family)
    (directory / (mode + '-receipt.json')).write_text(json.dumps({
        'pid': os.getpid(), 'hash_seed': os.environ.get('PYTHONHASHSEED'),
        'semantic_state_hash': envelope.semantic_state_hash, 'incidence': receipt}, sort_keys=True), encoding='utf-8')


def resource_graph(family):
    if family == 'projection-scalars':
        subject = projection_graph()
        one, two = 'a' * 80000, 'b' * 160000
        subject.game.get_procedure().context.extend((one, two, one, 'é' * 40000))
        return subject
    game = identity_graph('primitive')
    if family == 'large-scalar':
        game.get_procedure().context['large'] = 'x' * 1000000
    elif family == 'scalars':
        one, two = 'a' * 200000, 'b' * 320000
        game.get_procedure().context['scalars'] = [one, two, one, 'é' * 180000]
    else:
        tail_length, prefix_length = (60, 90) if family == 'depth' else (6, 9)
        tail = ['tail']
        for _ in range(tail_length - 1):
            tail = [tail]
        prefix = tail
        for _ in range(prefix_length):
            prefix = [prefix]
        game.get_procedure().context = {'short': tail, 'long': prefix}
    return game


def resource_receipt(subject, family):
    game = subject.game if family == 'projection-scalars' else subject
    context = game.get_procedure().context
    if family in ('depth', 'short-depth'):
        prefix = 90 if family == 'depth' else 9
        cursor = context['long']
        for _ in range(prefix):
            cursor = cursor[0]
        assert cursor is context['short']
        return {'prefix': prefix, 'shared_tail': True}
    if family == 'large-scalar':
        assert context['large'] == 'x' * 1000000
        return {'large': len(context['large'])}
    if family == 'projection-scalars':
        assert subject._policies['home'].state is context and context[2] is context
        values = context[3:]
        expected = [80000, 160000, 80000, 40000]
    else:
        values = context['scalars']
        expected = [200000, 320000, 200000, 180000]
    assert list(map(len, values)) == expected and values[0] == values[2]
    assert values[-1] == 'é' * expected[-1]
    return {'scalar_lengths': expected, 'shared_projection': family == 'projection-scalars'}


def resource_process(directory, family):
    from dataclasses import replace
    from botbowl.lab import snapshot_io as io
    def forbidden(*args, **kwargs):
        raise AssertionError('Resource rejection reached decoder/adapters/private clone')
    bb.Game.init = forbidden
    codecs = graph_registry()
    document = json.loads((directory / 'snapshot.json').read_text())
    canonical_limit = 6654684 + 12000131 + 256  # Completed S + max(T) + retained topology summary.
    limits = (replace(io.SnapshotLimits(), max_depth=163) if family == 'depth' else
              replace(io.SnapshotLimits(), max_bytes=canonical_limit) if family == 'large-scalar' else
              io.SnapshotLimits())
    direct_work = io._Work(replace(limits, max_work=3000000))
    semantic = io._semantic_hash(document['payload'], document['scope'], work=direct_work)
    validator, prepared = io._validate_and_hash(
        document['payload'], document['scope'], replace(limits, max_work=3000000), codecs)
    assert semantic == prepared == document['semantic_state_hash']
    subject = clone_from_snapshot(io.read_snapshot(
        directory / 'snapshot.json', limits=limits, adapters=codecs), adapters=codecs)
    receipt = resource_receipt(subject, family)
    saved = capture_snapshot(subject, scope=document['scope'], adapters=codecs)
    envelope = io.write_snapshot(directory / 'resaved.json', saved, limits=limits, adapters=codecs)
    assert envelope.semantic_state_hash == semantic
    if family == 'depth':
        upper = replace(limits, max_depth=164)
        assert io._semantic_hash(document['payload'], document['scope'], limits=upper) == semantic
        io.read_snapshot(directory / 'snapshot.json', limits=upper, adapters=codecs)
        assert io.write_snapshot(directory / 'resaved.json', saved, limits=upper,
                                 adapters=codecs).semantic_state_hash == semantic

    # Independently measure the writer's freshly encoded payload boundary.
    encoder = io._Encoder(limits)
    roots = {name: encoder.atom(value) for name, value in (
        ('game', saved._game), ('episode', saved._episode), ('components', saved._components))}
    writer_validator, writer_semantic = io._validate_and_hash(
        {'roots': roots, 'nodes': encoder.nodes}, document['scope'], replace(limits, max_work=3000000), codecs)
    assert writer_semantic == semantic
    work_limits = {'direct': direct_work.used, 'read': validator.work.used,
                   'write': writer_validator.work.used}
    io._semantic_hash(document['payload'], document['scope'],
                      limits=replace(limits, max_work=work_limits['direct']))
    io.read_snapshot(directory / 'snapshot.json', adapters=codecs,
                     limits=replace(limits, max_work=work_limits['read']))
    io.write_snapshot(directory / 'resaved.json', saved, adapters=codecs,
                      limits=replace(limits, max_work=work_limits['write']))

    rejected = []
    low_limits = ([replace(limits, max_depth=value) for value in (128, 162)] if family == 'depth' else
                  [replace(limits, max_bytes=value) for value in (12300000, canonical_limit - 1)]
                  if family == 'large-scalar' else [])
    low_limits.extend(replace(limits, max_work=work_limits[name] - 1)
                      for name in ('direct', 'read', 'write'))
    original_decoder, original_clone = io._Decoder.__init__, io.memory.clone_from_snapshot
    prior = (directory / 'resaved.json').read_bytes()
    for index, low in enumerate(low_limits):
        io._Decoder.__init__, io.memory.clone_from_snapshot = forbidden, forbidden
        try:
            operations = (
                lambda: io._semantic_hash(document['payload'], document['scope'], limits=low),
                lambda: io.read_snapshot(directory / 'snapshot.json', limits=low, adapters=codecs),
                lambda: io.write_snapshot(directory / 'resaved.json', saved, limits=low, adapters=codecs),
            )
            if index >= len(low_limits) - 3:
                operations = (operations[index - (len(low_limits) - 3)],)
            for operation in operations:
                try:
                    operation()
                except io.SnapshotLimitError:
                    rejected.append('SnapshotLimitError')
                else:
                    raise AssertionError('Resource boundary unexpectedly accepted')
        finally:
            io._Decoder.__init__, io.memory.clone_from_snapshot = original_decoder, original_clone
    assert (directory / 'resaved.json').read_bytes() == prior
    (directory / 'resource-receipt.json').write_text(json.dumps({
        'pid': os.getpid(), 'hash_seed': os.environ.get('PYTHONHASHSEED'), 'family': family,
        'limits': limits.__dict__, 'work_limits': work_limits, 'topology': validator.topology,
        'rejections': rejected, 'semantic_state_hash': semantic, 'aliases': receipt}, sort_keys=True))


if __name__ == '__main__':
    if sys.argv[1] == 'resource-resave':
        resource_process(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1].startswith('graph-'):
        graph_process(sys.argv[1], Path(sys.argv[2]), sys.argv[3], len(sys.argv) > 4)
    else:
        main()
