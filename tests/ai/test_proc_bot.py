import pytest

from botbowl import Action, ActionChoice, ActionType, Square
from botbowl.ai.proc_bot import ProcBot
from botbowl.core.procedure import Procedure
from tests.game.test_shadowing import shadowing_game, assert_shadowing_decision
from tests.util import only_fixed_rolls


def pending_shadowing():
    game, player, opponent = shadowing_game()
    with only_fixed_rolls(game, d6=[6]):
        game.step(Action(ActionType.MOVE, position=Square(4, 5)))
    assert_shadowing_decision(game, player, opponent)
    return game, player, opponent


def test_default_shadowing_decline_is_legal_and_resumes_movement():
    game, player, opponent = pending_shadowing()
    with only_fixed_rolls(game):
        action = ProcBot('legacy bot').act(game)
        assert action.action_type == ActionType.DONT_USE_SKILL
        assert game.is_action_allowed(action)
        game.step(action)
        assert opponent.position == Square(6, 5)
        assert game.actor is game.get_team_agent(player.team)
        assert game.is_action_allowed(Action(ActionType.END_PLAYER_TURN))


def test_shadowing_dispatch_calls_subclass_override():
    class ShadowBot(ProcBot):
        def use_shadowing(self, game):
            self.seen_game = game
            return Action(ActionType.USE_SKILL, player=game.get_procedure().shadower)

        def player_action(self, game):
            pytest.fail('Shadowing must dispatch to its own hook')

    game, player, opponent = pending_shadowing()
    bot = ShadowBot('shadower')
    with only_fixed_rolls(game, d6=[2, 2]):
        action = bot.act(game)
        assert bot.seen_game is game
        assert action.action_type == ActionType.USE_SKILL
        assert action.player is opponent
        assert game.is_action_allowed(action)
        game.step(action)
        assert opponent.position == Square(5, 5)
        assert game.actor is game.get_team_agent(player.team)


@pytest.mark.parametrize('disabled', [False, True])
def test_default_hook_requires_an_available_legal_decline(disabled):
    game, _, _ = pending_shadowing()
    if disabled:
        game.state.available_actions = [
            ActionChoice(choice.action_type, team=choice.team, players=choice.players,
                         skill=choice.skill, disabled=choice.action_type == ActionType.DONT_USE_SKILL)
            for choice in game.get_available_actions()]
    else:
        game.state.available_actions = [choice for choice in game.get_available_actions()
                                        if choice.action_type != ActionType.DONT_USE_SKILL]
    with only_fixed_rolls(game), pytest.raises(NotImplementedError, match='use_shadowing'):
        ProcBot('legacy bot').act(game)


def test_unknown_procedure_error_identifies_procedure():
    class UnhandledDecision(Procedure):
        pass

    game, _, _ = shadowing_game()
    UnhandledDecision(game)
    with only_fixed_rolls(game), pytest.raises(Exception, match='Unknown procedure.*UnhandledDecision'):
        ProcBot('legacy bot').act(game)


@pytest.mark.parametrize('blitz', [False, True])
def test_existing_player_action_dispatch_is_preserved(blitz):
    class MoveBot(ProcBot):
        def player_action(self, game):
            self.seen_game = game
            return Action(ActionType.END_PLAYER_TURN)

    game, _, _ = shadowing_game(blitz=blitz)
    bot = MoveBot('mover')
    with only_fixed_rolls(game):
        action = bot.act(game)
        assert bot.seen_game is game
        assert game.is_action_allowed(action)
        game.step(action)
