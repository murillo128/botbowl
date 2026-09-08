"""Local session pause gates share the HTTP host lock and injectable clocks."""
import pickle

import pytest

pytest.importorskip('flask')
import botbowl as bb
from botbowl.web import api
from botbowl.web.errors import WebError
from tests.framework.test_forced_action import FakeTime
from tests.framework.test_server import host, client, game, assert_error, act, save


def clocks(game):
    game.config.competition_mode = False
    game.time_source = now = FakeTime()
    game.add_primary_clock(game.active_team)
    primary = game.state.clocks[0]
    now.now = 3
    game.add_secondary_clock(game.active_team)
    secondary = game.state.clocks[-1]
    now.now = 5
    return now, primary, secondary


def test_pause_blocks_actions_and_driver_and_resumes_only_running_clocks(client, game, host):
    now, primary, secondary = clocks(game)
    prefix = '/games/' + game.game_id
    response = client.post(prefix + '/pause', json={})
    assert response.status_code == 200 and response.json['paused']
    now.now = 1005
    # The fake source itself is part of the pickled game; take the invariant
    # after advancing it, so no test mistakes the external clock for mutation.
    frozen = pickle.dumps(game)
    for _ in range(2):
        assert client.post(prefix + '/pause', json={}).json['paused']
        assert client.get(prefix).json['paused']
        assert client.post(prefix + '/update', json={}).json['paused']
        assert_error(act(client, game, {'action_type': 'END_TURN'}), 409, 'game_paused')
        assert_error(act(client, game, None), 409, 'game_paused')
        with pytest.raises(WebError, match='paused'):
            api.step(game.game_id, bb.Action(bb.ActionType.END_TURN))
        assert pickle.dumps(game) == frozen
    assert primary.get_running_time() == 3
    assert secondary.get_running_time() == 2
    assert not primary.is_running() and not secondary.is_running()
    response = client.post(prefix + '/resume', json={})
    assert response.status_code == 200 and not response.json['paused']
    resumed = pickle.dumps(game)
    assert client.post(prefix + '/resume', json={}).status_code == 200
    assert pickle.dumps(game) == resumed
    now.now += 1
    assert not primary.is_running() and secondary.is_running()
    assert primary.get_running_time() == 3
    assert secondary.get_running_time() == 3
    game.remove_secondary_clocks()
    now.now += 2
    assert primary.is_running() and primary.get_running_time() == 5
    turn = game.active_team
    assert act(client, game, {'action_type': 'END_TURN'}).status_code == 200
    assert game.active_team is not turn


@pytest.mark.parametrize('operation', ['pause', 'resume'])
def test_competition_and_terminal_restriction(client, game, operation):
    game.config.competition_mode = True
    before = pickle.dumps(game)
    assert_error(client.post(f'/games/{game.game_id}/{operation}', json={}), 409, 'pause_not_allowed')
    assert pickle.dumps(game) == before
    game.config.competition_mode = False
    game.state.game_over = True
    assert_error(client.post(f'/games/{game.game_id}/{operation}', json={}), 409, 'game_ended')


@pytest.mark.parametrize('operation', ['pause', 'resume'])
def test_control_request_validation(client, game, operation):
    assert_error(client.post(f'/games/missing/{operation}', json={}), 404)
    assert_error(client.get(f'/games/{game.game_id}/{operation}'), 405)
    for payload in ('[]', '{', '{"toggle": true}'):
        assert_error(client.post(f'/games/{game.game_id}/{operation}', data=payload), 400)


def test_explicit_local_creation_and_no_clock_pause_before_start(client, host):
    name = api.get_teams('1v1')[0].name
    settings = dict(home_team_name=name, away_team_name=name, local=True)
    response = client.put('/game/create', json={'mode': '1v1', 'game': settings})
    assert response.status_code == 200 and response.json['pause_allowed']
    game_id = response.json['game_id']
    game = host.get_game(game_id)
    assert not game.config.competition_mode
    assert client.post(f'/games/{game_id}/pause', json={}).json['paused']
    state = game.to_json()
    assert api.update_game(game_id).to_json() == state
    assert client.post(f'/games/{game_id}/resume', json={}).status_code == 200
    assert act(client, game, {'action_type': 'START_GAME'}).status_code == 200
    settings['local'] = 'true'
    assert_error(client.put('/game/create', json={'mode': '1v1', 'game': settings}), 400)


def test_paused_save_load_requires_explicit_resume(client, game, host):
    now, primary, secondary = clocks(game)
    host.pause_game(game.game_id)
    assert save(client, game).status_code == 200
    now.now += 1000
    loaded = client.post('/game/load/round-one', json={})
    assert loaded.status_code == 200 and loaded.json['paused']
    restored = host.get_game(loaded.json['game_id'])
    assert all(not clock.is_running() for clock in restored.state.clocks)
    assert api.update_game(restored.game_id) is restored
    host.resume_game(restored.game_id)
    assert not restored.state.clocks[0].is_running()
    assert restored.state.clocks[1].is_running()
    assert restored.state.clocks[1].get_running_time() == 2
    assert host.is_paused(game)
