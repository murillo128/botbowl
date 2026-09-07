"""Small headless construction boundary over the existing loaders and Game."""
from copy import deepcopy
from typing import Literal, Optional, Union
from uuid import uuid4

from botbowl.core.game import Game
from botbowl.core.load import (load_arena, load_config, load_rule_set,
                               load_team_by_filename, load_team_by_name)
from botbowl.core.model import Agent, Configuration, RuleSet, Team
from botbowl.lab.rules import SUPPORTED_SIZES, describe_rules


def _resource_name(value: Optional[str], field: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in ("/", "\\", "\x00")):
        raise ValueError(f"{field} must be a resource name, not a path")
    return value


def _team(value: Union[str, Team], rules: RuleSet, size: int, seat: str) -> Team:
    if isinstance(value, Team):
        return value  # Game owns the deep copy, after input validation.
    if not isinstance(value, str) or not value:
        raise ValueError(f"{seat}_team must be a team name or Team")
    try:
        return load_team_by_filename(value, rules, board_size=size)
    except ValueError:
        try:
            return load_team_by_name(value, rules, board_size=size)
        except ValueError as error:
            raise ValueError(f"Unknown {seat}_team {value!r} for size {size}") from error


def create_game(
    config: Optional[Union[str, Configuration]] = None,
    home_team: Union[str, Team] = "human",
    away_team: Union[str, Team] = "human",
    *,
    control: Literal["external", "policy"],
    size: Optional[int] = None,
    seed: Optional[int] = None,
    home_agent: Optional[Agent] = None,
    away_agent: Optional[Agent] = None,
) -> Game:
    """Return an isolated Game waiting for START_GAME, without calling act().

    Omitted config loads gym-{size}, defaulting to 11. Team strings are shipped
    file stems or display names for that size. Configuration and Team objects
    are copied; supplied policy agents remain caller-owned, as with Game.

    External control uses human placeholder agents and accepts no policies.
    Policy control requires two distinct non-human Agents (e.g. make_bot).
    Game.init calls their new_game callbacks; only an explicit PolicyDriver.run
    calls act. Both modes use the external one-decision engine semantics.
    Seed controls engine randomness only, not a policy's own RNG or UUIDs.
    Invalid resources/arguments raise ValueError before policy callbacks.
    """
    if control not in ("external", "policy"):
        raise ValueError("control must be 'external' or 'policy'")
    if size is not None and (type(size) is not int or size not in SUPPORTED_SIZES):
        raise ValueError("size must be one of 1, 3, 5, 7, 11")
    if seed is not None and (type(seed) is not int or not 0 <= seed < 2**32):
        raise ValueError("seed must be None or an integer in [0, 2**32)")
    if control == "external":
        if home_agent is not None or away_agent is not None:
            raise ValueError("external control does not accept policy agents")
        home_agent, away_agent = Agent("home", human=True), Agent("away", human=True)
    elif (not isinstance(home_agent, Agent) or not isinstance(away_agent, Agent)
          or home_agent.human or away_agent.human or home_agent == away_agent):
        raise ValueError("policy control requires two distinct non-human Agents")

    if config is None:
        config = f"gym-{11 if size is None else size}"
    if isinstance(config, str):
        name = _resource_name(config, "config")
        try:
            configuration = load_config(name)
        except OSError as error:
            raise ValueError(f"Unknown configuration {name!r}") from error
    elif isinstance(config, Configuration):
        configuration = deepcopy(config)
    else:
        raise ValueError("config must be a configuration name, Configuration or None")
    actual_size = configuration.pitch_max
    if type(actual_size) is not int or actual_size not in SUPPORTED_SIZES:
        raise ValueError("Configuration size must be one of 1, 3, 5, 7, 11")
    if size is not None and size != actual_size:
        raise ValueError(f"size {size} disagrees with configuration size {actual_size}")
    arena_name = _resource_name(configuration.arena, "config.arena")
    if arena_name not in (f"ff-pitch-{actual_size}", f"ff-pitch-{actual_size}.txt"):
        raise ValueError(f"Configuration arena is incompatible with size {actual_size}")
    rules_name = _resource_name(configuration.ruleset, "config.ruleset")
    try:
        rules = load_rule_set(rules_name)
        arena = load_arena(arena_name)
    except OSError as error:
        raise ValueError("Unknown configuration ruleset or arena") from error
    home = _team(home_team, rules, actual_size, "home")
    away = _team(away_team, rules, actual_size, "away")
    # Reuse the accepted loaded-resource contract, including roster, formation,
    # rules, numeric limit and identity validation; do not implement it twice.
    describe_rules(configuration, rules, arena, home, away)
    game = Game(str(uuid4()), home, away, home_agent, away_agent, configuration,
                arena=arena, ruleset=rules, seed=seed, external_control=True)
    game.init()
    return game
