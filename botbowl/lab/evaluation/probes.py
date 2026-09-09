"""Data-only probe export and embedding alignment. No encoder or head is loaded.

Use validated DATA-03 windows and EVAL-01 records. See docs/lab/probes.md.
"""
from dataclasses import dataclass
import math
import re

from botbowl.lab.evaluation.oracle import EvaluationRecord, LabelRequest, label_spec, _value
from botbowl.lab.records import (_Record, context, decode_json, encode_json,
                                 identifier, integer, keys, require)
from botbowl.lab.splits import validate_split_manifest, validate_window_membership
from botbowl.lab.windows import WindowSpecV1, validate_availability


@dataclass(frozen=True)
class ProbeSpec:
    label: str
    entity_id: str
    related_entity_id: str = None
    component: str = None
    target_offset: int = 0

    def __post_init__(self):
        LabelRequest(self.label, self.entity_id, self.related_entity_id)
        spec = label_spec(self.label)
        require(spec.category in ('state_fact', 'exact_rule_quantity'),
                'Reference probes use catalogue facts and exact quantities only')
        integer(self.target_offset)
        require(self.component in ('x', 'y') if spec.value_type == 'position'
                else self.component is None, 'Position needs x/y; other labels have no component')

    @property
    def kind(self):
        return ('classification' if label_spec(self.label).value_type in
                ('boolean', 'location', 'nullable_player_id') else 'regression')

    def to_json(self):
        return dict(self.__dict__)


def sample_id(window, entity_id):
    """Decision ID names the selected primitive, including its branch."""
    cutoff = window['metadata']['cutoff']
    return {'episode_id': cutoff['episode_id'], 'entity_id': entity_id,
            'decision_id': [cutoff['episode_id'], cutoff['branch_id'], cutoff['decision_seq'] + 1]}


def _id(value):
    keys(value, ('episode_id', 'entity_id', 'decision_id'))
    identifier(value['episode_id'])
    identifier(value['entity_id'])
    decision = value['decision_id']
    require(type(decision) is list and len(decision) == 3, 'Expected full decision ID')
    identifier(decision[0])
    identifier(decision[1])
    integer(decision[2], 1)
    require(decision[0] == value['episode_id'], 'Foreign decision ID')
    return (value['episode_id'], value['entity_id'], decision[1], decision[2])


def _vector(value):
    require(type(value) is list and len(value) > 0, 'Expected nonempty numeric vector')
    require(all(type(x) in (int, float) and math.isfinite(x) for x in value),
            'Vectors must contain finite numeric data')


def _raw_selector(selector, spec):
    keys(selector, ('field', 'path'))
    require(selector['field'] in dict(spec.profile.fields), 'Raw field is outside input profile')
    require(type(selector['path']) is list, 'Raw path must be a data path')
    for part in selector['path']:
        require(type(part) is str or type(part) is int and part >= 0, 'Invalid raw path')


def _raw_scalar(observation, selector):
    if observation is None:
        return [0.0, 0.0]
    value = observation[selector['field']]
    for part in selector['path']:
        if value is None:
            return [0.0, 0.0]
        if type(value) is dict and set(value) == {'value', 'present'}:
            require(type(value['present']) is bool, 'Invalid feature presence mask')
            if not value['present']:
                require(value['value'] is None, 'Absent feature must have null value')
                return [0.0, 0.0]
        require((type(value) is list and type(part) is int and part < len(value)) or
                (type(value) is dict and part in value), 'Raw path does not resolve')
        value = value[part]
    if value is None:
        return [0.0, 0.0]
    require(type(value) in (bool, int, float) and math.isfinite(value),
            'Raw selectors must resolve numeric/boolean scalars')
    return [float(value), 1.0]


def _window(window, spec):
    """Recheck causal references; payload authenticity belongs to iter_windows."""
    meta = window['metadata']
    cutoff = meta['cutoff']
    context(cutoff)
    require(window['origin']['episode_id'] == cutoff['episode_id'] and
            meta['branch_id'] == cutoff['branch_id'], 'Foreign window cutoff')
    for side, refs, size in (('inputs', 'history', spec.history_length),
                             ('targets', 'target_observations', spec.horizon)):
        data = window[side]
        require(len(data['observations']) == len(data['presence']) == len(meta[refs]) == size,
                'Window dimensions disagree')
        for i, (obs, present, ref) in enumerate(zip(data['observations'], data['presence'], meta[refs])):
            require(type(present) is bool and present == (obs is not None) == (ref is not None),
                    'Invalid window presence mask')
            if not present:
                continue
            at = ref['available_at']
            context(at)
            require(all(at[f] == cutoff[f] for f in ('episode_id', 'branch_id')),
                    'Window crosses episode/branch')
            if side == 'inputs':
                require(set(obs) == set(dict(spec.profile.fields)), 'Input fields disagree with profile')
                for field, _ in spec.profile.fields:
                    validate_availability(field, at, cutoff, spec)
                require(at['decision_seq'] == cutoff['decision_seq'] -
                        (size - i - 1) * spec.history_stride, 'Misaligned history reference')
            else:
                require(at['decision_seq'] == cutoff['decision_seq'] + i + 1 and
                        at['event_seq'] >= cutoff['event_seq'], 'Future outside declared horizon')
    require(meta['history'][-1] is not None and
            meta['history'][-1]['available_at'] == cutoff, 'Missing exact cutoff observation')


def _class(value):
    # Null carrier is a real class; unavailable labels are handled separately.
    return 'null' if value is None else str(value).lower() if type(value) is bool else value


@dataclass(frozen=True, init=False)
class ProbeTask(_Record):
    """Immutable bounded JSON task; arrays and targets are detached on read."""

    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'spec', 'label', 'kind', 'unit', 'window_spec',
                    'raw_features', 'literal_in_observation', 'literal_in_input_profile',
                    'split_manifest', 'metrics', 'samples'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'Unknown probe task version')
        keys(data['spec'], ProbeSpec.__dataclass_fields__)
        spec = ProbeSpec(**data['spec'])
        label = label_spec(spec.label)
        require(data['label'] == label.to_json() and data['kind'] == spec.kind and
                data['unit'] == label.unit, 'Probe target metadata mismatch')
        window = WindowSpecV1.from_json(data['window_spec'])
        require(spec.target_offset <= window.horizon, 'Target outside window horizon')
        for selector in data['raw_features']:
            _raw_selector(selector, window)
        require(bool(data['raw_features']), 'At least one raw feature is required')
        literal = label.observation_path is not None and spec.label != 'team.possession'
        in_profile = literal and spec.target_offset == 0 and any(
            ('primary.' + label.observation_path == field or
             ('primary.' + label.observation_path).startswith(field + '.'))
            for field, _ in window.profile.fields)
        require(type(data['literal_in_observation']) is bool and data['literal_in_observation'] == literal and
                type(data['literal_in_input_profile']) is bool and data['literal_in_input_profile'] == in_profile,
                'Literal-target disclosure mismatch')
        require(data['metrics'] == (['accuracy', 'per_class'] if spec.kind == 'classification'
                                   else ['mae', 'rmse', 'train_target_scale']), 'Metric contract mismatch')
        split = validate_split_manifest(data['split_manifest']).to_json()
        by_source = {entry['source']['source_id']: entry for entry in split['sources']}
        require(type(data['samples']) is list and bool(data['samples']), 'Empty probe task')
        validate_window_membership(split, data['samples'])
        seen = set()
        for row in data['samples']:
            keys(row, ('id', 'origin', 'family_id', 'split', 'cutoff', 'target_context',
                       'present', 'target', 'unavailability', 'raw'))
            identity = _id(row['id'])
            require(identity not in seen, 'Duplicate probe sample ID')
            seen.add(identity)
            require(row['id']['entity_id'] == spec.entity_id and
                    row['id']['episode_id'] == row['origin']['episode_id'], 'Misaligned sample ID')
            require(row['family_id'] == by_source[row['origin']['source_id']]['family_id'],
                    'Origin family mismatch')
            context(row['cutoff'])
            require(row['id'] == sample_id({'metadata': {'cutoff': row['cutoff']}}, spec.entity_id),
                    'Sample ID and cutoff disagree')
            target_ctx = row['target_context']
            if target_ctx is not None:
                context(target_ctx)
                require(all(target_ctx[f] == row['cutoff'][f] for f in ('episode_id', 'branch_id')) and
                        target_ctx['decision_seq'] == row['cutoff']['decision_seq'] + spec.target_offset and
                        target_ctx['event_seq'] >= row['cutoff']['event_seq'], 'Target outside declared horizon')
                if spec.target_offset == 0:
                    require(target_ctx == row['cutoff'], 'Current target context mismatch')
            require(type(row['present']) is bool, 'Invalid target presence mask')
            if row['present']:
                require(target_ctx is not None and row['unavailability'] is None, 'Missing target context')
                if spec.kind == 'regression':
                    require(type(row['target']) in (int, float) and math.isfinite(row['target']),
                            'Expected numeric target')
                    if spec.component is not None:
                        integer(row['target'])
                    else:
                        _value(label.value_type, row['target'])
                else:
                    require(type(row['target']) is str, 'Expected categorical target')
                    if label.value_type == 'boolean':
                        require(row['target'] in ('true', 'false'), 'Invalid boolean class')
                    else:
                        _value(label.value_type, None if label.value_type == 'nullable_player_id'
                               and row['target'] == 'null' else row['target'])
            else:
                require(row['target'] is None and type(row['unavailability']) is dict,
                        'Absent target must be null with a reason')
            _vector(row['raw'])
            require(len(row['raw']) == 2 * len(data['raw_features']) * window.history_length,
                    'Raw dimensions disagree')
            require(all(x in (0.0, 1.0) for x in row['raw'][1::2]), 'Invalid raw presence mask')
            require(all(present or value == 0 for value, present in zip(row['raw'][::2], row['raw'][1::2])),
                    'Absent raw features must use zero padding')


def export_probe_task(windows, records, spec, *, split_manifest, raw_features):
    """Join one catalogue target to DATA-03 windows by full captured context.

    Missing future slots stay masked; missing requested label records are errors.
    Unrelated labels may coexist in the sidecar. No label enters raw features.
    """
    require(type(spec) is ProbeSpec, 'Expected ProbeSpec')
    spec.__post_init__()
    windows = list(windows)
    require(bool(windows), 'No windows supplied')
    # Reject executable/custom objects before navigating any input containers.
    windows = decode_json(encode_json(windows))
    raw_features = decode_json(encode_json(raw_features))
    split = validate_split_manifest(split_manifest).to_json()
    validate_window_membership(split, windows)
    by_source = {entry['source']['source_id']: entry for entry in split['sources']}
    index = {}
    for record in records:
        require(type(record) is EvaluationRecord, 'Expected validated EvaluationRecord')
        row = record.to_json()
        EvaluationRecord.from_json(row)
        require(record.key not in index, 'Duplicate evaluation record ID')
        index[record.key] = row
    window_spec = WindowSpecV1.from_json(windows[0]['metadata']['spec'])
    require(spec.target_offset <= window_spec.horizon, 'Target outside window horizon')
    for selector in raw_features:
        _raw_selector(selector, window_spec)
    samples = []
    for window in windows:
        require(window['metadata']['spec'] == window_spec.to_json(), 'Mixed window specifications')
        _window(window, window_spec)
        meta = window['metadata']
        family = by_source[window['origin']['source_id']]['family_id']
        require(meta['family_id'] == family and meta['split_version'] == split['split_version'],
                'Window family/split version mismatch')
        cutoff = meta['cutoff']
        ref = (meta['history'][-1] if spec.target_offset == 0 else
               meta['target_observations'][spec.target_offset - 1])
        target_context = None if ref is None else ref['available_at']
        value, present = None, False
        reason = {'code': 'missing_future', 'reason': 'Window target slot is absent.'}
        if target_context is not None:
            key = tuple(target_context[f] for f in ('episode_id', 'branch_id', 'event_seq', 'decision_seq')) + (
                spec.entity_id, spec.related_entity_id, spec.label, label_spec(spec.label).version)
            require(key in index, 'Requested evaluation label is missing at target context')
            record = index[key]
            require(record['context'] == target_context and record['available_at'] == target_context,
                    'Label is misaligned or available after target boundary')
            present = record['status'] == 'available'
            reason = record['unavailability']
            if present:
                value = record['value'] if spec.component is None else record['value'][spec.component]
                if spec.kind == 'classification':
                    value = _class(value)
        raw = []
        for observation in window['inputs']['observations']:
            for selector in raw_features:
                raw.extend(_raw_scalar(observation, selector))
        samples.append({'id': sample_id(window, spec.entity_id), 'origin': window['origin'],
                        'family_id': family, 'split': window['split'], 'cutoff': cutoff,
                        'target_context': target_context, 'present': present, 'target': value,
                        'unavailability': reason, 'raw': raw})
    samples.sort(key=lambda row: _id(row['id']))
    label = label_spec(spec.label)
    literal = label.observation_path is not None and spec.label != 'team.possession'
    return ProbeTask({'schema_version': 1, 'spec': spec.to_json(), 'label': label.to_json(),
                      'kind': spec.kind, 'unit': label.unit, 'window_spec': window_spec.to_json(),
                      'raw_features': raw_features, 'literal_in_observation': literal,
                      'literal_in_input_profile': literal and spec.target_offset == 0 and any(
                          ('primary.' + label.observation_path == field or
                           ('primary.' + label.observation_path).startswith(field + '.'))
                          for field, _ in window_spec.profile.fields),
                      'split_manifest': split,
                      'metrics': ['accuracy', 'per_class'] if spec.kind == 'classification'
                      else ['mae', 'rmse', 'train_target_scale'], 'samples': samples})


def align_embeddings(task, bundle):
    """Return a defensive ID-sorted join, requiring an exact row set, even masks.

    Arrays use plain JSON numeric lists. Encoder provenance is an inert identity,
    not a path to load; the producer must attest to the declared causal inputs.
    """
    require(type(task) is ProbeTask, 'Expected validated ProbeTask')
    data = task.to_json()
    bundle = decode_json(encode_json(bundle))
    keys(bundle, ('schema_version', 'encoder_id', 'encoder_revision', 'observation_view', 'window_spec', 'rows'))
    require(type(bundle['schema_version']) is int and bundle['schema_version'] == 1, 'Unknown embedding version')
    identifier(bundle['encoder_id'])
    require(type(bundle['encoder_revision']) is str and
            re.fullmatch(r'sha256:[0-9a-f]{64}', bundle['encoder_revision']) is not None,
            'Expected encoder artifact SHA-256 identity')
    require(type(bundle['observation_view']) is str and 0 < len(bundle['observation_view']) <= 512,
            'Explicit observation view required')
    require(bundle['window_spec'] == data['window_spec'], 'Embedding input window specification mismatch')
    require(type(bundle['rows']) is list, 'Expected embedding rows')
    index, dimension = {}, None
    for row in bundle['rows']:
        keys(row, ('id', 'values'))
        key = _id(row['id'])
        require(key not in index, 'Duplicate embedding ID')
        _vector(row['values'])
        dimension = len(row['values']) if dimension is None else dimension
        require(len(row['values']) == dimension, 'Inconsistent embedding dimensions')
        index[key] = row['values']
    require(set(index) == {_id(row['id']) for row in data['samples']}, 'Embedding records are misaligned')
    return [{**row, 'embedding': index[_id(row['id'])]} for row in data['samples']]
