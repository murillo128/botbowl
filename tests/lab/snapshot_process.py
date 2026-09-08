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


if __name__ == '__main__':
    main()
