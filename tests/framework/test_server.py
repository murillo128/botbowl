"""Local HTTP boundaries, trusted storage and deterministic replay pagination."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import pickle
import threading

import pytest

pytest.importorskip('flask')
import botbowl as bb
from botbowl.ai.bots.illegal_action_bot import IllegalActionBot
from botbowl.ai.registry import registry
from botbowl.web import api, server
from botbowl.web.errors import WebError
from botbowl.web.host import InMemoryHost
from tests.baseline import progress_action, semantic_reports
from tests.util import get_custom_game_turn, get_game_coin_toss


@pytest.fixture
def host(tmp_path, monkeypatch):
    host = InMemoryHost(tmp_path / 'saves', tmp_path / 'replays', replay_cache_size=2)
    monkeypatch.setattr(api, 'host', host)
    monkeypatch.setitem(server.app.config, 'TESTING', True)
    return host


@pytest.fixture
def client(host):
    return server.app.test_client()


@pytest.fixture
def game(host):
    game, _ = get_custom_game_turn([(3, 3)], [(5, 3)])
    game.game_id = 'test-game'
    game.replay = bb.Replay('test-game')
    game.dice.fix(bb.D6, 4, 6)
    game.rng.normal()  # Exercise the cached Gaussian state as well as MT keys.
    host.add_game(game)
    return game


def assert_error(response, status, code=None):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.mimetype == 'application/json'
    assert set(response.json) == {'error'}
    assert set(response.json['error']) == {'code', 'message'}
    assert response.json['error']['message']
    if code is not None:
        assert response.json['error']['code'] == code


def act(client, game, action):
    return client.post('/games/' + game.game_id + '/act', json={'action': action})


def save(client, game, name='round-one'):
    return client.post('/game/save', json={'game_id': game.game_id, 'name': name})


def test_server(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'static/dist/js/botbowl.js' in response.data
    assert client.get('/static/dist/js/botbowl.js').status_code == 200


@pytest.mark.parametrize('mode', [None, 'standard', 'Standard', '7v7', '5v5', '3v3', '1v1'])
def test_create_modes_and_default(client, host, mode):
    teams = client.get('/teams/' + (mode or 'standard')).json
    data = {'game': {'home_team_name': teams[0]['name'], 'away_team_name': teams[0]['name']}}
    if mode is not None:
        data['mode'] = mode
    response = client.put('/game/create', json=data)
    assert response.status_code == 200
    assert response.mimetype == 'application/json'
    game = host.get_game(response.json['game_id'])
    assert game.state.home_team.team_id != game.state.away_team.team_id
    assert game.config.pitch_max == (11 if mode in (None, 'standard', 'Standard') else int(mode[0]))


def test_api_default_mode(host):
    name = api.get_teams()[0].name
    game = api.new_game(name, name, bb.Agent('away', human=True), bb.Agent('home', human=True))
    assert game.config.pitch_max == 11


@pytest.mark.parametrize('mode', ['', 'foo', '../web-11', 3, None, []])
def test_invalid_modes(client, host, mode):
    assert_error(client.put('/game/create', json={'mode': mode, 'game': {}}), 400)
    assert not host.games


@pytest.mark.parametrize('patch', [
    {'home_team_name': '../human'}, {'away_team_name': 'missing'},
    {'home_player': 'missing-bot'}, {'away_player': 12}, {'home_team_name': []},
])
def test_invalid_create_settings(client, host, patch):
    name = api.get_teams('1v1')[0].name
    settings = {'home_team_name': name, 'away_team_name': name, **patch}
    assert_error(client.put('/game/create', json={'mode': '1v1', 'game': settings}), 400)
    assert not host.games


@pytest.mark.parametrize('path', ['/game/create', '/game/save', '/games/missing/act'])
@pytest.mark.parametrize('payload', ['{', 'null', '[]', '"text"'])
def test_malformed_json(client, path, payload):
    method = client.put if path == '/game/create' else client.post
    assert_error(method(path, data=payload, content_type='application/json'), 400)


@pytest.mark.parametrize('method,path,body', [
    ('get', '/games/missing', None), ('post', '/games/missing/act', {'action': None}),
    ('post', '/games/missing/update', None), ('delete', '/game/missing/delete', None),
    ('post', '/game/load/missing/', None), ('delete', '/save/missing/delete', None),
    ('get', '/replays/missing', None), ('get', '/steps/missing/0/10', None),
    ('post', '/game/save', {'game_id': 'missing', 'name': 'valid-name'}),
    ('get', '/unknown-route', None),
])
def test_missing_resources(client, method, path, body):
    assert_error(getattr(client, method)(path, json=body), 404)


@pytest.mark.parametrize('action,status', [
    ({'action_type': 'MOVE', 'position': {'x': -1, 'y': 3}}, 409),
    ({'action_type': 'MOVE', 'position': {'x': 999, 'y': 3}}, 409),
    ({'action_type': 'CONTINUE'}, 409), (None, 409),
    ({'action_type': 'START_MOVE', 'player_id': 'missing'}, 409),
    ({'action_type': 'START_MOVE', 'player_id': 3}, 400),
    ({'action_type': 'BOGUS'}, 400), ({}, 400), ([], 400),
    ({'action_type': 'MOVE', 'position': {'x': True, 'y': 3}}, 400),
    ({'action_type': 'MOVE', 'position': {'x': 3.0, 'y': 3}}, 400),
    ({'action_type': 'MOVE', 'position': []}, 400),
])
def test_rejected_action_preserves_entire_game(client, game, action, status):
    assert act(client, game, {'action_type': 'START_MOVE', 'position': {'x': 3, 'y': 3}}).status_code == 200
    game.enable_forward_model()
    before, prior_action = pickle.dumps(game), game.action
    assert_error(act(client, game, action), status)
    assert pickle.dumps(game) == before
    assert game.action is prior_action


def test_repeated_observation_never_refreshes(client, game, monkeypatch):
    game.add_primary_clock(game.active_team)
    game.state.clocks[0]._started_at -= 1000  # Expired; GET must not enforce it.
    game.config.competition_mode = True
    assert act(client, game, {'action_type': 'START_MOVE', 'position': {'x': 3, 'y': 3}}).status_code == 200
    before = pickle.dumps(game)
    def forbidden(*args):
        pytest.fail('Observation must not call refresh/step')
    monkeypatch.setattr(bb.Game, 'refresh', forbidden)
    monkeypatch.setattr(bb.Game, 'step', forbidden)
    second = server.app.test_client()
    for viewer in (client, second, client, second):
        assert viewer.get('/games/' + game.game_id).status_code == 200
        assert viewer.get('/games/').status_code == 200
        assert pickle.dumps(game) == before


def test_explicit_update_enforces_expired_clock(client, game):
    game.add_primary_clock(game.active_team)
    game.state.clocks[0]._started_at -= 1000
    game.config.competition_mode = True
    before = game.active_team.team_id, len(game.state.reports)
    response = client.post('/games/' + game.game_id + '/update', json={})
    assert response.status_code == 200
    assert (game.active_team.team_id, len(game.state.reports)) != before


def test_explicit_update_advances_automatic_step(client, host):
    game = get_game_coin_toss()
    game.game_id = 'slow-game'
    game.config.fast_mode = False
    host.add_game(game)
    assert act(client, game, {'action_type': 'START_GAME'}).status_code == 200
    assert not game.state.available_actions
    before = pickle.dumps(game)
    assert client.get('/games/slow-game').status_code == 200
    assert pickle.dumps(game) == before
    assert client.post('/games/slow-game/update').status_code == 200
    assert pickle.dumps(game) != before


def test_two_clients_serialize_decisions(client, game):
    barrier = threading.Barrier(2)
    def request_action():
        with server.app.test_client() as other:
            barrier.wait(timeout=5)
            return act(other, game, {'action_type': 'START_MOVE', 'position': {'x': 3, 'y': 3}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: request_action(), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    accepted = next(response.json for response in responses if response.status_code == 200)
    before = pickle.dumps(game)
    observed = client.get('/games/' + game.game_id).json
    assert pickle.dumps(game) == before
    # Clock JSON measures elapsed monotonic time; the underlying clocks stay intact.
    accepted['state']['clocks'] = observed['state']['clocks'] = None
    assert observed == accepted
    assert game.action.action_type == bb.ActionType.START_MOVE


def test_internal_error_is_json_failure(client, game, monkeypatch):
    def fail(*args):
        raise RuntimeError('private diagnostic')
    monkeypatch.setattr(api, 'step', fail)
    response = act(client, game, {'action_type': 'START_MOVE', 'position': {'x': 3, 'y': 3}})
    assert_error(response, 500, 'internal_error')
    assert b'private diagnostic' not in response.data


@pytest.fixture
def slow_bot_game(client, host, monkeypatch):
    monkeypatch.setitem(registry.bots, 'web-test-illegal', IllegalActionBot)
    # Keep elapsed clock JSON comparable between the acting and observing clients.
    monkeypatch.setattr('botbowl.core.model.time.monotonic', lambda: 10000.0)

    def create(bot_side):
        name = client.get('/teams/1v1').json[0]['name']
        response = client.put('/game/create', json={
            'mode': '1v1', 'game': {'home_team_name': name, 'away_team_name': name,
                                    bot_side: 'web-test-illegal'}})
        assert response.status_code == 200
        game = host.get_game(response.json['game_id'])
        assert game.config.fast_mode is False
        assert game.config.competition_mode is True
        return game

    return create


def test_valid_client_action_then_bot_failure_is_internal(client, slow_bot_game):
    game = slow_bot_game('home_player')
    observer = server.app.test_client()
    for _ in range(10):
        if any(choice.action_type == bb.ActionType.HEADS for choice in game.state.available_actions):
            break
        if game.actor is not None and game.actor.human:
            response = act(client, game, progress_action(game).to_json())
        else:
            response = client.post('/games/' + game.game_id + '/update', json={})
        assert response.status_code == 200
    assert game.actor.human
    assert game.validate_action(bb.Action(bb.ActionType.HEADS)).allowed
    game.set_seed(1)  # HEADS loses to the home bot, whose next action is invalid.
    game.home_agent.i = 1

    before, rng_before = pickle.dumps(game), game.capture_rng_state()
    reports_before = len(game.state.reports)
    observed_before = observer.get('/games/' + game.game_id).json
    assert_error(act(client, game, {'action_type': 'USE_APOTHECARY'}), 409, 'action_not_available')
    assert observer.get('/games/' + game.game_id).json == observed_before
    assert pickle.dumps(game) == before
    assert game.capture_rng_state() == rng_before

    response = act(client, game, {'action_type': 'HEADS'})
    assert_error(response, 500, 'internal_error')
    assert response.json['error']['message'] == 'Internal server error.'
    assert len(game.state.reports) == reports_before + 1
    assert game.capture_rng_state() != rng_before
    after = pickle.dumps(game)
    assert after != before
    observed = observer.get('/games/' + game.game_id)
    assert observed.status_code == 200
    assert observed.json == api.game_to_json(game)
    assert len(observed.json['state']['reports']) == reports_before + 1
    assert pickle.dumps(game) == after


def test_explicit_update_bot_failure_is_internal(client, slow_bot_game):
    game = slow_bot_game('away_player')
    observer = server.app.test_client()
    game.set_seed(87)
    game.away_agent.rnd.seed(101)
    for _ in range(2):
        assert client.post('/games/' + game.game_id + '/update', json={}).status_code == 200

    before, rng_before = pickle.dumps(game), game.capture_rng_state()
    reports_before = len(game.state.reports)
    assert type(game.state.stack.peek()).__name__ == 'Fans'
    response = client.post('/games/' + game.game_id + '/update', json={})
    assert_error(response, 500, 'internal_error')
    assert response.json['error']['message'] == 'Internal server error.'
    assert len(game.state.reports) > reports_before
    assert game.capture_rng_state() != rng_before
    assert type(game.state.stack.peek()).__name__ == 'CoinTossKickReceive'
    after = pickle.dumps(game)
    assert after != before
    observed = observer.get('/games/' + game.game_id)
    assert observed.status_code == 200
    assert observed.json == api.game_to_json(game)
    assert len(observed.json['state']['reports']) == len(game.state.reports)
    assert pickle.dumps(game) == after


def test_save_load_duplicate_delete(client, host, game):
    before = pickle.dumps(game)
    assert save(client, game, 'Round One').status_code == 200
    assert pickle.dumps(game) == before
    original = (host.save_dir / 'round one.bb').read_bytes()
    assert api.save_game_exists('ROUND ONE')
    assert_error(save(client, game, 'ROUND ONE'), 409)
    assert (host.save_dir / 'round one.bb').read_bytes() == original
    response = client.post('/game/load/Round One/')
    assert response.status_code == 200
    loaded_id = response.json['game_id']
    assert loaded_id != game.game_id
    assert host.get_game(loaded_id).capture_rng_state() == game.capture_rng_state()
    assert client.get('/games/').json['saved_games'][0]['name'] == 'round one'
    assert client.delete('/game/' + loaded_id + '/delete').status_code == 200
    assert_error(client.get('/games/' + loaded_id), 404)
    assert client.delete('/save/ROUND ONE/delete').status_code == 200
    assert not api.save_game_exists('Round One')
    assert_error(client.post('/game/load/round one'), 404)
    assert_error(client.delete('/save/round one/delete'), 404)


def test_load_is_explicit_and_never_reseeds(client, game, monkeypatch):
    assert save(client, game).status_code == 200
    before = pickle.dumps(game)
    assert_error(client.get('/game/load/round-one/'), 405)
    def forbidden(*args):
        pytest.fail('Restore must not reseed')
    monkeypatch.setattr(bb.Game, 'set_seed', forbidden)
    assert client.post('/game/load/round-one').status_code == 200
    assert pickle.dumps(game) == before


def test_save_load_continuation_actions_and_randomness(client, host, monkeypatch):
    game = get_game_coin_toss(seed=2026)
    game.game_id = 'continuation'
    game.config.kick_off_table = False
    game.config.pathfinding_enabled = False
    game.rng.normal()
    game.dice.fix(bb.D6, 4, 5)
    host.add_game(game)
    assert save(client, game).status_code == 200
    loaded = host.get_game(client.post('/game/load/round-one').json['game_id'])
    assert game.capture_rng_state() == loaded.capture_rng_state()
    initial_rng = game.capture_rng_state()
    for tick in range(64):
        # Equal monotonic time for both continuations; compare semantic game state,
        # reports, all natural/forced RNG and decisions after every HTTP action.
        monkeypatch.setattr('botbowl.core.model.time.monotonic', lambda: 100000.0 + tick)
        action = progress_action(game).to_json()
        left = act(client, game, action)
        right = act(client, loaded, action)
        assert left.status_code == right.status_code == 200
        assert game.state.to_json(ignore_clocks=True) == loaded.state.to_json(ignore_clocks=True)
        assert semantic_reports(game) == semantic_reports(loaded)
        assert game.capture_rng_state() == loaded.capture_rng_state()
        assert game.action.to_json() == loaded.action.to_json() if game.action is not None else loaded.action is None
        if game.state.game_over:
            break
    assert tick > 10
    assert game.capture_rng_state() != initial_rng
    assert game.rng.normal(size=12).tolist() == loaded.rng.normal(size=12).tolist()
    assert [game.dice.roll(bb.D6) for _ in range(12)] == [loaded.dice.roll(bb.D6) for _ in range(12)]


def test_save_preserves_running_and_paused_clocks(client, host, game, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr('botbowl.core.model.time.monotonic', lambda: now[0])
    game.add_primary_clock(game.active_team)
    now[0] += 10
    game.add_secondary_clock(game.active_team)
    now[0] += 2
    before = pickle.dumps(game)
    assert save(client, game).status_code == 200
    assert pickle.dumps(game) == before
    now[0] += 500
    loaded = host.get_game(client.post('/game/load/round-one').json['game_id'])
    assert [clock.is_running() for clock in loaded.state.clocks] == [False, True]
    assert [clock.get_running_time() for clock in loaded.state.clocks] == [10, 2]


@pytest.mark.parametrize('name', ['../bad', '/tmp/bad', 'a/b/c', '..', 'a.b', 'ab', 'x' * 40,
                                       ' aab', 'aab ', 'a\\b', 'café', '\x00abc'])
def test_invalid_names_before_filesystem(client, host, game, monkeypatch, name):
    def forbidden(*args):
        pytest.fail('Invalid name touched filesystem')
    monkeypatch.setattr(host, '_files', forbidden)
    assert_error(save(client, game, name), 400)
    for operation in (host.load_game, host.delete_saved_game, host.save_game_exists):
        with pytest.raises(WebError) as exc:
            operation(name)
        assert exc.value.status == 400


@pytest.mark.parametrize('route', ['/replays/..', '/steps/../0/10', '/replays/a%5Cb',
                                   '/game/load/a.b', '/save/a.b/delete'])
def test_invalid_path_routes(client, route):
    method = client.post if route.startswith('/game/load') else client.delete if route.endswith('/delete') else client.get
    assert_error(method(route), 400)


def test_legacy_case_collision_cannot_overwrite_or_choose(client, host, game):
    assert save(client, game, 'Round One').status_code == 200
    path = host.save_dir / 'round one.bb'
    path.rename(host.save_dir / 'Round One.bb')
    assert client.post('/game/load/ROUND ONE').status_code == 200
    assert_error(save(client, game, 'round one'), 409)
    path.write_bytes((host.save_dir / 'Round One.bb').read_bytes())
    assert_error(client.post('/game/load/round one'), 409)
    assert_error(client.get('/games/'), 409)
    assert_error(client.delete('/save/round one/delete'), 409)


@pytest.mark.parametrize('failure', ['mkdir', 'dump', 'fsync', 'link'])
def test_save_fault_preserves_live_game_and_no_partial_file(client, host, game, monkeypatch, failure):
    before = pickle.dumps(game)
    def fail(*args, **kwargs):
        raise OSError('injected filesystem fault')
    target, name = (Path, 'mkdir') if failure == 'mkdir' else (pickle, 'dump') if failure == 'dump' else (__import__('os'), failure)
    monkeypatch.setattr(target, name, fail)
    assert_error(save(client, game), 500, 'storage_error')
    assert pickle.dumps(game) == before
    assert not list(host.save_dir.glob('*'))


def test_atomic_publication_and_duplicate_race(client, host, game, monkeypatch):
    import os
    real_link = os.link
    def inspect(source, destination):
        assert not Path(destination).exists()
        stored = pickle.loads(Path(source).read_bytes())
        assert stored.capture_rng_state() == game.capture_rng_state()
        return real_link(source, destination)
    monkeypatch.setattr(os, 'link', inspect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        def request_save(_):
            with server.app.test_client() as other:
                return save(other, game, 'same-name').status_code
        assert sorted(pool.map(request_save, range(2))) == [200, 409]
    assert len(list(host.save_dir.iterdir())) == 1


@pytest.mark.parametrize('contents', [b'not a pickle', b'\x80\x04', pickle.dumps({'wrong': 'type'})])
def test_corrupt_save_fails_without_registration(client, host, game, contents):
    host.save_dir.mkdir()
    (host.save_dir / 'corrupt.bb').write_bytes(contents)
    before = pickle.dumps(game), list(host.games)
    assert_error(client.post('/game/load/corrupt'), 500, 'storage_error')
    assert_error(client.get('/games/'), 500, 'storage_error')
    assert (pickle.dumps(game), list(host.games)) == before


def test_trusted_legacy_game_load(client, host, game):
    host.save_dir.mkdir()
    (host.save_dir / 'legacy.bb').write_bytes(pickle.dumps(game))
    response = client.post('/game/load/legacy')
    assert response.status_code == 200
    loaded = host.get_game(response.json['game_id'])
    assert loaded.capture_rng_state() == game.capture_rng_state()
    assert loaded.state.to_json(ignore_clocks=True) == game.state.to_json(ignore_clocks=True)


def test_symlinks_and_nonregular_files_are_rejected(client, host, game, tmp_path):
    host.save_dir.mkdir()
    outside = tmp_path / 'outside.bb'
    outside.write_bytes(pickle.dumps(game))
    (host.save_dir / 'linked.bb').symlink_to(outside)
    assert_error(client.post('/game/load/linked'), 400)
    assert_error(client.delete('/save/linked/delete'), 400)
    assert_error(save(client, game, 'linked'), 409)
    assert outside.exists()
    (host.save_dir / 'folder.bb').mkdir()
    assert_error(client.post('/game/load/folder'), 400)


def write_replay(host, name, indices=(0, 2, 7)):
    replay = bb.Replay('internal-id')
    replay.steps = {i: bb.ReplayStep({'state': {'reports': []}, 'frame': i}, 0) for i in reversed(indices)}
    host.replay_dir.mkdir(exist_ok=True)
    (host.replay_dir / (name + '.rep')).write_bytes(pickle.dumps(replay))
    return replay


def test_replay_paging_before_preload_boundaries_and_clients(client, host):
    write_replay(host, 'example')
    second = server.app.test_client()
    assert client.get('/steps/example/1/1').json == {'2': {'frame': 2, 'state': {'reports': []}}}
    assert list(second.get('/steps/example/0/2').json) == ['0', '2']
    assert list(client.get('/steps/example/2/100').json) == ['7']
    assert client.get('/steps/example/3/1').json == {}
    assert second.get('/steps/example/999999999999/100').json == {}
    response = second.get('/replays/example')
    assert response.status_code == 200
    assert response.json['replay_id'] == 'example'
    assert list(response.json['steps']) == ['0', '2', '7']
    returned = api.get_replay('example')
    returned.steps[0].game['frame'] = -1
    assert client.get('/steps/example/0/1').json['0']['frame'] == 0


def test_replay_default_page_bound_and_empty(client, host):
    write_replay(host, 'long', tuple(range(0, 205, 2)))
    assert len(client.get('/replays/long').json['steps']) == 100
    assert len(client.get('/steps/long/100/100').json) == 3
    write_replay(host, 'empty', ())
    assert client.get('/steps/empty/0/100').json == {}
    assert client.get('/replays/empty').json == {'replay_id': 'empty', 'steps': {}, 'actions': {}}


@pytest.mark.parametrize('offset,count', [('-1', '1'), ('0', '0'), ('0', '-1'), ('0', '101'),
                                         ('a', '1'), ('0', '1.5'), ('0', 'True')])
def test_invalid_replay_limits_before_access(client, host, monkeypatch, offset, count):
    def forbidden(*args):
        pytest.fail('Invalid page accessed replay')
    monkeypatch.setattr(host, '_get_replay', forbidden)
    assert_error(client.get('/steps/missing/' + offset + '/' + count), 400)


def test_replay_lru_eviction_retains_persistence(client, host):
    for name in ('one', 'two', 'three'):
        write_replay(host, name)
    first = client.get('/steps/one/0/100').json
    client.get('/replays/two')
    client.get('/steps/one/0/1')  # Touch one so two is least recently used.
    client.get('/steps/three/0/1')
    assert list(host._replays) == ['one', 'three']
    assert len(list(host.replay_dir.iterdir())) == 3
    assert client.get('/steps/two/0/100').json == first
    assert len(host._replays) == 2
    assert client.get('/steps/one/0/100').json == first


def test_replay_cache_detects_replacement_and_deletion(client, host):
    write_replay(host, 'example')
    client.get('/replays/example')
    write_replay(host, 'example', (10, 20))
    assert set(client.get('/steps/example/0/100').json) == {'10', '20'}
    (host.replay_dir / 'example.rep').unlink()
    assert_error(client.get('/steps/example/0/100'), 404)
    assert 'example' not in host._replays


@pytest.mark.parametrize('contents', [b'garbage', pickle.dumps([])])
def test_corrupt_replay(client, host, contents):
    host.replay_dir.mkdir()
    (host.replay_dir / 'corrupt.rep').write_bytes(contents)
    assert_error(client.get('/steps/corrupt/0/1'), 500, 'storage_error')
    assert not host._replays


def test_no_pickle_upload_endpoint(client, host):
    assert_error(client.post('/game/load/upload', data=pickle.dumps(bb.Replay('upload'))), 400)
    assert not host.games
    assert not host.save_dir.exists()


@pytest.mark.parametrize('path', ['/games/test-game/update', '/game/load/round-one'])
@pytest.mark.parametrize('payload', ['{', 'null', '{"seed": 1}', '{"filename": "/tmp/untrusted"}'])
def test_update_and_restore_reject_parameters_without_mutation(client, game, path, payload):
    before = pickle.dumps(game)
    assert_error(client.post(path, data=payload), 400)
    assert pickle.dumps(game) == before


def test_read_and_delete_filesystem_faults(client, host, game, monkeypatch):
    import os
    assert save(client, game).status_code == 200
    before = pickle.dumps(game), list(host.games)
    def fail(*args, **kwargs):
        raise PermissionError('injected permission denial')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', fail)
        assert_error(client.post('/game/load/round-one'), 500, 'storage_error')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail)
        assert_error(client.delete('/save/round-one/delete'), 500, 'storage_error')
    assert (host.save_dir / 'round-one.bb').exists()
    assert (pickle.dumps(game), list(host.games)) == before


def test_legacy_game_without_owned_rng_is_rejected(client, host, game):
    host.save_dir.mkdir()
    legacy = deepcopy(game)
    del legacy.dice
    (host.save_dir / 'legacy.bb').write_bytes(pickle.dumps(legacy))
    before = list(host.games)
    assert_error(client.post('/game/load/legacy'), 500, 'storage_error')
    assert list(host.games) == before


def test_replay_reports_hydration_does_not_depend_on_pages(client, host):
    replay = write_replay(host, 'reports')
    replay.reports = [bb.Outcome(bb.OutcomeType.TURN_START), bb.Outcome(bb.OutcomeType.END_OF_TURN)]
    replay.steps[2].num_reports = 1
    replay.steps[7].num_reports = 2
    (host.replay_dir / 'reports.rep').write_bytes(pickle.dumps(replay))
    page = client.get('/steps/reports/1/1').json['2']
    assert page['state']['reports'] == [replay.reports[0].to_json()]
    assert client.get('/replays/reports').json['steps']['2'] == page
    assert client.get('/steps/reports/0/1').json['0']['state']['reports'] == []
    assert len(client.get('/steps/reports/2/1').json['7']['state']['reports']) == 2


def test_replay_cache_belongs_to_host(client, host, tmp_path):
    write_replay(host, 'same-id', (0,))
    assert list(client.get('/steps/same-id/0/1').json) == ['0']
    other = InMemoryHost(tmp_path / 'other-saves', tmp_path / 'other-replays')
    write_replay(other, 'same-id', (7,))
    assert list(other.get_replay_steps('same-id', 0, 1)) == [7]
    assert list(client.get('/steps/same-id/0/1').json) == ['0']


def test_replay_symlink_rejected(client, host, tmp_path):
    write_replay(host, 'real')
    (host.replay_dir / 'linked.rep').symlink_to(host.replay_dir / 'real.rep')
    assert_error(client.get('/steps/linked/0/1'), 400)
    assert not host._replays


def test_bot_start_progression_is_explicit(client, host):
    game = get_game_coin_toss()
    game.game_id = 'bot-start'
    # A bot in the START_GAME slot is started
    # by update, not by observation or by replacing a rejected human action.
    game.actor.human = False
    host.add_game(game)
    before = pickle.dumps(game)
    assert client.get('/games/bot-start').status_code == 200
    assert_error(act(client, game, {'action_type': 'MOVE', 'position': {'x': -1, 'y': 3}}), 409)
    assert pickle.dumps(game) == before
    game.config.fast_mode = False
    assert client.post('/games/bot-start/update').status_code == 200
    assert game.action.action_type == bb.ActionType.START_GAME
    assert pickle.dumps(game) != before


def test_corrupt_game_json_is_rejected_before_registration(client, host, game):
    host.save_dir.mkdir()
    corrupt = deepcopy(game)
    corrupt.state.home_team.name = object()
    (host.save_dir / 'corrupt.bb').write_bytes(pickle.dumps(corrupt))
    before = list(host.games)
    assert_error(client.post('/game/load/corrupt'), 500, 'storage_error')
    assert list(host.games) == before


def test_directory_read_fault_is_not_an_empty_success(client, host, game, monkeypatch):
    import os
    before = pickle.dumps(game)
    def fail(*args):
        raise PermissionError('injected directory denial')
    monkeypatch.setattr(os, 'scandir', fail)
    assert_error(client.get('/games/'), 500, 'storage_error')
    assert_error(client.get('/replays/'), 500, 'storage_error')
    assert_error(save(client, game), 500, 'storage_error')
    assert pickle.dumps(game) == before
