import pytest

import botbowl as bb
from botbowl.ai.placement import centered_kick
from botbowl.core.procedure import PlaceBall, Setup
from tests.formation_helpers import setup_game, snapshot


@pytest.mark.parametrize("width,height", ((8, 6), (9, 7), (8, 7), (9, 6)))
@pytest.mark.parametrize("reflected", (False, True))
def test_centered_kick_on_even_odd_and_reflected_arenas(width, height, reflected):
    board = [[bb.Tile.CROWD for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            board[y][x] = bb.Tile.AWAY if x < width // 2 else bb.Tile.HOME
    if reflected:
        board = [list(reversed(row)) for row in board]
    arena = bb.TwoPlayerArena(board)
    game = setup_game(arena=arena)
    game.state.receiving_this_drive = game.state.away_team
    game.state.kicking_this_drive = game.state.home_team
    proc = PlaceBall(game, bb.Ball(None))
    proc.start()
    game.set_available_actions()
    choice = game.get_available_actions()[0]
    legal = choice.positions[:]
    x_min, x_max = min(p.x for p in legal), max(p.x for p in legal)
    y_min, y_max = min(p.y for p in legal), max(p.y for p in legal)
    expected = game.get_square((x_min + x_max) // 2, (y_min + y_max) // 2)
    before = snapshot(game)
    action = centered_kick(game)
    assert snapshot(game) == before
    assert action.action_type is bb.ActionType.PLACE_BALL
    assert action.position == expected
    assert action.position in legal and game.is_action_allowed(action)
    choice.positions.reverse()
    assert centered_kick(game).position == expected


def test_centered_kick_uses_a_legal_square_when_the_zone_has_a_hole():
    game = setup_game()
    positions = [game.get_square(x, y) for x, y in ((1, 1), (3, 1), (1, 3), (3, 3))]
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.PLACE_BALL, game.active_team,
                                                   positions=positions)]
    action = centered_kick(game)
    assert action.position == game.get_square(1, 1)
    assert game.is_action_allowed(action)


@pytest.mark.parametrize("invalid", ("unavailable", "empty", "disabled", "none", "outside"))
def test_centered_kick_rejects_invalid_zone_without_mutation(invalid):
    game = setup_game()
    if invalid != "unavailable":
        positions = {"empty": [], "disabled": [game.get_square(2, 2)],
                     "none": [None], "outside": [bb.Square(-1, 2)]}[invalid]
        game.state.available_actions = [bb.ActionChoice(
            bb.ActionType.PLACE_BALL, game.active_team, positions=positions, disabled=invalid == "disabled")]
    before = snapshot(game)
    with pytest.raises(ValueError, match="legal PLACE_BALL zone"):
        centered_kick(game)
    assert snapshot(game) == before


@pytest.mark.parametrize("size", (1, 3, 5, 7, 11))
@pytest.mark.parametrize("home", (False, True))
def test_centered_kick_expands_to_a_recorded_real_action(size, home):
    game = setup_game(size, home)
    for _ in range(2):
        assert isinstance(game.get_procedure(), Setup)
        formation = game.get_procedure().formations[0]
        for action in formation.actions(game, game.active_team):
            game.step(action)
        game.step(bb.Action(bb.ActionType.END_SETUP))
    assert isinstance(game.get_procedure(), PlaceBall)
    game.replay = bb.Replay("centered-kick")
    action = centered_kick(game)
    assert action.position in game.get_team_side(game.get_receiving_team())
    assert game.is_action_allowed(action)
    assert game.replay.actions == {}
    game.step(action)
    assert list(game.replay.actions.values()) == [action.to_json()]
    placed = [report for report in game.state.reports if report.outcome_type is bb.OutcomeType.BALL_PLACED]
    assert len(placed) == 1 and placed[0].position == action.position


def test_illegal_ball_action_is_not_recorded():
    game = setup_game()
    game.replay = bb.Replay("invalid-kick")
    before = snapshot(game)
    with pytest.raises(bb.InvalidActionError):
        game.step(bb.Action(bb.ActionType.PLACE_BALL, position=game.get_square(2, 2)))
    assert snapshot(game) == before
