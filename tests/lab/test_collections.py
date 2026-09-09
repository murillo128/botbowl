"""Selection denominators, experimental provenance and durable causal views."""
import json
import subprocess
import sys

import pytest

from botbowl.lab import collections as collection
from botbowl.lab.branches import BranchSpec, BranchTree
from botbowl.lab.chance import ChancePolicy
from botbowl.lab.collections import (CollectionReader, CollectionSpecV1, collect,
                                     sample_finite_population, select_events)
from botbowl.lab.generate import JobConfig, _open_session
from botbowl.lab.policies import create_policy, policy_inputs
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.records import RecordError, encode_json
from botbowl.lab.replays import ReplayReader
from botbowl.lab.splits import build_split_manifest
from botbowl.lab.timeline import TimelineContext


def event(outcome, seq=1, decision=1, player='home:0'):
    ctx = TimelineContext('fixture', 'root', event_seq=seq, decision_seq=decision).to_json()
    return dict(schema_version=1, payload_version=1, kind='report', context=ctx,
                event_id=['fixture', 'root', seq], decision_seq=decision,
                data=dict(outcome_type=outcome, pos=None, player_id=player,
                          opp_player_id=None, rolls=[], team_id='home', n=None, skill=None))


def test_finite_population_selection_counts_and_known_weights():
    population = [{'unit_id': 'unit-%d' % i, 'events': [event('CASUALTY' if i % 2 else 'END_OF_TURN')]}
                  for i in range(12)]
    census = sample_finite_population(population, 'injury-v1')
    assert (census['attempted'], census['eligible'], census['accepted'], census['failed']) == (12, 6, 6, 0)
    assert all(row['sampling_weight'] == row['inclusion_probability'] == 1 for row in census['selected'])
    sample = sample_finite_population(population, 'injury-v1', numerator=1, denominator=2, seed=17)
    assert sample == sample_finite_population(population, 'injury-v1', numerator=1, denominator=2, seed=17)
    assert 0 < sample['accepted'] < 6
    assert all(row['sampling_weight'] == 2 and row['inclusion_probability'] == .5 for row in sample['selected'])
    assert 'finite frame' in sample['weight_scope']
    with pytest.raises(RecordError, match='Duplicate population'):
        sample_finite_population(population + population, 'injury-v1')


def test_duplicate_events_and_bounded_chain_semantics():
    down, casualty = event('KNOCKED_DOWN'), event('CASUALTY', 2)
    assert select_events([down, casualty, casualty], 'knockdown_injury-v1') == [casualty]
    assert select_events([event('INJURY_CASUALTY'), event('CASUALTY', 2),
                          event('CASUALTY_APOTHECARY', 3)], 'injury-v1') == [casualty]
    for changed in (event('CASUALTY', 2, decision=2), event('CASUALTY', 2, player='away:0')):
        assert not select_events([down, changed], 'knockdown_injury-v1')
    with pytest.raises(RecordError, match='Conflicting duplicate'):
        select_events([casualty, event('KNOCKED_OUT', 2)], 'injury-v1')
    with pytest.raises(RecordError, match='logical order'):
        select_events([casualty, down], 'knockdown_injury-v1')


def spec(**kwargs):
    generation = kwargs.pop('generation', {'scenario': 'pickup', 'home_policy': 'possession',
                                           'max_decisions': 4, 'master_seed': 0})
    return CollectionSpecV1.create(generation=generation, **kwargs)


@pytest.mark.parametrize('mode', ['natural', 'selected'])
def test_engine_reproducibility_resume_and_causal_windows(tmp_path, mode):
    design = spec(mode=mode, predicate='possession_gain-v1' if mode == 'selected' else None,
                  target=2, max_episodes=3, before=4, after=4)
    first = tmp_path / 'first'
    paused = collect(design, first, attempt_limit=1)
    assert paused['status'] == 'in_progress' and paused['attempted'] == 1
    original = CollectionReader(first).factual(0).manifest
    resumed = collect(design, first)
    second = tmp_path / 'second'
    assert collect(design, second) == resumed
    assert resumed['status'] == 'complete'
    a, b = CollectionReader(first), CollectionReader(second)
    assert a.records() == b.records()
    assert a.factual(0).manifest == original
    assert collect(design, first) == resumed
    a.verify()
    splits = build_split_manifest(a.origin_sources(), proportions={'train': 1}, seed=0,
                                  split_version='collections-v1').to_json()
    samples = list(a.iter_selected_windows(split_manifest=splits))
    assert samples
    for sample in samples:
        assert sample['metadata']['collection']['mode'] == mode
        assert sample['split'] == 'train'
        payload = json.dumps(sample['inputs'])
        assert all(canary not in payload for canary in (
            'possession_gain-v1', 'inclusion_probability', 'sampling_weight', 'collection',
            'origin_family_id', 'seed', 'future', 'event_ids'))
        cutoff = sample['metadata']['cutoff']
        for reference in sample['metadata']['history']:
            if reference is not None:
                assert reference['available_at']['event_seq'] <= cutoff['event_seq']
                assert reference['available_at']['decision_seq'] <= cutoff['decision_seq']
        assert len(sample['inputs']['presence']) == len(sample['targets']['presence']) == 5
    assert not all(samples[0]['inputs']['presence'])  # Beginning of episode is masked.
    assert not all(samples[-1]['targets']['presence'])  # Scenario terminal is masked.
    assert 'conditions the dataset' in resumed['warning']
    if mode == 'selected':
        assert len(samples) == 2
        assert all(r['selection']['sampling_weight'] is None for r in a.records())


def test_no_match_budget_and_failed_attempts_keep_denominators(tmp_path, monkeypatch):
    design = spec(mode='selected', predicate='injury-v1', target=1, max_episodes=3,
                  max_decisions=12)
    original = collection._generate_attempt
    calls = []

    def fail_once(data, plan, root):
        calls.append(root)
        if len(calls) == 1:
            raise RuntimeError('PRIVATE_FUTURE_SELECTION_CANARY')
        return original(data, plan, root)

    monkeypatch.setattr(collection, '_generate_attempt', fail_once)
    report = collect(design, tmp_path)
    assert (report['attempted'], report['failed'], report['rejected'], report['accepted']) == (3, 1, 2, 0)
    assert report['status'] == 'insufficient_matches'
    assert report['attempted_decisions'] == 8  # Failed reservation 4 + two actual prefixes of 2.
    assert 'PRIVATE_FUTURE_SELECTION_CANARY' not in json.dumps(CollectionReader(tmp_path).records())
    assert collect(design, tmp_path) == report and len(calls) == 3
    with pytest.raises(RecordError, match='identical'):
        collect(spec(max_episodes=3), tmp_path)


def test_decision_budget_does_not_launch_unbounded_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(collection, '_generate_attempt', lambda *args: pytest.fail('Must not launch'))
    report = collect(spec(max_decisions=1), tmp_path)
    assert report['status'] == 'insufficient_matches'
    assert report['attempted'] == report['attempted_decisions'] == 0


@pytest.mark.parametrize('stage', ['engine', 'before_commit', 'after_commit'])
def test_interrupted_attempt_is_never_duplicated(tmp_path, monkeypatch, stage):
    design = spec(target=2, max_episodes=3)
    original_generate, original_write = collection._generate_attempt, collection._write
    interrupted = False

    def generate(*args):
        nonlocal interrupted
        if stage == 'engine' and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return original_generate(*args)

    def write(root, name, data):
        nonlocal interrupted
        commit = name == 'collection.json' and data['attempts'] and data['attempts'][-1]['status'] == 'accepted'
        if commit and stage == 'before_commit' and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        original_write(root, name, data)
        if commit and stage == 'after_commit' and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    monkeypatch.setattr(collection, '_generate_attempt', generate)
    monkeypatch.setattr(collection, '_write', write)
    with pytest.raises(KeyboardInterrupt):
        collect(design, tmp_path)
    report = collect(design, tmp_path)
    assert report['status'] == 'complete'
    assert report['failed'] == (0 if stage == 'after_commit' else 1)
    records = CollectionReader(tmp_path).records()
    assert len({r['episode_id'] for r in records}) == len(records)


def patch_for(design):
    data = design.to_json()
    plan = collection._plan(data, 0)
    session = _open_session(plan['episodes'][0], JobConfig(output='unused', **plan['job']))
    try:
        return {'schema_version': 1, 'branch_id': 'edited', 'snapshot_id': 'edited-0',
                'reason': 'FUTURE_PATCH_SELECTION_CANARY', 'author': 'fixture',
                'operations': [{'entity': 'team', 'entity_id': 'home',
                                'field': 'rerolls', 'old_value': 0, 'new_value': 1}]}
    finally:
        session.close()


@pytest.mark.parametrize('mode', ['forced', 'intervened'])
def test_engine_alternatives_retain_factual_family_split_and_labels(tmp_path, mode, monkeypatch):
    from botbowl.lab.policies import ReferencePolicy
    act = ReferencePolicy.act

    def checked_act(self, features, legal, control=None):
        assert 'FUTURE_PATCH_SELECTION_CANARY' not in json.dumps([features, control])
        return act(self, features, legal, control)

    monkeypatch.setattr(ReferencePolicy, 'act', checked_act)
    generation = {'max_decisions': 1, 'home_policy': 'scripted'}
    kwargs = {'chance': ChancePolicy('forced').to_json()} if mode == 'forced' else {
        'patch': patch_for(spec(generation=generation))}
    design = spec(generation=generation, mode=mode, max_episodes=1, **kwargs)
    report = collect(design, tmp_path / 'a')
    assert report['status'] == 'complete', CollectionReader(tmp_path / 'a').records()
    reader = CollectionReader(tmp_path / 'a')
    reader.verify()
    record = reader.records()[0]
    assert record['mode'] == record['alternative']['mode'] == mode
    assert record['factual_mode'] == 'natural'
    assert record['alternative']['chance']['natural'] == (mode != 'forced')
    assert collect(design, tmp_path / 'b') == report
    repeated = CollectionReader(tmp_path / 'b').records()[0]
    assert repeated['alternative']['final_state_sha256'] == record['alternative']['final_state_sha256']
    # Replay manifests include physical snapshot checksums; engine-generated
    # entity IDs are not byte-identical across runs. Semantic state is identical.
    assert repeated['generation'] == record['generation']
    natural = spec(generation=generation, max_episodes=1)
    collect(natural, tmp_path / 'factual-only')
    assert CollectionReader(tmp_path / 'factual-only').factual(0).manifest == reader.factual(0).manifest
    sources = reader.origin_sources()
    assert len(sources) == 2 and sources[0]['origin_family_id'] == sources[1]['origin_family_id']
    collect(spec(generation={**generation, 'master_seed': 1, 'episode_prefix': 'other'}, max_episodes=1),
            tmp_path / 'other-family')
    other = CollectionReader(tmp_path / 'other-family').origin_sources()
    split = build_split_manifest(sources + other, proportions={'train': .5, 'test': .5}, seed=3,
                                 split_version='alternatives-v1').to_json()
    related = [source for source in split['sources'] if source['source']['source_id'] in
               {s['source_id'] for s in sources} and source['source']['origin_family_id'] == record['origin_family_id']]
    assert len(related) == 2 and len({source['family_id'] for source in related}) == 1
    assert set(split['assignments'].values()) == {'train', 'test'}
    lineage = json.loads((tmp_path / 'a/attempt-0000/lineage.json').read_text())
    assert all(node['origin_family_id'] == record['origin_family_id'] for node in lineage['nodes'])
    if mode == 'intervened':
        applied = lineage['nodes'][-1]['intervention']['patch']['operations'][0]
        assert (applied['field'], applied['old_value'], applied['new_value']) == ('rerolls', 0, 1)
        assert record['alternative']['reachability'] == 'synthetic'
    else:
        assert lineage['nodes'][-1]['chance_result']['natural'] is False
    with pytest.raises(RecordError, match='ReplayV1'):
        list(reader.iter_selected_windows(split_manifest=split))


def test_forced_tape_really_injects_roll_and_is_reproducible(tmp_path):
    # Obtain the exact supported dice context from an independent prefix, then
    # declare a fabricated success in that context. No positional tape guessing.
    generation = {'scenario': 'pickup', 'home_policy': 'possession', 'max_decisions': 2}
    data = spec(generation=generation).to_json()
    plan = collection._plan(data, 0)
    entry = plan['episodes'][0]
    session = _open_session(entry, JobConfig(output='unused', **plan['job']))
    tree = BranchTree(session.snapshot().session, kind='simulated_alternative',
                      origin_family_id=entry['origin_family_id'])
    policy = create_policy(entry['policy_specs']['home'])
    try:
        point = tree.root_snapshot
        branch = tree.fork(point, BranchSpec('continuation', point.branch_id, point.snapshot_id,
                          {'policy_id': 'fixture', 'version': '1'}, 2,
                          chance=ChancePolicy(seed=SeedSpec(**entry['seed_plan']['sources']['engine']))))
        def drive(observation, legal):
            features, control = policy_inputs(observation.primary)
            return policy.act(features, legal, control)
        branch.run(drive, policy_id='fixture', version='1')
        tape = branch._session._game.dice.chance.tape()
        assert len(tape['rolls']) == 1 and tape['rolls'][0]['die'] == 'D6'
        tape['rolls'][0].update(result=6, mode='forced', natural=False, rng_advance=False,
                                provenance='fabricated')
    finally:
        policy.close()
        tree.close()
        session.close()
    design = spec(generation=generation, mode='forced', max_episodes=1,
                  chance=ChancePolicy('forced', tape=tape).to_json())
    assert collect(design, tmp_path)['status'] == 'complete', CollectionReader(tmp_path).records()
    game = ReplayReader(tmp_path / 'attempt-0000', 'alternative').replay_all()
    try:
        rolls = game.dice.chance.tape()['rolls']
        assert rolls[0]['result'] == 6 and rolls[0]['natural'] is False
    finally:
        game.close()


@pytest.mark.parametrize('change', [
    {'mode': 'selected', 'predicate': '__import__("os").system("touch canary")'},
    {'mode': 'natural', 'chance': ChancePolicy('forced').to_json()},
    {'mode': 'forced'}, {'before': True}, {'max_episodes': 1001},
    {'generation': {'home_policy': 'external.module'}},
])
def test_invalid_specs_reject_before_execution(tmp_path, change):
    with pytest.raises((RecordError, ValueError)):
        collect(spec(**change), tmp_path / 'data')
    assert not list(tmp_path.iterdir())


def test_forbidden_patch_and_mutable_input_are_not_executed(tmp_path):
    patch = patch_for(spec())
    patch['operations'][0]['field'] = '__reduce__'
    with pytest.raises(RecordError, match='Unsupported patch'):
        spec(mode='intervened', patch=patch)
    data = spec().to_json()
    frozen = CollectionSpecV1(data)
    data['mode'] = 'forced'
    assert frozen.to_json()['mode'] == 'natural'


def test_failed_alternative_retains_immutable_factual_episode(tmp_path):
    patch = patch_for(spec())
    patch['operations'][0]['old_value'] = 999
    design = spec(mode='intervened', patch=patch, max_episodes=1)
    report = collect(design, tmp_path)
    assert report['status'] == 'insufficient_matches' and report['failed'] == 1
    reader = CollectionReader(tmp_path)
    record = reader.records()[0]
    assert record['error_type'] == 'InterventionError' and record['alternative'] is None
    assert reader.factual(0).manifest['source_family'] == record['origin_family_id']
    assert reader.generation_plan(0)['episodes'][0]['episode_id'] == record['episode_id']
    assert len(reader.origin_sources()) == 1
    reader.verify()


def test_resume_verifies_committed_shards(tmp_path):
    design = spec(target=2, max_episodes=2)
    collect(design, tmp_path, attempt_limit=1)
    shard = next((tmp_path / 'attempt-0000/factual').glob('data-*/primary-*.jsonl'))
    shard.write_bytes(shard.read_bytes().replace(b'primary', b'PRImary', 1))
    with pytest.raises(RecordError, match='checksum'):
        collect(design, tmp_path)
    assert not (tmp_path / 'attempt-0001').exists()


def test_cli_reopen_and_unknown_schema(tmp_path):
    design = spec(max_episodes=1)
    plan = tmp_path / 'spec.json'
    plan.write_bytes(encode_json(design.to_json()))
    command = [sys.executable, '-m', 'botbowl.lab.collections', str(plan), str(tmp_path / 'data')]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 0 and second.stdout == first.stdout
    bad = design.to_json()
    bad['schema_version'] = 2
    plan.write_bytes(encode_json(bad))
    assert subprocess.run(command, capture_output=True).returncode == 2
