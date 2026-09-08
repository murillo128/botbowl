"""Bounded #14 forced-roll conformance at final launch/selected-face boundaries.

Only tests drive procedures; query implementations never instantiate or step
them. Stop before catch/scatter/push/armour/ball-continuation resolution.
"""
import pytest
import botbowl as bb
from tests.util import get_custom_game_turn
from tests.game.probability_oracles import PASS_ROWS
from tests.game.test_pass_outcome_probs import fixture
from tests.game.test_probability_queries import set_strength


def drive(root, request_team=True, final_action=None):
    game = root.game
    root.start()
    root.started = True
    for _ in range(40):
        proc = game.state.stack.peek()
        assert isinstance(proc, (type(root), bb.Reroll, bb.Loner)), type(proc)
        if not proc.started:
            proc.start()
            proc.started = True
        action = None
        if isinstance(proc, bb.Reroll) and proc.skill is None and proc.loner is None and proc.pro is None:
            if proc.can_use_pro:
                action = bb.Action(bb.ActionType.DONT_USE_SKILL)
            elif proc.can_use_team_reroll:
                action = bb.Action(bb.ActionType.USE_REROLL if request_team else bb.ActionType.DONT_USE_REROLL)
        elif proc is root and final_action is not None and root.roll is not None and root.reroll is None:
            action = bb.Action(final_action)
        done = proc.step(action)
        if proc is root and done:
            return
        if done:
            proc.end()
            proc.done = True
            assert game.state.stack.peek() is proc
            game.state.stack.pop()
    raise AssertionError('procedure did not reach final boundary')


def launch(game):
    mapping = {bb.OutcomeType.ACCURATE_PASS: 'A', bb.OutcomeType.INACCURATE_PASS: 'I', bb.OutcomeType.FUMBLE: 'F'}
    return [mapping[o.outcome_type] for o in game.state.reports if o.outcome_type in mapping][-1]


@pytest.mark.parametrize('row', PASS_ROWS)
def test_all_sixty_engine_pass_faces(row):
    observed = ''
    for raw in range(1, 7):
        game, p, target = fixture(row)
        game.external_control = True
        p.team.state.rerolls = 0
        result = game.get_pass_outcome_probs(p, game.get_ball(), target, reroll_policy='never')
        proc = bb.PassAttempt(game, p, game.get_ball(), target, result.pass_distance)
        with game.dice.force(d6=[raw], strict=True):
            drive(proc, False)
            assert not game.dice.pending(bb.D6)
        observed += launch(game)
    assert observed == row[5]


@pytest.mark.parametrize('skill,loner,rolls,expected,spent', [
    ('usable', False, [1, 1], 'F', 0),
    ('usable', False, [2, 1], 'F', 0),
    ('usable', False, [1, 2], 'I', 0),
    ('usable', False, [1, 6], 'A', 0),
    ('usable', True, [1, 6], 'A', 0),
    ('used', False, [1, 6], 'A', 1),
    ('absent', True, [1, 3], 'F', 1),
    ('absent', True, [2, 4, 6], 'A', 1),
    ('absent', True, [1, 4, 1], 'F', 1),
    ('absent', False, [1, 2], 'I', 1),
    ('usable', True, [6], 'A', 0),
])
def test_pass_precedence_replacement_and_loner_cost(skill, loner, rolls, expected, spent):
    game, p, target = fixture()
    game.external_control = True
    p.extra_skills = [bb.Skill.PRO] + ([bb.Skill.PASS] if skill != 'absent' else [])
    p.extra_skills += [bb.Skill.LONER] if loner else []
    if skill == 'used':
        p.state.used_skills.add(bb.Skill.PASS)
    p.team.state.rerolls = 2
    result = game.get_pass_outcome_probs(p, game.get_ball(), target, reroll_policy='pass_skill_then_team')
    assert result.reroll.source == ('pass' if skill == 'usable' else 'team')
    proc = bb.PassAttempt(game, p, game.get_ball(), target, result.pass_distance)
    with game.dice.force(d6=rolls, strict=True):
        drive(proc)
        assert not game.dice.pending(bb.D6)
    assert launch(game) == expected
    assert p.team.state.rerolls == 2 - spent
    assert p.team.state.reroll_used == bool(spent)
    assert not p.has_used_skill(bb.Skill.PRO)
    assert p.has_used_skill(bb.Skill.PASS) == (skill == 'used')


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('dice,block,initial,replacement,final,request_team,loner,gate,spent', [
    (2, False, ('ATTACKER_DOWN', 'BOTH_DOWN'), ('PUSH', 'PUSH'), 'PUSH', True, False, (), 1),
    (2, True, ('ATTACKER_DOWN', 'BOTH_DOWN'), (), 'BOTH_DOWN', False, False, (), 0),
    (-2, False, ('ATTACKER_DOWN', 'DEFENDER_DOWN'), ('PUSH', 'PUSH'), 'PUSH', True, False, (), 1),
    (-2, False, ('PUSH', 'BOTH_DOWN'), (), 'PUSH', False, False, (), 0),
    (1, False, ('ATTACKER_DOWN',), (), 'ATTACKER_DOWN', True, True, (3,), 1),
    (1, False, ('ATTACKER_DOWN',), ('DEFENDER_DOWN',), 'DEFENDER_DOWN', True, True, (4,), 1),
    (1, False, ('ATTACKER_DOWN',), ('ATTACKER_DOWN',), 'ATTACKER_DOWN', True, False, (), 1),
])
def test_block_attacker_funds_all_dice_and_no_third_attempt(
        reverse, dice, block, initial, replacement, final, request_team, loner, gate, spent):
    game, players = get_custom_game_turn([(5, 5)], [(6, 5)])
    a, d = players[::-1] if reverse else players
    game.state.current_team = a.team
    game.external_control = True
    a.extra_skills = [bb.Skill.PRO] + ([bb.Skill.BLOCK] if block else []) + ([bb.Skill.LONER] if loner else [])
    set_strength(a, 4 if dice == 2 else 3)
    set_strength(d, 4 if dice == -2 else 3)
    a.team.state.rerolls = d.team.state.rerolls = 2
    result = game.get_block_outcome_probs(a, d, reroll_policy='avoid_attacker_down')
    assert result.signed_dice == dice and result.reroll.source == 'team'
    proc = bb.Block(game, a, d)
    with game.dice.force(block_dice=[getattr(bb.BBDieResult, s) for s in initial + replacement],
                         d6=gate, strict=True):
        drive(proc, request_team, getattr(bb.ActionType, 'SELECT_' + final))
        assert not game.dice.pending(bb.BBDie) and not game.dice.pending(bb.D6)
    assert proc.selected_die == getattr(bb.BBDieResult, final)
    assert proc.favor is (a.team if dice > 0 else d.team)
    assert a.team.state.rerolls == 2 - spent and d.team.state.rerolls == 2
    assert a.team.state.reroll_used == bool(spent)
    assert not a.has_used_skill(bb.Skill.PRO)
    rolls = [o.rolls[0] for o in game.state.reports if o.outcome_type == bb.OutcomeType.BLOCK_ROLL]
    assert len(rolls) == (2 if replacement else 1)
    assert all(len(r.dice) == abs(dice) for r in rolls)
