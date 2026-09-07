"""
==========================
Author: Niels Justesen
Year: 2018
==========================
This module contains functions to communicate with a game host to manage games.
"""
from botbowl.web.host import InMemoryHost
from botbowl.web.errors import WebError
from botbowl.core.game import Game
from botbowl.core.model import Action, Agent
from botbowl.core.table import ActionType
from botbowl.core.load import load_rule_set, load_config, load_all_teams
from botbowl.ai.registry import list_bots
import uuid

host = InMemoryHost()


def get_config_name(board_size):
    return f"web-{board_size}.json"


ruleset = load_rule_set('BB2016', all_rules=False)
game_modes = {
    'standard': get_config_name(11),
    '7v7': get_config_name(7),
    '5v5': get_config_name(5),
    '3v3': get_config_name(3),
    '1v1': get_config_name(1)
}


def get_config(game_mode):
    if not isinstance(game_mode, str) or game_mode.lower() not in game_modes:
        raise WebError("Unknown game mode.")
    return load_config(game_modes[game_mode.lower()])


def new_game(away_team_name, home_team_name, away_agent=None, home_agent=None, game_mode='standard'):
    config = get_config(game_mode)
    if not isinstance(away_agent, Agent) or not isinstance(home_agent, Agent):
        raise WebError("Both game agents are required.")
    if not isinstance(home_team_name, str) or not isinstance(away_team_name, str):
        raise WebError("Team names must be strings.")
    # Select from loaded configuration-owned teams, never a client-supplied path.
    teams = {team.name: team for team in load_all_teams(ruleset, board_size=config.pitch_max)}
    if home_team_name not in teams or away_team_name not in teams:
        raise WebError("Unknown team for this game mode.")
    # Loading twice preserves unique roster IDs when both coaches choose one team.
    away = next(team for team in load_all_teams(ruleset, board_size=config.pitch_max)
                if team.name == away_team_name)
    game = Game(str(uuid.uuid4()), teams[home_team_name], away, home_agent, away_agent, config, record=False)
    game.init()
    host.add_game(game)
    return game


def step(game_id, action):
    with host.lock:
        game = host.get_game(game_id)
        game.step(action)  # #7 validates before any mutation; no implicit refresh.
        return game


def update_game(game_id):
    """Explicitly check clocks and advance automatic steps in interactive games."""
    with host.lock:
        game = host.get_game(game_id)
        if game.state.game_over:
            return game
        if not game.is_started() and game.actor is not None and not game.actor.human:
            game.step(Action(ActionType.START_GAME))
        else:
            game.refresh()
        return game


def save_game_exists(name):
    return host.save_game_exists(name)


def save_game(game_id, name):
    host.save_game(game_id, name)


def delete_game(game_id):
    host.end_game(game_id)


def delete_save(name):
    host.delete_saved_game(name)


def get_game(game_id):
    return host.get_game(game_id)


def get_replay(replay_id):
    return host.load_replay(replay_id)


def get_replay_steps(replay_id, from_idx, num_steps):
    return host.get_replay_steps(replay_id, from_idx, num_steps)


def get_replay_ids():
    return host.get_replay_ids()


def load_game(name):
    return host.load_game(name)


def get_games():
    return host.get_games()


def get_saved_games():
    return host.get_saved_games()


def get_teams(game_mode='standard'):
    config = get_config(game_mode)
    return load_all_teams(ruleset, board_size=config.pitch_max)


def get_bots():
    return list_bots()
