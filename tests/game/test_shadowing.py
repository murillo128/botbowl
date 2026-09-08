import pytest

from botbowl import Action, ActionType, BBDieResult, OutcomeType, Skill, Square
from botbowl.core.procedure import BlitzAction, Block, MoveAction, Reroll, Shadowing, Turn
from tests.util import get_custom_game_turn, only_fixed_rolls


def shadowing_game(blitz=False, gfi=False, pathfinding=False, shadowing=True):
    game, (player, opponent) = get_custom_game_turn(
        [(5, 5)], [(6, 5)], pathfinding_enabled=pathfinding)
    for team in game.state.teams:
        team.state.rerolls = 0
    if shadowing:
        opponent.extra_skills.append(Skill.SHADOWING)
    player.extra_skills.append(Skill.STAB)
    game.step(Action(ActionType.START_BLITZ if blitz else ActionType.START_MOVE, player=player))
    if gfi:
        player.state.moves = player.get_ma()
        game.set_available_actions()
    game.state.reports.clear()
    return game, player, opponent


def reports(game, outcome=None, skill=None):
    return [report for report in game.state.reports
            if (outcome is None or report.outcome_type == outcome)
            and (skill is None or report.skill == skill)]


def assert_squares(game, player, opponent, player_square, opponent_square):
    assert player.position == player_square
    assert opponent.position == opponent_square
    assert game.get_player_at(player_square) is player
    assert game.get_player_at(opponent_square) is opponent


def assert_shadowing_decision(game, player, opponent):
    proc = game.get_procedure()
    assert isinstance(proc, Shadowing)
    assert proc.player is player
    assert proc.shadower is opponent
    assert proc.position == Square(5, 5)
    assert game.state.current_team is player.team
    assert game.state.active_player is player
    assert game.actor is game.get_team_agent(opponent.team)
    choices = game.get_available_actions()
    assert {choice.action_type for choice in choices} == {
        ActionType.USE_SKILL, ActionType.DONT_USE_SKILL}
    assert all(choice.team is opponent.team and choice.players == [opponent]
               and choice.skill == Skill.SHADOWING for choice in choices)
    assert not reports(game, skill=Skill.SHADOWING)


@pytest.mark.parametrize('attack', [ActionType.BLOCK, ActionType.STAB])
@pytest.mark.parametrize('pathfinding', [False, True])
@pytest.mark.parametrize('reroll', ['initial_success', 'none', 'decline', 'team_success', 'team_failure',
                                   'sure_feet_success', 'sure_feet_failure'])
def test_stationary_blitz_gfi_never_shadows(attack, pathfinding, reroll):
    game, player, opponent = shadowing_game(blitz=True, gfi=True, pathfinding=pathfinding)
    team_reroll = reroll in ('decline', 'team_success', 'team_failure')
    if team_reroll:
        player.team.state.rerolls = 1
    if reroll.startswith('sure_feet'):
        player.extra_skills.append(Skill.SURE_FEET)
    success = reroll.endswith('success')
    dice = [2 if reroll == 'initial_success' else 1]
    if reroll not in ('initial_success', 'none', 'decline'):
        dice.append(2 if success else 1)
    # Successful Stab armour or failed GFI fall armour; no Shadowing dice.
    if not success or attack == ActionType.STAB:
        dice += [1, 1]
    block_dice = [BBDieResult.PUSH] if success and attack == ActionType.BLOCK else []
    with only_fixed_rolls(game, d6=dice, block_dice=block_dice):
        game.step(Action(attack, position=opponent.position))
        if team_reroll:
            assert isinstance(game.get_procedure(), Reroll)
            assert game.actor is game.get_team_agent(player.team)
            assert game.state.current_team is player.team
            assert_squares(game, player, opponent, Square(5, 5), Square(6, 5))
            assert player.state.up
            assert not reports(game, skill=Skill.SHADOWING)
            game.step(Action(ActionType.DONT_USE_REROLL if reroll == 'decline'
                             else ActionType.USE_REROLL))
        assert not isinstance(game.get_procedure(), Shadowing)
        assert not reports(game, skill=Skill.SHADOWING)
        assert_squares(game, player, opponent, Square(5, 5), Square(6, 5))
        assert player.state.up == success
        assert opponent.state.up
        gfi_reports = [r for r in reports(game) if r.outcome_type in (
            OutcomeType.FAILED_GFI, OutcomeType.SUCCESSFUL_GFI)]
        assert [r.rolls[0].get_sum() for r in gfi_reports] == dice[:len(gfi_reports)]
        assert len(gfi_reports) == (1 if reroll in ('initial_success', 'none', 'decline') else 2)
        assert all(r.player is player and r.position == Square(5, 5) for r in gfi_reports)
        assert len(reports(game, OutcomeType.REROLL_USED)) == reroll.startswith('team_')
        assert len(reports(game, skill=Skill.SURE_FEET)) == reroll.startswith('sure_feet')
        if success:
            assert game.state.current_team is player.team
            assert game.actor is game.get_team_agent(player.team)
            assert not reports(game, OutcomeType.TURNOVER)
            assert len(reports(game, OutcomeType.BLOCK_ROLL)) == (attack == ActionType.BLOCK)
            assert len(reports(game, skill=Skill.STAB)) == (attack == ActionType.STAB)
            if attack == ActionType.BLOCK:
                assert isinstance(game.get_procedure(), Block)
                assert game.is_action_allowed(Action(ActionType.SELECT_PUSH))
            else:
                assert isinstance(game.get_procedure(), Turn)
                assert game.state.active_player is None
                assert game.is_action_allowed(Action(ActionType.END_TURN))
        else:
            assert not reports(game, OutcomeType.BLOCK_ROLL)
            assert not reports(game, skill=Skill.STAB)
            assert [r.outcome_type for r in reports(game)][-5:] == [
                OutcomeType.KNOCKED_DOWN, OutcomeType.ARMOR_NOT_BROKEN,
                OutcomeType.TURNOVER, OutcomeType.TURN_START, OutcomeType.STUNNED_TURNED]
            assert isinstance(game.get_procedure(), Turn)
            assert game.state.current_team is opponent.team
            assert game.actor is game.get_team_agent(opponent.team)
            assert game.state.active_player is None
            assert game.is_action_allowed(Action(ActionType.START_MOVE, player=opponent))


@pytest.mark.parametrize('blitz', [False, True])
@pytest.mark.parametrize('gfi', [False, True])
@pytest.mark.parametrize('decision', ['decline', 'success', 'failure', 'no_skill'])
def test_movement_shadowing_decision_and_next_action(blitz, gfi, decision):
    game, player, opponent = shadowing_game(blitz=blitz, gfi=gfi,
                                           shadowing=decision != 'no_skill')
    destination = Square(4, 5)
    with only_fixed_rolls(game, d6=([2] if gfi else []) + [6]):
        game.step(Action(ActionType.MOVE, position=destination))
        assert_squares(game, player, opponent, destination, Square(6, 5))
        assert game.get_player_at(Square(5, 5)) is None
        assert [r.outcome_type for r in reports(game)] == (
            [OutcomeType.SUCCESSFUL_GFI] if gfi else []) + [OutcomeType.SUCCESSFUL_DODGE]
        if decision != 'no_skill':
            assert_shadowing_decision(game, player, opponent)
    shadow_dice = [2, 2] if decision == 'success' else [6, 6] if decision == 'failure' else []
    with only_fixed_rolls(game, d6=shadow_dice):
        if decision != 'no_skill':
            game.step(Action(ActionType.DONT_USE_SKILL if decision == 'decline'
                             else ActionType.USE_SKILL, player=opponent))
        assert_squares(game, player, opponent, destination,
                       Square(5, 5) if decision == 'success' else Square(6, 5))
        skill_reports = reports(game, skill=Skill.SHADOWING)
        assert len(skill_reports) == (decision in ('success', 'failure'))
        if skill_reports:
            assert skill_reports[0].player is opponent
            assert skill_reports[0].rolls[0].get_sum() == sum(shadow_dice)
        assert type(game.get_procedure()) is (BlitzAction if blitz else MoveAction)
        assert game.actor is game.get_team_agent(player.team)
        assert game.state.current_team is player.team
        assert game.state.active_player is player
        assert game.is_action_allowed(Action(ActionType.END_PLAYER_TURN))
        game.step(Action(ActionType.END_PLAYER_TURN))
        assert isinstance(game.get_procedure(), Turn)
        assert len(reports(game, OutcomeType.END_PLAYER_TURN)) == 1
        assert not reports(game, OutcomeType.TURNOVER)


@pytest.mark.parametrize('failure', ['gfi', 'dodge'])
@pytest.mark.parametrize('accept', [False, True])
def test_failed_real_movement_shadows_before_fall_and_ball_bounce(failure, accept):
    game, player, opponent = shadowing_game(gfi=failure == 'gfi')
    ball = game.get_ball()
    ball.move_to(player.position)
    ball.is_carried = True
    destination = Square(4, 5)
    with only_fixed_rolls(game, d6=[1]):
        game.step(Action(ActionType.MOVE, position=destination))
        assert_squares(game, player, opponent, destination, Square(6, 5))
        assert_shadowing_decision(game, player, opponent)
        assert player.state.up  # Shadowing resolves before the queued KnockDown.
        assert ball.is_carried and ball.position == destination
        assert [r.outcome_type for r in reports(game)] == [
            OutcomeType.FAILED_GFI if failure == 'gfi' else OutcomeType.FAILED_DODGE]
    with only_fixed_rolls(game, d6=([2, 2] if accept else []) + [1, 1], d8=[4]):
        game.step(Action(ActionType.USE_SKILL if accept else ActionType.DONT_USE_SKILL,
                         player=opponent))
        assert_squares(game, player, opponent, destination,
                       Square(5, 5) if accept else Square(6, 5))
        assert not player.state.up
        assert not ball.is_carried and ball.position == Square(3, 5)
        assert [r.outcome_type for r in reports(game)][1:] == (
            [OutcomeType.SKILL_USED] if accept else []) + [
            OutcomeType.KNOCKED_DOWN, OutcomeType.FUMBLE, OutcomeType.ARMOR_NOT_BROKEN,
            OutcomeType.BALL_BOUNCED, OutcomeType.BALL_BOUNCE_GROUND,
            OutcomeType.TURNOVER, OutcomeType.TURN_START, OutcomeType.STUNNED_TURNED]
        assert isinstance(game.get_procedure(), Turn)
        assert game.actor is game.get_team_agent(opponent.team)
        assert game.state.current_team is opponent.team
        assert game.is_action_allowed(Action(ActionType.START_MOVE, player=opponent))


def test_movement_gfi_reroll_shadows_once_before_pickup():
    game, player, opponent = shadowing_game(gfi=True)
    player.team.state.rerolls = 1
    destination = Square(4, 5)
    ball = game.get_ball()
    ball.move_to(destination)
    ball.is_carried = False
    with only_fixed_rolls(game, d6=[1]):
        game.step(Action(ActionType.MOVE, position=destination))
        assert isinstance(game.get_procedure(), Reroll)
        assert_squares(game, player, opponent, Square(5, 5), Square(6, 5))
        assert game.actor is game.get_team_agent(player.team)
    with only_fixed_rolls(game, d6=[2, 6]):
        game.step(Action(ActionType.USE_REROLL))
        assert_shadowing_decision(game, player, opponent)
        assert player.position == destination
        assert not ball.is_carried
        assert [r.outcome_type for r in reports(game)] == [
            OutcomeType.FAILED_GFI, OutcomeType.REROLL_USED,
            OutcomeType.SUCCESSFUL_GFI, OutcomeType.SUCCESSFUL_DODGE]
    with only_fixed_rolls(game, d6=[2, 2, 6]):
        game.step(Action(ActionType.USE_SKILL, player=opponent))
        assert len(reports(game, skill=Skill.SHADOWING)) == 1
        assert reports(game)[-1].outcome_type == OutcomeType.SUCCESSFUL_PICKUP
        assert ball.is_carried and ball.position == destination
        assert game.actor is game.get_team_agent(player.team)
        assert isinstance(game.get_procedure(), MoveAction)
        assert game.is_action_allowed(Action(ActionType.END_PLAYER_TURN))
