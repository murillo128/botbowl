from tests.util import *
import pytest


def test_stab_success_kill():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0
    attacker, defender = get_block_players(game, team)
    attacker.role.skills = [Skill.STAB]
    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.step(Action(ActionType.STAB, position=defender.position))
    assert game.has_report_of_type(OutcomeType.DEAD)


def test_stab_blitz():
    game = get_game_turn()
    game.config.pathfinding_enabled = True
    team = game.get_agent_team(game.actor)
    other_team = game.get_opp_team(team)
    game.clear_board()
    stabber = team.players[0]
    stabber.role.skills = [Skill.STAB]
    defender = other_team.players[0]
    defender.role.skills = []
    game.put(stabber, Square(1, 1))
    game.put(defender, Square(3, 3))
    game.step(Action(ActionType.START_BLITZ, player=stabber))
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.dice.fix(D6, 6)
    game.step(Action(ActionType.STAB, position=defender.position))
    assert game.has_report_of_type(OutcomeType.DEAD)
    assert game.has_report_of_type(OutcomeType.END_PLAYER_TURN)


@pytest.mark.parametrize('blitz', [False, True])
@pytest.mark.parametrize('attempt', [1, 2])
@pytest.mark.parametrize('armour', [7, 8, 9])
def test_frenzy_stab_armour_and_single_activation(blitz, attempt, armour):
    from tests.game.test_frenzy import frenzy_game, first_frenzy_push, attack_reports
    from tests.game.test_push import assert_board_references
    game, attacker, defender = frenzy_game(blitz)
    assert defender.get_av() == 8
    if attempt == 2:
        with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
            first_frenzy_push(game, defender)
    # Mighty Blow must modify neither Stab armour nor injury (7 stays stunned).
    attacker.extra_skills.append(Skill.MIGHTY_BLOW)
    with only_fixed_rolls(game, d6=[4, armour - 4] + ([3, 4] if armour > 8 else [])):
        game.step(Action(ActionType.STAB, position=defender.position))
        assert defender.state.up == (armour <= 8)
        assert defender.state.stunned == (armour > 8)
        assert defender.position == Square(5 + attempt, 8)
        assert attacker.position == Square(4 + attempt, 8)
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)) == 1
        assert len(attack_reports(game, OutcomeType.BLOCK_ROLL)) == attempt - 1
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.FRENZY)) == attempt - 1
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1
        assert isinstance(game.get_procedure(), Turn)
        assert attacker.state.used
        assert game.state.active_player is None
        assert_board_references(game)
        with pytest.raises(InvalidActionError):
            game.step(Action(ActionType.START_BLOCK, player=attacker))
        with pytest.raises(InvalidActionError):
            game.step(Action(ActionType.STAB, position=defender.position))


@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('pathfinding', [False, True])
def test_initial_frenzy_stab_blitz_gfi(failure, pathfinding):
    from tests.game.test_frenzy import frenzy_game, attack_reports
    game, attacker, defender = frenzy_game(blitz=True, moves=6)
    game.config.pathfinding_enabled = pathfinding
    game.set_available_actions()
    # Failure consumes GFI + fall armour; success consumes GFI + Stab armour.
    with only_fixed_rolls(game, d6=[1 if failure else 2, 1, 1]):
        game.step(Action(ActionType.STAB, position=defender.position))
        assert attacker.state.up is not failure
        assert defender.state.up
        assert game.has_report_of_type(OutcomeType.FAILED_GFI if failure else OutcomeType.SUCCESSFUL_GFI)
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)) == (not failure)
        assert not attack_reports(game, OutcomeType.BLOCK_ROLL)
        assert not attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.FRENZY)
        assert game.state.active_player is None
        assert isinstance(game.get_procedure(), Turn)
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == (not failure)


@pytest.mark.parametrize('attempt', [1, 2])
@pytest.mark.parametrize('skill,injury,expected', [
    (Skill.THICK_SKULL, 8, OutcomeType.KNOCKED_OUT),
    (Skill.STUNTY, 7, OutcomeType.STUNNED),
    (None, 7, OutcomeType.STUNNED),
])
def test_frenzy_stab_injury_ignores_modifiers(attempt, skill, injury, expected):
    from tests.game.test_frenzy import frenzy_game, first_frenzy_push, attack_reports
    game, attacker, defender = frenzy_game(blitz=True)
    if attempt == 2:
        with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
            first_frenzy_push(game, defender)
    if skill is not None:
        defender.extra_skills.append(skill)
    else:
        defender.injuries.append(CasualtyEffect.NIGGLING)
    with only_fixed_rolls(game, d6=[6, 6, 4, injury - 4]):
        game.step(Action(ActionType.STAB, position=defender.position))
        reports = attack_reports(game, expected, defender)
        assert len(reports) == 1
        assert reports[0].rolls[0].modifiers == 0
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1


@pytest.mark.parametrize('attempt', [1, 2])
@pytest.mark.parametrize('revolted', [False, True])
def test_frenzy_stab_foul_appearance(attempt, revolted):
    from tests.game.test_frenzy import frenzy_game, first_frenzy_push, attack_reports
    game, attacker, defender = frenzy_game(blitz=True, defender_skills=[Skill.FOUL_APPEARANCE])
    if attempt == 2:
        with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH], d6=[2]):
            first_frenzy_push(game, defender)
    with only_fixed_rolls(game, d6=[1] if revolted else [2, 1, 1]):
        game.step(Action(ActionType.STAB, position=defender.position))
        assert defender.state.up
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)) == (not revolted)
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1
        assert not game.has_report_of_type(OutcomeType.TURNOVER)


@pytest.mark.parametrize('attempt', [1, 2])
def test_frenzy_stab_stakes_is_the_only_armour_modifier(attempt):
    from tests.game.test_frenzy import frenzy_game, first_frenzy_push, attack_reports
    game, attacker, defender = frenzy_game(blitz=True)
    if attempt == 2:
        with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
            first_frenzy_push(game, defender)
    attacker.extra_skills.append(Skill.STAKES)
    defender.team.race = 'Undead'
    game.set_available_actions()
    if attempt == 2:
        assert next(a for a in game.get_available_actions() if a.action_type == ActionType.STAB).rolls == [[8]]
    with only_fixed_rolls(game, d6=[4, 4, 1, 1]):
        game.step(Action(ActionType.STAB, position=defender.position))
        assert defender.state.stunned
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAKES)) == 1
        assert len(attack_reports(game, OutcomeType.SKILL_USED, attacker, Skill.STAB)) == 1


@pytest.mark.parametrize('attempt', [1, 2])
def test_frenzy_stab_casualty_releases_ball_and_ends_once(attempt):
    from tests.game.test_frenzy import frenzy_game, first_frenzy_push, attack_reports
    from tests.game.test_push import assert_board_references
    game, attacker, defender = frenzy_game(blitz=True)
    if attempt == 2:
        with only_fixed_rolls(game, block_dice=[BBDieResult.PUSH]):
            first_frenzy_push(game, defender)
    ball = game.get_ball()
    ball.move_to(defender.position)
    ball.is_carried = True
    with only_fixed_rolls(game, d6=[6] * 5, d8=[2, 2]):
        game.step(Action(ActionType.STAB, position=defender.position))
        assert len(attack_reports(game, OutcomeType.DEAD, defender)) == 1
        assert defender.position is None
        assert ball.position == Square(5 + attempt, 7)
        assert not ball.is_carried
        assert_board_references(game)
        assert len(attack_reports(game, OutcomeType.END_PLAYER_TURN, attacker)) == 1
        assert isinstance(game.get_procedure(), Turn)
