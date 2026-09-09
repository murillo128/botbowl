"""Bounded simulation branches and immutable external shadow-future records.

Executable state stays in SIM-03 snapshots / ReplayV1. This module's JSON is
inert provenance, never a simulator snapshot or a policy loader.
"""
from dataclasses import dataclass
from typing import Optional

from .actions import ActionV1
from .chance import ChancePolicy, branch_from_snapshot
from .records import (
    RecordError, _Record, context, decode_json, encode_json, identifier, integer,
    keys, require,
)
from .replays import ReplayContinuation, ReplayRecorder
from .session import SessionSnapshot, SimulationSession
from .snapshot_io import snapshot_hash
from .snapshots import Snapshot, capture_snapshot, clone_from_snapshot


class BranchError(RecordError):
    """Invalid lineage, exhausted budget, or unavailable branch operation."""


def _check(condition, message):
    if not condition:
        raise BranchError(message)


def _copy(value):
    return decode_json(encode_json(value))


def _version(value):
    require(type(value) is int and value == 1, 'Unsupported branch schema version')


def _policy(value):
    keys(value, ('policy_id', 'version'))
    identifier(value['policy_id'])
    identifier(value['version'])


@dataclass(frozen=True, init=False)
class PredictionV1(_Record):
    """An imported prediction; nested data is stored as immutable JSON bytes."""

    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'kind', 'prediction_id', 'origin_family_id',
                    'branch_id', 'parent_snapshot_id', 'model', 'issued_at',
                    'available_history', 'horizon', 'output', 'metadata', 'revision_of'))
        _version(data['schema_version'])
        require(data['kind'] == 'predicted', 'Expected a prediction, not executable state')
        for name in ('prediction_id', 'origin_family_id', 'branch_id', 'parent_snapshot_id'):
            identifier(data[name])
        keys(data['model'], ('model_id', 'version'))
        identifier(data['model']['model_id'])
        identifier(data['model']['version'])
        context(data['issued_at'])
        require(data['issued_at']['branch_id'] == data['branch_id'], 'Prediction branch mismatch')
        keys(data['available_history'], ('through', 'references'))
        through = data['available_history']['through']
        context(through)
        require(through['episode_id'] == data['issued_at']['episode_id'] and
                through['branch_id'] == data['branch_id'] and
                through['decision_seq'] <= data['issued_at']['decision_seq'] and
                through['event_seq'] <= data['issued_at']['event_seq'],
                'Available history extends beyond emission')
        require(type(data['available_history']['references']) is list, 'Expected history references')
        for ref in data['available_history']['references']:
            identifier(ref)
        integer(data['horizon'])
        require(type(data['metadata']) is dict, 'Expected metadata object')
        if data['revision_of'] is not None:
            identifier(data['revision_of'])
            require(data['revision_of'] != data['prediction_id'], 'Prediction revision cycle')


@dataclass(frozen=True)
class BranchSpec:
    branch_id: str
    parent_branch_id: str
    parent_snapshot_id: str
    policy: dict
    horizon: int
    initial_action: Optional[ActionV1] = None
    chance: Optional[ChancePolicy] = None
    kind: str = 'simulated_alternative'
    intervention: Optional[dict] = None
    assumptions: tuple = ()


@dataclass(frozen=True)
class BranchSnapshot:
    """A tree-owned snapshot reference. Only registered references may be forked."""
    snapshot_id: str
    branch_id: str
    origin_family_id: str
    engine: Snapshot


class BranchSession:
    """A session whose actions are counted, retained and tied to one tree node.

    A continuation driver receives copied public observations and legal actions.
    Its declared policy identity must agree with BranchSpec. It never receives
    the engine, future tape, snapshot registry, or sibling sessions.
    """

    def __init__(self, tree, session, node, initial):
        self._tree = tree
        self._session = session
        self._node = node
        self._initial = initial
        self._actions = []
        self._hashes = []
        self._finished = False

    @property
    def branch_id(self):
        return self._node['branch_id']

    @property
    def origin_family_id(self):
        return self._tree.origin_family_id

    @property
    def state_revision(self):
        return self._session.state_revision

    @property
    def closed(self):
        return self._session.closed

    def observe(self, observer_team=None):
        return self._session.observe(observer_team)

    def legal_actions(self, observer_team=None):
        return self._session.legal_actions(observer_team)

    def _open(self):
        _check(not self._tree.closed and not self.closed, 'Branch is closed')
        _check(self._node['status'] == 'open', 'Branch is not executable')

    def step(self, action, expected_revision):
        self._open()
        _check(len(self._actions) < self._node['horizon'], 'Branch decision horizon exhausted')
        _check(self._tree._decisions < self._tree.max_decisions, 'Tree decision budget exhausted')
        semantic = SimulationSession._action(action)
        # Invalid actions/revisions never consume a decision or fail a healthy branch.
        from .session import InvalidAction
        try:
            result = self._session.step(semantic, expected_revision)
        except InvalidAction:
            raise
        except Exception as error:
            self._tree._decisions += 1
            self._node['attempted_decisions'] += 1
            self._fail(error)
            raise
        self._tree._decisions += 1
        self._node['attempted_decisions'] += 1
        self._actions.append(semantic.to_json())
        self._node['accepted_decisions'] = len(self._actions)
        try:
            self._hashes.append(snapshot_hash(self._session.snapshot().engine))
        except Exception as error:
            self._fail(error)
            raise
        self._node['chance_result'] = self._session._game.dice.chance.metadata()
        self._node['result'] = {
            'context': self._session._timeline.context.to_json(),
            'state_hash': self._hashes[-1],
            'terminated': result.terminated, 'truncated': result.truncated,
            'end_reason': result.end_reason,
        }
        if result.terminated or result.truncated or len(self._actions) == self._node['horizon']:
            self.finish()
        return result

    def _fail(self, error):
        self._node['status'] = 'failed'
        self._node['diagnostic'] = {'error_type': type(error).__name__, 'message': str(error)}
        self._session.close()

    def run(self, driver, *, policy_id, version):
        """Execute the remaining declared horizon with an explicitly supplied driver."""
        _check({'policy_id': policy_id, 'version': version} == self._node['policy'],
               'Continuation policy does not match declaration')
        _check(callable(driver), 'Expected a continuation driver')
        while self._node['status'] == 'open':
            self._open()
            _check(self._tree._decisions < self._tree.max_decisions, 'Tree decision budget exhausted')
            try:
                action = driver(self.observe(), self.legal_actions())
                self.step(action, self.state_revision)
            except Exception as error:
                self._fail(error)
                raise
        return self.observe()

    def finish(self):
        """Validate the declared chance prefix, including unused replay/matched draws."""
        if self._finished:
            return
        self._open()
        try:
            self._session._game.dice.chance.finish()
        except Exception as error:
            self._fail(error)
            raise
        self._finished = True
        self._node['status'] = 'finished'
        self._node['chance_result'] = self._session._game.dice.chance.metadata()

    def snapshot(self, snapshot_id):
        _check(self._node['status'] != 'failed', 'Failed branch has no settled snapshot')
        return self._tree._register(self._session.snapshot().engine, snapshot_id, self.branch_id)

    def export_replay(self, destination, relative_path, *, replay_id):
        """Materialize and verify the accepted prefix through the existing ReplayV1 API.

        Playback uses an owned clone and the retained actions, without invoking
        the continuation policy. A failed branch cannot publish a complete replay.
        """
        _check(self._node['status'] == 'finished', 'Finish the branch before exporting replay')
        game = clone_from_snapshot(self._initial)
        origin = self._tree._snapshots[self._node['parent_snapshot_id']]['replay_origin']
        recorder = None
        try:
            recorder = ReplayRecorder(
                ReplayContinuation(game, self.origin_family_id, origin),
                destination, relative_path, replay_id=replay_id)
            for action, expected in zip(self._actions, self._hashes):
                recorder.advance(ActionV1.from_json(action))
                _check(snapshot_hash(capture_snapshot(game)) == expected,
                       'Branch replay diverged from accepted state')
            manifest = recorder.close(
                truncation_reason=None if game.state.game_over else (
                    'branch_horizon' if len(self._actions) == self._node['horizon']
                    else 'branch_prefix'))
        except Exception:
            if recorder is not None:
                recorder.abort()
            raise
        finally:
            game.close()
        self._node['replay'] = {'replay_id': replay_id, 'path': relative_path,
                                'origin_family_id': self.origin_family_id}
        return manifest

    def close(self):
        if self.closed:
            return
        if self._node['status'] == 'open':
            self._node['status'] = 'closed'
        self._session.close()


class BranchTree:
    """One factual origin and its bounded alternatives and shadow predictions."""

    def __init__(self, snapshot, *, origin_family_id, snapshot_id='origin',
                 max_nodes=128, max_depth=16, max_decisions=1000, max_steps=100000):
        identifier(origin_family_id)
        for value in (max_nodes, max_depth, max_decisions, max_steps):
            integer(value, 1)
        self._origin_family_id = origin_family_id
        self.max_nodes, self.max_depth = max_nodes, max_depth
        self.max_decisions, self.max_steps = max_decisions, max_steps
        self._nodes, self._snapshots, self._sessions, self._predictions = {}, {}, {}, {}
        self._decisions = 0
        self.closed = False
        engine = self._engine(snapshot)
        game = clone_from_snapshot(engine)
        try:
            _check(game.timeline is not None, 'A branch tree requires a logical timeline')
            root = game.timeline.context.branch_id
            self._nodes[root] = {'branch_id': root, 'parent': None, 'kind': 'observed',
                                 'origin_family_id': origin_family_id, 'depth': 0}
        finally:
            game.close()
        self.root_snapshot = self._register(engine, snapshot_id, root)

    @property
    def origin_family_id(self):
        return self._origin_family_id

    @staticmethod
    def _engine(snapshot):
        if type(snapshot) is SessionSnapshot:
            _check(snapshot.truncation_reason is None, 'Failed session is not a branch point')
            snapshot = snapshot.engine
        _check(type(snapshot) is Snapshot and snapshot.scope == 'engine',
               'Expected an engine snapshot; predictions and observations are not restorable')
        return snapshot

    def _register(self, snapshot, snapshot_id, branch_id, replay_origin=None):
        _check(not self.closed, 'Tree is closed')
        identifier(snapshot_id)
        _check(snapshot_id not in self._snapshots, 'Duplicate snapshot ID')
        _check(branch_id in self._nodes, 'Unknown snapshot parent')
        engine = self._engine(snapshot)
        game = clone_from_snapshot(engine)
        try:
            _check(game.timeline is not None and game.timeline.context.branch_id == branch_id,
                   'Snapshot branch does not match parent')
            ctx = game.timeline.context.to_json()
            if self._snapshots:
                root_ctx = next(iter(self._snapshots.values()))['context']
                _check(ctx['episode_id'] == root_ctx['episode_id'], 'Snapshot episode mismatch')
            owned = capture_snapshot(game)
            ref = BranchSnapshot(snapshot_id, branch_id, self.origin_family_id, owned)
            self._snapshots[snapshot_id] = {
                'ref': ref, 'context': ctx, 'state_hash': snapshot_hash(owned),
                'replay_origin': _copy(replay_origin),
            }
            return ref
        finally:
            game.close()

    def observe(self, snapshot, *, snapshot_id):
        """Retain a later factual boundary without rewriting predictions."""
        root = next(iter(self._nodes))
        return self._register(snapshot, snapshot_id, root)

    @classmethod
    def from_replay(cls, reader, decision_seq, **limits):
        """Use a verified factual replay boundary and inherit its canonical family."""
        game = reader.seek_decision(decision_seq)
        try:
            manifest = reader.manifest
            tree = cls(capture_snapshot(game), origin_family_id=manifest['origin_family_id'], **limits)
            ctx = game.timeline.context
            tree._snapshots[tree.root_snapshot.snapshot_id]['replay_origin'] = {
                'replay_id': manifest['replay_id'], 'branch_id': ctx.branch_id,
                'decision_seq': ctx.decision_seq, 'event_seq': ctx.event_seq,
            }
            return tree
        finally:
            game.close()

    def fork(self, snapshot, branch_spec):
        """Clone a registered boundary and apply at most one legal initial action."""
        _check(not self.closed, 'Tree is closed')
        _check(type(branch_spec) is BranchSpec, 'Expected BranchSpec')
        spec = branch_spec
        for value in (spec.branch_id, spec.parent_branch_id, spec.parent_snapshot_id):
            identifier(value)
        _check(spec.branch_id not in self._nodes and spec.branch_id not in self._predictions,
               'Duplicate branch ID or lineage cycle')
        _check(spec.parent_branch_id in self._nodes, 'Unknown parent branch')
        saved = self._snapshots.get(spec.parent_snapshot_id)
        _check(saved is not None and type(snapshot) is BranchSnapshot and saved['ref'] is snapshot,
               'Unknown or substituted parent snapshot')
        _check(snapshot.branch_id == spec.parent_branch_id, 'Snapshot parent mismatch')
        _check(snapshot_hash(snapshot.engine) == saved['state_hash'], 'Parent snapshot was mutated')
        _check(len(self._nodes) + len(self._predictions) < self.max_nodes, 'Tree node budget exhausted')
        depth = self._nodes[spec.parent_branch_id]['depth'] + 1
        _check(depth <= self.max_depth, 'Tree depth budget exhausted')
        integer(spec.horizon)
        _check(spec.horizon <= self.max_decisions - self._decisions, 'Decision budget exceeded')
        _policy(spec.policy)
        _check(spec.kind in ('simulated_alternative', 'intervened'), 'Invalid simulated branch kind')
        _check((spec.intervention is not None) == (spec.kind == 'intervened'),
               'Intervened branches require separate intervention provenance')
        _check(spec.intervention is None or type(spec.intervention) is dict and bool(spec.intervention),
               'Expected nonempty intervention provenance')
        _check(type(spec.assumptions) in (tuple, list) and
               all(type(value) is str for value in spec.assumptions), 'Expected textual assumptions')
        _check(spec.initial_action is None or spec.horizon > 0, 'Initial action exceeds zero horizon')
        # All data is copied before allocating resources or publishing a tree node.
        node = _copy({
            'branch_id': spec.branch_id, 'parent': saved['context'],
            'origin_family_id': self.origin_family_id, 'kind': spec.kind, 'depth': depth,
            'parent_branch_id': spec.parent_branch_id, 'parent_snapshot_id': spec.parent_snapshot_id,
            'divergence': saved['context'], 'initial_action': None if spec.initial_action is None
            else SimulationSession._action(spec.initial_action).to_json(),
            'policy': spec.policy, 'horizon': spec.horizon, 'intervention': spec.intervention,
            'assumptions': list(spec.assumptions), 'status': 'open', 'accepted_decisions': 0,
            'attempted_decisions': 0, 'diagnostic': None, 'result': None, 'replay': None,
        })
        game = branch_from_snapshot(snapshot.engine, branch_id=spec.branch_id, policy=spec.chance)
        try:
            initial = capture_snapshot(game)
            node['chance'] = game.dice.chance.to_json()
            node['chance_result'] = game.dice.chance.metadata()
            seq = game.timeline.context.decision_seq
            session = SimulationSession.from_snapshot(
                SessionSnapshot(1, 'engine', seq, seq + spec.horizon, self.max_steps, None, initial))
        finally:
            game.close()
        branch = BranchSession(self, session, node, initial)
        # Preflight an initial action without advancing; invalid forks leave no node.
        try:
            if node['initial_action'] is not None:
                semantic = ActionV1.from_json(node['initial_action'])
                session._actions.decode(session._actions.request(semantic))
        except Exception:
            session.close()
            raise
        self._nodes[spec.branch_id], self._sessions[spec.branch_id] = node, branch
        if node['initial_action'] is not None:
            branch.step(node['initial_action'], branch.state_revision)
        elif spec.horizon == 0 or session.observe().terminated:
            branch.finish()
        return branch

    def import_prediction(self, data):
        _check(not self.closed, 'Tree is closed')
        prediction = PredictionV1(data)
        row = prediction.to_json()
        identity = row['prediction_id']
        _check(identity not in self._predictions and identity not in self._nodes, 'Duplicate prediction ID')
        _check(len(self._nodes) + len(self._predictions) < self.max_nodes, 'Tree node budget exhausted')
        _check(row['origin_family_id'] == self.origin_family_id, 'Prediction family mismatch')
        saved = self._snapshots.get(row['parent_snapshot_id'])
        _check(saved is not None and saved['context'] == row['issued_at'], 'Unknown prediction emission boundary')
        _check(row['branch_id'] in self._nodes, 'Unknown prediction branch')
        if row['revision_of'] is not None:
            _check(row['revision_of'] in self._predictions, 'Unknown prediction revision parent')
            original = self._predictions[row['revision_of']].to_json()
            for key in row:
                if key not in ('prediction_id', 'metadata', 'revision_of'):
                    _check(row[key] == original[key], 'Revisions may correct metadata only')
        self._predictions[identity] = prediction
        return prediction

    def revise_prediction(self, prediction_id, *, revision_id, metadata):
        _check(prediction_id in self._predictions, 'Unknown prediction')
        row = self._predictions[prediction_id].to_json()
        row.update(prediction_id=revision_id, revision_of=prediction_id, metadata=metadata)
        return self.import_prediction(row)

    def export(self):
        """Return versioned inert tree data and a compact result index.

        ``branches`` has DATA-02's exact branch_id/parent row shape. Prediction
        records live separately and are never added to that executable lineage.
        Chance declarations are privileged provenance, not policy inputs.
        """
        data = {
            'format': 'BranchTreeV1', 'schema_version': 1,
            'origin_family_id': self.origin_family_id,
            'limits': {'max_nodes': self.max_nodes, 'max_depth': self.max_depth,
                       'max_decisions': self.max_decisions, 'max_steps': self.max_steps},
            'attempted_decisions': self._decisions,
            'branches': [{'branch_id': node['branch_id'], 'parent': node['parent']}
                         for node in self._nodes.values()],
            'nodes': list(self._nodes.values()),
            'snapshots': [{'snapshot_id': key, 'branch_id': saved['ref'].branch_id,
                           'origin_family_id': self.origin_family_id,
                           'context': saved['context'], 'state_hash': saved['state_hash'],
                           'replay_origin': saved['replay_origin']}
                          for key, saved in self._snapshots.items()],
            'predictions': [row.to_json() for row in self._predictions.values()],
            'results': [{'branch_id': key, 'kind': node['kind'],
                         'origin_family_id': self.origin_family_id,
                         'status': node['status'], 'result': node['result'],
                         'chance': node['chance_result'], 'replay': node['replay']}
                        for key, node in self._nodes.items() if node['parent'] is not None],
        }
        return _copy(data)

    def close(self):
        if self.closed:
            return
        for session in self._sessions.values():
            session.close()
        self.closed = True


__all__ = ['BranchError', 'BranchSession', 'BranchSnapshot', 'BranchSpec', 'BranchTree', 'PredictionV1']
