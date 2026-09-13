"""Negative controls: every marked call must produce its declared diagnostic."""
from botbowl import Action, ActionType, DecisionResult, Game, PolicyDriver, create_game


def misuse(game: Game) -> None:
    create_game(control="external", seed="17")  # expect: arg-type
    create_game(control="automatic")  # expect: arg-type
    create_game(control="external", home_team=42)  # expect: arg-type
    create_game()  # expect: call-arg
    Action("START_GAME")  # expect: arg-type
    game.advance("START_GAME")  # expect: arg-type
    PolicyDriver(game, {"away": lambda game: "HEADS"})  # expect: dict-item
    result: DecisionResult = game.advance(Action(ActionType.START_GAME))
    result.terminal = False  # expect: misc
