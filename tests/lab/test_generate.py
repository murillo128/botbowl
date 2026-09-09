"""Headless dataset contracts, supplied-action replay and failure boundaries."""
from dataclasses import replace
import json

import pytest

from botbowl.lab.generate import (JobConfig, ReferencePolicy, build_plan, execute_plan,
                                  generate, replay_episode, validate_dataset)
from botbowl.lab.recording import EpisodeReader, JsonlEpisodeWriter
from botbowl.lab.session import SimulationSession


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('policy', ['random', 'scripted'])
def test_reproducible_plan_and_supplied_actions(tmp_path, side, policy, monkeypatch):
    config = JobConfig(str(tmp_path / 'first'), episodes=2, side=side,
                       home_policy=policy, away_policy=policy, master_seed=17,
                       scenario='pickup', max_decisions=8)
    plan = build_plan(config)
    first = execute_plan(plan, config.output)
    second = execute_plan(plan, str(tmp_path / 'second'))
    assert first == second
    assert validate_dataset(config.output) == first
    for entry in first['episodes']:
        episode_id = entry['episode_id']
        reader = EpisodeReader(config.output, episode_id)
        episode = reader.read_episode()
        transitions = episode['channels']['transitions']
        assert transitions and {row['actor_id'] for row in transitions} <= {'home', 'away'}
        assert transitions[0]['actor_id'] == side
        assert all(row['source_family'] == entry['origin_family_id'] for row in transitions)
        assert all(row['action']['actor_id'] == row['actor_id'] for row in transitions)
        primary = json.dumps(reader.read_channels(['primary']))
        assert all(word not in primary for word in ('seed', 'oracle', 'generation', 'future'))
        assert 'privileged' not in json.dumps(reader.read_inputs())
        actions = [row['action'] for row in transitions]
        monkeypatch.setattr(ReferencePolicy, 'act', lambda *args: pytest.fail('Replay invoked a policy'))
        replayed = replay_episode(config.output, episode_id, str(tmp_path / ('replay-' + episode_id)), actions)
        assert replayed['episodes'] == [entry]


@pytest.mark.parametrize('horizon,budget,reason,count', [
    (0, 8, 'fragment_horizon', 0), (None, 0, 'decision_budget', 0),
    (1, 8, 'fragment_horizon', 1), (None, 1, 'decision_budget', 1),
])
def test_empty_and_bounded_fragments(tmp_path, horizon, budget, reason, count):
    dataset = generate(JobConfig(str(tmp_path / 'data'), scenario='match',
                                 horizon=horizon, max_decisions=budget))
    end = dataset['episodes'][0]['end']
    assert end == dict(reason=reason, terminated=False, truncated=True,
                      scenario_terminal=False, scenario_success=None, decisions=count)
    validate_dataset(tmp_path / 'data')
    rows = EpisodeReader(tmp_path / 'data', 'episode-000000').read_episode()['channels']
    assert len(rows['transitions']) == count
    assert rows['primary']


def test_scenario_terminal_is_not_match_completion_or_budget(tmp_path):
    dataset = generate(JobConfig(str(tmp_path / 'data'), max_decisions=1))
    end = dataset['episodes'][0]['end']
    assert end['reason'] == 'scenario_terminal'
    assert end['scenario_terminal'] and not end['terminated'] and not end['truncated']
    assert end['scenario_success'] is False  # The lifecycle script ends the turn.
    assert EpisodeReader(tmp_path / 'data', 'episode-000000').manifest['end'] == {
        'kind': 'truncated', 'reason': 'scenario_terminal'}


def test_natural_match_and_both_actors(tmp_path):
    dataset = generate(JobConfig(str(tmp_path / 'data'), scenario='match', size=1,
                                 home_policy='scripted', away_policy='scripted',
                                 max_decisions=256))
    end = dataset['episodes'][0]['end']
    assert end['reason'] == 'game_over' and end['terminated'] and not end['truncated']
    episode = EpisodeReader(tmp_path / 'data', 'episode-000000').read_episode()
    assert {row['actor_id'] for row in episode['channels']['transitions']} == {'home', 'away'}
    replayed = replay_episode(tmp_path / 'data', 'episode-000000', str(tmp_path / 'replay'))
    assert replayed['episodes'] == dataset['episodes']


@pytest.mark.parametrize('change', [
    {'episodes': 0}, {'episodes': True}, {'master_seed': -1}, {'master_seed': None},
    {'scenario': 'os.system'}, {'home_policy': 'pickle'}, {'input_profile': 'oracle'},
    {'size': 2}, {'max_decisions': -1}, {'max_steps': 0}, {'distance': True},
    {'horizon': 33}, {'side': 'spectator'}, {'scenario': 'pass_receive', 'size': 1},
])
def test_invalid_config_has_no_side_effects(tmp_path, change):
    with pytest.raises(ValueError):
        generate(JobConfig(str(tmp_path / 'data'), **change))
    assert not list(tmp_path.iterdir())


def test_plan_tampering_and_destination_protection(tmp_path):
    config = JobConfig(str(tmp_path / 'data'))
    plan = build_plan(config)
    plan['episodes'][0]['seed_plan']['sources']['engine']['master_seed'] += 1
    with pytest.raises(ValueError):
        execute_plan(plan, config.output)
    assert not list(tmp_path.iterdir())
    generate(config)
    before = (tmp_path / 'data' / 'dataset.json').read_bytes()
    with pytest.raises(FileExistsError):
        generate(config)
    assert (tmp_path / 'data' / 'dataset.json').read_bytes() == before


@pytest.mark.parametrize('failure', ['policy', 'invalid_action', 'write', 'hash', 'cancel'])
def test_failures_close_every_resource_and_never_confirm(tmp_path, monkeypatch, failure):
    policies, sessions, writers = [], [], []
    policy_init, session_init, writer_init = ReferencePolicy.__init__, SimulationSession.__init__, JsonlEpisodeWriter.__init__

    def own_policy(self, *args, **kwargs):
        policy_init(self, *args, **kwargs)
        policies.append(self)

    def own_session(self, *args, **kwargs):
        session_init(self, *args, **kwargs)
        sessions.append(self)

    def own_writer(self, *args, **kwargs):
        writer_init(self, *args, **kwargs)
        writers.append(self)

    monkeypatch.setattr(ReferencePolicy, '__init__', own_policy)
    monkeypatch.setattr(SimulationSession, '__init__', own_session)
    monkeypatch.setattr(JsonlEpisodeWriter, '__init__', own_writer)

    def fail(*args, **kwargs):
        if failure == 'cancel':
            raise KeyboardInterrupt()
        raise OSError('secret error message must not be persisted')

    if failure == 'write':
        monkeypatch.setattr(JsonlEpisodeWriter, 'flush', fail)
    elif failure == 'hash':
        monkeypatch.setattr('botbowl.lab.generate._episode_hash', fail)
    elif failure == 'invalid_action':
        monkeypatch.setattr(ReferencePolicy, 'act', lambda *args: {'type': 'UNKNOWN'})
    else:
        monkeypatch.setattr(ReferencePolicy, 'act', fail)
    with pytest.raises((RuntimeError, ValueError, OSError, KeyboardInterrupt)):
        generate(JobConfig(str(tmp_path / 'data'), scenario='match'))
    assert policies and all(policy.closed for policy in policies)
    # reset constructs a temporary facade around the same owned Game; inspect
    # only the session registered before generator recorder attachment.
    assert sessions[-1].closed
    assert writers and all(writer.closed for writer in writers)
    assert all(all(stream.closed for stream in writer._streams.values()) for writer in writers)
    assert not (tmp_path / 'data' / 'episode-000000').exists()
    assert not (tmp_path / 'data' / 'dataset.json').exists()
    diagnostic = (tmp_path / 'data' / 'episode-000000.failure.json').read_text()
    assert 'failed' in diagnostic and 'secret' not in diagnostic


def test_execution_budget_keeps_pending_transition(tmp_path):
    dataset = generate(JobConfig(str(tmp_path / 'data'), scenario='match', max_steps=1))
    end = dataset['episodes'][0]['end']
    assert end['reason'] == 'execution_budget' and end['truncated']
    rows = EpisodeReader(tmp_path / 'data', 'episode-000000').read_episode()['channels']
    assert rows['transitions'][-1]['status'] == 'pending'
    assert rows['diagnostics']
    assert replay_episode(tmp_path / 'data', 'episode-000000', str(tmp_path / 'replay'))['episodes'] == dataset['episodes']


def test_replay_rejects_wrong_length_and_outcome_tampering(tmp_path):
    generate(JobConfig(str(tmp_path / 'data')))
    with pytest.raises(ValueError):
        replay_episode(tmp_path / 'data', 'episode-000000', str(tmp_path / 'replay'), [])
    path = tmp_path / 'data' / 'dataset.json'
    data = json.loads(path.read_text())
    data['episodes'][0]['end']['terminated'] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        validate_dataset(tmp_path / 'data')


def test_policy_seed_independent_of_scenario_engine_and_other_seat(tmp_path):
    config = JobConfig(str(tmp_path / 'data'))
    first = build_plan(config)['episodes'][0]
    second = build_plan(replace(config, home_policy='random'))['episodes'][0]
    assert first['recipe'] == second['recipe']
    assert first['origin_family_id'] == second['origin_family_id']
    for purpose in ('engine', 'scenario', 'scenario-layout', 'policy-away', 'observation'):
        assert first['seed_plan']['sources'][purpose] == second['seed_plan']['sources'][purpose]


@pytest.mark.parametrize('scenario', ['movement', 'pickup', 'pass_receive', 'block',
                                     'possession_recovery', 'touchdown'])
def test_all_registered_scenarios_record_valid_synthetic_prefix(tmp_path, scenario):
    result = generate(JobConfig(str(tmp_path / 'data'), scenario=scenario,
                                home_policy='random', max_decisions=3))
    assert validate_dataset(tmp_path / 'data') == result
    episode = EpisodeReader(tmp_path / 'data', 'episode-000000').read_episode()
    first = episode['channels']['events'][0]
    assert first['kind'] == 'half_started' and first['context']['round'] == 0
    assert first['decision_seq'] is None


def test_recording_attachment_rejects_used_session_and_reset(tmp_path):
    from botbowl.lab.generate import _open_session
    config = JobConfig(str(tmp_path / 'data'))
    entry = build_plan(config)['episodes'][0]
    session = _open_session(entry, config)
    metadata = dict(episode_id=entry['episode_id'], source_family=entry['origin_family_id'],
                    scenario_id=config.scenario, policies=entry['policies'], seed_plan=entry['seed_plan'])
    try:
        recorder = session.start_recording(tmp_path, 'record', **metadata)
        with pytest.raises(ValueError):
            session.start_recording(tmp_path, 'duplicate', **metadata)
        with pytest.raises(ValueError):
            session._session.reset(session._session._config, session._session._seed_plan)
        recorder.close()
        recorder.close()
    finally:
        session.close()
        session.close()
    assert not (tmp_path / 'record').exists()
    assert not (tmp_path / 'duplicate.partial').exists()
    other = _open_session(entry, config)
    try:
        legal = other.legal_actions()
        other.step(legal.actions[0], legal.state_revision)
        with pytest.raises(ValueError):
            other.start_recording(tmp_path, 'late', **metadata)
    finally:
        other.close()
    assert not (tmp_path / 'late.partial').exists()


def test_cancel_during_synthetic_construction_closes_candidate(tmp_path, monkeypatch):
    import botbowl.lab.scenarios as scenarios
    candidates = []

    def cancel(session, spec):
        candidates.append(session)
        raise KeyboardInterrupt()

    monkeypatch.setattr(scenarios, '_construct', cancel)
    with pytest.raises(KeyboardInterrupt):
        generate(JobConfig(str(tmp_path / 'data')))
    assert len(candidates) == 1 and candidates[0].closed
    assert not (tmp_path / 'data' / 'episode-000000').exists()
    assert (tmp_path / 'data' / 'episode-000000.failure.json').exists()


def test_imported_snapshot_cannot_invent_recording_start(tmp_path):
    from botbowl.lab.generate import _open_session
    config = JobConfig(str(tmp_path / 'data'), scenario='match')
    entry = build_plan(config)['episodes'][0]
    original = _open_session(entry, config)
    imported = SimulationSession.from_snapshot(original.snapshot())
    try:
        with pytest.raises(ValueError, match='original reset observation'):
            imported.start_recording(tmp_path, 'imported', episode_id=entry['episode_id'],
                source_family=entry['origin_family_id'], scenario_id=config.scenario,
                policies=entry['policies'], seed_plan=entry['seed_plan'])
        assert not (tmp_path / 'imported.partial').exists()
    finally:
        imported.close()
        original.close()


def test_large_valid_episode_hashes_validates_and_replays(tmp_path):
    import hashlib
    from botbowl.lab.records import MAX_EPISODE_BYTES, MAX_RECORD_BYTES, RecordError, encode_json

    destination = tmp_path / 'data'
    generated = generate(JobConfig(str(destination), scenario='match', size=11,
                                   home_policy='random', away_policy='random', max_decisions=256))
    episode = EpisodeReader(destination, 'episode-000000').read_episode()
    transitions = episode['channels']['transitions']
    assert len(transitions) == 256
    canonical = json.dumps(episode, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, separators=(',', ':')).encode('utf-8')
    assert MAX_RECORD_BYTES < len(canonical) < MAX_EPISODE_BYTES
    assert generated['episodes'][0]['semantic_sha256'] == hashlib.sha256(canonical).hexdigest()
    # The individual-record encoder must retain its existing bounds.
    with pytest.raises(RecordError, match='JSON record byte limit'):
        encode_json(episode)
    assert not list(destination.glob('*.failure.json'))
    assert not list(destination.glob('*.partial'))
    assert validate_dataset(destination) == generated
    replayed = replay_episode(destination, 'episode-000000', str(tmp_path / 'replay'),
                              [row['action'] for row in transitions])
    assert replayed['episodes'] == generated['episodes']
    assert validate_dataset(tmp_path / 'replay') == replayed


def test_streaming_episode_hash_preserves_existing_canonical_bytes():
    from botbowl.lab.generate import _episode_hash, _hash

    # Unicode, floating point and nested ordering must retain V1 hash semantics.
    episode = {'manifest': {'title': 'Bowl é 🏈', 'number': 1.25},
               'channels': {'empty': [], 'rows': [{'z': None, 'a': [True, 0, -3]}]}}
    assert _episode_hash(episode) == _hash(episode)


@pytest.mark.parametrize('legacy', [False, True])
def test_maximum_batch_plan_roundtrip_and_hash(tmp_path, legacy):
    import hashlib
    from botbowl.lab.generate import (_atomic_json, _encode_job_json, _job_hash,
                                      _read_job_json, _validate_plan)
    from botbowl.lab.records import RecordError, encode_json

    config = JobConfig(str(tmp_path / 'data'), episodes=1000)
    plan = build_plan(config, _legacy=legacy)
    canonical = json.dumps(plan, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, separators=(',', ':')).encode('utf-8')
    assert _encode_job_json(plan) == canonical
    assert _job_hash(plan) == hashlib.sha256(canonical).hexdigest()
    if legacy:
        assert encode_json(plan) == canonical
    else:
        # The old whole-record codec rejects this aggregate's node count.
        with pytest.raises(RecordError, match='JSON depth/node limit'):
            encode_json(plan)
    path = tmp_path / 'plan.json'
    _atomic_json(path, plan, job=True)
    assert path.read_bytes() == canonical
    loaded = _read_job_json(path)
    assert loaded == plan
    assert _validate_plan(loaded).episodes == 1000


def test_large_batch_generates_validates_and_replays(tmp_path):
    import hashlib
    from botbowl.lab.generate import _encode_job_json, _job_hash
    from botbowl.lab.records import MAX_RECORD_BYTES, RecordError, decode_json, encode_json

    # Exercise real recording and both aggregate files beyond the old 4 MiB
    # boundary, without running hundreds of full matches. Budget zero is a
    # supported fragment, not a mocked episode or a fabricated summary.
    destination = tmp_path / 'data'
    config = JobConfig(str(destination), episodes=300, max_decisions=0)
    plan = build_plan(config)
    assert len(_encode_job_json(plan)) > MAX_RECORD_BYTES
    with pytest.raises(RecordError, match='JSON record byte limit'):
        encode_json(plan)
    dataset = generate(config)
    assert len(dataset['episodes']) == 300
    assert all(row['end']['decisions'] == 0 for row in dataset['episodes'])
    for name in ('plan.json', 'dataset.json'):
        payload = (destination / name).read_bytes()
        assert len(payload) > MAX_RECORD_BYTES
        with pytest.raises(RecordError, match='JSON record byte limit'):
            decode_json(payload)
    assert dataset['plan_sha256'] == hashlib.sha256((destination / 'plan.json').read_bytes()).hexdigest()
    assert dataset['plan_sha256'] == _job_hash(plan)
    assert validate_dataset(destination) == dataset
    replayed = replay_episode(destination, 'episode-000299', str(tmp_path / 'replay'))
    assert replayed['episodes'] == [dataset['episodes'][-1]]
    assert replayed['plan_sha256'] == dataset['plan_sha256']
    assert validate_dataset(tmp_path / 'replay') == replayed
    assert not list(destination.glob('*.failure.json'))
    assert not list(destination.glob('*.partial'))


def test_job_codec_keeps_small_file_bytes_and_per_entry_limits(tmp_path, monkeypatch):
    from copy import deepcopy
    import botbowl.lab.generate as generator
    from botbowl.lab.records import MAX_RECORD_BYTES, RecordError, encode_json

    plan = build_plan(JobConfig(str(tmp_path / 'data')))
    assert generator._encode_job_json(plan) == encode_json(plan)
    assert generator._job_hash(plan) == generator._hash(plan)
    oversized = deepcopy(plan)
    oversized['episodes'][0]['padding'] = 'x' * MAX_RECORD_BYTES
    with pytest.raises(RecordError, match='JSON record byte limit'):
        generator._encode_job_json(oversized)
    nested = {}
    for _ in range(41):
        nested = {'nested': nested}
    oversized['episodes'][0] = nested
    with pytest.raises(RecordError, match='JSON depth/node limit'):
        generator._encode_job_json(oversized)
    oversized['episodes'] = [{}] * 1001
    with pytest.raises(RecordError, match='Job episode count limit'):
        generator._encode_job_json(oversized)
    monkeypatch.setattr(generator, 'MAX_JOB_BYTES', 100)
    with pytest.raises(RecordError, match='JSON job byte limit'):
        generator._encode_job_json(plan)
    path = tmp_path / 'oversized.json'
    path.write_bytes(b' ' * 101)
    with pytest.raises(RecordError, match='JSON job byte limit'):
        generator._read_job_json(path)


@pytest.mark.parametrize('payload', [
    b'{"schema_version":1,"schema_version":2}',
    b'{"schema_version":2,"plan_sha256":"x","replay_episode_id":null,"episodes":[{"x":1,"x":2}]}',
    b'{"schema_version":2,"plan_sha256":"x","replay_episode_id":null,"episodes":[{"x":NaN}]}',
    b'{"schema_version":2,"plan_sha256":"x","replay_episode_id":null,"episodes":[{"x":1e999}]}',
    b'{"schema_version":2,"plan_sha256":"x","replay_episode_id":null,"episodes":[{"x":' + b'9' * 80 + b'}]}',
    b'{"schema_version":2,"plan_sha256":"x","replay_episode_id":null,"episodes":[{"x":"\xff"}]}',
    b'[]',
])
def test_job_decoder_rejects_invalid_json(payload):
    from botbowl.lab.generate import _decode_job_json
    from botbowl.lab.records import RecordError

    with pytest.raises(RecordError):
        _decode_job_json(payload)
