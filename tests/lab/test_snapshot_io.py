"""Cross-process executable continuation and adversarial SnapshotFileV1 input."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import botbowl as bb
from botbowl.core import procedure
from botbowl.lab import snapshot_io as io
from botbowl.lab.snapshots import (SnapshotError, capture_snapshot, clone_from_snapshot,
                                   restore_snapshot)
from tests.lab.snapshot_process import create
from tests.lab.test_snapshots import (CounterPolicy, RandomWrapper, assert_references,
                                      boundary, executable, logical, registry)
from tests.lab.test_timeline import fresh
from tests.lab.test_reproducibility import episode


def multiprocess(tmp_path, case, size=3, fm=False, side='home', scope='engine'):
    label = '-'.join(map(str, (case, size, int(fm), side, scope)))
    root = Path(os.environ.get('BOTBOWL_SNAPSHOT_EVIDENCE', tmp_path)) / label
    root.mkdir(parents=True, exist_ok=True)
    for mode in ('A', 'B'):
        with (root / (mode + '.log')).open('w') as log:
            result = subprocess.run([sys.executable, '-m', 'tests.lab.snapshot_process', mode,
                                     str(root), case, str(size), str(int(fm)), side, scope],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=60)
        assert result.returncode == 0, (root / (mode + '.log')).read_text()
    assert json.loads((root / 'A-trace.json').read_text()) == json.loads((root / 'B-trace.json').read_text())
    first, second = [json.loads((root / (mode + '-evidence.json')).read_text()) for mode in ('A', 'B')]
    assert first['pid'] != second['pid'] and os.getpid() not in (first['pid'], second['pid'])


@pytest.mark.parametrize('size', (1, 3, 5, 7, 11))
@pytest.mark.parametrize('case', ('fresh', 'setup', 'terminal'))
@pytest.mark.parametrize('fm', (False, True))
def test_process_a_to_b_five_sizes(tmp_path, size, case, fm):
    multiprocess(tmp_path, case, size, fm, side='away')


@pytest.mark.parametrize('case', ('reroll', 'push', 'interception', 'apothecary'))
@pytest.mark.parametrize('fm', (False, True))
@pytest.mark.parametrize('side', ('home', 'away'))
def test_process_a_to_b_pending_decisions(tmp_path, case, fm, side):
    multiprocess(tmp_path, case, fm=fm, side=side)


@pytest.mark.parametrize('fm', (False, True))
def test_process_a_to_b_suspended_route_and_rebuilt_paths(tmp_path, fm):
    multiprocess(tmp_path, 'route', fm=fm)


@pytest.mark.parametrize('size', (1, 3, 5, 7, 11))
@pytest.mark.parametrize('scope', ('engine', 'episode'))
def test_process_a_to_b_episode_policies_wrappers_and_five_streams(tmp_path, size, scope):
    multiprocess(tmp_path, 'episode', size, scope=scope)


def saved_file(tmp_path, game=None):
    game = logical(fresh()) if game is None else game
    path = tmp_path / 'snapshot.json'
    envelope = io.write_snapshot(path, capture_snapshot(game))
    return game, path, json.loads(path.read_text()), envelope


def reseal(path, document, semantic=False):
    if semantic:
        document['semantic_state_hash'] = io._semantic_hash(document['payload'], document['scope'])
    document['payload_digest'] = io._digest({k: v for k, v in document.items() if k != 'payload_digest'})
    path.write_bytes(io._json(document))


def node(document, tag):
    return next(n for n in document['payload']['nodes'] if n['type'] == tag)


def field(record, key, value):
    for pair in record['fields']:
        if pair[0] == key:
            pair[1] = value
            return
    raise AssertionError(key)


@pytest.mark.parametrize('damage', (
    'digest', 'enum', 'procedure', 'duplicate-id', 'dangling', 'negative-ref', 'bool-ref',
    'version', 'codec-version', 'ruleset', 'descriptor', 'unknown-field', 'duplicate-field',
    'missing-field', 'wrong-field-type', 'rng-index', 'rng-array', 'rng-flag', 'forced-roll',
    'square-index', 'player-alias', 'duplicate-key', 'unreachable', 'semantic',
    'array-shape', 'array-dtype', 'array-overflow', 'immutable-cycle', 'engine-components',
))
def test_corruption_rejected_before_materialization_and_preserves_target(tmp_path, monkeypatch, damage):
    game = logical(fresh())
    game.get_procedure().context = np.array([1, 2], dtype=np.uint32)
    game.dice.fix(bb.D6, 4)
    game, path, doc, _ = saved_file(tmp_path, game)
    before = executable(game)
    identities = tuple(id(v) for v in (game.state, game.dice, game.trajectory, game.timeline))
    if damage == 'digest':
        doc['provenance']['note'] = 'changed'
    elif damage == 'enum':
        field(node(doc, 'model/GameState'), 'weather', {'enum': ['WeatherType', 'UNKNOWN']})
    elif damage == 'procedure':
        node(doc, 'procedure/StartGame')['type'] = 'sentinel.module.Procedure'
    elif damage == 'duplicate-id':
        doc['payload']['nodes'][1]['id'] = 0
    elif damage in ('dangling', 'negative-ref', 'bool-ref'):
        field(node(doc, 'procedure/StartGame'), 'game', {'ref': {'dangling': 999999, 'negative-ref': -1, 'bool-ref': True}[damage]})
    elif damage == 'version':
        doc['version'] = 2
    elif damage == 'codec-version':
        doc['component_versions']['graph'] = True
    elif damage == 'ruleset':
        doc['descriptor']['ruleset_id'] = 'UNKNOWN'
    elif damage == 'descriptor':
        doc['descriptor']['engine_version'] = '999'
    elif damage in ('unknown-field', 'duplicate-field'):
        item = node(doc, 'procedure/StartGame')
        item['fields'].append(['__reduce__', 'sentinel.module'] if damage == 'unknown-field' else item['fields'][0])
    elif damage == 'missing-field':
        item = node(doc, 'procedure/StartGame')
        item['fields'] = [p for p in item['fields'] if p[0] != 'game']
    elif damage == 'wrong-field-type':
        field(node(doc, 'model/GameState'), 'pitch', {'ref': node(doc, 'procedure/StartGame')['id']})
    elif damage == 'rng-index':
        node(doc, 'rng')['position'] = 625
    elif damage == 'rng-array':
        node(doc, 'rng')['keys'][0] = 2**32
    elif damage == 'rng-flag':
        node(doc, 'rng')['has_gauss'] = True
    elif damage == 'forced-roll':
        node(doc, 'dice')['queues'][0][1] = [7]
    elif damage == 'square-index':
        field(node(doc, 'model/Square'), 'x', -10)
    elif damage == 'player-alias':
        field(node(doc, 'model/Player'), 'team', None)
    elif damage == 'duplicate-key':
        item = next(n for n in doc['payload']['nodes'] if n['type'] == 'dict' and n['items'])
        item['items'].append(item['items'][0])
    elif damage == 'unreachable':
        doc['payload']['nodes'].append({'id': len(doc['payload']['nodes']), 'type': 'list', 'items': []})
    elif damage == 'semantic':
        doc['semantic_state_hash'] = 'sha256:' + '0' * 64
    elif damage in ('array-shape', 'array-dtype', 'array-overflow'):
        item = next(n for n in doc['payload']['nodes'] if n['type'] == 'array' and n['dtype'] == '<u4')
        if damage == 'array-shape':
            item['shape'] = [2**40]
        elif damage == 'array-dtype':
            item['dtype'] = 'sentinel.module'
        else:
            item['items'][0] = 2**32
    elif damage == 'immutable-cycle':
        item = next(n for n in doc['payload']['nodes'] if n['type'] == 'tuple')
        item['items'].append({'ref': item['id']})
    else:
        doc['scope'] = 'episode'
    if damage == 'digest':
        path.write_bytes(io._json(doc))
    else:
        reseal(path, doc)
    def forbidden(*args, **kwargs):
        pytest.fail('Malformed file reached engine materialization')
    monkeypatch.setattr(io._Decoder, '__init__', forbidden)
    with pytest.raises(io.SnapshotFileError):
        restore_snapshot(game, io.read_snapshot(path))
    assert executable(game) == before
    assert tuple(id(v) for v in (game.state, game.dice, game.trajectory, game.timeline)) == identities


@pytest.mark.parametrize('raw', (b'{', b'\x80\x04N.', b'{"format":1,"format":2}', b'\xff', b'null',
                               b'{"x":NaN}', b'{"x":1e999}', b'[] trailing'))
def test_truncation_duplicate_json_keys_utf8_and_no_legacy_fallback(tmp_path, raw):
    path = tmp_path / 'bad.json'
    path.write_bytes(raw)
    with pytest.raises(io.SnapshotFileError):
        io.read_snapshot(path)


@pytest.mark.parametrize('limit', ('bytes', 'nodes', 'json-depth', 'graph-depth', 'array-bytes'))
def test_configurable_limits_before_materialization(tmp_path, monkeypatch, limit):
    _, path, doc, _ = saved_file(tmp_path)
    limits = io.SnapshotLimits()
    if limit == 'bytes':
        limits = replace(limits, max_bytes=path.stat().st_size - 1)
    elif limit == 'nodes':
        limits = replace(limits, max_nodes=5)
    elif limit == 'json-depth':
        path.write_text('[' * 40 + '0' + ']' * 40)
        limits = replace(limits, max_depth=20)
    elif limit == 'graph-depth':
        # Shallow JSON, deep reference chain, retained in Procedure.context.
        nodes = doc['payload']['nodes']
        start = len(nodes)
        for i in range(80):
            nodes.append({'id': start + i, 'type': 'list', 'items': [{'ref': start + i + 1}] if i < 79 else []})
        field(node(doc, 'procedure/StartGame'), 'context', {'ref': start})
        reseal(path, doc)
        limits = replace(limits, max_depth=64)
    else:
        item = next(n for n in doc['payload']['nodes'] if n['type'] == 'array')
        item['dtype'] = '<U999999999'
        reseal(path, doc)
    monkeypatch.setattr(io._Decoder, '__init__', lambda *a: pytest.fail('Limit checked too late'))
    with pytest.raises(io.SnapshotLimitError):
        io.read_snapshot(path, limits=limits)


def test_module_sentinels_are_inert_data_and_never_imported(tmp_path, monkeypatch):
    name = 'snapshot_sentinel_module'
    sentinel = tmp_path / (name + '.py')
    sentinel.write_text('raise AssertionError("sentinel executed")')
    monkeypatch.syspath_prepend(str(tmp_path))
    game = logical(fresh())
    game.get_procedure().context = {'module': name, 'callable': name + '.run', 'pickle': 'inert text'}
    _, path, doc, _ = saved_file(tmp_path, game)
    original = importlib.import_module
    def guarded(module, *args, **kwargs):
        assert name not in module
        return original(module, *args, **kwargs)
    monkeypatch.setattr(importlib, 'import_module', guarded)
    clone = clone_from_snapshot(io.read_snapshot(path))
    assert clone.get_procedure().context == game.get_procedure().context
    assert name not in sys.modules
    node(doc, 'procedure/StartGame')['type'] = name
    reseal(path, doc)
    with pytest.raises(io.SnapshotFileError):
        io.read_snapshot(path)
    assert name not in sys.modules


@pytest.mark.parametrize('failure', ('encode', 'write', 'flush', 'fsync', 'replace'))
def test_atomic_write_preserves_valid_destination_and_cleans_temp(tmp_path, monkeypatch, failure):
    game, path, _, _ = saved_file(tmp_path)
    before = path.read_bytes()
    saved = capture_snapshot(game)
    def fail(*args, **kwargs):
        raise OSError('injected write failure')
    if failure == 'encode':
        monkeypatch.setattr(io._Encoder, 'atom', fail)
    elif failure in ('fsync', 'replace'):
        monkeypatch.setattr(io.os, failure, fail)
    else:
        original = io.tempfile.NamedTemporaryFile
        def temporary(*args, **kwargs):
            stream = original(*args, **kwargs)
            setattr(stream, failure, fail)
            return stream
        monkeypatch.setattr(io.tempfile, 'NamedTemporaryFile', temporary)
    with pytest.raises(OSError):
        io.write_snapshot(path, saved)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['snapshot.json']


def test_restore_failure_is_atomic_after_read_including_late_adapter_failure(tmp_path):
    policy = CounterPolicy()
    context = episode(policies={'home': policy, 'away': RandomWrapper(policy)})
    logical(context.game)
    path = tmp_path / 'episode.json'
    io.write_snapshot(path, capture_snapshot(context, scope='episode', adapters=registry()), adapters=registry())
    saved = io.read_snapshot(path, adapters=registry())
    context.act()
    before = executable(context.game), context.decisions, context._policies['home'].count
    codecs = registry()
    def fail(*args):
        raise ValueError('late decode failure')
    kind, capture, _ = codecs._by_name['wrapper-v1']
    codecs._by_name['wrapper-v1'] = kind, capture, fail
    with pytest.raises(SnapshotError):
        restore_snapshot(context, saved, adapters=codecs)
    assert (executable(context.game), context.decisions, context._policies['home'].count) == before
    with pytest.raises(io.SnapshotIncompatibleError):
        io.read_snapshot(path)


def test_independent_loads_aliases_cycles_rng_arrays_queues_and_no_init(tmp_path, monkeypatch):
    game, _ = boundary('reroll', False, 'home')
    cycle = np.empty((2, 2), dtype=object)
    cycle[0, 0], cycle[0, 1] = game, cycle
    frozen = frozenset((game,))
    cycle[1, 0] = cycle[1, 1] = frozen
    game.get_procedure().context.context = {'a': cycle, 'b': cycle, 'key': {frozen: cycle},
                                            'rng': game.rng, 'dice': game.dice,
                                            'zero': np.array(game, dtype=object),
                                            'bytes': b'\x00\xff', 'ints': np.array([0, 255], dtype='u1')}
    # Three saved frames, including strict mode and cached Gaussian.
    game.rng.normal()
    with game.dice.force(d3=[1], d6=[6, 2], d8=[7], block_dice=[bb.BBDieResult.PUSH]):
        with game.dice.force(d3=[3], d6=[4, 5], d8=[8], block_dice=[bb.BBDieResult.BOTH_DOWN], strict=True):
            _, path, _, _ = saved_file(tmp_path, game)
    before = game.capture_rng_state()
    def forbidden(*args, **kwargs):
        pytest.fail('Engine constructor or rule step invoked during load')
    # SIM-02 constructs private DiceSource(0) during detached validation;
    # this consumes no source/global RNG and is not a file-selected constructor.
    monkeypatch.setattr(bb.Game, 'init', forbidden)
    monkeypatch.setattr(bb.Game, '__init__', forbidden)
    monkeypatch.setattr(procedure.Procedure, '__init__', forbidden)
    monkeypatch.setattr(bb.Clock, '__init__', forbidden)
    left, right = [clone_from_snapshot(io.read_snapshot(path)) for _ in range(2)]
    for clone in (left, right):
        root = clone.get_procedure()
        assert root.context.reroll is root
        context = root.context.context
        assert context['a'] is context['b'] is context['a'][0, 1]
        assert context['a'][0, 0] is context['zero'][()] is clone
        assert context['rng'] is clone.rng and context['dice'] is clone.dice
        assert next(iter(context['key'])) is context['a'][1, 0] is context['a'][1, 1]
        assert next(iter(context['a'][1, 0])) is clone
        assert len(clone.dice._queues) == 3 and clone.dice._strict[-1]
        assert_references(clone)
    left.get_procedure().context.context['a'][0, 0] = 'changed'
    assert right.get_procedure().context.context['a'][0, 0] is right
    left.rng.normal()
    bb.D6(left.dice)
    assert right.dice.pending(bb.D6) == (4, 5)
    assert game.capture_rng_state() == before


def test_payload_digest_semantic_hash_and_normalized_local_ids(tmp_path):
    game, path, first, envelope = saved_file(tmp_path)
    game.game_id = 'different local game ID'
    game.start_time = 123456
    game.timeline._context = replace(game.timeline.context, episode_id='other-episode', branch_id='other-branch')
    game.config.name = '/different/local/path.json'
    game.config.arena = '/different/local/arena.txt'
    game.home_agent.agent_id = 'different-seat-id'
    old = game.state.home_team.team_id
    game.state.home_team.team_id = 'different-team-id'
    for mapping in (game.state.team_by_id, game.state.dugouts):
        mapping['different-team-id'] = mapping.pop(old)
    for p in game.state.home_team.players:
        old = p.player_id
        p.player_id += '-different'
        game.state.player_by_id[p.player_id] = game.state.player_by_id.pop(old)
        game.state.team_by_player_id[p.player_id] = game.state.team_by_player_id.pop(old)
    # Observation bindings intentionally retain original IDs: regenerate the
    # derived binding after deliberately renaming the otherwise equivalent graph.
    from botbowl.lab.observations import ObservationControl
    game.timeline._entities = ObservationControl(game)
    second = io.write_snapshot(path, capture_snapshot(game), provenance={'path': '/tmp/B', 'timestamp': 123})
    assert envelope.payload_digest != second.payload_digest
    assert envelope.semantic_state_hash == second.semantic_state_hash
    game.rng.randint(20)
    third = io.write_snapshot(path, capture_snapshot(game))
    assert third.semantic_state_hash != second.semantic_state_hash
    # Reassign every graph ID while preserving traversal edges: wire identity
    # changes the digest, never normalized semantic identity.
    doc = deepcopy(first)
    nodes = doc['payload']['nodes']
    translate = {i: len(nodes) - 1 - i for i in range(len(nodes))}
    def remap(value):
        if type(value) is dict:
            if set(value) == {'ref'}:
                value['ref'] = translate[value['ref']]
            else:
                for item in value.values():
                    remap(item)
        elif type(value) is list:
            for item in value:
                remap(item)
    remap(doc['payload'])
    for record in nodes:
        record['id'] = translate[record['id']]
    nodes.reverse()
    assert io._semantic_hash(doc['payload'], doc['scope']) == envelope.semantic_state_hash
    reseal(path, doc)
    assert_references(clone_from_snapshot(io.read_snapshot(path)))


def test_codec_inventory_covers_every_engine_procedure_and_rejects_extensions(tmp_path):
    actual = {cls for cls in vars(procedure).values() if isinstance(cls, type) and
              cls.__module__ == procedure.__name__ and issubclass(cls, procedure.Procedure)}
    assert actual == {cls for tag, cls in io._CLASSES.items() if tag.startswith('procedure/')}
    class FutureProcedure(procedure.Procedure):
        pass
    game = logical(fresh())
    original = game.get_procedure()
    original.context = object.__new__(FutureProcedure)
    with pytest.raises(SnapshotError):
        io.write_snapshot(tmp_path / 'bad.json', capture_snapshot(game))
    assert not (tmp_path / 'bad.json').exists()


def test_rng_purity_capture_read_and_component_reference_validation(tmp_path, monkeypatch):
    context = episode(policies={'away': CounterPolicy()})
    logical(context.game)
    path = tmp_path / 'episode.json'
    local = context.game.capture_rng_state()
    global_rng = deepcopy(np.random.get_state())
    codecs = registry()
    io.write_snapshot(path, capture_snapshot(context, scope='episode', adapters=codecs), adapters=codecs)
    io.read_snapshot(path, adapters=codecs)
    assert context.game.capture_rng_state() == local
    after = np.random.get_state()
    assert global_rng[0] == after[0] and np.array_equal(global_rng[1], after[1]) and global_rng[2:] == after[2:]
    doc = json.loads(path.read_text())
    component = node(doc, 'ComponentState')
    field(component, 'state', doc['payload']['roots']['game'])
    reseal(path, doc)
    monkeypatch.setattr(io._Decoder, '__init__', lambda *a: pytest.fail('Component type checked too late'))
    with pytest.raises(io.SnapshotFileError, match='component data'):
        io.read_snapshot(path, adapters=codecs)


def test_mutable_container_cycle_semantic_alias_and_version_errors(tmp_path):
    game = logical(fresh())
    shared = [1, 2]
    context = {'left': shared, 'right': shared}
    context['cycle'] = context
    game.get_procedure().context = context
    _, path, doc, first = saved_file(tmp_path, game)
    clone = clone_from_snapshot(io.read_snapshot(path))
    assert clone.get_procedure().context['cycle'] is clone.get_procedure().context
    second = io.write_snapshot(path, capture_snapshot(clone))
    assert first.semantic_state_hash == second.semantic_state_hash
    context['right'] = [1, 2]
    third = io.write_snapshot(path, capture_snapshot(game))
    assert third.semantic_state_hash != first.semantic_state_hash
    doc['component_versions']['schema'] = 'future-schema'
    reseal(path, doc)
    with pytest.raises(io.SnapshotIncompatibleError):
        io.read_snapshot(path)


@pytest.mark.parametrize('fm', (False, True))
def test_restore_rebuilds_paths_trajectory_and_new_undo_origin(tmp_path, fm):
    game = create('route', 3, fm, 'home')
    _, path, _, _ = saved_file(tmp_path, game)
    saved = io.read_snapshot(path)
    restore_snapshot(game, saved)
    assert game.get_step() == 0
    game.advance(bb.Action(bb.ActionType.USE_REROLL))
    assert game.get_procedure().player.position == bb.Square(3, 5)
    assert game.get_procedure().paths
    if not fm:
        game.enable_forward_model()
    before = executable(game)
    checkpoint = game.capture_checkpoint()
    game.advance(bb.Action(bb.ActionType.END_PLAYER_TURN))
    after = executable(game)
    game.restore_checkpoint(checkpoint)
    assert executable(game) == before
    game.advance(bb.Action(bb.ActionType.END_PLAYER_TURN))
    assert executable(game) == after
    assert_references(game)


@pytest.mark.parametrize('case', ('foul', 'stab-block', 'stab-blitz'))
@pytest.mark.parametrize('fm', (False, True))
@pytest.mark.parametrize('side', ('home', 'away'))
def test_process_a_to_b_playable_armor_targets(tmp_path, case, fm, side):
    multiprocess(tmp_path, case, fm=fm, side=side)


@pytest.mark.parametrize('case', ('frenzy-stakes', 'path-handoff', 'path-foul'))
def test_process_a_to_b_frenzy_and_flat_pathfinder_targets(tmp_path, case):
    multiprocess(tmp_path, case)
