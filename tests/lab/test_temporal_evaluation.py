"""Hand-calculated forecasts, causal canaries and family-level denominators."""
from copy import deepcopy
import json

import pytest

from botbowl.lab.evaluation.oracle import EvaluationRecord, label_spec
from botbowl.lab.evaluation.temporal import (ForecastV1, TemporalTask, prepare_case, forecast_request,
    make_forecast, baseline_forecast, validate_forecast, evaluate_forecasts)
from botbowl.lab.records import RecordError, EventV1
from botbowl.lab.windows import WindowSpecV1, iter_windows
from tests.lab.test_windows import template, enumerable, freeze, PROFILE, write_episode
from examples.lab.temporal import run_demo, POLICY


SCORE = TemporalTask('team.score', 'home')
MODEL = ('artificial', 'v1')


def fixture_data(tmp_path, template, *, count=10, constant=False, end='terminal', name='enumerable', family=None):
    reader, rows = enumerable(tmp_path, template, count=count, end=end, name=name)
    if family is not None:
        manifest = reader.manifest
        manifest['source_family'] = family
        for row in rows['transitions']:
            row['source_family'] = family
        reader = write_episode(reader.directory, manifest, rows)
    if constant:
        for row in rows['primary']:
            for team in row['channel']['data']['teams']:
                team['score'] = 3
        reader = write_episode(reader.directory, reader.manifest, rows)
    split = freeze(reader)
    spec = WindowSpecV1(2, 8, profile=PROFILE)
    windows = list(iter_windows(reader, spec, split_manifest=split))
    labels = []
    for row in rows['primary']:
        for task, value in ((SCORE, row['channel']['data']['teams'][0]['score']),
                            (TemporalTask('match.terminal'), row['channel']['data']['match']['game_over'])):
            labels.append(EvaluationRecord({'schema_version': 1, 'label': label_spec(task.name).to_json(),
                'entity_id': task.entity_id, 'related_entity_id': None, 'context': row['context'],
                'available_at': row['context'], 'rules': reader.manifest['provenance']['rules'],
                'status': 'available', 'value': value, 'unavailability': None, 'provenance': None}))
    return windows, split, labels


def case(window, split, **kwargs):
    return prepare_case(window, split_manifest=split, protocol=kwargs.pop('protocol', 'teacher_forced'),
                        continuation_policy=POLICY, budget=32, **kwargs)


def prediction(case, values, task=SCORE, **kwargs):
    return make_forecast(case, [{'horizon': h, 'task': task.to_json(), 'value': v} for h, v in values.items()],
                         model_id=MODEL[0], model_revision=MODEL[1], **kwargs)


def evaluate(cases, forecasts, labels=(), tasks=(SCORE,)):
    return evaluate_forecasts(cases, forecasts, tasks, models=[MODEL], records=labels)


def metric(report, h, model='artificial'):
    return next(m for m in report['metrics'] if m['model'][0] == model and m['horizon'] == h)


@pytest.mark.parametrize('constant', [False, True])
def test_hand_computed_multihorizon_and_baselines(tmp_path, template, constant):
    windows, split, labels = fixture_data(tmp_path, template, constant=constant)
    c = case(windows[1], split)
    forecasts = [prediction(c, {h: 3 if constant else 1 + h for h in (1, 2, 4, 8)})]
    report = evaluate([c], forecasts, labels)
    for h in (1, 2, 4, 8):
        assert metric(report, h)['mae_or_error_rate'] == 0
        assert metric(report, h, 'baseline.persistence')['mae_or_error_rate'] == (0 if constant else h)
        assert metric(report, h, 'baseline.linear')['mae_or_error_rate'] == 0
        assert metric(report, h)['family_standard_error'] is None
    assert report == evaluate([c], forecasts, reversed(labels))
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    raw = forecasts[0].to_json()
    raw['predictions'][0]['value'] = 999
    assert forecasts[0].to_json()['predictions'][0]['value'] != 999
    assert ForecastV1.from_json(forecasts[0].to_json()) == forecasts[0]


@pytest.mark.parametrize('end,cause', [('terminal', 'early_terminal'), ('truncated', 'truncated')])
def test_early_end_and_zero_valid_samples(tmp_path, template, end, cause):
    windows, split, labels = fixture_data(tmp_path, template, count=2, end=end)
    c = case(windows[-1], split)
    report = evaluate([c], [prediction(c, {h: 2 for h in (1, 2, 4, 8)})], labels)
    assert metric(report, 1)['valid'] == 1
    for h in (2, 4, 8):
        m = metric(report, h)
        assert (m['attempted'], m['valid'], m['excluded']) == (1, 0, 1)
        assert m['causes'] == {cause: 1}
        assert m['mae_or_error_rate'] is m['family_mean_error'] is m['rmse'] is None
    assert evaluate([], [], [])['case_count'] == 0


def test_protocols_do_not_mix_and_autonomous_context_stays_initial(tmp_path, template):
    windows, split, labels = fixture_data(tmp_path, template)
    teacher = case(windows[2], split, horizons=(1,))
    auto = case(windows[2], split, horizons=(1,), protocol='autonomous', initial_window=windows[0])
    assert forecast_request(auto)['inputs']['observations'][-1]['primary.teams[].score'] == [0, 0]
    report = evaluate([teacher, auto], [prediction(teacher, {1: 2}), prediction(auto, {1: 0})], labels)
    values = {report['strata'][m['stratum']]['protocol']: m['mae_or_error_rate']
              for m in report['metrics'] if m['model'][0] == 'artificial'}
    assert values == {'teacher_forced': 1, 'autonomous': 3}
    assert len(report['strata']) == 2


@pytest.mark.parametrize('attack', ['observation', 'action', 'relabelled_observation', 'last_visible', 'profile', 'budget'])
def test_autonomous_future_injection_rejected(tmp_path, template, attack):
    windows, split, _ = fixture_data(tmp_path, template)
    c = case(windows[2], split, protocol='autonomous', initial_window=windows[0])
    raw = prediction(c, {1: 1}).to_json()
    req = raw['request']
    if attack == 'observation':
        req['history'][-1] = windows[1]['metadata']['history'][-1]
        req['inputs']['observations'][-1] = windows[1]['inputs']['observations'][-1]
    elif attack == 'action':
        req['inputs']['action'] = {'future_command': True}
    elif attack == 'relabelled_observation':
        req['inputs']['observations'][-1]['primary.teams[].score'] = [999, 999]
    elif attack == 'last_visible':
        req['last_visible_at'] = windows[2]['metadata']['cutoff']
    elif attack == 'profile':
        req['window_spec']['profile']['fields'].append(['privileged.rng', 'string'])
    else:
        req['budget'] += 1
    with pytest.raises(ValueError):
        validate_forecast(ForecastV1.from_json(raw), c)


def test_conditioned_initial_action_only(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    split = freeze(reader)
    windows = list(iter_windows(reader, WindowSpecV1(2, 8, profile=PROFILE, mode='action_conditioned'),
                                split_manifest=split))
    c = case(windows[2], split, protocol='autonomous', initial_window=windows[0])
    f = prediction(c, {1: 0})
    assert f.to_json()['request']['inputs']['action'] == rows['transitions'][0]['action']
    raw = f.to_json()
    raw['request']['inputs']['action'] = rows['transitions'][2]['action']
    with pytest.raises(RecordError, match='receipt'):
        validate_forecast(ForecastV1.from_json(raw), c)


def test_targets_do_not_change_forecasts_or_selection(tmp_path, template):
    windows, split, labels = fixture_data(tmp_path, template)
    c = case(windows[1], split)
    original = baseline_forecast(c, [SCORE])
    modified = deepcopy(windows[1])
    modified['targets']['observations'][0]['primary.teams[].score'] = [900, 900]
    altered = case(modified, split)
    assert altered['case_id'] == c['case_id']
    assert baseline_forecast(altered, [SCORE]) == original
    forecasts = [prediction(c, {1: 2})]
    first = evaluate([c], forecasts, labels)
    changed = []
    for label in labels:
        raw = label.to_json()
        if raw['label']['name'] == 'team.score':
            raw['value'] += 99
        changed.append(EvaluationRecord.from_json(raw))
    second = evaluate([altered], forecasts, changed)
    assert [(r['case_id'], r['horizon'], r['reason']) for r in first['rows']] == [
        (r['case_id'], r['horizon'], r['reason']) for r in second['rows']]
    assert metric(first, 1)['mae_or_error_rate'] == 0
    assert metric(second, 1)['mae_or_error_rate'] == 99


def test_actor_alignment_repeated_actor_and_terminal(tmp_path, template):
    windows, split, labels = fixture_data(tmp_path, template, count=3)
    tasks = (TemporalTask('decision.actor'), TemporalTask('match.terminal'), TemporalTask('event.TOUCHDOWN'))
    c = case(windows[0], split, horizons=(1, 2, 3))
    rows = []
    for task in tasks:
        values = ['away', 'away', None] if task.name == 'decision.actor' else (
            [False, False, True] if task.name == 'match.terminal' else [False] * 3)
        rows.extend({'horizon': h, 'task': task.to_json(), 'value': v} for h, v in enumerate(values, 1))
    report = evaluate([c], [make_forecast(c, rows, model_id=MODEL[0], model_revision=MODEL[1])], labels, tasks)
    assert all(m['mae_or_error_rate'] == 0 for m in report['metrics'] if m['model'][0] == MODEL[0])


def test_missing_failures_invalid_entities_and_embeddings_are_counted(tmp_path, template):
    windows, split, labels = fixture_data(tmp_path, template)
    cases = [case(w, split, horizons=(1,)) for w in windows[:4]]
    forecasts = [prediction(cases[0], {1: 'invalid'}),
                 make_forecast(cases[1], [], model_id=MODEL[0], model_revision=MODEL[1], failure='producer crashed'),
                 make_forecast(cases[2], [{'horizon': 1, 'values': [1., 2.]}],
                               model_id=MODEL[0], model_revision=MODEL[1], representation='embedding')]
    report = evaluate(cases, forecasts, labels)
    assert metric(report, 1)['causes'] == {'invalid_domain_or_entity': 1, 'forecast_failed': 1,
                                         'no_decoder': 1, 'missing_forecast': 1}
    assert report['latent_metrics'] is None
    assert all(c['paired'] == 0 and c['attempted'] == 4 for c in report['comparisons'])
    c = cases[0]
    missing = evaluate([c], [prediction(c, {1: 0})])
    assert metric(missing, 1)['causes'] == {'missing_label': 1}
    task = TemporalTask('ball.carrier', 'ball:0')
    bad = prediction(c, {1: 'home:999'}, task)
    result = evaluate([c], [bad], tasks=[task])
    assert metric(result, 1)['causes'] == {'invalid_domain_or_entity': 1}


def test_unequal_branches_and_family_weighting(tmp_path, template):
    # Related episodes remain one cluster despite different lengths.
    first, split, labels = fixture_data(tmp_path, template, count=4)
    second, split2, labels2 = fixture_data(tmp_path, template, count=2, name='other')
    cases = [case(w, split, horizons=(1,)) for w in first[:3]]
    c2 = case(second[0], split2, horizons=(1,))
    # Separate split manifests yield the same family when origin metadata says
    # they are related. Do not infer independence just from different episodes.
    assert c2['request']['family_id'] == cases[0]['request']['family_id']
    forecasts = [prediction(c, {1: i + 1}) for i, c in enumerate(cases)] + [prediction(c2, {1: 5})]
    report = evaluate(cases + [c2], forecasts, labels + labels2)
    m = metric(report, 1)
    assert m['valid'] == 4 and m['family_count'] == 1
    assert m['family_mean_error'] == 1 and m['family_standard_error'] is None
    bad = prediction(cases[0], {1: 1}).to_json()
    bad['request']['emitted_at']['branch_id'] = 'sibling'
    with pytest.raises(ValueError):
        validate_forecast(ForecastV1.from_json(bad), cases[0])


def test_cpu_simulator_continuation_smoke(tmp_path):
    result = run_demo(tmp_path)
    assert result['continuation_counts'] == {'attempted': 2, 'valid': 2, 'failed': 0, 'truncated': 0}
    report = result['evaluation']
    assert report['case_count'] == 4
    assert all(m['mae_or_error_rate'] in (None, 0) for m in report['metrics'])


def test_uncertainty_uses_independent_family_means(tmp_path, template):
    first, split, labels = fixture_data(tmp_path, template, count=4)
    second, split2, labels2 = fixture_data(tmp_path, template, count=2, name='independent', family='independent')
    cases = [case(w, split, horizons=(1,)) for w in first[:3]]
    other = case(second[0], split2, horizons=(1,))
    forecasts = [prediction(c, {1: i + 1}) for i, c in enumerate(cases)] + [prediction(other, {1: 5})]
    report = evaluate(cases + [other], forecasts, labels + labels2)
    m = metric(report, 1)
    assert m['valid'] == 4 and m['family_count'] == 2
    assert m['mae_or_error_rate'] == 1  # three zero errors and one error of four
    assert m['family_mean_error'] == 2  # two equally weighted families: 0 and 4
    assert m['family_standard_error'] == 2  # sample variance 8 / two clusters
    assert report == evaluate(list(reversed(cases + [other])), list(reversed(forecasts)), labels + labels2)


def test_known_event_occurrence_and_missing_event_denominators(tmp_path, template):
    reader, rows = enumerable(tmp_path, template, count=3)
    manifest = reader.manifest
    # Every decision emits one report; a touchdown occurs only on decision two.
    for i, row in enumerate(rows['primary']):
        row['context']['event_seq'] = i
    manifest['final_context'] = rows['primary'][-1]['context']
    events = []
    for i, transition in enumerate(rows['transitions'], 1):
        transition['event_start'], transition['event_stop'] = i, i + 1
        ctx = rows['primary'][i]['context']
        events.append(EventV1({'schema_version': 1, 'payload_version': 1,
            'event_id': [ctx['episode_id'], ctx['branch_id'], i], 'context': ctx,
            'decision_seq': i, 'kind': 'report', 'data': {
                'outcome_type': 'TOUCHDOWN' if i == 2 else 'TURN_START', 'pos': None,
                'player_id': None, 'opp_player_id': None, 'rolls': [], 'team_id': 'home',
                'n': None, 'skill': None}}))
    rows['events'] = [e.to_json() for e in events]
    reader = write_episode(reader.directory, manifest, rows)
    split = freeze(reader)
    windows = list(iter_windows(reader, WindowSpecV1(2, 3, profile=PROFILE), split_manifest=split))
    c = case(windows[0], split, horizons=(1, 2, 3))
    task = TemporalTask('event.TOUCHDOWN')
    f = prediction(c, {1: False, 2: True, 3: True}, task)
    report = evaluate_forecasts([c], [f], [task], models=[MODEL], events=events)
    assert [metric(report, h)['mae_or_error_rate'] for h in (1, 2, 3)] == [0, 0, 0]
    assert [metric(report, h, 'baseline.persistence')['mae_or_error_rate'] for h in (1, 2, 3)] == [0, 1, 1]
    missing = evaluate_forecasts([c], [f], [task], models=[MODEL], events=events[:1])
    assert metric(missing, 2)['causes'] == {'missing_events': 1}


def test_unequal_branch_targets_are_masked_not_joined_to_sibling(tmp_path, template):
    reader, rows = enumerable(tmp_path, template, count=5)
    manifest = reader.manifest
    parent = deepcopy(rows['primary'][3]['context'])
    manifest['branches'].append({'branch_id': 'alternative', 'parent': parent})
    rows['primary'].insert(4, deepcopy(rows['primary'][3]))
    for row in rows['primary'][4:]:
        row['observation_id'] += 1
        row['context']['branch_id'] = 'alternative'
    for row in rows['transitions'][3:]:
        row['before'] = {**row['before'], 'branch_id': 'alternative'}
        row['after'] = {**row['after'], 'branch_id': 'alternative'}
        row['transition_id'][1] = 'alternative'
        row['pre_observation'] += 1
        row['post_observation'] += 1
    manifest['final_context'] = rows['primary'][-1]['context']
    manifest['final_observation'] += 1
    reader = write_episode(reader.directory, manifest, rows)
    split = freeze(reader)
    windows = list(iter_windows(reader, WindowSpecV1(2, 4, profile=PROFILE), split_manifest=split))
    root = case(windows[0], split, horizons=(3, 4))
    branch = case(windows[3], split, horizons=(2, 3))
    task = TemporalTask('decision.actor')
    report = evaluate([root, branch], [prediction(root, {3: 'home', 4: None}, task),
                                      prediction(branch, {2: None, 3: None}, task)], tasks=[task])
    assert metric(report, 4)['causes'] == {'trace_boundary_or_gap': 1}
    assert metric(report, 2)['valid'] == 1
    assert metric(report, 3)['valid'] == 1 and metric(report, 3)['causes'] == {'early_terminal': 1}
    change = TemporalTask('decision.actor_changed')
    c = case(windows[1], split, horizons=(1,))  # away acts twice consecutively
    assert metric(evaluate([c], [prediction(c, {1: False}, change)], tasks=[change]), 1)['mae_or_error_rate'] == 0


@pytest.mark.parametrize('mutation', ['target_context', 'family', 'input', 'spec'])
def test_case_mutation_is_rejected(tmp_path, template, mutation):
    windows, split, labels = fixture_data(tmp_path, template)
    c = case(windows[0], split)
    f = prediction(c, {1: 1})
    if mutation == 'target_context':
        c['window']['metadata']['target_observations'][0]['available_at']['decision_seq'] += 1
    elif mutation == 'family':
        c['window']['metadata']['family_id'] = 'other-family'
    elif mutation == 'input':
        c['initial_window']['inputs']['observations'][-1]['primary.teams[].score'] = [88, 88]
    else:
        c['window']['metadata']['spec']['unit'] = 'events'
    with pytest.raises(ValueError):
        evaluate([c], [f], labels)
