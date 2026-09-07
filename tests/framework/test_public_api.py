"""Factory acceptance against the real loaders, engine and policy driver."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import botbowl as bb


@pytest.mark.parametrize("size", [1, 3, 5, 7, 11])
@pytest.mark.parametrize("config_kind", ["default", "name", "object"])
def test_sizes_and_one_decision(size, config_kind):
    config = None if config_kind == "default" else f"gym-{size}"
    if config_kind == "object":
        config = bb.load_config(config)
    away_name = "Human Team" if size == 11 else f"Human Team {size}"
    game = bb.create_game(config, "human", away_name, size=size,
                          seed=17, control="external")
    try:
        assert type(game) is bb.Game and game.external_control
        assert game.config.pitch_max == size
        assert game.arena.width == bb.load_arena(f"ff-pitch-{size}").width
        assert game.ruleset.name == "BB2016"
        assert game.state.home_team.players and game.state.away_team.players
        assert game.state.home_team.team_id != game.state.away_team.team_id
        assert [c.action_type for c in game.get_available_actions()] == [bb.ActionType.START_GAME]
        assert not game.state.reports and game.replay is None
        result = game.advance(bb.Action(bb.ActionType.START_GAME), max_steps=100)
        assert isinstance(result, bb.DecisionResult)
        assert not result.terminal and result.actor == game.away_agent
        assert isinstance(result.events, tuple) and result.events
        assert bb.ActionType.HEADS in [c.action_type for c in game.get_available_actions()]
    finally:
        game.close()
    assert game.closed and not game.state.game_over


def test_default_and_legacy_imports():
    game = bb.create_game(control="external")
    assert game.config.pitch_max == 11
    game.close()
    from botbowl.core.game import Game
    from botbowl.core.load import load_config
    from botbowl.ai.registry import make_bot

    assert bb.Game is Game and bb.load_config is load_config and bb.make_bot is make_bot
    assert bb.Agent and bb.Action and bb.PolicyDriver and bb.RandomBot


@pytest.mark.parametrize("name", ["bot-bowl.json", "web-1", "web-3", "web-5", "web-7", "web-11"])
def test_other_shipped_configurations(name):
    game = bb.create_game(name, control="external")
    assert game.get_available_actions()[0].action_type == bb.ActionType.START_GAME
    game.close()


def test_loading_does_not_mutate_ruleset_constructor_defaults():
    defaults = bb.RuleSet("defaults")
    before = deepcopy((defaults.races, defaults.star_players, defaults.inducements,
                       defaults.spp_actions, defaults.spp_levels, defaults.improvements))
    game = bb.create_game(size=1, control="external")
    game.close()
    after = (defaults.races, defaults.star_players, defaults.inducements,
             defaults.spp_actions, defaults.spp_levels, defaults.improvements)
    # Constructor defaults may be populated by unrelated legacy callers.
    assert [len(value) for value in after] == [len(value) for value in before]
    assert game.ruleset.races is not defaults.races


def test_object_and_loader_isolation():
    config = bb.load_config("gym-3")
    config.rounds = 2
    rules = bb.load_rule_set("BB2016")
    home = bb.load_team_by_filename("human", rules, 3)
    away = bb.load_team_by_filename("human", rules, 3)
    original = deepcopy(home.to_json())
    first = bb.create_game(config, home, away, seed=17, control="external")
    second = bb.create_game(config, home, away, seed=17, control="external")
    try:
        assert first.config.rounds == second.config.rounds == 2
        first.config.rounds = 1
        first.config.time_limits.turn = 1
        first.config.offensive_formations[0].formation[0][0] = "x"
        first.state.home_team.players[0].extra_skills.append(bb.Skill.DODGE)
        first.state.home_team.players[0].role.skills.append(bb.Skill.DODGE)
        first.state.home_team.state.rerolls = 0
        first.ruleset.races[0].roles[0].skills.append(bb.Skill.DODGE)
        first.ruleset.spp_actions.clear()
        assert second.config.rounds == config.rounds == 2
        assert second.config.time_limits.turn == config.time_limits.turn == 60
        assert second.config.offensive_formations[0].formation[0][0] != "x"
        assert config.offensive_formations[0].formation[0][0] != "x"
        assert second.state.home_team.to_json() == home.to_json() == original
        fresh_rules = bb.load_rule_set("BB2016")
        assert second.ruleset.spp_actions == fresh_rules.spp_actions
        assert second.ruleset.races[0].roles[0].skills == fresh_rules.races[0].roles[0].skills
        assert first.ruleset.races is not second.ruleset.races
        assert len(first.ruleset.races) == len(second.ruleset.races) == len(fresh_rules.races)
        first.advance(bb.Action(bb.ActionType.START_GAME), max_steps=100)
        assert second.get_available_actions()[0].action_type == bb.ActionType.START_GAME
        assert not second.state.reports
    finally:
        first.close()
        second.close()


def test_default_calls_and_both_seats_are_isolated():
    first = bb.create_game(size=1, control="external")
    second = bb.create_game(size=1, control="external")
    try:
        first.config.offensive_formations.clear()
        first.state.home_team.players[0].role.skills.clear()
        first.arena.board[1][1] = bb.Tile.CROWD
        assert second.config.offensive_formations
        assert second.arena.board[1][1] != bb.Tile.CROWD
        assert first.state.away_team.players[0].role.skills
        assert second.state.home_team.players[0].role.skills
        assert bb.load_config("gym-1").offensive_formations
    finally:
        first.close()
        second.close()


def test_seed_determinism():
    first = bb.create_game(size=3, seed=17, control="external")
    second = bb.create_game(size=3, seed=17, control="external")

    def semantic(value):
        if isinstance(value, dict):
            return {k: semantic(v) for k, v in value.items() if not k.endswith("_id")}
        if isinstance(value, (list, tuple)):
            return [semantic(v) for v in value]
        return value

    try:
        for action in (bb.ActionType.START_GAME, bb.ActionType.HEADS, bb.ActionType.KICK):
            left = first.advance(bb.Action(action), max_steps=100)
            right = second.advance(bb.Action(action), max_steps=100)
            assert semantic([e.to_json() for e in left.events]) == semantic([e.to_json() for e in right.events])
            assert first.state.weather == second.state.weather
            assert (first.active_team == first.state.home_team) == (second.active_team == second.state.home_team)
            assert [c.action_type for c in first.get_available_actions()] == [c.action_type for c in second.get_available_actions()]
            np.testing.assert_array_equal(first.rng.get_state()[1], second.rng.get_state()[1])
            assert first.rng.get_state()[2:] == second.rng.get_state()[2:]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("seed", [None, 0, 2**32 - 1])
def test_seed_endpoints(seed):
    game = bb.create_game(size=1, seed=seed, control="external")
    game.close()


@pytest.mark.parametrize("kwargs, message", [
    ({"config": "unknown"}, "Unknown configuration"),
    ({"config": "../gym-1"}, "resource name"),
    ({"config": 1}, "config must"),
    ({"config": "gym-3", "size": 1}, "disagrees"),
    ({"size": 2}, "size must"),
    ({"size": True}, "size must"),
    ({"size": 3.0}, "size must"),
    ({"home_team": "missing"}, "Unknown home_team"),
    ({"away_team": "orc", "size": 1}, "Unknown away_team"),
    ({"home_team": None}, "home_team must"),
    ({"away_team": 1}, "away_team must"),
    ({"seed": -1}, "seed must"),
    ({"seed": 2**32}, "seed must"),
    ({"seed": True}, "seed must"),
    ({"seed": 1.0}, "seed must"),
    ({"seed": "17"}, "seed must"),
    ({"control": "automatic"}, "control must"),
    ({"home_agent": bb.Agent("home")}, "external control"),
    ({"control": "policy"}, "two distinct non-human"),
    ({"control": "policy", "home_agent": bb.Agent("home", human=True),
      "away_agent": bb.Agent("away", human=True)}, "two distinct non-human"),
])
def test_invalid_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        bb.create_game(**{"control": "external", **kwargs})


def test_control_is_required():
    with pytest.raises(TypeError, match="control"):
        bb.create_game()


@pytest.mark.parametrize("field,value", [
    ("arena", "ff-pitch-11.txt"), ("arena", None), ("ruleset", "missing"),
    ("ruleset", None), ("pitch_max", 2), ("rounds", -1),
    ("pitch_min", 4), ("roster_size", 1), ("fast_mode", "yes"),
])
def test_invalid_configuration_objects(field, value):
    config = bb.load_config("gym-3")
    setattr(config, field, value)
    with pytest.raises(ValueError):
        bb.create_game(config, control="external")


def test_invalid_team_objects():
    rules = bb.load_rule_set("BB2016")
    home = bb.load_team_by_filename("human", rules, 11)
    with pytest.raises(ValueError, match="roster"):
        bb.create_game(size=1, home_team=home, control="external")
    with pytest.raises(ValueError, match="distinct team identities"):
        bb.create_game(home_team=home, away_team=home, control="external")
    away = bb.load_team_by_filename("human", rules, 11)
    away.players[0].player_id = home.players[0].player_id
    with pytest.raises(ValueError, match="Player identities"):
        bb.create_game(home_team=home, away_team=away, control="external")


class Spy(bb.Agent):
    def __init__(self, name):
        super().__init__(name)
        self.calls = []

    def new_game(self, game, team):
        self.calls.append("new_game")

    def act(self, game):
        self.calls.append("act")
        return bb.Action(game.get_available_actions()[0].action_type)

    def end_game(self, game):
        self.calls.append("end_game")


def test_explicit_policy_mode_does_not_schedule_policies():
    home, away = Spy("home"), Spy("away")
    game = bb.create_game(size=3, seed=17, control="policy", home_agent=home, away_agent=away)
    assert game.home_agent is home and game.away_agent is away
    assert home.calls == away.calls == ["new_game"]
    game.init()
    assert home.calls == away.calls == ["new_game"]
    driver = bb.PolicyDriver(game, {game.state.home_team.team_id: home.act,
                                    game.state.away_team.team_id: away.act})
    actor = game.actor
    other = away if actor is home else home
    result = driver.run(max_decisions=1, max_steps=100)
    assert len(driver.trace) == 1 and not result.terminal
    assert actor.calls == ["new_game", "act"] and other.calls == ["new_game"]
    game.close()
    assert "end_game" not in home.calls + away.calls
    with pytest.raises(bb.InvalidActionError) as error:
        driver.run(max_decisions=0)
    assert error.value.code == "game_closed"
    assert actor.calls == ["new_game", "act"] and other.calls == ["new_game"]


def test_invalid_factory_never_initializes_policies():
    home, away = Spy("home"), Spy("away")
    with pytest.raises(ValueError):
        bb.create_game("unknown", control="policy", home_agent=home, away_agent=away)
    with pytest.raises(ValueError, match="distinct"):
        bb.create_game(control="policy", home_agent=home, away_agent=home)
    assert home.calls == away.calls == []


def test_close_and_invalid_action_preserve_boundary():
    game = bb.create_game(size=1, seed=17, control="external")
    before = game.to_json()
    with pytest.raises(bb.InvalidActionError):
        game.advance(bb.Action(bb.ActionType.HEADS), max_steps=100)
    assert game.to_json() == before
    game.close()
    game.close()
    assert game.to_json() == before and game.closed
    assert not game.validate_action(bb.Action(bb.ActionType.START_GAME)).allowed
    for operation in (game.init, game.advance, game.step, game.refresh):
        with pytest.raises(bb.InvalidActionError) as error:
            operation()
        assert error.value.code == "game_closed"
    assert game.to_json() == before


def test_execution_budget():
    game = bb.create_game(size=1, control="external")
    try:
        with pytest.raises(bb.GameTruncatedError) as error:
            game.advance(bb.Action(bb.ActionType.START_GAME), max_steps=0)
        assert error.value.code == "step_budget" and not game.state.game_over
    finally:
        game.close()


def test_no_optional_import_or_service(tmp_path):
    package_parent = str(Path(bb.__file__).resolve().parent.parent)
    code = f"import sys; sys.path.insert(0, {package_parent!r})\n" + '''
import importlib.abc
import socket
class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'gym', 'gymnasium', 'flask', 'docker',
                'torch', 'matplotlib', 'tkinter', 'pygame'} or fullname.startswith(
                ('botbowl.web', 'botbowl.ai.env', 'botbowl.ai.competition')):
            raise AssertionError('Unexpected import: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
class RejectSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise AssertionError('Unexpected socket')
socket.socket = RejectSocket
import botbowl as bb
for size in (1, 3, 5, 7, 11):
    game = bb.create_game(size=size, seed=17, control='external')
    game.advance(bb.Action(bb.ActionType.START_GAME), max_steps=100)
    game.close()
'''
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, timeout=30)


@pytest.mark.parametrize("example", ["public_external.py", "public_policy.py"])
def test_real_public_examples_from_another_cwd(example, tmp_path):
    path = Path(__file__).resolve().parents[2] / "examples" / example
    # Works with both editable local development and CI's installed wheel.
    subprocess.run([sys.executable, str(path), "--max-steps", "100"],
                   cwd=tmp_path, check=True, timeout=30)
    rejected = subprocess.run([sys.executable, str(path), "--max-steps", "0"],
                              cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0 and "execution budget exhausted" in rejected.stderr
