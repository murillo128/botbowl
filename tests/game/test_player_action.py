import pytest
from copy import deepcopy
from botbowl.core.game import *
from unittest.mock import *
import numpy as np
from tests.util import get_custom_game_turn, only_fixed_rolls

'''
TODO: Re-write to not use mocks and fix issues.
@patch("botbowl.core.game.Game")
def test_turn_start_player_action_default(mock_game):
    # patch the mock game proc stack
    stack = Stack()
    mock_game.state.stack = stack
    with patch("botbowl.core.util.Stack", new_callable=PropertyMock) as a:
        a.return_value = stack

        role = Role("Blitzer", "orc", 6,3,3,9, [], 50000, None)
        player = Player("1", role, "test", 1, "orc")

        team = Team("humans", "dudes", Race("orc", None, 10, False, None))
        # test subject
        turn = Turn(mock_game, team, None, None)
        turn.started = True
        action = Action(ActionType.START_MOVE, player=player)
        turn.step(action)

        proc = stack.pop()
        assert isinstance(proc, PlayerAction)


@patch("botbowl.core.game.Game")
def test_turn_start_player_action_with_bonehead(mock_game):
    # patch the mock game proc stack
    stack = Stack()
    mock_game.state.stack = stack

    with patch("botbowl.core.util.Stack", new_callable=PropertyMock) as a:
        a.return_value = stack

        role = Role("Blitzer", "orc", 6,3,3,9, [], 50000, None)
        player = Player("1", role, "test", 1, "orc", extra_skills=[Skill.BONE_HEAD])

        team = Team("humans", "dudes", Race("orc", None, 10, False, None))
        # test subject
        turn = Turn(mock_game, team, None, None)
        turn.started = True
        action = Action(ActionType.START_MOVE, player=player)
        turn.step(action)

        # uppermost is bonehead
        proc = stack.pop()
        assert isinstance(proc, Bonehead)
        # next is the intended player action
        proc = stack.pop()
        assert isinstance(proc, PlayerAction)
'''


@pytest.mark.parametrize("home_team", [True, False])
@pytest.mark.parametrize("turnover", [True, False])
def test_blitz_block_resets_for_next_turn(home_team, turnover):
    game, players = get_custom_game_turn([(5, 8)], [(6, 8)], ball_position=(2, 2))
    for team in game.state.teams:
        team.state.rerolls = 0
    if (game.state.current_team == game.state.home_team) != home_team:
        game.step(Action(ActionType.END_TURN))
    attacker = next(player for player in players if player.team == game.state.current_team)
    defender = next(player for player in players if player.team != game.state.current_team)
    team = attacker.team
    game.enable_forward_model()
    before = deepcopy(game.state)
    step = game.get_step()

    game.step(Action(ActionType.START_BLITZ, player=attacker))
    defender_square = defender.position
    dx = defender.position.x - attacker.position.x
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
        game.step(Action(ActionType.BLOCK, position=defender.position))
        game.step(Action(ActionType.SELECT_PUSH))
        game.step(Action(ActionType.PUSH, position=game.get_square(defender.position.x + dx, defender.position.y)))
        game.step(Action(ActionType.FOLLOW_UP, position=defender_square))
    assert attacker.state.has_blocked
    assert not game.is_action_allowed(Action(ActionType.BLOCK, position=defender.position))

    if turnover:
        # A failed dodge ends the team turn with no explicit END_PLAYER_TURN.
        with only_fixed_rolls(game, d6=[1, 1, 1]):
            game.step(Action(ActionType.MOVE, position=game.get_square(attacker.position.x, attacker.position.y + 1)))
        assert game.has_report_of_type(OutcomeType.TURNOVER)
    else:
        game.step(Action(ActionType.END_PLAYER_TURN))
        assert attacker.state.used
        assert not game.is_action_allowed(Action(ActionType.START_BLITZ, player=attacker))
        assert not game.is_action_allowed(Action(ActionType.START_BLOCK, player=attacker))
        assert game.state.active_player is None
        assert game.state.player_action_type is None
        game.step(Action(ActionType.END_TURN))

    assert game.state.current_team != team
    assert not attacker.state.has_blocked
    assert not attacker.state.used
    assert attacker.state.moves == 0
    assert not attacker.state.used_skills
    assert not attacker.state.squares_moved
    assert not game.is_action_allowed(Action(ActionType.START_BLITZ, player=attacker))
    game.step(Action(ActionType.END_TURN))
    assert game.state.current_team == team
    game.step(Action(ActionType.START_BLITZ, player=attacker))
    if turnover:
        game.step(Action(ActionType.STAND_UP))
    assert game.is_action_allowed(Action(ActionType.BLOCK, position=defender.position))
    with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
        game.step(Action(ActionType.BLOCK, position=defender.position))
    assert game.is_action_allowed(Action(ActionType.SELECT_PUSH))

    after = deepcopy(game.state)
    undone = game.revert(step)
    assert not game.state.compare(before)
    game.forward(undone)
    assert not game.state.compare(after)
