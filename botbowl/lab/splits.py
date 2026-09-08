"""Deterministic DATA-04 partitions over complete origin families.

The contract groups only declared provenance relationships and exact identifiers
or fingerprints.  It deliberately makes no claim to discover arbitrary semantic
similarity between otherwise unrelated simulations.
"""
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import re

from .records import EpisodeManifestV1


_SOURCE_KINDS = ('episode', 'snapshot', 'replay', 'augmentation', 'variant')
_RELATION_KINDS = ('parent_episode', 'parent_snapshot', 'replay_source',
                   'augmentation_source', 'variant_source')
_GROUP_KINDS = ('scenario', 'policy')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
_GROUP_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,383}')
_GROUPING_RULE = {
    'id': 'origin-components',
    'version': 1,
    'exact_duplicates': 'content-digest-equality',
    'semantic_origins': 'semantic-origin-fingerprint-equality',
    'near_variants': 'declared-variant-groups-only',
}
_ALGORITHM = {'id': 'stable-family-balance', 'version': 1}


class SplitError(ValueError):
    """Malformed, inconsistent, or contaminated split data."""


class InsufficientOriginsError(SplitError):
    """There are too few independent families for the requested protocol."""

    def __init__(self, families, required_splits):
        self.families = families
        self.required_splits = required_splits
        super().__init__(
            'Only {} independent origin families are available for {} non-empty '
            'splits; collect more origins or choose another protocol'.format(
                families, required_splits))


class SplitVersionRequiredError(SplitError):
    """The source set or split policy changed without a split version bump."""


def _require(condition, message):
    if not condition:
        raise SplitError(message)


def _identifier(value, field='identifier'):
    _require(type(value) is str and bool(_IDENTIFIER.fullmatch(value)),
             '{} must be a public identifier'.format(field))


def _group_identifier(value, field):
    _require(type(value) is str and bool(_GROUP_IDENTIFIER.fullmatch(value)),
             '{} must be a public group identifier'.format(field))


def _digest(value):
    payload = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(payload).hexdigest()


def _validate_plain(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    _require(depth <= 40 and count[0] <= 1000000, 'Split JSON depth/node limit')
    kind = type(value)
    if value is None or kind in (bool, str):
        return
    if kind is int:
        _require(-2**256 < value < 2**256, 'Split JSON integer limit')
        return
    if kind is float:
        _require(math.isfinite(value), 'Split data must contain finite numbers')
        return
    if kind is list:
        for item in value:
            _validate_plain(item, depth + 1, count)
        return
    if kind is dict:
        _require(all(type(key) is str for key in value),
                 'Split JSON object keys must be strings')
        for item in value.values():
            _validate_plain(item, depth + 1, count)
        return
    raise SplitError('Split data must be plain JSON')


def _plain_copy(value):
    _validate_plain(value)
    try:
        payload = json.dumps(value, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
        result = json.loads(payload.decode('ascii'))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise SplitError('Split data must be finite plain JSON') from error
    _require(len(payload) <= 16 * 1024 * 1024, 'Split manifest byte limit')
    return payload, result


def _keys(value, expected, message='Unknown or missing fields'):
    _require(type(value) is dict and set(value) == set(expected), message)


def _validate_digest(value, field):
    _require(type(value) is str and bool(_DIGEST.fullmatch(value)),
             '{} must be a SHA-256 fingerprint'.format(field))


def _validate_source(data):
    _keys(data, ('schema_version', 'source_id', 'source_version', 'kind',
                 'episode_id', 'origin_family_id', 'content_digest',
                 'semantic_origin_fingerprint', 'variant_group_id',
                 'relationships', 'groups'))
    _require(type(data['schema_version']) is int and data['schema_version'] == 1,
             'Unknown OriginSource version')
    _identifier(data['source_id'], 'source_id')
    _identifier(data['source_version'], 'source_version')
    _require(data['kind'] in _SOURCE_KINDS and type(data['kind']) is str,
             'Unknown source kind')
    for field in ('episode_id', 'origin_family_id', 'variant_group_id'):
        if data[field] is not None:
            _identifier(data[field], field)
    _validate_digest(data['content_digest'], 'content_digest')
    if data['semantic_origin_fingerprint'] is not None:
        _validate_digest(data['semantic_origin_fingerprint'],
                         'semantic_origin_fingerprint')
    relationships = data['relationships']
    _require(type(relationships) is list, 'relationships must be a list')
    seen = set()
    for relation in relationships:
        _keys(relation, ('kind', 'source_id'), 'Malformed origin relationship')
        _require(type(relation['kind']) is str and relation['kind'] in _RELATION_KINDS,
                 'Unknown origin relationship kind')
        _identifier(relation['source_id'], 'relationship source_id')
        key = (relation['kind'], relation['source_id'])
        _require(key not in seen, 'Duplicate origin relationship')
        seen.add(key)
    _keys(data['groups'], _GROUP_KINDS, 'groups must declare scenario and policy')
    for kind in _GROUP_KINDS:
        values = data['groups'][kind]
        _require(type(values) is list and all(type(value) is str for value in values),
                 'Origin groups must be identifier lists')
        _require(len(values) == len(set(values)),
                 'Origin groups must be unique lists')
        for value in values:
            _group_identifier(value, '{} group'.format(kind))


@dataclass(frozen=True, init=False)
class OriginSourceV1:
    """Frozen normalized provenance for one episode, snapshot, or derivative."""
    _encoded: bytes

    def __init__(self, data):
        _, copied = _plain_copy(data)
        _validate_source(copied)
        copied['relationships'].sort(key=lambda relation: (relation['kind'], relation['source_id']))
        for kind in _GROUP_KINDS:
            copied['groups'][kind].sort()
        encoded, _ = _plain_copy(copied)
        object.__setattr__(self, '_encoded', encoded)

    @classmethod
    def from_json(cls, data):
        return cls(data)

    def to_json(self):
        return json.loads(self._encoded.decode('ascii'))


def origin_from_episode(manifest, *, source_id=None, source_version='EpisodeManifestV1-1',
                        semantic_origin_fingerprint=None, variant_group_id=None,
                        relationships=()):
    """Adapt a validated DATA-02 manifest without treating metadata as features."""
    record = manifest if type(manifest) is EpisodeManifestV1 else EpisodeManifestV1(manifest)
    data = record.to_json()
    policies = []
    for side in ('home', 'away'):
        policy = data['provenance']['policies'][side]
        policies.append(policy_group_id(policy['id'], policy['version']))
    return OriginSourceV1({
        'schema_version': 1,
        'source_id': data['episode_id'] if source_id is None else source_id,
        'source_version': source_version,
        'kind': 'episode',
        'episode_id': data['episode_id'],
        'origin_family_id': data['source_family'],
        'content_digest': _digest(data),
        'semantic_origin_fingerprint': semantic_origin_fingerprint,
        'variant_group_id': variant_group_id,
        'relationships': list(relationships),
        'groups': {'scenario': [data['scenario_id']],
                   'policy': sorted(set(policies))},
    })


def policy_group_id(policy_id, version):
    """Unambiguous holdout key for a DATA-02 policy ID and nullable version."""
    _identifier(policy_id, 'policy_id')
    if version is not None:
        _identifier(version, 'policy version')
    return '{}:{}:{}'.format(len(policy_id), policy_id,
                             'none' if version is None else 'version:' + version)


class _UnionFind:
    def __init__(self, values):
        self.parent = {value: value for value in values}

    def find(self, value):
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            parent = self.parent[value]
            self.parent[value] = root
            value = parent
        return root

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            if right < left:
                left, right = right, left
            self.parent[right] = left


def _normalize_sources(sources):
    _require(type(sources) in (list, tuple) and bool(sources),
             'At least one origin source is required')
    by_id = {}
    for value in sources:
        source = value if type(value) is OriginSourceV1 else OriginSourceV1(value)
        data = source.to_json()
        previous = by_id.get(data['source_id'])
        if previous is not None:
            _require(previous == data, 'Duplicate source_id has different content')
        else:
            by_id[data['source_id']] = data
    return [by_id[key] for key in sorted(by_id)]


def _components(sources):
    by_id = {source['source_id']: source for source in sources}
    union = _UnionFind(by_id)
    edges = {source_id: [] for source_id in by_id}
    indexes = {}

    def join(namespace, value, source_id):
        if value is None:
            return
        key = (namespace, value)
        if key in indexes:
            union.union(source_id, indexes[key])
        else:
            indexes[key] = source_id

    episode_contents = {}
    for source in sources:
        source_id = source['source_id']
        for relation in source['relationships']:
            target = relation['source_id']
            _require(target in by_id, 'Unknown origin relationship target')
            edges[source_id].append(target)
            union.union(source_id, target)
        join('episode', source['episode_id'], source_id)
        join('origin-family', source['origin_family_id'], source_id)
        join('content', source['content_digest'], source_id)
        join('semantic-origin', source['semantic_origin_fingerprint'], source_id)
        join('variant-group', source['variant_group_id'], source_id)
        if source['kind'] == 'episode' and source['episode_id'] is not None:
            old = episode_contents.get(source['episode_id'])
            _require(old in (None, source['content_digest']),
                     'Duplicate episode_id has different content')
            episode_contents[source['episode_id']] = source['content_digest']

    # Every relationship points from a derivative to an ancestor. Any directed
    # cycle is invalid even though all its members would be in one component.
    indegree = dict.fromkeys(by_id, 0)
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    pending = [key for key, value in indegree.items() if value == 0]
    heapq.heapify(pending)
    visited = 0
    while pending:
        node = heapq.heappop(pending)
        visited += 1
        for target in edges[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(pending, target)
    _require(visited == len(by_id), 'Cyclic origin relationships')

    groups = {}
    for source_id in by_id:
        groups.setdefault(union.find(source_id), []).append(source_id)
    result = {}
    for members in groups.values():
        members.sort()
        family_names = {by_id[item]['origin_family_id'] for item in members
                        if by_id[item]['origin_family_id'] is not None}
        if family_names:
            anchors = ['origin-family:' + value for value in sorted(family_names)]
        else:
            variants = {by_id[item]['variant_group_id'] for item in members
                        if by_id[item]['variant_group_id'] is not None}
            semantics = {by_id[item]['semantic_origin_fingerprint'] for item in members
                         if by_id[item]['semantic_origin_fingerprint'] is not None}
            episodes = {by_id[item]['episode_id'] for item in members
                        if by_id[item]['episode_id'] is not None}
            roots = {item for item in members if not edges[item]}
            if variants:
                anchors = ['variant:' + value for value in sorted(variants)]
            elif semantics:
                anchors = ['semantic:' + value for value in sorted(semantics)]
            elif episodes:
                anchors = ['episode:' + value for value in sorted(episodes)]
            else:
                anchors = ['root:' + value for value in sorted(
                    {by_id[item]['content_digest'] for item in roots})]
        family_id = 'family-' + _digest(anchors).split(':', 1)[1]
        for source_id in members:
            result[source_id] = family_id
    return result


def _validate_proportions(proportions):
    _require(type(proportions) is dict and bool(proportions),
             'proportions must be a non-empty object')
    result = {}
    for split, value in proportions.items():
        _identifier(split, 'split name')
        _require(type(value) in (int, float) and type(value) is not bool and
                 math.isfinite(value) and value > 0, 'Split proportions must be positive finite numbers')
        result[split] = float(value)
    _require(math.isclose(sum(result.values()), 1.0, rel_tol=0.0, abs_tol=1e-12),
             'Split proportions must sum to one')
    return {key: result[key] for key in sorted(result)}


def _validate_held_out(value, splits):
    _keys(value, _GROUP_KINDS, 'held_out_groups must declare scenario and policy maps')
    result = {}
    for kind in _GROUP_KINDS:
        mapping = value[kind]
        _require(type(mapping) is dict, 'Held-out group declarations must be objects')
        result[kind] = {}
        for group, split in sorted(mapping.items()):
            _group_identifier(group, '{} held-out group'.format(kind))
            _require(type(split) is str and split in splits,
                     'Held-out group names an unknown split')
            result[kind][group] = split
    return result


def _target_counts(family_count, proportions):
    splits = sorted(proportions)
    if family_count < len(splits):
        raise InsufficientOriginsError(family_count, len(splits))
    result = dict.fromkeys(splits, 1)
    remaining = family_count - len(splits)
    exact = {split: proportions[split] * remaining for split in splits}
    for split in splits:
        result[split] += int(math.floor(exact[split]))
    left = family_count - sum(result.values())
    order = sorted(splits, key=lambda split: (-(exact[split] - math.floor(exact[split])), split))
    for split in order[:left]:
        result[split] += 1
    return result


def _score(seed, family_id, split=None):
    return _digest(['stable-family-balance', 1, seed, family_id, split])


def _assign(families, sources, proportions, seed, held_out):
    members = {}
    for source in sources:
        members.setdefault(families[source['source_id']], []).append(source)
    targets = _target_counts(len(members), proportions)
    forced = {}
    for family_id, family_sources in members.items():
        destinations = set()
        for source in family_sources:
            for kind in _GROUP_KINDS:
                destinations.update(held_out[kind][group] for group in source['groups'][kind]
                                    if group in held_out[kind])
        _require(len(destinations) <= 1,
                 'One origin family matches held-out groups assigned to different splits')
        if destinations:
            forced[family_id] = next(iter(destinations))

    assignments = {}
    counts = dict.fromkeys(proportions, 0)
    for family_id in sorted(forced, key=lambda family: (_score(seed, family), family)):
        split = forced[family_id]
        assignments[family_id] = split
        counts[split] += 1
    unforced = sorted(set(members) - set(forced),
                      key=lambda family: (_score(seed, family), family))
    empty = [name for name in proportions if counts[name] == 0]
    if len(unforced) < len(empty):
        raise InsufficientOriginsError(len(unforced), len(empty))
    for index, family_id in enumerate(unforced):
        # Whole-family quotas are approximate. Explicit held-outs take priority;
        # the remaining family goes to the greatest current target deficit.
        empty = [name for name in proportions if counts[name] == 0]
        candidates = empty if len(unforced) - index == len(empty) else proportions
        split = min(candidates, key=lambda name: (
            -(targets[name] - counts[name]), _score(seed, family_id, name), name))
        assignments[family_id] = split
        counts[split] += 1
    return {key: assignments[key] for key in sorted(assignments)}


def _counts(sources, families, assignments, proportions):
    result = {'sources': len(sources), 'families': len(assignments), 'splits': {}}
    for split in sorted(proportions):
        family_ids = {family for family, destination in assignments.items() if destination == split}
        result['splits'][split] = {
            'families': len(family_ids),
            'sources': sum(families[source['source_id']] in family_ids for source in sources),
        }
    return result


def _manifest_data(sources, proportions, seed, held_out, split_version):
    _identifier(split_version, 'split_version')
    _require(type(seed) is int and type(seed) is not bool and 0 <= seed < 2**256,
             'seed must be an unsigned 256-bit integer')
    normalized = _normalize_sources(sources)
    ratios = _validate_proportions(proportions)
    retained = _validate_held_out(held_out, ratios)
    families = _components(normalized)
    assignments = _assign(families, normalized, ratios, seed, retained)
    entries = [{'source': source, 'family_id': families[source['source_id']]}
               for source in normalized]
    return {
        'schema_version': 1,
        'split_version': split_version,
        'algorithm': dict(_ALGORITHM),
        'grouping_rule': dict(_GROUPING_RULE),
        'seed': seed,
        'proportions': ratios,
        'held_out_groups': retained,
        'sources': entries,
        'source_set_digest': _digest(normalized),
        'assignments': assignments,
        'counts': _counts(normalized, families, assignments, ratios),
    }


def _validate_manifest(data):
    _keys(data, ('schema_version', 'split_version', 'algorithm', 'grouping_rule',
                 'seed', 'proportions', 'held_out_groups', 'sources',
                 'source_set_digest', 'assignments', 'counts'))
    _require(type(data['schema_version']) is int and data['schema_version'] == 1,
             'Unknown SplitManifest version')
    _require(_digest(data['algorithm']) == _digest(_ALGORITHM),
             'Unknown split assignment algorithm')
    _require(_digest(data['grouping_rule']) == _digest(_GROUPING_RULE),
             'Unknown origin grouping rule')
    _require(type(data['sources']) is list and bool(data['sources']), 'Missing manifest sources')
    sources = []
    for entry in data['sources']:
        _keys(entry, ('source', 'family_id'), 'Malformed manifest source entry')
        source = OriginSourceV1(entry['source']).to_json()
        _identifier(entry['family_id'], 'family_id')
        sources.append(source)
    normalized = _normalize_sources(sources)
    _require(sources == normalized, 'Manifest sources must be unique and canonically ordered')
    expected = _manifest_data(normalized, data['proportions'], data['seed'],
                              data['held_out_groups'], data['split_version'])
    _require(_digest(data) == _digest(expected), 'Corrupt or non-canonical split manifest')


@dataclass(frozen=True, init=False)
class SplitManifestV1:
    """Frozen assignment and provenance inventory for one split version."""
    _encoded: bytes

    def __init__(self, data):
        encoded, copied = _plain_copy(data)
        _validate_manifest(copied)
        object.__setattr__(self, '_encoded', encoded)

    @classmethod
    def from_json(cls, data):
        return cls(data)

    def to_json(self):
        return json.loads(self._encoded.decode('ascii'))


def build_split_manifest(sources, *, proportions, seed, split_version,
                         held_out_groups=None, previous_manifest=None):
    """Assign complete origin components with deterministic, versioned balancing.

    Supplying ``previous_manifest`` makes a source-set or policy change require a
    different ``split_version``. It never patches one branch or family in place.
    """
    held_out = ({'scenario': {}, 'policy': {}} if held_out_groups is None
                else held_out_groups)
    data = _manifest_data(sources, proportions, seed, held_out, split_version)
    if previous_manifest is not None:
        previous = (previous_manifest if type(previous_manifest) is SplitManifestV1
                    else SplitManifestV1(previous_manifest)).to_json()
        policy_fields = ('algorithm', 'grouping_rule', 'seed', 'proportions',
                         'held_out_groups', 'source_set_digest')
        changed = any(previous[field] != data[field] for field in policy_fields)
        if changed and previous['split_version'] == split_version:
            raise SplitVersionRequiredError(
                'Source set or split policy changed; create a new split_version')
    return SplitManifestV1(data)


def validate_split_manifest(manifest, sources=None):
    """Validate a frozen manifest, optionally against the current source inventory."""
    value = manifest if type(manifest) is SplitManifestV1 else SplitManifestV1(manifest)
    data = value.to_json()
    if sources is None:
        return value
    current = _normalize_sources(sources)
    old_sources = [entry['source'] for entry in data['sources']]
    old_family = {entry['source']['source_id']: entry['family_id'] for entry in data['sources']}
    current_family = _components(current)
    by_component = {}
    for source_id, family_id in current_family.items():
        if source_id in old_family:
            by_component.setdefault(family_id, set()).add(data['assignments'][old_family[source_id]])
    _require(all(len(destinations) == 1 for destinations in by_component.values()),
             'A new origin relationship connects existing splits')
    _require(current == old_sources,
             'Current source inventory differs; create and freeze a new split version')
    return value


def validate_window_membership(manifest, samples):
    """Reject a DATA-03-style sample whose DATA-02 origin belongs elsewhere.

    Each sample contains ``split`` and an ``origin`` object with the recorder's
    ``episode_id`` and ``source_family`` plus this contract's stable ``source_id``.
    Other window fields are ignored.
    """
    data = validate_split_manifest(manifest).to_json()
    by_id = {entry['source']['source_id']: entry for entry in data['sources']}
    _require(type(samples) in (list, tuple), 'samples must be a list or tuple')
    for sample in samples:
        _require(type(sample) is dict and 'split' in sample and 'origin' in sample,
                 'Window sample is missing split provenance')
        origin = sample['origin']
        _keys(origin, ('source_id', 'episode_id', 'source_family'),
              'Malformed window origin reference')
        _identifier(origin['source_id'], 'window source_id')
        _require(origin['source_id'] in by_id, 'Window references an unknown source')
        entry = by_id[origin['source_id']]
        source = entry['source']
        _require(origin['episode_id'] == source['episode_id'] and
                 origin['source_family'] == source['origin_family_id'],
                 'Window origin disagrees with recorder provenance')
        expected = data['assignments'][entry['family_id']]
        _require(sample['split'] == expected, 'Window sample belongs to a different split')
    return True


__all__ = [
    'InsufficientOriginsError',
    'OriginSourceV1',
    'SplitError',
    'SplitManifestV1',
    'SplitVersionRequiredError',
    'build_split_manifest',
    'origin_from_episode',
    'policy_group_id',
    'validate_split_manifest',
    'validate_window_membership',
]
