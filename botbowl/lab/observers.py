"""DATA-08 pure, versioned perception protocols. No Game or policy is accepted.

Only ``features`` and ``present`` are model inputs. Metadata includes privileged
observer seeds and alignment identities; see docs/lab/observers.md.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math

from .channels import InputProfile, PRIMARY_PROFILE, make_channel, project_inputs
from .observations import ObservationV1
from .randomness import DERIVATION_ALGORITHM, GENERATOR, SeedSpec
from .records import context, integer, require
from .views import ENTITY_CHANNELS, ENTITY_UNITS


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _unit(path):
    # Share the native entity view's units; JSON integers remain integers.
    player = path[len('primary.players[].'):] if path.startswith('primary.players[].') else path
    if player in ENTITY_CHANNELS:
        return ENTITY_UNITS[ENTITY_CHANNELS.index(player)]
    if path.endswith(('.x', '.y')):
        return 'arena_cell'
    if path.startswith('derived.'):
        return {'derived.roll_probabilities[][]': 'probability',
                'derived.block_dice[][]': 'signed_dice'}.get(path, 'count')
    return 'count'


@dataclass(frozen=True)
class NumericNoiseV1:
    path: str
    distribution: str
    scale: float
    unit: str
    minimum: float
    maximum: float
    bounds: str = 'error'

    def __post_init__(self):
        require(type(self.path) is str and type(self.unit) is str, 'Expected noise path/unit')
        require(self.distribution in ('normal', 'uniform'), 'Unknown noise distribution')
        for value in (self.scale, self.minimum, self.maximum):
            require(type(value) in (int, float) and math.isfinite(value), 'Noise requires finite numbers')
        require(self.scale >= 0 and -2**53 <= self.minimum <= self.maximum <= 2**53,
                'Invalid noise scale or exact numeric bounds')
        require(self.bounds in ('error', 'clip'), 'Declare error or clip bounds behavior')


@dataclass(frozen=True)
class ObservationTransformSpecV1:
    hidden_fields: tuple = ()
    hidden_entities: tuple = ()
    noise: tuple = ()
    every_k: int = 1
    offset: int = 0
    revision: int = 1
    schema_version: int = 1

    def __post_init__(self):
        require(type(self.schema_version) is int and self.schema_version == 1, 'Unknown transform schema')
        integer(self.revision, 1)
        integer(self.every_k, 1)
        integer(self.offset, 0)
        require(self.offset < self.every_k, 'Offset must be smaller than every_k')
        for values in (self.hidden_fields, self.hidden_entities):
            require(type(values) is tuple and all(type(v) is str for v in values)
                    and len(set(values)) == len(values), 'Expected unique immutable selectors')
        require(type(self.noise) is tuple and all(type(n) is NumericNoiseV1 for n in self.noise),
                'Expected NumericNoiseV1 tuple')
        for noise in self.noise:
            noise.__post_init__()
        require(len({n.path for n in self.noise}) == len(self.noise), 'Duplicate noise field')

    def to_json(self):
        return {**asdict(self), 'hidden_fields': list(self.hidden_fields),
                'hidden_entities': list(self.hidden_entities), 'noise': [asdict(n) for n in self.noise]}

    @classmethod
    def from_json(cls, data):
        require(type(data) is dict and set(data) == set(cls.__dataclass_fields__),
                'Unknown or missing transform fields')
        require(all(type(data[k]) is list for k in ('hidden_fields', 'hidden_entities', 'noise')),
                'Expected transform arrays')
        try:
            return cls(**{**data, 'hidden_fields': tuple(data['hidden_fields']),
                          'hidden_entities': tuple(data['hidden_entities']),
                          'noise': tuple(NumericNoiseV1(**n) for n in data['noise'])})
        except TypeError as error:
            raise ValueError('Malformed noise fields') from error

    def validate_profile(self, profile):
        self.__post_init__()
        require(type(profile) is InputProfile, 'Expected InputProfile')
        profile.__post_init__()
        fields = dict(profile.fields)
        require(set(self.hidden_fields) <= set(fields), 'Hidden field is absent from the profile')
        for noise in self.noise:
            require(fields.get(noise.path) in ('integer', 'number'), 'Noise requires a selected numeric field')
            require(noise.unit == _unit(noise.path), 'Noise unit differs from the field unit')
            if fields[noise.path] == 'integer':
                require(int(noise.minimum) == noise.minimum and int(noise.maximum) == noise.maximum,
                        'Integer fields require integral bounds')
        return True


def _transform_projected(projected, spec, available_at, observer_seed):
    """Internal: input must come directly from project_inputs/iter_windows."""
    profile = InputProfile.from_json(projected['metadata']['profile'])
    spec.validate_profile(profile)
    context(available_at)
    # SeedSpec validates entropy and isolates this from all simulator streams.
    require(observer_seed is not None, 'Observer seed must be explicit')
    recipe = SeedSpec(observer_seed, available_at['episode_id'], 'observation', 'transforms-v1')
    kinds = dict(profile.fields)
    fields = projected['metadata']['channels'].get('primary', {}).get('fields', {})
    ids = fields.get('primary.players[].id', [])
    teams = fields.get('primary.teams[].id', [])
    balls = fields.get('primary.balls[].carrier.value', [])
    require(len(set(ids)) == len(ids) and len(set(teams)) == len(teams), 'Duplicate entity IDs')
    known = ids + ['ball:' + str(i) for i in range(len(balls))]
    require(set(spec.hidden_entities) <= set(known), 'Unknown hidden entity')
    retained = available_at['decision_seq'] % spec.every_k == spec.offset
    noises = {n.path: n for n in spec.noise}
    features, present = {}, {}

    def transform(value, path, indices=(), entity=None):
        if type(value) is list:
            values, masks = [], []
            for i, item in enumerate(value):
                key = entity
                if not indices:
                    if path.startswith('primary.players[].'):
                        key = ids[i]
                    elif path.startswith('primary.teams[].'):
                        key = teams[i]
                    elif path.startswith('primary.balls[].'):
                        key = 'ball:' + str(i)
                v, m = transform(item, path, indices + (i,), key)
                values.append(v)
                masks.append(m)
            return values, masks
        if not retained or path in spec.hidden_fields or entity in spec.hidden_entities or value is None:
            return None, False
        if path in noises:
            noise = noises[path]
            require(noise.minimum <= value <= noise.maximum, 'Source value outside declared noise bounds')
            # Entity IDs replace the outer row ordinal, so roster permutation is harmless.
            address = {'context': available_at, 'transform': spec.to_json(), 'path': path,
                       'entity': entity, 'indices': indices[1:] if entity is not None else indices}
            rng = SeedSpec(recipe.master_seed, recipe.episode_key, 'observation',
                           _digest(address)).generator()
            delta = (rng.normal(0, noise.scale) if noise.distribution == 'normal'
                     else rng.uniform(-noise.scale, noise.scale))
            value = float(value) + float(delta)
            require(math.isfinite(value), 'Noise overflow')
            if kinds[path] == 'integer':
                value = int(round(value))  # nearest integer, ties to even
            if noise.bounds == 'clip':
                value = max(noise.minimum, min(noise.maximum, value))
            require(noise.minimum <= value <= noise.maximum, 'Noisy value outside declared bounds')
            value = int(value) if kinds[path] == 'integer' else float(value)
        return value, True

    for path, value in projected['features'].items():
        features[path], present[path] = transform(value, path)
    return {'features': features, 'present': present, 'retained': retained,
            'metadata': {'schema_version': 1, 'transform': spec.to_json(),
                         'profile': profile.to_json(), 'available_at': deepcopy(available_at),
                         'units': {p: _unit(p) for p, t in profile.fields if t in ('integer', 'number')},
                         'dtypes': dict(profile.fields), 'missing': 'null-with-false-presence',
                         'order': ['subsample', 'hide', 'noise', 'round-integers', 'bounds'],
                         'rng': {'seed': recipe.to_json(), 'derivation': DERIVATION_ALGORITHM,
                                 'generator': GENERATOR, 'address_version': 1},
                         'aids': {'action_mask_input': profile.include_action_mask,
                                  'derived_inputs': [p for p, _ in profile.fields if p.startswith('derived.')]},
                         'sufficiency': 'not-asserted', 'policy_effect': 'offline-only'}}


def transform_observation(channels, spec, *, available_at, observer_seed, profile=PRIMARY_PROFILE):
    """Project authorized channels, then hide/noise copied leaves with parallel masks.

    Accept an ObservationV1, its plain JSON representation, or channel envelopes.
    Output is a masked projection, never a forged ObservationV1. Entity identity,
    seed recipes, and source metadata are not copied to features.
    """
    require(type(spec) is ObservationTransformSpecV1, 'Expected ObservationTransformSpecV1')
    if type(channels) is ObservationV1:
        channels = channels.to_json()
    if type(channels) is dict and 'schema_version' in channels:
        channels = {'primary': make_channel('primary', channels)}
    return _transform_projected(project_inputs(channels, profile), spec, available_at, observer_seed)


def iter_observed_windows(reader, window_spec, transform_spec, *, observer_seed,
                          split_manifest, source_id=None):
    """Keep every causal window/target; mask only the input history observations.

    Original context references, transition ranges, targets, action conditioning,
    split membership and branch boundaries are preserved by DATA-03. The caller
    must feed only inputs to a model, never target or privileged metadata.
    """
    from .windows import WindowSpecV1, iter_windows
    require(type(window_spec) is WindowSpecV1, 'Expected WindowSpecV1')
    require(observer_seed is not None, 'Observer seed must be explicit')
    SeedSpec(observer_seed, purpose='observation')
    require(type(transform_spec) is ObservationTransformSpecV1, 'Expected transform spec')
    transform_spec.validate_profile(window_spec.profile)
    stream = iter_windows(reader, window_spec, split_manifest=split_manifest, source_id=source_id)
    try:
        for sample in stream:
            masks, transforms = [], []
            for index, observation in enumerate(sample['inputs']['observations']):
                if observation is None:
                    masks.append(None)
                    transforms.append(None)
                    continue
                result = _transform_projected(
                    {'features': observation, 'metadata': sample['metadata']['history_projection'][index]},
                    transform_spec, sample['metadata']['history'][index]['available_at'], observer_seed)
                sample['inputs']['observations'][index] = result['features'] if result['retained'] else None
                sample['inputs']['presence'][index] = result['retained']
                masks.append(result['present'] if result['retained'] else None)
                transforms.append(result['metadata'])
            sample['inputs']['field_presence'] = masks
            sample['metadata']['observation_transforms'] = transforms
            yield sample
    finally:
        stream.close()


def build_ood_manifest(split_manifest, protocols, *, held_out, evaluation_split='test',
                       train_split='train', history, protocol_version):
    """Validate explicit holdouts against DATA-04 whole-family assignments.

    ``protocols`` maps every frozen source ID to its ScenarioSpecV1, input
    profile, transform spec, and home/away policy descriptors (plain JSON).
    ``held_out`` maps axes to nonempty lists of exact identities; use
    ``ood_protocol_axes`` to obtain those identities. No split is reassigned.
    This checks producer declarations; authenticate sources before freezing.
    """
    from .splits import validate_split_manifest
    from .windows import WindowSpecV1
    require(type(protocol_version) is str and bool(protocol_version), 'Declare an OOD protocol version')
    require(type(history) is WindowSpecV1, 'Declare available history with WindowSpecV1')
    history.__post_init__()
    frozen = validate_split_manifest(split_manifest).to_json()
    require(train_split != evaluation_split and
            {train_split, evaluation_split} <= set(frozen['proportions']), 'Incompatible OOD splits')
    require(type(protocols) is dict and set(protocols) ==
            {entry['source']['source_id'] for entry in frozen['sources']}, 'Incomplete OOD source inventory')
    inventory = []
    for entry in frozen['sources']:
        source = entry['source']
        protocol = protocols[source['source_id']]
        axes = ood_protocol_axes(protocol, source_version=source['source_version'])
        scenario = protocol['scenario']
        from .scenarios import ScenarioSpecV1
        spec = ScenarioSpecV1.from_json(scenario)
        require(set(source['groups']['scenario']) == {spec.variant_id} or
                set(source['groups']['scenario']) == {spec.scenario_id}, 'Scenario provenance mismatch')
        require(set(source['groups']['policy']) == set(axes['policy']), 'Policy provenance mismatch')
        inventory.append({'source_id': source['source_id'], 'family_id': entry['family_id'],
                          'split': frozen['assignments'][entry['family_id']], 'axes': axes,
                          'protocol': deepcopy(protocol),
                          'history': replace(history, profile=InputProfile.from_json(protocol['profile'])).to_json()})
    require(type(held_out) is dict and bool(held_out), 'Declare at least one OOD holdout')
    for axis, values in held_out.items():
        require(axis in inventory[0]['axes'] and type(values) is list and bool(values)
                and all(type(v) is str for v in values) and len(set(values)) == len(values),
                'Unknown or malformed OOD holdout')
        for value in values:
            matches = [row for row in inventory if value in row['axes'][axis]]
            require(bool(matches), 'Unknown held-out identity')
            require(all(row['split'] == evaluation_split for row in matches),
                    'Held-out identity leaks outside evaluation split')
    require(any(row['split'] == train_split for row in inventory), 'No training origins')
    # Every evaluation family must be justified by at least one declared holdout.
    held_families = {row['family_id'] for row in inventory
                     if any(set(values) & set(row['axes'][axis]) for axis, values in held_out.items())}
    require(all(row['family_id'] in held_families for row in inventory if row['split'] == evaluation_split),
            'Evaluation family has no declared OOD factor')
    return {'schema_version': 1, 'protocol_version': protocol_version,
            'split_manifest': frozen, 'sources': inventory, 'held_out': deepcopy(held_out),
            'train_split': train_split, 'evaluation_split': evaluation_split,
            'history': history.to_json(), 'variation': 'independent-declared-axes',
            'configuration': 'ScenarioSpecV1 variant and RulesDescriptor; never observation noise',
            'identifiability': 'not-guaranteed; report observational aliasing',
            'policy_effect': 'offline transforms; restricted-perception policies require separate opt-in'}


def ood_protocol_axes(protocol, *, source_version):
    """Return exact held-out identities after validating all protocol versions."""
    from .scenarios import ScenarioSpecV1
    from .splits import policy_group_id
    require(type(protocol) is dict and set(protocol) == {'scenario', 'profile', 'transform', 'policies'},
            'Expected complete OOD protocol')
    scenario = ScenarioSpecV1.from_json(protocol['scenario'])
    profile = InputProfile.from_json(protocol['profile'])
    transform = ObservationTransformSpecV1.from_json(protocol['transform'])
    transform.validate_profile(profile)
    require(type(source_version) is str and bool(source_version), 'Declare source version')
    policies = protocol['policies']
    require(type(policies) is dict and set(policies) == {'home', 'away'}, 'Declare both policies')
    for policy in policies.values():
        require(type(policy) is dict and set(policy) == {'id', 'version'} and policy['version'] is not None,
                'OOD requires explicit policy versions')
    return {'scenario': [scenario.variant_id], 'profile': [_digest(profile.to_json())],
            'transform': [_digest(transform.to_json())],
            'policy': sorted({policy_group_id(p['id'], p['version']) for p in policies.values()}),
            'source_version': [source_version], 'rules': [_digest(scenario.rules.to_json())]}


def validate_ood_manifest(data):
    """Recompute the OOD contract, detecting stale identities and assignments."""
    from .windows import WindowSpecV1
    require(type(data) is dict, 'Expected OOD manifest')
    try:
        rebuilt = build_ood_manifest(
            data['split_manifest'], {s['source_id']: s['protocol'] for s in data['sources']},
            held_out=data['held_out'], evaluation_split=data['evaluation_split'],
            train_split=data['train_split'], history=WindowSpecV1.from_json(data['history']),
            protocol_version=data['protocol_version'])
    except (KeyError, TypeError) as error:
        raise ValueError('Malformed OOD manifest') from error
    require(data == rebuilt and type(data['schema_version']) is int, 'Stale or incompatible OOD manifest')
    return rebuilt
