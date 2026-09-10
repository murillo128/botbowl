"""DATA-09 bounded experimental collections. See docs/lab/collections.md.

Selection and experiment metadata are audit data, never encoder inputs. The
generator retains factual DATA-02 episodes in DATA-07 and alternatives in
SIM-05/ReplayV1; neither representation is relabelled on export.
"""
import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import random
import re
import stat
from types import MappingProxyType

from .branches import BranchSpec, BranchTree
from .chance import ChancePolicy
from .generate import JobConfig, build_plan, _open_session, _run
from .interventions import _spec as validate_patch
from .policies import create_policy, policy_inputs
from .randomness import SeedSpec
from .records import (EventV1, MAX_EPISODE_BYTES, MAX_RECORD_BYTES, RecordError, _Record, decode_json,
                      encode_json, integer, keys, require)
from .recording import EpisodeReader, _path, _read_file
from .replays import ReplayReader
from .scenarios import ScenarioSnapshot
from .snapshot_io import snapshot_hash, write_snapshot
from .snapshots import capture_snapshot, clone_from_snapshot
from .splits import OriginSourceV1, origin_from_episode
from .storage import DatasetReader, DatasetWriter, _atomic
from .windows import WindowSpecV1, iter_windows


BIAS_WARNING = (
    'Selection on future outcomes conditions the dataset even when the selection '
    'metadata is hidden from the encoder. Sampled rates are not natural game '
    'probabilities; these collections make no claim of human representativeness '
    'or identified causal effects.'
)

# Ordered report subsequences, within ONE engine decision and player. These are
# event predicates, not a new rules interpreter or a causal-effect estimator.
PREDICATES = MappingProxyType({
    'possession_gain-v1': (('SUCCESSFUL_PICKUP',), ('SUCCESSFUL_CATCH',), ('INTERCEPTION',)),
    'injury-v1': (('KNOCKED_OUT',), ('CASUALTY',)),
    'knockdown_injury-v1': (('KNOCKED_DOWN', 'KNOCKED_OUT'), ('KNOCKED_DOWN', 'CASUALTY')),
})


def _hash(data):
    return hashlib.sha256(encode_json(data)).hexdigest()


def _read(root, name):
    return decode_json(_read_file(_path(root, name), MAX_RECORD_BYTES))


def _write(root, name, data):
    _atomic(root, name, encode_json(data))


def select_events(events, predicate):
    """Return unique EventV1 anchors; reject conflicting duplicate identities.

    A repeated identical delivery is idempotent. Chains cannot cross episodes,
    branches, decisions or players. CASUALTY is the engine's single credit, not
    the separate injury-table or Apothecary report.
    """
    require(type(predicate) is str and predicate in PREDICATES, 'Unknown event predicate')
    seen, history, matches, total_bytes = {}, {}, [], 0
    for ordinal, raw in enumerate(events):
        require(ordinal < 100000, 'Event delivery limit')
        event = EventV1(raw)
        total_bytes += len(event._encoded)
        require(total_bytes <= MAX_EPISODE_BYTES, 'Event selection byte limit')
        row = event.to_json()
        require(row['payload_version'] == 1, 'Select sports events only')
        identity = tuple(row['event_id'])
        if identity in seen:
            require(seen[identity] == row, 'Conflicting duplicate event')
            continue
        seen[identity] = row
        require(len(seen) <= 100000, 'Event selection limit')
        if row['kind'] != 'report':
            continue
        ctx, data = row['context'], row['data']
        group = (ctx['episode_id'], ctx['branch_id'], row['decision_seq'], data['player_id'])
        previous = history.setdefault(group, [])
        require(not previous or previous[-1]['context']['event_seq'] < ctx['event_seq'],
                'Events out of logical order')
        outcome = data['outcome_type']
        for pattern in PREDICATES[predicate]:
            if outcome != pattern[-1]:
                continue
            if len(pattern) == 1 or (row['decision_seq'] is not None and
                    data['player_id'] is not None and
                    any(e['data']['outcome_type'] == pattern[0] for e in previous)):
                matches.append(row)
                break
        previous.append(row)
    return matches


def sample_finite_population(population, predicate, *, numerator=1, denominator=1, seed=0):
    """Known inclusion weights ONLY for a fixed, fully enumerated eligible frame.

    Each member is {unit_id, events}. Inspect every member, then independently
    retain each predicate-positive unit with rational probability n/d. There is
    no target-count stopping, failed generation or inference about excluded
    predicate-negative units. Caller supplies the entire finite frame as data.
    """
    integer(numerator, 1)
    integer(denominator, 1)
    integer(seed)
    require(numerator <= denominator, 'Invalid inclusion probability')
    require(type(population) is list and len(population) <= 1000, 'Finite population limit')
    require(type(predicate) is str and predicate in PREDICATES, 'Unknown event predicate')
    frame_hash = _hash(population)  # Bound the complete frame before sampling.
    frame, ids = [], set()
    for item in population:
        keys(item, ('unit_id', 'events'))
        from .records import identifier
        identifier(item['unit_id'])
        require(item['unit_id'] not in ids, 'Duplicate population unit')
        ids.add(item['unit_id'])
        frame.append((item['unit_id'], select_events(item['events'], predicate)))
    rng, selected, eligible = random.Random(seed), [], 0
    for unit_id, matches in frame:
        if not matches:
            continue
        eligible += 1
        if rng.randrange(denominator) < numerator:
            selected.append({'unit_id': unit_id, 'event_ids': [e['event_id'] for e in matches],
                             'inclusion_probability': numerator / denominator,
                             'sampling_weight': denominator / numerator})
    return {'schema_version': 1, 'design': 'fixed-eligible-frame-bernoulli-v1',
            'predicate': predicate, 'frame_sha256': frame_hash, 'seed': seed,
            'probability': {'numerator': numerator, 'denominator': denominator},
            'weight_scope': 'predicate-positive units of this finite frame only',
            'attempted': len(frame), 'eligible': eligible, 'accepted': len(selected),
            'failed': 0, 'selected': selected, 'warning': BIAS_WARNING}


@dataclass(frozen=True, init=False)
class CollectionSpecV1(_Record):
    """Immutable, bounded data-only plan; use ``create`` for default fields."""

    @classmethod
    def create(cls, *, generation=None, mode='natural', target=1, max_episodes=16,
               max_decisions=512, predicate=None, before=2, after=2,
               chance=None, patch=None):
        require(generation is None or type(generation) is dict, 'Expected generation configuration')
        config = JobConfig(output='unused', episodes=1, **({} if generation is None else generation))
        generation = asdict(config)
        del generation['output'], generation['episodes']
        return cls(dict(schema_version=1, generation=generation, mode=mode, target=target,
                        max_episodes=max_episodes, max_decisions=max_decisions,
                        predicate=predicate, before=before, after=after, chance=chance, patch=patch))

    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'generation', 'mode', 'target', 'max_episodes',
                    'max_decisions', 'predicate', 'before', 'after', 'chance', 'patch'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'Unknown collection schema')
        require(data['mode'] in ('natural', 'selected', 'forced', 'intervened'), 'Unknown mode')
        config = JobConfig(output='unused', episodes=1, **data['generation'])
        require(len(config.episode_prefix) <= 90, 'Collection prefix too long')
        for name in ('target', 'max_episodes', 'max_decisions'):
            integer(data[name], 1)
        require(data['target'] <= data['max_episodes'] <= 1000 and
                data['max_decisions'] <= 1000000, 'Collection search limit')
        for name in ('before', 'after'):
            integer(data[name])
            require(data[name] <= 256, 'Window limit')
        require((data['predicate'] is not None) == (data['mode'] == 'selected'),
                'Only selected mode has an outcome predicate')
        if data['predicate'] is not None:
            require(type(data['predicate']) is str and data['predicate'] in PREDICATES,
                    'Unknown event predicate')
        require((data['patch'] is not None) == (data['mode'] == 'intervened'),
                'Intervened mode requires an exact patch')
        if data['patch'] is not None:
            patch = validate_patch(data['patch'])
            require(patch['reachability'] in ('synthetic', 'unknown'),
                    'Collection V1 has no external reachability witness')
            allowed = {'player': ('position', 'extra_ma', 'extra_st', 'extra_ag', 'extra_av'),
                       'team': ('rerolls', 'bribes', 'babes', 'apothecaries'),
                       'ball': ('placement',), 'configuration': ('registered_config',)}
            for operation in patch['operations']:
                keys(operation, ('entity', 'entity_id', 'field', 'old_value', 'new_value'))
                require(type(operation['entity']) is str and operation['entity'] in allowed and
                        operation['field'] in allowed[operation['entity']], 'Unsupported patch operation')
                entity, identity = operation['entity'], operation['entity_id']
                require(type(identity) is str and (
                    entity == 'team' and identity in ('home', 'away') or
                    entity == 'player' and re.fullmatch(r'(home|away):[0-9]+', identity) or
                    entity == 'ball' and identity == 'ball' or
                    entity == 'configuration' and identity == 'rules'),
                    'Collection patches require stable semantic entity IDs')
        if data['mode'] in ('natural', 'selected'):
            require(data['chance'] is None, 'Unmodified modes cannot inject chance')
        elif data['chance'] is not None:
            chance = ChancePolicy.from_json(data['chance'])
            require(chance.cursor == 0 and not chance.tape()['rolls'] and
                    not data['chance']['used'] and not data['chance']['counts'],
                    'Expected unused chance declaration')
            require(chance.mode in ('independent', 'forced'), 'Unsupported collection chance mode')
            require(chance.mode != 'independent' or chance.seed is not None,
                    'Independent chance requires a reproducible seed')
        if data['mode'] == 'forced':
            require(data['chance'] is not None and data['chance']['mode'] == 'forced',
                    'Forced mode requires an explicit forced tape')


def _plan(spec, index):
    generation = spec['generation'].copy()
    generation['episode_prefix'] += '-%04d' % index
    return build_plan(JobConfig(output='unused', episodes=1, **generation))


def _reservation(spec):
    config = JobConfig(output='unused', episodes=1, **spec['generation'])
    horizon = config.max_decisions if config.horizon is None else config.horizon
    return min(config.max_decisions, horizon) * (2 if spec['mode'] in ('forced', 'intervened') else 1)


def _resolve_patch(point, patch):
    """Bind stable DATA-02 entities to this snapshot's engine IDs, then use SIM-08.

    Both the original stable plan and the exact applied engine patch are kept.
    No state is edited by this adapter; old-value and legality guards remain the
    editor's responsibility on its private clone.
    """
    resolved = validate_patch(patch)
    game = clone_from_snapshot(point.engine)
    try:
        entities = game.timeline._entities
        teams = {semantic: engine for engine, semantic in entities._team_ids.items()}
        players = {semantic: engine for engine, semantic in entities._player_ids.items()}
        for op in resolved['operations']:
            mapping = teams if op['entity'] == 'team' else players if op['entity'] == 'player' else None
            if mapping is not None:
                require(op['entity_id'] in mapping, 'Unknown patch entity')
                op['entity_id'] = mapping[op['entity_id']]
            if op['entity'] == 'ball':
                for field in ('old_value', 'new_value'):
                    value = op[field]
                    keys(value, ('position', 'carrier_id'))
                    if value['carrier_id'] is not None:
                        require(value['carrier_id'] in players, 'Unknown ball carrier')
                        value['carrier_id'] = players[value['carrier_id']]
        return resolved
    finally:
        game.close()


def _alternative(spec, entry, config, root):
    """Run a SIM-05 alternative from the same untouched recipe snapshot."""
    with ExitStack() as stack:
        factual = _open_session(entry, config)
        stack.callback(factual.close)
        snapshot = factual.snapshot()
        if type(snapshot) is ScenarioSnapshot:
            snapshot = snapshot.session
        before = snapshot_hash(snapshot.engine)
        tree = BranchTree(snapshot, kind='simulated_alternative',
                          origin_family_id=entry['origin_family_id'], max_nodes=3,
                          max_depth=2, max_decisions=max(1, _reservation(spec)),
                          max_steps=config.max_steps)
        stack.callback(tree.close)
        point, intervention = tree.root_snapshot, None
        write_snapshot(root / 'origin.json', point.engine, provenance={
            'origin_family_id': point.origin_family_id, 'snapshot_id': point.snapshot_id,
            'branch_id': point.branch_id, 'kind': 'simulated_alternative'})
        if spec['patch'] is not None:
            edited = tree.intervene(point, _resolve_patch(point, spec['patch']))
            point, intervention = edited.snapshot, edited.provenance
            edited.write(root / 'intervention.json')
        chance = (ChancePolicy.from_json(spec['chance']) if spec['chance'] is not None else
                  ChancePolicy(seed=SeedSpec(
                      **entry['seed_plan']['sources']['engine'])))
        horizon = config.max_decisions if config.horizon is None else config.horizon
        branch = tree.fork(point, BranchSpec(
            branch_id='continuation', parent_branch_id=point.branch_id,
            parent_snapshot_id=point.snapshot_id,
            policy={'policy_id': 'collection-policies', 'version': '1'}, horizon=horizon,
            chance=chance, kind='intervened' if intervention else 'simulated_alternative',
            intervention=intervention))
        policies = {side: create_policy(entry['policy_specs'][side]) for side in ('home', 'away')}
        for policy in policies.values():
            stack.callback(policy.close)

        def drive(observation, legal):
            features, control = policy_inputs(observation.primary)
            return policies[observation.next_actor].act(features, legal, control)

        branch.run(drive, policy_id='collection-policies', version='1')
        replay = branch.export_replay(root, 'alternative', replay_id=entry['episode_id'] + '-alt')
        final_hash = snapshot_hash(branch.snapshot('continuation-final').engine)
        after = factual.snapshot()
        require(snapshot_hash((after.session if type(after) is ScenarioSnapshot else after).engine) == before,
                'Factual snapshot changed')
        lineage = tree.export()
        _write(root, 'lineage.json', lineage)
        return {'lineage_sha256': _hash(lineage), 'replay_sha256': _hash(replay),
                'origin_family_id': tree.origin_family_id,
                'mode': spec['mode'], 'chance': lineage['nodes'][-1]['chance_result'],
                'reachability': 'unknown' if intervention is None else intervention['reachability'],
                'policy_specs': entry['policy_specs'], 'horizon': horizon,
                'final_state_sha256': final_hash,
                'attempted_decisions': lineage['attempted_decisions']}


def _generate_attempt(spec, plan, root):
    root.mkdir()
    raw = root / 'recording'
    raw.mkdir()
    config = JobConfig(output='unused', **plan['job'])
    entry = plan['episodes'][0]
    summary = _run(entry, config, raw)
    source = EpisodeReader(raw, entry['episode_id'])
    source.read_episode()  # Whole-episode causal validation before publication.
    with DatasetWriter(root / 'factual', plan) as writer:
        writer.append_episode(source)
    matches = select_events(source.iter_channel('events'), spec['predicate']) if spec['predicate'] else []
    result = {'status': 'accepted' if spec['mode'] != 'selected' or matches else 'rejected',
            'episode_id': entry['episode_id'], 'origin_family_id': entry['origin_family_id'],
            'mode': spec['mode'], 'factual_mode': 'natural',
            'manifest_sha256': _hash(source.manifest), 'generation': summary,
            'selection': {'predicate': spec['predicate'], 'unit': 'episode',
                          'event_ids': [e['event_id'] for e in matches],
                          'anchors': [e['context'] for e in matches],
                          'inclusion_probability': None, 'sampling_weight': None,
                          'missing_weight_reason': 'inclusion_probability_unknown_for_generation_and_stopping'},
            'alternative': None, 'attempted_decisions': summary['end']['decisions']}
    if spec['mode'] in ('forced', 'intervened'):
        try:
            result['alternative'] = _alternative(spec, entry, config, root)
            result['attempted_decisions'] += result['alternative']['attempted_decisions']
        except (OSError, MemoryError):
            raise
        except Exception as error:
            # Preserve an already-confirmed factual episode even if the
            # alternative fails. It still is not an accepted experiment.
            result.update(status='failed', error_type=type(error).__name__,
                          attempted_decisions=_reservation(spec))
    return result


@contextmanager
def _lock(root):
    import fcntl
    fd = os.open(str(_path(root, 'collection.lock')), os.O_CREAT | os.O_RDWR |
                 getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'Invalid collection lock')
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def collect(spec, destination, *, attempt_limit=None):
    """Generate/resume a single-writer collection; failed attempts never retry.

    ``attempt_limit`` pauses after this many new attempts, without changing the
    search design. Process death leaves an admitted attempt charged at its full
    reserved decision cap on reopening. Atomic DATA-07 episodes remain intact.
    """
    require(type(spec) is CollectionSpecV1, 'Expected CollectionSpecV1')
    data = spec.to_json()
    if attempt_limit is not None:
        integer(attempt_limit, 1)
    root = Path(destination).resolve()
    package = Path(__file__).resolve().parents[1]
    require(root != package and package not in root.parents, 'Collection cannot be in installed package')
    root.mkdir(parents=True, exist_ok=True)
    with _lock(root):
        if _path(root, 'spec.json').exists():
            require(_read(root, 'spec.json') == data, 'Resume requires identical collection spec')
        else:
            _write(root, 'spec.json', data)
        if not _path(root, 'collection.json').exists():
            _write(root, 'collection.json', {'schema_version': 1, 'spec_sha256': _hash(data), 'attempts': []})
        reader = CollectionReader(root)
        state = deepcopy(reader._state)
        # An admission without a committed outcome may have run the engine.
        # Never erase it or infer success from unpublished artifacts.
        for index, entry in enumerate(state['attempts']):
            if entry['status'] == 'started':
                result = {'status': 'failed', 'error_type': 'InterruptedAttempt',
                          'attempted_decisions': entry['reserved_decisions']}
                _commit(root, state, index, result, data)
        reader.verify()
        new = 0
        while True:
            reader = CollectionReader(root)
            report = reader.report()
            if report['status'] != 'in_progress' or (attempt_limit is not None and new >= attempt_limit):
                return report
            index = len(state['attempts'])
            state['attempts'].append({'status': 'started', 'reserved_decisions': _reservation(data),
                                      'result_sha256': None})
            _write(root, 'collection.json', state)  # Admission precedes all engine execution.
            try:
                result = _generate_attempt(data, _plan(data, index), _path(root, 'attempt-%04d' % index))
            except (OSError, MemoryError):
                raise  # Infrastructure failure: preserve admission, stop this process.
            except Exception as error:
                result = {'status': 'failed', 'error_type': type(error).__name__,
                          'attempted_decisions': state['attempts'][index]['reserved_decisions']}
            _commit(root, state, index, result, data)
            new += 1


def _commit(root, state, index, result, spec):
    plan = _plan(spec, index)
    entry = plan['episodes'][0]
    result.update(schema_version=1, attempt_index=index, generation_plan_sha256=_hash(plan),
                  episode_id=entry['episode_id'], origin_family_id=entry['origin_family_id'],
                  mode=spec['mode'])
    _write(root, 'attempt-%04d.json' % index, result)
    state['attempts'][index].update(status=result['status'], result_sha256=_hash(result))
    _write(root, 'collection.json', state)


class CollectionReader:
    """Read a committed audit index; project inputs only through DATA-03."""

    def __init__(self, destination):
        try:
            self._load(destination)
        except (KeyError, TypeError, IndexError, AttributeError) as error:
            raise RecordError('Malformed collection metadata') from error

    def _load(self, destination):
        self.root = Path(destination).resolve()
        self._spec = CollectionSpecV1(_read(self.root, 'spec.json')).to_json()
        self._state = _read(self.root, 'collection.json')
        keys(self._state, ('schema_version', 'spec_sha256', 'attempts'))
        require(type(self._state['schema_version']) is int and self._state['schema_version'] == 1 and
                self._state['spec_sha256'] == _hash(self._spec), 'Collection identity mismatch')
        require(type(self._state['attempts']) is list and
                len(self._state['attempts']) <= self._spec['max_episodes'], 'Attempt limit')
        self._results = []
        for index, entry in enumerate(self._state['attempts']):
            keys(entry, ('status', 'reserved_decisions', 'result_sha256'))
            require(entry['reserved_decisions'] == _reservation(self._spec), 'Decision reservation mismatch')
            if entry['status'] == 'started':
                require(index == len(self._state['attempts']) - 1 and entry['result_sha256'] is None,
                        'Invalid pending attempt')
                self._results.append(None)
                continue
            require(entry['status'] in ('failed', 'accepted', 'rejected'), 'Unknown attempt status')
            result = _read(self.root, 'attempt-%04d.json' % index)
            require(_hash(result) == entry['result_sha256'] and result['status'] == entry['status'],
                    'Attempt checksum/status mismatch')
            common = {'schema_version', 'status', 'attempt_index', 'generation_plan_sha256',
                      'episode_id', 'origin_family_id', 'mode', 'attempted_decisions'}
            expected = common | ({'error_type'} if result['status'] == 'failed' else set())
            if 'manifest_sha256' in result:
                expected |= {'factual_mode', 'manifest_sha256', 'generation', 'selection', 'alternative'}
            keys(result, expected)
            require(type(result['schema_version']) is int and result['schema_version'] == 1,
                    'Unknown attempt schema')
            require(result['status'] == 'failed' or 'manifest_sha256' in result, 'Missing factual episode')
            plan = _plan(self._spec, index)
            require(result['generation_plan_sha256'] == _hash(plan) and result['attempt_index'] == index and
                    result['mode'] == self._spec['mode'] and
                    result['episode_id'] == plan['episodes'][0]['episode_id'] and
                    result['origin_family_id'] == plan['episodes'][0]['origin_family_id'],
                    'Attempt generation identity mismatch')
            integer(result['attempted_decisions'])
            require(result['attempted_decisions'] <= entry['reserved_decisions'], 'Decision cap exceeded')
            if 'manifest_sha256' in result:
                require(result['mode'] == self._spec['mode'] and result['factual_mode'] == 'natural',
                        'Cannot relabel collection provenance')
                source = self.factual(index)
                planned = _plan(self._spec, index)['episodes'][0]
                require(_hash(source.manifest) == result['manifest_sha256'] and
                        source.manifest['episode_id'] == result['episode_id'] == planned['episode_id'] and
                        source.manifest['source_family'] == result['origin_family_id'] == planned['origin_family_id'],
                        'Factual identity mismatch')
                selection = result['selection']
                keys(selection, ('predicate', 'unit', 'event_ids', 'anchors', 'inclusion_probability',
                                 'sampling_weight', 'missing_weight_reason'))
                require(selection['predicate'] == self._spec['predicate'] and selection['unit'] == 'episode' and
                        selection['inclusion_probability'] is None and selection['sampling_weight'] is None and
                        selection['missing_weight_reason'] == 'inclusion_probability_unknown_for_generation_and_stopping',
                        'Invalid generated inclusion claim')
                require(type(selection['event_ids']) is list and type(selection['anchors']) is list and
                        len(selection['event_ids']) == len(selection['anchors']), 'Invalid selection anchors')
                if result['status'] != 'failed':
                    require((result['status'] == 'accepted') == (self._spec['mode'] != 'selected' or
                            bool(selection['event_ids'])), 'Selection status mismatch')
                if result['alternative'] is not None:
                    require(self._spec['mode'] in ('forced', 'intervened'), 'Cannot relabel an alternative')
                    alternative = result['alternative']
                    folder = _path(self.root, 'attempt-%04d' % index)
                    lineage = _read(folder, 'lineage.json')
                    replay = ReplayReader(folder, 'alternative').manifest
                    require(_hash(lineage) == alternative['lineage_sha256'] and
                            _hash(replay) == alternative['replay_sha256'] and
                            lineage['origin_family_id'] == replay['origin_family_id'] == result['origin_family_id'] and
                            alternative['mode'] == self._spec['mode'], 'Alternative provenance mismatch')
                    node = lineage['nodes'][-1]
                    require(alternative['chance'] == node['chance_result'] and
                            (self._spec['mode'] != 'forced' or node['chance']['mode'] == 'forced' and
                             alternative['chance']['natural'] is False) and
                            (self._spec['mode'] != 'intervened' or node['kind'] == 'intervened' and
                             node['intervention'] is not None), 'Experiment classification mismatch')
                else:
                    require(self._spec['mode'] in ('natural', 'selected') or result['status'] == 'failed',
                            'Missing experimental continuation')
            self._results.append(result)
        require(self.report()['attempted_decisions'] <= self._spec['max_decisions'], 'Search decision limit')

    @property
    def spec(self):
        return deepcopy(self._spec)

    def factual(self, index):
        integer(index)
        require(index < len(self._state['attempts']), 'Unknown attempt')
        return DatasetReader(_path(self.root, 'attempt-%04d/factual' % index)).episode(0)

    def generation_plan(self, index):
        """Materialize the exact plan, including for a failed admitted attempt."""
        integer(index)
        require(index < len(self._state['attempts']), 'Unknown attempt')
        return _plan(self._spec, index)

    def report(self):
        statuses = [entry['status'] for entry in self._state['attempts']]
        decisions = sum(entry['reserved_decisions'] if result is None else result['attempted_decisions']
                        for entry, result in zip(self._state['attempts'], self._results))
        accepted = statuses.count('accepted')
        complete = accepted >= self._spec['target']
        exhausted = len(statuses) >= self._spec['max_episodes'] or decisions + _reservation(self._spec) > self._spec['max_decisions']
        return {'schema_version': 1, 'mode': self._spec['mode'], 'selection_unit': 'episode',
                'status': ('in_progress' if 'started' in statuses else 'complete' if complete else
                           'insufficient_matches' if exhausted else 'in_progress'),
                'attempted': len(statuses), 'accepted': accepted, 'failed': statuses.count('failed'),
                'rejected': statuses.count('rejected'), 'pending': statuses.count('started'),
                'attempted_decisions': decisions,
                'decision_accounting': 'actual_on_success_upper_bound_on_failure',
                'limits': {k: self._spec[k] for k in ('target', 'max_episodes', 'max_decisions')},
                'warning': BIAS_WARNING}

    def records(self):
        return deepcopy(self._results)

    def verify(self):
        """Verify retained DATA-07 shards and executable alternative replays."""
        for index, result in enumerate(self._results):
            if result is None or 'manifest_sha256' not in result:
                continue
            folder = _path(self.root, 'attempt-%04d' % index)
            DatasetReader(folder / 'factual').verify()
            matches = (select_events(self.factual(index).iter_channel('events'), self._spec['predicate'])
                       if self._spec['predicate'] else [])
            require(result['selection']['event_ids'] == [e['event_id'] for e in matches] and
                    result['selection']['anchors'] == [e['context'] for e in matches],
                    'Selection differs from recorded events')
            if result['alternative'] is not None:
                game = ReplayReader(folder, 'alternative').replay_all()
                try:
                    require(snapshot_hash(capture_snapshot(game)) == result['alternative']['final_state_sha256'],
                            'Alternative final state mismatch')
                finally:
                    game.close()

    def origin_sources(self):
        """DATA-04 sources for factual AND alternative artifacts, same family."""
        sources = []
        for index, result in enumerate(self._results):
            if result is None or 'manifest_sha256' not in result:
                continue
            factual = origin_from_episode(self.factual(index).manifest).to_json()
            sources.append(factual)
            if result['alternative'] is not None:
                alternate = deepcopy(factual)
                alternate.update(source_id=factual['source_id'] + '-alt', kind='replay',
                                 source_version='ReplayV1-1',
                                 content_digest='sha256:' + result['alternative']['replay_sha256'],
                                 relationships=[{'kind': 'parent_episode', 'source_id': factual['source_id']}])
                sources.append(OriginSourceV1(alternate).to_json())
        return sources

    def iter_selected_windows(self, *, split_manifest):
        """Pre-event cutoff, before history slots and after future decisions.

        Includes the causing decision in targets. Multiple matching events from
        that decision share one window. Uncaused setup events remain audit
        anchors but have no invented decision window. Alternative trajectories
        stay in ReplayV1, outside this factual DATA-03 projection.
        """
        require(self._spec['mode'] in ('natural', 'selected'), 'Use ReplayV1 for experimental continuations')
        window = WindowSpecV1(self._spec['before'] + 1, self._spec['after'] + 1)
        for index, result in enumerate(self._results):
            if result is None or result['status'] != 'accepted':
                continue
            anchors = {(ctx['branch_id'], ctx['decision_seq'] - 1)
                       for ctx in result['selection']['anchors'] if ctx['decision_seq'] > 0}
            for sample in iter_windows(self.factual(index), window, split_manifest=split_manifest):
                cutoff = sample['metadata']['cutoff']
                if self._spec['mode'] == 'natural' or (cutoff['branch_id'], cutoff['decision_seq']) in anchors:
                    sample['metadata']['collection'] = {
                        'mode': self._spec['mode'], 'selection': deepcopy(result['selection']),
                        'warning': BIAS_WARNING}
                    yield sample


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path, help='CollectionSpecV1 JSON')
    parser.add_argument('output', type=Path)
    parser.add_argument('--attempt-limit', type=int)
    args = parser.parse_args(argv)
    try:
        spec = CollectionSpecV1(decode_json(_read_file(args.spec, MAX_RECORD_BYTES)))
        report = collect(spec, args.output, attempt_limit=args.attempt_limit)
    except (ValueError, OSError) as error:
        parser.exit(2, 'Collection rejected: %s\n' % type(error).__name__)
    print(encode_json(report).decode('utf-8'))
    return 0 if report['status'] != 'insufficient_matches' else 3


if __name__ == '__main__':
    raise SystemExit(main())
