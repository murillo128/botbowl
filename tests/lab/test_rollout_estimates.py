"""SIM-07 denominators, uncertainty, bounded continuations and data isolation."""
from copy import deepcopy
from dataclasses import replace
import json
import math
import statistics

import pytest

import botbowl as bb
from botbowl.lab.actions import ActionControl
from botbowl.lab.chance import ChancePolicy, branch_from_snapshot
from botbowl.lab.randomness import SeedSpec, capture_stream
from botbowl.lab.rollouts import (
    ContinuationPolicy, Horizon, SamplePlan, binary_summary,
    estimate_continuations, exact_dice_distribution, scalar_summary,
)
from botbowl.lab.session import NoProgress, SessionConfig, SimulationSession
from botbowl.lab.snapshot_io import snapshot_hash
from botbowl.lab.snapshots import capture_snapshot
from tests.lab.test_chance_modes import gfi_boundary


def first_factory(config, rng):
    return lambda observation, legal: legal.actions[0]


POLICY = ContinuationPolicy('first-legal', 'v1', {}, first_factory)


@pytest.fixture
def session():
    source = SimulationSession(SessionConfig(size=1), SeedSpec(17, 'rollout-test'))
    owned = SimulationSession.from_snapshot(source.snapshot())
    source.close()
    yield owned
    owned.close()


def estimate(session, n=3, **kwargs):
    values = dict(snapshot=session.snapshot(), first_action=session.legal_actions().actions[0],
                  continuation_policy=POLICY, horizon=Horizon('decisions', 1),
                  sample_plan=SamplePlan(tuple(range(n)), 421))
    values.update(kwargs)
    return estimate_continuations(**values)


def test_deterministic_prefix_counts_normalization_and_reproducibility(session):
    report = estimate(session)
    assert report['method'] == 'monte_carlo'
    assert report['counts'] == {'attempted': 3, 'valid': 3, 'failed': 0, 'truncated': 0}
    assert report['complete_unconditional_sample']
    assert report['summaries']['touchdown']['frequency'] == 0
    assert report['summaries']['touchdown']['wilson95'][1] > 0
    assert report['summaries']['possession_loss']['n'] == 0  # no initial carrier
    assert report['summaries']['possession_loss']['excluded'] == 3
    assert report['summaries']['score_home']['sample_variance'] == 0
    assert sum(v['frequency'] for v in report['summaries']['ball_possession']['categories']) == 1
    assert report == estimate(session)
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert report['snapshot']['descriptor']['ruleset_id'] == 'BB2016'


@pytest.mark.parametrize('n', [0, 1])
def test_empty_and_single_sample_do_not_invent_uncertainty(session, n):
    report = estimate(session, n)
    assert report['counts']['attempted'] == report['counts']['valid'] == n
    summary = report['summaries']['score_home']
    assert summary['sample_variance'] is summary['standard_error'] is None
    assert summary['mean'] == (0 if n else None)
    binary = report['summaries']['touchdown']
    assert binary['n'] == n
    if not n:
        assert binary['frequency'] is binary['wilson95'] is None
        assert not report['complete_unconditional_sample']
    else:
        assert binary['wilson95'][1] == pytest.approx(.7934506856)


@pytest.mark.parametrize('k,n,expected', [(0, 10, (0., .2775328)),
    (10, 10, (.7224672, 1.)), (4, 10, (.1681803, .6873262))])
def test_wilson_against_independent_reference_values(k, n, expected):
    summary = binary_summary([True] * k + [False] * (n - k))
    assert summary['wilson95'] == pytest.approx(expected, abs=1e-7)
    assert summary['frequency'] == k / n
    # Equivalently, each Wilson endpoint solves the inverted score test.
    z2 = 1.959963984540054 ** 2
    for bound in summary['wilson95']:
        assert (k / n - bound) ** 2 == pytest.approx(z2 * bound * (1 - bound) / n, abs=1e-14)


def test_scalar_moments_and_quantiles_against_independent_calculation():
    data = [1, 2, 3, 4, 5, 6]
    summary = scalar_summary(data, 'pips')
    assert summary['mean'] == statistics.mean(data) == 3.5
    assert summary['sample_variance'] == statistics.variance(data) == 3.5
    assert summary['standard_error'] == math.sqrt(statistics.variance(data) / 6)
    assert summary['quantiles'] == {'0.0': 1, '0.25': 2.25, '0.5': 3.5, '0.75': 4.75, '1.0': 6}
    assert summary['units'] == 'pips'


def test_exact_d6_and_block_physical_faces():
    d6 = exact_dice_distribution('D6', 2)
    assert d6['method'] == 'exact' and d6['physical_outcomes'] == 36
    assert len(d6['outcomes']) == 36
    assert sum(v['probability'] for v in d6['outcomes']) == pytest.approx(1.)
    assert sum(v['probability'] for v in d6['outcomes'] if sum(v['faces']) == 7) == pytest.approx(1 / 6)
    assert sum(sum(v['faces']) * v['probability'] for v in d6['outcomes']) == pytest.approx(7.)
    block = exact_dice_distribution('BBDie', 2)
    assert len(block['outcomes']) == 25  # PUSH occupies two physical faces
    assert sum(v['probability'] for v in block['outcomes'] if v['faces'] == ['PUSH', 'PUSH']) == pytest.approx(1 / 9)
    assert sum(v['probability'] for v in block['outcomes'] if 'ATTACKER_DOWN' in v['faces']) == pytest.approx(11 / 36)
    assert sum(v['multiplicity'] for v in block['outcomes']) == 36
    with pytest.raises(ValueError):
        exact_dice_distribution('D6', 4)


def test_policy_failure_illegal_initial_action_and_retry_are_retained(session):
    def fail(config, rng):
        def driver(observation, legal):
            raise RuntimeError('policy failed')
        return driver
    failed_policy = replace(POLICY, factory=fail)
    report = estimate(session, continuation_policy=failed_policy, horizon=Horizon('decisions', 2))
    assert report['counts'] == {'attempted': 3, 'valid': 0, 'failed': 3, 'truncated': 0}
    assert report['causes'] == {'RuntimeError': 3}
    assert not report['complete_unconditional_sample']
    assert report['summaries']['touchdown']['n'] == 0
    assert all(row['values'] is None and row['decisions'] == 1 for row in report['records'])
    retry = estimate(session, continuation_policy=failed_policy, horizon=Horizon('decisions', 2),
                     sample_plan=SamplePlan((1,), 421))
    assert retry['records'] == [report['records'][1]]
    illegal = replace(session.legal_actions().actions[0], type='END_TURN')
    invalid = estimate(session, first_action=illegal)
    assert invalid['counts']['failed'] == 3
    assert invalid['causes'] == {'invalid_action': 3}
    assert all(row['decisions'] == 0 for row in invalid['records'])


def test_decision_and_engine_budgets_censor_without_negative_events(session, monkeypatch):
    report = estimate(session, horizon=Horizon('turns', 1, max_decisions=1))
    assert report['counts']['truncated'] == 3
    assert report['causes'] == {'decision_budget': 3}
    assert report['summaries']['touchdown']['frequency'] is None
    assert all(row['progress'] == 0 and row['values'] is None for row in report['records'])
    def fail(*args, **kwargs):
        raise NoProgress('automatic work exhausted')
    monkeypatch.setattr(SimulationSession, 'step', fail)
    report = estimate(session, n=1)
    assert report['counts']['truncated'] == 1
    assert report['summaries']['touchdown']['n'] == 0


def test_event_horizon_includes_settling_batch_and_zero_horizon(session):
    report = estimate(session, n=1, horizon=Horizon('events', 1))
    assert report['records'][0]['progress'] > 1
    assert report['records'][0]['decisions'] == 1
    assert report['records'][0]['reason'] == 'horizon'
    empty = estimate(session, n=1, first_action=None, horizon=Horizon('decisions', 0))
    assert empty['counts']['valid'] == 1
    assert empty['records'][0]['decisions'] == 0
    with pytest.raises(ValueError, match='zero horizon'):
        estimate(session, horizon=Horizon('decisions', 0))


def finish_factory(config, rng):
    previous = None
    def driver(observation, legal):
        nonlocal previous
        choices = {a.type: a for a in legal.actions}
        priorities = ['START_GAME', 'HEADS', 'RECEIVE']
        if previous and previous.startswith('SETUP_FORMATION_'):
            priorities.append('END_SETUP')
        priorities.extend(['SETUP_FORMATION_SPREAD', 'SETUP_FORMATION_WEDGE', 'END_TURN'])
        selected = next((choices[name] for name in priorities if name in choices), legal.actions[0])
        previous = selected.type
        return selected
    return driver


def test_natural_termination_and_team_turn_horizon():
    config = bb.load_config('gym-1')
    config.rounds = 1
    config.kick_off_table = False
    config.pathfinding_enabled = False
    simulation = SimulationSession(SessionConfig(game_config=config), SeedSpec(17, 'terminal'))
    policy = replace(POLICY, factory=finish_factory)
    try:
        absorbed = estimate(simulation, n=1, continuation_policy=policy,
                            horizon=Horizon('decisions', 500))
        row = absorbed['records'][0]
        assert row['status'] == 'valid' and row['reason'] == 'terminal_absorbed'
        assert row['terminated'] and row['decisions'] < 500
        censored = estimate(simulation, n=1, continuation_policy=policy,
                            horizon=Horizon('decisions', 500, terminal='censor'))
        assert censored['records'][0]['reason'] == 'terminal_censored'
        assert censored['counts']['truncated'] == 1
        assert censored['summaries']['score_home']['n'] == 0
        turns = estimate(simulation, n=1, continuation_policy=policy, horizon=Horizon('turns', 1))
        assert turns['records'][0]['progress'] == 1
        assert turns['records'][0]['reason'] == 'horizon'
    finally:
        simulation.close()


def test_indexed_dice_reordering_and_sibling_source_rng_isolation():
    factual = gfi_boundary()
    saved = capture_snapshot(factual.game)
    before, rng = snapshot_hash(saved), capture_stream(factual.game.rng)
    sibling = branch_from_snapshot(saved, branch_id='sibling', policy=ChancePolicy(seed=SeedSpec(83)))
    sibling_hash = snapshot_hash(capture_snapshot(sibling))
    try:
        actions = ActionControl(factual.game, factual.game.timeline._entities).legal_actions().actions
        move = next(a for a in actions if a.type == 'MOVE' and a.position.x == 3 and a.position.y == 2)
        args = (saved, move, POLICY, Horizon('decisions', 1))
        forward = estimate_continuations(*args, SamplePlan((0, 1, 2, 3), 879))
        reverse = estimate_continuations(*args, SamplePlan((3, 1, 0, 2), 879))
        assert forward['counts']['valid'] == 4
        assert {r['sample_id']: r for r in forward['records']} == {r['sample_id']: r for r in reverse['records']}
        assert len({tuple(SeedSpec(**r['seed']).seed_words()) for r in forward['records']}) == 4
        assert snapshot_hash(capture_snapshot(factual.game)) == before
        assert capture_stream(factual.game.rng) == rng
        assert snapshot_hash(capture_snapshot(sibling)) == sibling_hash
    finally:
        sibling.close()
        factual.close()


def test_spy_only_receives_copied_allowed_channels_and_owned_policy_rng(session):
    calls = []
    def factory(config, rng):
        config['mutated'] = True
        def spy(observation, legal):
            public = observation.to_json()
            assert set(public) == {'primary', 'derived', 'control', 'events', 'next_actor',
                                  'terminated', 'truncated', 'end_reason', 'decision_id', 'state_revision'}
            assert public['primary']['data']['observer_team'] == legal.actor_id
            assert not any(word in json.dumps(public) for word in ('master_seed', 'future_tape', 'privileged', 'snapshot', 'MT19937'))
            action = deepcopy(legal.actions[int(rng.randint(len(legal.actions)))])
            calls.append(legal.actor_id)
            observation.primary.clear()
            legal.actions.clear()
            return action
        return spy
    policy = ContinuationPolicy('spy', 'v1', {}, factory)
    before = snapshot_hash(session.snapshot().engine)
    forward = estimate(session, continuation_policy=policy, horizon=Horizon('decisions', 3))
    backward = estimate(session, continuation_policy=policy, horizon=Horizon('decisions', 3),
                        sample_plan=SamplePlan((2, 0, 1), 421))
    assert forward['counts']['valid'] == 3 and calls
    assert {r['sample_id']: r for r in forward['records']} == {r['sample_id']: r for r in backward['records']}
    assert policy.config == forward['policy']['config'] == {}
    assert snapshot_hash(session.snapshot().engine) == before


@pytest.mark.parametrize('make', [lambda: SamplePlan((1, 1), 1), lambda: SamplePlan((True,), 1),
    lambda: SamplePlan((0,), None), lambda: Horizon('seconds', 1), lambda: Horizon('events', -1),
    lambda: Horizon('turns', 1, terminal='ignore'), lambda: SamplePlan((0,), 1, 'bad name')])
def test_invalid_contracts_fail_before_execution(make):
    with pytest.raises(ValueError):
        make()


def test_touchdown_possession_and_resources_at_real_scoring_boundary(session):
    drivers = {side: finish_factory({}, None) for side in ('home', 'away')}
    for _ in range(60):
        observation = session.observe()
        if observation.primary['data']['decision']['phase'] == 'turn':
            break
        legal = session.legal_actions()
        session.step(drivers[legal.actor_id](observation, legal), session.state_revision)
    else:
        pytest.fail('Fixture did not reach a turn')
    game = session._game
    team = game.state.current_team
    scorer = team.players[0]
    for player in list(game.get_players_on_pitch()):
        game.pitch_to_reserves(player)
    endzone = game.get_opp_endzone_x(team)
    game.reserves_to_pitch(scorer, game.get_square(endzone + (1 if endzone == 1 else -1), 2))
    game.state.weather = bb.WeatherType.NICE
    game.get_ball().move_to(scorer.position)
    game.get_ball().is_carried = True
    game.set_available_actions()
    start = next(a for a in session.legal_actions().actions if a.type == 'START_MOVE')
    session.step(start, session.state_revision)
    move = next(a for a in session.legal_actions().actions
                if a.type == 'MOVE' and a.position.x == endzone and a.position.y == 2)
    report = estimate(session, n=2, first_action=move)
    assert report['counts']['valid'] == 2
    for event in ('touchdown', 'possession_loss'):
        assert report['summaries'][event]['frequency'] == 1
        assert report['summaries'][event]['wilson95'][0] < 1
    side = game.timeline._entities._team(team)
    assert report['summaries']['score_' + side]['mean'] == 1
    assert report['summaries']['rerolls_' + side]['mean'] == team.state.rerolls
    assert report['summaries']['ball_possession']['categories'][0]['value'] == 'no_ball'
    # A touchdown before a later technical failure is excluded, never a complete
    # positive/negative trajectory silently added to a different denominator.
    def failure(config, rng):
        raise RuntimeError('post-score policy unavailable')
    failed = estimate(session, n=1, first_action=move, continuation_policy=replace(POLICY, factory=failure),
                      horizon=Horizon('decisions', 2))
    assert failed['counts']['failed'] == 1
    assert failed['summaries']['touchdown']['n'] == 0
    assert failed['records'][0]['values'] is None


def test_mixed_success_failure_uses_valid_denominator(session):
    def intermittent(config, rng):
        if rng.random_sample() < .5:
            raise RuntimeError('sample policy unavailable')
        return first_factory(config, rng)
    report = estimate(session, n=10, continuation_policy=replace(POLICY, factory=intermittent),
                      horizon=Horizon('decisions', 2))
    valid, failed = report['counts']['valid'], report['counts']['failed']
    assert 0 < valid < 10 and valid + failed == 10
    assert not report['complete_unconditional_sample']
    for name in ('touchdown', 'score_home', 'ball_position'):
        assert report['summaries'][name]['n'] == valid
        assert report['summaries'][name]['excluded'] == failed
    assert report['summaries']['touchdown']['positive'] == 0
    assert report['causes'] == {'RuntimeError': failed}


def test_recorded_actions_replay_without_policy_and_branch_names_do_not_collide(session):
    report = estimate(session, n=1, horizon=Horizon('decisions', 3))
    row = report['records'][0]
    replay = branch_from_snapshot(session.snapshot().engine, branch_id=row['branch_id'],
                                  policy=ChancePolicy(seed=SeedSpec(**row['seed'])))
    try:
        control = ActionControl(replay, replay.timeline._entities)
        for action in row['actions']:
            semantic = SimulationSession._action(action)
            replay.advance(control.decode(control.request(semantic)), max_steps=100000)
        assert snapshot_hash(capture_snapshot(replay)) == row['final_state_hash']
        child = estimate_continuations(capture_snapshot(replay), None, POLICY,
                                       Horizon('decisions', 0), SamplePlan((0,), 421))
        assert child['counts']['valid'] == 1
        assert child['records'][0]['branch_id'] != row['branch_id']
    finally:
        replay.close()
