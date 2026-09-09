"""Occupied Perfect Defence placements must survive search undo and replay."""
from copy import deepcopy

import pytest

import botbowl as bb
from botbowl.core.procedure import Setup
from tests.framework.test_forward_model import assert_game_states
from tests.util import get_game_setup


def assert_positions(game, expected=None):
    positions = []
    for team in game.state.teams:
        for player in team.players:
            positions.append(player.position)
            assert game.state.player_by_id[player.player_id] is player
            if player.position is not None:
                assert game.get_player_at(player.position) is player
    for y, row in enumerate(game.state.pitch.board):
        for x, player in enumerate(row):
            if player is not None:
                assert player.position is game.get_square(x, y)
                assert game.state.player_by_id[player.player_id] is player
    if expected is not None:
        assert positions == expected
    return positions


@pytest.mark.parametrize("home", [True, False])
def test_perfect_defence_revert_forward(home):
    game = get_game_setup(home_team=home)
    game.config.kick_off_table = True
    game.config.pathfinding_enabled = False
    for formation in (bb.ActionType.SETUP_FORMATION_SPREAD, bb.ActionType.SETUP_FORMATION_WEDGE):
        game.step(bb.Action(formation))
        game.step(bb.Action(bb.ActionType.END_SETUP))
    game.dice.fix(bb.D6, 1, 2, 2)  # Scatter distance, then Perfect Defence.
    game.dice.fix(bb.D8, 2)
    game.step(bb.Action(bb.ActionType.PLACE_BALL, position=game.get_square(7 if home else 20, 8)))
    assert isinstance(game.get_procedure(), Setup) and game.get_procedure().reorganize
    assert game.active_team is (game.state.home_team if home else game.state.away_team)

    game.enable_forward_model()
    # Independently owned state/rules; the clean execution never reverts.
    clean = deepcopy(game)
    players = game.get_players_on_pitch(game.active_team)
    decisions = [
        (bb.ActionType.PLACE_PLAYER, players[0].player_id, players[1].position),
        (bb.ActionType.SETUP_FORMATION_ZONE, None, None),
        (bb.ActionType.SETUP_FORMATION_ZONE, None, None),  # Includes self-swaps.
        (bb.ActionType.END_SETUP, None, None),
    ]
    for action_type, player_id, position in decisions:
        before = deepcopy(game)
        positions = assert_positions(game)
        step = game.get_step()
        for target in (game, clean):
            action = bb.Action(action_type,
                               player=target.get_player(player_id) if player_id is not None else None,
                               position=target.get_square(position.x, position.y) if position is not None else None)
            assert target.validate_action(action).allowed
            target.step(action)
            assert_positions(target)
        assert_game_states(game, clean, equal=True)
        rng_after = game.capture_rng_state()
        assert rng_after == clean.capture_rng_state()
        for _ in range(2):
            undone = game.revert(step)
            assert_positions(game, positions)
            assert_game_states(game, before, equal=True)
            assert game.capture_rng_state() == rng_after  # Legacy revert leaves RNG advanced.
            game.forward(undone)
            assert_positions(game)
            assert_game_states(game, clean, equal=True)
            assert game.capture_rng_state() == rng_after


@pytest.mark.parametrize("ball_at", [0, 1])
@pytest.mark.parametrize("self_swap", [False, True])
def test_swap_ball_revert_forward(ball_at, self_swap):
    game = get_game_setup(home_team=True)
    game.step(bb.Action(bb.ActionType.SETUP_FORMATION_ZONE))
    players = game.get_players_on_pitch(game.active_team)[:2]
    ball = bb.Ball(players[ball_at].position, is_carried=True)
    game.put(ball, ball.position)
    expected_ball = ball.position if self_swap else players[1].position
    game.enable_forward_model()
    clean = deepcopy(game)
    before = deepcopy(game)
    positions = assert_positions(game)
    step = game.get_step()
    for target in (game, clean):
        a, b = [target.get_player(player.player_id) for player in players]
        target.swap(a, a if self_swap else b)
        assert_positions(target)
    # Preserve the existing ball movement order, including a ball initially at b.
    assert ball.position == expected_ball
    assert_game_states(game, clean, equal=True)
    rng = game.capture_rng_state()
    for _ in range(2):
        undone = game.revert(step)
        assert_positions(game, positions)
        assert_game_states(game, before, equal=True)
        game.forward(undone)
        assert_positions(game)
        assert_game_states(game, clean, equal=True)
        assert game.capture_rng_state() == rng
