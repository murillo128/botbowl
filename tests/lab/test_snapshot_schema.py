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


GRAPH_FAMILIES = ('primitive', 'mutable', 'anchored', 'cycle2', 'cycle3',
                  'set', 'rset', 'frozenset', 'symmetric2', 'symmetric3')


def permute_wire(document, variant):
    result = deepcopy(document)
    nodes = result['payload']['nodes']
    for item in nodes:
        if variant in ('fields', 'combined') and 'fields' in item:
            item['fields'].reverse()
        if (variant in ('unordered', 'combined') or variant == item['type']) and item['type'] in ('dict', 'rdict', 'set', 'rset', 'frozenset'):
            item['items'].reverse()
    if variant in ('ids', 'combined'):
        count = len(nodes)
        def renumber(value):
            if type(value) is dict:
                if set(value) == {'ref'}:
                    value['ref'] = count - 1 - value['ref']
                else:
                    for child in value.values():
                        renumber(child)
            elif type(value) is list:
                for child in value:
                    renumber(child)
        renumber(result['payload'])
        nodes.reverse()
        for index, item in enumerate(nodes):
            item['id'] = index
    return result


def graph_document(tmp_path, family):
    from tests.lab.snapshot_process import graph_registry, identity_graph, projection_graph
    codecs = graph_registry()
    subject = projection_graph() if family == 'projection' else identity_graph(family)
    snapshot = capture_snapshot(subject, scope='episode' if family == 'projection' else 'engine', adapters=codecs)
    path = tmp_path / 'snapshot.json'
    envelope = io.write_snapshot(path, snapshot, adapters=codecs)
    return subject, path, json.loads(path.read_text()), envelope, codecs


@pytest.mark.parametrize('family', GRAPH_FAMILIES + ('projection',))
def test_finite_canonical_graph_permutations_and_fresh_processes(tmp_path, family):
    from tests.lab.snapshot_process import identity_receipt
    root = Path(os.environ.get('BOTBOWL_SNAPSHOT_EVIDENCE', tmp_path)) / ('graph-' + family)
    root.mkdir(parents=True, exist_ok=True)
    subject, path, doc, envelope, codecs = graph_document(root, family)
    scope = doc['scope']
    work_counts = []
    expected = None if family == 'projection' else identity_receipt(subject, family)
    variants = ('ids', 'fields', 'dict', 'rdict', 'set', 'rset', 'frozenset', 'combined')
    receipts = []
    for seed in range(1, 9):
        candidate = permute_wire(doc, variants[seed - 1])
        work = io._Work(io.SnapshotLimits())
        assert io._semantic_hash(candidate['payload'], scope, work=work) == envelope.semantic_state_hash
        work_counts.append(work.used)
        reseal(path, candidate, semantic=True)
        loaded = clone_from_snapshot(io.read_snapshot(path, adapters=codecs), adapters=codecs)
        if family == 'projection':
            shared = loaded.game.get_procedure().context
            assert shared is loaded._policies['home'].state and shared[2] is shared
        else:
            assert identity_receipt(loaded, family) == expected
        child = root / ('seed-' + str(seed))
        child.mkdir(exist_ok=True)
        (child / 'snapshot.json').write_bytes(path.read_bytes())
        with (child / 'process.log').open('w') as log:
            result = subprocess.run([sys.executable, '-m', 'tests.lab.snapshot_process',
                                     'graph-resave', str(child), family],
                                    env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                                    stdout=log, stderr=subprocess.STDOUT, timeout=30)
        assert result.returncode == 0, (child / 'process.log').read_text()
        receipt = json.loads((child / 'graph-resave-receipt.json').read_text())
        assert receipt['semantic_state_hash'] == envelope.semantic_state_hash
        if expected is not None:
            assert receipt['incidence'] == expected
        receipts.append(receipt)
    assert len(set(work_counts)) == 1
    (root / 'matrix.json').write_text(json.dumps({'family': family, 'wire_variants': variants,
        'work': work_counts, 'receipts': receipts}, indent=2) + '\n')


@pytest.mark.parametrize('family', GRAPH_FAMILIES)
def test_independently_constructed_identity_graph_producers(tmp_path, family):
    root = Path(os.environ.get('BOTBOWL_SNAPSHOT_EVIDENCE', tmp_path)) / ('producers-' + family)
    receipts = []
    for seed in (1, 8):
        child = root / str(seed)
        child.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'tests.lab.snapshot_process', 'graph-produce', str(child), family]
        if seed == 8:
            command.append('reverse')
        with (child / 'process.log').open('w') as log:
            result = subprocess.run(command, env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                                    stdout=log, stderr=subprocess.STDOUT, timeout=30)
        assert result.returncode == 0, (child / 'process.log').read_text()
        receipts.append(json.loads((child / 'graph-produce-receipt.json').read_text()))
    assert receipts[0]['pid'] != receipts[1]['pid']
    assert receipts[0]['semantic_state_hash'] == receipts[1]['semantic_state_hash']
    assert receipts[0]['incidence'] == receipts[1]['incidence']


@pytest.mark.parametrize('family', ('primitive', 'mutable', 'symmetric2', 'symmetric3'))
def test_every_tied_mapping_pair_permutation(tmp_path, family):
    from itertools import permutations
    from tests.lab.snapshot_process import identity_receipt
    game, path, doc, envelope, _ = graph_document(tmp_path, family)
    expected = identity_receipt(game, family)
    mapping = next(item for item in doc['payload']['nodes'] if item['type'] == 'dict'
                   and item['items'] and all(type(key) is dict and 'ref' in key and
                       doc['payload']['nodes'][key['ref']]['type'] == 'procedure/Procedure'
                       for key, _ in item['items']))
    pairs = list(mapping['items'])
    for ordered in permutations(pairs):
        # Reverse/permutate only these wire pairs; refs, associations, fields
        # and every other unordered container stay exactly as originally saved.
        mapping['items'] = list(ordered)
        assert io._semantic_hash(doc['payload'], 'engine') == envelope.semantic_state_hash
        reseal(path, doc, semantic=True)
        assert identity_receipt(clone_from_snapshot(io.read_snapshot(path)), family) == expected


@pytest.mark.parametrize('change', ('association', 'alias', 'cycle', 'tuple', 'projection-alias', 'opaque-literal'))
def test_canonical_hash_distinguishes_anchored_nonisomorphic_graphs(tmp_path, change):
    family = {'association': 'anchored', 'alias': 'anchored', 'cycle': 'cycle3', 'tuple': 'frozenset',
              'projection-alias': 'projection', 'opaque-literal': 'projection'}[change]
    subject, path, doc, envelope, codecs = graph_document(tmp_path, family)
    if family == 'projection':
        data = subject.game.get_procedure().context
        if change == 'projection-alias':
            subject._policies['home'].state = deepcopy(data)
        else:
            # In opaque adapter data a string equal to a local engine ID stays
            # literal. It must not acquire the normal engine-ID normalization.
            data[0] = 'different-opaque-literal'
    else:
        context = subject.get_procedure().context
        keys = context['anchors']
        if change == 'association':
            context['map'][keys[0]], context['map'][keys[1]] = context['map'][keys[1]], context['map'][keys[0]]
        elif change == 'alias':
            context['map'][keys[1]] = context['map'][keys[0]]
        elif change == 'cycle':
            keys[0].context['next'] = keys[2]
        else:
            value = context['composites'].pop(tuple(keys))
            context['composites'][tuple(reversed(keys))] = value
    changed = io.write_snapshot(path, capture_snapshot(subject, scope=doc['scope'], adapters=codecs), adapters=codecs)
    assert changed.semantic_state_hash != envelope.semantic_state_hash


def test_symmetric_canonical_work_rejection_is_atomic_and_order_invariant(tmp_path, monkeypatch):
    from tests.lab.snapshot_process import graph_registry, identity_graph
    small, path, doc, envelope, _ = graph_document(tmp_path, 'symmetric3')
    limits = replace(io.SnapshotLimits(), max_work=500000)
    assert io._semantic_hash(doc['payload'], doc['scope'], limits=limits) == envelope.semantic_state_hash
    io.read_snapshot(path, limits=limits)
    io.write_snapshot(path, capture_snapshot(small), limits=limits)
    prior = path.read_bytes()
    live = logical(fresh())
    before = executable(live)
    identities = tuple(id(v) for v in (live.state, live.dice, live.trajectory, live.timeline))
    saved = capture_snapshot(identity_graph('symmetric10'))
    encoder = io._Encoder(io.SnapshotLimits())
    doc['payload'] = {'roots': {name: encoder.atom(value) for name, value in (
        ('game', saved._game), ('episode', saved._episode), ('components', saved._components))}, 'nodes': encoder.nodes}
    # This budget admits the small 2/3-member fixtures, but bounded search of the
    # larger true symmetry must reject. No candidate may leak as a valid hash.
    monkeypatch.setattr(io._Decoder, '__init__', lambda *a: pytest.fail('Reached decoder construction'))
    monkeypatch.setattr(io.memory, 'clone_from_snapshot', lambda *a, **k: pytest.fail('Reached private clone/adapters'))
    rejected_work = []
    for variant in ('ids', 'fields', 'unordered', 'combined'):
        candidate = permute_wire(doc, variant)
        work = io._Work(limits)
        with pytest.raises(io.SnapshotLimitError, match='work'):
            io._semantic_hash(candidate['payload'], candidate['scope'], work=work)
        rejected_work.append(work.used)
        hostile = tmp_path / ('hostile-' + variant + '.json')
        reseal(hostile, candidate)
        with pytest.raises(io.SnapshotLimitError, match='work'):
            restore_snapshot(live, io.read_snapshot(hostile, limits=limits, adapters=graph_registry()))
    assert len(set(rejected_work)) == 1
    with pytest.raises(io.SnapshotLimitError, match='work'):
        io.write_snapshot(path, saved, limits=limits)
    assert path.read_bytes() == prior and not list(tmp_path.glob('.*.tmp'))
    assert executable(live) == before
    assert tuple(id(v) for v in (live.state, live.dice, live.trajectory, live.timeline)) == identities


# A1: 20 exercised branches across the decision's 14 target-bearing sites.
# The three pass variants share one constructor, as do the five path variants.
TARGET_PRODUCERS = (
    ('handoff', 'Game.get_handoff_actions', 'HANDOFF'),
    ('stand-up', 'Game.get_stand_up_actions', 'STAND_UP'),
    ('adjacent-move', 'Game.get_adjacent_move_actions', 'MOVE'),
    ('leap', 'Game.get_leap_actions', 'LEAP'),
    ('foul', 'Game.get_foul_actions', 'FOUL'),
    ('pickup-teammate', 'Game.get_pickup_teammate_actions', 'PICKUP_TEAM_MATE'),
    ('block', 'Game.get_block_actions/BLOCK', 'BLOCK'),
    ('stab', 'Game.get_block_actions/STAB', 'STAB'),
    ('pass', 'Game.get_pass_actions', 'PASS'),
    ('throw-teammate', 'Game.get_pass_actions', 'THROW_TEAM_MATE'),
    ('throw-bomb', 'Game.get_pass_actions', 'THROW_BOMB'),
    ('hypnotic', 'Game.get_hypnotic_gaze_actions', 'HYPNOTIC_GAZE'),
    ('interception', 'Interception.available_actions', 'SELECT_PLAYER'),
    ('path-move', 'MoveAction._get_actions_from_paths', 'MOVE'),
    ('path-block', 'MoveAction._get_actions_from_paths', 'BLOCK'),
    ('path-stab', 'MoveAction._get_actions_from_paths', 'STAB'),
    ('path-handoff', 'MoveAction._get_actions_from_paths', 'HANDOFF'),
    ('path-foul', 'MoveAction._get_actions_from_paths', 'FOUL'),
    ('frenzy-block', 'Frenzy.available_actions/BLOCK', 'BLOCK'),
    ('frenzy-stab', 'Frenzy.available_actions/STAB', 'STAB'),
)


def target_producer(branch, action_name):
    from tests.lab.snapshot_process import armor_boundary
    from tests.lab.test_timeline import players, turn
    if branch.startswith('frenzy-') or branch in ('path-handoff', 'path-foul'):
        game = armor_boundary('frenzy-stakes' if branch.startswith('frenzy-') else branch)
        choices = game.get_procedure().available_actions()
    else:
        game = logical(turn(pathfinding=branch.startswith('path-'), rounds=2))
        attacker, ally, other, defender = players(game, [(3, 3), (3, 4), (2, 3)], [(4, 3)], ball=(3, 3))
        attacker.extra_skills.extend((bb.Skill.STAB, bb.Skill.LEAP, bb.Skill.HYPNOTIC_GAZE, bb.Skill.ALWAYS_HUNGRY))
        ally.extra_skills.append(bb.Skill.RIGHT_STUFF)
        other.extra_skills.append(bb.Skill.RIGHT_STUFF)
        if branch.startswith('path-'):
            game.advance(bb.Action(bb.ActionType.START_BLITZ, player=attacker))
            choices = game.get_procedure().available_actions()
        elif branch == 'interception':
            child = proc.Interception(game, defender.team, game.state.pitch.balls[0], [defender], attacker)
            game.state.stack.pop()
            choices = child.available_actions()
        elif branch in ('pass', 'throw-teammate', 'throw-bomb'):
            piece = game.state.pitch.balls[0] if branch == 'pass' else ally
            if branch == 'throw-bomb':
                piece = object.__new__(bb.Bomb)
                Reversible.__init__(piece)
                bb.Bomb.__init__(piece, attacker.position)
            choices = game.get_pass_actions(attacker, piece)
        else:
            if branch == 'stand-up':
                attacker.state.up = False
                attacker.extra_ma = -20
            if branch == 'foul':
                defender.state.up = False
            method = {'handoff': 'get_handoff_actions', 'stand-up': 'get_stand_up_actions',
                      'adjacent-move': 'get_adjacent_move_actions', 'leap': 'get_leap_actions',
                      'foul': 'get_foul_actions', 'pickup-teammate': 'get_pickup_teammate_actions',
                      'block': 'get_block_actions', 'stab': 'get_block_actions',
                      'hypnotic': 'get_hypnotic_gaze_actions'}[branch]
            choices = getattr(game, method)(attacker)
    choice = next(c for c in choices if c.action_type.name == action_name)
    # Direct producer output retained as inert context; A2 separately proves
    # playable continuation. The file boundary clears paths, retaining targets.
    game.get_procedure().context = choice
    return game, choice


def target_shape(value):
    return [type(value).__name__, [target_shape(v) for v in value]] if isinstance(value, (list, tuple)) else value


@pytest.mark.parametrize('branch,site,action_name', TARGET_PRODUCERS)
def test_all_target_producer_branches_round_trip(tmp_path, branch, site, action_name):
    game, choice = target_producer(branch, action_name)
    expected = target_shape(choice.rolls)
    if branch in ('path-handoff', 'path-foul'):
        assert choice.rolls and all(type(v) is int for v in choice.rolls)
    elif branch in ('path-move', 'path-block', 'path-stab'):
        assert not choice.rolls and choice.paths
    else:
        assert choice.rolls and all(isinstance(row, (list, tuple)) for row in choice.rolls)
    _, path, _, _ = saved_file(tmp_path, game)
    restored = clone_from_snapshot(io.read_snapshot(path)).get_procedure().context
    assert restored.action_type.name == action_name
    assert target_shape(restored.rolls) == expected
    assert list(restored.block_dice) == list(choice.block_dice) and not restored.paths
    if branch == 'pickup-teammate':
        assert len(restored.rolls) == 2 and restored.rolls[0] is restored.rolls[1]
    root = Path(os.environ.get('BOTBOWL_SNAPSHOT_EVIDENCE', tmp_path)) / 'producer-inventory'
    root.mkdir(parents=True, exist_ok=True)
    (root / (branch + '.json')).write_text(json.dumps({'site': site, 'branch': branch,
        'fixture': 'direct producer data in Procedure.context', 'action': action_name, 'shape': expected,
        'block_dice': list(choice.block_dice)}, indent=2) + '\n')


def test_finite_target_constructor_inventory():
    import ast
    sites = []
    for source in (bb.Game, proc.Procedure):
        import inspect
        tree = ast.parse(Path(inspect.getfile(source)).read_text())
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for method in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                for call in ast.walk(method):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'ActionChoice':
                        sites.append((cls.name, method.name, any(k.arg == 'rolls' for k in call.keywords)))
    assert len(sites) == 92 and sum(target for _, _, target in sites) == 14
    assert len({site for _, site, _ in TARGET_PRODUCERS}) == 14


@pytest.mark.parametrize('frenzy', (False, True))
@pytest.mark.parametrize('stakes', (False, True))
@pytest.mark.parametrize('armor', (1, 10))
def test_stab_producer_armor_extremes(tmp_path, frenzy, stakes, armor):
    from tests.lab.snapshot_process import armor_boundary
    game = armor_boundary('frenzy-stakes')
    continuation = game.get_procedure()
    attacker, defender = continuation.attacker, continuation.defender
    # Effective reductions are capped at two below the declared role. A custom
    # low-armor role therefore exercises the engine's absolute minimum; the
    # shared ruleset role is updated too, retaining descriptor coherence.
    if armor == 1:
        defender.role.av = 1
    defender.extra_av = -100 if armor == 1 else 100
    assert defender.get_av() == armor
    if not stakes:
        attacker.extra_skills.remove(bb.Skill.STAKES)
    if frenzy:
        choices = continuation.available_actions()
    else:
        attacker.state.has_blocked = False
        choices = game.get_block_actions(attacker)
    choice = next(c for c in choices if c.action_type == bb.ActionType.STAB)
    expected = armor + 1 + (-1 if frenzy else 1) * int(stakes)
    assert choice.rolls == [[expected]]
    continuation.context = choice
    _, path, _, _ = saved_file(tmp_path, game)
    restored = clone_from_snapshot(io.read_snapshot(path)).get_procedure().context
    assert restored.rolls == [[expected]]


@pytest.mark.parametrize('armor,expected', ((1, 2), (10, 12)))
def test_foul_producer_assist_clamps(tmp_path, armor, expected):
    from tests.lab.test_timeline import players, turn
    game = logical(turn(rounds=2))
    own = [(3, 3), (4, 4), (5, 3)] if armor == 1 else [(3, 3)]
    opponents = [(4, 3)] if armor == 1 else [(4, 3), (3, 4), (2, 3)]
    placed = players(game, own, opponents, ball=(3, 3))
    attacker, defender = placed[0], placed[len(own)]
    defender.state.up = False
    if armor == 1:
        role = next(role for race in game.ruleset.races if race.name == defender.team.race
                    for role in race.roles if role.name == defender.role.name)
        role.av = 1
        for team in game.state.teams:
            if team.race == defender.team.race:
                for player in team.players:
                    if player.role.name == role.name:
                        player.role = role
    defender.extra_av = -100 if armor == 1 else 100
    assert defender.get_av() == armor
    assists_from = game.get_assisting_players(attacker, defender, foul=True)
    assists_to = game.get_assisting_players(defender, attacker, foul=True)
    unclamped = armor + 1 - len(assists_from) + len(assists_to)
    assert unclamped < 2 if armor == 1 else unclamped > 12
    choice = game.get_foul_actions(attacker)[0]
    assert choice.rolls == [[expected]]
    game.get_procedure().context = choice
    _, path, _, _ = saved_file(tmp_path, game)
    assert clone_from_snapshot(io.read_snapshot(path)).get_procedure().context.rolls == [[expected]]


ACCEPTED_TARGETS = (
    ('MOVE', [[2, 6], []]), ('STAND_UP', [[]]), ('LEAP', [[2, 6]]),
    ('PICKUP_TEAM_MATE', [[], [2]]), ('BLOCK', [[2, 6]]), ('PASS', [[2, 6]]),
    ('THROW_TEAM_MATE', [[2, 6]]), ('THROW_BOMB', [[2, 6]]), ('HYPNOTIC_GAZE', [[2, 6]]),
    ('SELECT_PLAYER', [[2, 6]]), ('HANDOFF', [[2, 6], []]), ('HANDOFF', [2, 6]),
    ('FOUL', [[2, 12], []]), ('FOUL', [2, 12]), ('STAB', [[1], [12], [2, 6, 12]]),
)


@pytest.mark.parametrize('container', ('list', 'rlist', 'tuple'))
@pytest.mark.parametrize('action,rolls', ACCEPTED_TARGETS)
def test_target_endpoints_sequence_shapes_and_aliases(tmp_path, action, rolls, container):
    from botbowl.core.forward_model import ReversibleList
    cls = {'list': list, 'rlist': ReversibleList, 'tuple': tuple}[container]
    data = cls(cls(row) if isinstance(row, list) else row for row in rolls)
    # Keep shared repeated rows rather than flattening or regenerating targets.
    if rolls and isinstance(rolls[0], list):
        data = cls([data[0], data[0]] + list(data)[1:])
    game = logical(fresh())
    choice = bb.ActionChoice(getattr(bb.ActionType, action), team=game.state.home_team,
                             rolls=data, block_dice=cls([-3, -2, 1, 2, 3]) if action == 'BLOCK' else None)
    game.get_procedure().context = choice
    _, path, _, _ = saved_file(tmp_path, game)
    restored = clone_from_snapshot(io.read_snapshot(path)).get_procedure().context
    assert target_shape(restored.rolls) == target_shape(data)
    if rolls and isinstance(rolls[0], list):
        assert restored.rolls[0] is restored.rolls[1]
    assert target_shape(restored.block_dice) == target_shape(choice.block_dice)


@pytest.mark.parametrize('container', ('list', 'rlist', 'tuple'))
def test_empty_targets_for_every_action_type(tmp_path, container):
    from botbowl.core.forward_model import ReversibleList
    cls = {'list': list, 'rlist': ReversibleList, 'tuple': tuple}[container]
    game = logical(fresh())
    choices = [bb.ActionChoice(action, team=game.state.home_team, rolls=cls(())) for action in bb.ActionType]
    game.get_procedure().context = choices
    _, path, _, _ = saved_file(tmp_path, game)
    restored = clone_from_snapshot(io.read_snapshot(path)).get_procedure().context
    assert [c.action_type for c in restored] == list(bb.ActionType)
    assert all(type(c.rolls) is cls and not c.rolls for c in restored)


BAD_TARGETS = (
    [('MOVE', [[v]], []) for v in (1, 7, True, 2.0, '2', None, bb.Skill.DODGE, b'2')]
    + [('FOUL', [[v]], []) for v in (1, 13, True, 2.0, '2', None, bb.Skill.DODGE, b'2')]
    + [('STAB', [[v]], []) for v in (0, 13, True, 2.0, '2', None, bb.Skill.DODGE, b'2')]
    + [('STAB', [[v, 12]], []) for v in (1, 7, True, 2.0, '2', None, bb.Skill.DODGE, b'2')]
    + [('BLOCK', [], [v]) for v in (-4, -1, 0, 4, True, 2.0, '2', None, bb.Skill.DODGE, b'2')]
    + [('MOVE', [2], []), ('STAB', [2], []), ('STAB', [[]], []), ('END_TURN', [[2]], []),
       ('HANDOFF', [2, [2]], []), ('FOUL', [[2], 2], []), ('FOUL', [[[2]]], []),
       ('MOVE', {2}, []), ('MOVE', [{2}], []), ('MOVE', {'row': [2]}, []), ('MOVE', [], [1])]
)


@pytest.mark.parametrize('action,rolls,block_dice', BAD_TARGETS)
def test_resigned_target_domain_rejections_precede_decoder(tmp_path, monkeypatch, action, rolls, block_dice):
    game = logical(fresh())
    choice = bb.ActionChoice(getattr(bb.ActionType, action), team=game.state.home_team)
    game.get_procedure().context = choice
    _, path, doc, _ = saved_file(tmp_path, game)
    # Encode malformed but closed data without passing the writer's validation.
    # Attach only new reachable container nodes, preserving all old wire refs.
    encoder = io._Encoder(io.SnapshotLimits())
    encoder.nodes = doc['payload']['nodes']
    item = node(doc, 'model/ActionChoice')  # Context precedes the offered choices.
    context = dict(node(doc, 'procedure/StartGame')['fields'])['context']
    item = doc['payload']['nodes'][context['ref']]
    field(item, 'rolls', encoder.atom(rolls))
    field(item, 'block_dice', encoder.atom(block_dice))
    # Replacing old empty containers leaves otherwise unreachable wire nodes.
    # Compact them through reachability without constructing engine objects.
    doc = reachable_document(doc)
    expected = 'Nonempty block_dice requires BLOCK' if action != 'BLOCK' and block_dice else 'Invalid field domain: model/ActionChoice'
    assert_rejected(path, doc, game, monkeypatch, expected)


def reachable_document(doc):
    nodes = doc['payload']['nodes']
    reachable = set()
    def visit(value):
        if type(value) is dict:
            if set(value) == {'ref'}:
                index = value['ref']
                if index not in reachable:
                    reachable.add(index)
                    visit(nodes[index])
            else:
                for child in value.values():
                    visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)
    visit(doc['payload']['roots'])
    indices = {old: new for new, old in enumerate(sorted(reachable))}
    doc['payload']['nodes'] = [nodes[i] for i in sorted(reachable)]
    def rewrite(value):
        if type(value) is dict:
            if set(value) == {'ref'}:
                value['ref'] = indices[value['ref']]
            else:
                for child in value.values():
                    rewrite(child)
        elif type(value) is list:
            for child in value:
                rewrite(child)
    rewrite(doc['payload'])
    for i, item in enumerate(doc['payload']['nodes']):
        item['id'] = i
    return doc
