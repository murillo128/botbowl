"""Exact conditional launch tables; legacy threshold estimates are separate."""
from fractions import Fraction as F
from itertools import product

import pytest
import botbowl as bb
from tests.util import get_custom_game_turn
from tests.game.probability_oracles import PASS_ROWS, pass_oracle


def fixture(row=PASS_ROWS[1], reverse=False):
    ag, offset, sunny, zones, accurate, *_ = row
    opponents = [(4, 4), (4, 5), (4, 6)][:zones]
    if reverse:
        game, players = get_custom_game_turn(opponents, [(5, 5)], ball_position=(5, 5))
        passer = players[-1]
        game.state.current_team = passer.team
    else:
        game, players = get_custom_game_turn([(5, 5)], opponents, ball_position=(5, 5))
        passer = players[0]
    for p in players:
        p.role.skills = []
        p.extra_skills = []
    passer.extra_ag += ag - passer.get_ag()
    passer.extra_skills = [bb.Skill.ACCURATE] if accurate else []
    game.state.weather = bb.WeatherType.VERY_SUNNY if sunny else bb.WeatherType.NICE
    return game, passer, game.get_square(5 + offset, 5)


def values(result):
    return result.accurate, result.inaccurate, result.fumble


def close(actual, expected):
    assert actual == pytest.approx(tuple(float(x) for x in expected), abs=1e-12, rel=0)


@pytest.mark.parametrize('row', PASS_ROWS, ids=['P' + str(i) for i in range(1, 11)])
@pytest.mark.parametrize('reverse', [False, True])
def test_pass_tables_and_resource_cross(row, reverse):
    assert pass_oracle(row[5])[0] == tuple(map(F, row[6]))
    assert pass_oracle(row[5], 'pass')[0] == tuple(map(F, row[7]))
    game, passer, target = fixture(row, reverse)
    initial_skills = list(passer.extra_skills)
    for policy, skill, team, loner, already in product(
            ('never', 'pass_skill', 'pass_skill_then_team'), ('absent', 'usable', 'used'),
            (False, True), (False, True), (False, True)):
        passer.extra_skills = initial_skills + ([bb.Skill.PASS] if skill != 'absent' else [])
        passer.extra_skills += [bb.Skill.LONER, bb.Skill.PRO] if loner else []
        passer.state.used_skills = {bb.Skill.PASS} if skill == 'used' else set()
        passer.team.state.rerolls = 3 if team else 0
        source = 'none'
        if policy != 'never' and not already:
            if skill == 'usable':
                source = 'pass'
            elif policy == 'pass_skill_then_team' and team:
                source = 'team'
        expected, replacement, use = pass_oracle(row[5], source, loner and source == 'team')
        result = game.get_pass_outcome_probs(passer, game.get_ball(), target,
                                            reroll_policy=policy, already_rerolled=already)
        close(values(result), expected)
        info = result.reroll
        assert (info.policy, info.already_rerolled, info.source) == (policy, already, source)
        assert (info.pass_skill_available, info.team_reroll_available) == (skill == 'usable', team)
        assert info.loner_success_probability == (0.5 if source == 'team' and loner else 1.0)
        close((info.replacement_probability, info.pass_use_probability, info.team_use_probability),
              (replacement, use if source == 'pass' else 0, use if source == 'team' else 0))
        if skill != 'used' and not loner:
            legacy = game.get_pass_prob(passer, game.get_ball(), target,
                                        policy != 'never' and not already,
                                        policy == 'pass_skill_then_team' and not already)
            assert result.accurate == pytest.approx(legacy, abs=1e-12, rel=0)


# Range, passer skills, weather, opponents (coordinate, Presence, upright), M, raw.
MODIFIERS = [
    (3, (), 'VERY_SUNNY', (), 0, 'FIIAAA'),
    (6, (), 'VERY_SUNNY', (), -1, 'FFIIAA'),
    (9, (), 'VERY_SUNNY', (), -2, 'FFFIIA'),
    (6, ('ACCURATE',), 'NICE', (), 1, 'FIAAAA'),
    (6, ('STRONG_ARM',), 'NICE', (), 1, 'FIAAAA'),
    (3, ('STRONG_ARM',), 'NICE', (), 1, 'FIAAAA'),
    (9, ('STRONG_ARM',), 'NICE', (), 0, 'FIIAAA'),
    (12, ('STRONG_ARM',), 'NICE', (), -1, 'FFIIAA'),
    (6, ('ACCURATE', 'STRONG_ARM'), 'NICE', (), 2, 'FAAAAA'),
    (6, ('STUNTY',), 'NICE', (), -1, 'FFIIAA'),
    (6, (), 'NICE', (((4, 4), False, True),), -1, 'FFIIAA'),
    (6, ('NERVES_OF_STEEL',), 'NICE', (((4, 4), False, True), ((4, 5), False, True)), 0, 'FIIAAA'),
    (6, ('NERVES_OF_STEEL',), 'VERY_SUNNY',
     (((4, 4), False, True), ((4, 5), False, True), ((5, 2), True, True)), -2, 'FFFIIA'),
    (6, (), 'NICE', (((5, 2), True, True),), -1, 'FFIIAA'),
    (6, (), 'NICE', (((5, 2), True, False),), -1, 'FFIIAA'),
    (6, (), 'NICE', (((5, 1), True, True),), 0, 'FIIAAA'),
    (6, (), 'NICE', (((5, 2), True, True), ((6, 2), True, True)), -2, 'FFFIIA'),
    (6, ('NERVES_OF_STEEL',), 'NICE', (((5, 2), True, True),), -1, 'FFIIAA'),
    (6, (), 'NICE', (((4, 5), False, False),), 0, 'FIIAAA'),
] + [(offset, (), weather, (), modifier, raw)
     for offset, modifier, raw in ((3, 1, 'FIAAAA'), (6, 0, 'FIIAAA'))
     for weather in ('POURING_RAIN', 'SWELTERING_HEAT', 'BLIZZARD')]


@pytest.mark.parametrize('offset,skills,weather,opponents,modifier,raw', MODIFIERS)
def test_real_modifier_geometry(offset, skills, weather, opponents, modifier, raw):
    game, players = get_custom_game_turn([(5, 5)], [p[0] for p in opponents], ball_position=(5, 5))
    passer = players[0]
    passer.extra_skills = [getattr(bb.Skill, s) for s in skills]
    for p, (_, presence, up) in zip(players[1:], opponents):
        # Duplicating the skill on a player must not double the penalty.
        p.extra_skills = [bb.Skill.DISTURBING_PRESENCE] * 2 if presence else []
        p.state.up = up
    game.state.weather = getattr(bb.WeatherType, weather)
    result = game.get_pass_outcome_probs(passer, game.get_ball(), game.get_square(5 + offset, 5))
    assert result.modifier == modifier
    close(values(result), pass_oracle(raw)[0])


def test_no_tackle_zone_and_effective_agility():
    game, (p, opponent) = get_custom_game_turn([(5, 5)], [(4, 5)], ball_position=(5, 5))
    opponent.state.bone_headed = True
    target = game.get_square(11, 5)
    assert game.get_pass_outcome_probs(p, game.get_ball(), target).modifier == 0
    for extra, injuries, expected in ((-20, [], 6), (20, [], 1),
                                       (3, [bb.CasualtyEffect.AG], 2)):
        p.extra_ag = extra
        p.injuries = injuries
        assert game.get_pass_outcome_probs(p, game.get_ball(), target).agility_target == expected


def test_legacy_limits_and_prospective_passer():
    game, p, target = fixture()
    p.extra_skills = [bb.Skill.PASS]
    p.state.used_skills.add(bb.Skill.PASS)
    assert game.get_pass_prob(p, game.get_ball(), target) == .75
    assert game.get_pass_outcome_probs(p, game.get_ball(), target).accurate == .5
    p.extra_skills = [bb.Skill.LONER]
    p.team.state.rerolls = 2
    assert game.get_pass_prob(p, game.get_ball(), target, False, True) == .75
    assert game.get_pass_outcome_probs(p, game.get_ball(), target,
                                       reroll_policy='pass_skill_then_team').accurate == .625
    p.extra_skills = [bb.Skill.SAFE_THROW]
    assert game.get_pass_prob(p, game.get_ball(), target) == .5
    assert game.get_pass_prob(p, p, target, False, False) == float(F(1, 3))
    p.extra_skills = [bb.Skill.HAIL_MARY_PASS]
    assert game.get_pass_outcome_probs(p, game.get_ball(), target).accurate == .5
    # Hail Mary's historical zero-modifier approximation still returns a float;
    # the new typed API rejects that range independently.
    assert game.get_pass_prob(p, game.get_ball(), game.get_square(19, 5)) == .5
    baseline = game.get_pass_outcome_probs(p, game.get_ball(), target)
    game.get_ball().is_carried = False
    game.get_ball().move_to(game.get_square(3, 3))
    for team in game.state.teams:
        occupant = next(x for x in team.players if x.position is None)
        game.put(occupant, target)
        assert game.get_pass_outcome_probs(p, game.get_ball(), target) == baseline
        game.remove(occupant)
