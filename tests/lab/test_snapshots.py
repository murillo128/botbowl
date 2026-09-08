"""Executable continuation, ownership and transactional SIM-02 boundaries."""
from dataclasses import replace
from enum import Enum
import io
import os
import pickle
import socket
import threading

import numpy as np
import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.core.forward_model import Reversible
from botbowl.lab.actions import ActionControl, StaleDecisionError
from botbowl.lab.randomness import capture_stream
from botbowl.lab.snapshots import (LogicalTime, SnapshotAdapters, SnapshotError,
                                   capture_snapshot, clone_from_snapshot, restore_snapshot)
from botbowl.lab.timeline import Timeline
from tests.baseline import progress_action
from tests.lab.test_reproducibility import episode
from tests.lab.test_timeline import fresh, turn, until, players, assert_integrity


SIZES = (1, 3, 5, 7, 11)


def logical(game):
    game.time_source = LogicalTime()
    for clock in game.state.clocks:
        elapsed = clock.get_running_time()
        clock.time_source = game.time_source
        clock._started_at = -elapsed
        clock.paused_seconds = 0
        clock.paused_at = None if clock.is_running() else 0
    return game


def executable(game):
    """Inspect continuation fields and entity references, beyond viewer JSON.

    Ignore tracing implementation/caches and wall audit stamps. Procedure cycles
    are represented by node references; all other state values are inspected.
    """
    seen = {}
    def walk(value):
        if value is game:
            return ('Game',)
        if isinstance(value, Enum):
            return (type(value).__name__, value.name)
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, bb.Square):
            return ('square', value.x, value.y)
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, (list, tuple)):
            return tuple(walk(item) for item in value)
        if isinstance(value, dict):
            return tuple((walk(key), walk(item)) for key, item in value.items())
        if isinstance(value, set):
            return tuple(sorted((walk(item) for item in value), key=repr))
        if id(value) in seen:
            return ('ref', seen[id(value)])
        seen[id(value)] = len(seen)
        if type(value).__name__ == 'Path':
            return walk((value.steps, value.rolls, value.prob, value.block_dice,
                         value.handoff_roll, value.foul_roll))
        ignored = {'_trajectory', '_ignored_keys', '__setattr__', 'paths', 'started_at',
                   'always_show_attr', 'show_if_true_attr',
                   '_started_at', 'time_source', 'paused_at', 'paused_seconds'}
        if isinstance(value, bb.Clock):
            return (walk(value.team), value.seconds, value.is_primary, value.is_running(),
                    value.get_running_time())
        return (type(value).__name__, tuple((key, walk(item)) for key, item in vars(value).items()
                                            if key not in ignored))
    state = walk(game.state)
    action = walk(game.action)
    rng = game.capture_rng_state()
    return (state, action, rng.rng_state, rng.queues, rng.strict,
            None if game.timeline is None else game.timeline.to_json())


def assert_references(game):
    seen = set()
    def walk(obj):
        if isinstance(obj, bb.Game):
            assert obj is game
            return
        if id(obj) in seen:
            return
        seen.add(id(obj))
        if isinstance(obj, proc.Procedure):
            assert obj.game is game
        if isinstance(obj, Reversible):
            assert obj._trajectory in (None, game.trajectory)
        if isinstance(obj, bb.Player):
            assert game.state.player_by_id[obj.player_id] is obj
            assert any(obj.team is team for team in game.state.teams)
        if isinstance(obj, dict):
            for key, item in obj.items():
                walk(key); walk(item)
        elif isinstance(obj, np.ndarray) and obj.dtype.kind == 'O':
            for item in obj.flat:
                walk(item)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for item in obj:
                walk(item)
        elif type(obj).__module__ in ('botbowl.core.model', 'botbowl.core.procedure', 'botbowl.core.util'):
            for key, item in vars(obj).items():
                if key != '_trajectory':
                    walk(item)
    walk(game.state)
    walk(game.action)
    assert game.square_shortcut is game.state.pitch.squares
    if game.timeline is not None:
        assert game.timeline._game is game
        game.timeline._entities._check(game)


def repeat(game, actions):
    snapshot = capture_snapshot(game)
    before = executable(game)
    for action in actions:
        game.advance(action(game))
    after = executable(game)
    restored = restore_snapshot(game, snapshot)
    assert restored is game
    assert executable(game) == before
    assert_references(game)
    for action in actions:
        game.advance(action(game))
    assert executable(game) == after
    assert_references(game)
    return snapshot


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('fm', (False, True))
@pytest.mark.parametrize('side', ('home', 'away'))
def test_before_randomness_setup_both_sides_all_sizes(size, fm, side):
    game = logical(fresh(size))
    if fm:
        game.enable_forward_model()
    game.rng.normal()  # Nonempty Gaussian cache is part of the executable state.
    repeat(game, [lambda g: bb.Action(bb.ActionType.START_GAME)])
    until(game, lambda g: isinstance(g.get_procedure(), proc.Setup)
          and g.active_team is getattr(g.state, side + '_team'))
    snapshot = repeat(game, [progress_action, progress_action])
    clone = clone_from_snapshot(snapshot)
    assert clone.trajectory.enabled == fm and clone.get_step() == snapshot.undo_origin == 0
    assert_references(clone)


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('fm', (False, True))
def test_terminal_is_captured_without_repeating_finalizers(size, fm):
    game = logical(fresh(size))
    if fm:
        game.enable_forward_model()
    until(game, lambda g: g.state.game_over)
    before = executable(game)
    snapshot = capture_snapshot(game)
    clone = clone_from_snapshot(snapshot)
    assert clone.advance().terminal
    assert executable(clone) == before
    assert clone._end_notified and clone.end_time == game.end_time
    restore_snapshot(game, snapshot)
    assert game.advance().events == () and executable(game) == before
    assert_integrity(game)


def boundary(kind, fm, side):
    game = logical(turn(size=3, rounds=2))
    until(game, lambda g: type(g.get_procedure()) is proc.Turn
          and g.active_team is getattr(g.state, side + '_team'))
    attacker, defender = players(game, [(3, 3)], [(4, 3)], ball=(3, 3))
    attacker.extra_skills = defender.extra_skills = []
    if fm:
        game.enable_forward_model()
    if kind == 'reroll':
        attacker.team.state.rerolls = 1
        game.advance(bb.Action(bb.ActionType.START_MOVE, player=attacker))
        game.dice.fix(bb.D6, 1, 6)
        game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
        assert isinstance(game.get_procedure(), proc.Reroll)
        actions = [lambda g: bb.Action(bb.ActionType.USE_REROLL),
                   lambda g: bb.Action(bb.ActionType.END_PLAYER_TURN)]
    elif kind == 'push':
        attacker.team.state.rerolls = 0
        game.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
        game.dice.fix(bb.BBDie, bb.BBDieResult.PUSH, bb.BBDieResult.PUSH, bb.BBDieResult.PUSH)
        game.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position))
        game.advance(bb.Action(bb.ActionType.SELECT_PUSH))
        assert isinstance(game.get_procedure(), proc.Push)
        actions = [progress_action, progress_action]
    elif kind == 'interception':
        # Real pass procedure, with a defender on the flight line.
        game.move(defender, bb.Square(5, 3))
        game.advance(bb.Action(bb.ActionType.START_PASS, player=attacker))
        game.dice.fix(bb.D6, 6, 6, 6, 6)
        game.advance(bb.Action(bb.ActionType.PASS, position=bb.Square(7, 3)))
        assert isinstance(game.get_procedure(), proc.Interception)
        actions = [lambda g: bb.Action(bb.ActionType.SELECT_PLAYER,
                                      player=g.get_procedure().interceptors[0]), progress_action]
    else:
        defender.team.state.apothecaries = 1
        attacker.team.state.rerolls = 0
        game.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
        game.dice.fix(bb.BBDie, bb.BBDieResult.DEFENDER_DOWN, bb.BBDieResult.DEFENDER_DOWN,
                      bb.BBDieResult.DEFENDER_DOWN)
        game.dice.fix(bb.D6, 6, 6, 6, 6, 6, 1, 2, 3)
        game.dice.fix(bb.D8, 8, 1, 2)
        game.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position))
        game.advance(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
        until(game, lambda g: isinstance(g.get_procedure(), proc.Apothecary))
        actions = [lambda g: bb.Action(bb.ActionType.USE_APOTHECARY),
                   lambda g: bb.Action(bb.ActionType.SELECT_SECOND_ROLL)]
    return game, actions


@pytest.mark.parametrize('kind', ('reroll', 'push', 'interception', 'apothecary'))
@pytest.mark.parametrize('fm', (False, True))
@pytest.mark.parametrize('side', ('home', 'away'))
def test_pending_procedure_continuations(kind, fm, side):
    game, actions = boundary(kind, fm, side)
    snapshot = repeat(game, actions)
    left, right = clone_from_snapshot(snapshot), clone_from_snapshot(snapshot)
    left.advance(actions[0](left))
    assert executable(right) == executable(clone_from_snapshot(snapshot))
    assert_references(left)
    assert_references(right)


@pytest.mark.parametrize('fm', (False, True))
def test_suspended_multistep_route_reroll_capture_clone_restore_replay(fm, monkeypatch):
    game = logical(turn(pathfinding=True, rounds=2))
    player, _ = players(game, [(3, 3)], [(4, 3)], ball=(3, 3))
    player.extra_skills = []
    player.team.state.rerolls = 1
    if fm:
        game.enable_forward_model()
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    destination = bb.Square(3, 5)
    route = tuple(game.get_procedure().paths[destination].steps)
    assert len(route) == 2 and route[-1] == destination

    # Observe real automatic route steps, including continuation after restore.
    original = proc.MoveAction.step
    automatic_steps = []
    def step(self, action):
        if action is None:
            assert self.steps and self.game._snapshot_busy
            with pytest.raises(SnapshotError, match='settled'):
                capture_snapshot(self.game)
            automatic_steps.append(self.steps[0])
        return original(self, action)
    monkeypatch.setattr(proc.MoveAction, 'step', step)
    game.rng.normal()  # Include the MT19937 Gaussian cache in exact replay.
    game.dice.fix(bb.D6, 1, 6, 6, 6)
    game.advance(bb.Action(bb.ActionType.MOVE, position=destination))

    def suspended(target):
        reroll = target.get_procedure()
        assert type(reroll) is proc.Reroll and reroll.context.reroll is reroll
        move = next(p for p in target.state.stack.items if type(p) is proc.MoveAction)
        assert tuple(move.steps) == route[1:]
        assert move.orig_action_type is bb.ActionType.MOVE
        assert move.player is reroll.context.player
        assert target._snapshot_ready and not target._snapshot_busy
        assert target.timeline._pending is None
        assert [c.action_type for c in target.state.available_actions] == [
            bb.ActionType.USE_REROLL, bb.ActionType.DONT_USE_REROLL]
        assert_references(target)

    def choices(target):
        # The executable walker covers all non-cache choice fields. Compare
        # regenerated Python/native path values explicitly as well.
        return [(choice.action_type, [
            (tuple((s.x, s.y) for s in path.steps), path.rolls, path.prob,
             path.block_dice, path.handoff_roll, path.foul_roll)
            for path in choice.paths]) for choice in target.state.available_actions]

    def finish(target):
        target.advance(bb.Action(bb.ActionType.USE_REROLL))
        move = target.get_procedure()
        assert type(move) is proc.MoveAction and move.steps is None
        assert move.player.position == destination
        assert tuple(move.player.state.squares_moved) == (bb.Square(3, 3),) + route
        assert_references(target)
        assert_integrity(target)
        at_destination = executable(target), choices(target)
        target.advance(bb.Action(bb.ActionType.END_PLAYER_TURN))
        assert_references(target)
        assert_integrity(target)
        return at_destination, executable(target), choices(target)

    suspended(game)
    saved = capture_snapshot(game)
    before = executable(game), choices(game)
    clone = clone_from_snapshot(saved)
    suspended(clone)
    assert clone.get_step() == saved.undo_origin == 0 and clone.trajectory.enabled == fm
    assert (executable(clone), choices(clone)) == before
    expected = finish(game)
    assert (executable(clone), choices(clone)) == before
    assert finish(clone) == expected
    restore_snapshot(game, saved)
    suspended(game)
    assert (executable(game), choices(game)) == before
    assert game.get_step() == 0 and game.trajectory.enabled == fm
    assert finish(game) == expected
    assert automatic_steps == [route[0], route[1], route[1], route[1]]


@pytest.mark.parametrize('fm', (False, True))
def test_divergent_branches_mutable_aliases_and_route_caches(fm):
    game = logical(turn(pathfinding=True))
    player = players(game, [(3, 3)], [(7, 3)], ball=(3, 3))[0]
    if fm:
        game.enable_forward_model()
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    original = pickle.dumps(game)
    snapshot = capture_snapshot(game)
    assert pickle.dumps(game) == original
    left = clone_from_snapshot(snapshot, branch_id='left')
    right = clone_from_snapshot(snapshot, branch_id='right')
    assert left.timeline.events == right.timeline.events == game.timeline.events
    assert left.timeline.context.branch_id == 'left'
    assert right.timeline.context.branch_id == 'right'
    assert left.get_step() == right.get_step() == 0
    assert left.get_procedure().paths is not game.get_procedure().paths
    assert all(path in left.get_procedure().paths.values()
               for choice in left.state.available_actions for path in choice.paths)
    left.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
    right.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(2, 3)))
    assert left.state.active_player.position != right.state.active_player.position
    assert game.state.active_player.position == bb.Square(3, 3)
    left.state.home_team.players[0].role.skills.append(bb.Skill.PRO)
    left.config.time_limits.turn += 1
    left.ruleset.races[0].roles[0].skills.append(bb.Skill.LONER)
    assert bb.Skill.PRO not in game.state.home_team.players[0].role.skills
    assert right.config.time_limits.turn == game.config.time_limits.turn
    assert pickle.dumps(game) == original
    for clone in (left, right):
        assert_references(clone)


def test_all_rng_frames_strict_queues_and_live_scope_restore():
    game = logical(fresh())
    game.dice.fix(bb.D6, 2)
    with game.dice.force(d3=[3], d6=[6, 1], d8=[8], block_dice=[bb.BBDieResult.PUSH], strict=True):
        with game.dice.force(d3=[1], d6=[4, 5], d8=[7], block_dice=[bb.BBDieResult.BOTH_DOWN], strict=True):
            snapshot = capture_snapshot(game)
            source = game.dice
            for die in (bb.D3, bb.D6, bb.D8, bb.BBDie):
                die(game.dice)
            restore_snapshot(game, snapshot)
            assert game.dice is source
            expected = [die(game.dice).get_value() for die in (bb.D3, bb.D6, bb.D8, bb.BBDie)]
        assert game.dice.pending(bb.D6) == (6, 1)
    assert game.dice.pending(bb.D6) == (2,)
    left, right = clone_from_snapshot(snapshot), clone_from_snapshot(snapshot)
    assert [die(left.dice).get_value() for die in (bb.D3, bb.D6, bb.D8, bb.BBDie)] == expected
    with pytest.raises(bb.ForcedRollExhausted):
        bb.D3(left.dice)
    assert right.dice.pending(bb.D6) == (4, 5)
    assert game.dice.pending(bb.D6) == (2,)


@pytest.mark.parametrize('fm', (False, True))
def test_post_snapshot_undo_redo_and_rng_timeline_checkpoint(fm):
    game, actions = boundary('reroll', fm, 'home')
    snapshot = capture_snapshot(game)
    clone = clone_from_snapshot(snapshot)
    if not fm:
        clone.enable_forward_model()
    assert clone.get_step() == 0
    checkpoint = clone.capture_checkpoint()
    before = executable(clone)
    clone.advance(actions[0](clone))
    state_after = clone.state.compare(clone.state)
    after = executable(clone)
    steps = clone.restore_checkpoint(checkpoint)
    assert executable(clone) == before
    clone.advance(actions[0](clone))
    assert executable(clone) == after
    # Legacy undo is state-only; prove executable procedure fields and positions
    # can be redone with the new trajectory, independently of RNG/timeline.
    replay_state = pickle.dumps(clone.state.to_json(ignore_clocks=True))
    steps = clone.revert(0)
    clone.forward(steps)
    assert pickle.dumps(clone.state.to_json(ignore_clocks=True)) == replay_state
    assert state_after == []
    assert_references(clone)
    with pytest.raises(ValueError):
        game.restore_checkpoint(checkpoint)


class CounterPolicy:
    def __init__(self, count=0):
        self.count = count

    def act(self, view, control, rng):
        self.count += 1
        rng.randint(100)
        return {'action_type': control['choices'][0]['action_type']}


class RandomWrapper:
    def __init__(self, policy, rng=None):
        self.policy = policy
        self.rng = np.random.RandomState(45) if rng is None else rng

    def act(self, view, control, rng):
        self.rng.normal()
        return self.policy.act(view, control, rng)


def registry():
    result = SnapshotAdapters()
    result.register('counter-v1', CounterPolicy, lambda p, r: p.count,
                    lambda state, r: CounterPolicy(state))
    result.register('wrapper-v1', RandomWrapper,
                    lambda p, r: {'policy': r.capture(p.policy), 'rng': capture_stream(p.rng)},
                    lambda state, r: RandomWrapper(r.restore(state['policy']), rng_from(state['rng'])))
    return result


def rng_from(state):
    rng = np.random.RandomState(0)
    rng.set_state(state)
    return rng


def test_episode_scope_policy_wrapper_streams_and_truncation():
    context = episode(max_decisions=3, policies={'home': CounterPolicy(), 'away': RandomWrapper(CounterPolicy())})
    logical(context.game)
    Timeline(context.game)
    context.act()
    context._streams['scenario'].normal()
    context._streams['observation'].normal()
    codecs = registry()
    snapshot = capture_snapshot(context, scope='episode', adapters=codecs)
    before = executable(context.game)
    expected = context.act(), context.act()
    streams = {k: capture_stream(v) for k, v in context._streams.items()}
    assert context.truncated
    restore_snapshot(context, snapshot, adapters=codecs)
    assert executable(context.game) == before and context.decisions == 1
    assert (context.act(), context.act()) == expected
    assert {k: capture_stream(v) for k, v in context._streams.items()} == streams
    left, right = [clone_from_snapshot(snapshot, adapters=codecs) for _ in range(2)]
    left.act()
    assert left._policies['away'].policy.count == 2
    assert right._policies['away'].policy.count == 1
    assert capture_stream(left._policies['away'].rng) != capture_stream(right._policies['away'].rng)
    assert left._control._game is left.game and right._control._game is right.game
    assert context._control._game is context.game


def test_engine_scope_omits_policies_and_retains_context_owned_streams():
    context = episode(policies={'away': CounterPolicy()})
    context._streams['policy-away'].normal()
    snapshot = capture_snapshot(context)
    clone = clone_from_snapshot(snapshot)
    assert clone._policies == {} and clone._scenario is None
    assert {k: capture_stream(v) for k, v in clone._streams.items()} == {
        k: capture_stream(v) for k, v in context._streams.items()}
    with pytest.raises(ValueError, match='No restricted policy'):
        clone.act()
    clone.step({'action_type': 'START_GAME'})
    assert context.decisions == 0


@pytest.mark.parametrize('component', (CounterPolicy(), RandomWrapper(CounterPolicy()), lambda inputs, rng: None))
def test_episode_rejects_unadapted_active_components(component):
    context = episode(**({'scenario': component} if callable(component) else {'policies': {'away': component}}))
    before = pickle.dumps(context.game)
    with pytest.raises(SnapshotError):
        capture_snapshot(context, scope='episode')
    assert pickle.dumps(context.game) == before
    if isinstance(component, RandomWrapper):
        codecs = SnapshotAdapters().register('wrapper', RandomWrapper,
            lambda p, r: r.capture(p.policy), lambda state, r: RandomWrapper(r.restore(state)))
        with pytest.raises(SnapshotError, match='capture failed'):
            capture_snapshot(context, scope='episode', adapters=codecs)


@pytest.mark.parametrize('damage', ('version', 'process', 'board', 'procedure', 'rng', 'counter', 'config'))
def test_corrupt_snapshot_rejected_atomically(damage):
    game = logical(turn())
    snapshot = capture_snapshot(game)
    game.advance(progress_action(game))
    before = pickle.dumps(game)
    identities = tuple(id(x) for x in (game.state, game.dice, game.trajectory, game.timeline))
    if damage == 'version':
        snapshot = replace(snapshot, version=99)
    elif damage == 'process':
        snapshot = replace(snapshot, _pid=os.getpid() + 1)
    elif damage == 'board':
        snapshot._game.state.pitch.board.pop()
    elif damage == 'procedure':
        snapshot._game.get_procedure().game = game
    elif damage == 'rng':
        snapshot._game.rng.randint(5)
    elif damage == 'counter':
        snapshot._game.timeline._context = replace(snapshot._game.timeline.context, decision_seq=100)
    else:
        snapshot._game.config.rounds += 1
    with pytest.raises(SnapshotError):
        restore_snapshot(game, snapshot)
    assert pickle.dumps(game) == before
    assert tuple(id(x) for x in (game.state, game.dice, game.trajectory, game.timeline)) == identities


def test_configuration_codec_and_late_adapter_failure_are_atomic():
    context = episode(policies={'home': CounterPolicy(), 'away': RandomWrapper(CounterPolicy())})
    snapshot = capture_snapshot(context, scope='episode', adapters=registry())
    context.step({'action_type': 'START_GAME'})
    before = pickle.dumps(context)
    codecs = registry()
    def fail(state, registry):
        raise ValueError('decode failure')
    codecs._by_name['wrapper-v1'] = (RandomWrapper, None, fail)
    with pytest.raises(SnapshotError, match='restore failed'):
        restore_snapshot(context, snapshot, adapters=codecs)
    assert pickle.dumps(context) == before
    context.game.config.rounds += 1
    before = pickle.dumps(context)
    with pytest.raises(SnapshotError, match='incompatible'):
        restore_snapshot(context, snapshot, adapters=registry())
    assert pickle.dumps(context) == before


@pytest.mark.parametrize('resource', (lambda: io.StringIO('data'), lambda: socket.socket(),
                                    lambda: threading.Thread(), lambda: (lambda: None),
                                    lambda: CounterPolicy()))
def test_uninventoried_resources_are_rejected_without_copy_hooks(resource):
    game = logical(fresh())
    value = resource()
    game.get_procedure().context = value
    try:
        with pytest.raises(SnapshotError, match='Unsupported object'):
            capture_snapshot(game)
    finally:
        if hasattr(value, 'close'):
            value.close()


def test_capture_rejects_inflight_failed_automatic_and_macro_boundaries(monkeypatch):
    game = logical(fresh())
    original = proc.StartGame.step
    def step(self, action):
        with pytest.raises(SnapshotError, match='settled'):
            capture_snapshot(self.game)
        return original(self, action)
    monkeypatch.setattr(proc.StartGame, 'step', step)
    game.advance(progress_action(game))
    with game.timeline.primitive('macro', 0):
        with pytest.raises(SnapshotError, match='macro'):
            capture_snapshot(game)
    with pytest.raises(bb.GameTruncatedError):
        game.advance(progress_action(game), max_steps=0)
    with pytest.raises(SnapshotError, match='settled'):
        capture_snapshot(game)


def test_logical_clock_pause_resume_and_transport_revision_ownership():
    game = logical(turn())
    game.time_source.advance(12)
    game.pause_clocks()
    game.time_source.advance(5)
    snapshot = capture_snapshot(game)
    clone = clone_from_snapshot(snapshot)
    assert clone.time_source.value == 17
    assert clone.state.clocks[0].get_running_time() == game.state.clocks[0].get_running_time()
    clone.resume_clocks()
    clone.time_source.advance(4)
    assert clone.state.clocks[0].get_running_time() == game.state.clocks[0].get_running_time() + 4
    # This consumer owns a monotonic command revision, independent of logical time.
    revision = 12
    control = ActionControl(game, game.timeline._entities)
    request = control.request(control.encode(progress_action(game)))
    control_revision = control.state_revision
    game.advance(progress_action(game))
    logical_after = game.timeline.context.decision_seq
    restore_snapshot(game, snapshot)
    revision += 1
    control.entities = game.timeline._entities
    assert control.state_revision > control_revision
    with pytest.raises(StaleDecisionError):
        control.decode(request)
    assert game.timeline.context.decision_seq < logical_after
    assert revision == 13


def test_no_persistence_and_no_silent_uninventoried_game_fields():
    game = fresh()
    snapshot = capture_snapshot(game)
    with pytest.raises(TypeError, match='process-local'):
        pickle.dumps(snapshot)
    game.transport_revision = 1
    with pytest.raises(SnapshotError, match='Uninventoried'):
        capture_snapshot(game)


def test_shared_nested_component_alias_survives_both_codecs():
    policy = CounterPolicy()
    context = episode(policies={'home': RandomWrapper(policy), 'away': RandomWrapper(policy)})
    codecs = registry()
    snapshot = capture_snapshot(context, scope='episode', adapters=codecs)
    clone = clone_from_snapshot(snapshot, adapters=codecs)
    assert clone._policies['home'].policy is clone._policies['away'].policy
    assert clone._policies['home'].policy is not policy
    clone.act()
    assert clone._policies['home'].policy.count == 1 and policy.count == 0


def test_callback_capture_and_restore_are_rejected_before_codecs():
    context = episode()
    snapshot = capture_snapshot(context)
    class Callback(CounterPolicy):
        def act(self, view, control, rng):
            for operation in (lambda: capture_snapshot(context),
                              lambda: restore_snapshot(context, snapshot)):
                with pytest.raises(SnapshotError):
                    operation()
            return super().act(view, control, rng)
    context._policies['away'] = Callback()
    context.act()
    assert context.decisions == 1


def test_arena_class_lists_become_owned_and_arena_cache_is_rebuilt():
    game = fresh()
    game.arena.to_json()
    before = pickle.dumps(game)
    snapshot = capture_snapshot(game)
    clone = clone_from_snapshot(snapshot)
    assert pickle.dumps(game) == before
    clone.arena.home_tiles.clear()
    assert game.arena.home_tiles
    assert clone.arena.json is None
    assert clone.arena.to_json() == game.arena.to_json()


def test_source_reference_defects_and_unknown_game_extensions_fail_before_capture():
    game = fresh()
    player = game.state.home_team.players[0]
    player.team = game.state.away_team
    with pytest.raises(SnapshotError, match='Foreign'):
        capture_snapshot(game)


def test_foreign_live_forced_scope_is_rejected_atomically():
    game = fresh()
    snapshot = capture_snapshot(game)
    with game.dice.force(d6=[1]):
        before = pickle.dumps(game)
        with pytest.raises(SnapshotError, match='different live'):
            restore_snapshot(game, snapshot)
        assert pickle.dumps(game) == before


def test_policy_encode_failure_wrong_type_decode_and_missing_codec_are_atomic():
    context = episode(policies={'away': CounterPolicy()})
    before = pickle.dumps(context)
    codecs = SnapshotAdapters().register('counter', CounterPolicy,
        lambda p, r: {'bad': lambda: None}, lambda state, r: CounterPolicy())
    with pytest.raises(SnapshotError, match='Unsupported'):
        capture_snapshot(context, scope='episode', adapters=codecs)
    assert pickle.dumps(context) == before
    snapshot = capture_snapshot(context, scope='episode', adapters=registry())
    bad = SnapshotAdapters().register('counter-v1', CounterPolicy,
        lambda p, r: p.count, lambda state, r: object())
    for adapters in (None, bad):
        with pytest.raises(SnapshotError):
            restore_snapshot(context, snapshot, adapters=adapters)
        assert pickle.dumps(context) == before


def test_engine_rng_alias_and_procedure_cycles_use_one_memo():
    game, _ = boundary('reroll', True, 'home')
    reroll = game.get_procedure()
    assert reroll.context.reroll is reroll
    # Context is the supported graph-extension slot; engine RNG aliases must
    # remain aliases rather than silently creating a second random source.
    reroll.context.context = (game, game.rng, game.dice)
    snapshot = capture_snapshot(game)
    clone = clone_from_snapshot(snapshot)
    copied = clone.get_procedure()
    assert copied.context.reroll is copied
    assert copied.context.context == (clone, clone.rng, clone.dice)
    restore_snapshot(game, snapshot)
    assert game.get_procedure().context.context == (game, game.rng, game.dice)


@pytest.mark.parametrize('carrier', ('array', 'scalar-array', 'frozenset', 'nested'))
def test_context_containers_rebind_live_game_aliases_and_recapture(carrier):
    game = logical(fresh())
    frozen = frozenset((game,))
    if carrier == 'frozenset':
        value = frozen
    elif carrier == 'scalar-array':
        value = np.empty((), dtype=object)
        value[()] = game
    else:
        value = np.empty((2, 2), dtype=object)
        value[0, 0] = game
        value[0, 1] = value  # Mutable-container cycle.
        value[1, 0] = frozen if carrier == 'nested' else game
        value[1, 1] = value[1, 0]
    game.get_procedure().context = {'first': value, 'again': value,
                                    'tuple': (value,), 'frozen': frozen,
                                    'key': {frozen: value}}

    def identities(target):
        context = target.get_procedure().context
        item = context['first']
        assert context['again'] is context['tuple'][0] is item
        assert next(iter(context['frozen'])) is target
        assert next(iter(context['key'])) is context['frozen']
        assert context['key'][context['frozen']] is item
        if carrier == 'frozenset':
            assert item is context['frozen']
        elif carrier == 'scalar-array':
            assert item[()] is target
        else:
            assert item[0, 0] is target and item[0, 1] is item
            assert item[1, 0] is item[1, 1]
            assert item[1, 0] is (context['frozen'] if carrier == 'nested' else target)
        assert_references(target)
        return item

    saved = capture_snapshot(game)
    clone = clone_from_snapshot(saved)
    assert identities(clone) is not identities(game)
    restore_snapshot(game, saved)
    assert identities(game) is not identities(clone)
    # A successful live restore must itself be an admissible capture source.
    recaptured = capture_snapshot(game)
    sibling = clone_from_snapshot(recaptured)
    assert identities(sibling) is not identities(game)
    restore_snapshot(game, recaptured)
    identities(game)
    identities(clone)


def test_engine_scope_drops_policy_resources_and_finalization_callbacks():
    game = logical(fresh())
    class Spy(bb.Agent):
        def __init__(self):
            super().__init__('spy', human=False)
            self.renderer = io.StringIO('caller-owned')
            self.calls = 0
        def act(self, game):
            raise AssertionError('hidden policy call')
        def end_game(self, game):
            self.calls += 1
    agent = Spy()
    game.away_agent = agent
    clone = clone_from_snapshot(capture_snapshot(game))
    assert clone.away_agent.agent_id == agent.agent_id
    assert not hasattr(clone.away_agent, 'renderer')
    until(clone, lambda g: g.state.game_over)
    assert agent.calls == 0 and not game.state.game_over
    agent.renderer.close()


def test_native_and_python_uncached_path_capture_does_not_touch_source_caches():
    game = logical(turn(pathfinding=True))
    player = players(game, [(3, 3)])[0]
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    paths = list(game.get_procedure().paths.values())
    before = [(path._steps, path._rolls) for path in paths]
    clone = clone_from_snapshot(capture_snapshot(game))
    assert [(path._steps, path._rolls) for path in paths] == before
    assert clone.get_procedure().paths
    clone.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
    assert player.position == bb.Square(3, 3)


def test_adapter_can_preserve_a_retained_supplied_stream_alias():
    context = episode()
    wrapper = RandomWrapper(CounterPolicy(), context._streams['policy-away'])
    context._policies['away'] = wrapper
    # Returning the owned RNG (data) explicitly declares its identity binding.
    codecs = SnapshotAdapters()
    codecs.register('counter-v1', CounterPolicy, lambda p, r: p.count,
                    lambda state, r: CounterPolicy(state))
    codecs.register('bound-stream-v1', RandomWrapper,
                    lambda p, r: (r.capture(p.policy), p.rng),
                    lambda state, r: RandomWrapper(r.restore(state[0]), state[1]))
    snapshot = capture_snapshot(context, scope='episode', adapters=codecs)
    clone = clone_from_snapshot(snapshot, adapters=codecs)
    assert clone._policies['away'].rng is clone._streams['policy-away']
    assert clone._streams['policy-away'] is not context._streams['policy-away']
    restore_snapshot(context, snapshot, adapters=codecs)
    assert context._policies['away'].rng is context._streams['policy-away']


def test_component_data_cannot_smuggle_a_known_engine_reference_through_the_memo():
    context = episode(policies={'away': CounterPolicy()})
    codecs = SnapshotAdapters().register('bad', CounterPolicy,
        lambda p, r: context.game, lambda state, r: CounterPolicy())
    with pytest.raises(SnapshotError, match='Unsupported object in component data'):
        capture_snapshot(context, scope='episode', adapters=codecs)
