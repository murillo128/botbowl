"""Independent score calculations, causal canaries, provenance and engine smoke."""
from copy import deepcopy
import json
import math

import pytest

import botbowl as bb
from botbowl.lab.evaluation.calibration import (
    CalibrationTarget, binary_scores, categorical_scores, empirical_crps,
    interval_scores, validate_prediction, validate_continuation_pair, evaluate_calibration,
)
from botbowl.lab.evaluation.temporal import TemporalTask, make_forecast
from botbowl.lab.rollouts import exact_dice_distribution
from botbowl.lab.randomness import SeedSpec
from examples.lab.calibration import demo_inputs, run_demo


@pytest.fixture(scope="module")
def original_inputs(tmp_path_factory):
    return demo_inputs(tmp_path_factory.mktemp("calibration"))


@pytest.fixture
def inputs(original_inputs):
    return deepcopy(original_inputs)


def evaluate(inputs, forecasts=None, target=None, **kwargs):
    case, forecast, default, pair = inputs
    return evaluate_calibration([case], [forecast] if forecasts is None else forecasts,
        [target or default], models=[('artificial', 'v1')], continuations={(case['case_id'], 1): pair}, **kwargs)


def forecast(inputs, value, task=None):
    return make_forecast(inputs[0], [{'horizon': 1, 'task': (task or inputs[2].task).to_json(), 'value': value}],
                         model_id='artificial', model_revision='v1')


def test_scores_against_independent_arithmetic():
    assert binary_scores(.25, True) == {'brier': .75 ** 2, 'log_score': -math.log(.25)}
    assert binary_scores(.25, False)['brier'] == .25 ** 2
    scores = categorical_scores([.2, .3, .5], ['a', 'b', 'c'], 'b')
    assert scores['brier'] == pytest.approx(.04 + .49 + .25)
    assert scores['log_score'] == -math.log(.3)
    for samples, y in [([0, 2], 1), ([1, 2, 4, 9], 3), ([2], 5), ([-3, -1, 2], -.5)]:
        brute = sum(abs(x - y) for x in samples) / len(samples) - sum(
            abs(x - z) for x in samples for z in samples) / (2 * len(samples) ** 2)
        assert empirical_crps(samples, y) == pytest.approx(brute)
    assert empirical_crps([0, 2], 1) == .5
    assert interval_scores(1, 1, 1) == {'coverage': 1., 'width': 0}


def test_exact_distribution_rewards_calibrated_over_overconfident_and_constant():
    # Two equally frequent known event probabilities. Constant .5 misses resolution.
    losses = {}
    for name, predictions in [('calibrated', [.2, .8]), ('overconfident', [.01, .99]), ('constant', [.5, .5])]:
        losses[name] = {key: sum(.5 * ((1 - q) * binary_scores(p, False)[key] +
                                      q * binary_scores(p, True)[key])
                                for p, q in zip(predictions, [.2, .8]))
                        for key in ('brier', 'log_score')}
    for key in ('brier', 'log_score'):
        assert losses['calibrated'][key] < losses['overconfident'][key]
        assert losses['calibrated'][key] < losses['constant'][key]
    dice = exact_dice_distribution('D6')
    p = sum(r['probability'] for r in dice['outcomes'] if r['faces'][0] >= 2)
    assert p == pytest.approx(5 / 6)
    assert sum(r['probability'] * binary_scores(p, r['faces'][0] >= 2)['brier']
               for r in dice['outcomes']) == pytest.approx(5 / 36)


@pytest.mark.parametrize('probabilities,support', [([-.1, 1.1], [False, True]),
    ([.2, .2], [False, True]), ([float('nan'), .5], [False, True]),
    ([float('inf'), 0], [False, True]), ([1], [False, True]), ([.5, .5], [False, False])])
def test_bad_probabilities_and_dimensions(probabilities, support):
    with pytest.raises(ValueError):
        categorical_scores(probabilities, support, True)


@pytest.mark.parametrize('samples', [[], [float('nan')], [float('inf')], [True], [[1, 2]]])
def test_bad_scalar_samples(samples):
    with pytest.raises(ValueError):
        empirical_crps(samples, 1)


def test_zero_exact_not_clipped_and_support_is_strict(inputs):
    assert binary_scores(0., True)['log_score'] == 'infinity'
    assert binary_scores(0., False)['log_score'] == 0
    with pytest.raises(ValueError, match='outside declared support'):
        categorical_scores([.5, .5], ['a', 'b'], 'c')
    report = evaluate(inputs, [forecast(inputs, {'kind': 'binary', 'probability': 1.})])
    score = report['metrics'][0]['scores']['log_score']
    assert score['mean'] == 'infinity' and score['infinite'] == 4
    assert score['by_family']['mean'] == 'infinity'
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('change', ['policy', 'action', 'horizon', 'context', 'snapshot',
                                   'seeds', 'origins', 'row_seed', 'policy_seed', 'counts'])
def test_continuation_receipts_reject_mismatch_and_reuse(inputs, change):
    case, _, _, pair = inputs
    pair = deepcopy(pair)
    ref, ev = pair['reference'], pair['evaluation']
    if change == 'policy':
        ev['policy']['version'] = 'v2'
    elif change == 'action':
        ev['first_action'] = {}
    elif change == 'horizon':
        ev['horizon']['limit'] = 2
    elif change == 'context':
        ev['snapshot']['context']['decision_seq'] += 1
    elif change == 'snapshot':
        ev['snapshot']['state_hash'] = 'foreign'
    elif change == 'seeds':
        pair['evaluation'] = deepcopy(ref)
    elif change == 'origins':
        ev['records'][0]['branch_id'] = ref['records'][0]['branch_id']
        ev['records'][0]['final_context']['branch_id'] = ref['records'][0]['branch_id']
    elif change == 'row_seed':
        ev['records'][0]['seed']['master_seed'] += 1
    elif change == 'policy_seed':
        ev['records'][0]['policy_seeds']['home']['master_seed'] += 1
    else:
        ev['counts']['valid'] -= 1
    with pytest.raises(ValueError):
        validate_continuation_pair(case, 1, **pair)


def test_labels_do_not_change_forecasts_bins_parameters_or_reference(inputs):
    original = inputs[1].to_json()
    first = evaluate(inputs)
    for row in inputs[3]['evaluation']['records']:
        row['values']['touchdown'] = True
    second = evaluate(inputs)
    assert inputs[1].to_json() == original
    assert first['bins'] == second['bins'] and first['levels'] == second['levels']
    assert first['references'] == second['references']
    assert first['metrics'][0]['scores']['brier']['mean'] == 0
    assert second['metrics'][0]['scores']['brier']['mean'] == 1
    assert first['metrics'][0]['reliability'][0]['bins'][0]['mean_probability'] == 0
    assert second['metrics'][0]['reliability'][0]['bins'][0]['mean_probability'] == 0
    # Reference labels affect reference summaries only, never predictor scores.
    for row in inputs[3]['reference']['records']:
        row['values']['touchdown'] = True
    third = evaluate(inputs)
    assert third['metrics'] == second['metrics']


def test_invalid_missing_failed_embedding_and_point_denominators(inputs):
    invalid = forecast(inputs, {'kind': 'binary', 'probability': -1})
    failed = make_forecast(inputs[0], [], model_id='artificial', model_revision='v1', failure='unavailable')
    latent = make_forecast(inputs[0], [{'horizon': 1, 'values': [1., 2.]}],
                          model_id='artificial', model_revision='v1', representation='embedding')
    for forecasts in ([], [invalid], [failed], [latent]):
        metric = evaluate(inputs, forecasts)['metrics'][0]
        assert metric['attempted_forecasts'] == metric['invalid_forecasts'] == 1
        assert metric['attempted_samples'] == metric['excluded_rows'] == 4
        assert metric['valid_samples'] == 0 and metric['causes']
    metric = evaluate(inputs, [forecast(inputs, False)])['metrics'][0]
    assert set(metric['scores']) == {'absolute_error', 'squared_error'}
    assert metric['reliability'][0]['ece'] is None


def test_scalar_intervals_units_crps_and_diversity_are_separate(inputs):
    target = CalibrationTarget(TemporalTask('team.score', 'home'), 'scalar')
    value = {'kind': 'samples', 'unit': 'touchdown', 'samples': [0, 2]}
    metric = evaluate(inputs, [forecast(inputs, value, target.task)], target, levels=(.5,))['metrics'][0]
    assert metric['scores']['crps']['mean'] == .5  # y = 0
    assert metric['scores']['coverage@0.5']['mean'] == 0  # [.5, 1.5]
    assert metric['scores']['width@0.5']['mean'] == 1
    assert metric['scores']['sample_variance']['mean'] == 2
    for intervals in ([], [{'level': .5, 'lower': 2, 'upper': 1}],
                      [{'level': .5, 'lower': float('nan'), 'upper': 1}]):
        with pytest.raises(ValueError):
            validate_prediction(dict(value, intervals=intervals), target, inputs[0], (.5,))
    with pytest.raises(ValueError, match='unit'):
        validate_prediction(dict(value, unit='yards'), target, inputs[0])


def test_categorical_classwise_reliability_and_support(inputs):
    target = CalibrationTarget(TemporalTask('team.score', 'home'), 'categorical', (0, 1, 2))
    value = {'kind': 'categorical', 'support': [0, 1, 2], 'probabilities': [.5, .3, .2]}
    metric = evaluate(inputs, [forecast(inputs, value, target.task)], target)['metrics'][0]
    assert metric['scores']['brier']['mean'] == pytest.approx(.25 + .09 + .04)
    assert len(metric['reliability']) == 3
    wrong = dict(value, support=[0, 1, 3])
    metric = evaluate(inputs, [forecast(inputs, wrong, target.task)], target)['metrics'][0]
    assert metric['invalid_forecasts'] == 1 and metric['valid_samples'] == 0


def test_small_samples_families_reproducibility_and_holdout(inputs, tmp_path):
    first = evaluate(inputs)
    assert first == evaluate(inputs)
    assert json.loads(json.dumps(first, allow_nan=False)) == first
    metric = first['metrics'][0]
    family = metric['reliability'][0]['bins'][0]['frequency_by_family']
    assert family['families'] == 1 and family['standard_error'] is None
    assert family['hoeffding95'] == [0., 1.] and family['status'] == 'insufficient_families'
    with pytest.raises(ValueError, match='held-out'):
        evaluate(inputs, fit_families=[inputs[0]['request']['family_id']])
    other = demo_inputs(tmp_path, name='other', n=1)
    report = evaluate_calibration([inputs[0], other[0]], [inputs[1], other[1]], [inputs[2]],
        models=[('artificial', 'v1')], continuations={
            (i[0]['case_id'], 1): i[3] for i in (inputs, other)})
    metric = report['metrics'][0]
    assert metric['attempted_samples'] == 5
    assert metric['scores']['brier']['by_family']['families'] == 2
    assert report['references'][0]['status'] in ('descriptive', 'insufficient_samples')


def test_terminal_absorption_censoring_and_empty_samples(inputs):
    for report in inputs[3].values():
        for row in report['records']:
            row.update(reason='terminal_absorbed', terminated=True)
    assert evaluate(inputs)['metrics'][0]['valid_samples'] == 4
    for report in inputs[3].values():
        report['horizon']['terminal'] = 'censor'
        report['counts'].update(valid=0, truncated=4)
        for row in report['records']:
            row.update(status='truncated', reason='terminal_censored', values=None)
    metric = evaluate(inputs)['metrics'][0]
    assert metric['valid_samples'] == 0 and metric['excluded_rows'] == 4
    assert metric['causes'] == {'truncated:terminal_censored': 4}
    for report in inputs[3].values():
        report['records'] = []
        report['sample_plan']['sample_ids'] = []
        report['counts'] = {'attempted': 0, 'valid': 0, 'failed': 0, 'truncated': 0}
    metric = evaluate(inputs)['metrics'][0]
    assert metric['attempted_forecasts'] == 1 and metric['attempted_samples'] == 0
    assert metric['causes'] == {'empty_evaluation': 1}


def test_cpu_example(tmp_path):
    report = run_demo(tmp_path)
    assert report['exact_d6_at_least_two']['expected_brier'] == pytest.approx(5 / 36)
    assert report['calibration']['metrics'][0]['scores']['brier']['mean'] == 0


def test_engine_dice_known_probability_smoke():
    # Actual engine dice, independently seeded: raw event, no tactical claim.
    probability = sum(r['probability'] for r in exact_dice_distribution('D6')['outcomes']
                      if r['faces'][0] >= 2)
    outcomes = [bb.D6(bb.DiceSource(SeedSpec(879, str(i)).seed_words())).value >= 2
                for i in range(80)]
    observed = sum(outcomes) / len(outcomes)
    assert abs(observed - probability) < .15
    assert sum(binary_scores(probability, y)['brier'] for y in outcomes) / len(outcomes) == pytest.approx(
        observed * (1 - probability) ** 2 + (1 - observed) * probability ** 2)


def test_correlated_windows_do_not_inflate_family_evidence():
    from botbowl.lab.evaluation.calibration import _family_summary
    original = [('family-a', 0.), ('family-b', 1.)]
    correlated = [('family-a', 0.)] * 100 + [('family-b', 1.)]
    summary = _family_summary(original, True)
    assert summary == _family_summary(correlated, True)
    assert summary['mean'] == .5 and summary['families'] == 2
    assert summary['standard_error'] == .5
    assert _family_summary([('family-' + str(i), 0.) for i in range(100)], True)['hoeffding95'][1] < .14


@pytest.mark.parametrize('kwargs', [{'bins': (0, .5, .5, 1)}, {'bins': (.1, 1)},
                                    {'levels': ()}, {'levels': (0, .8)}, {'levels': (.8, .8)}])
def test_bins_and_levels_are_fixed_valid_inputs(inputs, kwargs):
    with pytest.raises(ValueError):
        evaluate(inputs, **kwargs)


def test_cross_case_seed_leakage_and_wrong_receipt(inputs, tmp_path):
    other = demo_inputs(tmp_path, name='other', n=4)
    # Keep a valid pair within each case but swap the role seed namespaces across
    # cases. Different contexts do not justify using reference randomness on test.
    for role, opposite in [('reference', 'evaluation'), ('evaluation', 'reference')]:
        source = inputs[3][opposite]
        destination = other[3][role]
        destination['sample_plan'] = deepcopy(source['sample_plan'])
        for row, source_row in zip(destination['records'], source['records']):
            for field in ('sample_id', 'branch_id', 'seed', 'policy_seeds'):
                row[field] = deepcopy(source_row[field])
            row['final_context']['branch_id'] = row['branch_id']
    with pytest.raises(ValueError, match='Cross-case'):
        evaluate_calibration([inputs[0], other[0]], [inputs[1], other[1]], [inputs[2]],
            models=[('artificial', 'v1')], continuations={
                (i[0]['case_id'], 1): i[3] for i in (inputs, other)})
    from botbowl.lab.evaluation.temporal import ForecastV1
    raw = inputs[1].to_json()
    raw['request']['continuation_policy']['version'] = 'foreign'
    with pytest.raises(ValueError, match='receipt'):
        evaluate(inputs, [ForecastV1.from_json(raw)])


def test_provenance_retained_and_invalid_targets_not_negative_events(inputs):
    report = evaluate(inputs)
    receipt = report['sampling'][0]
    assert receipt['family_id'] == inputs[0]['request']['family_id']
    assert receipt['sample_plans']['reference'] == inputs[3]['reference']['sample_plan']
    ev = inputs[3]['evaluation']
    ev['records'][0].update(status='failed', reason='engine_failure', values=None)
    ev['counts'].update(valid=3, failed=1)
    metric = evaluate(inputs)['metrics'][0]
    assert metric['attempted_samples'] == 4 and metric['valid_samples'] == 3
    assert metric['causes'] == {'failed:engine_failure': 1}
    assert metric['reliability'][0]['bins'][0]['n'] == 3
