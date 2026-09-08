"""Re-signed hostile data and immutable graph regressions for the SIM-03 schema."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.core.forward_model import Reversible
from botbowl.lab import snapshot_io as io
from botbowl.lab.actions import PathOptionsV1, PositionV1, SkillOptionsV1
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot, restore_snapshot
from tests.lab.test_snapshot_io import field, node, reseal, saved_file
from tests.lab.test_snapshots import boundary, executable, logical
from tests.lab.test_timeline import fresh


def assert_rejected(path, doc, game, monkeypatch, message):
    # Both public digests are recomputable: neither is an authentication gate.
    reseal(path, doc, semantic=True)
    before = executable(game)
    identities = tuple(id(v) for v in (game.state, game.dice, game.trajectory, game.timeline))
    monkeypatch.setattr(io._Decoder, '__init__', lambda *args: pytest.fail('Reached decoder construction'))
    with pytest.raises(io.SnapshotFileError, match=message):
        restore_snapshot(game, io.read_snapshot(path))
    assert executable(game) == before
    assert tuple(id(v) for v in (game.state, game.dice, game.trajectory, game.timeline)) == identities


@pytest.mark.parametrize('missing', (
    'player', 'use_reroll', 'loner', 'can_use_team_reroll', 'can_use_pro', 'secondary_clock',
    'pro', 'skill', 'block_actions', 'block_action',
))
def test_reroll_local_fields_are_required_before_decoder(tmp_path, monkeypatch, missing):
    game, _ = boundary('reroll', False, 'home')
    game, path, doc, _ = saved_file(tmp_path, game)
    item = node(doc, 'procedure/Reroll')
    item['fields'] = [p for p in item['fields'] if p[0] != missing]
    assert_rejected(path, doc, game, monkeypatch, 'Missing required codec fields: procedure/Reroll')


# These are known engine domains, independently stated, not values generated
# from CODEC_SCHEMA. Each mutation retains valid tags and recomputes both hashes.
@pytest.mark.parametrize('tag,name,bad', (
    ('model/TeamState', 'rerolls', 'two'),
    ('model/TeamState', 'rerolls', True),
    ('model/TeamState', 'bribes', -1),
    ('model/PlayerState', 'up', 'yes'),
    ('model/PlayerState', 'moves', 1.5),
    ('model/DiceRoll', 'roll_type', {'enum': ['WeatherType', 'NICE']}),
    ('model/DiceRoll', 'target_higher', 1),
    ('model/DiceRoll', 'modifiers', 'zero'),
    ('model/D6', 'value', 7),
    ('model/D3', 'value', 4),
    ('model/D8', 'value', 0),
    ('model/BBDie', 'value', {'enum': ['RollType', 'AGILITY_ROLL']}),
    ('model/Configuration', 'rounds', 0),
    ('model/Configuration', 'debug_mode', 'false'),
    ('model/TimeLimits', 'turn', -1),
    ('model/Player', 'role', 'Lineman'),
    ('model/Role', 'ma', 'six'),
    ('model/Race', 'apothecary', 1),
    ('model/Team', 'rerolls', 'two'),
    ('model/RuleSet', 'se_interval', '50000'),
    ('model/Inducement', 'cost', -2),
    ('model/ActionChoice', 'disabled', 'false'),
    ('model/Outcome', 'outcome_type', {'enum': ['WeatherType', 'NICE']}),
    ('model/Outcome', 'n', 'UNKNOWN_EFFECT'),
    ('model/Action', 'action_type', {'enum': ['RollType', 'AGILITY_ROLL']}),
    ('model/Ball', 'is_carried', 1),
    ('model/Bomb', 'on_ground', 'yes'),
    ('procedure/Dodge', 'roll', 123),
    ('procedure/Dodge', 'break_tackle_target', 7),
    ('procedure/Reroll', 'use_reroll', 'yes'),
    ('procedure/Turn', 'pass_available', 'yes'),
    ('procedure/MoveAction', 'can_undo', 'yes'),
    ('data/TimelineContext', 'half', 'one'),
    ('data/TimelineContext', 'half', 3),
    ('data/TimelineContext', 'event_seq', -1),
    ('data/TimelineEvent', 'decision_seq', 'one'),
    ('data/DecisionEnvelope', 'event_stop', 'one'),
    ('data/DecisionEnvelope', 'status', 'UNKNOWN'),
    ('data/SeedSpec', 'purpose', 'unsupported-purpose'),
    ('data/SeedSpec', 'master_seed', 2**256),
    ('data/SeedSpec', 'derivation_version', True),
    ('data/SeedSpec', 'component_id', '../module'),
    ('data/ActionV1', 'schema_version', True),
    ('data/ActionV1', 'actor_id', 'spectator'),
    ('data/PositionV1', 'x', 'three'),
    ('data/SkillOptionsV1', 'skill', 'NICE'),
    ('data/TimelineCheckpoint', 'activation_open', 1),
    ('LogicalTime', 'value', -1),
    ('Clock', 'is_primary', 1),
    ('Agent', 'human', 'yes'),
))
def test_closed_scalar_enum_and_reference_domains(tmp_path, monkeypatch, tag, name, bad):
    game, _ = boundary('reroll', False, 'home')
    # Keep additional ordinary data codecs reachable without changing the active
    # Reroll -> Dodge continuation. Constructors run only in the source fixture.
    bomb = object.__new__(bb.Bomb)
    Reversible.__init__(bomb)  # Bomb's engine constructor does not initialize its reversible base.
    bb.Bomb.__init__(bomb, bb.Square(2, 2))
    game.get_procedure().context.context = [
        SeedSpec(13), PositionV1(3, 4), SkillOptionsV1('DODGE'), PathOptionsV1([PositionV1(3, 4)]),
        game.timeline.capture(), bomb, bb.Action(bb.ActionType.USE_REROLL),
        bb.D3(game.dice), bb.D8(game.dice), bb.BBDie(game.dice),
    ]
    game.add_secondary_clock(game.active_team)
    game, path, doc, _ = saved_file(tmp_path, game)
    field(node(doc, tag), name, bad)
    assert_rejected(path, doc, game, monkeypatch, 'Invalid field domain: ' + tag + '.' + name)


@pytest.mark.parametrize('tag,name', (
    ('model/PlayerState', 'used_skills'), ('model/PlayerState', 'injuries_gained'),
    ('model/Role', 'skills'), ('model/Role', 'n_skill_sets'), ('model/Race', 'roles'),
    ('model/RuleSet', 'inducements'), ('model/GameState', 'reports'),
    ('model/ActionChoice', 'players'), ('model/ActionChoice', 'positions'),
    ('model/DiceRoll', 'dice'), ('procedure/Reroll', 'block_actions'),
    ('procedure/Dodge', 'diving_tacklers'), ('Timeline', '_events'),
))
def test_collection_item_domains(tmp_path, monkeypatch, tag, name):
    game, _ = boundary('reroll', False, 'home')
    game, path, doc, _ = saved_file(tmp_path, game)
    value = dict(node(doc, tag)['fields'])[name]
    items = doc['payload']['nodes'][value['ref']]['items']
    items.append('invalid item')
    assert_rejected(path, doc, game, monkeypatch, 'Invalid field domain')


@pytest.mark.parametrize('name', ('Touchback', 'HighKick', 'EatThrall'))
@pytest.mark.parametrize('started', (False, True))
def test_lazy_fields_follow_real_start_phase(tmp_path, monkeypatch, name, started):
    game = logical(fresh())
    player = game.state.home_team.players[0]
    game.put(player, bb.Square(3, 3))
    ball = bb.Ball(bb.Square(3, 3))
    game.state.receiving_this_drive = game.state.home_team
    child = getattr(proc, name)(game, player if name == 'EatThrall' else ball)
    game.state.stack.pop()  # Retain as context; the source decision remains StartGame.
    game.get_procedure().context = child
    if started:
        child.start()
        child.started = True
    game, path, doc, _ = saved_file(tmp_path, game)
    clone = clone_from_snapshot(io.read_snapshot(path))
    lazy_name = {'Touchback': 'players_on_pitch_standing', 'HighKick': 'available_players',
                 'EatThrall': 'victim_pos'}[name]
    assert hasattr(clone.get_procedure().context, lazy_name) == started
    item = node(doc, 'procedure/' + name)
    if started:
        item['fields'] = [p for p in item['fields'] if p[0] != lazy_name]
    else:
        item['fields'].append([lazy_name, None])
    assert_rejected(path, doc, game, monkeypatch, 'Invalid lazy field phase')


def dag_document(tmp_path, levels):
    game, path, doc, _ = saved_file(tmp_path)
    nodes = doc['payload']['nodes']
    previous = 'leaf'
    for _ in range(levels):
        index = len(nodes)
        nodes.append({'id': index, 'type': 'tuple', 'items': [previous, previous]})
        previous = {'ref': index}
    index = len(nodes)
    nodes.append({'id': index, 'type': 'dict', 'items': [[previous, 'value']]})
    field(node(doc, 'procedure/StartGame'), 'context', {'ref': index})
    return game, path, doc


def test_small_shared_immutable_dag_rejected_with_explicit_work_limit(tmp_path, monkeypatch):
    game, path, doc = dag_document(tmp_path, 26)
    assert len(io._json(doc)) < 200000 and len(doc['payload']['nodes']) < 1500
    # The semantic helper must itself reject boundedly; no exponentially expanded
    # sort-key bytes or Python key may be built even outside read_snapshot.
    with pytest.raises(io.SnapshotLimitError, match='work'):
        io._semantic_hash(doc['payload'], doc['scope'])
    reseal(path, doc)
    before = executable(game)
    monkeypatch.setattr(io._Decoder, '__init__', lambda *args: pytest.fail('Reached decoder construction'))
    with pytest.raises(io.SnapshotLimitError, match='work'):
        restore_snapshot(game, io.read_snapshot(path))
    assert executable(game) == before


def test_affordable_shared_dag_retains_aliases_and_resaves(tmp_path):
    _, path, doc = dag_document(tmp_path, 8)
    reseal(path, doc, semantic=True)
    clone = clone_from_snapshot(io.read_snapshot(path))
    key = next(iter(clone.get_procedure().context))
    for _ in range(7):
        assert key[0] is key[1]
        key = key[0]
    again = io.write_snapshot(path, capture_snapshot(clone))
    assert again.semantic_state_hash == doc['semantic_state_hash']
    with pytest.raises(io.SnapshotLimitError, match='work'):
        io.read_snapshot(path, limits=replace(io.SnapshotLimits(), max_work=100))


def unordered_context():
    shared = ('ordered', frozenset(('m', 'n')))
    return {frozenset(('a', 'c')): 1, frozenset(('b', 'd')): 2,
            frozenset((('z', frozenset(('e', 'g'))), ('w', frozenset(('f', 'h'))))): shared,
            ('tuple', frozenset(('i', 'j'))): shared}


def test_recursive_unordered_wire_order_tuple_order_and_alias_identity(tmp_path):
    game = logical(fresh())
    game.get_procedure().context = unordered_context()
    _, path, doc, envelope = saved_file(tmp_path, game)
    for item in doc['payload']['nodes']:
        if item['type'] in ('frozenset', 'set', 'rset', 'dict', 'rdict'):
            item['items'].reverse()
    assert io._semantic_hash(doc['payload'], doc['scope']) == envelope.semantic_state_hash
    reseal(path, doc, semantic=True)
    clone = clone_from_snapshot(io.read_snapshot(path))
    assert clone.get_procedure().context == unordered_context()
    changed = deepcopy(doc)
    ordered = next(n for n in changed['payload']['nodes'] if n['type'] == 'tuple' and n['items'][0] == 'ordered')
    ordered['items'].reverse()
    assert io._semantic_hash(changed['payload'], changed['scope']) != envelope.semantic_state_hash
    nodes = doc['payload']['nodes']
    shared = next(n for n in nodes if n['type'] == 'tuple' and n['items'][0] == 'ordered')
    copied = dict(shared, id=len(nodes))
    nodes.append(copied)
    context = node(doc, 'procedure/StartGame')
    pairs = nodes[dict(context['fields'])['context']['ref']]['items']
    next(pair for pair in pairs if pair[1] == {'ref': shared['id']})[1] = {'ref': copied['id']}
    assert io._semantic_hash(doc['payload'], doc['scope']) != envelope.semantic_state_hash


@pytest.mark.parametrize('seed', (1, 2, 3, 4, 5, 6, 7, 8))
def test_fresh_process_resave_across_hash_seeds(tmp_path, seed):
    game = logical(fresh())
    game.get_procedure().context = unordered_context()
    root = Path(os.environ.get('BOTBOWL_SNAPSHOT_EVIDENCE', tmp_path)) / ('resave-seed-' + str(seed))
    root.mkdir(parents=True, exist_ok=True)
    _, path, _, envelope = saved_file(root, game)
    with (root / 'resave.log').open('w') as log:
        result = subprocess.run([sys.executable, '-m', 'tests.lab.snapshot_process', 'resave', str(root)],
                                env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                                stdout=log, stderr=subprocess.STDOUT, timeout=20)
    assert result.returncode == 0, (root / 'resave.log').read_text()
    evidence = json.loads((root / 'resave-evidence.json').read_text())
    assert evidence['pid'] != os.getpid()
    assert evidence['hash_seed'] == str(seed)
    assert evidence['semantic_state_hash'] == envelope.semantic_state_hash
    clone = clone_from_snapshot(io.read_snapshot(root / 'resaved.json'))
    assert clone.get_procedure().context == unordered_context()


@pytest.mark.parametrize('declined', (False, True))
def test_loner_result_exists_only_after_declined_retry(tmp_path, monkeypatch, declined):
    game, _ = boundary('reroll', False, 'home')
    root = game.get_procedure()
    loner = proc.Loner(game, root.player, root.context)
    game.state.stack.pop()
    root.context.context = loner
    loner.started = True
    with game.dice.force(d6=[1 if declined else 6]):
        finished = loner.step(None)
    if declined:
        assert not finished and loner.reroll is game.get_procedure()
        game.state.stack.pop()
        loner.reroll.use_reroll = False
        assert loner.step(None)
    else:
        assert finished and loner.success
    loner.done = True
    game, path, doc, _ = saved_file(tmp_path, game)
    clone = clone_from_snapshot(io.read_snapshot(path))
    assert hasattr(clone.get_procedure().context.context, 'result') == declined
    item = node(doc, 'procedure/Loner')
    if declined:
        item['fields'] = [p for p in item['fields'] if p[0] != 'result']
    else:
        item['fields'].append(['result', False])
    assert_rejected(path, doc, game, monkeypatch, 'Invalid lazy field phase')


def test_write_work_preflight_precedes_private_clone_and_preserves_file(tmp_path, monkeypatch):
    game, path, _, _ = saved_file(tmp_path)
    saved = capture_snapshot(game)
    before = path.read_bytes()
    state = executable(game)
    monkeypatch.setattr(io.memory, 'clone_from_snapshot', lambda *a, **kw: pytest.fail('Work preflight ran too late'))
    with pytest.raises(io.SnapshotLimitError, match='work'):
        io.write_snapshot(path, saved, limits=replace(io.SnapshotLimits(), max_work=5))
    assert path.read_bytes() == before and executable(game) == state
    assert list(tmp_path.glob('.*.tmp')) == []


def test_immutable_key_identities_are_memoized_without_expanding_python_keys():
    nodes = []
    previous = 'leaf'
    for index in range(26):
        nodes.append({'id': index, 'type': 'tuple', 'items': [previous, previous]})
        previous = {'ref': index}
    work = io._Work(io.SnapshotLimits(max_work=200))
    keys = io._GraphKeys(nodes, work)
    keys.key(previous)
    assert len(keys.memo) == 26 and work.used < 200
    # A small wire DAG can have cheap identity checks but unaffordable Python
    # tuple hashing. The independent materialization preflight rejects it.
    with pytest.raises(io.SnapshotLimitError, match='work'):
        io._GraphKeys(nodes, io._Work(io.SnapshotLimits())).validate()
