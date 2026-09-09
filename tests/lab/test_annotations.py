"""External numeric/text data never executes and must align with a real replay."""
from copy import deepcopy
import json
import pickle
import sys

import numpy as np
import pytest

from botbowl.lab.annotations import AnnotationBundleV1, MAX_BUNDLE_BYTES, validate_replay
from botbowl.lab.replays import ReplayReader
from tests.lab.test_replays import record


def annotation_bundle(context, family='fixture-family', kind='entity_score'):
    values = [[20], [10]]
    if kind == 'projection_2d':
        values = [[2, 3], [1, 4]]
    fitting = {'split_manifest_id': 'artificial-split', 'split_version': 'v1',
               'fit_partitions': ['train'], 'evaluation_partition': 'test', 'held_out': True}
    return {'format': 'AnnotationBundleV1', 'schema_version': 1, 'bundle_id': 'artificial', 'items': [{
        'annotation_id': 'values', 'episode_id': context['episode_id'], 'origin_family_id': family,
        'branch_id': context['branch_id'], 'target': deepcopy(context), 'target_kind': 'decision',
        'entity_ids': ['away:0', 'home:0'], 'kind': kind,
        'method': {'method_id': 'artificial', 'version': 'v1'}, 'issued_at': deepcopy(context),
        'horizon': 0, 'retrospective': False,
        'provenance': {'source': 'Artificial fixture', 'access': 'public',
                       'fitting': fitting if kind in ('projection_2d', 'probe_output') else None},
        'data': '<script>window.annotationInjected=true</script>' if kind == 'human_note' else {
            'format': 'numeric_json', 'shape': [2, len(values[0])], 'dtype': 'float64',
            'nbytes': len(values) * len(values[0]) * 8, 'values': values}}]}


@pytest.fixture
def replay(tmp_path):
    game, manifest, _, _, _ = record(tmp_path, decisions=5)
    reader = ReplayReader(tmp_path, 'replay')
    before = pickle.dumps(game)
    try:
        yield reader, manifest
        assert pickle.dumps(game) == before
    finally:
        game.close()


def boundary(reader, decision):
    game = reader.seek_decision(decision)
    try:
        return game.timeline.context.to_json()
    finally:
        game.close()


@pytest.mark.parametrize('kind', ['embedding', 'probe_output', 'projection_2d', 'entity_score', 'human_note'])
def test_valid_types_round_trip_alignment_and_immutability(replay, tmp_path, kind):
    reader, manifest = replay
    data = annotation_bundle(boundary(reader, 1), kind=kind)
    bundle = validate_replay(AnnotationBundleV1(data), reader)
    path = tmp_path / 'bundle.json'
    path.write_text(json.dumps(data))
    assert AnnotationBundleV1.load(path) == bundle
    data['items'][0]['method']['version'] = 'mutated'
    assert bundle.to_json()['items'][0]['method']['version'] == 'v1'
    if kind != 'human_note':
        aligned = bundle.aligned('values', ['home:0', 'away:0'])
        assert aligned[:, 0].tolist() == ([1, 2] if kind == 'projection_2d' else [10, 20])
        aligned[:] = -1
        assert np.all(bundle.aligned('values', ['home:0']) >= 0)
        with pytest.raises(ValueError):
            bundle.aligned('values', ['missing'])


@pytest.mark.parametrize('damage', ['version', 'empty', 'huge', 'nan', 'inf', 'object', 'dtype',
    'shape', 'bytes', 'ragged', 'bool', 'fractional_int', 'overflow', 'entities', 'duplicate',
    'horizon', 'negative', 'method', 'projection_shape', 'fit_missing', 'fit_test', 'fit_overlap'])
def test_invalid_schema_matrices_and_fitting(replay, damage):
    reader, _ = replay
    data = annotation_bundle(boundary(reader, 1))
    item = data['items'][0]
    matrix = item['data']
    if damage == 'version': data['schema_version'] = 2
    elif damage == 'empty': matrix['values'] = []; matrix['shape'][0] = 0
    elif damage == 'huge': matrix['shape'] = [2, 2**62]
    elif damage == 'nan': matrix['values'][0][0] = float('nan')
    elif damage == 'inf': matrix['values'][0][0] = float('inf')
    elif damage == 'object': matrix['values'][0][0] = {'module': 'sentinel'}
    elif damage == 'dtype': matrix['dtype'] = 'object'
    elif damage == 'shape': matrix['shape'][0] = 1
    elif damage == 'bytes': matrix['nbytes'] = 17
    elif damage == 'ragged': matrix['values'][0].append(1)
    elif damage == 'bool': matrix['values'][0][0] = True
    elif damage == 'fractional_int': matrix['dtype'] = 'int64'; matrix['values'][0][0] = 0.5
    elif damage == 'overflow': matrix['dtype'] = 'float32'; matrix['nbytes'] = 8; matrix['values'][0][0] = 1e100
    elif damage == 'entities': item['entity_ids'] = []
    elif damage == 'duplicate': item['entity_ids'] = ['home:0', 'home:0']
    elif damage == 'horizon': item['horizon'] = 2
    elif damage == 'negative': item['horizon'] = -1
    elif damage == 'method': item['method']['execute'] = 'sentinel.py'
    elif damage == 'projection_shape': item['kind'] = 'projection_2d'
    else:
        data = annotation_bundle(boundary(reader, 1), kind='projection_2d')
        fit = data['items'][0]['provenance']['fitting']
        if damage == 'fit_missing': data['items'][0]['provenance']['fitting'] = None
        elif damage == 'fit_test': fit['fit_partitions'] = ['test']
        else: fit['evaluation_partition'] = 'train'
    with pytest.raises(ValueError): AnnotationBundleV1(data)


@pytest.mark.parametrize('damage', ['family', 'branch', 'episode', 'entity', 'time', 'target', 'future'])
def test_replay_membership(replay, damage):
    reader, _ = replay
    data = annotation_bundle(boundary(reader, 1))
    item = data['items'][0]
    if damage == 'family': item['origin_family_id'] = 'unknown'
    elif damage in ('branch', 'episode'):
        key = damage + '_id'
        item[key] = item['target'][key] = item['issued_at'][key] = 'unknown'
    elif damage == 'entity': item['entity_ids'][0] = 'unknown'
    elif damage == 'time': item['issued_at']['round'] = 42
    elif damage == 'target': item['target']['event_seq'] += 1
    else: item['target']['decision_seq'] = 99; item['horizon'] = 98
    with pytest.raises(ValueError): validate_replay(AnnotationBundleV1(data), reader)


def test_late_prediction_retrospective_and_event_membership(replay):
    reader, _ = replay
    data = annotation_bundle(boundary(reader, 1))
    item = data['items'][0]
    item['issued_at'] = boundary(reader, 2)
    item['horizon'] = 1
    with pytest.raises(ValueError): AnnotationBundleV1(data)
    item['retrospective'] = True
    validate_replay(AnnotationBundleV1(data), reader)
    event = reader.seek_event(1)
    try:
        item['target_kind'] = 'event'
        item['target'] = event.event['context']
        item['issued_at'] = boundary(reader, event.previous_decision)
        item['horizon'] = abs(item['target']['decision_seq'] - item['issued_at']['decision_seq'])
        item['retrospective'] = False
        validate_replay(AnnotationBundleV1(data), reader)
        item['target']['round'] = 42
        with pytest.raises(ValueError): validate_replay(AnnotationBundleV1(data), reader)
    finally:
        event.game.close()


def test_untrusted_files_and_module_script_sentinels(replay, tmp_path, monkeypatch):
    reader, _ = replay
    marker = tmp_path / 'executed'
    module = tmp_path / 'annotation_sentinel.py'
    module.write_text('from pathlib import Path\nPath(%r).touch()\n' % str(marker))
    monkeypatch.syspath_prepend(str(tmp_path))
    data = annotation_bundle(boundary(reader, 1))
    data['items'][0]['method']['method_id'] = 'annotation_sentinel'
    data['items'][0]['provenance']['source'] = str(module) + '; touch ' + str(marker)
    validate_replay(AnnotationBundleV1(data), reader)
    assert 'annotation_sentinel' not in sys.modules
    assert not marker.exists()
    for payload in (b'{"format":', b'PK\x03\x04truncated', b' ' * (MAX_BUNDLE_BYTES + 1)):
        path = tmp_path / 'bad.json'
        path.write_bytes(payload)
        with pytest.raises(ValueError): AnnotationBundleV1.load(path)
    class Evil:
        def __reduce__(self):
            return eval, ("__import__('pathlib').Path(%r).touch()" % str(marker),)
    path = tmp_path / 'pickle.json'
    path.write_bytes(pickle.dumps(Evil()))
    with pytest.raises(ValueError): AnnotationBundleV1.load(path)
    path = tmp_path / 'object.npz'
    np.savez_compressed(path, values=np.array([Evil()], dtype=object))
    with pytest.raises(ValueError): AnnotationBundleV1.load(path)
    assert not marker.exists()


def test_same_decision_late_event_and_exploratory_test_fit(replay):
    reader, _ = replay
    data = annotation_bundle(boundary(reader, 1), kind='projection_2d')
    item = data['items'][0]
    item['target']['event_seq'] = 0
    item['issued_at']['event_seq'] = 1
    with pytest.raises(ValueError, match='Late emission'): AnnotationBundleV1(data)
    data = annotation_bundle(boundary(reader, 1), kind='projection_2d')
    data['items'][0]['provenance']['fitting'].update(fit_partitions=['test'], held_out=False)
    validate_replay(AnnotationBundleV1(data), reader)
    data['items'][0]['data']['values'][0][0] = 2**53
    with pytest.raises(ValueError, match='lossless browser'): AnnotationBundleV1(data)


def test_team_entities_use_existing_public_ids(replay):
    reader, _ = replay
    data = annotation_bundle(boundary(reader, 1))
    data['items'][0]['entity_ids'] = ['away', 'home']
    bundle = validate_replay(AnnotationBundleV1(data), reader)
    assert bundle.aligned('values', ['home', 'away']).tolist() == [[10], [20]]
