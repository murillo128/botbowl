"""Finite statistical oracles and engine boundaries for the three decision axes."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import json
import math

import pytest

import botbowl as bb
from botbowl.lab.channels import make_channel, project_inputs
from botbowl.lab.evaluation.calibration import binary_scores
from botbowl.lab.evaluation.decision_axes import (
    AssessmentContext, DecisionAssessmentV1, DecisionQualityV1, DiscreteReference,
    HUMAN_RUBRIC, assess_outcome, assessment_channel, evaluate_decision,
    exact_reference, observed_impact, oracle_impact, reference_from_continuations,
    sampled_reference, surprise,
)
from botbowl.lab.evaluation import EvaluationRecord
from botbowl.lab.observations import ObservationControl, observe
from botbowl.lab.rollouts import Horizon
from examples.lab.decision_axes import engine_demo, synthetic_demo


def context(budget=2):
    return AssessmentContext({'state_id': 's', 'model': {'id': 'finite', 'version': 'v1'},
        'policy': {'name': 'stop', 'version': 'v1', 'config': {}},
        'horizon': asdict(Horizon('decisions', 1)), 'budget': budget})


UTILITY = {'id': 'points', 'version': 'v1', 'definition': 'maximize declared points',
           'unit': 'point', 'values': {'win': 10., 'lose': -2.}}
ORIGIN = {'description': 'Enumerated synthetic outcomes'}


def exact(p=.25, action='a', ctx=None):
    return exact_reference(ctx or context(), action, 'result', {'win': p, 'lose': 1-p}, origin=ORIGIN)


def sampled(outcomes, action='a'):
    return sampled_reference(context(len(outcomes)), action, 'result', ['win', 'lose'], outcomes,
        sample_ids=range(len(outcomes)), seed_plan={'algorithm': 'synthetic-iid-v1', 'master_seed': 17}, origin=ORIGIN)


def quality(refs=None, legal=('a', 'b'), chosen='a'):
    return evaluate_decision(chosen, legal, refs or [exact(), exact(.75, 'b')], utility=UTILITY,
                             origin='Experiment frozen before observing realized outcome')


def assessment(q=None, outcome='win', after=10, annotations=()):
    impact = observed_impact('s', 'r', 'home.points', 'point', 0, after, origin='Synthetic state facts')
    return assess_outcome(q or quality(), 'r', outcome, event=[outcome], impacts=[impact], annotations=annotations)


def test_finite_formulas_units_and_calibration_convention():
    ref = exact(.25)
    row = surprise(ref, ['win'])
    assert row['probability'] == .25
    assert row['nats'] == pytest.approx(math.log(4))
    assert row['nats'] == binary_scores(.25, True)['log_score']
    assert row['units'] == 'nats'
    assert surprise(ref, ['win', 'lose'])['nats'] == 0
    q = quality().to_json()
    assert q['estimates']['a']['expected_utility'] == 1
    assert q['estimates']['b']['expected_utility'] == 7
    assert q['regret'] == 6 and q['regret_interval95'] == [6, 6]
    assert q['utility']['unit'] == 'point'
    assert q['estimates']['a']['standard_error'] == 0
    row = assessment().to_json()
    assert row['impact'][0]['delta'] == 10
    assert row['decision_quality'] == q
    assert 'score' not in row and 'aggregate' not in row


def test_three_counterexamples_remain_distinct():
    rows = synthetic_demo()
    rare, expected, lucky = (rows[k] for k in ('rare_no_impact', 'expected_valuable', 'bad_decision_lucky_outcome'))
    assert rare['surprise']['nats'] == pytest.approx(lucky['surprise']['nats'])
    assert rare['impact'][0]['delta'] == 0 and lucky['impact'][0]['delta'] == 10
    assert rare['decision_quality']['regret'] == 0
    assert lucky['decision_quality']['regret'] == pytest.approx(7.9)
    assert expected['surprise']['nats'] < .02 and expected['impact'][0]['delta'] == 10
    assert expected['decision_quality']['regret'] == 0


def test_zero_mc_successes_is_censored_not_impossible():
    row = surprise(sampled(['lose'] * 4), ['win'])
    assert row['probability'] == 0 and row['nats'] is None
    assert row['status'] == 'censored' and row['nats_interval95'][1] is None
    assert row['probability_interval95'][0] == 0
    # Wilson endpoint at k=0: z^2 / (n + z^2).
    z2 = 1.959963984540054 ** 2
    assert row['probability_interval95'][1] == pytest.approx(z2 / (4 + z2))
    assert row['nats_interval95'][0] == pytest.approx(-math.log(z2 / (4 + z2)))
    incompatible = surprise(exact(0), ['win'])
    assert incompatible['status'] == 'model_incompatible'
    assert incompatible['nats'] is None and incompatible['probability_interval95'] is None
    assert surprise(sampled([]), ['win'])['status'] == 'insufficient_samples'


def test_mc_nonzero_sampling_error_and_regret_intervals():
    q = quality([sampled(['win', 'lose']), sampled(['win', 'win'], 'b')]).to_json()
    a = q['estimates']['a']
    assert a['expected_utility'] == 4 and a['standard_error'] == 6
    assert q['regret'] == 6
    assert q['regret_interval95'] == [0, 12]
    assert q['estimates']['b']['standard_error'] == 0
    assert q['estimates']['b']['interval95'][0] < 10  # no false certainty at all successes
    n = 100
    q = quality([sampled(['win'] * n), sampled(['lose'] * n, 'b')]).to_json()
    radius = 12 * math.sqrt(math.log(80) / (2*n))
    assert q['estimates']['a']['interval95'] == pytest.approx([10-radius, 10])
    row = surprise(sampled(['win', 'lose']), ['win'])
    assert row['nats'] == pytest.approx(math.log(2))
    assert row['nats_interval95'][0] < row['nats'] < row['nats_interval95'][1]


def test_empty_and_single_sample_quality_are_explicit():
    empty = quality([sampled([]), sampled([], 'b')]).to_json()
    assert empty['regret'] is None and empty['regret_interval95'] == [0, 12]
    single = quality([sampled(['win'])], legal=('a',)).to_json()
    assert single['estimates']['a']['standard_error'] is None
    assert single['regret'] == 0 and single['regret_interval95'] == [0, 0]


def test_partial_comparison_and_illegal_actions():
    row = quality(legal=('a', 'b', 'c')).to_json()
    assert row['comparison_scope'] == 'evaluated_subset'
    assert row['compared_actions'] == ['a', 'b'] and row['regret'] == 6
    for kwargs in ({'chosen': 'c'}, {'legal': ('a',)}, {'legal': ('a', 'a')}, {'chosen': 'unoffered'}):
        with pytest.raises(ValueError):
            quality(**kwargs)


@pytest.mark.parametrize('field', ['state_id', 'model', 'policy', 'horizon', 'budget'])
def test_missing_context_rejected(field):
    data = context().to_json()
    del data[field]
    with pytest.raises(ValueError):
        AssessmentContext(data)


@pytest.mark.parametrize('field', ['state_id', 'model', 'policy', 'horizon', 'budget'])
def test_foreign_alternative_context_rejected(field):
    data = context().to_json()
    data[field] = {'state_id': 'other', 'model': {'id': 'other', 'version': 'v1'},
        'policy': {'name': 'other', 'version': 'v1', 'config': {}},
        'horizon': asdict(Horizon('turns', 1)), 'budget': 3}[field]
    with pytest.raises(ValueError, match='same state'):
        quality([exact(), exact(.75, 'b', AssessmentContext(data))])


@pytest.mark.parametrize('p', [-.1, 1.1, float('nan'), float('inf'), True, [1, 2], {'latent_distance': .1}])
def test_nonprobabilities_rejected(p):
    data = exact().to_json()
    data['probabilities'][0] = p
    with pytest.raises(ValueError):
        DiscreteReference(data)


def test_missing_utility_event_origin_and_invalid_normalization():
    for utility in (None, {}, dict(UTILITY, values={'win': 10})):
        with pytest.raises(ValueError):
            evaluate_decision('a', ['a'], [exact()], utility=utility, origin='fixture')
    for event in (None, 'win', [], ['undefined'], ['win', 'win'], [.25], [{'latent': .25}]):
        with pytest.raises(ValueError):
            surprise(exact(), event)
    for field, value in [('probabilities', [.2, .2]), ('method', 'latent_distance'), ('origin', {}),
                         ('support', ['win', 'win'])]:
        data = exact().to_json()
        data[field] = value
        with pytest.raises(ValueError):
            DiscreteReference(data)


def test_sample_receipts_require_equal_budget_unique_ids_and_seed():
    for change in ({'ids': [0, 0]}, {'outcomes': ['win']}, {'seed_plan': {}}, {'outcomes': ['win', 'unknown']}):
        data = sampled(['win', 'lose']).to_json()
        data['samples'].update(change)
        with pytest.raises(ValueError):
            DiscreteReference(data)
    with pytest.raises(ValueError):
        quality([sampled(['win']), sampled(['win', 'lose'], 'b')])
    with pytest.raises(ValueError):
        quality([exact(), sampled(['win', 'lose'], 'b')])


def test_frozen_quality_cannot_be_rewritten_by_lucky_or_unlucky_outcome():
    q = quality()
    old = q.to_json()
    lucky, unlucky = assessment(q).to_json(), assessment(q, 'lose', -2).to_json()
    assert lucky['decision_quality'] == unlucky['decision_quality'] == old
    lucky['decision_quality']['utility']['values']['win'] = 900
    assert q.to_json() == old
    with pytest.raises(FrozenInstanceError):
        q._encoded = b'{}'
    forged = q.to_json()
    forged['regret'] = 0
    with pytest.raises(ValueError):
        DecisionQualityV1.from_json(forged)
    frozen = assessment(q)
    assert DecisionAssessmentV1.from_json(frozen.to_json()) == frozen


def test_impact_identity_and_causal_claim_rejected():
    row = assessment().to_json()
    for key, value in [('method', 'causal_effect'), ('state_id', 'foreign'), ('delta', 99), ('result_id', 'other')]:
        forged = deepcopy(row)
        forged['impact'][0][key] = value
        with pytest.raises(ValueError):
            DecisionAssessmentV1.from_json(forged)
    with pytest.raises(ValueError):
        assess_outcome(quality(), 'r', 'win', event=['lose'], impacts=row['impact'])


def test_human_rubric_consent_and_disagreement_are_separate():
    annotations = [{'author': author, 'consent': True, 'rubric_id': HUMAN_RUBRIC['id'],
                    'rating': rating, 'rationale': 'Personal impression'} for author, rating in [('rater-a', 1), ('rater-b', 3)]]
    row = assessment(annotations=annotations).to_json()
    assert row['human']['disagreement'] == 2
    assert assessment().to_json()['human']['disagreement'] is None
    assert row['decision_quality'] == quality().to_json()
    annotations[0]['consent'] = False
    with pytest.raises(ValueError, match='consent'):
        assessment(annotations=annotations)


def test_evaluation_sidecar_never_changes_standard_inputs():
    game = bb.create_game(size=1, seed=17, control='external')
    try:
        channels = {'primary': make_channel('primary', observe(game, ObservationControl(game), 'home').to_json())}
        inputs = project_inputs(channels)
        channels['evaluation'] = assessment_channel(assessment())
        assert project_inputs(channels) == inputs
        channels['evaluation'] = assessment_channel(assessment(outcome='lose', after=-2))
        assert project_inputs(channels) == inputs
        assert 'decision_quality' not in json.dumps(inputs)
    finally:
        game.close()


def test_engine_oracle_and_fixed_seed_assessment_reproduction():
    first, second = engine_demo(), engine_demo()
    assert first == second
    assert first['decision_quality']['method'] == 'monte_carlo'
    assert first['decision_quality']['available_at'] == 'before_realized_outcome'
    assert first['surprise']['probability'] == 1
    assert all(row['delta'] == 0 for row in first['impact'])
    assert all(row['oracle_records'] for row in first['impact'])
    a, b = first['impact'][0]['oracle_records']
    changed = deepcopy(b)
    changed['value'] = a['value'] + 1
    impact = oracle_impact('s', 'r', EvaluationRecord(a), EvaluationRecord(changed))
    assert impact['delta'] == 1


@pytest.fixture
def continuation_report():
    from botbowl.lab.actions import ActionControl
    from botbowl.lab.rollouts import ContinuationPolicy, SamplePlan, estimate_continuations
    from botbowl.lab.snapshots import capture_snapshot
    from botbowl.lab.timeline import Timeline
    from examples.lab.decision_axes import first_factory
    game = bb.create_game(size=1, seed=17, control='external')
    try:
        Timeline(game, episode_id='decision-axes-adapter')
        action = ActionControl(game).legal_actions().actions[0]
        return estimate_continuations(capture_snapshot(game), action,
            ContinuationPolicy('first', 'v1', {}, first_factory), Horizon('decisions', 1),
            SamplePlan((0, 1), 42, experiment_id='adapter'))
    finally:
        game.close()


def adapt(report, categories=None):
    return reference_from_continuations(report, model_id='engine', model_version='v1',
        observable='touchdown', categories=categories or {'yes': True, 'no': False})


def test_continuation_adapter_retains_reference_and_rejects_censoring(continuation_report):
    report = continuation_report
    row = adapt(report).to_json()
    assert row['samples']['ids'] == [0, 1] and row['samples']['outcomes'] == ['no', 'no']
    assert row['context']['state_id'] == report['snapshot']['state_hash']
    assert row['context']['policy'] == report['policy'] and row['context']['horizon'] == report['horizon']
    assert row['samples']['seed_plan']['master_seed'] == 42
    assert len(row['origin']['report_sha256']) == 64
    assert surprise(adapt(report), ['yes'])['status'] == 'censored'
    # Legitimate truncated SIM-07 report must not become an unconditional estimate.
    censored = deepcopy(report)
    censored['records'][0].update(status='truncated', values=None, reason='decision_budget')
    censored['counts'].update(valid=1, truncated=1)
    censored['complete_unconditional_sample'] = False
    with pytest.raises(ValueError, match='Incomplete continuations'):
        adapt(censored)
    for field, value in [('sample_id', 8), ('seed', {}), ('policy_seeds', {}),
                         ('values', {'touchdown': None}), ('branch_id', 'foreign'), ('actions', [])]:
        changed = deepcopy(report)
        changed['records'][0][field] = value
        with pytest.raises((ValueError, KeyError)):
            adapt(changed)
    with pytest.raises(ValueError, match='out-of-support'):
        adapt(report, {'yes': True})
    changed = deepcopy(report)
    changed['horizon']['limit'] = 0
    with pytest.raises(ValueError, match='positive horizon'):
        adapt(changed)


def test_observed_possession_and_resource_units():
    possession = observed_impact('s', 'r', 'team.possession:home', 'indicator', True, False, origin='fixture')
    assert possession['delta'] == -1
    resource = observed_impact('s', 'r', 'team.rerolls:home', 'reroll', 2, 1, origin='fixture')
    assert resource['delta'] == -1 and resource['unit'] == 'reroll'
    with pytest.raises(ValueError):
        observed_impact('s', 'r', 'latent', 'distance', [0, 1], [1, 2], origin='fixture')
