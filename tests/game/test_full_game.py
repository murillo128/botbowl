from botbowl.ai.registry import make_bot
from botbowl.core.game import *


def test_team():
    config = load_config("gym-11")
    ruleset = load_rule_set(config.ruleset)
    home = load_team_by_filename("human", ruleset)
    away = load_team_by_filename("human", ruleset)
    away_agent = make_bot("random")
    home_agent = make_bot("random")
    game = Game(1, home, away, home_agent, away_agent, config)
    game.init(max_steps=20000)
    assert game.state.game_over
    assert game.end_time is not None


def test_bounded_sweltering_heat_game():
    from random import Random
    from tests.util import get_game_turn

    game = get_game_turn(home_team="human", away_team="human")
    game.config.rounds = 2
    game.config.pathfinding_enabled = False
    game.state.weather = WeatherType.SWELTERING_HEAT
    policy = Random(11)
    for _ in range(2000):
        if game.state.game_over:
            break
        choice = policy.choice([choice for choice in game.get_available_actions()
                                if not choice.disabled and choice.action_type != ActionType.PLACE_PLAYER])
        game.step(Action(choice.action_type,
                         player=policy.choice(choice.players) if choice.players else None,
                         position=policy.choice(choice.positions) if choice.positions else None))
        assert all(not player.state.heated for player in game.get_players_on_pitch())
    assert game.state.game_over
    assert (game.has_report_of_type(OutcomeType.END_OF_GAME_WINNER) or
            game.has_report_of_type(OutcomeType.END_OF_GAME_DRAW))
    assert game.has_report_of_type(OutcomeType.PLAYER_HEATED)
