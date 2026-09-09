"""Research HTTP data, provenance and isolation over actual ReplayV1 files."""
import base64
from copy import deepcopy
import hashlib
import json
import pickle

import pytest

pytest.importorskip('flask')
from botbowl.lab.commands import Access, CommandGateway, SessionRegistry
from botbowl.lab.research import ResearchLimits, ResearchStore, pack_replay
from botbowl.lab.rendering import render_grid
from botbowl.lab.views import grid_view
from botbowl.web.research import create_app
from tests.lab.test_replays import record


@pytest.fixture
def research(tmp_path):
    game, manifest, _, _, _ = record(tmp_path, decisions=5)
    grants = {'viewer': Access('spectator'), 'player': Access('player', team='away'),
              'evaluator': Access('evaluator'),
              'editor': Access('evaluator', capabilities=frozenset({'snapshot', 'restore'}))}
    registry = SessionRegistry()
    gateway = CommandGateway(registry, lambda token: token if token in grants else None)
    store = ResearchStore(grants)
    app = create_app(gateway, store)
    source = tmp_path / 'replay'
    bundle = pack_replay(source)
    before = pickle.dumps(game)
    hashes = {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    yield app, store, bundle, manifest
    assert pickle.dumps(game) == before
    assert hashes == {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in source.rglob('*') if p.is_file()}
    store.close()
    registry.close()
    game.close()


def auth(principal='viewer'):
    return {'Authorization': 'Bearer ' + principal}


def upload(client, bundle):
    result = client.post('/research/replays', json=bundle, headers=auth())
    assert result.status_code == 201, result.json
    return result.json


def prediction(row, context, identity='forecast'):
    return {'schema_version': 1, 'kind': 'predicted', 'prediction_id': identity,
            'origin_family_id': row['origin_family_id'], 'branch_id': context['branch_id'],
            'parent_snapshot_id': 'declared-emission', 'model': {'model_id': 'artificial', 'version': 'v1'},
            'issued_at': context, 'available_history': {'through': context, 'references': []},
            'horizon': 2, 'output': {'probability': 0, 'missing': None, 'interval': [0, 1]},
            'metadata': {'note': '<img src=x onerror=alert(1)>'}, 'revision_of': None}


def test_navigation_search_values_geometry_and_isolation(research):
    app, store, bundle, manifest = research
    client = app.test_client()
    row = upload(client, bundle)
    prefix = '/research/replays/' + row['id']
    for decision in (0, 1, 3, 5, 1):
        frame = client.get(prefix + '/frame', query_string={'decision': decision, 'entity': 'home:0'}, headers=auth()).json
        assert frame['context']['decision_seq'] == decision
        assert frame['selected']['state'] == 'present'
        assert frame['selected']['value']['status']['moves'] == 0
        assert base64.b64decode(frame['image']['rgb']) == render_grid(grid_view(frame['observation'])).tobytes()
        assert frame['image']['width'] == frame['observation']['geometry']['width'] * 12
        serialized = json.dumps(frame)
        for forbidden in ('rng_state', 'seed_spec', 'SnapshotFileV1', 'checkpoints', 'chance_policy'):
            assert forbidden not in serialized
    absent = client.get(prefix + '/frame?decision=0&entity=missing', headers=auth()).json
    assert absent['selected'] == {'state': 'absent_entity', 'value': None}
    assert not absent['observation']['players'][0]['position']['present']
    events = client.get(prefix + '/events', headers=auth()).json['events']
    assert events
    event = events[-1]
    selected = client.get(prefix + '/event?event=' + str(event['context']['event_seq']), headers=auth()).json
    assert selected['frame']['context']['decision_seq'] == selected['previous_decision']
    assert selected['next_decision'] == selected['previous_decision'] + 1
    assert selected['event'] == event
    found = client.get(prefix + '/events', query_string={'kind': event['kind']}, headers=auth()).json['events']
    assert event in found and all(e['kind'] == event['kind'] for e in found)
    assert client.get(prefix + '/events?entity=nonexistent', headers=auth()).json['events'] == []
    assert client.get(prefix + '/frame?decision=999', headers=auth()).status_code == 400
    # Reconnection gives the same inert resource IDs without replaying a command.
    assert client.get('/research/replays', headers=auth()).json['replays'][0]['id'] == row['id']
    assert store.summary(row['id'])['final'] == manifest['final_context']


def test_two_unequal_branches_permission_and_private_chance(research):
    app, store, bundle, _ = research
    client = app.test_client()
    row = upload(client, bundle)
    prefix = '/research/replays/' + row['id']
    original_upload = pack_replay(store.root / row['id'])
    for role in ('viewer', 'player', 'evaluator'):
        assert client.post(prefix + '/branches', data='not-json', headers=auth(role)).status_code == 403
        assert client.get(prefix + '/actions?decision=1', headers=auth(role)).status_code == 403
    actions = client.get(prefix + '/actions?decision=1', headers=auth('editor')).json['actions']
    assert len(actions) >= 2
    branches = []
    for horizon, action in zip((1, 2), actions):
        result = client.post(prefix + '/branches', json={'decision': 1, 'action': action, 'horizon': horizon}, headers=auth('editor'))
        assert result.status_code == 201, result.json
        branch = result.json
        assert branch['final']['decision_seq'] == 1 + horizon
        assert branch['provenance']['kind'] == 'simulated_alternative'
        assert branch['provenance']['initial_action'] == action
        assert branch['provenance']['policy'] == {'policy_id': 'first-legal', 'version': 'v1'}
        assert branch['end']['reason'] == 'branch_horizon'
        assert branch['provenance']['chance'] == {'mode': 'independent', 'natural': True}
        assert branch['origin_family_id'] == row['origin_family_id']
        assert branch['provenance']['parent'] == row['id']
        branches.append(branch)
    assert store.summary(row['id']) == row
    assert pack_replay(store.root / row['id']) == original_upload
    for branch in branches:
        frame = client.get('/research/replays/' + branch['id'] + '/frame?decision=1', headers=auth()).json
        assert frame['context']['branch_id'] == branch['id']
        assert frame['context']['decision_seq'] == 1


def test_immutable_predictions_late_emission_revisions_and_annotations(research):
    app, store, bundle, _ = research
    client = app.test_client()
    row = upload(client, bundle)
    prefix = '/research/replays/' + row['id']
    context = client.get(prefix + '/frame?decision=1', headers=auth()).json['context']
    record = prediction(row, context)
    payload = {'prediction': record, 'target_decision': 1, 'retrospective': False}
    result = client.post(prefix + '/predictions', json=payload, headers=auth())
    assert result.status_code == 201 and result.json['kind'] == 'model_prediction'
    assert result.json['record']['output']['probability'] == 0
    assert client.post(prefix + '/predictions', json=payload, headers=auth()).status_code == 400
    late = deepcopy(payload)
    late['prediction']['prediction_id'] = 'late'
    late['target_decision'] = 0
    assert client.post(prefix + '/predictions', json=late, headers=auth()).status_code == 400
    late['retrospective'] = True
    assert client.post(prefix + '/predictions', json=late, headers=auth()).json['kind'] == 'retrospective_analysis'
    revision = deepcopy(payload)
    revision['prediction'].update(prediction_id='revision', revision_of='forecast')
    revision['prediction']['output'] = 0.99
    assert client.post(prefix + '/predictions', json=revision, headers=auth()).status_code == 400
    future = deepcopy(payload)
    future['prediction']['prediction_id'] = 'future'
    future['prediction']['available_history']['through']['decision_seq'] = 4
    assert client.post(prefix + '/predictions', json=future, headers=auth()).status_code == 400
    for decision in (2, 4, 5):
        assert client.get(prefix + '/frame?decision=' + str(decision), headers=auth()).status_code == 200
    assert store.summary(row['id'])['predictions'][0]['record'] == record
    text = '<script>alert("human")</script>'
    assert client.post(prefix + '/annotations', json={'decision': 1, 'text': text}, headers=auth()).json == {
        'kind': 'human_annotation', 'decision': 1, 'text': text}


@pytest.mark.parametrize('damage', ['empty', 'truncated', 'pickle', 'traversal', 'extra', 'oversize'])
def test_untrusted_uploads_rejected_without_registration(research, damage):
    app, store, bundle, _ = research
    client = app.test_client()
    bad = deepcopy(bundle)
    if damage == 'empty':
        bad['files'] = {}
    elif damage == 'truncated':
        bad['files']['manifest.json'] = bad['files']['manifest.json'][:-4]
    elif damage == 'pickle':
        bad['files'] = {'replay.rep': 'cos\nsystem\n(S"touch /tmp/unsafe"\ntR.'}
    elif damage == 'traversal':
        bad['files']['../outside.json'] = '{}'
    elif damage == 'extra':
        bad['files']['unrelated.json'] = '{}'
    else:
        store.limits = ResearchLimits(max_bytes=16)
    response = client.post('/research/replays', json=bad, headers=auth())
    assert response.status_code in (400, 413), response.json
    assert not store.entries
    assert not list(store.root.iterdir())


def test_empty_confirmed_replay_and_transport_boundary(tmp_path):
    game, _, _, _, _ = record(tmp_path, decisions=0)
    store = ResearchStore({'viewer': Access('spectator')})
    registry = SessionRegistry()
    gateway = CommandGateway(registry, lambda token: 'viewer' if token == 'viewer' else None)
    try:
        app = create_app(gateway, store)
        client = app.test_client()
        row = upload(client, pack_replay(tmp_path / 'replay'))
        assert row['initial'] == row['final']
        prefix = '/research/replays/' + row['id']
        assert client.get(prefix + '/events', headers=auth()).json == {'events': []}
        assert client.get(prefix + '/frame?decision=0', headers=auth()).status_code == 200
        assert client.get('/research/replays').status_code == 401
        assert client.get('/research/replays/missing/frame?decision=0', headers=auth()).status_code == 404
        assert client.get('/research/replays', headers={**auth(), 'Host': 'evil.example'}).status_code == 403
        assert client.get('/research/replays', headers={**auth(), 'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/research/replays', environ_base={'REMOTE_ADDR': '192.0.2.1'}, headers=auth()).status_code == 403
        assert client.get('/research/replays', headers={**auth(), 'Origin': 'http://localhost'}).status_code == 200
        for asset in ('', 'viewer.js', 'viewer.css'):
            response = client.get('/research/' + asset)
            assert response.status_code == 200
            assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
        assert client.get('/research/../../etc/passwd', headers=auth()).status_code == 404
    finally:
        store.close()
        registry.close()
        game.close()
