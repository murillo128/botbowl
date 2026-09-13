"""Short ordinary corpus: mechanism outcomes, natural games and harness controls."""
from dataclasses import replace
import json
import signal
import time

import pytest

import botbowl as bb
from tests.baseline import progress_action
from tests.interaction_corpus import CorpusFailure, deadline, fresh, generated, replay, sequence
from tests.interaction_scenarios import injury, micro, movement, negatrait, push


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('pathfinding', [False, True])
@pytest.mark.parametrize('variant', ['team', 'sure_feet', 'decline'])
def test_movement_reroll_shadowing(side, pathfinding, variant):
    movement(side=side, pathfinding=pathfinding, variant=variant)


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('scenario,variant', [
    (push, 'accept'), (push, 'decline'), (injury, 'use'), (injury, 'decline'),
    (negatrait, 'success'), (negatrait, 'failure')])
def test_compatible_pairs(side, scenario, variant):
    scenario(side=side, variant=variant)


@pytest.mark.parametrize('size,seed,pathfinding', [(1, 0, False), (3, 17, False),
                                                (11, 3, False), (3, 0, True)])
def test_natural_game_and_action_replay(size, seed, pathfinding):
    probe = generated(size=size, seed=seed, pathfinding=pathfinding)
    assert probe.game.state.game_over
    assert probe.result()['actors'] == ['away', 'home']
    restored = replay(json.loads(json.dumps(probe.reproduction())))
    assert restored.result() == probe.result()


def test_changed_order_and_synthetic_recipe_replay():
    cases = [(movement, dict(side='away', variant='sure_feet')),
             (injury, dict(side='home', variant='decline')),
             (negatrait, dict(side='away', variant='failure')),
             (generated, dict(size=1, seed=0))]
    first = [function(**options) for function, options in cases]
    second = [function(**options) for function, options in reversed(cases)]
    for left, right in zip(first, reversed(second)):
        assert left.result() == right.result()
        assert left.reproduction() == right.reproduction()
        assert replay(json.loads(json.dumps(left.reproduction()))).result() == left.result()


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('variant', ['team', 'sure_feet', 'decline'])
def test_serialized_pathfinding_recipe_replay(side, variant):
    probe = movement(side=side, variant=variant, pathfinding=True)
    record = probe.reproduction()
    saved = json.loads(json.dumps(record))
    assert replay(saved).result() == probe.result()
    assert replay(record).result() == probe.result()
    assert record == saved


@pytest.mark.parametrize('tampering', ['action', 'forced_scope', 'forced_roll', 'fixture_state', 'path_roll'])
def test_serialized_pathfinding_recipe_rejects_tampering(tampering):
    probe = movement(side='away', variant='decline', pathfinding=True)
    record = json.loads(json.dumps(probe.reproduction()))
    # A valid saved journal must pass first, so a serialization defect cannot
    # make these negative controls pass for an unrelated reason.
    assert replay(record).result() == probe.result()
    if tampering == 'action':
        record['actions'][-1]['action_type'] = 'END_TURN'
    elif tampering == 'forced_scope':
        record['forced'][0]['decision'] += 1
    elif tampering == 'forced_roll':
        record['forced'][0]['rolls']['d6'][0] = 6
    elif tampering == 'fixture_state':
        record['fixtures'][2]['state']['weather'] = 'BLIZZARD'
    else:
        paths = [path for choice in record['fixtures'][2]['state']['available_actions']
                 for path in choice['paths'] if path['rolls'] and path['rolls'][0]]
        assert paths, 'control must alter an actual pathfinding roll'
        value = paths[0]['rolls'][0][0]
        paths[0]['rolls'][0][0] = 1 if value != 1 else 6
    with pytest.raises(AssertionError, match='synthetic recipe/journal mismatch'):
        replay(record)


def test_interleaved_sequences_preserve_rng_and_all_forced_queues():
    probes = [fresh(size=1, seed=seed, case='generated', origin='natural') for seed in (0, 17)]
    streams = [sequence(probe) for probe in probes]
    pending = [0, 1]
    while pending:
        for index in pending[:]:
            other = probes[1 - index].game
            before = other.capture_rng_state()
            with other.dice.force(d3=[2], d6=[4], d8=[7], block_dice=[bb.BBDieResult.PUSH], strict=True):
                queued = other.capture_rng_state()
                try:
                    next(streams[index])
                except StopIteration:
                    pending.remove(index)
                assert other.capture_rng_state() == queued
            assert other.capture_rng_state() == before
    for probe, seed in zip(probes, (0, 17)):
        assert probe.result() == generated(size=1, seed=seed).result()


@pytest.mark.parametrize('corruption,reason', [
    ('position', 'board/position'), ('duplicate', 'board/position'),
    ('membership', 'roster team'), ('ball', 'unique carrier'),
    ('resource', 'resource'), ('actor', 'actor/options disagree')])
def test_invariants_detect_controlled_corruption(corruption, reason):
    probe, player, opponent = micro('negative-control', 'home', 0)
    game = probe.game
    probe.config['deliberate_corruption'] = corruption
    if corruption == 'position':
        player.position = game.get_square(4, 5)
    elif corruption == 'duplicate':
        game.state.pitch.board[5][4] = player
    elif corruption == 'membership':
        player.team = opponent.team
    elif corruption == 'ball':
        game.get_ball().is_carried = True
    elif corruption == 'resource':
        player.team.state.rerolls = -1
    else:
        game.state.available_actions[-1] = bb.ActionChoice(bb.ActionType.END_TURN, team=opponent.team)
    with pytest.raises(CorpusFailure, match=reason) as failure:
        probe.check()
    record = json.loads(str(failure.value))
    assert record['config']['seed'] == 0 and record['config']['origin'] == 'synthetic'
    assert record['actions'] and record['fixtures'] and record['procedures'] and record['options']


def test_nonprogress_keeps_small_replay_prefix(monkeypatch):
    probe = fresh(size=1, case='generated', origin='natural')
    probe.config['deliberate_corruption'] = 'advance returns without changing state'
    monkeypatch.setattr(probe.game, 'advance', lambda *args, **kwargs: None)
    action = progress_action(probe.game)
    with pytest.raises(CorpusFailure, match='nonprogress') as failure:
        for _ in range(probe.repeat_limit):
            probe.step(action)
    record = failure.value.reproduction
    assert len(record['actions']) == probe.repeat_limit
    assert all(item == action.to_json() for item in record['actions'])
    assert not probe.game.state.game_over


def test_deliberate_legal_activation_undo_cycle_is_bounded():
    probe, player, _ = micro('negative-control', 'home', 0)
    probe.config['deliberate_corruption'] = 'repeat legal START_MOVE/UNDO forever'
    probe.repeat_limit = 3
    probe.visits.clear()
    probe.check()
    start = len(probe.actions)
    with pytest.raises(CorpusFailure, match='nonprogress') as failure:
        for _ in range(probe.repeat_limit):
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            probe.step(bb.Action(bb.ActionType.UNDO))
    suffix = failure.value.reproduction['actions'][start:]
    assert [action['action_type'] for action in suffix] == ['START_MOVE', 'UNDO'] * 3
    assert not probe.game.state.game_over


@pytest.mark.parametrize('name,value', [('max_decisions', 0), ('max_decisions', True),
    ('repeat_limit', -1), ('repeat_limit', 1.5), ('engine_steps', -1), ('engine_steps', False),
    ('seconds', 0), ('seconds', -1), ('seconds', True), ('seconds', float('inf')),
    ('seconds', float('nan'))])
def test_invalid_limits_rejected(name, value):
    with pytest.raises(ValueError):
        replace(fresh(size=1), **{name: value})


def test_decision_limit_is_failure_with_replay():
    probe = replace(fresh(size=1), max_decisions=1)
    probe.step(progress_action(probe.game))
    with pytest.raises(CorpusFailure, match='decision limit') as failure:
        probe.step(progress_action(probe.game))
    assert len(failure.value.reproduction['actions']) == 1
    assert not probe.game.state.game_over


def test_engine_step_limit_is_failure_with_attempted_action():
    probe = replace(fresh(size=1), engine_steps=0)
    with pytest.raises(CorpusFailure, match='execution budget exhausted') as failure:
        probe.step(progress_action(probe.game))
    assert len(failure.value.reproduction['actions']) == 1
    assert not probe.game.state.game_over


def test_elapsed_time_limit_is_failure():
    probe = fresh(size=1)
    probe.started -= probe.seconds
    with pytest.raises(CorpusFailure, match='wall-time limit'):
        probe.step(progress_action(probe.game))
    assert not probe.actions and not probe.game.state.game_over


@pytest.mark.skipif(not hasattr(signal, 'setitimer'), reason='POSIX hard wall timer')
def test_hard_time_limit_interrupts_stuck_engine_and_restores_handler(monkeypatch):
    probe = replace(fresh(size=1), seconds=0.05, started=time.monotonic())
    previous = signal.getsignal(signal.SIGALRM)
    monkeypatch.setattr(probe.game, 'advance', lambda *args, **kwargs: time.sleep(5))
    with pytest.raises(CorpusFailure, match='hard wall-time limit') as failure:
        probe.step(progress_action(probe.game))
    assert len(failure.value.reproduction['actions']) == 1
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    assert not probe.game.state.game_over


def test_forced_queue_failure_cleanup_and_diagnostic():
    probe = fresh(size=1)
    before = probe.game.capture_rng_state()
    with pytest.raises(CorpusFailure, match='unconsumed forced rolls') as failure:
        with probe.dice(d6=[6]):
            pass
    assert probe.game.capture_rng_state() == before
    assert failure.value.reproduction['forced'] == [dict(decision=0, rolls=dict(d6=[6]))]


@pytest.mark.parametrize('seconds', [0, -1, True, float('inf'), float('nan')])
def test_hard_deadline_rejects_invalid_values(seconds):
    with pytest.raises(ValueError):
        with deadline(seconds):
            pytest.fail('invalid deadline entered')
