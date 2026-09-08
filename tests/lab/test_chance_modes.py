"""SIM-06 event validation, finite mappings and owned branch continuations."""
from collections import Counter
from copy import deepcopy
import json

import numpy as np
import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.chance import (ChanceError, ChancePolicy, DOMAINS,
                                branch_from_snapshot, install_chance, validate_tape)
from botbowl.lab.randomness import SeedSpec, capture_stream
from botbowl.lab.rendering import render_grid
from botbowl.lab.snapshot_io import read_snapshot, write_snapshot, snapshot_hash
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot, restore_snapshot
from botbowl.lab.timeline import Timeline
from botbowl.lab.views import grid_view
from tests.lab.test_reproducibility import episode, PREFIX


DICE = (bb.D3, bb.D6, bb.D8, bb.BBDie)


def recorded(dice=DICE):
    context = episode()
    values = [die(context.game.dice).value for die in dice]
    return context.game.dice.chance.tape(), values


def replay(tape, mode='replay'):
    context = episode(chance_policy=ChancePolicy(mode, tape=tape))
    return context, context.game.dice.chance


def test_exact_replay_uses_recorded_values_and_preserves_rng_interleaving():
    tape, values = recorded()
    context, policy = replay(tape)
    assert [die(context.game.dice).value for die in DICE] == values
    assert policy.cursor == 4
    assert policy.finish()['mode'] == 'replay'
    natural = episode()
    for die in DICE:
        die(natural.game.dice)
    assert capture_stream(natural.game.rng) == capture_stream(context.game.rng)
    # A different seed demonstrates that results really come from the tape.
    other = episode(seed=993, chance_policy=ChancePolicy('replay', tape=tape))
    assert [die(other.game.dice).value for die in DICE] == values


def test_real_episode_prefix_replays_dice_and_direct_coin_toss_rng():
    factual = episode()
    results = [factual.step(action) for action in PREFIX]
    tape = factual.game.dice.chance.tape()
    other, policy = replay(tape)
    actual = [other.step(action) for action in PREFIX]
    assert [r.events for r in actual] == [r.events for r in results]
    assert other.observe('home') == factual.observe('home')
    assert capture_stream(other.game.rng) == capture_stream(factual.game.rng)
    policy.finish()


def test_wrong_die_shifted_event_exhausted_and_leftover_fail_without_consumption():
    tape, _ = recorded((bb.D6,))
    context, policy = replay(tape)
    before = context.game.capture_rng_state()
    with pytest.raises(ChanceError, match='die/domain'):
        bb.D8(context.game.dice)
    assert context.game.capture_rng_state() == before
    with pytest.raises(ChanceError, match='suffix'):
        policy.finish()
    context.game.state.round += 1
    with pytest.raises(ChanceError, match='event diverged'):
        bb.D6(context.game.dice)
    assert context.game.capture_rng_state() == before
    context.game.state.round -= 1
    bb.D6(context.game.dice)
    before = context.game.capture_rng_state()
    with pytest.raises(ChanceError, match='exhausted'):
        bb.D6(context.game.dice)
    assert context.game.capture_rng_state() == before


@pytest.mark.parametrize('field,value', [('result', 0), ('result', 7), ('result', True),
    ('result', 1.0), ('index', 1), ('die', 'D8'), ('domain', [1, 2, 3]),
    ('domain', [True, 2, 3, 4, 5, 6]), ('mode', 'unknown'), ('rng_advance', 1)])
def test_invalid_tape_records(field, value):
    tape, _ = recorded((bb.D6,))
    tape['rolls'][0][field] = value
    with pytest.raises(ChanceError):
        validate_tape(tape)


@pytest.mark.parametrize('version', [0, 2, True, '1'])
def test_tape_version_is_strict(version):
    with pytest.raises(ChanceError):
        ChancePolicy('replay', tape={'version': version, 'rolls': []})


def test_forced_results_never_become_natural_in_replay_or_episode_results():
    tape, _ = recorded((bb.D6,))
    tape['rolls'][0]['result'] = 6
    context, policy = replay(tape, 'forced')
    rng = capture_stream(context.game.rng)
    assert bb.D6(context.game.dice).value == 6
    assert capture_stream(context.game.rng) == rng
    assert not policy.finish()['natural']
    assert not context._result().chance['natural']
    assert policy.tape()['rolls'][0]['mode'] == 'forced'
    other, replayed = replay(policy.tape())
    assert not other._result().chance['natural']
    bb.D6(other.game.dice)
    assert not replayed.finish()['natural']
    with context.game.dice.force(d6=[2]):
        with pytest.raises(ChanceError):
            bb.D6(context.game.dice)


def test_legacy_queues_record_fabrication_and_remain_branch_owned():
    context = episode()
    context.game.dice.fix(bb.D6, 1, 6)
    saved = capture_snapshot(context)
    left, right = clone_from_snapshot(saved), clone_from_snapshot(saved)
    assert bb.D6(left.game.dice).value == 1
    assert left.game.dice.pending(bb.D6) == (6,)
    assert right.game.dice.pending(bb.D6) == context.game.dice.pending(bb.D6) == (1, 6)
    assert not left._result().chance['natural']
    assert left.game.dice.chance.tape()['rolls'][0]['provenance'] == 'legacy-forced-queue'


def test_checkpoint_snapshot_and_file_restore_cursor_and_binding(tmp_path):
    tape, values = recorded((bb.D6, bb.D8))
    context, policy = replay(tape)
    bb.D6(context.game.dice)
    checkpoint = context.capture_checkpoint()
    saved = capture_snapshot(context, scope='episode')
    digest = snapshot_hash(saved)
    path = tmp_path / 'chance.json'
    write_snapshot(path, saved)
    from_file = read_snapshot(path)
    assert snapshot_hash(from_file) == digest
    for snapshot in (saved, from_file):
        left = clone_from_snapshot(snapshot)
        right = clone_from_snapshot(snapshot)
        assert left.game.dice.chance.cursor == 1
        assert bb.D8(left.game.dice).value == values[1]
        assert right.game.dice.chance.cursor == context.game.dice.chance.cursor == 1
        assert left.game.dice.chance._game is left.game
        restore_snapshot(left, snapshot)
        assert left.game.dice.chance._game is left.game
        assert bb.D8(left.game.dice).value == values[1]
    assert bb.D8(context.game.dice).value == values[1]
    context.restore_checkpoint(checkpoint)
    assert context.game.dice.chance.cursor == 1
    assert bb.D8(context.game.dice).value == values[1]
    context.game.dice.chance.finish()


def test_observation_control_render_and_context_query_do_not_consume_tape():
    tape, _ = recorded()
    context, policy = replay(tape)
    before = context.game.capture_rng_state()
    for _ in range(3):
        observation = context.observe('home')
        context.decision_control()
        render_grid(grid_view(observation))
        policy.context()
        policy.tape()
    assert context.game.capture_rng_state() == before
    encoded = json.dumps(observation)
    assert all(key not in encoded for key in ('chance', 'master_seed', 'cursor', 'MT19937'))


def gfi_boundary():
    context = episode(size=3)
    game = context.game
    Timeline(game, episode_id='gfi')
    game.state.stack.items.clear()
    team = game.state.home_team
    game.state.current_team = team
    game.state.half = 1
    game.state.weather = bb.WeatherType.NICE
    game.state.pitch.balls.append(bb.Ball(game.get_square(1, 1)))
    turn = proc.Turn(game, team, half=1, turn=1)
    turn.started = True
    player = next(p for p in team.players if p.role.name == 'Blitzer')
    game.put(player, game.get_square(2, 2))
    player.state.moves = player.get_ma()
    team.state.rerolls = 1
    game.set_available_actions()
    context.step({'action_type': 'START_MOVE', 'player_id': context._control._player(player)})
    return context


MOVE = {'action_type': 'MOVE', 'position': {'x': 3, 'y': 2}}


def matched_fixture():
    factual = gfi_boundary()
    saved = capture_snapshot(factual)
    # No selected/forced results: keep the actual GFI sample, including failure.
    factual.step(MOVE)
    tape = factual.game.dice.chance.tape()
    first = tape['rolls'][0]
    assert first['context']['matchable'] and first['die'] == 'D6'
    matches = [{'source_index': 0, 'target': deepcopy(first['context'])}]
    return factual, saved, tape, matches


def test_bounded_two_branch_matched_gfi_reuses_same_variable():
    factual, saved, tape, matches = matched_fixture()
    before = factual.game.capture_rng_state()
    policy = ChancePolicy('matched', tape=tape, matches=matches, unmatched='independent')
    left = branch_from_snapshot(saved, branch_id='left', policy=policy)
    right = branch_from_snapshot(saved, branch_id='right', policy=policy)
    left.step(MOVE)
    right.step(MOVE)
    for branch in (left, right):
        first = branch.game.dice.chance.tape()['rolls'][0]
        assert first['result'] == tape['rolls'][0]['result']
        assert first['scope'] == 'declared-gfi'
        assert branch.game.dice.chance.finish()['mode'] == 'matched'
    assert factual.game.capture_rng_state() == before
    assert not policy.tape()['rolls']


def test_matched_event_and_domain_diverge_before_reuse():
    _, saved, tape, matches = matched_fixture()
    wrong = deepcopy(matches)
    wrong[0]['target']['cause']['decision'] += 1
    policy = ChancePolicy('matched', tape=tape, matches=wrong, unmatched='independent')
    branch = branch_from_snapshot(saved, branch_id='wrong-event', policy=policy)
    rng = capture_stream(branch.game.rng)
    with pytest.raises(ChanceError, match='diverged'):
        branch.step(MOVE)
    assert not branch.game.dice.chance.tape()['rolls']
    assert capture_stream(branch.game.rng) == rng
    # Stop directly at the actual GFI producer to ask for an incompatible die.
    policy = branch.game.dice.chance
    event = policy.context()
    policy._matches[0]['target'] = event
    before = branch.game.capture_rng_state()
    with pytest.raises(ChanceError, match='die/domain'):
        bb.D8(branch.game.dice)
    assert branch.game.capture_rng_state() == before


@pytest.mark.parametrize('change', ['duplicate-source', 'duplicate-target', 'participants', 'phase', 'forced', 'domain'])
def test_reject_ambiguous_repeated_or_incompatible_matching_contract(change):
    _, _, tape, matches = matched_fixture()
    if change in ('duplicate-source', 'duplicate-target'):
        matches.append(deepcopy(matches[0]))
    elif change == 'participants':
        matches[0]['target']['participants']['player'] = 'away:0'
    elif change == 'phase':
        matches[0]['target']['phase'][0] = 2
    elif change == 'forced':
        tape['rolls'][0]['natural'] = False
        tape['rolls'][0]['mode'] = 'forced'
    elif change == 'domain':
        tape['rolls'][0].update(die='D8', domain=list(DOMAINS['D8']))
    with pytest.raises(ChanceError):
        ChancePolicy('matched', tape=tape, matches=matches)


def test_unmatched_requires_permission_and_records_fallback():
    context = episode()
    policy = install_chance(context.game, ChancePolicy('matched'))
    rng = capture_stream(context.game.rng)
    with pytest.raises(ChanceError, match='Unmatched'):
        bb.D6(context.game.dice)
    assert capture_stream(context.game.rng) == rng
    policy = install_chance(context.game, ChancePolicy('matched', unmatched='independent'))
    bb.D6(context.game.dice)
    row = policy.tape()['rolls'][0]
    assert row['provenance'] == 'independent-fallback' and row['scope'] == 'none'


def test_default_independent_branches_get_new_owned_sources_and_no_alias():
    context = gfi_boundary()
    snapshot = capture_snapshot(context)
    before = context.game.capture_rng_state()
    left = branch_from_snapshot(snapshot, branch_id='left')
    right = branch_from_snapshot(snapshot, branch_id='right')
    assert left.game.dice.chance.mode == right.game.dice.chance.mode == 'independent'
    assert capture_stream(left.game.rng) != capture_stream(right.game.rng)
    right_before = right.game.capture_rng_state()
    for _ in range(20):
        bb.D6(left.game.dice)
    assert right.game.capture_rng_state() == right_before
    assert context.game.capture_rng_state() == before
    recipe = SeedSpec(44, 'test', component_id='left')
    a = branch_from_snapshot(snapshot, branch_id='a', policy=ChancePolicy(seed=recipe))
    b = branch_from_snapshot(snapshot, branch_id='b', policy=ChancePolicy(seed=recipe))
    assert [bb.D6(a.game.dice).value for _ in range(20)] == [bb.D6(b.game.dice).value for _ in range(20)]
    assert a.game.rng is not b.game.rng


@pytest.mark.parametrize('die', DICE)
def test_finite_outcome_mapping_and_declared_distribution(die):
    class Face:
        def __init__(self, face):
            self.face = face
        def randint(self, low, high):
            assert low == 1 and high == len(DOMAINS[die.__name__]) + 1
            return self.face
    outcomes = []
    for face in range(1, len(DOMAINS[die.__name__]) + 1):
        value = die(Face(face)).value
        outcomes.append(value.name if die is bb.BBDie else value)
    assert tuple(outcomes) == DOMAINS[die.__name__]
    counts = Counter(outcomes)
    if die is bb.BBDie:
        assert counts['PUSH'] == 2 and sorted(counts.values()) == [1, 1, 1, 1, 2]
    else:
        assert all(count == 1 for count in counts.values())
    # Independent mode delegates to the unchanged sampler, including its state.
    context = episode()
    reference = np.random.RandomState(0)
    reference.set_state(context.game.rng.get_state())
    assert [die(context.game.dice).value for _ in range(40)] == [die(reference).value for _ in range(40)]
    assert capture_stream(context.game.rng) == capture_stream(reference)


def test_matched_snapshot_retains_used_events_and_rejects_reuse(tmp_path):
    _, saved, tape, matches = matched_fixture()
    branch = branch_from_snapshot(saved, branch_id='matched', policy=ChancePolicy(
        'matched', tape=tape, matches=matches, unmatched='independent'))
    branch.step(MOVE)
    snapshot = capture_snapshot(branch)
    path = tmp_path / 'matched.json'
    write_snapshot(path, snapshot)
    restored = clone_from_snapshot(read_snapshot(path))
    policy = restored.game.dice.chance
    assert policy._used == [0]
    assert policy.finish() == branch.game.dice.chance.finish()
    # Repeat the declared causal event explicitly: no second reuse is permitted.
    policy.context = lambda: deepcopy(matches[0]['target'])
    before = capture_stream(restored.game.rng)
    with pytest.raises(ChanceError, match='already consumed'):
        bb.D6(restored.game.dice)
    assert capture_stream(restored.game.rng) == before


@pytest.mark.parametrize('field,value', [('cursor', 99), ('used', [0]), ('fabricated', 'no')])
def test_file_adapter_rejects_invalid_chance_state_before_engine_allocation(tmp_path, monkeypatch, field, value):
    from botbowl.lab import snapshot_io as io
    context = episode()
    path = tmp_path / 'invalid.json'
    write_snapshot(path, capture_snapshot(context))
    document = json.loads(path.read_text())
    node = next(n for n in document['payload']['nodes'] if n['type'] == 'dice')
    state = json.loads(node['chance'])
    state[field] = value
    node['chance'] = json.dumps(state)
    document['payload_digest'] = io._digest({k: v for k, v in document.items() if k != 'payload_digest'})
    path.write_text(json.dumps(document))
    def forbidden(*args):
        pytest.fail('Decoder was reached before chance validation')
    monkeypatch.setattr(io._Decoder, '__init__', forbidden)
    with pytest.raises(io.SnapshotFileError):
        read_snapshot(path)


def test_fabricated_history_survives_independent_continuation():
    context = gfi_boundary()
    context.game.dice.fix(bb.D6, 6)
    bb.D6(context.game.dice)
    assert not context._result().chance['natural']
    branch = branch_from_snapshot(capture_snapshot(context), branch_id='new')
    assert branch._result().chance['mode'] == 'independent'
    assert not branch._result().chance['natural']


def test_episode_completion_checks_unconsumed_tape():
    tape, _ = recorded((bb.D6,))
    context = episode(max_decisions=0, chance_policy=ChancePolicy('replay', tape=tape))
    with pytest.raises(ChanceError, match='suffix'):
        context.step(PREFIX[0])


def test_matched_prefix_and_nested_replay_preserve_rng_consumption():
    _, saved, tape, matches = matched_fixture()
    matched = branch_from_snapshot(saved, branch_id='matched', policy=ChancePolicy(
        'matched', tape=tape, matches=matches, unmatched='independent',
        seed=SeedSpec(44, 'review', component_id='matched')))
    origin = capture_snapshot(matched)
    matched.step(MOVE)
    expected_rng = capture_stream(matched.game.rng)
    expected_view = matched.observe('home')
    current_tape = matched.game.dice.chance.tape()
    assert current_tape['rolls'][0]['natural']
    assert not current_tape['rolls'][0]['rng_advance']
    for _ in range(3):
        repeated = clone_from_snapshot(origin)
        install_chance(repeated.game, ChancePolicy('replay', tape=current_tape))
        repeated.step(MOVE)
        repeated.game.dice.chance.finish()
        assert repeated.observe('home') == expected_view
        assert capture_stream(repeated.game.rng) == expected_rng
        current_tape = repeated.game.dice.chance.tape()
        assert not current_tape['rolls'][0]['rng_advance']
