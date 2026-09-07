import pytest

from botbowl.core.game import InvalidActionError
from botbowl.core.model import Action
from botbowl.core.procedure import Apothecary, Turn
from botbowl.core.table import ActionType, BBDieResult, CasualtyEffect, OutcomeType, Skill
from tests.game.casualty_helpers import assert_location, assert_rewards, injure, injury_game
from tests.util import get_custom_game_turn


@pytest.mark.parametrize('own', [False, True])
@pytest.mark.parametrize('use', [False, True])
def test_apothecary_ko(use, own):
    game, attacker, victim = injury_game(apothecaries=2, own=own)
    position = victim.position
    with game.dice.force(d6=[4, 4], strict=True):
        injure(game, victim, attacker)
        assert isinstance(game.get_procedure(), Apothecary)
        game.step(Action(ActionType.USE_APOTHECARY if use else ActionType.DONT_USE_APOTHECARY))
    assert victim.team.state.apothecaries == 2 - int(use)
    assert victim.state.injuries_gained == []
    assert victim.state.knocked_out == (not use)
    assert victim.state.stunned == use
    assert_location(game, victim, 'pitch' if use else 'ko')
    if use:
        assert victim.position == position
        assert not victim.state.up
    assert [r.outcome_type for r in game.state.reports] == [
        OutcomeType.APOTHECARY_USED_KO if use else OutcomeType.KNOCKED_OUT]
    assert_rewards(game, victim, 0.0 if use else 0.2)
    assert all(clock.is_primary for clock in game.state.clocks)


@pytest.mark.parametrize('use', [False, True])
def test_apothecary_crowd_ko(use):
    game, (attacker, victim) = get_custom_game_turn([(2, 5)], [(1, 5)])
    for player in (attacker, victim):
        game.get_reserves(player.team).remove(player)
    victim.team.state.apothecaries = 2
    crowd_square = game.get_square(0, 5)
    with game.dice.force(block_dice=[BBDieResult.PUSH], d6=[4, 4], strict=True):
        game.step(Action(ActionType.START_BLOCK, player=attacker))
        game.step(Action(ActionType.BLOCK, position=victim.position))
        game.step(Action(ActionType.SELECT_PUSH))
        game.step(Action(ActionType.PUSH, position=crowd_square))
        game.step(Action(ActionType.FOLLOW_UP, position=attacker.position))
        assert isinstance(game.get_procedure(), Apothecary)
        assert victim.position == crowd_square
        game.step(Action(ActionType.USE_APOTHECARY if use else ActionType.DONT_USE_APOTHECARY))

    assert isinstance(game.get_procedure(), Turn)
    assert victim.team.state.apothecaries == 2 - int(use)
    assert_location(game, victim, 'reserves' if use else 'ko')
    assert game.get_player_at(crowd_square) is None
    assert victim not in game.get_players_on_pitch()
    assert victim.state.injuries_gained == []
    assert victim.state.knocked_out == (not use)
    assert not victim.state.stunned
    assert victim.state.up
    if use:
        assert not victim.state.used
    assert [r.outcome_type for r in game.state.reports] == [
        OutcomeType.BLOCK_ACTION_STARTED, OutcomeType.BLOCK_ROLL,
        OutcomeType.ACTION_SELECT_DIE, OutcomeType.PUSHED_INTO_CROWD,
        OutcomeType.KNOCKED_DOWN,
        OutcomeType.APOTHECARY_USED_KO if use else OutcomeType.KNOCKED_OUT,
        OutcomeType.END_PLAYER_TURN]
    recovery = game.state.reports[-2]
    assert recovery.player is victim and recovery.team is victim.team
    assert recovery.opp_player is None  # The crowd inflicts the injury.
    if not use:
        assert recovery.rolls[0].get_sum() == 8
    # The crowd knockdown earns 0.1; only an untreated KO adds 0.2.
    assert_rewards(game, victim, 0.1 + (0.0 if use else 0.2))
    assert all(clock.is_primary for clock in game.state.clocks)


@pytest.mark.parametrize('own', [False, True])
@pytest.mark.parametrize('choice,first,second,effect,location', [
    (ActionType.DONT_USE_APOTHECARY, (4, 3), None, CasualtyEffect.MNG, 'casualties'),
    (ActionType.SELECT_FIRST_ROLL, (4, 3), (6, 8), CasualtyEffect.MNG, 'casualties'),
    (ActionType.SELECT_SECOND_ROLL, (4, 3), (6, 8), CasualtyEffect.DEAD, 'casualties'),
    (ActionType.SELECT_FIRST_ROLL, (3, 8), (6, 8), CasualtyEffect.NONE, 'reserves'),
    (ActionType.SELECT_SECOND_ROLL, (6, 8), (3, 8), CasualtyEffect.NONE, 'reserves'),
    (ActionType.DONT_USE_APOTHECARY, (3, 8), None, CasualtyEffect.NONE, 'casualties'),
])
def test_apothecary_selected_casualty(choice, first, second, effect, location, own):
    game, attacker, victim = injury_game(apothecaries=2, own=own)
    use = second is not None
    d6 = [5, 5, first[0]] + ([second[0]] if use else [])
    d8 = [first[1]] + ([second[1]] if use else [])
    with game.dice.force(d6=d6, d8=d8, strict=True):
        injure(game, victim, attacker)
        assert [a.action_type for a in game.get_available_actions()] == [
            ActionType.USE_APOTHECARY, ActionType.DONT_USE_APOTHECARY]
        assert victim.state.injuries_gained == []
        assert_location(game, victim, 'pitch')
        if use:
            game.step(Action(ActionType.USE_APOTHECARY))
            proc = game.get_procedure()
            assert proc.roll_second.d68
            assert proc.roll_second.get_sum() == second[0] * 10 + second[1]
            assert [a.action_type for a in game.get_available_actions()] == [
                ActionType.SELECT_FIRST_ROLL, ActionType.SELECT_SECOND_ROLL]
        game.step(Action(choice))

    assert isinstance(game.get_procedure(), Turn)
    assert victim.team.state.apothecaries == 2 - int(use)
    assert victim.state.injuries_gained == ([] if effect is CasualtyEffect.NONE else [effect])
    assert_location(game, victim, location)
    selected = second if choice is ActionType.SELECT_SECOND_ROLL else first
    final_type = {CasualtyEffect.MNG: OutcomeType.MISS_NEXT_GAME,
                  CasualtyEffect.DEAD: OutcomeType.DEAD,
                  CasualtyEffect.NONE: OutcomeType.BADLY_HURT}[effect]
    expected = [OutcomeType.INJURY_CASUALTY, OutcomeType.CASUALTY]
    if use:
        expected += [OutcomeType.CASUALTY_APOTHECARY, OutcomeType.APOTHECARY_USED_CASUALTY]
    assert [r.outcome_type for r in game.state.reports] == expected + [final_type]
    final = game.state.reports[-1]
    assert final.player is victim and final.opp_player is attacker
    assert final.rolls[0].get_sum() == selected[0] * 10 + selected[1]
    assert_rewards(game, victim, 0.5)


def test_apothecary_rejects_actions_from_the_wrong_phase():
    game, attacker, victim = injury_game(apothecaries=2, own=True)
    with game.dice.force(d6=[5, 5, 4, 6], d8=[3, 8], strict=True):
        injure(game, victim, attacker)
        for use, invalid in [(False, ActionType.SELECT_SECOND_ROLL),
                             (True, ActionType.DONT_USE_APOTHECARY)]:
            if use:
                game.step(Action(ActionType.USE_APOTHECARY))
            reports = list(game.state.reports)
            rng = game.capture_rng_state()
            with pytest.raises(InvalidActionError):
                game.step(Action(invalid))
            assert game.state.reports == reports
            assert game.capture_rng_state() == rng
            assert victim.team.state.apothecaries == 2 - int(use)
        game.step(Action(ActionType.SELECT_FIRST_ROLL))
    assert victim.state.injuries_gained == [CasualtyEffect.MNG]
    assert victim.team.state.apothecaries == 1
    assert all(clock.is_primary for clock in game.state.clocks)


@pytest.mark.parametrize('choice', [ActionType.DONT_USE_APOTHECARY,
                                   ActionType.SELECT_FIRST_ROLL, ActionType.SELECT_SECOND_ROLL])
@pytest.mark.parametrize('regeneration', [3, 4])
@pytest.mark.parametrize('decay', [False, True])
def test_apothecary_regeneration_and_decay(choice, regeneration, decay):
    skills = [Skill.REGENERATION] + ([Skill.DECAY] if decay else [])
    game, attacker, victim = injury_game(apothecaries=1, skills=skills)
    use = choice is not ActionType.DONT_USE_APOTHECARY
    d6 = [5, 5, 4] + ([6] if use else []) + [regeneration]
    d8 = [3] + ([8] if use else [])
    if decay and regeneration < 4:
        d6 += [5]
        d8 += [3]  # Decay: MA reduction, with no second Regeneration roll.
    with game.dice.force(d6=d6, d8=d8, strict=True):
        injure(game, victim, attacker)
        if use:
            game.step(Action(ActionType.USE_APOTHECARY))
        game.step(Action(choice))
        if decay and regeneration < 4 and not use:
            # A declined resource is still available for the Decay result.
            game.step(Action(ActionType.DONT_USE_APOTHECARY))
    assert isinstance(game.get_procedure(), Turn)
    assert victim.team.state.apothecaries == 1 - int(use)
    expected = [OutcomeType.INJURY_CASUALTY, OutcomeType.CASUALTY]
    if use:
        expected += [OutcomeType.CASUALTY_APOTHECARY, OutcomeType.APOTHECARY_USED_CASUALTY]
    expected += [OutcomeType.SUCCESSFUL_REGENERATION if regeneration >= 4
                 else OutcomeType.FAILED_REGENERATION]
    injuries = []
    if regeneration < 4:
        second = choice is ActionType.SELECT_SECOND_ROLL
        expected += [OutcomeType.DEAD if second else OutcomeType.MISS_NEXT_GAME]
        injuries = [CasualtyEffect.DEAD if second else CasualtyEffect.MNG]
        if decay:
            expected += [OutcomeType.DECAYING, OutcomeType.MISS_NEXT_GAME]
            if CasualtyEffect.MNG not in injuries:
                injuries.append(CasualtyEffect.MNG)
            injuries.append(CasualtyEffect.MA)
    assert victim.state.injuries_gained == injuries
    assert_location(game, victim, 'reserves' if regeneration >= 4 else 'casualties')
    assert [r.outcome_type for r in game.state.reports] == expected
    assert_rewards(game, victim, 0.5)
