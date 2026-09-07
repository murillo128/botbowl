"""Documented boundaries, including non-default input objects and a callable."""
from botbowl import (Action, ActionType, Configuration, DecisionResult, Game,
                     Policy, PolicyDriver, create_game, load_config,
                     load_rule_set, load_team_by_filename)


def choose(game: Game) -> Action:
    return Action(game.get_available_actions()[0].action_type)


def use_api() -> None:
    config: Configuration = load_config("gym-3")
    rules = load_rule_set("BB2016")
    game: Game = create_game(config, load_team_by_filename("human", rules, 3),
                            "human", control="external", seed=17)
    try:
        result: DecisionResult = game.advance(Action(ActionType.START_GAME), max_steps=100)
        policy: Policy = choose
        driver = PolicyDriver(game, {game.state.away_team.team_id: policy})
        PolicyDriver(game, {"away": lambda current: Action(ActionType.HEADS)})
        result = driver.run(max_decisions=1, max_steps=100)
        assert isinstance(result.terminal, bool)
    finally:
        game.close()
