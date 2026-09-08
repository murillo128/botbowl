"""Signed block tables: correlated marginals from one selected physical face."""
from fractions import Fraction as F

import pytest
import botbowl as bb
from tests.util import get_custom_game_turn
from tests.game.probability_oracles import BLOCK_ROWS, block_oracle, block_resolver, select
from tests.game.test_probability_queries import STRENGTHS, set_strength
from tests.game.test_pass_outcome_probs import close


def values(result):
    return (result.attacker_down, result.defender_down,
            result.attacker_ball_loss, result.defender_ball_loss)


@pytest.mark.parametrize('row', BLOCK_ROWS)
def test_signed_block_tables(row, monkeypatch):
    block, dice, counts, base_a, base_d, final_a, final_d = row
    base = block_oracle(dice, a_block=block)
    final = block_oracle(dice, reroll=True, a_block=block)
    assert base[0] == tuple(F(c, 6 ** abs(dice)) for c in counts)
    assert base[1] == (F(base_a), F(base_d), 0, 0)
    assert final[1] == (F(final_a), F(final_d), 0, 0)
    assert final[3] == F(base_a)
    for reverse in (False, True):
        game, players = get_custom_game_turn([(5, 5)], [(6, 5)])
        attacker, defender = players[::-1] if reverse else players
        game.state.current_team = attacker.team
        attacker.team.state.rerolls = 3
        attacker.extra_skills = [bb.Skill.BLOCK] if block else []
        for p, st in zip((attacker, defender), STRENGTHS[dice]):
            set_strength(p, st)
        if dice == -1:
            monkeypatch.setattr(game, 'num_block_dice_at', lambda *a, **kw: -1)
        for policy, expected in (('never', base), ('avoid_attacker_down', final)):
            result = game.get_block_outcome_probs(attacker, defender, reroll_policy=policy)
            close(result.selected_face_probabilities, expected[0])
            close(values(result), expected[1])
            close((result.reroll.replacement_probability, result.reroll.team_use_probability), expected[2:])
            assert result.reroll.pass_use_probability == 0
            assert result.reroll.pass_skill_available is False
            assert (result.signed_dice, result.chooser, result.reroll_team) == (
                dice, 'attacker' if dice > 0 else 'defender', 'attacker')
            assert result.reroll.team_reroll_available
            assert sum(result.selected_face_probabilities) == pytest.approx(1, abs=1e-12, rel=0)
            if policy == 'never':
                assert values(result) == game.get_block_probs(attacker, defender)


# A bounded pair corpus, separate from #8's much larger single-roll matrix.
PAIR_CASES = (
    (1, 'strip', False, False, 'd'),
    (1, 'sure_hands', False, False, 'd'),
    (-2, 'crowd', True, True, 'a'),
    (2, 'stand_firm', False, True, 'd'),
    (-2, 'root', True, False, 'd'),
    (2, 'side_escape', False, True, 'd'),
    (-2, 'side_surrounded', True, True, 'd'),
    (2, 'grab', True, False, 'd'),
    (-2, 'tackle', False, True, 'd'),
)


@pytest.mark.parametrize('dice,geometry,a_block,d_block,carrier', PAIR_CASES)
def test_correlated_skill_ball_geometry_pairs(dice, geometry, a_block, d_block, carrier):
    a_pos, d_pos = (5, 2), (5, 1)
    game, (a, d) = get_custom_game_turn([a_pos], [d_pos], ball_position=a_pos if carrier == 'a' else d_pos)
    a.extra_skills = [bb.Skill.STRIP_BALL] + ([bb.Skill.BLOCK] if a_block else [])
    d.extra_skills = [bb.Skill.BLOCK] if d_block else []
    if geometry == 'sure_hands':
        d.extra_skills += [bb.Skill.SURE_HANDS]
    if geometry == 'stand_firm':
        d.extra_skills += [bb.Skill.STAND_FIRM]
    if geometry == 'root':
        d.state.taken_root = True
    if geometry in ('side_escape', 'side_surrounded', 'grab'):
        d.extra_skills += [bb.Skill.SIDE_STEP]
    if geometry == 'grab':
        a.extra_skills += [bb.Skill.GRAB]
    if geometry == 'tackle':
        a.extra_skills += [bb.Skill.TACKLE]
    # Interior strip fixtures and Dodge-protected geometric pushes.
    if geometry in ('strip', 'sure_hands'):
        game.move(a, game.get_square(5, 5))
        game.move(d, game.get_square(6, 5))
    else:
        d.extra_skills += [bb.Skill.DODGE]
    if geometry == 'side_surrounded':
        extras = iter(p for t in game.state.teams for p in t.players if p not in (a, d))
        for square in game.get_adjacent_squares(d.position):
            if game.get_player_at(square) is None:
                p = next(extras)
                game.put(p, square)
                p.state.up = False
    for p, strength in zip((a, d), STRENGTHS[dice]):
        set_strength(p, strength)
    a.team.state.rerolls = 2
    kwargs = dict(a_block=a_block, d_block=d_block, dodge=geometry not in ('strip', 'sure_hands'),
                  tackle=geometry == 'tackle', strip=True, sure_hands=geometry == 'sure_hands',
                  carrier=carrier, crowd=geometry in ('crowd', 'side_surrounded', 'grab', 'tackle'))
    expected = block_oracle(dice, reroll=True, **kwargs)
    result = game.get_block_outcome_probs(a, d, reroll_policy='avoid_attacker_down')
    close(result.selected_face_probabilities, expected[0])
    close(values(result), expected[1])
    close((result.reroll.replacement_probability, result.reroll.team_use_probability), expected[2:])
    never = game.get_block_outcome_probs(a, d)
    assert values(never) == game.get_block_probs(a, d)
    if geometry == 'strip':
        close(values(never), (F(1, 3), F(1, 2), 0, F(5, 6)))
        close(values(result), (F(1, 9), F(1, 2), 0, F(17, 18)))
    # Every Loner marginal and face is the equally weighted retained/replaced
    # mixture; a separate one-die fixture explicitly enumerates all six gates.
    a.extra_skills += [bb.Skill.LONER]
    loner = game.get_block_outcome_probs(a, d, reroll_policy='avoid_attacker_down')
    close(values(loner), tuple((x + y) / 2 for x, y in zip(values(never), values(result))))
    close(loner.selected_face_probabilities,
          tuple((x + y) / 2 for x, y in zip(never.selected_face_probabilities, expected[0])))
    assert loner.reroll.team_use_probability == result.reroll.team_use_probability
    assert loner.reroll.replacement_probability == result.reroll.replacement_probability / 2


def test_loner_one_die_and_selected_tuple_trigger():
    game, (a, d) = get_custom_game_turn([(5, 5)], [(6, 5)])
    a.extra_skills = [bb.Skill.LONER, bb.Skill.PRO]
    a.team.state.rerolls = 2
    expected = block_oracle(1, reroll=True, loner=True)
    result = game.get_block_outcome_probs(a, d, reroll_policy='avoid_attacker_down')
    close(values(result), (F(2, 9), F(1, 2), 0, 0))
    close(result.selected_face_probabilities, expected[0])
    close((result.reroll.replacement_probability, result.reroll.team_use_probability), (F(1, 6), F(1, 3)))
    assert select(('skull', 'both'), 2, block_resolver())[0] == 'both'
    assert 'a' in select(('skull', 'both'), 2, block_resolver())[1]
    assert 'a' not in select(('skull', 'both'), 2, block_resolver(a_block=True))[1]
    assert select(('skull', 'pow'), -2, block_resolver())[0] == 'skull'
    assert select(('push', 'both'), -2, block_resolver())[0] == 'push'


def test_blitz_horns_vacated_carrier_and_conditional_skills():
    game, (a, helper, d) = get_custom_game_turn([(2, 5), (6, 6)], [(6, 5)], ball_position=(2, 5))
    a.extra_skills = [bb.Skill.BLOCK, bb.Skill.HORNS, bb.Skill.WRESTLE, bb.Skill.FRENZY,
                      bb.Skill.DAUNTLESS, bb.Skill.PRO]
    d.extra_skills = [bb.Skill.WRESTLE, bb.Skill.DUMP_OFF, bb.Skill.FOUL_APPEARANCE, bb.Skill.FEND]
    set_strength(d, 4)
    a.team.state.rerolls = 2
    origin = game.get_square(5, 5)
    expected = block_oracle(2, reroll=True, a_block=True, carrier='a')
    result = game.get_blitz_outcome_probs(a, origin, d, reroll_policy='avoid_attacker_down')
    close(values(result), expected[1])
    close(result.selected_face_probabilities, expected[0])
    assert result.blitz and result.signed_dice == 2
    assert result.attack_position == (5, 5)
    never = game.get_blitz_outcome_probs(a, origin, d)
    assert values(never) == game.get_blitz_probs(a, origin, d)
    # The old square is a legal Side Step escape in hypothetical occupancy.
    game, (a, d) = get_custom_game_turn([(4, 1)], [(5, 1)])
    d.extra_skills = [bb.Skill.SIDE_STEP]
    origin = game.get_square(5, 2)
    extras = iter(p for t in game.state.teams for p in t.players if p not in (a, d))
    for square in game.get_adjacent_squares(d.position):
        if square != origin and game.get_player_at(square) is None:
            p = next(extras)
            game.put(p, square)
            p.state.up = False
    assert values(game.get_blitz_outcome_probs(a, origin, d)) == game.get_blitz_probs(a, origin, d)
    # No ball is a valid block experiment; loose balls never imply release.
    game.state.pitch.balls.clear()
    assert values(game.get_blitz_outcome_probs(a, origin, d))[2:] == (0, 0)
