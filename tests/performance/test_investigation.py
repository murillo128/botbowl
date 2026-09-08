"""Deterministic instrument contracts; deliberately no wall-time thresholds."""
from copy import deepcopy
import importlib.util
import json
import pickle

import pytest

from botbowl.benchmarks import headless, prototypes
from botbowl.core.forward_model import Reversible, Trajectory, add_reversibility
from botbowl.core.model import Square


def test_fixture_rules_do_not_accumulate_or_inherit_loader_history():
    inherited = headless.botbowl.RuleSet('inherited-defaults')
    original_races = inherited.races[:]
    try:
        inherited.races.append(object())
        first, second = headless.fixture(7), headless.fixture(7)
        assert headless.state_json(first) == headless.state_json(second)
        for field, count in [('races', 24), ('star_players', 71), ('inducements', 8),
                             ('spp_actions', 5), ('spp_levels', 7), ('improvements', 6)]:
            assert len(getattr(first.ruleset, field)) == count
            assert len(getattr(second.ruleset, field)) == count
            assert getattr(first.ruleset, field) is not getattr(second.ruleset, field)
            assert getattr(first.ruleset, field) is not getattr(inherited, field)
        assert len(inherited.races) == len(original_races) + 1
    finally:
        inherited.races[:] = original_races


def test_operation_counts_and_stable_seeds(tmp_path):
    sample, frames = headless.run_sample('python', [7], 3, temp_root=tmp_path)
    plain, other = headless.run_sample('python', [7], 3, False, tmp_path)
    assert sample['traces'] == plain['traces']
    assert frames == other
    for key in ['policy', 'decision_step', 'decision_step_uninstrumented', 'deepcopy', 'checkpoint_capture',
                'checkpoint_restore', 'observation', 'observation_to_json',
                'state_to_json', 'json_encode', 'replay_record_step',
                'legal_actions_read', 'pathfinding_query']:
        assert sample['counts'][key] == 3
    assert sample['counts']['replay_write'] == sample['counts']['replay_pickle'] == 1
    assert sample['counts']['internal_one_step'] == 11
    assert sample['counts']['procedure_step'] == 11
    assert sample['counts']['legal_actions'] == 11
    assert list(tmp_path.iterdir()) == []
    changed, _ = headless.run_sample('python', [17], 3, False, tmp_path)
    assert changed['traces'] != plain['traces']


def test_report_is_finite_and_has_repeated_samples(tmp_path):
    report, _ = headless.benchmark([7], 1, 2, 0, ['python'], tmp_path)
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert report['semantic_identity'] is True
    assert report['corpus']['policy_seeds'] == [10007]
    data = report['backends']['python']
    assert data['peak_python_bytes'] > 0
    for metric in data['times_ns'].values():
        assert len(metric['samples']) == 2
        assert metric['min'] <= metric['median'] <= metric['max']
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('args', [dict(decisions=0), dict(repetitions=1),
                                 dict(warmup=-1), dict(seeds=[]), dict(seeds=[7, 7])])
def test_invalid_benchmark_parameters(args):
    with pytest.raises(ValueError):
        headless.benchmark(**args)


def test_cleanup_and_instrumentation_restoration_after_failure(tmp_path, monkeypatch):
    original_step = headless.Game._one_step
    original_pathfinder = headless.procedure.Pathfinder

    def fail(*args, **kwargs):
        raise OSError('injected write failure')

    monkeypatch.setattr(headless, 'write_bytes', fail)
    with pytest.raises(OSError, match='injected'):
        headless.run_sample('python', [7], 1, temp_root=tmp_path)
    assert headless.Game._one_step is original_step
    assert headless.procedure.Pathfinder is original_pathfinder
    assert list(tmp_path.iterdir()) == []


def test_internal_operation_counter_counts_automatic_steps():
    game = headless.fixture(7)
    meter = headless.Meter()
    import random
    action = headless.choose(game, random.Random(10007))
    with headless.instrumentation(meter):
        meter.call('decision_step', game.step, action)
    assert meter.counts['decision_step'] == 1
    assert meter.counts['internal_one_step'] > 1  # Place ball drives automatic kickoff work.


@pytest.mark.parametrize('name', ['python', 'native'])
def test_read_measurements_preserve_results_and_live_state(name):
    if name == 'native':
        pytest.importorskip('botbowl.core.pathfinding.cython_pathfinding')
    game = headless.fixture(7)
    control = headless.ObservationControl(game)
    before = headless.state_json(game)
    rng_before = pickle.dumps(game.capture_rng_state())
    player = sorted(game.get_players_on_pitch(), key=lambda p: p.player_id)[0]
    identities = (id(player), id(player.state), id(player.position))
    meter = headless.Meter()
    with headless.backend(name) as module:
        expected = module.get_all_paths(game, player)
        actual = meter.call('query', module.get_all_paths, game, player)
        assert len(actual) == len(expected)
        assert actual == expected  # Path.__eq__ includes steps, rolls, probabilities and terminal metadata.
    expected = headless.observe(game, control, 'home').to_json()
    actual = meter.call('observation', headless.observe, game, control, 'home').to_json()
    assert actual == expected
    assert headless.state_json(game) == before
    assert pickle.dumps(game.capture_rng_state()) == rng_before
    assert identities == (id(player), id(player.state), id(player.position))


@pytest.mark.skipif(importlib.util.find_spec('botbowl.core.pathfinding.cython_pathfinding') is None,
                    reason='Explicit native extension absent')
def test_native_and_python_same_corpus(tmp_path):
    python, frames = headless.run_sample('python', [7, 17], 4, temp_root=tmp_path)
    native, other = headless.run_sample('native', [7, 17], 4, temp_root=tmp_path)
    # Inputs match exactly; inherited equal-cost path choices may differ in outputs.
    assert python['counts'] == native['counts']
    assert frames == other
    plain_native, _ = headless.run_sample('native', [7, 17], 4, False, tmp_path)
    assert native['traces'] == plain_native['traces']


@pytest.mark.parametrize('cls', [Square, prototypes.SlotSquare, prototypes.DataSquare])
def test_square_identity_copy_hash_json_and_reversible(cls):
    with prototypes.square_variant(cls):
        square = cls(1, 2, False)
        equal = cls(1, 2, True)
        assert square == equal and hash(square) == hash(equal) == 33
        assert square.to_json() == {'x': 1, 'y': 2}
        assert square.distance(cls(4, 3)) == 3
        assert deepcopy(square) == square and deepcopy(square) is not square
        assert pickle.loads(pickle.dumps(square)) == square
        copied = deepcopy([square, square])
        assert copied[0] is copied[1] and copied[0] is not square
        with pytest.raises(AttributeError):
            square.x = 3
        trajectory = Trajectory()
        trajectory.enabled = True
        owner = Reversible()
        owner.position = square
        owner.set_trajectory(trajectory)
        assert add_reversibility(square, trajectory) is square
        owner.position = cls(2, 3)
        undone = trajectory.revert(0)
        assert owner.position is square
        trajectory.step_forward(undone)
        assert owner.position == cls(2, 3)


@pytest.mark.parametrize('cls', [prototypes.SlotSquare, prototypes.DataSquare])
def test_square_prototype_checkpoint_and_decision_identity(cls, tmp_path):
    reference, frames = headless.run_sample('python', [7], 4, temp_root=tmp_path)
    with prototypes.square_variant(cls):
        result, other = headless.run_sample('python', [7], 4, temp_root=tmp_path)
    assert result['traces'] == reference['traces']
    assert other == frames
    from botbowl.core import model
    assert model.Square is Square


@pytest.mark.parametrize('interval', [1, 2, 4, 16, 64])
def test_delta_roundtrip_seek_and_no_aliasing(interval):
    frames = [{'a': [1, 2], 'b': {'x': 3}, 'gone': None},
              {'a': [], 'b': {'new': 'x'}}, {}, {'new': [None, True]}]
    original = deepcopy(frames)
    records = prototypes.encode_frames(frames, interval)
    restored = prototypes.decode_frames(records)
    assert restored == frames
    assert [prototypes.seek_frame(records, i) for i in range(len(frames))] == frames
    assert prototypes.decode_frames(json.loads(json.dumps(records))) == frames
    assert prototypes.decode_frames(pickle.loads(pickle.dumps(records))) == frames
    restored[0]['a'].append(9)
    assert frames == original


def test_replay_prototype_existing_frames_and_cleanup(tmp_path):
    _, frames = headless.run_sample('python', [7], 2, False, tmp_path)
    report = prototypes.replay_report(frames, 2, tmp_path)
    assert report['semantic_identity'] is True
    assert report['frame_count'] == 2
    assert list(tmp_path.iterdir()) == []
    replays = headless.legacy_replays([7], 2)
    report = prototypes.legacy_replay_report(replays, 2, tmp_path)
    assert report['legacy']['bytes'] > 0
    assert list(tmp_path.iterdir()) == []


def test_web_host_and_temporary_storage_restored(tmp_path):
    pytest.importorskip('flask')
    from botbowl.web import api
    from botbowl.benchmarks.web import local_server
    original = api.host
    game = headless.fixture(7)
    with pytest.raises(RuntimeError, match='injected'):
        with local_server(game, tmp_path) as (app, url):
            assert api.host is not original
            assert url.startswith('http://127.0.0.1:')
            assert app.test_client().get('/games/' + game.game_id).status_code == 200
            raise RuntimeError('injected')
    assert api.host is original
    assert list(tmp_path.iterdir()) == []
