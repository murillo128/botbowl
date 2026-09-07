"""Independent finite-roll oracle and read-only query acceptance for issue #8."""
from fractions import Fraction
from itertools import product
import math
import pickle

import numpy as np
import pytest

import botbowl as bb
from tests.util import get_custom_game_turn


# Test-only symbols, deliberately independent of production enums/order statistics.
FACES = ('skull', 'both', 'push', 'push', 'stumble', 'pow')
TIE_ORDER = ('pow', 'stumble', 'push', 'both', 'skull')
STRENGTHS = {1: (3, 3), -1: (3, 3), 2: (4, 3), -2: (3, 4), 3: (7, 3), -3: (3, 7)}


def oracle(dice, a_block=False, d_block=False, dodge=False, tackle=False,
           strip=False, sure_hands=False, carrier=None, crowd=False):
    """Enumerate every roll, resolve each face, and choose by successive filters.

    No engine/query helper or production probability/ranking formula is used.
    Fractions count equally likely rolls; duplicated push faces stay duplicated.
    """
    def resolve(face):
        fallen = set()
        released = set()
        if face == 'skull':
            fallen.add('a')
        elif face == 'both':
            if not a_block:
                fallen.add('a')
            if not d_block:
                fallen.add('d')
        else:
            if crowd or face == 'pow' or (face == 'stumble' and (not dodge or tackle)):
                fallen.add('d')
            if strip and not sure_hands and carrier == 'd':
                released.add('d')
        if carrier in fallen:
            released.add(carrier)
        return fallen, released

    chooser, opponent = ('a', 'd') if dice > 0 else ('d', 'a')
    counts = [0, 0, 0, 0]
    rolls = 0
    for roll in product(FACES, repeat=abs(dice)):
        candidates = [(face, *resolve(face)) for face in roll]
        # One decision policy for the complete outcome, not separate per-event
        # optima. Keep a preference only if at least one candidate satisfies it.
        for preferred in (
            lambda c: chooser not in c[1],
            lambda c: opponent in c[1],
            lambda c: chooser not in c[2],
            lambda c: opponent in c[2],
        ):
            preferred_candidates = [c for c in candidates if preferred(c)]
            if preferred_candidates:
                candidates = preferred_candidates
        _, fallen, released = min(candidates, key=lambda c: TIE_ORDER.index(c[0]))
        counts[0] += 'a' in fallen
        counts[1] += 'd' in fallen
        counts[2] += 'a' in released
        counts[3] += 'd' in released
        rolls += 1
    assert rolls == 6 ** abs(dice)
    return tuple(Fraction(count, rolls) for count in counts)


def set_strength(player, strength):
    player.extra_st += strength - player.get_st()


def assert_probabilities(actual, expected):
    assert len(actual) == 4
    assert all(math.isfinite(p) and 0 <= p <= 1 for p in actual)
    assert actual == pytest.approx(tuple(float(p) for p in expected), abs=1e-15, rel=0)


@pytest.mark.parametrize('dice', [1, -1, 2, -2, 3, -3])
@pytest.mark.parametrize('carrier', [None, 'a', 'd'])
@pytest.mark.parametrize('geometry', ['interior', 'crowd', 'stand_firm', 'side_step'])
def test_block_against_enumerated_oracle(dice, carrier, geometry, monkeypatch):
    at_edge = geometry != 'interior'
    a_pos, d_pos = ((5, 2), (5, 1)) if at_edge else ((5, 5), (6, 5))
    ball_pos = {'a': a_pos, 'd': d_pos}.get(carrier)
    game, (attacker, defender) = get_custom_game_turn([a_pos], [d_pos], ball_position=ball_pos)
    for player, strength in zip((attacker, defender), STRENGTHS[dice]):
        set_strength(player, strength)
    if dice == -1:
        # The engine returns +1 at equal strength. A single die has no choice,
        # but exercise the negative selector contract as well.
        monkeypatch.setattr(game, 'num_block_dice_at', lambda *args, **kwargs: -1)
    assert game.num_block_dice(attacker, defender) == dice
    for flags in product((False, True), repeat=6):
        a_block, d_block, dodge, tackle, strip, sure_hands = flags
        attacker.extra_skills = [skill for skill, active in (
            (bb.Skill.BLOCK, a_block), (bb.Skill.TACKLE, tackle), (bb.Skill.STRIP_BALL, strip)) if active]
        defender.extra_skills = [skill for skill, active in (
            (bb.Skill.BLOCK, d_block), (bb.Skill.DODGE, dodge), (bb.Skill.SURE_HANDS, sure_hands),
            (bb.Skill.STAND_FIRM, geometry == 'stand_firm'),
            (bb.Skill.SIDE_STEP, geometry == 'side_step')) if active]
        expected = oracle(dice, *flags, carrier=carrier, crowd=geometry == 'crowd')
        actual = game.get_block_probs(attacker, defender)
        assert_probabilities(actual, expected)


def test_hand_counted_three_dice_and_correlated_both_down():
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)])
    attacker.extra_skills = [bb.Skill.BLOCK]
    set_strength(attacker, 7)
    assert game.get_block_probs(attacker, defender)[0] == 1 / 216
    attacker.extra_skills = []
    set_strength(attacker, 3)
    # One unskilled die: both down contributes to BOTH marginals.
    assert_probabilities(game.get_block_probs(attacker, defender), (Fraction(2, 6), Fraction(3, 6), 0, 0))
    set_strength(attacker, 4)
    # With 2 dice, the 4 all-skull/both rolls cause self-down; 3 of these
    # also down the defender. 20 rolls contain stumble/pow: 23/36 total.
    assert_probabilities(game.get_block_probs(attacker, defender), (Fraction(4, 36), Fraction(23, 36), 0, 0))
    set_strength(attacker, 3)
    set_strength(defender, 4)
    # Defender chooses skull if offered, push before both down otherwise.
    assert_probabilities(game.get_block_probs(attacker, defender), (Fraction(16, 36), Fraction(9, 36), 0, 0))


@pytest.mark.parametrize('edge', [False, True])
@pytest.mark.parametrize('grab', [False, True])
def test_surrounded_side_step_falls_back_to_legal_pushes(edge, grab):
    a_pos, d_pos = ((5, 2), (5, 1)) if edge else ((5, 6), (5, 5))
    game, (attacker, defender) = get_custom_game_turn([a_pos], [d_pos], ball_position=d_pos)
    defender.extra_skills = [bb.Skill.SIDE_STEP, bb.Skill.DODGE]
    attacker.extra_skills = [bb.Skill.BLOCK, bb.Skill.STRIP_BALL] + ([bb.Skill.GRAB] if grab else [])
    extras = iter(p for t in game.state.teams for p in t.players if p not in (attacker, defender))
    for square in game.get_adjacent_squares(defender.position):
        if game.get_player_at(square) is None:
            player = next(extras)
            game.put(player, square)
            player.state.up = False  # Occupy the square without adding assists.
    expected = {(4, 0), (5, 0), (6, 0)} if edge else {(4, 4), (5, 4), (6, 4)}
    destinations = game.get_push_squares(attacker.position, defender.position)
    assert {(s.x, s.y) for s in destinations} == expected
    assert_probabilities(game.get_block_probs(attacker, defender),
                         oracle(1, a_block=True, dodge=True, strip=True, carrier='d', crowd=edge))
    # Actual engine fallback advertises these same destinations. No new choice
    # is made by the query on behalf of a procedure or agent.
    game.state.active_player = attacker
    push = bb.Push(game, attacker, defender)
    assert push.step(None) is False
    assert {(s.x, s.y) for s in push.available_actions()[0].positions} == expected


@pytest.mark.parametrize('grab', [False, True])
def test_side_step_never_offers_crowd_when_in_bounds_escape_exists(grab):
    game, (attacker, defender) = get_custom_game_turn([(5, 2)], [(5, 1)])
    defender.extra_skills = [bb.Skill.SIDE_STEP]
    attacker.extra_skills = [bb.Skill.GRAB] if grab else []
    destinations = game.get_push_squares(attacker.position, defender.position)
    expected = {(4, 0), (5, 0), (6, 0)} if grab else {(4, 1), (6, 1), (4, 2), (6, 2)}
    assert {(s.x, s.y) for s in destinations} == expected
    assert_probabilities(game.get_block_probs(attacker, defender), oracle(1, crowd=grab))


@pytest.mark.parametrize('side_step', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_destination_preference_is_explicit_and_order_independent(side_step, reverse, monkeypatch):
    game, (attacker, defender) = get_custom_game_turn([(5, 2)], [(5, 1)])
    defender.extra_skills = [bb.Skill.SIDE_STEP] if side_step else []
    # Inject a mixed candidate list to distinguish preference from indexing;
    # legal ordinary/Side Step geometry normally filters one of these groups.
    squares = [game.get_square(4, 1), game.get_square(5, 0)]
    monkeypatch.setattr(game, '_get_push_squares_at', lambda *args, **kwargs: squares[::-1] if reverse else squares)
    assert_probabilities(game.get_block_probs(attacker, defender), oracle(1, crowd=not side_step))


def test_blitz_horns_assists_and_vacated_origin():
    game, (attacker, helper, defender) = get_custom_game_turn([(2, 5), (6, 6)], [(6, 5)], ball_position=(2, 5))
    attacker.extra_skills = [bb.Skill.BLOCK, bb.Skill.HORNS]
    set_strength(defender, 4)
    attack_position = game.get_square(5, 5)
    # 3 strength + Horns + one assist beats ST4; without Horns it is one die.
    assert game.num_block_dice_at(attacker, defender, attack_position) == 1
    assert game.num_block_dice_at(attacker, defender, attack_position, blitz=True) == 2
    assert_probabilities(game.get_blitz_probs(attacker, attack_position, defender),
                         oracle(2, a_block=True, carrier='a'))

    game, (attacker, defender) = get_custom_game_turn([(4, 1)], [(5, 1)])
    defender.extra_skills = [bb.Skill.SIDE_STEP]
    extras = iter(p for t in game.state.teams for p in t.players if p not in (attacker, defender))
    origin = game.get_square(5, 2)
    for square in game.get_adjacent_squares(defender.position):
        if square != origin and game.get_player_at(square) is None:
            p = next(extras)
            game.put(p, square)
            p.state.up = False
    assert game._get_push_squares_at(attacker, defender, origin) == [attacker.position]
    assert_probabilities(game.get_blitz_probs(attacker, origin, defender), oracle(1))


def test_grab_direct_choices_do_not_leak_into_chain_push_fallback():
    game, (pusher, defender) = get_custom_game_turn([(5, 6)], [(5, 5)])
    pusher.extra_skills = [bb.Skill.GRAB]
    game.state.active_player = pusher
    push = bb.Push(game, pusher, defender, chain=True)
    assert push.step(None) is False
    assert {(s.x, s.y) for s in push.available_actions()[0].positions} == {(4, 4), (5, 4), (6, 4)}

    game, (attacker, defender) = get_custom_game_turn([(5, 2)], [(5, 1)])
    attacker.extra_skills = [bb.Skill.GRAB]
    # Direct Grab offers empty in-bounds neighbours, so this block cannot surf.
    # The shared engine fallback must still expose ordinary crowd directions.
    assert all(s.out_of_bounds for s in game.get_push_squares(attacker.position, defender.position))
    assert_probabilities(game.get_block_probs(attacker, defender), oracle(1))


def test_loose_ball_is_not_a_carrier_and_stand_firm_does_not_stop_strip_ball():
    game, (attacker, defender) = get_custom_game_turn([(5, 2)], [(5, 1)], ball_position=(5, 1))
    attacker.extra_skills = [bb.Skill.BLOCK, bb.Skill.STRIP_BALL]
    defender.extra_skills = [bb.Skill.BLOCK, bb.Skill.DODGE, bb.Skill.STAND_FIRM]
    expected = oracle(1, a_block=True, d_block=True, dodge=True, strip=True, carrier='d')
    assert expected[1] == Fraction(1, 6)
    assert expected[3] == Fraction(4, 6)
    assert_probabilities(game.get_block_probs(attacker, defender), expected)
    game.get_ball().is_carried = False
    assert game.get_block_probs(attacker, defender)[2:] == (0, 0)
    game.get_ball().is_carried = True
    defender.extra_skills = [bb.Skill.BLOCK, bb.Skill.DODGE]
    defender.state.taken_root = True
    assert_probabilities(game.get_block_probs(attacker, defender), expected)


@pytest.mark.parametrize('dodge,tackle,allow_skill,allow_team', list(product((False, True), repeat=4)))
def test_dodge_from_uses_hypothetical_tackle_and_tail(dodge, tackle, allow_skill, allow_team):
    game, (player, opponent) = get_custom_game_turn([(2, 5)], [(5, 5)], rerolls=1)
    player.extra_skills = [bb.Skill.DODGE] if dodge else []
    opponent.extra_skills = [bb.Skill.PREHENSILE_TAIL] + ([bb.Skill.TACKLE] if tackle else [])
    player.team.state.rerolls = 1
    origin, destination = game.get_square(4, 5), game.get_square(3, 5)
    # AG3 to an unmarked square is 3+, modified to 4+ by the tail at origin.
    reroll = (allow_skill and dodge and not tackle) or allow_team
    successes = sum(first >= 4 or (reroll and second >= 4)
                    for first, second in product(range(1, 7), repeat=2))
    actual = game.get_dodge_prob_from(player, origin, destination, allow_skill, allow_team)
    assert math.isfinite(actual) and 0 <= actual <= 1
    assert actual == successes / 36
    assert game.get_dodge_prob(player, destination, allow_skill, allow_team) == 1


@pytest.mark.parametrize('rerolls,used,current,expected', [(0, False, True, .5), (1, True, True, .5),
                                                           (1, False, False, .5), (1, False, True, .75)])
def test_dodge_team_reroll_eligibility_is_preserved(rerolls, used, current, expected):
    game, (player, opponent) = get_custom_game_turn([(2, 5)], [(5, 5)])
    opponent.extra_skills = [bb.Skill.PREHENSILE_TAIL]
    player.team.state.rerolls = rerolls
    player.team.state.reroll_used = used
    if not current:
        game.state.current_team = opponent.team
    assert game.get_dodge_prob_from(player, game.get_square(4, 5), game.get_square(3, 5),
                                    allow_team_reroll=True) == expected


class QueryProbeError(RuntimeError):
    pass


def observable_snapshot(game):
    # Whole-object byte snapshot, independent of the known #20 compare helper.
    # Also retain identities so replacing live objects with equal copies fails.
    return (pickle.dumps(game, protocol=4), game.capture_rng_state(),
            tuple(id(x) for x in (game.state, game.state.pitch, game.state.pitch.board,
                                 game.trajectory, game.trajectory.action_log,
                                 game.home_agent, game.away_agent)),
            tuple(id(p.position) for t in game.state.teams for p in t.players),
            tuple(id(step) for step in game.trajectory.action_log))


@pytest.mark.parametrize('forward_model', [False, True])
@pytest.mark.parametrize('carrier', [None, 'a', 'd'])
@pytest.mark.parametrize('query', ['block', 'blitz', 'dodge', 'dodge_from', 'push'])
@pytest.mark.parametrize('fail', [False, True])
def test_queries_are_pure_even_during_exceptions(forward_model, carrier, query, fail, monkeypatch):
    ball_pos = {'a': (5, 5), 'd': (6, 5)}.get(carrier)
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)], ball_position=ball_pos,
                                                     forward_model_enabled=forward_model)
    attacker.extra_skills = [bb.Skill.DODGE, bb.Skill.BLOCK, bb.Skill.STRIP_BALL]
    defender.extra_skills = [bb.Skill.PREHENSILE_TAIL, bb.Skill.SIDE_STEP]
    # Preserve nonempty trajectory, Gaussian cache, all forced queues, and
    # independent external agent/policy RNG state, rather than only positions.
    game.rng.normal()
    game.home_agent.query_test_rng = np.random.RandomState(91)
    game.away_agent.query_test_rng = np.random.RandomState(92)
    for die, value in ((bb.D3, 2), (bb.D6, 4), (bb.D8, 7), (bb.BBDie, bb.BBDieResult.PUSH)):
        game.dice.fix(die, value)
    origin, destination = game.get_square(5, 6), game.get_square(4, 6)
    calls = {
        'block': lambda: game.get_block_probs(attacker, defender),
        'blitz': lambda: game.get_blitz_probs(attacker, origin, defender),
        'dodge': lambda: game.get_dodge_prob(attacker, destination, True, True),
        'dodge_from': lambda: game.get_dodge_prob_from(attacker, origin, destination, True, True),
        'push': lambda: game.get_push_squares(attacker.position, defender.position),
    }
    helper = 'get_dodge_modifiers' if query.startswith('dodge') else 'get_adjacent_squares'
    original = getattr(bb.Game, helper)
    with game.dice.force(d6=[2], strict=True):
        before = observable_snapshot(game)
        def probe(self, *args, **kwargs):
            # Check from INSIDE evaluation too: moving then restoring the live
            # board or disabling trajectory recording cannot pass this gate.
            assert observable_snapshot(game) == before
            if fail:
                raise QueryProbeError('induced query failure')
            return original(self, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(bb.Game, helper, probe)
            if fail:
                with pytest.raises(QueryProbeError, match='induced query failure'):
                    calls[query]()
            else:
                for _ in range(3):
                    calls[query]()
        assert observable_snapshot(game) == before


@pytest.mark.parametrize('forward_model', [False, True])
@pytest.mark.parametrize('query', ['block', 'blitz', 'dodge_from'])
def test_invalid_query_origin_preserves_state(forward_model, query):
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)], forward_model_enabled=forward_model)
    before = observable_snapshot(game)
    with pytest.raises(ValueError):
        if query == 'block':
            game.get_block_probs(attacker, attacker)
        elif query == 'blitz':
            game.get_blitz_probs(attacker, defender.position, defender)
        else:
            game.get_dodge_prob_from(attacker, defender.position, game.get_square(7, 5))
    assert observable_snapshot(game) == before
