from tests.util import *
import pytest

from tests.game.casualty_helpers import assert_location, assert_rewards, injure, injury_game


def test_casualty():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the casualty roll #1 (Gouged Eye / MNG)
    game.dice.fix(D6, 4)
    game.dice.fix(D8, 3)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert defender.state.injuries_gained[0] is CasualtyEffect.MNG

    assert not game.has_report_of_type(OutcomeType.SUCCESSFUL_REGENERATION)
    assert not game.has_report_of_type(OutcomeType.FAILED_REGENERATION)


def test_casualty_regeneration_success():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    defender.extra_skills.append(Skill.REGENERATION)
    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # add a value for casualty effect
    game.dice.fix(D6, 3)
    # fix the regeneration roll
    game.dice.fix(D6, 4)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert game.has_report_of_type(OutcomeType.SUCCESSFUL_REGENERATION)
    assert defender in game.get_reserves(defender.team)
    assert not defender.state.injuries_gained


def test_casualty_regeneration_fail():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    defender.extra_skills.append(Skill.REGENERATION)
    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # add a value for casualty effect
    game.dice.fix(D6, 4)
    # fix the regeneration roll
    game.dice.fix(D6, 3)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert game.has_report_of_type(OutcomeType.FAILED_REGENERATION)
    assert defender not in game.get_reserves(defender.team)
    assert defender.state.injuries_gained


def test_casualty_with_decay():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    defender.extra_skills.append(Skill.DECAY)
    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the casualty roll #1 (Gouged Eye / MNG)
    game.dice.fix(D6, 4)
    game.dice.fix(D8, 3)
    # fix the casualty roll #2 (BH / none)
    game.dice.fix(D6, 3)
    game.dice.fix(D8, 1)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert defender.state.injuries_gained[0] is CasualtyEffect.MNG
    assert game.has_report_of_type(OutcomeType.MISS_NEXT_GAME)
    assert len(defender.state.injuries_gained) == 1
    assert game.has_report_of_type(OutcomeType.BADLY_HURT)


def test_casualty_with_decay_mng_twice_is_just_one():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    defender.extra_skills.append(Skill.DECAY)
    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the casualty roll #1 (Gouged Eye / MNG)
    game.dice.fix(D6, 4)
    game.dice.fix(D8, 3)
    # fix the casualty roll #2 (BH / none)
    game.dice.fix(D6, 4)
    game.dice.fix(D8, 4)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert defender.state.injuries_gained[0] is CasualtyEffect.MNG
    assert game.has_report_of_type(OutcomeType.MISS_NEXT_GAME)
    assert len(defender.state.injuries_gained) == 1


def test_casualty_decay_regeneration_success():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    defender.extra_skills.append(Skill.REGENERATION)
    defender.extra_skills.append(Skill.DECAY)

    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # add a value for casualty effect
    game.dice.fix(D6, 3)
    # fix the regeneration roll
    game.dice.fix(D6, 4)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert game.has_report_of_type(OutcomeType.SUCCESSFUL_REGENERATION)
    assert defender in game.get_reserves(defender.team)
    assert len(defender.state.injuries_gained) == 0


def test_casualty_regeneration_failure():
    game = get_game_turn()
    team = game.get_agent_team(game.actor)
    team.state.rerolls = 0

    attacker, defender = get_block_players(game, team)
    attacker.extra_st = defender.get_st() - attacker.get_st() + 1  # make this a 2 die block.
    attacker.extra_skills.append(Skill.BLOCK)
    defender_pos = Square(defender.position.x, defender.position.y)
    defender.extra_skills.append(Skill.REGENERATION)
    defender.extra_skills.append(Skill.DECAY)

    # it's a 2 dice block
    game.dice.clear(BBDie)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.fix(BBDie, BBDieResult.BOTH_DOWN)
    game.dice.clear(D6)
    # fix the armour roll
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # fix the injury roll to casualty
    game.dice.fix(D6, 5)
    game.dice.fix(D6, 5)
    # add a value for casualty effect - BH
    game.dice.fix(D6, 3)
    # fix the regeneration roll
    game.dice.fix(D6, 2)
    # add a value for casualty effect #2 - DEAD
    game.dice.fix(D6, 6)

    game.step(Action(ActionType.START_BLOCK, player=attacker))
    game.step(Action(ActionType.BLOCK, position=defender.position))
    game.step(Action(ActionType.SELECT_BOTH_DOWN))

    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert game.has_report_of_type(OutcomeType.FAILED_REGENERATION)
    assert not defender in game.get_reserves(defender.team)
    assert game.has_report_of_type(OutcomeType.BADLY_HURT)
    assert game.has_report_of_type(OutcomeType.DEAD)
    assert len(defender.state.injuries_gained) == 1


@pytest.mark.parametrize('regeneration', [None, 3, 4])
@pytest.mark.parametrize('decay', [False, True])
def test_one_casualty_credit_with_recovery_and_decay(regeneration, decay):
    skills = ([Skill.REGENERATION] if regeneration else []) + ([Skill.DECAY] if decay else [])
    game, attacker, victim = injury_game(skills=skills)
    d6 = [5, 5, 4] + ([regeneration] if regeneration else [])
    d8 = [3]
    recovered = regeneration == 4
    if decay and not recovered:
        d6 += [6]
        d8 += [8]
    with game.dice.force(d6=d6, d8=d8, strict=True):
        injure(game, victim, attacker)
    assert sum(r.outcome_type is OutcomeType.CASUALTY for r in game.state.reports) == 1
    expected = [OutcomeType.INJURY_CASUALTY, OutcomeType.CASUALTY]
    if regeneration:
        expected += [OutcomeType.SUCCESSFUL_REGENERATION if recovered else OutcomeType.FAILED_REGENERATION]
    if not recovered:
        expected += [OutcomeType.MISS_NEXT_GAME]
        if decay:
            expected += [OutcomeType.DECAYING, OutcomeType.DEAD]
    assert [r.outcome_type for r in game.state.reports] == expected
    assert victim.state.injuries_gained == ([] if recovered else
                                           [CasualtyEffect.MNG] + ([CasualtyEffect.DEAD] if decay else []))
    assert victim.team.state.apothecaries == 0
    assert_location(game, victim, 'reserves' if recovered else 'casualties')
    casualty = game.state.reports[1]
    assert casualty.player is victim and casualty.opp_player is attacker
    assert casualty.team is victim.team
    assert casualty.rolls[0].get_sum() == 43
    if decay and not recovered:
        decay_report = next(r for r in game.state.reports if r.outcome_type is OutcomeType.DECAYING)
        assert decay_report.rolls[0].get_sum() == 68
        assert decay_report.n == 'DEAD'
        assert decay_report.opp_player is attacker
    assert_rewards(game, victim, 0.5)


def test_reward_does_not_credit_apothecary_decision_or_old_events():
    from examples.a2c.a2c_env import A2C_Reward

    game, attacker, victim = injury_game(apothecaries=1, own=True)
    reward = A2C_Reward()
    assert reward(game) == 0.0
    with game.dice.force(d6=[5, 5, 4, 6], d8=[3, 8], strict=True):
        injure(game, victim, attacker)
        assert reward(game) == -0.5
        assert reward(game) == 0.0
        game.step(Action(ActionType.USE_APOTHECARY))
        assert reward(game) == 0.0
        game.step(Action(ActionType.SELECT_FIRST_ROLL))
        assert reward(game) == 0.0
    # A later casualty is new credit, even on the same player's team.
    other = next(p for p in victim.team.players if p is not victim)
    game.reserves_to_pitch(other, game.get_square(7, 5))
    with game.dice.force(d6=[5, 5, 4], d8=[3], strict=True):
        injure(game, other, attacker)
    assert reward(game) == -0.5
    assert reward(game) == 0.0
