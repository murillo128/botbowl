"""Finite forced decisions and monotonic competition clocks, without sleeps."""
from copy import deepcopy
import pickle

import pytest
import botbowl as bb
from tests.util import get_game_setup, get_game_turn


class FakeTime:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def fresh(size=11):
    config = bb.load_config(f"gym-{size}")
    config.time_limits.turn = 10
    config.time_limits.secondary = 2
    config.competition_mode = True
    config.kick_off_table = False
    config.pathfinding_enabled = False
    config.rounds = 1
    rules = bb.load_rule_set(config.ruleset)
    game = bb.Game("forced", bb.load_team_by_filename("human", rules, board_size=size),
                   bb.load_team_by_filename("human", rules, board_size=size),
                   bb.Agent("home", human=True), bb.Agent("away", human=True),
                   config, seed=0, time_source=FakeTime())
    game.init()
    game.step(bb.Action(bb.ActionType.START_GAME))
    return game


def test_forced_action():
    game = fresh()
    for _ in range(100):
        if game.state.game_over:
            break
        game.time_source.now += 10
        game.refresh(max_steps=1000)
    assert game.state.game_over
    assert game.end_time is not None
    assert game.get_winner() is None


def test_forced_setup():
    game = fresh()
    game.step(bb.Action(bb.ActionType.HEADS))
    game.step(bb.Action(bb.ActionType.KICK))
    for count in (11, 22):
        game.time_source.now += 10  # Exact boundary must expire.
        game.refresh(max_steps=100)
        assert len(game.get_players_on_pitch()) == count


@pytest.mark.parametrize("home", [True, False])
@pytest.mark.parametrize("only_place", [True, False])
def test_zero_players_complete_setup_through_public_boundary(home, only_place):
    game = get_game_setup(home)
    team = game.active_team
    assert not game.get_players_on_pitch(team)
    for _ in range(30):
        if game.is_setup_legal(team):
            break
        if only_place:
            game.state.available_actions = [c for c in game.state.available_actions
                                            if c.action_type == bb.ActionType.PLACE_PLAYER]
        before = [(p.player_id, p.position) for p in team.players]
        action = game._forced_action()
        assert game.validate_action(action).allowed
        assert action.action_type != bb.ActionType.END_SETUP
        game.advance(action)
        assert [(p.player_id, p.position) for p in team.players] != before
    assert game.is_setup_legal(team)
    game.set_available_actions()
    action = game._forced_action()
    assert action.action_type == bb.ActionType.END_SETUP
    game.advance(action)
    assert game.has_report_of_type(bb.OutcomeType.SETUP_DONE)


def test_partial_setup_can_be_repaired():
    game = get_game_setup(True)
    team = game.active_team
    for player, square in zip(team.players[:4], game.get_team_side(team)[:4]):
        game.advance(bb.Action(bb.ActionType.PLACE_PLAYER, player=player, position=square))
    for _ in range(30):
        if game.is_setup_legal(team):
            break
        game.state.available_actions = [c for c in game.state.available_actions
                                        if c.action_type == bb.ActionType.PLACE_PLAYER]
        action = game._forced_action()
        assert game.validate_action(action).allowed
        game.advance(action)
    assert game.is_setup_legal(team)


@pytest.mark.parametrize("kind", ["empty", "disabled", "illegal_end", "impossible_template"])
def test_impossible_forcing_is_typed_finite_and_has_no_sporting_result(kind):
    game = get_game_setup(True)
    if kind == "empty":
        game.state.available_actions = []
    elif kind == "disabled":
        game.state.available_actions = [bb.ActionChoice(c.action_type, team=c.team, disabled=True)
                                        for c in game.state.available_actions]
    elif kind == "illegal_end":
        game.state.available_actions = [c for c in game.state.available_actions
                                        if c.action_type == bb.ActionType.END_SETUP]
    else:
        game.config.scrimmage_min = 100
    before = pickle.dumps(game)
    with pytest.raises(bb.NoProgressError, match="cannot complete setup") as error:
        game._forced_action()
    assert error.value.__cause__ is not None
    assert "procedures=" in str(error.value)
    assert pickle.dumps(game) == before
    assert not game.state.game_over and game.end_time is None and game.get_winner() is None


def test_mixed_choices_never_resample_excluded_placement():
    game = fresh()
    team = game.active_team
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.PLACE_PLAYER, team=team),
        bb.ActionChoice(bb.ActionType.TAILS, team=team),
        bb.ActionChoice(bb.ActionType.HEADS, team=team, disabled=True),
    ]
    for _ in range(30):
        action = game._forced_action()
        assert action.action_type == bb.ActionType.TAILS
        assert game.validate_action(action).allowed
    game.advance(action)
    assert bb.ActionType.KICK in [c.action_type for c in game.get_available_actions()]


def test_empty_non_setup_choices():
    game = fresh()
    game.state.available_actions = []
    with pytest.raises(bb.NoProgressError, match="no legal forced action"):
        game._forced_action()


def test_clock_monotonic_pause_resume_and_exact_boundary(monkeypatch):
    now = FakeTime(10)
    clock = bb.Clock(None, 5, time_source=now)
    audit = clock.started_at
    now.now = 12
    clock.pause()
    clock.pause()
    now.now = 100
    monkeypatch.setattr("botbowl.core.model.time.time", lambda: -100000)
    assert clock.get_running_time() == 2 and not clock.is_done()
    clock.resume()
    clock.resume()
    now.now = 103
    assert clock.get_seconds_left() == 0 and clock.is_done()
    assert clock.started_at == audit
    zero = bb.Clock(None, 0, time_source=now)
    assert zero.is_done() and zero.get_ratio_done() == 1


def test_refresh_does_not_resume_paused_clock():
    game = fresh()
    clock = game.get_agent_clock(game.actor)
    clock.pause()
    before = deepcopy(game.state.to_json(ignore_clocks=True))
    game.time_source.now += 1000
    game.refresh()
    assert not clock.is_running()
    assert game.state.to_json(ignore_clocks=True) == before


def test_secondary_clock_resumes_primary_without_charging_pause():
    game = get_game_turn()
    game.time_source = FakeTime()
    game.add_primary_clock(game.active_team)
    primary = game.get_clock(game.active_team)
    game.time_source.now = 3
    game.add_secondary_clock(game.get_opp_team(game.active_team))
    game.time_source.now = 20
    assert primary.get_running_time() == 3
    game.remove_secondary_clocks()
    game.time_source.now = 22
    assert primary.is_running() and primary.get_running_time() == 5


def test_same_team_secondary_is_selected_over_paused_primary():
    game = fresh()
    primary = game.get_clock(game.active_team)
    game.time_source.now = 1
    game.add_secondary_clock(game.active_team)
    secondary = game.state.clocks[-1]
    assert game.get_agent_clock(game.actor) is secondary
    assert game.get_seconds_left() == 2
    assert not primary.is_running()
    game.time_source.now = 3
    assert secondary.is_done()
