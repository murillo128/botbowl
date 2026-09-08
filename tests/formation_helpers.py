"""Fresh setup scenarios with size-matched teams and explicit side selection."""
import pickle

import botbowl as bb
from botbowl.core.procedure import Setup


def setup_game(size=3, home=True, arena=None):
    config = bb.load_config(f"gym-{size}")
    config.kick_off_table = False
    rules = bb.load_rule_set(config.ruleset)
    teams = [bb.load_team_by_filename("human", rules, board_size=size) for _ in range(2)]
    game = bb.Game("formation", *teams, bb.Agent("home", human=True),
                   bb.Agent("away", human=True), config, arena=arena, seed=0)
    game.init()
    game.step(bb.Action(bb.ActionType.START_GAME))
    game.step(bb.Action(bb.ActionType.HEADS))
    team = game.state.home_team if home else game.state.away_team
    choice = bb.ActionType.KICK if game.active_team == team else bb.ActionType.RECEIVE
    game.step(bb.Action(choice))
    assert isinstance(game.get_procedure(), Setup)
    assert game.active_team == team
    return game


def reduce_roster(game, available):
    team = game.active_team
    for player in list(game.get_reserves(team))[available:]:
        game.get_reserves(team).remove(player)
        game.get_casualties(team).append(player)
    game.set_available_actions()


def snapshot(game):
    return pickle.dumps((game.state, game.action, game.replay, game.rng.get_state()))
