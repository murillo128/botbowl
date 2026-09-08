"""Public ownership, validation, resource, metadata and in-query purity gates."""
from copy import deepcopy
import inspect
import math
import time
from typing import get_args, get_type_hints

import numpy as np
import pytest
import botbowl as bb
from tests.util import get_custom_game_turn
from tests.game.test_probability_queries import observable_snapshot, QueryProbeError


def query_fixture(kind, reverse=False, forward_model=False):
    game, players = get_custom_game_turn([(5, 5)], [(6, 5)], ball_position=(5, 5),
                                         forward_model_enabled=forward_model)
    a, d = players[::-1] if reverse else players
    game.state.current_team = a.team
    a.team.state.rerolls = 3
    a.extra_skills = [bb.Skill.PASS, bb.Skill.PRO, bb.Skill.LONER]
    target = game.get_square(11, 5)
    origin = game.get_square(5, 6)
    def query(**kwargs):
        if kind == 'pass':
            return game.get_pass_outcome_probs(a, game.get_ball(), target, **kwargs)
        if kind == 'block':
            return game.get_block_outcome_probs(a, d, **kwargs)
        if kind == 'blitz':
            return game.get_blitz_outcome_probs(a, origin, d, **kwargs)
        return game.get_pass_prob(a, game.get_ball(), target)
    return game, a, d, query


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz'])
@pytest.mark.parametrize('branch', ['none', 'zero', 'used', 'wrong_team', 'no_turn', 'quick_snap', 'already'])
def test_resource_eligibility_branches(kind, branch):
    game, a, d, query = query_fixture(kind)
    a.extra_skills = [bb.Skill.PRO, bb.Skill.LONER]
    d.team.state.rerolls = 3
    if branch == 'zero':
        a.team.state.rerolls = 0
    elif branch == 'used':
        a.team.state.reroll_used = True
    elif branch == 'wrong_team':
        game.state.current_team = d.team
    elif branch == 'no_turn':
        game.state.stack.items.clear()
    elif branch == 'quick_snap':
        game.current_turn().quick_snap = True
    policy = 'pass_skill_then_team' if kind == 'pass' else 'avoid_attacker_down'
    result = query(reroll_policy=policy, already_rerolled=branch == 'already')
    assert result.reroll.policy == policy
    assert result.reroll.team_reroll_available == (branch in ('none', 'already'))
    if branch == 'none':
        assert result.reroll.source == 'team'
        assert result.reroll.team_use_probability > 0
        assert result.reroll.replacement_probability == result.reroll.team_use_probability / 2
    else:
        never = query(reroll_policy='never')
        assert result[:-1] == never[:-1]
        assert result.reroll.source == 'none'
        assert result.reroll.replacement_probability == result.reroll.team_use_probability == 0
        assert result.reroll.loner_success_probability == 1


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz'])
def test_roll_history_is_explicit_and_integer_values_are_copied(kind, monkeypatch):
    game, a, d, query = query_fixture(kind)
    before = query()
    game.state.rerolled_procs.add(game.current_turn())
    assert query() == before
    if kind == 'pass':
        monkeypatch.setattr(bb.Game, 'get_pass_modifiers', lambda *a, **kw: np.int64(-1))
    else:
        monkeypatch.setattr(bb.Game, 'num_block_dice_at', lambda *a, **kw: np.int64(1))
    result = query()
    assert type(result[0]) is float
    assert type(result.modifier if kind == 'pass' else result.signed_dice) is int


def test_occupied_hypothetical_origin_and_missing_ruleset():
    game, a, d, query = query_fixture('blitz')
    blocker = next(p for p in a.team.players if p.position is None)
    game.put(blocker, game.get_square(5, 6))
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match='(?i)origin.*occupied'):
        query()
    assert observable_snapshot(game) == before
    game.ruleset = None
    with pytest.raises(ValueError, match='ruleset'):
        query()


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz', 'legacy_pass'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('forward_model', [False, True])
@pytest.mark.parametrize('failure', ['none', 'internal', 'public'])
def test_all_query_paths_are_pure(kind, reverse, forward_model, failure, monkeypatch):
    game, a, d, query = query_fixture(kind, reverse, forward_model)
    # Nonempty reversible history, board references, external RNGs and cache.
    a.state.moves += 1
    if forward_model:
        assert game.trajectory.action_log
    game.rng.normal()
    game.home_agent.query_test_rng = np.random.RandomState(91)
    game.away_agent.query_test_rng = np.random.RandomState(92)
    for die, value in ((bb.D3, 2), (bb.D6, 4), (bb.D8, 7), (bb.BBDie, bb.BBDieResult.PUSH)):
        game.dice.fix(die, value)
    helper = 'get_pass_modifiers' if 'pass' in kind else 'get_adjacent_squares'
    original = getattr(bb.Game, helper)
    natural = query()
    for populated in (False, True):
        with game.dice.force(d3=[1], d6=[2], d8=[8], block_dice=[bb.BBDieResult.DEFENDER_DOWN], strict=True):
            with game.dice.force(d3=[3] if populated else [], d6=[6] if populated else [],
                                 d8=[1] if populated else [], block_dice=[bb.BBDieResult.PUSH] if populated else [],
                                 strict=True):
                before = observable_snapshot(game)
                def probe(self, *args, **kwargs):
                    assert observable_snapshot(game) == before
                    if failure == 'internal':
                        raise QueryProbeError('induced query failure')
                    return original(self, *args, **kwargs)
                with monkeypatch.context() as patch:
                    patch.setattr(bb.Game, helper, probe)
                    if failure == 'internal':
                        with pytest.raises(QueryProbeError, match='induced query failure'):
                            query()
                    elif failure == 'public' and kind != 'legacy_pass':
                        with pytest.raises(ValueError, match='policy'):
                            query(reroll_policy='invalid')
                    else:
                        for _ in range(3):
                            assert query() == natural
                assert observable_snapshot(game) == before


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz'])
def test_metadata_signatures_exports_and_immutable_ownership(kind):
    game, a, d, query = query_fixture(kind)
    result = query()
    expected_type = bb.PassOutcomeProbabilities if kind == 'pass' else bb.BlockOutcomeProbabilities
    assert type(result) is expected_type and type(result.reroll) is bb.RerollProbabilityInfo
    assert set(get_type_hints(expected_type)) == set(result._fields)
    assert set(get_type_hints(bb.RerollProbabilityInfo)) == set(result.reroll._fields)
    assert get_args(bb.PassRerollPolicy) == ('never', 'pass_skill', 'pass_skill_then_team')
    assert get_args(bb.BlockRerollPolicy) == ('never', 'avoid_attacker_down')
    assert result.ruleset_id == 'BB2016'
    assert result.conditions == ('normal_ball_launch_reached_v1' if kind == 'pass' else 'single_block_direct_effects_v1')
    before = observable_snapshot(game)
    with pytest.raises(AttributeError):
        result.ruleset_id = 'changed'
    with pytest.raises(AttributeError):
        result.reroll.source = 'team'
    coordinates = result.from_position if kind == 'pass' else result.attack_position
    with pytest.raises(TypeError):
        coordinates[0] = 0
    assert observable_snapshot(game) == before
    def data_only(value):
        if isinstance(value, tuple):
            for item in value:
                data_only(item)
        else:
            assert type(value) in (str, bool, int, float, bb.PassDistance, bb.WeatherType)
            if type(value) is float:
                assert math.isfinite(value) and 0 <= value <= 1
    data_only(result)
    if kind == 'pass':
        assert result.from_position == (5, 5) and result.target_position == (11, 5)
        assert result.pass_distance == bb.PassDistance.SHORT_PASS
        assert result.weather == bb.WeatherType.NICE
        assert result.agility_target == 4 and result.modifier == -1  # adjacent opponent
    else:
        assert result.selection_policy == 'issue8_local_v1'
        assert result.reroll_team == 'attacker'
        assert result.blitz == (kind == 'blitz')
    method = getattr(bb.Game, 'get_' + kind + '_outcome_probs')
    signature = inspect.signature(method)
    assert signature.return_annotation is expected_type
    assert signature.parameters['reroll_policy'].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters['reroll_policy'].default == ('pass_skill' if kind == 'pass' else 'never')
    assert signature.parameters['already_rerolled'].default is False
    assert 'reroll_policy' not in inspect.signature(bb.Game.get_block_probs).parameters
    args = (a, game.get_ball(), game.get_square(11, 5)) if kind == 'pass' else (
        (a, game.get_square(5, 6), d) if kind == 'blitz' else (a, d))
    with pytest.raises(TypeError):
        method(game, *args, 'never')


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz'])
@pytest.mark.parametrize('policy', [None, 1, True, [], {}, 'Never', 'pass_skill', 'avoid_attacker_down'])
def test_invalid_policies(kind, policy):
    if policy == 'pass_skill' and kind == 'pass' or policy == 'avoid_attacker_down' and kind != 'pass':
        return
    game, a, d, query = query_fixture(kind)
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match='policy'):
        query(reroll_policy=policy)
    assert observable_snapshot(game) == before


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz'])
@pytest.mark.parametrize('history', [0, 1, None, 'False', [], np.bool_(True)])
def test_invalid_history(kind, history):
    game, a, d, query = query_fixture(kind)
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match='already_rerolled'):
        query(already_rerolled=history)
    assert observable_snapshot(game) == before


@pytest.mark.parametrize('kind', ['pass', 'block', 'blitz'])
@pytest.mark.parametrize('bad', ['config', 'rules', 'weather', 'foreign_team', 'unregistered', 'off_pitch',
                                'missing_position', 'float_coordinate', 'bool_coordinate', 'multiple_balls'])
def test_shared_invalid_domains(kind, bad):
    game, a, d, query = query_fixture(kind)
    category = {'config': 'ruleset', 'rules': 'ruleset', 'weather': 'weather',
                'foreign_team': 'player', 'unregistered': 'player', 'multiple_balls': 'ball'}.get(bad, 'origin')
    if bad == 'config':
        game.config.ruleset = 'BB2020'
    elif bad == 'rules':
        game.ruleset.name = None
    elif bad == 'weather':
        game.state.weather = 'NICE'
    elif bad == 'foreign_team':
        a.team = deepcopy(a.team)
    elif bad == 'unregistered':
        del game.state.player_by_id[a.player_id]
    elif bad == 'multiple_balls':
        game.state.pitch.balls.append(bb.Ball(a.position))
    elif bad == 'off_pitch':
        a.position = bb.Square(0, 5)
    elif bad == 'missing_position':
        a.position = None
    else:
        a.position = bb.Square(5.0 if bad == 'float_coordinate' else True, 5)
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match=category):
        query()
    assert observable_snapshot(game) == before


@pytest.mark.parametrize('bad', ['player', 'foreign_player', 'ball_kind', 'bomb', 'foreign_ball', 'missing_ball',
                                'target_kind', 'missing_target', 'target_float', 'target_bool', 'target_bounds',
                                'equal', 'safe_throw', 'hail_mary', 'blizzard_long', 'blizzard_bomb'])
def test_pass_invalid_domains(bad):
    game, a, d, _ = query_fixture('pass')
    player, ball, target = a, game.get_ball(), game.get_square(11, 5)
    category = 'target'
    if bad in ('player', 'foreign_player'):
        player = object() if bad == 'player' else deepcopy(a)
        category = 'player'
    elif bad in ('ball_kind', 'bomb', 'foreign_ball'):
        # Kind rejection needs no initialized bomb/procedure. The historical
        # Bomb constructor's missing Reversible initialization is outside #28.
        ball = a if bad == 'ball_kind' else (object.__new__(bb.Bomb) if bad == 'bomb' else deepcopy(ball))
        category = 'ball'
    elif bad == 'missing_ball':
        game.state.pitch.balls.clear()
        category = 'ball'
    elif bad == 'target_kind':
        target = (11, 5)
    elif bad == 'missing_target':
        target = None
    elif bad == 'target_float':
        target = bb.Square(11, float('nan'))
    elif bad == 'target_bool':
        target = bb.Square(11, True)
    elif bad == 'target_bounds':
        target = bb.Square(100, 5)
    elif bad == 'equal':
        target = a.position
    elif bad == 'safe_throw':
        a.extra_skills.append(bb.Skill.SAFE_THROW)
        category = 'Safe Throw'
    elif bad == 'hail_mary':
        target = game.get_square(19, 5)
        category = 'distance'
    else:
        target = game.get_square(14 if bad == 'blizzard_long' else 17, 5)
        game.state.weather = bb.WeatherType.BLIZZARD
        category = 'Blizzard'
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match=category):
        game.get_pass_outcome_probs(player, ball, target)
    assert observable_snapshot(game) == before


@pytest.mark.parametrize('kind', ['block', 'blitz'])
@pytest.mark.parametrize('bad', ['attacker_kind', 'defender_kind', 'foreign_defender', 'same', 'same_team',
                                'nonadjacent', 'occupied', 'missing_origin', 'bool_origin', 'juggernaut'])
def test_block_invalid_domains(kind, bad):
    game, a, d, _ = query_fixture(kind)
    origin = game.get_square(5, 6)
    category = 'player'
    if bad == 'attacker_kind':
        a = object()
    elif bad == 'defender_kind':
        d = object()
    elif bad == 'foreign_defender':
        d = deepcopy(d)
    elif bad == 'same':
        d = a
    elif bad == 'same_team':
        d = next(p for p in a.team.players if p.position is None)
        game.put(d, game.get_square(6, 6))
    elif bad == 'nonadjacent':
        game.move(d, game.get_square(15, 5))
        category = 'target'
    elif bad == 'juggernaut':
        a.extra_skills.append(bb.Skill.JUGGERNAUT)
        category = 'Juggernaut'
    else:
        origin = {'occupied': d.position, 'missing_origin': None, 'bool_origin': bb.Square(True, 5)}[bad]
        category = 'origin|target'
    if kind == 'block' and bad in ('occupied', 'missing_origin', 'bool_origin', 'juggernaut'):
        assert game.get_block_outcome_probs(a, d)  # Non-blitz Juggernaut is supported.
        return
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match='(?i)' + category):
        if kind == 'block':
            game.get_block_outcome_probs(a, d)
        else:
            game.get_blitz_outcome_probs(a, origin, d)
    assert observable_snapshot(game) == before


@pytest.mark.parametrize('kind', ['block', 'blitz'])
@pytest.mark.parametrize('dice', [True, False, 1.0, 0, 4, -4, None, float('inf'), '2'])
def test_invalid_dice_count(kind, dice, monkeypatch):
    game, a, d, query = query_fixture(kind)
    monkeypatch.setattr(bb.Game, 'num_block_dice_at', lambda *a, **kw: dice)
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match='dice'):
        query()
    assert observable_snapshot(game) == before


@pytest.mark.parametrize('input_name', ['target', 'modifier'])
@pytest.mark.parametrize('value', [True, 1.0, float('nan'), float('inf'), None, '2'])
def test_malformed_pass_arithmetic(input_name, value, monkeypatch):
    game, a, d, query = query_fixture('pass')
    if input_name == 'target':
        table = list(bb.Rules.agility_table)
        table[a.get_ag()] = value
        monkeypatch.setattr(bb.Rules, 'agility_table', table)
    else:
        monkeypatch.setattr(bb.Game, 'get_pass_modifiers', lambda *a, **kw: value)
    before = observable_snapshot(game)
    with pytest.raises(ValueError, match=input_name):
        query()
    assert observable_snapshot(game) == before


def test_bounded_query_performance_and_no_sampling(monkeypatch):
    fixtures = [query_fixture(kind) for kind in ('pass', 'block', 'blitz')]
    def forbidden(*args, **kwargs):
        raise AssertionError('probability query attempted a draw or engine mutation')
    for cls in (bb.D3, bb.D6, bb.D8, bb.BBDie):
        monkeypatch.setattr(cls, '__init__', forbidden)
    for name in ('step', 'move', 'put', 'report', 'set_available_actions', 'revert', 'forward'):
        monkeypatch.setattr(bb.Game, name, forbidden)
    start = time.monotonic()
    for kind, (game, a, d, query) in zip(('pass', 'block', 'blitz'), fixtures):
        a.extra_st += 10  # Exercise three-die selection without pair enumeration.
        policy = 'pass_skill_then_team' if kind == 'pass' else 'avoid_attacker_down'
        for _ in range(1000):
            query(reroll_policy=policy)
    assert time.monotonic() - start < 5.0
