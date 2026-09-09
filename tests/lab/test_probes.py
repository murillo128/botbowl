"""Leakage, numeric controls, inert arrays and real DATA-03/EVAL-01 joins."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys

import numpy as np
import pytest

from botbowl.lab.evaluation.oracle import EvaluationContext, EvaluationOracle, LabelRequest
from botbowl.lab.evaluation.probes import ProbeSpec, ProbeTask, align_embeddings, export_probe_task
from botbowl.lab.records import RecordError, encode_json
from botbowl.lab.rules import RulesDescriptor
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1, iter_windows
from examples.lab.probe_reference import (CONTROLS, evaluate_final, family_weights,
                                          fit_reference, metrics, synthetic_fixture)
from tests.baseline import progress_action
from tests.lab.test_recording import record_game
from tests.lab.test_windows import PROFILE


BUDGETS = {'train': 24, 'validation': 12, 'test': 12}


@pytest.fixture
def synthetic():
    return synthetic_fixture()


@pytest.fixture
def captured(tmp_path):
    game, recorder = record_game(tmp_path, profile=PROFILE)
    records = {}
    requests = [LabelRequest('team.score', 'home'), LabelRequest('player.position', 'home:0'),
                LabelRequest('geometry.adjacent', 'home:0', 'away:0'),
                LabelRequest('block.defender_down', 'home:0', 'away:0')]
    try:
        for _ in range(4):
            ctx = EvaluationContext.capture(game, recorder.timeline._entities, recorder.timeline.context,
                                            requests, rules=RulesDescriptor(**recorder._provenance['rules']))
            for record in EvaluationOracle().evaluate(ctx):
                records[record.key] = record
            recorder.advance(progress_action(game))
        ctx = EvaluationContext.capture(game, recorder.timeline._entities, recorder.timeline.context,
                                        requests, rules=RulesDescriptor(**recorder._provenance['rules']))
        for record in EvaluationOracle().evaluate(ctx):
            records[record.key] = record
        recorder.finish(truncation_reason='fixture_limit')
        reader = EpisodeReader(tmp_path, 'episode')
        split = build_split_manifest([origin_from_episode(reader.manifest)], proportions={'train': 1},
                                     seed=1, split_version='probe-real-v1')
        windows = list(iter_windows(reader, WindowSpecV1(2, 2, profile=PROFILE), split_manifest=split))
        return windows, list(records.values()), split
    finally:
        recorder.close()
        game.close()


def export(captured, spec=None):
    windows, records, split = captured
    return export_probe_task(windows, records, spec or ProbeSpec('team.score', 'home'), split_manifest=split,
                             raw_features=[{'field': 'primary.teams[].score', 'path': [0]}])


def test_real_window_catalogue_join_current_future_and_masks(captured):
    current = export(captured).to_json()
    assert current['literal_in_observation'] and current['literal_in_input_profile']
    assert current['samples'][0]['raw'] == [0.0, 0.0, 0.0, 1.0]
    future = export(captured, ProbeSpec('team.score', 'home', target_offset=2)).to_json()
    assert not future['literal_in_input_profile']
    assert future['samples'][-1]['present'] is False
    assert future['samples'][-1]['target'] is None
    assert future['samples'][-1]['unavailability']['code'] == 'missing_future'
    for row in future['samples'][:-1]:
        assert row['target_context']['decision_seq'] == row['cutoff']['decision_seq'] + 2
    for spec in (ProbeSpec('player.position', 'home:0', component='x'),
                 ProbeSpec('geometry.adjacent', 'home:0', 'away:0'),
                 ProbeSpec('block.defender_down', 'home:0', 'away:0')):
        task = export(captured, spec).to_json()
        assert task['label']['name'] == spec.label
        assert all(r['present'] or r['target'] is None for r in task['samples'])
    assert export((list(reversed(captured[0])), list(reversed(captured[1])), captured[2])) == export(captured)


@pytest.mark.parametrize('mutation', ['future', 'history', 'mask', 'dimensions', 'missing_label', 'duplicate_label',
                                      'duplicate_window', 'family', 'split', 'entity'])
def test_export_rejects_bad_joins_and_causal_inputs(captured, mutation):
    windows, records, split = captured
    windows = deepcopy(windows)
    spec = ProbeSpec('team.score', 'home')
    if mutation == 'future':
        windows[0]['metadata']['target_observations'][0]['available_at']['decision_seq'] += 5
    elif mutation == 'history':
        windows[0]['metadata']['history'][-1]['available_at']['event_seq'] += 100
    elif mutation == 'mask':
        windows[0]['inputs']['presence'][-1] = 1
    elif mutation == 'dimensions':
        windows[0]['targets']['presence'].append(False)
    elif mutation == 'missing_label':
        records = []
    elif mutation == 'duplicate_label':
        records = records + records[:1]
    elif mutation == 'duplicate_window':
        windows.append(windows[0])
    elif mutation == 'family':
        windows[0]['metadata']['family_id'] = 'foreign'
    elif mutation == 'split':
        windows[0]['split'] = 'test'
    elif mutation == 'entity':
        spec = ProbeSpec('team.score', 'away')
    with pytest.raises(ValueError):
        export((windows, records, split), spec)


def test_unknown_labels_and_out_of_horizon_fail(captured):
    with pytest.raises(RecordError):
        ProbeSpec('tactical.universal_quality', 'home')
    with pytest.raises(RecordError):
        ProbeSpec('human.tactical_annotation', 'home:0')
    with pytest.raises(RecordError, match='horizon'):
        export(captured, ProbeSpec('team.score', 'home', target_offset=3))
    with pytest.raises(RecordError):
        ProbeSpec('player.position', 'home:0')


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'extra', 'dimension', 'nan', 'object',
                                      'callable', 'module', 'spec', 'hash', 'foreign_id'])
def test_embedding_rejection(synthetic, mutation):
    task, bundle = synthetic
    changed = deepcopy(bundle)
    if mutation == 'duplicate':
        changed['rows'].append(changed['rows'][0])
    elif mutation == 'missing':
        changed['rows'].pop()
    elif mutation == 'extra':
        row = deepcopy(changed['rows'][0])
        row['id']['decision_id'][2] = 9999
        changed['rows'].append(row)
    elif mutation == 'dimension':
        changed['rows'][0]['values'].append(2.0)
    elif mutation == 'nan':
        changed['rows'][0]['values'][0] = float('nan')
    elif mutation == 'object':
        changed['rows'][0]['values'][0] = object()
    elif mutation == 'callable':
        changed['loader'] = lambda: pytest.fail('Dataset callable executed')
    elif mutation == 'module':
        changed['module'] = 'os.system'
    elif mutation == 'spec':
        changed['window_spec']['history_length'] += 1
    elif mutation == 'hash':
        changed['encoder_revision'] = 'latest'
    elif mutation == 'foreign_id':
        changed['rows'][0]['id']['episode_id'] = 'foreign'
    with pytest.raises(RecordError):
        align_embeddings(task, changed)


def test_frozen_encoder_alignment_and_test_labels_do_not_affect_fit(synthetic, tmp_path):
    task, bundle = synthetic
    artifact = tmp_path / 'encoder.bin'
    artifact.write_bytes(b'opaque external artifact: runner must never load this')
    artifact.chmod(0o444)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    bundle['encoder_revision'] = 'sha256:' + digest
    original = encode_json(bundle)
    fit = fit_reference(task, bundle, budgets=BUDGETS)
    changed = task.to_json()
    for row in changed['samples']:
        if row['split'] == 'test':
            row['target'] += 10000
            row['raw'][0] += 123456
    changed_bundle = deepcopy(bundle)
    test_ids = {tuple(r['id']['decision_id']) for r in changed['samples'] if r['split'] == 'test'}
    for row in changed_bundle['rows']:
        if tuple(row['id']['decision_id']) in test_ids:
            row['values'] = [12345.0, 99999.0]
    assert fit_reference(ProbeTask(changed), changed_bundle, budgets=BUDGETS) == fit
    shuffled = task.to_json()
    shuffled['samples'].reverse()
    reversed_bundle = deepcopy(bundle)
    reversed_bundle['rows'].reverse()
    assert fit_reference(ProbeTask(shuffled), reversed_bundle, budgets=BUDGETS) == fit
    assert encode_json(bundle) == original
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
    result = evaluate_final(task, bundle, fit)
    changed_result = evaluate_final(ProbeTask(changed), changed_bundle, fit)
    assert result['controls']['external']['splits']['test']['rmse'] < 1e-10
    assert changed_result['controls']['external']['splits']['test']['rmse'] > 1
    for name in CONTROLS:
        assert {s: r['rows'] for s, r in result['controls'][name]['splits'].items()} == BUDGETS
    assert result['controls']['raw']['splits']['test']['rmse'] < 1e-10
    assert result['controls']['random_projection']['splits']['test']['rmse'] < 1e-10
    assert result['controls']['constant']['splits']['test']['rmse'] > 1


def test_random_labels_do_not_look_predictable():
    task, bundle = synthetic_fixture(51, families=15, decisions=80, random_target=True, classification=True)
    fit = fit_reference(task, bundle, budgets={'train': 300, 'validation': 160, 'test': 160}, seed=51)
    result = evaluate_final(task, bundle, fit)
    for name in CONTROLS:
        assert 0.3 < result['controls'][name]['splits']['test']['accuracy'] < 0.7


def test_absent_classes_are_reported_without_test_fitting():
    task, bundle = synthetic_fixture(classification=True)
    data = task.to_json()
    for row in data['samples']:
        row['target'] = 'true' if row['split'] == 'test' else 'false'
    task = ProbeTask(data)
    fit = fit_reference(task, bundle, budgets=BUDGETS)
    assert fit['classes'] == ['false']
    result = evaluate_final(task, bundle, fit)
    for name in CONTROLS:
        test = result['controls'][name]['splits']['test']
        assert test['accuracy'] == 0
        assert test['per_class']['true']['recall'] == 0
        assert test['per_class']['true']['precision'] is None
        assert test['per_class']['false']['recall'] is None


def test_metrics_known_formulas_and_family_not_row_weighting():
    assert family_weights(['a', 'a', 'b']).tolist() == [0.25, 0.25, 0.5]
    regression = metrics('regression', [0, 0, 0], [2, 2, 0], ['a', 'a', 'b'])
    assert regression['mae'] == 1
    assert regression['rmse'] == pytest.approx(np.sqrt(2))
    duplicated = metrics('regression', [0] * 5, [2] * 4 + [0], ['a'] * 4 + ['b'])
    assert duplicated['mae'] == regression['mae']
    assert duplicated['rmse'] == regression['rmse']
    classification = metrics('classification', ['a', 'a', 'b'], ['a', 'b', 'b'], ['f', 'f', 'g'])
    assert classification['accuracy'] == 0.75
    assert classification['per_class']['a']['precision'] == 1
    assert classification['per_class']['a']['recall'] == 0.5
    assert classification['per_class']['a']['f1'] == pytest.approx(2 / 3)
    assert classification['per_class']['b']['precision'] == pytest.approx(2 / 3)


def test_family_contamination_masks_and_insufficient_budget(synthetic):
    task, bundle = synthetic
    data = task.to_json()
    data['samples'][0]['split'] = 'test' if data['samples'][0]['split'] == 'train' else 'train'
    with pytest.raises(ValueError):
        ProbeTask(data)
    data = task.to_json()
    data['samples'][0]['family_id'] = data['samples'][-1]['family_id']
    with pytest.raises(ValueError):
        ProbeTask(data)
    data = task.to_json()
    row = data['samples'][0]
    row.update(present=False, target=None, unavailability={'code': 'off_pitch', 'reason': 'fixture'})
    masked = ProbeTask(data)
    fit = fit_reference(masked, bundle, budgets=BUDGETS)
    assert row['id'] not in fit['selected_ids'][row['split']]
    with pytest.raises(ValueError, match='Insufficient'):
        fit_reference(masked, bundle, budgets={'train': 10000, 'validation': 1, 'test': 1})
    for mutation in ('mask', 'dimension'):
        corrupt = deepcopy(data)
        if mutation == 'mask':
            corrupt['samples'][0]['present'] = 1
        else:
            corrupt['samples'][0]['raw'].append(0.0)
        with pytest.raises(ValueError):
            ProbeTask(corrupt)


def test_reference_cli_fixed_seed_and_json_files(synthetic, tmp_path):
    task, bundle = synthetic
    task_path, embeddings_path = tmp_path / 'task.json', tmp_path / 'embeddings.json'
    task_path.write_bytes(encode_json(task.to_json()))
    embeddings_path.write_bytes(encode_json(bundle))
    cmd = [sys.executable, '-m', 'examples.lab.probe_reference', '--seed', '17']
    one = subprocess.check_output(cmd)
    assert subprocess.check_output(cmd) == one
    assert subprocess.check_output(cmd + ['--task', str(task_path), '--embeddings', str(embeddings_path)]) == one
    assert set(json.loads(one)['controls']) == set(CONTROLS)
