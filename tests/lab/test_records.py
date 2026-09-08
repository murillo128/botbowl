"""Closed schemas, causal corruption rejection and inert bounded storage."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import math

import pytest

from botbowl.lab.recording import EpisodeReader, RecordingError
from botbowl.lab.records import (EpisodeManifestV1, EventV1, RecordError, TransitionV1,
                                decode_json, encode_json, validate_episode, validate_row)
from tests.baseline import progress_action
from tests.lab.test_recording import record_game


@pytest.fixture
def episode(tmp_path):
    game, recorder = record_game(tmp_path)
    recorder.advance(progress_action(game))
    recorder.advance(progress_action(game))
    recorder.finish(truncation_reason='fixture_limit')
    return EpisodeReader(tmp_path, 'episode').read_episode()


def record_examples(episode):
    return [(EpisodeManifestV1, episode['manifest']),
            (TransitionV1, episode['channels']['transitions'][0]),
            (EventV1, episode['channels']['events'][0])]


def test_data_only_validated_constructors_round_trip_and_frozen_copies(episode):
    for cls, source in record_examples(episode):
        record = cls(source)
        copied = record.to_json()
        assert copied == source and copied is not source
        copied['schema_version'] = 900
        assert record.to_json()['schema_version'] == 1
        assert cls.from_json(decode_json(encode_json(record.to_json()))) == record
        with pytest.raises(FrozenInstanceError):
            record._encoded = b'{}'


@pytest.mark.parametrize('version', [0, 2, True, '1', 1.0, None])
def test_unknown_or_wrong_dtype_versions_rejected(episode, version):
    for cls, source in record_examples(episode):
        source['schema_version'] = version
        with pytest.raises(RecordError):
            cls(source)


def test_all_required_and_extra_fields_fail_closed(episode):
    for cls, source in record_examples(episode):
        for key in source:
            broken = {k: v for k, v in source.items() if k != key}
            with pytest.raises(RecordError):
                cls(broken)
        with pytest.raises(RecordError):
            cls({**source, 'module': 'os.system'})


@pytest.mark.parametrize('value', [math.inf, -math.inf, math.nan, 2**256, b'pickle', (1, 2)])
def test_non_finite_non_json_and_large_integer_payloads_rejected(value):
    with pytest.raises(RecordError):
        encode_json({'payload': value})


@pytest.mark.parametrize('payload', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
                                     b'{"x":1e999}', b'\xff', b'{', b'null\nnull'])
def test_malformed_duplicate_keys_and_non_utf8_bytes_rejected(payload):
    with pytest.raises(RecordError):
        decode_json(payload)


def test_limits_depth_size_and_no_execution_hooks(tmp_path):
    marker = tmp_path / 'executed'

    class Executable:
        def __reduce__(self):
            marker.touch()
            return (str, ('bad',))

        def to_json(self):
            marker.touch()
            return {}

        def __deepcopy__(self, memo):
            marker.touch()
            return {}

    with pytest.raises(RecordError):
        EventV1({'data': Executable()})
    # A pickle GLOBAL/REDUCE payload is bytes, never loaded as pickle.
    with pytest.raises(RecordError):
        decode_json(b"cos\nsystem\n(S'touch " + str(marker).encode() + b"'\ntR.")
    assert not marker.exists()
    nested = None
    for _ in range(42):
        nested = [nested]
    with pytest.raises(RecordError):
        encode_json(nested)
    with pytest.raises(RecordError):
        decode_json(b'[' * 2000 + b'0' + b']' * 2000)
    with pytest.raises(RecordError):
        encode_json('x' * (4 * 1024 * 1024))


def test_null_absent_false_and_zero_are_distinct(episode):
    transition = episode['channels']['transitions'][0]
    assert transition['macro_id'] is transition['primitive_order'] is None
    assert transition['before']['event_seq'] == transition['before']['decision_seq'] == 0
    assert transition['before']['activation_seq'] is None
    obs = episode['channels']['primary'][0]
    assert obs['channel']['data']['match']['round'] == 0
    assert obs['channel']['data']['match']['drive'] == {'value': None, 'present': False}
    validate_row('primary', obs)
    obs['channel']['data']['match']['drive'] = {'value': 0, 'present': True}
    validate_row('primary', obs)
    obs['channel']['data']['match']['drive']['present'] = False
    with pytest.raises(ValueError):
        validate_row('primary', obs)


@pytest.mark.parametrize('field', ['event_start', 'event_stop', 'pre_observation', 'post_observation'])
def test_transition_indices_reject_boolean_and_float(episode, field):
    transition = episode['channels']['transitions'][0]
    for value in (True, 1.0, -1, 2**63):
        transition[field] = value
        with pytest.raises(RecordError):
            TransitionV1(transition)


@pytest.mark.parametrize('corruption', ['event_id', 'decision_id', 'event_gap', 'event_duplicate',
                                       'event_order', 'wrong_branch', 'wrong_cause', 'event_range',
                                       'missing_observation', 'actor', 'source', 'scenario',
                                       'observation_context', 'post_actor', 'continuity', 'unknown_entity',
                                       'premature_end', 'end_reason', 'unknown_payload_version'])
def test_whole_episode_rejects_causal_corruption(episode, corruption):
    rows, manifest = episode['channels'], episode['manifest']
    event, transition = rows['events'][0], rows['transitions'][0]
    if corruption == 'event_id':
        event['event_id'][2] += 1
    elif corruption == 'decision_id':
        transition['transition_id'][2] += 1
    elif corruption == 'event_gap':
        rows['events'].pop(0)
        manifest['files']['events']['rows'] -= 1
    elif corruption == 'event_duplicate':
        rows['events'].append(deepcopy(event))
        manifest['files']['events']['rows'] += 1
    elif corruption == 'event_order':
        rows['events'][0], rows['events'][1] = rows['events'][1], rows['events'][0]
    elif corruption == 'wrong_branch':
        event['context']['branch_id'] = event['event_id'][1] = 'foreign'
    elif corruption == 'wrong_cause':
        event['decision_seq'] = None
    elif corruption == 'event_range':
        transition['event_stop'] -= 1
    elif corruption == 'missing_observation':
        transition['post_observation'] = 999
    elif corruption == 'actor':
        transition['actor_id'] = 'home'
    elif corruption == 'source':
        transition['source_family'] = 'different'
    elif corruption == 'scenario':
        transition['scenario_id'] = 'different'
    elif corruption == 'observation_context':
        rows['primary'][transition['post_observation'] - 1]['context']['event_seq'] = 0
    elif corruption == 'post_actor':
        transition['next_actor_id'] = 'home'
    elif corruption == 'continuity':
        # Split a shared boundary into two different observations at identical time.
        row = deepcopy(rows['primary'][transition['post_observation'] - 1])
        row['observation_id'] = len(rows['primary']) + 1
        row['channel']['data']['teams'][0]['score'] += 1
        rows['primary'].append(row)
        manifest['files']['primary']['rows'] += 1
        transition['post_observation'] = row['observation_id']
    elif corruption == 'unknown_entity':
        event['data']['player_id'] = 'home:999'
    elif corruption == 'premature_end':
        transition['end'] = {'kind': 'truncated', 'reason': 'too_early'}
    elif corruption == 'end_reason':
        manifest['end']['reason'] = 'different'
    elif corruption == 'unknown_payload_version':
        event['payload_version'] = 2
    with pytest.raises(ValueError):
        validate_episode(manifest, rows)


@pytest.mark.parametrize('name', ['evaluation', 'privileged', 'control', 'derived'])
def test_nested_channel_injection_into_primary_is_rejected(episode, name):
    row = episode['channels']['primary'][0]
    row['channel']['data'][name] = {'oracle': 'CANARY'}
    with pytest.raises(ValueError):
        validate_row('primary', row)


def test_profile_forbidden_paths_and_version_rejected_before_loading(tmp_path, episode, monkeypatch):
    manifest_path = tmp_path / 'episode/manifest.json'
    manifest = episode['manifest']
    manifest['profile'] = {'schema_version': 1, 'name': 'custom', 'fields': [
        {'path': 'privileged.rng', 'type': 'string'}], 'include_action_mask': False}
    manifest_path.write_bytes(encode_json(manifest))
    with pytest.raises(RecordError):
        EpisodeReader(tmp_path, 'episode')
    manifest['profile']['fields'] = []
    manifest['profile']['schema_version'] = 2
    manifest_path.write_bytes(encode_json(manifest))
    with pytest.raises(RecordError):
        EpisodeReader(tmp_path, 'episode')


@pytest.mark.parametrize('path', ['../privileged/audit.jsonl', 'privileged/audit.jsonl',
                                 'inputs/primary.pkl', '/tmp/data.jsonl'])
def test_manifest_path_redirection_rejected(tmp_path, episode, path):
    manifest = episode['manifest']
    manifest['files']['primary']['path'] = path
    (tmp_path / 'episode/manifest.json').write_bytes(encode_json(manifest))
    with pytest.raises(RecordError):
        EpisodeReader(tmp_path, 'episode')


def test_rehashed_corruption_still_fails_full_reader(tmp_path, episode):
    path = tmp_path / 'episode/events/events.jsonl'
    rows = episode['channels']['events']
    rows[0]['decision_seq'] = None
    payload = b''.join(encode_json(row) + b'\n' for row in rows)
    path.write_bytes(payload)
    manifest = episode['manifest']
    manifest['files']['events'].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    (tmp_path / 'episode/manifest.json').write_bytes(encode_json(manifest))
    with pytest.raises(RecordError, match='causing'):
        EpisodeReader(tmp_path, 'episode').read_episode()


def test_unfulfilled_profile_cannot_be_confirmed(tmp_path):
    from botbowl.lab.channels import input_profile
    game, recorder = record_game(tmp_path, profile=input_profile('enriched'))
    recorder.advance(progress_action(game))
    with pytest.raises(RecordingError):
        recorder.finish(truncation_reason='fixture_limit')
    assert not (tmp_path / 'episode').exists()


def test_capture_size_limit_latches_failure_without_second_action(tmp_path, monkeypatch):
    game, recorder = record_game(tmp_path)
    monkeypatch.setattr('botbowl.lab.recording.MAX_EPISODE_BYTES', recorder._bytes + 1)
    with pytest.raises(RecordingError):
        recorder.advance(progress_action(game))
    assert recorder.failed
    count = recorder.timeline.context.decision_seq
    with pytest.raises(RecordingError):
        recorder.advance(progress_action(game))
    assert recorder.timeline.context.decision_seq == count
    assert not (tmp_path / 'episode').exists()


def test_large_declared_counts_fail_before_allocating_sequences(episode):
    episode['manifest']['final_context']['event_seq'] = 2**62
    with pytest.raises(RecordError, match='sequence limit'):
        validate_episode(episode['manifest'], episode['channels'])
