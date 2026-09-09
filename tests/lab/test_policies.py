"""Policy information, private state, semantic choice and coverage contracts."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from botbowl.lab.actions import ActionV1, EmptyOptionsV1, LegalActionsV1, PositionV1
from botbowl.lab.coverage import coverage_report, episode_coverage, option_counts
from botbowl.lab.generate import JobConfig, build_plan, execute_plan, generate, replay_episode, validate_dataset
from botbowl.lab.policies import (POLICIES, LegacyRandomAdapter, PolicySpecV1,
                                  ReferencePolicy, create_policy, policy_inputs)
from botbowl.lab.randomness import SeedSpec, capture_stream
from botbowl.lab.scenarios import create_scenario, scenario_spec


def seed(n=72, side='home'):
    return SeedSpec(n, 'policy-test', 'policy-' + side, 'reference-v1')


def action(name='END_TURN', player=None, position=None, actor='home'):
    return ActionV1(1, name, actor, player, None, position, EmptyOptionsV1())


def legal(*actions):
    return LegalActionsV1(1, 'decision-1', 0, 'home', list(actions), [])


@pytest.mark.parametrize('name', POLICIES)
def test_seed_state_roundtrip_and_interleaving(name):
    choices = legal(action('START_MOVE', 'home:0'), action('START_MOVE', 'home:1'), action())
    config = {} if name in ('random', 'scripted') else {'error_rate': .4}
    first = create_policy(PolicySpecV1(name, seed(), config))
    second = create_policy(first.spec.to_json())
    other = create_policy(PolicySpecV1(name, seed(91, 'away'), config))
    baseline = [first.act({}, choices) for _ in range(12)]
    for expected in baseline:
        other.act({}, choices)
        assert second.act({}, choices) == expected
    snapshot = json.loads(json.dumps(second.capture_state()))
    continuation = [second.act({}, choices) for _ in range(20)]
    restored = create_policy(first.spec)
    restored.restore_state(snapshot)
    assert [restored.act({}, choices) for _ in range(20)] == continuation
    before = restored.capture_state()
    bad = deepcopy(snapshot)
    bad['rng'][2] = 625
    with pytest.raises(ValueError):
        restored.restore_state(bad)
    assert restored.capture_state() == before
    other_state = other.capture_state()
    with pytest.raises(ValueError, match='Incompatible'):
        restored.restore_state(other_state)
    restored.close()
    closed = restored.capture_state()
    first.restore_state(closed)
    with pytest.raises(ValueError, match='live legal'):
        first.act({}, choices)


def test_spec_closed_and_defensive():
    config = {'error_rate': .25}
    spec = PolicySpecV1('possession', seed(), config)
    config['error_rate'] = .9
    wire = spec.to_json()
    assert wire['config']['error_rate'] == .25
    wire['config']['error_rate'] = .1
    with pytest.raises(ValueError):
        create_policy(wire)
    for key, value in [('name', 'os.system'), ('version', '2'), ('action_schema', 'unknown'),
                       ('restoration', 'pickle'), ('input_profile', {'privileged': True})]:
        bad = spec.to_json()
        bad[key] = value
        with pytest.raises(ValueError):
            create_policy(bad)
    bad = spec.to_json()
    bad['seed']['master_seed'] = None
    with pytest.raises(ValueError):
        create_policy(bad)
    with pytest.raises(ValueError):
        PolicySpecV1('random', SeedSpec(0))
    for config in ({'error_rate': True}, {'error_rate': float('nan')}, {'error_rate': -1}, {'search': True}):
        with pytest.raises(ValueError):
            PolicySpecV1('possession', seed(), config)


@pytest.mark.parametrize('name', POLICIES)
def test_missing_ties_terminal_zero_and_forbidden_inputs(name):
    policy = ReferencePolicy(name, seed())
    choices = legal(action('START_MOVE', 'home:0'), action('START_MOVE', 'home:1'))
    assert policy.act({}, choices) in choices.actions
    if name != 'random':
        assert policy.act({}, choices) == choices.actions[0]
    for features, control in [({'rng': {}}, None), ({'snapshot': {}}, None),
                              ({'evaluation': {}}, None), ({}, {'seed': 72})]:
        with pytest.raises(ValueError, match='Unauthorized'):
            policy.act(features, choices, control)
    with pytest.raises(ValueError):
        policy.act({}, legal())
    with pytest.raises(ValueError):
        policy.act({'primary.match.game_over': True}, choices)


def test_uniform_complete_actions_and_legal_errors():
    # A controlled RNG proves the distribution's support/denominator without
    # a statistical threshold: each complete action receives one of N indices.
    choices = legal(action('START_MOVE', 'home:0'), action('START_MOVE', 'home:1'), action())
    class Indices:
        def __init__(self):
            self.i = 0
        def randint(self, n):
            assert n == 3
            value = self.i
            self.i = (self.i + 1) % n
            return value
        def random_sample(self):
            return 0
    for name in ('random', 'possession'):
        policy = ReferencePolicy(name, seed(), None if name == 'random' else {'error_rate': 1})
        policy.rng = Indices()
        assert [policy.act({}, choices) for _ in range(3)] == choices.actions


@pytest.mark.parametrize('side', ['home', 'away'])
def test_possession_moves_carrier_forward_and_recovers_ball(side):
    policy = ReferencePolicy('possession', seed(side=side))
    carrier = side + ':0'
    features = {'primary.players[].position.value.x': [5, 8],
                'primary.players[].position.value.y': [5, 8],
                'primary.balls[].position.value.x': [5],
                'primary.balls[].position.value.y': [5]}
    control = {'primary.players[].id': [carrier, side + ':1'],
               'primary.balls[].carrier.value': [carrier],
               'primary.decision.active_player.value': carrier}
    starts = legal(action('START_MOVE', side + ':1', actor=side), action('START_MOVE', carrier, actor=side))
    assert policy.act(features, starts, control).player_id == carrier
    moves = legal(action('MOVE', position=PositionV1(4, 5), actor=side),
                  action('MOVE', position=PositionV1(6, 5), actor=side))
    assert policy.act(features, moves, control).position.x == (4 if side == 'home' else 6)
    control['primary.balls[].carrier.value'] = [None]
    features['primary.balls[].position.value.x'] = [4]
    assert policy.act(features, moves, control).position.x == 4


def test_risk_tolerance_changes_legal_priority():
    choices = legal(action('START_BLOCK', 'home:0'), action('START_MOVE', 'home:0'))
    assert ReferencePolicy('cautious', seed()).act({}, choices).type == 'START_MOVE'
    assert ReferencePolicy('risk_taking', seed()).act({}, choices).type == 'START_BLOCK'
    rerolls = legal(action('USE_REROLL'), action('DONT_USE_REROLL'))
    assert ReferencePolicy('cautious', seed()).act({}, rerolls).type == 'USE_REROLL'
    assert ReferencePolicy('risk_taking', seed()).act({}, rerolls).type == 'DONT_USE_REROLL'


def test_scripted_state_restores_setup_sequence():
    choices = legal(action('END_SETUP'), action('SETUP_FORMATION_SPREAD'))
    policy = ReferencePolicy('scripted', seed())
    assert policy.act({}, choices).type == 'SETUP_FORMATION_SPREAD'
    snapshot = policy.capture_state()
    restored = ReferencePolicy('scripted', seed())
    restored.restore_state(snapshot)
    assert restored.act({}, choices).type == 'END_SETUP'


@pytest.mark.parametrize('name', POLICIES)
def test_standard_inputs_and_engine_rng_are_isolated(name):
    with closing(create_scenario(scenario_spec('pickup'))) as session:
        engine = session._session._game
        before = capture_stream(engine.rng)
        result = session.observe()
        features, control = policy_inputs(result.primary)
        original = deepcopy(result.primary)
        policy = ReferencePolicy(name, seed())
        choices = session.legal_actions()
        assert policy.act(features, choices, control) in choices.actions
        assert before == capture_stream(engine.rng)
        assert result.primary == original
        wire = json.dumps([features, control, choices.to_json()])
        for forbidden in ('master_seed', 'snapshot', 'rng', 'evaluation', 'privileged'):
            assert forbidden not in wire


def test_explicit_legacy_adapter_and_rng_isolation():
    with closing(create_scenario(scenario_spec('movement'))) as session:
        game = session._session._game
        from botbowl.lab.actions import ActionControl
        controller = ActionControl(game)
        adapter = LegacyRandomAdapter(seed())
        before = capture_stream(game.rng)
        with pytest.raises(ValueError, match='explicit'):
            adapter.act({}, controller.legal_actions())
        chosen = adapter.act_game(game, controller)
        assert chosen in controller.legal_actions().actions
        assert capture_stream(game.rng) == before
        assert adapter.metadata['input_profile'] == 'privileged-game-copy'
        assert adapter.metadata['restoration'] == 'external-actions-only'
        with pytest.raises(ValueError):
            create_policy({'name': 'legacy_random'})
        adapter.close()
        with pytest.raises(ValueError):
            adapter.act_game(game, controller)


def test_exact_counter_denominators_and_empty_reset():
    choices = legal(action('START_MOVE', 'home:0'), action('START_MOVE', 'home:1'), action())
    rows = [option_counts(choices, choices.actions[0]), option_counts(legal(action()), action())]
    events = [{'kind': 'report', 'data': {'outcome_type': 'TOUCHDOWN'}}] * 2
    first = episode_coverage(rows, events, decision_budget=8, max_steps=100)
    seat = first['sides']['home']
    assert seat['decisions'] == 2 and seat['available_actions'] == 4
    assert seat['mean_available_actions'] == 2
    assert seat['types']['START_MOVE'] == dict(available_actions=2, offered_decisions=1,
        selected=1, availability_rate=.5, selection_rate=.5, selection_given_opportunity=1)
    assert seat['types']['END_TURN']['selection_given_opportunity'] == .5
    empty = episode_coverage([], [], decision_budget=0, max_steps=100)
    assert empty['decisions'] == 0 and empty['sides']['home']['mean_available_actions'] is None
    specs = {side: PolicySpecV1('random', seed(side=side)).to_json() for side in ('home', 'away')}
    report = coverage_report([{'coverage': coverage, 'scenario': 'movement', 'policy_specs': specs}
                              for coverage in (first, empty)])
    assert report['events']['report:TOUCHDOWN'] == dict(occurrences=2, episodes=1, episode_rate=.5)
    assert report['sample_episodes'] == 2 and report['sample_decisions'] == 2
    assert 'report:TOUCHDOWN' in report['reached_events']
    assert set(report['reached_events']).isdisjoint(report['unexercised_events'])
    assert set(report['reached_events'] + report['unexercised_events']) == set(report['supported_events'])
    assert coverage_report([])['events']['report:TOUCHDOWN']['episode_rate'] is None


@pytest.mark.parametrize('style', ['possession', 'cautious', 'risk_taking'])
def test_collection_metadata_coverage_replay_and_episode_reset(tmp_path, style):
    config = JobConfig(str(tmp_path / 'data'), episodes=2, home_policy=style,
                       home_policy_config={'error_rate': .2}, scenario='pickup', max_decisions=4)
    data = generate(config)
    assert validate_dataset(config.output) == data
    again = generate(replace(config, output=str(tmp_path / 'again')))
    assert again == data
    for episode in data['episodes']:
        assert episode['coverage']['decisions'] == episode['end']['decisions']
        assert episode['policy_specs']['home']['name'] == style
        assert episode['policy_specs']['home']['config'] == {'error_rate': .2}
    replayed = replay_episode(config.output, 'episode-000000', str(tmp_path / 'replay'))
    assert replayed['episodes'] == [data['episodes'][0]]
    path = tmp_path / 'data' / 'dataset.json'
    bad = json.loads(path.read_text())
    bad['episodes'][0]['coverage']['decisions'] += 1
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='coverage'):
        validate_dataset(config.output)


def test_legacy_generation_plan_still_validates_and_replays(tmp_path):
    config = JobConfig(str(tmp_path / 'old'), max_decisions=1)
    plan = build_plan(config, _legacy=True)
    assert plan['schema_version'] == 1 and 'policy_specs' not in plan['episodes'][0]
    data = execute_plan(plan, config.output)
    assert data['schema_version'] == 1
    assert validate_dataset(config.output) == data
    assert replay_episode(config.output, 'episode-000000', str(tmp_path / 'replay'))['episodes'] == data['episodes']


def test_finite_example_freezes_scenario_and_style_holdouts_by_family(tmp_path):
    from examples.lab.policies import COMPARISONS, HOLDOUTS, collect_comparison
    report, split = collect_comparison(tmp_path)
    assert report['sample_episodes'] == len(COMPARISONS) * 2
    assert report['sample_decisions'] <= len(COMPARISONS) * 2 * 8
    assert split['held_out_groups'] == HOLDOUTS
    assert len({g['policy_id'] for g in report['groups']}) == 5
    assert report['reached_events']
    family_splits = {}
    for row in split['sources']:
        source, family = row['source'], row['family_id']
        assigned = split['assignments'][family]
        assert family_splits.setdefault(family, assigned) == assigned
        held = ('touchdown' in source['groups']['scenario'] or
                set(source['groups']['policy']) & set(HOLDOUTS['policy']))
        if held:
            assert assigned == 'test'
