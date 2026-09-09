"""DATA-03 causal windows over inert DATA-02 records; see docs/lab/windows.md."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass

from .channels import InputProfile, PRIMARY_PROFILE, project_inputs
from .records import context, integer, keys, require
from .splits import origin_from_episode, validate_split_manifest


@dataclass(frozen=True)
class WindowSpecV1:
    """Decision history and future post-states, with explicit missing-slot policy.

    ``stride`` spaces cutoffs; ``history_stride`` spaces history observations.
    Other logical units require a new sampling contract, never an implicit cast.
    """
    history_length: int
    horizon: int
    stride: int = 1
    history_stride: int = 1
    mode: str = 'passive'
    unit: str = 'decisions'
    short_history: str = 'mask'
    short_targets: str = 'mask'
    profile: InputProfile = PRIMARY_PROFILE
    data_version: int = 1
    availability_version: int = 1
    schema_version: int = 1

    def __post_init__(self):
        for value in (self.schema_version, self.data_version, self.availability_version):
            require(type(value) is int and value == 1, 'Unknown window/data/availability version')
        for value in (self.history_length, self.horizon, self.stride, self.history_stride):
            integer(value, 1)
        require(self.unit == 'decisions', 'Undefined horizon conversion: only decisions are supported')
        require(self.mode in ('passive', 'action_conditioned'), 'Unknown window mode')
        require(self.short_history in ('mask', 'exclude') and self.short_targets in ('mask', 'exclude'),
                'Unknown missing-slot policy')
        require(type(self.profile) is InputProfile, 'Expected InputProfile')
        self.profile.__post_init__()

    def to_json(self):
        return {**self.__dict__, 'profile': self.profile.to_json()}

    @classmethod
    def from_json(cls, data):
        keys(data, cls.__dataclass_fields__)
        return cls(**{**data, 'profile': InputProfile.from_json(data['profile'])})


def availability_rules(spec):
    """Closed, versioned rules for EVERY feature leaf selected by the profile.

    Observation fields inherit their source row's capture context. ActionV1 is
    available only at selection, before resolution. Transition results, future
    commands, events, end reasons, macro results and provenance have no input rule.
    """
    spec.__post_init__()
    rules = {path: 'observation-at-capture-context-v1' for path, _ in spec.profile.fields}
    if spec.mode == 'action_conditioned':
        rules['action'] = 'ActionV1-at-cutoff-selection-v1'
    return {'schema_version': spec.availability_version, 'fields': rules}


def validate_availability(field, available_at, cutoff, spec):
    """Check source-derived availability, not just a window slot/index.

    Callers must retain the actual source context; inventing a context does not
    authenticate a producer's assertion. The builder binds these to stored rows
    and TransitionV1 pre/post references before invoking this validator.
    """
    require(field in availability_rules(spec)['fields'], 'Field has no authorized availability rule')
    context(available_at)
    context(cutoff)
    require(all(available_at[key] == cutoff[key] for key in ('episode_id', 'branch_id')),
            'Availability crosses episode/branch')
    if field == 'action':
        require(available_at == cutoff, 'Only the action selected at the cutoff is an input')
    else:
        require(available_at['decision_seq'] <= cutoff['decision_seq'] and
                available_at['event_seq'] <= cutoff['event_seq'], 'Input is available after the cutoff')
    return True


def _membership(manifest, split_manifest, source_id):
    split = validate_split_manifest(split_manifest).to_json()
    matches = [entry for entry in split['sources'] if entry['source']['source_id'] == source_id]
    require(len(matches) == 1, 'Unknown split source')
    entry = matches[0]
    actual = origin_from_episode(manifest, source_id=source_id).to_json()
    require(all(entry['source'][field] == actual[field] for field in
                ('kind', 'episode_id', 'origin_family_id', 'content_digest')),
            'Split source differs from recorded episode; freeze a new split manifest')
    return {'origin': {'source_id': source_id, 'episode_id': manifest['episode_id'],
                       'source_family': manifest['source_family']},
            'split': split['assignments'][entry['family_id']],
            'family_id': entry['family_id'], 'split_version': split['split_version']}


class _Observations:
    """Forward reference join with one observation per selected channel retained."""

    def __init__(self, reader, spec, manifest):
        self.spec, self.manifest = spec, manifest
        names = list(dict.fromkeys(['primary'] + [p.split('.')[0] for p, _ in spec.profile.fields]))
        self.streams = {name: reader.iter_channel(name) for name in names}
        self.current = None
        self.last_id = 0
        self.players = None

    def close(self):
        for stream in self.streams.values():
            stream.close()

    def _next(self):
        primary = next(self.streams['primary'], None)
        if primary is None:
            require(all(next(stream, None) is None for name, stream in self.streams.items()
                        if name != 'primary'), 'Extra authorized channel observations')
            return None
        require(primary['observation_id'] == self.last_id + 1, 'Observation IDs have gaps/duplicates')
        self.last_id += 1
        channels = {'primary': primary['channel']}
        for name, stream in self.streams.items():
            if name == 'primary':
                continue
            row = next(stream, None)
            require(row is not None and row['observation_id'] == self.last_id and
                    row['context'] == primary['context'], 'Authorized channel availability/reference mismatch')
            channels[name] = row['channel']
        _check_context(primary['context'], self.manifest)
        for boundary in ('initial', 'final'):
            if self.last_id == self.manifest[boundary + '_observation']:
                require(primary['context'] == self.manifest[boundary + '_context'],
                        'Episode observation/context mismatch')
        players = [p['id'] for p in primary['channel']['data']['players']]
        if self.players is None:
            require(len(players) == len(set(players)), 'Duplicate entity IDs')
            self.players = players
        require(players == self.players, 'Entity binding changed')
        projected = project_inputs(channels, self.spec.profile)
        return {'observation_id': self.last_id, 'context': primary['context'],
                'features': projected['features'], 'metadata': projected['metadata'],
                'public': primary['channel']['data']}

    def get(self, ref):
        require(self.current is None or ref >= self.current['observation_id'],
                'Observation references move backwards')
        while self.current is None or self.current['observation_id'] < ref:
            self.current = self._next()
            require(self.current is not None, 'Missing transition observation')
        return self.current

    def finish(self):
        while self._next() is not None:
            pass
        require(self.last_id == self.manifest['files']['primary']['rows'] and
                self.last_id >= self.manifest['final_observation'], 'Missing episode boundary observation')


def _check_context(ctx, manifest):
    context(ctx)
    branches = manifest['branches']
    index = next((i for i, branch in enumerate(branches) if branch['branch_id'] == ctx['branch_id']), None)
    require(ctx['episode_id'] == manifest['episode_id'] and index is not None, 'Foreign episode/branch')
    start = branches[index]['parent'] or manifest['initial_context']
    stop = branches[index + 1]['parent'] if index + 1 < len(branches) else manifest['final_context']
    for field in ('decision_seq', 'event_seq'):
        require(start[field] <= ctx[field] <= stop[field], 'Context outside recorded branch')


def _frames(reader, spec, manifest):
    observations = _Observations(reader, spec, manifest)
    transitions = reader.iter_channel('transitions')
    previous = None
    try:
        for transition in transitions:
            before, after = transition['before'], transition['after']
            _check_context(before, manifest)
            _check_context(after, manifest)
            require(transition['source_family'] == manifest['source_family'] and
                    transition['scenario_id'] == manifest['scenario_id'], 'Transition provenance mismatch')
            if previous is not None:
                old = previous['transition']
                require(before['decision_seq'] >= old['after']['decision_seq'] and
                        before['event_seq'] >= old['after']['event_seq'], 'Transition time moves backwards')
                require(old['end'] is None and old['status'] == 'resolved', 'Decision after ended/pending step')
            pre = observations.get(transition['pre_observation'])
            post = observations.get(transition['post_observation'])
            require(pre['context'] == before and post['context'] == after,
                    'Observation/decision availability mismatch')
            require(pre['public']['decision']['pending'] and not pre['public']['match']['game_over'] and
                    pre['public']['decision']['actor_team']['value'] == transition['actor_id'],
                    'Pre-observation actor/terminal mismatch')
            if transition['status'] == 'resolved':
                require(post['public']['decision']['actor_team']['value'] == transition['next_actor_id'],
                        'Post-observation actor mismatch')
            require(post['public']['match']['game_over'] ==
                    (transition['end'] is not None and transition['end']['kind'] == 'terminal'),
                    'Terminal flag/end mismatch')
            for field in ('player_id', 'target_id'):
                require(transition['action'][field] is None or transition['action'][field] in observations.players,
                        'Unknown action entity')
            continuous = previous is not None and before == previous['transition']['after']
            if continuous:
                require(pre['public'] == previous['post']['public'], 'Broken pre/post observation continuity')
            frame = {'transition': transition, 'pre': pre, 'post': post, 'continuous': continuous}
            yield frame
            previous = frame
        observations.finish()
    finally:
        transitions.close()
        observations.close()


def _reference(observation):
    return {'observation_id': observation['observation_id'], 'available_at': observation['context']}


def _sample(history, future, spec, membership):
    current = future[0]
    cutoff = current['transition']['before']
    available = list(history) + [current]
    selected = []
    for offset in reversed(range(spec.history_length)):
        index = len(available) - 1 - offset * spec.history_stride
        selected.append(available[index]['pre'] if index >= 0 else None)
    target_frames = list(future)[:spec.horizon]
    targets = [frame['post'] if frame['transition']['status'] == 'resolved' else None
               for frame in target_frames]
    targets.extend([None] * (spec.horizon - len(targets)))
    if (spec.short_history == 'exclude' and any(row is None for row in selected) or
            spec.short_targets == 'exclude' and any(row is None for row in targets)):
        return None
    for observation in selected:
        if observation is not None:
            for field, _ in spec.profile.fields:
                validate_availability(field, observation['context'], cutoff, spec)
    inputs = {'observations': [row['features'] if row else None for row in selected],
              'presence': [row is not None for row in selected]}
    if spec.mode == 'action_conditioned':
        validate_availability('action', current['transition']['before'], cutoff, spec)
        inputs['action'] = current['transition']['action']
    return deepcopy({
        'inputs': inputs,
        'targets': {'observations': [row['features'] if row else None for row in targets],
                    'presence': [row is not None for row in targets]},
        'origin': membership['origin'], 'split': membership['split'],
        'metadata': {
            'spec': spec.to_json(), 'availability': availability_rules(spec),
            'family_id': membership['family_id'], 'split_version': membership['split_version'],
            'branch_id': cutoff['branch_id'], 'cutoff': cutoff,
            'history': [_reference(row) if row else None for row in selected],
            'target_observations': [_reference(row) if row else None for row in targets],
            'history_projection': [row['metadata'] if row else None for row in selected],
            'target_projection': [row['metadata'] if row else None for row in targets],
            'source_transitions': [{key: frame['transition'][key] for key in
                                    ('transition_id', 'before', 'after', 'event_start', 'event_stop',
                                     'status', 'end', 'macro_id', 'primitive_order')}
                                   for frame in target_frames],
        }})


def iter_windows(reader, spec, *, split_manifest, source_id=None):
    """Yield independent samples using bounded history/lookahead storage.

    Feed only ``sample['inputs']`` to a model. Metadata, targets and provenance
    must remain separate. Exhaust/close this generator to release all streams.
    """
    require(type(spec) is WindowSpecV1, 'Expected WindowSpecV1')
    spec.__post_init__()
    manifest = reader.manifest
    require(manifest['schema_version'] == spec.data_version, 'Incompatible data version')
    recorded = InputProfile.from_json(manifest['profile'])
    require(set(spec.profile.fields) <= set(recorded.fields), 'Profile exceeds recorded input authority')
    membership = _membership(manifest, split_manifest, source_id or manifest['episode_id'])
    history = deque(maxlen=(spec.history_length - 1) * spec.history_stride)
    future = deque()
    ordinal = 0
    frames = _frames(reader, spec, manifest)

    def emit():
        nonlocal ordinal
        sample = _sample(history, future, spec, membership) if ordinal % spec.stride == 0 else None
        history.append(future.popleft())
        ordinal += 1
        return sample

    try:
        for frame in frames:
            if not frame['continuous']:
                while future:
                    sample = emit()
                    if sample is not None:
                        yield sample
                history.clear()
                ordinal = 0
            future.append(frame)
            if len(future) == spec.horizon:
                sample = emit()
                if sample is not None:
                    yield sample
        while future:
            sample = emit()
            if sample is not None:
                yield sample
    finally:
        frames.close()


def iter_window_batches(reader, spec, *, split_manifest, source_id=None, batch_size=32):
    """Batch without materializing the episode or fitting any transformation."""
    integer(batch_size, 1)
    stream = iter_windows(reader, spec, split_manifest=split_manifest, source_id=source_id)
    try:
        batch = []
        for sample in stream:
            batch.append(sample)
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
    finally:
        stream.close()


def validate_window_source(reader, spec, *, split_manifest, source_id=None):
    """Exhaust selected channel/reference/availability checks; retain no samples.

    Returns the number of eligible windows. This is selective validation, not
    DATA-02's complete event/scope/macro audit (``read_episode``).
    """
    return sum(1 for _ in iter_windows(reader, spec, split_manifest=split_manifest, source_id=source_id))
