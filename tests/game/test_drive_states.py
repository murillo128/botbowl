"""Temporary player state at real drive boundaries (upstream #240)."""
from copy import deepcopy

import pytest

from botbowl import Action, ActionType, CasualtyEffect, OutcomeType, Skill, WeatherType
from botbowl.core.procedure import Setup, Touchdown, Turn
from tests.util import get_custom_game_turn, get_game_coin_toss, get_game_setup, get_game_turn, only_fixed_rolls


def offered_players(game):
    return next(choice.players for choice in game.get_available_actions()
                if choice.action_type == ActionType.PLACE_PLAYER)


def apply_formation(game):
    action_type = (ActionType.SETUP_FORMATION_ZONE if game.get_procedure().team == game.get_kicking_team()
                   else ActionType.SETUP_FORMATION_LINE)
    game.step(Action(action_type))


def finish_drive(game, scorer, rolls):
    # Enter the same touchdown -> EndTurn -> ClearBoard -> PreKickoff -> Setup
    # stack used by a score, without unrelated movement or kickoff randomness.
    Touchdown(game, scorer)
    game.set_available_actions()
    with only_fixed_rolls(game, d6=rolls):
        game.step()
    assert isinstance(game.get_procedure(), Setup)


@pytest.fixture
def drive_game():
    game = get_game_turn(empty=True, home_team="human", away_team="human")
    game.config.pathfinding_enabled = False
    game.state.weather = WeatherType.SWELTERING_HEAT
    groups = []
    for team in game.state.teams:
        hot, ready, rested, reserve, ko, recovered, casualty, ejected = team.players[:8]
        side = game.get_team_side(team)
        for player, square in zip((hot, ready, ko, recovered, casualty, ejected), side):
            game.reserves_to_pitch(player, square)
        game.pitch_to_kod(ko)
        game.pitch_to_kod(recovered)
        game.pitch_to_casualties(casualty)
        casualty.state.injuries_gained.append(CasualtyEffect.MNG)
        game.pitch_to_dungeon(ejected)
        rested.state.heated = True  # Sat out the drive that is about to end.
        for player in team.players:
            player.state.used = True
            player.state.has_blocked = True
            player.state.moves = 3
            player.state.used_skills.add(Skill.DODGE)
            player.state.squares_moved.append(side[0])
            player.state.failed_nega_trait_this_turn = True
            player.state.bone_headed = True
            player.state.hypnotized = True
            player.state.taken_root = True
            player.state.spp_earned = 2
        groups.append((hot, ready, rested, reserve, ko, recovered, casualty, ejected))
    game.set_available_actions()
    game.state.reports.clear()
    return game, groups


def assert_reset_and_persistent_state(game, groups):
    for team, group in zip(game.state.teams, groups):
        hot, ready, rested, reserve, ko, recovered, casualty, ejected = group
        for player in team.players:
            assert not player.state.used
            assert not player.state.has_blocked
            assert player.state.moves == 0
            assert not player.state.used_skills
            assert not player.state.squares_moved
            assert not player.state.failed_nega_trait_this_turn
            assert not player.state.bone_headed
            assert not player.state.hypnotized
            assert not player.state.taken_root
            assert player.state.spp_earned == 2
        assert game.get_knocked_out(team) == [ko]
        assert ko.state.knocked_out
        assert not recovered.state.knocked_out
        assert recovered in game.get_reserves(team)
        assert game.get_casualties(team) == [casualty]
        assert casualty.state.injuries_gained == [CasualtyEffect.MNG]
        assert game.get_dungeon(team) == [ejected]
        assert ejected.state.ejected
        assert all(player.position is None for player in (ko, casualty, ejected))


@pytest.mark.parametrize("next_weather", [WeatherType.SWELTERING_HEAT, WeatherType.NICE])
@pytest.mark.parametrize("scoring_home", [True, False])
def test_two_drives_heat_recovery_and_setup(drive_game, next_weather, scoring_home):
    game, groups = drive_game
    scoring_team = game.state.home_team if scoring_home else game.state.away_team
    game.state.current_team = scoring_team
    game.enable_forward_model()
    before = deepcopy(game.state)
    step = game.get_step()

    # Only the two players on each pitch roll for heat; each team's two KOs
    # subsequently fail/pass recovery, in PreKickoff's stack order.
    finish_drive(game, scoring_team.players[1], [1, 2, 1, 6, 1, 4, 1, 4])
    assert all(player.position is None for team in game.state.teams for player in team.players)
    assert_reset_and_persistent_state(game, groups)
    heat_reports = [report for report in game.state.reports
                    if report.outcome_type in (OutcomeType.PLAYER_HEATED, OutcomeType.PLAYER_NOT_HEATED)]
    assert [(report.player, report.outcome_type) for report in heat_reports] == [
        (player, outcome) for group in groups for player, outcome in
        ((group[0], OutcomeType.PLAYER_HEATED), (group[1], OutcomeType.PLAYER_NOT_HEATED))]
    assert [report.rolls[0].get_sum() for report in heat_reports] == [1, 2, 1, 6]
    recovery_reports = [report for report in game.state.reports
                        if report.outcome_type in (OutcomeType.PLAYER_READY, OutcomeType.PLAYER_NOT_READY)]
    assert [(report.player, report.outcome_type) for report in recovery_reports] == [
        (player, outcome) for group in reversed(groups) for player, outcome in
        ((group[4], OutcomeType.PLAYER_NOT_READY), (group[5], OutcomeType.PLAYER_READY))]
    assert game.has_report_of_type(OutcomeType.TOUCHDOWN)
    for hot, ready, rested, reserve, ko, recovered, casualty, ejected in groups:
        assert hot.state.heated
        assert not rested.state.heated
        assert not any(player.state.heated for player in (ready, reserve, ko, recovered, casualty, ejected))

    for _ in range(2):
        team = game.get_procedure().team
        group = groups[game.state.teams.index(team)]
        hot, ready, rested, reserve, ko, recovered, casualty, ejected = group
        assert set(offered_players(game)) == set(game.get_reserves(team)) - {hot}
        illegal = Action(ActionType.PLACE_PLAYER, player=hot, position=game.get_team_side(team)[0])
        assert not game.is_action_allowed(illegal)
        apply_formation(game)
        assert hot.position is None
        assert all(not player.state.heated for player in game.get_players_on_pitch(team))
        assert all(game.get_player_at(player.position) is player for player in game.get_players_on_pitch(team))
        assert game.is_setup_legal(team)
        if team != game.get_receiving_team():
            game.step(Action(ActionType.END_SETUP))

    # Force a touchback and Changing Weather. Weather does not cancel the
    # already imposed missed drive; recovery happens at the next boundary.
    game.step(Action(ActionType.END_SETUP))
    corner = next(square for square in game.get_team_side(game.get_receiving_team()) if square.y == 1)
    weather_rolls = [1, 1] if next_weather == WeatherType.SWELTERING_HEAT else [3, 3]
    with only_fixed_rolls(game, d8=[1], d6=[1, 3, 4] + weather_rolls):
        game.step(Action(ActionType.PLACE_BALL, position=corner))
        receiver = game.get_players_on_pitch(game.get_receiving_team())[0]
        game.step(Action(ActionType.SELECT_PLAYER, player=receiver))
    assert isinstance(game.get_procedure(), Turn)
    assert game.state.weather == next_weather
    assert all(group[0].position is None for group in groups)
    assert all(group[0].state.heated for group in groups)

    scorer = game.get_players_on_pitch(game.state.current_team)[0]
    heat_rolls = [2] * len(game.get_players_on_pitch()) if next_weather == WeatherType.SWELTERING_HEAT else []
    report_start = len(game.state.reports)
    finish_drive(game, scorer, heat_rolls + [1, 1])
    assert not any(player.state.heated for team in game.state.teams for player in team.players)
    assert_reset_and_persistent_state(game, groups)
    reports = game.state.reports[report_start:]
    assert sum(report.outcome_type == OutcomeType.PLAYER_NOT_HEATED for report in reports) == len(heat_rolls)
    assert not any(report.outcome_type == OutcomeType.PLAYER_HEATED for report in reports)
    for _ in range(2):
        team = game.get_procedure().team
        assert team.players[0] in offered_players(game)
        assert team.players[2] in offered_players(game)
        apply_formation(game)
        assert game.is_setup_legal(team)
        if team != game.get_receiving_team():
            game.step(Action(ActionType.END_SETUP))

    after = deepcopy(game.state)
    undone = game.revert(step)
    assert not game.state.compare(before)
    game.forward(undone)
    assert not game.state.compare(after)
    # This checks trajectory restoration/replay only, not RNG restoration.


@pytest.mark.parametrize("home_team", [True, False])
@pytest.mark.parametrize("eligible_count", [0, 1, 2])
def test_setup_minimum_counts_only_eligible_reserves(home_team, eligible_count):
    game = get_game_setup(home_team)
    team = game.get_procedure().team
    for player in team.players[eligible_count:]:
        player.state.heated = True
    game.set_available_actions()
    assert offered_players(game) == team.players[:eligible_count]
    apply_formation(game)
    assert len(game.get_players_on_pitch(team)) == eligible_count
    assert game.is_setup_legal(team)
    game.step(Action(ActionType.END_SETUP))
    assert game.has_report_of_type(OutcomeType.SETUP_DONE)


def test_upstream_240_initial_setup_clears_old_heat():
    game = get_game_coin_toss()
    for team in game.state.teams:
        for player in team.players[:6]:
            player.state.heated = True
    for action_type in (ActionType.START_GAME, ActionType.HEADS, ActionType.RECEIVE,
                        ActionType.SETUP_FORMATION_SPREAD, ActionType.END_SETUP,
                        ActionType.SETUP_FORMATION_LINE, ActionType.END_SETUP):
        game.step(Action(action_type))
    assert game.get_players_on_pitch()
    assert all(not player.state.heated for player in game.get_players_on_pitch())


@pytest.mark.parametrize("home_team", [True, False])
def test_touchdown_ends_activation_before_setup(home_team):
    game, players = get_custom_game_turn([(5, 8)], [(6, 8)], ball_position=(2, 2))
    if (game.state.current_team == game.state.home_team) != home_team:
        game.step(Action(ActionType.END_TURN))
    scorer = next(player for player in players if player.team == game.state.current_team)
    endzone_x = 1 if home_team else game.arena.width - 2
    game.move(scorer, game.get_square(endzone_x + (1 if home_team else -1), 8))
    game.get_ball().move_to(scorer.position)
    game.get_ball().is_carried = True
    game.set_available_actions()
    game.step(Action(ActionType.START_MOVE, player=scorer))
    assert game.state.active_player is scorer
    game.enable_forward_model()
    before = deepcopy(game.state)
    step = game.get_step()
    with only_fixed_rolls(game):
        game.step(Action(ActionType.MOVE, position=game.get_square(endzone_x, 8)))
    assert isinstance(game.get_procedure(), Setup)
    assert game.has_report_of_type(OutcomeType.TOUCHDOWN)
    assert game.state.active_player is None
    assert game.state.player_action_type is None
    assert scorer.position is None
    assert not scorer.state.used
    assert scorer.state.moves == 0
    after = deepcopy(game.state)
    undone = game.revert(step)
    assert not game.state.compare(before)
    game.forward(undone)
    assert not game.state.compare(after)
