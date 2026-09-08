from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import os
import pickle
import random
import subprocess
import sys

import numpy as np
import pytest

from botbowl.lab.splits import (
    InsufficientOriginsError,
    OriginSourceV1,
    SplitError,
    SplitManifestV1,
    SplitVersionRequiredError,
    build_split_manifest,
    origin_from_episode,
    policy_group_id,
    validate_split_manifest,
    validate_window_membership,
)
from botbowl.lab.recording import EpisodeReader
from tests.baseline import progress_action
from tests.lab.test_recording import record_game


def digest(character):
    return 'sha256:' + character * 64


def source(source_id, *, kind='episode', episode_id=None, family=None, content=None,
           semantic=None, variant=None, relationships=(), scenarios=(), policies=()):
    stable_content = 'sha256:' + hashlib.sha256(source_id.encode('ascii')).hexdigest()
    return {
        'schema_version': 1,
        'source_id': source_id,
        'source_version': 'fixture-v1',
        'kind': kind,
        'episode_id': episode_id,
        'origin_family_id': family,
        'content_digest': content or stable_content,
        'semantic_origin_fingerprint': semantic,
        'variant_group_id': variant,
        'relationships': list(relationships),
        'groups': {'scenario': list(scenarios), 'policy': list(policies)},
    }


def family_for(manifest, source_id):
    return next(entry['family_id'] for entry in manifest.to_json()['sources']
                if entry['source']['source_id'] == source_id)


def split_for(manifest, source_id):
    data = manifest.to_json()
    return data['assignments'][family_for(manifest, source_id)]


def test_branches_replays_and_augmentations_stay_with_root_family():
    sources = [
        source('root', episode_id='episode-root', family='origin-a', content=digest('1')),
        source('branch', kind='snapshot', episode_id='episode-root', family='origin-a',
               content=digest('2'), relationships=[{'kind': 'parent_episode', 'source_id': 'root'}]),
        source('snapshot-branch', kind='variant', family='origin-a', content=digest('7'),
               relationships=[{'kind': 'parent_snapshot', 'source_id': 'branch'}]),
        source('replay', kind='replay', family='origin-a', content=digest('3'),
               relationships=[{'kind': 'replay_source', 'source_id': 'root'}]),
        source('augment', kind='augmentation', family='origin-a', content=digest('4'),
               relationships=[{'kind': 'augmentation_source', 'source_id': 'branch'},
                              {'kind': 'variant_source', 'source_id': 'snapshot-branch'}]),
        source('other-b', family='origin-b', content=digest('5')),
        source('other-c', family='origin-c', content=digest('6')),
    ]
    manifest = build_split_manifest(
        sources, proportions={'train': .6, 'validation': .2, 'test': .2},
        seed=17, split_version='fixture-v1')

    related = ('root', 'branch', 'snapshot-branch', 'replay', 'augment')
    assert len({family_for(manifest, item) for item in related}) == 1
    assert len({split_for(manifest, item) for item in related}) == 1
    counts = manifest.to_json()['counts']
    assert counts['sources'] == 7 and counts['families'] == 3
    assert [counts['splits'][name]['families'] for name in sorted(counts['splits'])] == [1, 1, 1]
    assert sorted(counts['splits'][name]['sources'] for name in counts['splits']) == [1, 1, 5]


def test_exact_duplicates_and_declared_near_variants_group_but_distinct_origins_do_not():
    sources = [
        source('duplicate-a', kind='snapshot', content=digest('1')),
        source('duplicate-renamed', kind='snapshot', content=digest('1')),
        source('variant-a', kind='variant', content=digest('2'), variant='weather-family'),
        source('variant-b', kind='variant', content=digest('3'), variant='weather-family'),
        source('distinct', kind='snapshot', content=digest('4')),
    ]
    manifest = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                                    seed=2, split_version='fixture-v1')

    assert family_for(manifest, 'duplicate-a') == family_for(manifest, 'duplicate-renamed')
    assert family_for(manifest, 'variant-a') == family_for(manifest, 'variant-b')
    assert len({family_for(manifest, item) for item in ('duplicate-a', 'variant-a', 'distinct')}) == 3
    assert manifest.to_json()['grouping_rule']['near_variants'] == 'declared-variant-groups-only'


def test_semantic_origin_fingerprint_groups_different_file_names_and_detects_contamination():
    original = [
        source('file-a', kind='snapshot', content=digest('1'), semantic=digest('a')),
        source('renamed-file-b', kind='snapshot', content=digest('2'), semantic=digest('b')),
    ]
    manifest = build_split_manifest(original, proportions={'train': .5, 'test': .5},
                                    seed=5, split_version='fixture-v1')
    assert split_for(manifest, 'file-a') != split_for(manifest, 'renamed-file-b')

    contaminated = deepcopy(original)
    contaminated[1]['semantic_origin_fingerprint'] = digest('a')
    with pytest.raises(SplitError, match='connects existing splits'):
        validate_split_manifest(manifest, contaminated)


@pytest.mark.parametrize('damage', ['missing-parent', 'cycle', 'duplicate-id', 'duplicate-episode'])
def test_invalid_provenance_graph_rejected(damage):
    if damage == 'missing-parent':
        sources = [source('a', relationships=[{'kind': 'parent_snapshot', 'source_id': 'missing'}])]
        message = 'Unknown origin relationship target'
    elif damage == 'cycle':
        sources = [
            source('a', relationships=[{'kind': 'variant_source', 'source_id': 'b'}]),
            source('b', relationships=[{'kind': 'variant_source', 'source_id': 'a'}]),
        ]
        message = 'Cyclic origin relationships'
    elif damage == 'duplicate-id':
        sources = [source('a', content=digest('1')), source('a', content=digest('2'))]
        message = 'Duplicate source_id has different content'
    else:
        sources = [source('a', episode_id='episode', content=digest('1')),
                   source('b', episode_id='episode', content=digest('2'))]
        message = 'Duplicate episode_id has different content'
    with pytest.raises(SplitError, match=message):
        build_split_manifest(sources, proportions={'train': 1.0}, seed=1,
                             split_version='fixture-v1')


def test_insufficient_independent_families_reports_diagnostic_without_splitting_siblings():
    sources = [source('a', family='same', content=digest('1')),
               source('b', family='same', content=digest('2'))]
    with pytest.raises(InsufficientOriginsError) as caught:
        build_split_manifest(sources, proportions={'train': .8, 'test': .2}, seed=1,
                             split_version='fixture-v1')
    assert caught.value.families == 1
    assert caught.value.required_splits == 2
    assert 'collect more origins or choose another protocol' in str(caught.value)


def test_reordering_and_same_seed_regenerate_identical_manifest():
    sources = [source('source-{}'.format(index), content=digest(format(index, 'x')))
               for index in range(1, 9)]
    arguments = dict(proportions={'train': .5, 'validation': .25, 'test': .25},
                     seed=123456, split_version='stable-v1')
    first = build_split_manifest(sources, **arguments)
    second = build_split_manifest(list(reversed(sources)), **arguments)
    assert first.to_json() == second.to_json()


def test_file_rename_does_not_change_family_assignment_and_split_rng_is_isolated():
    before = pickle.dumps((random.getstate(), np.random.get_state()))
    original = [source('logical-a', content=digest('1')),
                source('logical-b', content=digest('2'))]
    renamed = [source('renamed-file-entry', content=digest('1')),
               source('logical-b', content=digest('2'))]
    arguments = dict(proportions={'train': .5, 'test': .5}, seed=70,
                     split_version='stable-v1')
    first = build_split_manifest(original, **arguments)
    second = build_split_manifest(renamed, **arguments)
    assert family_for(first, 'logical-a') == family_for(second, 'renamed-file-entry')
    assert split_for(first, 'logical-a') == split_for(second, 'renamed-file-entry')
    assert pickle.dumps((random.getstate(), np.random.get_state())) == before


def test_held_out_scenario_and_policy_groups_override_balancing_without_splitting_family():
    sources = [
        source('weather-root', family='weather', content=digest('1'), scenarios=['blizzard']),
        source('weather-branch', family='weather', content=digest('2'), policies=['baseline']),
        source('policy', family='policy', content=digest('3'), policies=['unseen-policy']),
        source('train', family='train', content=digest('4')),
        source('spare', family='spare', content=digest('5')),
    ]
    manifest = build_split_manifest(
        sources, proportions={'train': .6, 'test': .4}, seed=0, split_version='ood-v1',
        held_out_groups={'scenario': {'blizzard': 'test'},
                         'policy': {'unseen-policy': 'test'}})
    assert split_for(manifest, 'weather-root') == split_for(manifest, 'weather-branch') == 'test'
    assert split_for(manifest, 'policy') == 'test'
    assert manifest.to_json()['held_out_groups']['scenario'] == {'blizzard': 'test'}


def test_conflicting_held_out_groups_on_one_family_rejected():
    sources = [source('a', scenarios=['weather'], policies=['policy']), source('b')]
    with pytest.raises(SplitError, match='different splits'):
        build_split_manifest(
            sources, proportions={'train': .5, 'test': .5}, seed=0, split_version='ood-v1',
            held_out_groups={'scenario': {'weather': 'test'}, 'policy': {'policy': 'train'}})


def test_source_or_policy_change_requires_new_version_and_never_patches_old_manifest():
    initial = [source('a', content=digest('1')), source('b', content=digest('2'))]
    first = build_split_manifest(initial, proportions={'train': .5, 'test': .5},
                                 seed=9, split_version='dataset-v1')
    appended = initial + [source('c', content=digest('3'))]
    with pytest.raises(SplitVersionRequiredError):
        build_split_manifest(appended, proportions={'train': .5, 'test': .5}, seed=9,
                             split_version='dataset-v1', previous_manifest=first)
    second = build_split_manifest(appended, proportions={'train': .5, 'test': .5}, seed=9,
                                  split_version='dataset-v2', previous_manifest=first)
    assert second.to_json()['split_version'] == 'dataset-v2'
    assert first.to_json()['counts']['sources'] == 2


def test_added_bridge_between_existing_splits_is_not_silently_repaired():
    sources = [source('a', content=digest('1')), source('b', content=digest('2'))]
    manifest = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                                    seed=9, split_version='dataset-v1')
    bridged = deepcopy(sources)
    bridged[1]['relationships'] = [{'kind': 'replay_source', 'source_id': 'a'}]
    with pytest.raises(SplitError, match='connects existing splits'):
        validate_split_manifest(manifest, bridged)


def test_window_validator_uses_recorder_origin_and_rejects_wrong_split():
    sources = [source('episode-a', episode_id='ep-a', family='family-a', content=digest('1')),
               source('episode-b', episode_id='ep-b', family='family-b', content=digest('2'))]
    manifest = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                                    seed=3, split_version='dataset-v1')
    assigned = split_for(manifest, 'episode-a')
    sample = {'window': [10, 20], 'split': assigned,
              'origin': {'source_id': 'episode-a', 'episode_id': 'ep-a',
                         'source_family': 'family-a'}}
    assert validate_window_membership(manifest, [sample])
    sample['split'] = 'test' if assigned == 'train' else 'train'
    with pytest.raises(SplitError, match='different split'):
        validate_window_membership(manifest, [sample])
    sample['split'] = assigned
    sample['origin']['source_family'] = 'family-b'
    with pytest.raises(SplitError, match='recorder provenance'):
        validate_window_membership(manifest, [sample])


def test_episode_adapter_reuses_validated_recorder_origin_metadata(tmp_path):
    game, recorder = record_game(tmp_path)
    recorder.advance(progress_action(game))
    recorder.finish(truncation_reason='split-fixture')
    episode = EpisodeReader(tmp_path, 'episode').manifest

    origin = origin_from_episode(episode).to_json()
    assert origin['source_id'] == origin['episode_id'] == episode['episode_id']
    assert origin['origin_family_id'] == episode['source_family']
    assert origin['groups'] == {
        'scenario': [episode['scenario_id']],
        'policy': [policy_group_id('script', None)],
    }
    assert origin['content_digest'].startswith('sha256:')


@pytest.mark.parametrize('proportions,seed', [
    ({'train': .8, 'test': .3}, 1),
    ({'train': 1.0, 'test': 0}, 1),
    ({'train': 1.0}, True),
    ({}, 1),
])
def test_invalid_arguments_rejected(proportions, seed):
    with pytest.raises(SplitError):
        build_split_manifest([source('a')], proportions=proportions, seed=seed,
                             split_version='dataset-v1')


def test_source_and_manifest_schemas_are_closed_frozen_and_self_validating():
    raw_source = source('a')
    frozen_source = OriginSourceV1(raw_source)
    raw_source['kind'] = 'changed-after-construction'
    assert frozen_source.to_json()['kind'] == 'episode'
    with pytest.raises(FrozenInstanceError):
        frozen_source._encoded = b'changed'

    manifest = build_split_manifest([frozen_source], proportions={'train': 1.0}, seed=4,
                                    split_version='dataset-v1')
    copied = manifest.to_json()
    copied['schema_version'] = 2
    with pytest.raises(SplitError, match='Unknown SplitManifest version'):
        SplitManifestV1(copied)
    copied = manifest.to_json()
    family = next(iter(copied['assignments']))
    copied['assignments'][family] = 'foreign'
    with pytest.raises(SplitError, match='Corrupt or non-canonical'):
        SplitManifestV1(copied)
    with pytest.raises(FrozenInstanceError):
        manifest._encoded = b'changed'


@pytest.mark.parametrize('damage', ['extra', 'version-bool', 'group-type', 'non-json'])
def test_origin_source_schema_rejects_unknown_versions_and_non_json_data(damage):
    raw = source('a')
    if damage == 'extra':
        raw['callable'] = 'os.system'
    elif damage == 'version-bool':
        raw['schema_version'] = True
    elif damage == 'group-type':
        raw['groups']['policy'] = ['valid', 1]
    else:
        raw['relationships'] = ({'kind': 'parent_episode', 'source_id': 'b'},)
    with pytest.raises(SplitError):
        OriginSourceV1(raw)


def test_python_hash_randomization_is_not_part_of_source_or_assignment_contract():
    # Use explicit digests: source_id/order are stable public identities, while
    # process-local hash() must never enter family or split assignment.
    sources = [source('a', content=digest('1')), source('b', content=digest('2'))]
    first = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                                 seed=42, split_version='dataset-v1')
    second = build_split_manifest(deepcopy(sources), proportions={'test': .5, 'train': .5},
                                  seed=42, split_version='dataset-v1')
    assert first.to_json() == second.to_json()


def test_process_hash_seed_and_permuted_worker_results_are_irrelevant():
    program = '''
import json
from tests.lab.test_splits import source
from botbowl.lab.splits import build_split_manifest
items = [source(name) for name in set(['a', 'b', 'c', 'd', 'e', 'f'])]
manifest = build_split_manifest(items, proportions={'train': .5, 'test': .5},
                                seed=42, split_version='v1')
print(json.dumps(manifest.to_json(), sort_keys=True))
'''
    outputs = [subprocess.check_output([sys.executable, '-c', program],
               env=dict(os.environ, PYTHONHASHSEED=value), text=True, timeout=30)
               for value in ('0', '12345')]
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('join_field', ['variant_group_id', 'semantic_origin_fingerprint', 'content_digest'])
def test_declared_or_exact_group_can_connect_different_family_labels(join_field):
    sources = [source('a', family='first'), source('b', family='second')]
    for item in sources:
        item[join_field] = 'near-variants' if join_field == 'variant_group_id' else digest('a')
    manifest = build_split_manifest(sources, proportions={'train': 1}, seed=0, split_version='v1')
    assert family_for(manifest, 'a') == family_for(manifest, 'b')


def test_duplicate_copy_does_not_change_assignment_or_family_identity():
    sources = [source('a'), source('b')]
    original = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                                    seed=0, split_version='v1')
    copy = dict(sources[0], source_id='copy')
    updated = build_split_manifest(sources + [copy], proportions={'train': .5, 'test': .5},
                                   seed=0, split_version='v2', previous_manifest=original)
    assert family_for(updated, 'copy') == family_for(original, 'a')
    assert updated.to_json()['assignments'] == original.to_json()['assignments']


def test_appended_bridge_is_detected_before_serving():
    sources = [source('a', family='first'), source('b', family='second')]
    manifest = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                                    seed=0, split_version='v1')
    bridge = source('bridge', relationships=[
        {'kind': 'parent_episode', 'source_id': 'a'},
        {'kind': 'variant_source', 'source_id': 'b'}])
    with pytest.raises(SplitError, match='connects existing splits'):
        validate_split_manifest(manifest, sources + [bridge])


def test_holdouts_cannot_silently_leave_a_requested_split_empty():
    sources = [source('a', scenarios=['ood']), source('b', scenarios=['ood'])]
    with pytest.raises(InsufficientOriginsError):
        build_split_manifest(sources, proportions={'train': .9, 'test': .1}, seed=0,
                             split_version='v1',
                             held_out_groups={'scenario': {'ood': 'test'}, 'policy': {}})


def test_unforced_families_fill_all_splits_even_when_holdouts_exceed_quotas():
    sources = [source(str(i), scenarios=['ood'] if i < 8 else []) for i in range(10)]
    manifest = build_split_manifest(sources, proportions={'train': .8, 'test': .1, 'validation': .1},
                                    seed=0, split_version='v1',
                                    held_out_groups={'scenario': {'ood': 'test'}, 'policy': {}})
    assert sorted(row['families'] for row in manifest.to_json()['counts']['splits'].values()) == [1, 1, 8]


def test_unordered_group_and_relationship_lists_have_canonical_identity():
    a = source('a', scenarios=['z', 'a'], relationships=[
        {'kind': 'parent_episode', 'source_id': 'b'},
        {'kind': 'replay_source', 'source_id': 'c'}])
    b = deepcopy(a)
    b['relationships'].reverse()
    b['groups']['scenario'].reverse()
    assert OriginSourceV1(a) == OriginSourceV1(b)


def test_policy_group_identity_is_unambiguous():
    assert policy_group_id('a:b', 'c') != policy_group_id('a', 'b:c')
    assert policy_group_id('a', None) != policy_group_id('a', 'unversioned')


def test_actual_snapshot_semantic_hash_groups_renamed_origins(tmp_path):
    from botbowl.lab.snapshot_io import write_snapshot
    from botbowl.lab.snapshots import capture_snapshot
    from tests.lab.test_snapshot_io import saved_file

    game, _, _, first = saved_file(tmp_path)
    second = write_snapshot(tmp_path / 'renamed.json', capture_snapshot(game),
                            provenance={'label': 'copy'})
    assert first.payload_digest != second.payload_digest
    assert first.semantic_state_hash == second.semantic_state_hash
    sources = [source('first', kind='snapshot', content=first.payload_digest,
                      semantic=first.semantic_state_hash),
               source('renamed', kind='snapshot', content=second.payload_digest,
                      semantic=second.semantic_state_hash)]
    manifest = build_split_manifest(sources, proportions={'train': 1}, seed=0, split_version='v1')
    assert manifest.to_json()['counts']['families'] == 1


@pytest.mark.parametrize('field', ['counts', 'algorithm', 'grouping_rule', 'source_set_digest', 'sources'])
def test_corrupt_manifest_metadata_is_rejected(field):
    manifest = build_split_manifest([source('a')], proportions={'train': 1}, seed=0,
                                    split_version='v1').to_json()
    if field == 'counts':
        manifest[field]['sources'] = True
    elif field in ('algorithm', 'grouping_rule'):
        manifest[field]['version'] = True
    elif field == 'source_set_digest':
        manifest[field] = digest('0')
    else:
        manifest[field][0]['family_id'] = 'wrong-family'
    with pytest.raises(SplitError):
        SplitManifestV1(manifest)
