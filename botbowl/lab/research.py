"""Bounded local research views over ReplayV1, BranchTree and public observations.

Uploaded files are inert JSON. Only private temporary directories hold executable
checkpoints; HTTP responses never contain snapshots, seeds or chance tapes.
"""
import base64
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
import threading
import uuid

from .actions import ActionControl, ActionV1
from .branches import BranchSpec, BranchTree, PredictionV1
from .chance import ChancePolicy
from .commands import Access, Forbidden, InvalidRequest, CapacityExceeded
from .observations import observe
from .rendering import render_grid
from .replays import ReplayReader
from .views import grid_view


@dataclass(frozen=True)
class ResearchLimits:
    max_bytes: int = 32 * 1024 * 1024
    max_files: int = 256
    max_replays: int = 8
    max_decisions: int = 128
    max_records: int = 128

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in self.__dict__.values()):
            raise ValueError('Research limits must be positive integers')


def pack_replay(directory, limits=ResearchLimits()):
    """Package one local ReplayV1 directory for upload; never follows symlinks."""
    directory = Path(directory)
    files = {}
    total = 0
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are not replay files')
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            if not _FILE.fullmatch(name):
                raise ValueError('Unexpected replay file')
            total += path.stat().st_size
            if total > limits.max_bytes or len(files) >= limits.max_files:
                raise CapacityExceeded()
            files[name] = path.read_text(encoding='utf-8')
    return {'format': 'ResearchReplayUploadV1', 'files': files}


_FILE = re.compile(r'(manifest\.json|blocks/block-[0-9]{8}\.json|checkpoints/decision-[0-9]{20}\.snapshot\.json)')


class ResearchStore:
    """Operator-owned grants apply to this single shared research workspace.

    Access reuses API-06 roles. Forking additionally requires BOTH explicit
    evaluator snapshot and restore capabilities; a role alone grants no writes.
    Imported factuality and prediction emission are declarations, not attestations.
    """

    def __init__(self, grants, limits=ResearchLimits()):
        if not grants or any(type(k) is not str or type(v) is not Access for k, v in grants.items()):
            raise ValueError('Explicit Access grants required')
        self.grants = dict(grants)
        self.limits = limits
        self._temporary = tempfile.TemporaryDirectory(prefix='botbowl-research-')
        self.root = Path(self._temporary.name)
        self.entries = {}
        self.lock = threading.RLock()

    def access(self, principal, *, fork=False):
        grant = self.grants.get(principal)
        if grant is None or (fork and not (grant.role == 'evaluator' and
                                          {'snapshot', 'restore'} <= grant.capabilities)):
            raise Forbidden()
        return grant

    def _entry(self, identity):
        if identity not in self.entries:
            raise KeyError(identity)
        return self.entries[identity]

    def _register(self, identity, provenance):
        reader = ReplayReader(self.root, identity)
        manifest = reader.manifest
        if manifest['final_context']['decision_seq'] - manifest['initial_context']['decision_seq'] > self.limits.max_decisions:
            raise CapacityExceeded()
        game = reader.replay_all()
        try:
            events = [event.to_json() for event in game.timeline.events
                      if event.context.event_seq > manifest['initial_context']['event_seq']]
            if provenance['kind'] == 'observed':
                tree = BranchTree.from_replay(reader, manifest['initial_context']['decision_seq'], kind='observed')
                tree.close()
        finally:
            game.close()
        self.entries[identity] = {'reader': reader, 'manifest': manifest, 'events': events,
                                  'provenance': provenance, 'predictions': [], 'annotations': []}
        return self.summary(identity)

    def upload(self, principal, data):
        self.access(principal)
        if len(self.entries) >= self.limits.max_replays:
            raise CapacityExceeded()
        if type(data) is not dict or set(data) != {'format', 'files'} or data['format'] != 'ResearchReplayUploadV1':
            raise InvalidRequest()
        files = data['files']
        if type(files) is not dict or not 1 <= len(files) <= self.limits.max_files:
            raise InvalidRequest()
        total = 0
        for name, content in files.items():
            if type(name) is not str or not _FILE.fullmatch(name) or type(content) is not str:
                raise InvalidRequest()
            total += len(content.encode('utf-8'))
            if total > self.limits.max_bytes:
                raise CapacityExceeded()
        identity = uuid.uuid4().hex
        directory = self.root / identity
        directory.mkdir()
        try:
            for name, content in files.items():
                path = directory / name
                path.parent.mkdir(exist_ok=True)
                path.write_text(content, encoding='utf-8')
            return self._register(identity, {'kind': 'observed', 'source': 'imported factual declaration',
                                             'policy': None, 'chance': 'recorded natural stream',
                                             'intervention': None, 'initial_action': None, 'parent': None})
        except Exception:
            shutil.rmtree(directory)
            raise

    def summary(self, identity):
        entry = self._entry(identity)
        m = entry['manifest']
        return deepcopy({'id': identity, 'replay_id': m['replay_id'], 'origin_family_id': m['origin_family_id'],
                         'initial': m['initial_context'], 'final': m['final_context'], 'end': m['end'],
                         'provenance': entry['provenance'], 'turns': entry['reader'].turn_index,
                         'predictions': entry['predictions'], 'annotations': entry['annotations']})

    def frame(self, identity, decision, entity=None):
        entry = self._entry(identity)
        m = entry['manifest']
        if type(decision) is not int or not m['initial_context']['decision_seq'] <= decision <= m['final_context']['decision_seq']:
            raise InvalidRequest()
        game = entry['reader'].seek_decision(decision)
        try:
            observation = observe(game, game.timeline._entities, 'home').to_json()
            rgb = render_grid(grid_view(observation))
            selected = next((p for p in observation['players'] if p['id'] == entity), None)
            return {'context': game.timeline.context.to_json(), 'observation': observation,
                    'selected': {'state': 'absent_entity' if selected is None else 'present', 'value': selected},
                    'image': {'width': rgb.shape[1], 'height': rgb.shape[0], 'cell_size': 12,
                              'rgb': base64.b64encode(rgb.tobytes()).decode('ascii')}}
        finally:
            game.close()

    def events(self, identity, kind='', entity=''):
        if len(kind) > 128 or len(entity) > 128:
            raise InvalidRequest()
        def contains(value):
            if isinstance(value, dict):
                return any(contains(v) for v in value.values())
            if isinstance(value, list):
                return any(contains(v) for v in value)
            return value == entity
        return deepcopy([e for e in self._entry(identity)['events']
                         if (not kind or kind.lower() in e['kind'].lower()) and
                         (not entity or contains(e['data']))])

    def event(self, identity, event_seq):
        found = self._entry(identity)['reader'].seek_event(event_seq)
        try:
            return {'event': found.event, 'previous_decision': found.previous_decision,
                    'next_decision': found.next_decision,
                    'frame': self.frame(identity, found.previous_decision)}
        finally:
            found.game.close()

    def actions(self, principal, identity, decision):
        self.access(principal, fork=True)
        game = self._entry(identity)['reader'].seek_decision(decision)
        try:
            return [a.to_json() for a in ActionControl(game, game.timeline._entities).legal_actions().actions]
        finally:
            game.close()

    def fork(self, principal, identity, data, *, seed=None):
        self.access(principal, fork=True)
        if type(data) is not dict or set(data) != {'decision', 'action', 'horizon'}:
            raise InvalidRequest()
        if type(data['horizon']) is not int or not 1 <= data['horizon'] <= self.limits.max_decisions:
            raise InvalidRequest()
        if len(self.entries) >= self.limits.max_replays:
            raise CapacityExceeded()
        source = self._entry(identity)
        tree = BranchTree.from_replay(source['reader'], data['decision'], kind=source['provenance']['kind'],
                                     max_decisions=self.limits.max_decisions)
        branch_id = uuid.uuid4().hex
        try:
            branch = tree.fork(tree.root_snapshot, BranchSpec(
                branch_id=branch_id, parent_branch_id=tree.root_snapshot.branch_id,
                parent_snapshot_id=tree.root_snapshot.snapshot_id, initial_action=ActionV1.from_json(data['action']),
                horizon=data['horizon'], policy={'policy_id': 'first-legal', 'version': 'v1'},
                chance=ChancePolicy(seed=seed)))
            branch.run(lambda observation, legal: legal.actions[0], policy_id='first-legal', version='v1')
            branch.export_replay(self.root, branch_id, replay_id=branch_id)
            node = tree.export()['nodes'][-1]
            return self._register(branch_id, {'kind': 'simulated_alternative', 'source': 'BranchTree simulation',
                'parent': identity, 'divergence': data['decision'], 'policy': node['policy'],
                'initial_action': node['initial_action'], 'intervention': node['intervention'],
                'horizon': data['horizon'], 'chance': {'mode': node['chance']['mode'],
                                                     'natural': node['chance_result']['natural']}})
        finally:
            tree.close()
            if branch_id not in self.entries:
                for name in (branch_id, branch_id + '.partial'):
                    directory = self.root / name
                    if directory.exists():
                        shutil.rmtree(directory)

    def prediction(self, identity, data):
        # Target start is separate from emission, so post-outcome imports cannot
        # silently present themselves as forecasts for an earlier comparison.
        if type(data) is not dict or set(data) != {'prediction', 'target_decision', 'retrospective'}:
            raise InvalidRequest()
        record = PredictionV1(data['prediction']).to_json()
        target, retrospective = data['target_decision'], data['retrospective']
        if type(target) is not int or type(retrospective) is not bool:
            raise InvalidRequest()
        entry = self._entry(identity)
        if len(entry['predictions']) >= self.limits.max_records:
            raise CapacityExceeded()
        if record['origin_family_id'] != entry['manifest']['origin_family_id']:
            raise InvalidRequest()
        emitted = self.frame(identity, record['issued_at']['decision_seq'])['context']
        self.frame(identity, target)
        if emitted != record['issued_at'] or (record['issued_at']['decision_seq'] > target and not retrospective):
            raise InvalidRequest()
        if any(p['record']['prediction_id'] == record['prediction_id'] for p in entry['predictions']):
            raise InvalidRequest()
        if record['revision_of'] is not None:
            original = next((p['record'] for p in entry['predictions']
                             if p['record']['prediction_id'] == record['revision_of']), None)
            if original is None or any(record[k] != original[k] for k in record
                                       if k not in ('prediction_id', 'revision_of', 'metadata')):
                raise InvalidRequest()
        row = {'kind': 'retrospective_analysis' if retrospective else 'model_prediction',
               'target_decision': target, 'record': record}
        entry['predictions'].append(deepcopy(row))
        return row

    def annotate(self, identity, data):
        if type(data) is not dict or set(data) != {'decision', 'text'} or type(data['text']) is not str or len(data['text']) > 4096:
            raise InvalidRequest()
        self.frame(identity, data['decision'])
        rows = self._entry(identity)['annotations']
        if len(rows) >= self.limits.max_records:
            raise CapacityExceeded()
        row = dict(data, kind='human_annotation')
        rows.append(deepcopy(row))
        return row

    def close(self):
        self.entries.clear()
        self._temporary.cleanup()
