"""Perception, RNG isolation, causal timing and family-level OOD canaries."""
from copy import deepcopy
from dataclasses import replace
import json
import pickle
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from botbowl.lab.channels import PRIMARY_PROFILE, InputProfile, make_channel, project_inputs
from botbowl.lab.observations import observe
from botbowl.lab.observers import (NumericNoiseV1, ObservationTransformSpecV1, transform_observation,
                                   iter_observed_windows, build_ood_manifest, ood_protocol_axes,
                                   validate_ood_manifest)
from botbowl.lab.randomness import capture_stream
from botbowl.lab.scenarios import scenario_spec
from botbowl.lab.splits import build_split_manifest, policy_group_id
from botbowl.lab.views import entity_view
from botbowl.lab.windows import WindowSpecV1, iter_windows
from tests.lab.test_observations import episode
from tests.lab.test_splits import source
from tests.lab.test_windows import template, enumerable, freeze, write_episode  # noqa: F401

CTX = {'episode_id': 'test', 'branch_id': 'root', 'decision_seq': 0, 'event_seq': 0,
       'activation_seq': None, 'team_turn_seq': None, 'drive_seq': None, 'half': None, 'round': None}
SCORE = 'primary.teams[].score'
MA = 'primary.players[].attributes.ma'


@pytest.fixture
def public():
    probe, control = episode(3)
    try:
        yield observe(probe.game, control, 'home').to_json()
    finally:
        probe.game.close()


def apply(public, spec=ObservationTransformSpecV1(), seed=17, ctx=CTX, profile=PRIMARY_PROFILE):
    return transform_observation(public, spec, available_at=ctx, observer_seed=seed, profile=profile)


def noise(path=MA, distribution='normal', scale=2, unit='attribute_point', lo=0, hi=30, bounds='clip'):
    return NumericNoiseV1(path, distribution, scale, unit, lo, hi, bounds)


def leaves(value):
    if isinstance(value, (list, dict)):
        for child in value.values() if isinstance(value, dict) else value:
            yield from leaves(child)
    else:
        yield value


def test_identity_copy_presence_and_view_units(public):
    original = deepcopy(public)
    result = apply(public)
    expected = project_inputs({'primary': make_channel('primary', public)})
    assert result['features'] == expected['features']
    for path, values in result['features'].items():
        assert list(leaves(result['present'][path])) == [v is not None for v in leaves(values)]
    assert result['metadata']['units'][MA] == entity_view(public).metadata['units'][4]
    result['features'][MA][0] = 999
    assert public == original
    assert apply(public)['features'][MA][0] != 999


def test_hide_fields_entities_total_aliasing(public):
    hidden = ObservationTransformSpecV1(hidden_fields=(SCORE,), hidden_entities=('home:0',))
    result = apply(public, hidden)
    assert result['features'][SCORE] == [None, None]
    assert result['present'][SCORE] == [False, False]
    assert result['features'][MA][0] is None
    assert result['present'][MA][0] is False
    assert result['features'][MA][1] == public['players'][1]['attributes']['ma']
    other = deepcopy(public)
    other['teams'][0]['score'] += 1
    # Observational aliasing: different scores become identical model inputs.
    assert apply(other, hidden)['features'] == result['features']
    total = apply(public, ObservationTransformSpecV1(hidden_fields=tuple(p for p, _ in PRIMARY_PROFILE.fields)))
    assert all(v is None for v in leaves(total['features']))
    assert all(v is False for v in leaves(total['present']))


@pytest.mark.parametrize('spec', [ObservationTransformSpecV1(hidden_fields=('primary.no_such_field',)),
                                  ObservationTransformSpecV1(hidden_entities=('missing:0',)),
                                  ObservationTransformSpecV1(noise=(noise('primary.match.game_over'),)),
                                  ObservationTransformSpecV1(noise=(noise(unit='arena_cell'),))])
def test_unknown_and_incompatible_fields(public, spec):
    with pytest.raises(ValueError):
        apply(public, spec)


def test_empty_arrays_and_original_absence(public):
    public['players'] = []
    public['balls'] = []
    result = apply(public, ObservationTransformSpecV1(noise=(noise(),)))
    assert result['features'][MA] == result['present'][MA] == []
    assert result['features']['primary.match.drive.value'] is None
    assert result['present']['primary.match.drive.value'] is False


@pytest.mark.parametrize('distribution', ['normal', 'uniform'])
def test_noise_stateless_reordered_workers_entities_and_namespaces(public, distribution):
    spec = ObservationTransformSpecV1(noise=(noise(distribution=distribution),))
    contexts = [{**CTX, 'decision_seq': i} for i in range(12)]
    baseline = [apply(public, spec, ctx=c) for c in contexts]
    with ThreadPoolExecutor(3) as pool:
        reordered = list(pool.map(lambda c: apply(public, spec, ctx=c), reversed(contexts)))
    assert baseline == list(reversed(reordered))
    assert baseline[0] == apply(public, spec)
    permuted = deepcopy(public)
    permuted['players'].reverse()
    assert apply(permuted, spec)['features'][MA] == list(reversed(baseline[0]['features'][MA]))
    assert baseline[0]['features'][MA] != apply(public, spec, seed=19)['features'][MA]
    assert baseline[0]['features'][MA] != apply(public, spec, ctx={**CTX, 'branch_id': 'fork'})['features'][MA]
    assert all(type(x) is int for x in baseline[0]['features'][MA])
    assert all(type(x) is bool for x in baseline[0]['present'][MA])


def test_numeric_bounds_rounding_and_schema(public):
    fixed = ObservationTransformSpecV1(noise=(noise(SCORE, scale=1e6, unit='count', lo=0, hi=0),))
    assert apply(public, fixed)['features'][SCORE] == [0, 0]
    with pytest.raises(ValueError, match='outside declared bounds'):
        apply(public, replace(fixed, noise=(replace(fixed.noise[0], bounds='error'),)))
    with pytest.raises(ValueError, match='Source value'):
        apply(public, ObservationTransformSpecV1(noise=(noise(lo=20, hi=30),)))
    with pytest.raises(ValueError, match='integral'):
        apply(public, ObservationTransformSpecV1(noise=(noise(lo=.5),)))
    for value in (float('inf'), float('nan'), -1):
        with pytest.raises(ValueError):
            noise(scale=value)
    assert ObservationTransformSpecV1.from_json(fixed.to_json()) == fixed
    with pytest.raises(ValueError):
        ObservationTransformSpecV1.from_json({**fixed.to_json(), 'schema_version': 2})


def test_leakage_and_aids(public):
    channels = {'primary': make_channel('primary', public),
                'evaluation': {'FUTURE_CANARY': object()}, 'privileged': {'RNG_CANARY': object()}}
    channels['primary']['metadata']['provenance'] = 'METADATA_CANARY'
    assert 'CANARY' not in json.dumps(apply(channels))
    channels['primary']['data']['players'][0]['future'] = 'FUTURE_CANARY'
    with pytest.raises(ValueError):
        apply(channels)
    del channels['primary']['data']['players'][0]['future']
    profile = InputProfile('custom', ((SCORE, 'integer'), ('control.action_mask[]', 'boolean')), True)
    channels['control'] = make_channel('control', {'action_mask': [True, False], 'action_ids': ['ID_CANARY']})
    result = apply(channels, ObservationTransformSpecV1(hidden_fields=(SCORE,)), profile=profile)
    assert result['features']['control.action_mask[]'] == [True, False]
    assert result['metadata']['aids']['action_mask_input'] is True
    assert result['metadata']['sufficiency'] == 'not-asserted'
    assert 'CANARY' not in json.dumps(result)


def test_observer_seed_and_reads_leave_game_rng_and_trajectory_unchanged():
    from tests.baseline import progress_action
    trajectories = []
    for seed in (17, 99):
        probe, control = episode(1, seed=7)
        game = probe.game
        game.init()
        trajectory = []
        try:
            for i in range(8):
                public = observe(game, control, 'home')
                before = pickle.dumps(game)
                global_rng = capture_stream(np.random)
                for _ in range(seed % 3 + 1):
                    apply(public, ObservationTransformSpecV1(noise=(noise(),)), seed=seed,
                          ctx={**CTX, 'decision_seq': i})
                assert pickle.dumps(game) == before
                assert capture_stream(np.random) == global_rng
                trajectory.append(public.to_json())
                game.step(progress_action(game))
            trajectories.append(trajectory)
        finally:
            game.close()
    assert trajectories[0] == trajectories[1]


@pytest.mark.parametrize('k', [1, 3])
def test_subsample_preserves_all_cutoffs_targets_ranges_and_causal_availability(tmp_path, template, k):
    reader, _ = enumerable(tmp_path, template)
    spec = WindowSpecV1(4, 2, mode='action_conditioned', profile=InputProfile.from_json(reader.manifest['profile']))
    split = freeze(reader)
    full = list(iter_windows(reader, spec, split_manifest=split))
    partial = list(iter_observed_windows(reader, spec, ObservationTransformSpecV1(every_k=k),
                                        observer_seed=7, split_manifest=split))
    assert len(full) == len(partial) == 6
    for old, new in zip(full, partial):
        assert old['targets'] == new['targets']
        assert old['inputs']['action'] == new['inputs']['action']
        for key in ('history', 'source_transitions', 'target_observations', 'cutoff'):
            assert old['metadata'][key] == new['metadata'][key]
        for index, ref in enumerate(new['metadata']['history']):
            retained = ref is not None and ref['available_at']['decision_seq'] % k == 0
            assert new['inputs']['presence'][index] == retained
            if retained:
                assert old['inputs']['observations'][index] == new['inputs']['observations'][index]
            else:
                assert new['inputs']['observations'][index] is None


def test_subsample_branches_keep_original_context(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    manifest = reader.manifest
    parent = deepcopy(rows['primary'][3]['context'])
    manifest['branches'].append({'branch_id': 'fork', 'parent': parent})
    rows['primary'].insert(4, deepcopy(rows['primary'][3]))
    for row in rows['primary'][4:]:
        row['observation_id'] += 1
        row['context']['branch_id'] = 'fork'
    for row in rows['transitions'][3:]:
        row['before'] = {**row['before'], 'branch_id': 'fork'}
        row['after'] = {**row['after'], 'branch_id': 'fork'}
        row['transition_id'][1] = 'fork'
        row['pre_observation'] += 1
        row['post_observation'] += 1
    manifest['final_observation'] += 1
    manifest['final_context'] = rows['primary'][-1]['context']
    reader = write_episode(reader.directory, manifest, rows)
    spec = WindowSpecV1(3, 2, profile=InputProfile.from_json(manifest['profile']))
    samples = list(iter_observed_windows(reader, spec, ObservationTransformSpecV1(every_k=2),
                                        observer_seed=7, split_manifest=freeze(reader)))
    assert len(samples) == 6
    for sample in samples:
        cutoff = sample['metadata']['cutoff']
        assert all(ref is None or ref['available_at']['branch_id'] == cutoff['branch_id']
                   for ref in sample['metadata']['history'])
    assert samples[3]['inputs']['presence'] == [False, False, False]


def ood_fixture():
    policies = {side: {'id': 'script', 'version': '1'} for side in ('home', 'away')}
    protocols = {}
    sources = []
    for name, recipe in [('id', 'movement'), ('ood', 'pickup')]:
        scenario = scenario_spec(recipe)
        protocols[name] = {'scenario': scenario.to_json(), 'profile': PRIMARY_PROFILE.to_json(),
                           'transform': ObservationTransformSpecV1().to_json(), 'policies': policies}
        sources.append(source(name, scenarios=[scenario.variant_id],
                              policies=[policy_group_id('script', '1')]))
    variant = ood_protocol_axes(protocols['ood'], source_version='fixture-v1')['scenario'][0]
    split = build_split_manifest(sources, proportions={'train': .5, 'test': .5}, seed=7,
                                 split_version='ood-v1', held_out_groups={'scenario': {variant: 'test'}, 'policy': {}})
    return split, protocols, {'scenario': [variant]}


def test_ood_holdouts_and_versions_roundtrip():
    split, protocols, held_out = ood_fixture()
    manifest = build_ood_manifest(split, protocols, held_out=held_out, history=WindowSpecV1(4, 2),
                                  protocol_version='experiment-v1')
    assert validate_ood_manifest(json.loads(json.dumps(manifest))) == manifest
    assert {s['split'] for s in manifest['sources']} == {'train', 'test'}
    protocols['ood']['scenario']['parameters']['distance'] = 2
    assert manifest['sources'][1]['protocol']['scenario']['parameters']['distance'] == 1
    with pytest.raises(ValueError, match='provenance'):
        build_ood_manifest(split, protocols, held_out=held_out, history=WindowSpecV1(4, 2),
                           protocol_version='experiment-v1')
    corrupt = deepcopy(manifest)
    corrupt['sources'][0]['axes']['source_version'] = ['wrong']
    with pytest.raises(ValueError, match='Stale'):
        validate_ood_manifest(corrupt)


@pytest.mark.parametrize('bad', ['unknown', 'leakage', 'scenario-version', 'policy-version',
                                  'incompatible-size', 'profile-leak', 'incomplete'])
def test_ood_invalid_protocols_fail_closed(bad):
    split, protocols, held_out = ood_fixture()
    if bad == 'unknown':
        held_out = {'scenario': ['not-present']}
    elif bad == 'leakage':
        held_out = {'policy': [policy_group_id('script', '1')]}
    elif bad == 'scenario-version':
        protocols['ood']['scenario']['version'] = 9
    elif bad == 'policy-version':
        protocols['ood']['policies'] = {s: {'id': 'script', 'version': None} for s in ('home', 'away')}
    elif bad == 'incompatible-size':
        protocols['ood']['scenario']['size'] = 2
    elif bad == 'profile-leak':
        protocols['ood']['profile']['fields'].append({'path': 'privileged.rng', 'type': 'integer'})
    else:
        del protocols['ood']
    with pytest.raises(ValueError):
        build_ood_manifest(split, protocols, held_out=held_out, history=WindowSpecV1(4, 2),
                           protocol_version='experiment-v1')


def test_subsampling_offset_composition_and_invalid_dropped_frame(public):
    spec = ObservationTransformSpecV1(every_k=3, offset=1, hidden_fields=(MA,),
                                     noise=(noise(lo=20, hi=30),))
    assert apply(public, spec)['retained'] is False
    retained = apply(public, spec, ctx={**CTX, 'decision_seq': 1})
    assert retained['retained'] is True
    assert all(v is None for v in retained['features'][MA])
    public['players'][0]['future'] = 'CANARY'
    with pytest.raises(ValueError):
        apply(public, spec)


def test_derived_numbers_and_observer_revision(public):
    path = 'derived.roll_probabilities[][]'
    profile = InputProfile('custom', ((path, 'number'),))
    channels = {'derived': make_channel('derived', {'roll_probabilities': [[0.0, 1.0], []]})}
    spec = ObservationTransformSpecV1(noise=(noise(path, unit='probability', scale=.2, lo=0, hi=1),))
    output = apply(channels, spec, profile=profile)
    assert output['features'][path][1] == []
    assert all(type(v) is float and 0 <= v <= 1 for v in output['features'][path][0])
    assert output['present'][path] == [[True, True], []]
    revised = apply(channels, replace(spec, revision=2), profile=profile)
    # Stream provenance changes even if clipping happens to equalize a value.
    assert revised['metadata']['transform']['revision'] == 2
    assert revised['features'] != output['features']


def test_ood_profile_transform_and_source_version_axes():
    split, protocols, held_out = ood_fixture()
    protocols['ood']['profile'] = InputProfile('custom', ((SCORE, 'integer'),)).to_json()
    protocols['ood']['transform'] = ObservationTransformSpecV1(hidden_fields=(SCORE,)).to_json()
    axes = ood_protocol_axes(protocols['ood'], source_version='fixture-v1')
    for axis in ('profile', 'transform'):
        manifest = build_ood_manifest(split, protocols, held_out={axis: axes[axis]},
                                      history=WindowSpecV1(3, 1), protocol_version='profile-ood-v1')
        row = next(s for s in manifest['sources'] if s['source_id'] == 'ood')
        assert row['history']['profile'] == protocols['ood']['profile']
        assert validate_ood_manifest(manifest) == manifest
    with pytest.raises(ValueError, match='leaks'):
        build_ood_manifest(split, protocols, held_out={'source_version': ['fixture-v1']},
                           history=WindowSpecV1(3, 1), protocol_version='profile-ood-v1')


@pytest.mark.parametrize('seed', [None, True, -1, 2**256])
def test_explicit_seed_required_even_for_empty_windows(tmp_path, template, seed):
    reader, _ = enumerable(tmp_path, template, count=0, end='truncated')
    with pytest.raises(ValueError):
        list(iter_observed_windows(reader, WindowSpecV1(1, 1), ObservationTransformSpecV1(),
                                   observer_seed=seed, split_manifest=freeze(reader)))
