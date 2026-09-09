"""Enumerable temporal canaries plus real recorder and bounded-stream regressions."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import tracemalloc

import pytest

import botbowl as bb
from botbowl.lab.channels import InputProfile, make_channel
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.records import CHANNEL_FILES, RecordError, encode_json, validate_episode
from botbowl.lab.splits import build_split_manifest, origin_from_episode, validate_window_membership
from botbowl.lab.windows import (WindowSpecV1, availability_rules, iter_windows,
                                 iter_window_batches, validate_availability, validate_window_source)
from tests.baseline import progress_action
from tests.lab.test_recording import record_game, complete, scenario_players
from tests.lab.test_timeline import until
from botbowl.core import procedure as proc


PROFILE = InputProfile('custom', (('primary.teams[].score', 'integer'),
                                   ('primary.match.game_over', 'boolean'),
                                   ('primary.decision.phase', 'string')))


def freeze(reader):
    return build_split_manifest([origin_from_episode(reader.manifest)],
                                proportions={'train': 1}, seed=17, split_version='fixture-v1')


def write_episode(root, manifest, rows):
    root.mkdir(parents=True, exist_ok=True)
    data = deepcopy(manifest)
    data['files'] = {}
    for name, values in rows.items():
        payload = b''.join(encode_json(row) + b'\n' for row in values)
        path = root / CHANNEL_FILES[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        data['files'][name] = {'path': CHANNEL_FILES[name], 'schema_version': 1,
                               'rows': len(values), 'bytes': len(payload),
                               'sha256': hashlib.sha256(payload).hexdigest()}
    (root / 'manifest.json').write_bytes(encode_json(data))
    return EpisodeReader(root.parent, root.name)


@pytest.fixture
def template(tmp_path):
    game, recorder = record_game(tmp_path, name='template', profile=PROFILE)
    recorder.advance(progress_action(game))
    recorder.finish(truncation_reason='fixture_limit')
    episode = EpisodeReader(tmp_path, 'template').read_episode()
    recorder.close()
    game.close()
    return episode


def enumerable(tmp_path, template, count=6, end='terminal', name='enumerable', profile=PROFILE):
    manifest = deepcopy(template['manifest'])
    manifest.update(episode_id=name, source_family='enumerable-family', profile=profile.to_json())
    ctx = {**manifest['initial_context'], 'episode_id': name}
    public = template['channels']['primary'][0]['channel']['data']
    actions = template['channels']['transitions'][0]['action']
    rows = {name: [] for name in ('primary', 'transitions', 'events', 'macros', 'diagnostics')}
    actors = ['home', 'away', 'away', 'home']
    for i in range(count + 1):
        data = deepcopy(public)
        actor = actors[i % len(actors)] if i < count or end != 'terminal' else None
        data['decision'].update(pending=actor is not None,
                                actor_team={'value': actor, 'present': actor is not None},
                                phase='terminal' if actor is None else 'coin_toss')
        data['match']['game_over'] = actor is None
        for team in data['teams']:
            team['score'] = i
        row = {'schema_version': 1, 'observation_id': i + 1,
               'context': {**ctx, 'decision_seq': i}, 'channel': make_channel('primary', data)}
        rows['primary'].append(row)
    for i in range(count):
        before, after = rows['primary'][i], rows['primary'][i + 1]
        actor = before['channel']['data']['decision']['actor_team']['value']
        rows['transitions'].append({
            'schema_version': 1, 'transition_id': [name, 'root', i + 1],
            'source_family': manifest['source_family'], 'scenario_id': manifest['scenario_id'],
            'before': before['context'], 'after': after['context'], 'actor_id': actor,
            'action': {**actions, 'type': 'HEADS', 'actor_id': actor},
            'pre_observation': i + 1, 'post_observation': i + 2,
            'event_start': 1, 'event_stop': 1,
            'next_actor_id': after['channel']['data']['decision']['actor_team']['value'],
            'status': 'resolved', 'end': None, 'macro_id': None, 'primitive_order': None})
    ending = {'kind': end, 'reason': 'game_over' if end == 'terminal' else 'fixture_limit'}
    if rows['transitions']:
        rows['transitions'][-1]['end'] = ending
    manifest.update(initial_context=rows['primary'][0]['context'], initial_observation=1,
                    final_context=rows['primary'][-1]['context'], final_observation=count + 1,
                    end=ending, branches=[{'branch_id': 'root', 'parent': None}])
    reader = write_episode(tmp_path / name, manifest, rows)
    validate_episode(reader.manifest, rows)
    return reader, rows


def windows(reader, spec=None):
    return list(iter_windows(reader, spec or WindowSpecV1(3, 2, profile=PROFILE), split_manifest=freeze(reader)))


def score(observation):
    return observation['primary.teams[].score'][0] if observation else None


def test_enumerable_shapes_masks_references_and_split(tmp_path, template):
    reader, _ = enumerable(tmp_path, template)
    samples = windows(reader)
    assert len(samples) == 6
    assert [score(o) for o in samples[0]['inputs']['observations']] == [None, None, 0]
    assert [score(o) for o in samples[3]['inputs']['observations']] == [1, 2, 3]
    assert [score(o) for o in samples[3]['targets']['observations']] == [4, 5]
    assert samples[-1]['targets']['presence'] == [True, False]
    assert samples[-1]['targets']['observations'][1] is None
    assert samples[3]['metadata']['cutoff']['decision_seq'] == 3
    assert [r['available_at']['decision_seq'] for r in samples[3]['metadata']['history']] == [1, 2, 3]
    assert [r['observation_id'] for r in samples[3]['metadata']['target_observations']] == [5, 6]
    assert validate_window_membership(freeze(reader), samples)
    assert {s['metadata']['family_id'] for s in samples} == {samples[0]['metadata']['family_id']}


def test_future_suffix_and_selected_action_interventions(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    spec = WindowSpecV1(3, 3, profile=PROFILE)
    baseline = windows(reader, spec)[2]
    conditioned = windows(reader, replace(spec, mode='action_conditioned'))[2]
    # A different possible suffix, with its exact source digest re-frozen.
    for row in rows['primary'][3:]:
        row['channel']['data']['teams'][0]['score'] = 987
    for row in rows['transitions'][3:]:
        row['action']['type'] = 'TAILS'
    changed = write_episode(reader.directory, reader.manifest, rows)
    assert windows(changed, spec)[2]['inputs'] == baseline['inputs']
    assert windows(changed, replace(spec, mode='action_conditioned'))[2]['inputs'] == conditioned['inputs']
    assert windows(changed, spec)[2]['targets'] != baseline['targets']
    rows['transitions'][2]['action']['type'] = 'TAILS'
    changed = write_episode(reader.directory, reader.manifest, rows)
    assert windows(changed, spec)[2]['inputs'] == baseline['inputs']
    new = windows(changed, replace(spec, mode='action_conditioned'))[2]['inputs']
    assert new['observations'] == conditioned['inputs']['observations']
    assert new['action']['type'] == 'TAILS' != conditioned['inputs']['action']['type']


@pytest.mark.parametrize('end', ['terminal', 'truncated'])
def test_missing_policies_stride_and_pending_targets(tmp_path, template, end):
    reader, rows = enumerable(tmp_path, template, end=end)
    spec = WindowSpecV1(2, 2, history_stride=2, stride=2, profile=PROFILE)
    samples = windows(reader, spec)
    assert [[score(o) for o in s['inputs']['observations']] for s in samples] == [
        [None, 0], [0, 2], [2, 4]]
    assert len(windows(reader, replace(spec, short_history='exclude'))) == 2
    assert len(windows(reader, WindowSpecV1(3, 2, short_targets='exclude', profile=PROFILE))) == 5
    if end == 'truncated':
        rows['transitions'][-1]['status'] = 'pending'
        reader = write_episode(reader.directory, reader.manifest, rows)
        assert windows(reader)[-1]['targets']['presence'] == [False, False]
        assert windows(reader)[-1]['inputs']['presence'] == [True, True, True]


@pytest.mark.parametrize('boundary', ['gap', 'branch', 'family'])
def test_no_window_crosses_trace_boundaries(tmp_path, template, boundary):
    reader, rows = enumerable(tmp_path, template)
    manifest = reader.manifest
    if boundary == 'gap':
        del rows['transitions'][2]
    elif boundary == 'branch':
        parent = deepcopy(rows['primary'][3]['context'])
        manifest['branches'].append({'branch_id': 'alternative', 'parent': parent})
        # Fork capture creates its own pre-observation at the same logical prefix.
        duplicate = deepcopy(rows['primary'][3])
        rows['primary'].insert(4, duplicate)
        for row in rows['primary'][4:]:
            row['observation_id'] += 1
            row['context']['branch_id'] = 'alternative'
        for row in rows['transitions'][3:]:
            row['before'] = {**row['before'], 'branch_id': 'alternative'}
            row['after'] = {**row['after'], 'branch_id': 'alternative'}
            row['transition_id'][1] = 'alternative'
            row['pre_observation'] += 1
            row['post_observation'] += 1
        manifest['final_observation'] += 1
        manifest['final_context'] = rows['primary'][-1]['context']
    else:
        rows['transitions'][3]['source_family'] = 'incompatible'
    reader = write_episode(reader.directory, manifest, rows)
    if boundary == 'family':
        with pytest.raises(RecordError, match='provenance'):
            windows(reader)
    else:
        samples = windows(reader)
        after = next(s for s in samples if s['metadata']['cutoff']['decision_seq'] == 3)
        assert after['inputs']['presence'] == [False, False, True]
        before = next(s for s in samples if s['metadata']['cutoff']['decision_seq'] == (1 if boundary == 'gap' else 2))
        assert before['targets']['presence'] == [True, False]


@pytest.mark.parametrize('corrupt', ['post_ref', 'old_row', 'extra_field', 'end', 'profile', 'source'])
def test_temporal_and_channel_canaries_fail_closed(tmp_path, template, corrupt):
    reader, rows = enumerable(tmp_path, template)
    frozen = freeze(reader)
    if corrupt == 'post_ref':
        rows['transitions'][2]['pre_observation'] = rows['transitions'][2]['post_observation']
    elif corrupt == 'old_row':
        rows['primary'][2]['context']['decision_seq'] = 5
    elif corrupt == 'extra_field':
        rows['primary'][2]['channel']['data']['future_oracle'] = 999
    elif corrupt == 'end':
        rows['primary'][2]['channel']['data']['match']['game_over'] = True
    elif corrupt == 'profile':
        with pytest.raises(ValueError, match='Forbidden'):
            InputProfile('custom', (('evaluation.labels.oracle', 'integer'),))
        return
    else:
        rows['primary'][2]['channel']['data']['teams'][0]['score'] = 999
    reader = write_episode(reader.directory, reader.manifest, rows)
    with pytest.raises(ValueError):
        list(iter_windows(reader, WindowSpecV1(2, 2, profile=PROFILE),
                          split_manifest=frozen if corrupt == 'source' else freeze(reader)))


def test_authorized_aids_use_actual_capture_context(tmp_path, template):
    profile = InputProfile('custom', PROFILE.fields + (('control.action_mask[]', 'boolean'),
                                                       ('derived.block_dice[][]', 'number')), True)
    reader, rows = enumerable(tmp_path, template)
    manifest = reader.manifest
    manifest['profile'] = profile.to_json()
    for name, payload in [('control', {'action_mask': [True, False], 'action_ids': ['FUTURE_CANARY']}),
                           ('derived', {'block_dice': [[2.0]]})]:
        rows[name] = [{**{k: row[k] for k in ('schema_version', 'observation_id', 'context')},
                       'channel': make_channel(name, payload)} for row in rows['primary']]
    reader = write_episode(reader.directory, manifest, rows)
    spec = WindowSpecV1(1, 1, profile=profile)
    assert windows(reader, spec)[0]['inputs']['observations'][0]['control.action_mask[]'] == [True, False]
    assert 'CANARY' not in json.dumps([s['inputs'] for s in windows(reader, spec)])
    rows['control'][2]['context'] = deepcopy(rows['primary'][4]['context'])
    reader = write_episode(reader.directory, manifest, rows)
    with pytest.raises(RecordError, match='availability'):
        windows(reader, spec)
    with pytest.raises(RecordError, match='authority'):
        windows(enumerable(tmp_path, template, name='no-aids')[0], spec)


def test_availability_rules_reject_future_and_forbidden_paths(template):
    cutoff = template['channels']['transitions'][0]['before']
    spec = WindowSpecV1(1, 1, profile=PROFILE)
    assert WindowSpecV1.from_json(spec.to_json()) == spec
    assert set(availability_rules(spec)['fields']) == {p for p, _ in PROFILE.fields}
    for field in ('action', 'end', 'next_actor_id', 'oracle', 'rng', 'metadata', 'macro_result'):
        with pytest.raises(RecordError, match='authorized'):
            validate_availability(field, cutoff, cutoff, spec)
    for clock in ('decision_seq', 'event_seq'):
        with pytest.raises(RecordError, match='after'):
            validate_availability(PROFILE.fields[0][0], {**cutoff, clock: cutoff[clock] + 1}, cutoff, spec)
    conditioned = replace(spec, mode='action_conditioned')
    assert validate_availability('action', cutoff, cutoff, conditioned)
    with pytest.raises(RecordError, match='cutoff'):
        validate_availability('action', {**cutoff, 'decision_seq': 1}, cutoff, conditioned)


@pytest.mark.parametrize('kwargs', [{'unit': 'events'}, {'unit': 'turns'}, {'data_version': 2},
                                    {'availability_version': 2}, {'horizon': True},
                                    {'history_length': 0}, {'history_stride': 0}, {'stride': -1}])
def test_undefined_units_versions_and_invalid_shapes_rejected(kwargs):
    with pytest.raises(ValueError):
        WindowSpecV1(**{'history_length': 2, 'horizon': 2, **kwargs})


def test_streaming_canaries_no_full_load_and_early_close(tmp_path, template, monkeypatch):
    reader, rows = enumerable(tmp_path, template, count=40)
    rows['privileged'] = [{'schema_version': 1, 'observation_id': 1,
                           'channel': make_channel('privileged', {'snapshot': 'RNG_CANARY'})}]
    rows['evaluation'] = [{'schema_version': 1, 'observation_id': 1, 'channel': make_channel(
        'evaluation', {'labels': {'oracle': 'ORACLE_CANARY'}, 'estimates': {}, 'provenance': {}})}]
    for row in rows['primary']:
        row['channel']['metadata']['provenance'] = 'METADATA_CANARY'
    reader = write_episode(reader.directory, reader.manifest, rows)
    frozen = freeze(reader)
    def forbidden(*args, **kwargs):
        pytest.fail('Full episode/Game/snapshot load is forbidden')
    monkeypatch.setattr(reader, 'read_channels', forbidden)
    monkeypatch.setattr(reader, 'read_inputs', forbidden)
    monkeypatch.setattr(bb.Game, '__init__', forbidden)
    original = reader.iter_channel
    counts, closed = {}, set()
    def tracked(name):
        assert name in ('primary', 'transitions')
        counts[name] = 0
        try:
            for row in original(name):
                counts[name] += 1
                yield row
        finally:
            closed.add(name)
    monkeypatch.setattr(reader, 'iter_channel', tracked)
    stream = iter_window_batches(reader, WindowSpecV1(3, 2, profile=PROFILE),
                                 split_manifest=frozen, batch_size=4)
    batch = next(stream)
    assert counts['transitions'] == 5 and counts['primary'] == 6
    assert 'CANARY' not in json.dumps([s['inputs'] for s in batch])
    assert 'METADATA_CANARY' in json.dumps(batch[0]['metadata'])
    batch[0]['inputs']['observations'][-1].clear()
    assert batch[1]['inputs']['observations'][-2]
    stream.close()
    assert closed == {'primary', 'transitions'}


def test_stream_memory_does_not_grow_with_episode(tmp_path, template):
    peaks = []
    for count in (16, 160):
        reader, _ = enumerable(tmp_path, template, count=count, name='memory-{}'.format(count))
        frozen = freeze(reader)
        tracemalloc.start()
        batches = 0
        for batch in iter_window_batches(reader, WindowSpecV1(3, 2, profile=PROFILE),
                                         split_manifest=frozen, batch_size=4):
            batches += 1
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert batches == count // 4
        peaks.append(peak)
    assert peaks[1] < peaks[0] + 500000


def test_real_recorder_all_primitive_cutoffs_and_terminal(tmp_path):
    game, recorder = record_game(tmp_path)
    complete(game, recorder)
    reader = EpisodeReader(tmp_path, 'episode')
    samples = windows(reader, WindowSpecV1(2, 2, profile=PROFILE))
    decisions = reader.read_channels(['transitions'])['transitions']
    assert len(samples) == len(decisions)
    assert [s['metadata']['cutoff'] for s in samples] == [d['before'] for d in decisions]
    assert any(a['actor_id'] == b['actor_id'] for a, b in zip(decisions, decisions[1:]))
    assert samples[-1]['targets']['presence'] == [True, False]
    assert validate_window_source(reader, WindowSpecV1(2, 2, profile=PROFILE), split_manifest=freeze(reader)) == len(samples)
    recorder.close()
    game.close()


def test_real_reroll_and_interrupted_macro_primitive_boundaries(tmp_path):
    game, recorder = record_game(tmp_path, 3, True)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    player, _ = scenario_players(game, [(3, 3)], [(4, 3)])
    player.team.state.rerolls = 1
    recorder.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    with game.dice.force(d6=[1], strict=True):
        recorder.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
    assert game.get_procedure().__class__ is proc.Reroll
    recorder.advance(bb.Action(bb.ActionType.DONT_USE_REROLL))
    recorder.finish(truncation_reason='fixture_limit')
    reader = EpisodeReader(tmp_path, 'episode')
    samples = windows(reader, WindowSpecV1(1, 1, mode='action_conditioned', profile=PROFILE))
    assert samples[-1]['inputs']['observations'][0]['primary.decision.phase'] == 'reroll'
    assert samples[-1]['inputs']['action']['type'] == 'DONT_USE_REROLL'
    recorder.close()
    game.close()

    game, recorder = record_game(tmp_path, 3, True, name='macro')
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    player = scenario_players(game, [(3, 3)])[0]
    recorder.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    macro = next(m for m in recorder.actions.legal_actions().macros
                 if m.to_json()['kind'] == 'route' and m.type == 'MOVE' and len(m.path) == 1)
    with pytest.raises(bb.GameTruncatedError):
        recorder.execute_macro(macro, max_steps=1)
    recorder.finish(truncation_reason='step_budget')
    samples = windows(EpisodeReader(tmp_path, 'macro'), WindowSpecV1(1, 1, mode='action_conditioned', profile=PROFILE))
    assert samples[-1]['inputs']['action']['type'] == 'MOVE'
    assert samples[-1]['targets']['presence'] == [False]
    assert samples[-1]['metadata']['source_transitions'][0]['primitive_order'] == 0
    assert 'macro_id' not in samples[-1]['inputs']
    recorder.close()
    game.close()


def test_real_defender_interrupts_macro_and_keeps_own_cutoff(tmp_path):
    game, recorder = record_game(tmp_path, 3, True)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    player, _ = scenario_players(game, [(2, 2)], [(3, 2)])
    player.extra_st = 1 - player.get_st()
    recorder.advance(bb.Action(bb.ActionType.START_BLITZ, player=player))
    macro = next(m for m in recorder.actions.legal_actions().macros
                 if m.to_json()['kind'] == 'route' and m.type == 'BLOCK' and len(m.path) == 1)
    with game.dice.force(block_dice=[bb.BBDieResult.PUSH] * 3, strict=True):
        result = recorder.execute_macro(macro)
    assert result.interruption == 'actor_changed'
    recorder.advance(bb.Action(bb.ActionType.SELECT_PUSH))
    recorder.finish(truncation_reason='fixture_limit')
    samples = windows(EpisodeReader(tmp_path, 'episode'),
                      WindowSpecV1(2, 1, mode='action_conditioned', profile=PROFILE))
    assert samples[-1]['inputs']['action']['actor_id'] != samples[-2]['inputs']['action']['actor_id']
    assert samples[-1]['inputs']['action']['type'] == 'SELECT_PUSH'
    assert samples[-1]['metadata']['cutoff']['decision_seq'] == samples[-2]['metadata']['cutoff']['decision_seq'] + 1
    assert 'interruption' not in json.dumps(samples[-2]['inputs'])
    recorder.close()
    game.close()


@pytest.mark.parametrize('corrupt', ['hash', 'partial', 'oversized', 'count'])
def test_incremental_channel_integrity(tmp_path, template, corrupt):
    reader, rows = enumerable(tmp_path, template)
    path = reader.directory / CHANNEL_FILES['primary']
    if corrupt == 'hash':
        path.write_bytes(path.read_bytes().replace(b'coin_toss', b'start_game'))
    elif corrupt == 'partial':
        path.write_bytes(path.read_bytes()[:-1])
    elif corrupt == 'oversized':
        from botbowl.lab.records import MAX_RECORD_BYTES
        path.write_bytes(b' ' * (MAX_RECORD_BYTES + 1) + b'\n')
    else:
        data = reader.manifest
        data['files']['primary']['rows'] += 1
        (reader.directory / 'manifest.json').write_bytes(encode_json(data))
    if corrupt in ('partial', 'oversized'):
        # Even matching byte attestations cannot make a malformed row valid.
        data = reader.manifest
        payload = path.read_bytes()
        data['files']['primary'].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        (reader.directory / 'manifest.json').write_bytes(encode_json(data))
    reader = EpisodeReader(tmp_path, 'enumerable')
    with pytest.raises(RecordError):
        list(reader.iter_channel('primary'))


def test_zero_decision_and_frozen_multiple_splits(tmp_path, template):
    reader, _ = enumerable(tmp_path, template, count=0, end='truncated')
    assert windows(reader) == []
    readers = []
    for i in range(3):
        reader, rows = enumerable(tmp_path, template, name='source-{}'.format(i))
        manifest = reader.manifest
        manifest['source_family'] = 'family-{}'.format(i)
        for row in rows['transitions']:
            row['source_family'] = manifest['source_family']
        readers.append(write_episode(reader.directory, manifest, rows))
    split = build_split_manifest([origin_from_episode(r.manifest) for r in readers],
                                 proportions={'train': 0.34, 'validation': 0.33, 'test': 0.33},
                                 seed=11, split_version='three-origins-v1')
    inherited = []
    for reader in readers:
        first = list(iter_windows(reader, WindowSpecV1(2, 1, profile=PROFILE), split_manifest=split))
        second = list(iter_windows(reader, WindowSpecV1(3, 4, stride=2, profile=PROFILE), split_manifest=split))
        assert validate_window_membership(split, first + second)
        assert len({(s['split'], s['metadata']['family_id']) for s in first + second}) == 1
        inherited.append(first[0]['split'])
    assert set(inherited) == {'train', 'validation', 'test'}
