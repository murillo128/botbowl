"""SIM-05 lineage, isolation, bounded execution and shadow-future semantics."""
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from botbowl.lab.actions import ActionV1, EmptyOptionsV1
from botbowl.lab.branches import BranchError, BranchSpec, BranchTree, PredictionV1
from botbowl.lab.chance import ChancePolicy
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.records import RecordError, context, keys
from botbowl.lab.replays import ReplayReader
from botbowl.lab.session import (
    ExecutionFailure, IncompatibleSnapshot, InvalidAction, SessionConfig,
    SimulationSession, StaleRevision,
)
from botbowl.lab.snapshot_io import read_snapshot, snapshot_hash, write_snapshot
from botbowl.lab.snapshots import capture_snapshot
from tests.lab.test_chance_modes import gfi_boundary
from tests.lab.test_replays import record


POLICY = {'policy_id': 'first-legal', 'version': 'v1'}


def spec(tree, branch_id='left', snapshot=None, **kwargs):
    snapshot = tree.root_snapshot if snapshot is None else snapshot
    values = dict(branch_id=branch_id, parent_branch_id=snapshot.branch_id,
                  parent_snapshot_id=snapshot.snapshot_id, policy=deepcopy(POLICY), horizon=1,
                  chance=ChancePolicy(seed=SeedSpec(44, 'branches', component_id=branch_id)))
    values.update(kwargs)
    return BranchSpec(**values)


@pytest.fixture
def factual():
    simulation = SimulationSession(SessionConfig(size=1), SeedSpec(17, 'branches'))
    # Session reset uses wall time; snapshot restoration installs LogicalTime
    # so repeated persistent hashes measure state rather than elapsed seconds.
    logical = SimulationSession.from_snapshot(simulation.snapshot())
    simulation.close()
    simulation = logical
    legal = simulation.legal_actions()
    simulation.step(legal.actions[0], legal.state_revision)
    yield simulation
    simulation.close()


@pytest.fixture
def tree(factual):
    value = BranchTree(factual.snapshot(), origin_family_id='family-46')
    yield value
    value.close()


def first(observation, legal):
    return legal.actions[0]


def test_two_alternatives_preserve_factual_hash_family_and_resources(factual, tree):
    original_hash = snapshot_hash(factual.snapshot().engine)
    choices = factual.legal_actions().actions
    assert {action.type for action in choices} == {'HEADS', 'TAILS'}
    left = tree.fork(tree.root_snapshot, spec(tree, horizon=2))
    right = tree.fork(tree.root_snapshot, spec(tree, 'right', horizon=2))
    right_hash = snapshot_hash(right._session.snapshot().engine)
    left.step(choices[0], left.state_revision)
    assert snapshot_hash(right._session.snapshot().engine) == right_hash
    right.step(choices[1], right.state_revision)
    left.run(first, **POLICY)
    right.run(first, **POLICY)
    assert snapshot_hash(factual.snapshot().engine) == original_hash
    assert left.origin_family_id == right.origin_family_id == tree.origin_family_id
    games = [factual._game, left._session._game, right._session._game]
    for attribute in ('rng', 'dice', 'timeline', 'state'):
        assert len({id(getattr(game, attribute)) for game in games}) == 3
    assert len({id(game.state.reports) for game in games}) == 3
    right_cache = deepcopy(right._session._game.arena.json)
    left._session._game.state.reports.clear()
    left._session._game.arena.json = {'canary': True}
    assert right._session._game.state.reports and factual._game.state.reports
    assert right._session._game.arena.json == right_cache
    data = tree.export()
    assert {node['kind'] for node in data['nodes']} == {'observed', 'simulated_alternative'}
    assert len(data['results']) == 2
    assert all(row['origin_family_id'] == 'family-46' for row in data['results'])
    # DATA-02's exact branch-row shape, topological order and logical parent context.
    seen = set()
    for row in data['branches']:
        keys(row, ('branch_id', 'parent'))
        if row['parent'] is not None:
            context(row['parent'])
            assert row['parent']['branch_id'] in seen
        seen.add(row['branch_id'])
    data['nodes'][1]['policy'].clear()
    assert tree.export()['nodes'][1]['policy'] == POLICY


def test_actual_dice_fixture_independent_and_forced_provenance(tmp_path):
    factual = gfi_boundary()
    saved = capture_snapshot(factual.game)
    before = snapshot_hash(saved)
    tree = BranchTree(saved, origin_family_id='gfi-family')
    try:
        left = tree.fork(tree.root_snapshot, spec(tree))
        right = tree.fork(tree.root_snapshot, spec(tree, 'right'))
        moves = [a for a in left.legal_actions().actions if a.type == 'MOVE']
        assert len(moves) > 1
        right_before = snapshot_hash(right._session.snapshot().engine)
        left.step(moves[0], left.state_revision)
        assert left._session._game.dice.chance.tape()['rolls']
        assert snapshot_hash(right._session.snapshot().engine) == right_before
        right.step(moves[1], right.state_revision)
        assert snapshot_hash(capture_snapshot(factual.game)) == before
        # Prefix playback and new-process restore include the chance cursor/RNG.
        manifest = left.export_replay(tmp_path, 'left', replay_id='left-replay')
        reader = ReplayReader(tmp_path, 'left')
        expected = snapshot_hash(left._session.snapshot().engine)
        assert snapshot_hash(capture_snapshot(reader.replay_all())) == expected
        output = tmp_path / 'worker.json'
        completed = subprocess.run(
            [sys.executable, '-m', 'tests.lab.replay_process', str(tmp_path), 'left',
             str(manifest['final_context']['decision_seq']), str(output)],
            capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        evidence = json.loads(output.read_text())
        assert evidence['hash'] == expected
        assert evidence['context']['branch_id'] == 'left'
        assert manifest['origin_family_id'] == 'gfi-family'
        # Same continuation seed and a recorded dice prefix can be forced, but
        # it remains fabricated and does not become an observed trajectory.
        tape = left._session._game.dice.chance.tape()
        forced = tree.fork(tree.root_snapshot, spec(
            tree, 'forced', chance=ChancePolicy('forced', tape=tape),
            initial_action=moves[0], kind='intervened',
            intervention={'operation': 'prescribe-die', 'applied_by': 'chance-policy'},
            assumptions=('Fabricated GFI outcome for a test.',)))
        assert not forced._node['chance_result']['natural']
        assert forced._node['kind'] == 'intervened'
        assert forced._node['intervention']['operation'] == 'prescribe-die'
    finally:
        tree.close()
        factual.close()


def test_replay_origin_inherits_family_without_remapping(tmp_path):
    _, source, _, _, _ = record(tmp_path, decisions=3)
    tree = BranchTree.from_replay(ReplayReader(tmp_path, 'replay'), 1)
    try:
        branch = tree.fork(tree.root_snapshot, spec(tree))
        branch.run(first, **POLICY)
        manifest = branch.export_replay(tmp_path, 'alternative', replay_id='alternative')
        assert manifest['origin_family_id'] == source['origin_family_id']
        assert manifest['origin'] == {
            'replay_id': 'replay', 'branch_id': source['initial_context']['branch_id'],
            'decision_seq': 1, 'event_seq': tree.export()['snapshots'][0]['context']['event_seq'],
        }
        assert ReplayReader(tmp_path, 'alternative').replay_all().timeline.context.branch_id == 'left'
    finally:
        tree.close()


@pytest.mark.parametrize('change', [
    {'parent_branch_id': 'missing'}, {'parent_snapshot_id': 'missing'},
    {'branch_id': 'main'}, {'horizon': 1001}, {'horizon': -1},
    {'horizon': True}, {'policy': {}}, {'kind': 'predicted'},
    {'kind': 'intervened'}, {'intervention': {'patch': 'unapplied'}},
])
def test_invalid_forks_do_not_mutate_tree(tree, change):
    if change == {'branch_id': 'main'}:
        change = {'branch_id': tree.root_snapshot.branch_id}
    before = tree.export()
    with pytest.raises((BranchError, RecordError)):
        tree.fork(tree.root_snapshot, spec(tree, **change))
    assert tree.export() == before


def test_duplicate_cycle_depth_node_and_total_decision_budgets(factual):
    tree = BranchTree(factual.snapshot(), origin_family_id='family', max_nodes=3,
                      max_depth=1, max_decisions=2)
    try:
        left = tree.fork(tree.root_snapshot, spec(tree))
        child = left.snapshot('left-start')
        with pytest.raises(BranchError, match='depth'):
            tree.fork(child, spec(tree, 'grandchild', snapshot=child))
        with pytest.raises(BranchError, match='Duplicate|cycle'):
            tree.fork(child, spec(tree, tree.root_snapshot.branch_id, snapshot=child))
        with pytest.raises(BranchError, match='Duplicate'):
            tree.fork(tree.root_snapshot, spec(tree))
        right = tree.fork(tree.root_snapshot, spec(tree, 'right', horizon=2))
        with pytest.raises(BranchError, match='node'):
            tree.fork(tree.root_snapshot, spec(tree, 'extra'))
        left.run(first, **POLICY)
        right.step(right.legal_actions().actions[0], right.state_revision)
        before = snapshot_hash(right._session.snapshot().engine)
        with pytest.raises(BranchError, match='decision budget'):
            right.step(right.legal_actions().actions[0], right.state_revision)
        assert snapshot_hash(right._session.snapshot().engine) == before
        right.finish()
    finally:
        tree.close()


def test_illegal_action_stale_revision_and_policy_rejection(tree):
    illegal = ActionV1(1, 'START_GAME', 'away', None, None, None, EmptyOptionsV1())
    before = tree.export()
    with pytest.raises(ValueError):
        tree.fork(tree.root_snapshot, spec(tree, initial_action=illegal))
    assert tree.export() == before
    left = tree.fork(tree.root_snapshot, spec(tree))
    before = tree.export()
    with pytest.raises(InvalidAction):
        left.step(illegal, left.state_revision)
    with pytest.raises(StaleRevision):
        left.step(left.legal_actions().actions[0], 0)
    with pytest.raises(BranchError, match='policy'):
        left.run(first, policy_id='undeclared', version='v1')
    assert tree.export() == before
    left.run(first, **POLICY)
    with pytest.raises(BranchError):
        left.step(illegal, left.state_revision)


def test_failure_is_retained_and_does_not_modify_sibling_or_factual(factual, tree, monkeypatch, tmp_path):
    left = tree.fork(tree.root_snapshot, spec(tree))
    right = tree.fork(tree.root_snapshot, spec(tree, 'right'))
    original = snapshot_hash(factual.snapshot().engine)
    sibling = snapshot_hash(right._session.snapshot().engine)

    def fail(*args, **kwargs):
        left._session._game.rng.randint(100)
        raise RuntimeError('injected branch failure')

    monkeypatch.setattr(left._session._game, 'advance', fail)
    with pytest.raises(ExecutionFailure):
        left.step(left.legal_actions().actions[0], left.state_revision)
    assert left.closed and left._node['status'] == 'failed'
    assert left._node['attempted_decisions'] == 1
    assert left._node['accepted_decisions'] == 0
    with pytest.raises(BranchError):
        left.export_replay(tmp_path, 'failed', replay_id='failed')
    assert snapshot_hash(factual.snapshot().engine) == original
    assert snapshot_hash(right._session.snapshot().engine) == sibling
    right.run(first, **POLICY)
    tree.close()
    tree.close()
    left.close()
    right.close()
    assert left.closed and right.closed


def prediction(tree):
    snapshot = tree.export()['snapshots'][0]
    ctx = snapshot['context']
    return {
        'schema_version': 1, 'kind': 'predicted', 'prediction_id': 'prediction-1',
        'origin_family_id': tree.origin_family_id, 'branch_id': ctx['branch_id'],
        'parent_snapshot_id': snapshot['snapshot_id'],
        'model': {'model_id': 'external-model', 'version': 'v7'}, 'issued_at': ctx,
        'available_history': {'through': deepcopy(ctx), 'references': ['history-1']},
        'horizon': 2, 'output': {'win_probability': .6}, 'metadata': {'label': 'original'},
        'revision_of': None,
    }


def test_prediction_observation_and_metadata_revision_are_immutable(factual, tree):
    raw = prediction(tree)
    imported = tree.import_prediction(raw)
    original = imported.to_json()
    raw['output']['win_probability'] = 0
    imported.to_json()['output'].clear()
    with pytest.raises(FrozenInstanceError):
        imported._encoded = b'{}'
    legal = factual.legal_actions()
    factual.step(legal.actions[0], legal.state_revision)
    tree.observe(factual.snapshot(), snapshot_id='future-observed')
    revised = tree.revise_prediction('prediction-1', revision_id='prediction-2',
                                    metadata={'label': 'corrected'})
    assert imported.to_json() == original
    assert revised.to_json()['issued_at'] == original['issued_at']
    assert revised.to_json()['output'] == original['output']
    assert revised.to_json()['revision_of'] == 'prediction-1'
    assert tree.export()['predictions'][0] == original
    with pytest.raises(BranchError):
        BranchTree(imported, origin_family_id='family')
    with pytest.raises(IncompatibleSnapshot):
        SimulationSession.from_snapshot(imported)
    with pytest.raises(BranchError):
        tree.fork(imported, spec(tree))
    with pytest.raises(BranchError, match='Duplicate'):
        tree.import_prediction(original)
    revised_data = revised.to_json()
    revised_data.update(prediction_id='prediction-3', output={'win_probability': .7})
    with pytest.raises(BranchError, match='metadata only'):
        tree.import_prediction(revised_data)


@pytest.mark.parametrize('change', [
    {'schema_version': 2}, {'schema_version': True}, {'origin_family_id': 'other'},
    {'parent_snapshot_id': 'absent'}, {'branch_id': 'absent'}, {'revision_of': 'absent'},
    {'output': float('nan')}, {'kind': 'observed'}, {'output': object()},
])
def test_prediction_import_rejects_invalid_or_executable_data(tree, change):
    raw = prediction(tree)
    raw.update(change)
    with pytest.raises(RecordError):
        tree.import_prediction(raw)
    assert tree.export()['predictions'] == []


def test_prediction_history_cannot_claim_future(tree):
    raw = prediction(tree)
    raw['available_history']['through']['decision_seq'] += 1
    with pytest.raises(RecordError, match='beyond emission'):
        tree.import_prediction(raw)


def test_file_snapshot_can_start_independent_session_and_tree(factual, tmp_path):
    source = factual.snapshot()
    path = tmp_path / 'source.snapshot.json'
    write_snapshot(path, source.engine)
    restored = read_snapshot(path)
    session = SimulationSession.from_snapshot(replace(source, engine=restored))
    try:
        assert session.observe().primary == factual.observe().primary
        with pytest.raises(IncompatibleSnapshot):
            SimulationSession.from_snapshot(replace(source, accepted_decisions=999))
        tree = BranchTree(restored, origin_family_id='family')
        branch = tree.fork(tree.root_snapshot, spec(tree, horizon=0))
        assert branch._node['status'] == 'finished'
        tree.close()
    finally:
        session.close()


def test_nested_branch_inherits_family_and_snapshot_lineage(tree):
    left = tree.fork(tree.root_snapshot, spec(tree, horizon=2))
    left.step(left.legal_actions().actions[0], left.state_revision)
    boundary = left.snapshot('left-decision-1')
    grandchild = tree.fork(boundary, spec(tree, 'grandchild', snapshot=boundary, horizon=0))
    assert grandchild.origin_family_id == tree.origin_family_id
    node = tree.export()['nodes'][-1]
    assert node['parent_branch_id'] == 'left'
    assert node['parent_snapshot_id'] == 'left-decision-1'
    assert node['divergence']['branch_id'] == 'left'
    assert node['depth'] == 2
    with pytest.raises(BranchError, match='substituted'):
        tree.fork(replace(boundary), spec(tree, 'forged', snapshot=boundary))


def test_policy_driver_failure_and_read_data_cannot_change_sibling(tree):
    left = tree.fork(tree.root_snapshot, spec(tree))
    right = tree.fork(tree.root_snapshot, spec(tree, 'right'))
    sibling_hash = snapshot_hash(right._session.snapshot().engine)

    def broken(observation, legal):
        assert not {'predictions', 'chance', 'snapshots', 'rng'} & set(observation.to_json())
        observation.primary['data'].clear()
        legal.actions.clear()
        raise RuntimeError('driver failed')

    with pytest.raises(RuntimeError, match='driver failed'):
        left.run(broken, **POLICY)
    assert left.closed and left._node['status'] == 'failed'
    assert tree.export()['attempted_decisions'] == 0
    assert snapshot_hash(right._session.snapshot().engine) == sibling_hash
    right.run(first, **POLICY)


def test_exhausted_and_unused_chance_tapes_fail_only_the_branch():
    from botbowl.lab.chance import ChanceError

    factual = gfi_boundary()
    tree = BranchTree(capture_snapshot(factual.game), origin_family_id='chance-family')
    try:
        natural = tree.fork(tree.root_snapshot, spec(tree))
        move = next(a for a in natural.legal_actions().actions if a.type == 'MOVE')
        natural.step(move, natural.state_revision)
        tape = natural._session._game.dice.chance.tape()
        with pytest.raises(ChanceError):
            tree.fork(tree.root_snapshot, spec(tree, 'unused', horizon=0,
                                               chance=ChancePolicy('replay', tape=tape)))
        assert tree.export()['nodes'][-1]['status'] == 'failed'
        exhausted = tree.fork(tree.root_snapshot, spec(
            tree, 'exhausted', chance=ChancePolicy('replay', tape={'version': 1, 'rolls': []})))
        natural_hash = snapshot_hash(natural._session.snapshot().engine)
        with pytest.raises(ExecutionFailure):
            exhausted.step(move, exhausted.state_revision)
        assert exhausted._node['status'] == 'failed'
        assert snapshot_hash(natural._session.snapshot().engine) == natural_hash
    finally:
        tree.close()
        factual.close()


def test_failed_replay_export_never_confirms_a_directory(tree, tmp_path):
    branch = tree.fork(tree.root_snapshot, spec(tree))
    branch.run(first, **POLICY)
    branch._hashes[0] = 'sha256:' + '0' * 64  # retained evidence corruption
    with pytest.raises(BranchError, match='diverged'):
        branch.export_replay(tmp_path, 'corrupt', replay_id='corrupt')
    assert not (tmp_path / 'corrupt').exists()
    assert (tmp_path / 'corrupt.partial').exists()
    assert branch._node['replay'] is None
