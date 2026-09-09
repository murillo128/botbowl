"""SIM-08 atomic edits, executable isolation and conservative reachability."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest
import botbowl as bb
from botbowl.core import procedure
from botbowl.lab.actions import ActionControl
from botbowl.lab.branches import BranchError, BranchSnapshot, BranchTree, BranchSpec
from botbowl.lab.chance import ChancePolicy
from botbowl.lab.interventions import InterventionError, apply_intervention, _ball, _rules
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.snapshot_io import read_snapshot, snapshot_hash, write_snapshot
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot
from tests.lab.test_snapshots import boundary, executable, logical, assert_references
from tests.lab.test_timeline import turn, until


def spec(operations, **overrides):
    result = dict(schema_version=1, branch_id='edited', snapshot_id='edited-snapshot',
                  author='test-author', reason='test experimental state', operations=operations)
    result.update(overrides)
    return result


def op(entity, entity_id, field, old_value, new_value):
    return dict(entity=entity, entity_id=entity_id, field=field,
                old_value=old_value, new_value=new_value)


def resource(game, value=7, side='home'):
    team = getattr(game.state, side + '_team')
    return op('team', team.team_id, 'rerolls', team.state.rerolls, value)


def source(game):
    return BranchSnapshot('source', game.timeline.context.branch_id, 'family-63', capture_snapshot(game))


def empty(game):
    return next({'x': x, 'y': y} for y in range(1, game.arena.height - 1)
                for x in range(1, game.arena.width - 1) if game.get_player_at(bb.Square(x, y)) is None)


@pytest.fixture
def game():
    value = logical(turn(3))
    yield value
    value.close()


@pytest.mark.parametrize('size', [1, 3, 5])
@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('fm', [False, True])
def test_valid_patch_all_fields_both_sides_small_boards(size, side, fm, tmp_path):
    game = logical(turn(size, rounds=2))
    until(game, lambda g: type(g.get_procedure()) is procedure.Turn and
          g.active_team is getattr(g.state, side + '_team'))
    if fm:
        game.enable_forward_model()
    saved = source(game)
    before = executable(game)
    original_hash = snapshot_hash(saved.engine)
    player = game.get_players_on_pitch(getattr(game.state, side + '_team'))[0]
    destination = empty(game)
    operations = [op('player', player.player_id, 'position',
                     {'x': player.position.x, 'y': player.position.y}, destination),
                  op('player', player.player_id, 'extra_ma', player.extra_ma, 1),
                  resource(game, side=side),
                  op('ball', 'ball', 'placement', _ball(game),
                     {'position': destination, 'carrier_id': player.player_id})]
    result = apply_intervention(saved, spec(operations))
    clone = clone_from_snapshot(result.snapshot.engine)
    assert executable(game) == before
    assert snapshot_hash(saved.engine) == original_hash
    assert clone.get_player(player.player_id).position == bb.Square(**destination)
    assert clone.get_player(player.player_id).extra_ma == 1
    assert getattr(clone.state, side + '_team').state.rerolls == 7
    assert clone.get_ball_carrier().player_id == player.player_id
    assert_references(clone)
    assert clone.trajectory.enabled == fm
    assert clone.capture_rng_state() == game.capture_rng_state()
    provenance = result.provenance
    assert provenance['kind'] == 'intervened' and provenance['reachability'] == 'synthetic'
    assert provenance['origin_family_id'] == 'family-63'
    assert provenance['parent_snapshot_id'] == 'source'
    assert provenance['patch']['operations'] == operations
    assert provenance['pre_hash'] == original_hash
    assert provenance['post_hash'] == snapshot_hash(result.snapshot.engine)
    assert provenance['continuation_chance'] is None
    assert clone.timeline.context.decision_seq == game.timeline.context.decision_seq
    assert [a.to_json() for a in clone.state.available_actions] == [a.to_json() for a in clone.get_procedure().available_actions()]
    path = tmp_path / 'edited.json'
    result.write(path)
    restored = clone_from_snapshot(read_snapshot(path))
    assert snapshot_hash(capture_snapshot(restored)) == provenance['post_hash']
    assert json.loads(path.read_text())['provenance'] == provenance
    restored.advance(bb.Action(bb.ActionType.END_TURN))
    assert executable(game) == before
    restored.close(); clone.close(); game.close()


def test_simultaneous_swap_follows_carrier(game):
    left, right = game.get_players_on_pitch()[:2]
    positions = [{'x': p.position.x, 'y': p.position.y} for p in (left, right)]
    original_ball = _ball(game)
    result = apply_intervention(source(game), spec([
        op('player', left.player_id, 'position', positions[0], positions[1]),
        op('player', right.player_id, 'position', positions[1], positions[0])]))
    clone = clone_from_snapshot(result.snapshot.engine)
    assert clone.get_player(left.player_id).position == bb.Square(**positions[1])
    assert clone.get_player(right.player_id).position == bb.Square(**positions[0])
    assert _ball(clone)['carrier_id'] == original_ball['carrier_id']
    clone.close()


@pytest.mark.parametrize('damage', ['collision', 'outside', 'unknown-player', 'unknown-team',
                                  'old-value', 'bool-old', 'forbidden', 'resource', 'bool-resource',
                                  'carrier', 'unknown-carrier', 'duplicate', 'executable', 'attribute'])
@pytest.mark.parametrize('fm', [False, True])
def test_rejections_are_atomic_even_after_private_writes(game, damage, fm):
    if fm:
        game.enable_forward_model()
    player, other = game.get_players_on_pitch()[:2]
    bad = op('player', player.player_id, 'position',
             {'x': player.position.x, 'y': player.position.y}, empty(game))
    if damage == 'collision': bad['new_value'] = {'x': other.position.x, 'y': other.position.y}
    if damage == 'outside': bad['new_value'] = {'x': 0, 'y': 1}
    if damage == 'unknown-player': bad['entity_id'] = 'missing'
    if damage == 'unknown-team': bad = resource(game); bad['entity_id'] = 'missing'
    if damage == 'old-value': bad['old_value'] = {'x': 100, 'y': 100}
    if damage == 'bool-old': bad = resource(game); bad['old_value'] = True
    if damage == 'forbidden': bad['field'] = '__class__'
    if damage == 'resource': bad = resource(game, -1)
    if damage == 'bool-resource': bad = resource(game, True)
    if damage in ('carrier', 'unknown-carrier'):
        bad = op('ball', 'ball', 'placement', _ball(game),
                 {'position': empty(game), 'carrier_id': player.player_id if damage == 'carrier' else 'missing'})
    if damage == 'executable': bad['module'] = 'pathlib'
    if damage == 'attribute': bad = op('player', player.player_id, 'extra_ag', 0, 99)
    operations = [resource(game, 8, 'away'), bad]
    if damage == 'duplicate': operations.append(deepcopy(bad))
    saved = source(game)
    before, state_hash = executable(game), snapshot_hash(saved.engine)
    identities = (id(game.state), id(game.dice), id(game.trajectory), id(game.timeline))
    with pytest.raises(InterventionError):
        apply_intervention(saved, spec(operations))
    assert executable(game) == before
    assert snapshot_hash(saved.engine) == state_hash
    assert identities == (id(game.state), id(game.dice), id(game.trajectory), id(game.timeline))


@pytest.mark.parametrize('kind', ['reroll', 'push', 'interception', 'apothecary'])
@pytest.mark.parametrize('side', ['home', 'away'])
def test_pending_decisions_rejected_without_rng_or_history_change(kind, side):
    game, _ = boundary(kind, True, side)
    saved, before = source(game), executable(game)
    with pytest.raises(InterventionError, match='Pending decision incompatible'):
        apply_intervention(saved, spec([resource(game)]))
    assert executable(game) == before
    game.close()


def test_rules_registry_changes_identity_and_rejects_paths(game):
    before = _rules(game)
    operation = op('configuration', 'rules', 'registered_config', before['config_digest'],
                   {'config_id': 'pathfinding-enabled', 'version': 1})
    result = apply_intervention(source(game), spec([operation]))
    assert result.provenance['rules_effective']['config_digest'] != before['config_digest']
    assert result.provenance['rules_effective']['config_id'] != before['config_id']
    assert _rules(game) == before and not game.config.pathfinding_enabled
    for invalid in ({'config_id': '../../gym-3', 'version': 1},
                    {'config_id': 'pathfinding-enabled', 'version': True},
                    {'config_id': 'pathfinding-enabled', 'version': 2},
                    {'config_id': 'pathfinding-enabled', 'version': 1, 'eval': '1+1'}):
        operation['new_value'] = invalid
        with pytest.raises(InterventionError):
            apply_intervention(source(game), spec([operation]))


def test_two_tree_branches_isolated_and_continuation_declares_chance(game):
    tree = BranchTree(capture_snapshot(game), origin_family_id='f', kind='observed')
    first = tree.intervene(tree.root_snapshot, spec([resource(game, 6)]))
    second = tree.intervene(tree.root_snapshot, spec([resource(game, 7)], branch_id='other', snapshot_id='other-s'))
    before = snapshot_hash(second.snapshot.engine)
    branch = tree.fork(first.snapshot, BranchSpec(
        branch_id='continuation', parent_branch_id=first.snapshot.branch_id,
        parent_snapshot_id=first.snapshot.snapshot_id, policy={'policy_id': 'manual', 'version': '1'},
        horizon=1, chance=ChancePolicy(seed=SeedSpec(12, 'intervention'))))
    assert branch.origin_family_id == tree.origin_family_id == second.snapshot.origin_family_id
    legal = branch.legal_actions()
    branch.step(next(action for action in legal.actions if action.type == 'END_TURN'), legal.state_revision)
    assert snapshot_hash(second.snapshot.engine) == before
    exported = tree.export()
    assert [n['kind'] for n in exported['nodes']][1:3] == ['intervened', 'intervened']
    assert exported['nodes'][1]['initial_action'] is None
    assert exported['nodes'][1]['chance'] is None
    assert exported['nodes'][-1]['chance']['mode'] == 'independent'
    before_tree = tree.export()
    with pytest.raises(BranchError, match='Duplicate'):
        tree.intervene(tree.root_snapshot, spec([resource(game)]))
    assert tree.export() == before_tree
    with pytest.raises(InterventionError):
        tree.intervene(tree.root_snapshot, spec([resource(game, -1)], branch_id='bad', snapshot_id='bad-s'))
    assert tree.export() == before_tree
    tree.close()


def test_only_replayed_complete_witness_enables_validated_recipe(game):
    player = game.get_players_on_pitch(game.active_team)[0]
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    start = source(game)
    control = ActionControl(game)
    action = control.encode(bb.Action(bb.ActionType.END_PLAYER_TURN)).to_json()
    game.advance(bb.Action(bb.ActionType.END_PLAYER_TURN))
    end = source(game)
    same = resource(game, game.state.home_team.state.rerolls)
    patch = spec([same], reachability='validated_recipe', recipe={'actions': [action]})
    result = apply_intervention(end, patch, recipe_start=start)
    assert result.provenance['reachability'] == 'validated_recipe'
    assert result.provenance['recipe_witness']['end_hash'] == result.provenance['edited_hash']
    patch['operations'][0]['new_value'] += 1
    with pytest.raises(InterventionError, match='does not reach'):
        apply_intervention(end, patch, recipe_start=start)
    with pytest.raises(InterventionError, match='start snapshot'):
        apply_intervention(end, patch)
    patch['recipe']['actions'] = []
    with pytest.raises(InterventionError, match='replayed legal actions'):
        apply_intervention(end, patch, recipe_start=start)
    unknown = apply_intervention(end, spec([same], reachability='unknown'))
    assert unknown.provenance['reachability'] == 'unknown'


def test_cli_data_only_roundtrip_and_rejection_preserves_destination(game, tmp_path):
    saved = source(game)
    source_path, patch_path, output = (tmp_path / name for name in ('source.json', 'patch.json', 'out.json'))
    write_snapshot(source_path, saved.engine, provenance=dict(
        snapshot_id=saved.snapshot_id, branch_id=saved.branch_id, origin_family_id=saved.origin_family_id))
    patch_path.write_text(json.dumps(spec([resource(game)])))
    command = [sys.executable, '-m', 'botbowl.lab.interventions', str(source_path), str(patch_path), str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    expected = output.read_bytes()
    restored = read_snapshot(output)
    assert snapshot_hash(restored) == json.loads(expected)['provenance']['post_hash']
    canary = tmp_path / 'executed'
    data = spec([resource(game)])
    data['__reduce__'] = ['pathlib.Path.touch', str(canary)]
    patch_path.write_text(json.dumps(data))
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2 and not canary.exists()
    assert output.read_bytes() == expected


@pytest.mark.parametrize('field', ['extra_st', 'extra_ag', 'extra_av'])
def test_other_public_attributes(game, field):
    player = game.get_players_on_pitch()[0]
    result = apply_intervention(source(game), spec([
        op('player', player.player_id, field, getattr(player, field), 1)]))
    clone = clone_from_snapshot(result.snapshot.engine)
    assert getattr(clone.get_player(player.player_id), field) == 1
    clone.close()


@pytest.mark.parametrize('field', ['bribes', 'babes', 'apothecaries'])
def test_other_resources_and_loose_ball(game, field):
    team = game.state.away_team
    position = empty(game)
    result = apply_intervention(source(game), spec([
        op('team', team.team_id, field, getattr(team.state, field), 2),
        op('ball', 'ball', 'placement', _ball(game), {'position': position, 'carrier_id': None})]))
    clone = clone_from_snapshot(result.snapshot.engine)
    assert getattr(clone.state.away_team.state, field) == 2
    assert clone.get_ball_carrier() is None
    assert _ball(clone) == {'position': position, 'carrier_id': None}
    clone.close()


def test_tree_intervention_budget_and_late_failure_publish_nothing(game):
    for limits in ({'max_nodes': 1}, {'max_depth': 1}):
        tree = BranchTree(capture_snapshot(game), kind='observed', origin_family_id='f', **limits)
        point = tree.root_snapshot
        if 'max_depth' in limits:
            point = tree.intervene(point, spec([resource(game)])).snapshot
        before = tree.export()
        with pytest.raises(BranchError, match='budget'):
            tree.intervene(point, spec([resource(game)], branch_id='new', snapshot_id='new-s'))
        assert tree.export() == before
        tree.close()
    tree = BranchTree(capture_snapshot(game), kind='observed', origin_family_id='f')
    before = tree.export()
    player, other = game.get_players_on_pitch()[:2]
    collision = op('player', player.player_id, 'position',
                   {'x': player.position.x, 'y': player.position.y},
                   {'x': other.position.x, 'y': other.position.y})
    with pytest.raises(InterventionError, match='collision'):
        tree.intervene(tree.root_snapshot, spec([resource(game), collision]))
    assert tree.export() == before
    tree.close()


def test_cli_cannot_overwrite_source(game, tmp_path):
    path, patch = tmp_path / 'source.json', tmp_path / 'patch.json'
    write_snapshot(path, source(game).engine, provenance={
        'snapshot_id': 's', 'branch_id': 'root', 'origin_family_id': 'f'})
    patch.write_text(json.dumps(spec([resource(game)])))
    before = path.read_bytes()
    result = subprocess.run([sys.executable, '-m', 'botbowl.lab.interventions',
                             str(path), str(patch), str(path)], capture_output=True, text=True)
    assert result.returncode == 2 and 'Output must differ' in result.stderr
    assert path.read_bytes() == before
