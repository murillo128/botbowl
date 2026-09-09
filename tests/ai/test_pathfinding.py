from more_itertools import first
from pytest import approx

from tests.util import Square, get_game_turn, Skill, Action, ActionType, get_custom_game_turn
from tests.baseline import scenario, SIZES
import pytest
import unittest.mock
import numpy as np
import botbowl.core.procedure
import pickle

import botbowl.core.pathfinding.python_pathfinding as python_pathfinding
# Keep native cases visible in Python-only jobs; a requested native job must
# additionally use --require-pathfinding=native so absence fails collection.
try:
    import botbowl.core.pathfinding.cython_pathfinding as cython_pathfinding
except ImportError:
    cython_pathfinding = None

requires_native = pytest.mark.skipif(
    cython_pathfinding is None, reason="Optional Cython extension is not installed")
pathfinding_modules_to_test = [
    pytest.param(python_pathfinding, id="python"),
    pytest.param(cython_pathfinding, id="native", marks=requires_native),
]

PROP_PRECISION = 0.000000001


def assert_path(path, steps, rolls, probability, handoff_roll=None):
    assert path is not None
    assert tuple((square.x, square.y) for square in path.steps) == tuple(steps)
    assert path.get_last_step() == Square(*steps[-1])
    assert path.rolls == tuple(rolls)
    assert path.handoff_roll == handoff_roll
    assert path.block_dice is None
    assert path.foul_roll is None
    assert path.prob == pytest.approx(probability, rel=0, abs=PROP_PRECISION)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("action_state", ["available", "unavailable", "started"])
@pytest.mark.parametrize("rain", [False, True])
@pytest.mark.parametrize("catch", [False, True])
def test_handoff_query(pf, action_state, rain, catch):
    # Upstream #234 (Mattias Bermell / mrbermell), expanded to check semantics.
    game, (carrier, receiver) = get_custom_game_turn(
        player_positions=[(2, 2), (5, 2)], ball_position=(2, 2),
        weather=botbowl.WeatherType.POURING_RAIN if rain else botbowl.WeatherType.NICE)
    if catch:
        receiver.extra_skills.append(Skill.CATCH)
    if action_state == "started":
        game.step(Action(ActionType.START_HANDOFF, player=carrier))
        assert not game.is_handoff_available()
        assert game.get_player_action_type() == botbowl.PlayerActionType.HANDOFF
    elif action_state == "unavailable":
        game.use_handoff_action()
    before = pickle.dumps(game)
    path = pf.get_safest_path(game, carrier, receiver.position)
    if action_state == "unavailable":
        assert path is None
    else:
        target = 4 if rain else 3
        assert_path(path, [(3, 2), (4, 2), (5, 2)], [[], [], []], 1.0, target)
        assert game.get_player_at(path.get_last_step()) is receiver
        # Path.prob excludes the terminal catch, as in docs/bots-ii.md.
        p_catch = (7 - target) / 6
        if catch:
            p_catch += (1 - p_catch) * p_catch
        assert game.get_catch_prob(receiver, handoff=True) == pytest.approx(
            p_catch, rel=0, abs=PROP_PRECISION)
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("invalid", ["opponent", "prone", "no_ball", "absent_ball", "loose_ball", "out_of_bounds"])
@pytest.mark.parametrize("started", [False, True])
def test_invalid_handoff_query(pf, invalid, started):
    game, (carrier, receiver, opponent) = get_custom_game_turn(
        player_positions=[(2, 2), (4, 2)], opp_player_positions=[(4, 4)],
        ball_position=(2, 2))
    if started:
        game.step(Action(ActionType.START_HANDOFF, player=carrier))
    target = receiver.position
    if invalid == "opponent":
        target = opponent.position
    elif invalid == "prone":
        receiver.state.up = False
    elif invalid == "no_ball":
        game.get_ball().move_to(Square(10, 10))
        game.get_ball().is_carried = False
    elif invalid == "loose_ball":
        game.get_ball().is_carried = False
    elif invalid == "absent_ball":
        game.state.pitch.balls.clear()
    else:
        target = Square(0, 2)
    before = pickle.dumps(game)
    assert pf.get_safest_path(game, carrier, target) is None
    paths = pf.Pathfinder(game, carrier, can_handoff=True).get_paths()
    assert all(path.get_last_step() != target for path in paths)
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("query", ["get_safest_path", "get_safest_path_to_endzone", "get_all_paths"])
@pytest.mark.parametrize("forward_model", [False, True])
@pytest.mark.parametrize("overrides", ["position", "moves", "both", "zero", "same", "default"])
def test_hypothetical_query_restores_state(pf, query, forward_model, overrides):
    game, (player,) = get_custom_game_turn(
        player_positions=[(5, 1)], ball_position=(5, 1),
        forward_model_enabled=forward_model)
    player.role.ma = 6
    player.state.moves = 2
    kwargs = {}
    if overrides in ("position", "both", "zero"):
        kwargs["from_position"] = Square(3, 1)
    elif overrides == "same":
        kwargs["from_position"] = player.position
    if overrides in ("moves", "both", "same"):
        kwargs["num_moves_used"] = 6
    elif overrides == "zero":
        kwargs["num_moves_used"] = 0
    origin_x = kwargs.get("from_position", player.position).x
    moves = kwargs.get("num_moves_used", 2)
    target = Square(1, 1) if query == "get_safest_path_to_endzone" else Square(origin_x - 1, 1)
    args = (target,) if query == "get_safest_path" else ()
    state = player.state
    ball = game.get_ball()
    log = game.trajectory.action_log
    before = pickle.dumps(game)
    result = getattr(pf, query)(game, player, *args, **kwargs)
    assert pickle.dumps(game) == before
    assert player.state is state and game.get_ball() is ball
    assert game.trajectory.action_log is log
    path = first(p for p in result if p.get_last_step() == target) if query == "get_all_paths" else result
    steps = [(x, 1) for x in range(origin_x - 1, target.x - 1, -1)]
    gfis = max(0, len(steps) - (6 - moves))
    if gfis > 2:
        assert path is None
        return
    rolls = [[]] * (len(steps) - gfis) + [[2]] * gfis
    assert_path(path, steps, rolls, (5 / 6) ** gfis)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("query", ["get_safest_path", "get_safest_path_to_endzone", "get_all_paths"])
@pytest.mark.parametrize("forward_model", [False, True])
def test_hypothetical_query_restores_after_exception(pf, query, forward_model, monkeypatch):
    game, (player,) = get_custom_game_turn(
        player_positions=[(5, 2)], ball_position=(5, 2),
        forward_model_enabled=forward_model)
    def fail(*args, **kwargs):
        assert player.position == Square(3, 2)
        assert player.state.moves == 1
        raise RuntimeError("injected pathfinding failure")
    before = pickle.dumps(game)
    state, ball = player.state, game.get_ball()
    args = (Square(2, 2),) if query == "get_safest_path" else ()
    with monkeypatch.context() as patch:
        patch.setattr(type(game), "get_players_on_pitch", fail)
        with pytest.raises(RuntimeError, match="injected pathfinding failure"):
            getattr(pf, query)(game, player, *args, from_position=Square(3, 2), num_moves_used=1)
    assert pickle.dumps(game) == before
    assert player.state is state and game.get_ball() is ball


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_neighbors(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(5, 5)
    game.put(player, position)
    for neighbor in game.get_adjacent_squares(position):
        path = pf.get_safest_path(game, player, neighbor)
        assert len(path) == 1 and path.steps[0] == neighbor
        assert path.prob == 1.0


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_out_of_bounds(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(1, 1)
    game.put(player, position)
    for neighbor in game.get_adjacent_squares(position):
        if game.is_out_of_bounds(neighbor):
            path = pf.get_safest_path(game, player, neighbor)
            assert path is None


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_gfi(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(5, 5)
    game.put(player, position)
    for gfis in [0, 1, 2]:
        moves = player.get_ma() + gfis
        target = Square(position.x + moves, position.y)
        path = pf.get_safest_path(game, player, target)
        assert len(path.steps) == moves and path.get_last_step() == target
        assert path.prob == (5 / 6) ** gfis


skills_and_rerolls_perms = [
    (True, True),
    (True, False),
    (False, True),
    (False, False)
]


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("skills_and_rerolls", skills_and_rerolls_perms)
def test_get_safest_path(skills_and_rerolls, pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(1, 1)
    game.put(player, position)
    skill, reroll = skills_and_rerolls
    if skill:
        player.extra_skills = [Skill.SURE_FEET]
    for y in range(game.arena.height):
        for x in range(game.arena.width):
            square = Square(x, y)
            if position != square and not game.is_out_of_bounds(square):
                if position.distance(square) != player.get_ma() + 2:
                    continue
                    # TODO: REMOVE
                path = pf.get_safest_path(game, player, square, allow_team_reroll=reroll)
                if position.distance(square) > player.get_ma() + 2:
                    assert path is None
                else:
                    p = 5 / 6
                    if position.distance(square) == player.get_ma() + 2:
                        p_clean = p * p
                        p_reroll_one = (p * (1 - p) * p)
                        p_reroll_both = (1 - p) * p * (1 - p) * p
                        if reroll and not skill or not reroll and skill:
                            p = p_clean + p_reroll_one * 2
                        elif reroll and skill:
                            p = p_clean + p_reroll_one * 2 + p_reroll_both
                        else:
                            p = p_clean
                        assert path is not None
                        assert path.prob == pytest.approx(p, PROP_PRECISION)
                    elif position.distance(square) == player.get_ma() + 1:
                        assert path is not None
                        if reroll or skill:
                            p = p + (1 - p) * p
                        assert path.prob == pytest.approx(p, PROP_PRECISION)
                    else:
                        assert path is not None
                        p = 1.0
                        assert path.prob == p


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_invalid_path(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(1, 1)
    game.put(player, position)
    target_a = Square(12, 12)
    path = pf.get_safest_path(game, player, target_a)
    assert path is None


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_avoid_path(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(1, 8)
    game.put(player, position)
    opp = game.get_reserves(game.state.away_team)[0]
    opp_position = Square(3, 8)
    game.put(opp, opp_position)
    target_a = Square(6, 8)
    path = pf.get_safest_path(game, player, target_a)
    assert path is not None
    assert len(path.steps) == 6
    assert path.prob == 1.0


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("sure_feet", [True, False])
def test_sure_feet_over_ag4_dodge(sure_feet, pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    player.role.ma = 4
    player.role.ag = 4
    if sure_feet:
        player.extra_skills = [Skill.SURE_FEET]
    game.put(player, Square(4, 1))
    opp1 = game.get_reserves(game.state.away_team)[0]
    game.put(opp1, Square(3, 3))
    opp2 = game.get_reserves(game.state.away_team)[1]
    game.put(opp2, Square(5, 3))
    target = Square(2, 4)
    path = pf.get_safest_path(game, player, target)
    assert path is not None
    if sure_feet:
        assert len(path.steps) == 5
        p = (5 / 6)
        assert path.prob == p + (1 - p) * p
    else:
        assert len(path.steps) == 4
        p = (5 / 6)
        assert path.prob == p


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_dodge_needed_path_long(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.home_team)[0]
    position = Square(1, 8)
    game.put(player, position)
    opp = game.get_reserves(game.state.away_team)[0]
    opp_position = Square(3, 8)
    game.put(opp, opp_position)
    target_a = Square(9, 8)
    path = pf.get_safest_path(game, player, position=target_a)
    assert path is not None
    assert len(path.steps) == 8
    assert path.prob == (4 / 6) * (5 / 6) * (5 / 6)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("y_start", [1, 4, 15])
def test_path_to_endzone_home(pf, y_start):
    game, (player, ) = get_custom_game_turn(player_positions=[(4, y_start)],
                                            ball_position=(4, y_start))

    path: python_pathfinding.Path = pf.get_safest_path_to_endzone(game, player)
    assert path is not None
    assert len(path) == 3
    assert path.get_last_step().x == 1


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("y_start", [1, 4, 15])
def test_path_to_endzone_away(pf, y_start):
    game, (player, ) = get_custom_game_turn(player_positions=[],
                                            opp_player_positions=[(23, y_start)],
                                            ball_position=(23, y_start))

    path: python_pathfinding.Path = pf.get_safest_path_to_endzone(game, player)

    assert path is not None
    assert len(path) == 3
    assert path.get_last_step().x == 26


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_all_paths(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.away_team)[0]
    player.role.ma = 6
    position = Square(1, 1)
    game.put(player, position)
    paths = pf.get_all_paths(game, player)
    assert paths is not None
    moves_left = player.num_moves_left(include_gfi=True)
    assert len(paths) == ((moves_left + 1) * (moves_left + 1)) - 1


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_all_paths_down(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.away_team)[0]
    player.role.ma = 6
    player.state.up = False
    position = Square(1, 1)
    game.put(player, position)
    paths = pf.get_all_paths(game, player)
    assert paths is not None
    moves_left = player.num_moves_left(include_gfi=True)
    assert len(paths) == ((moves_left - 3 + 1) * (moves_left - 3 + 1)) - 1


def test_that_unittest_mock_patch_works():
    """
    This test makes sure that unittest.mock.patch works as expected in other tests.
    """
    with unittest.mock.patch('botbowl.core.procedure.Pathfinder', None):
        game = get_game_turn()
        game.config.pathfinding_enabled = True
        team = game.get_agent_team(game.actor)
        player = game.get_players_on_pitch(team=team)[0]
        with pytest.raises(TypeError):
            game.step(Action(ActionType.START_MOVE, player=player))


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_blitz_action_type_is_block(pf):
    with unittest.mock.patch('botbowl.core.procedure.Pathfinder', pf.Pathfinder):
        game = get_game_turn()
        game.config.pathfinding_enabled = True
        team = game.get_agent_team(game.actor)
        player = game.get_players_on_pitch(team=team)[0]
        player.role.ma = 16
        game.step(Action(ActionType.START_BLITZ, player=player))
        assert np.sum([len(action.positions) for action in game.get_available_actions() if action.action_type == ActionType.BLOCK]) == 11


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_handoff_action_type_is_handoff(pf):
    with unittest.mock.patch('botbowl.core.procedure.Pathfinder', pf.Pathfinder):
        game = get_game_turn()
        game.config.pathfinding_enabled = True
        team = game.get_agent_team(game.actor)
        player = game.get_players_on_pitch(team=team)[0]
        game.move(game.get_ball(), player.position)
        game.get_ball().is_carried = True
        player.role.ma = 16
        game.step(Action(ActionType.START_HANDOFF, player=player))
        assert np.sum([len(action.positions) for action in game.get_available_actions() if action.action_type == ActionType.HANDOFF]) == 10


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_handoff_action_type_is_foul(pf):
    with unittest.mock.patch('botbowl.core.procedure.Pathfinder', pf.Pathfinder):
        game = get_game_turn()
        game.config.pathfinding_enabled = True
        team = game.get_agent_team(game.actor)
        player = game.get_players_on_pitch(team=team)[0]
        game.move(game.get_ball(), player.position)
        game.get_ball().carried = True
        player.role.ma = 16
        for opp_player in game.get_players_on_pitch(team=game.get_opp_team(team)):
            opp_player.state.up = False
        game.step(Action(ActionType.START_FOUL, player=player))
        assert np.sum([len(action.positions) for action in game.get_available_actions() if action.action_type == ActionType.FOUL]) == 11


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_blitz_action_type_is_block_with_stab(pf):
    with unittest.mock.patch('botbowl.core.procedure.Pathfinder', pf.Pathfinder):
        game = get_game_turn()
        game.config.pathfinding_enabled = True
        team = game.get_agent_team(game.actor)
        player = game.get_players_on_pitch(team=team)[0]
        player.role.ma = 16
        player.role.skills = [Skill.STAB]
        game.step(Action(ActionType.START_BLITZ, player=player))
        assert len([action.action_type for action in game.get_available_actions() if action.action_type == ActionType.BLOCK]) == 1
        assert len([action.action_type for action in game.get_available_actions() if action.action_type == ActionType.STAB]) == 1


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_all_blitz_paths_one(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.away_team)[0]
    player.role.ma = 6
    game.put(player, Square(1, 1))
    opp_player = game.get_reserves(game.state.home_team)[0]
    game.put(opp_player, Square(3, 3))
    paths = pf.get_all_paths(game, player, blitz=True)
    assert paths is not None
    blitz_paths = [path for path in paths if path.block_dice is not None]
    assert len(blitz_paths) == 1


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_all_blitz_paths_two(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.away_team)[0]
    player.role.ma = 6
    game.put(player, Square(1, 1))
    opp_player = game.get_reserves(game.state.home_team)[0]
    game.put(opp_player, Square(3, 3))
    opp_player = game.get_reserves(game.state.home_team)[1]
    game.put(opp_player, Square(4, 3))
    paths = pf.get_all_paths(game, player, blitz=True)
    assert paths is not None
    blitz_paths = [path for path in paths if path.block_dice is not None]
    assert len(blitz_paths) == 2


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_handoff_after_gfi(pf):
    game, (player, other_player) = get_custom_game_turn(player_positions=[(2, 2), (3, 3)],
                                                        ball_position=(2, 2))
    player.role.ma = 6
    player.state.moves = 8

    pathfinder = pf.Pathfinder(game,
                               player,
                               can_handoff=True)
    paths = pathfinder.get_paths()
    assert len(paths) == 1
    assert len(paths[0].steps) == 1
    assert paths[0].steps[0] == other_player.position


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_foul_after_gfi(pf):
    game, (player, opp_player) = get_custom_game_turn(player_positions=[(1, 1)],
                                                      opp_player_positions=[(2, 2)])
    player.role.ma = 6
    player.state.moves = 8
    opp_player.state.up = False

    pathfinder = pf.Pathfinder(game,
                               player,
                               can_foul=True)
    paths = pathfinder.get_paths()
    assert len(paths) == 1
    assert len(paths[0].steps) == 1
    assert paths[0].steps[0] == opp_player.position


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_foul(pf):
    game, (player, opp_player_1, opp_player_2) = get_custom_game_turn(player_positions=[(1, 1)],
                                                                      opp_player_positions=[(1, 2), (1, 3)])

    player.role.ma = 1
    opp_player_1.state.up = False
    opp_player_2.state.up = False

    pathfinder = pf.Pathfinder(game,
                               player,
                               directly_to_adjacent=True,
                               can_foul=True,
                               trr=False)
    paths = pathfinder.get_paths()
    total_fouls = 0
    for path in paths:
        fouls = 0
        for step in path.steps:
            if step in [opp_player_1.position, opp_player_2.position]:
                fouls += 1
                assert step == path.get_last_step()
        assert fouls <= 1
        total_fouls += fouls
    assert total_fouls == 2


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_handoff(pf):
    game, (player, teammate_1, teammate_2) = get_custom_game_turn(player_positions=[(2, 2), (2, 3), (2, 4)],
                                                                  ball_position=(2, 2))

    player.role.ma = 1
    pathfinder = pf.Pathfinder(game,
                               player,
                               directly_to_adjacent=True,
                               can_handoff=True,
                               trr=False)

    paths = pathfinder.get_paths()
    total_handoffs = 0
    for path in paths:
        handoffs = 0
        for step in path.steps:
            if step in [teammate_1.position, teammate_2.position]:
                handoffs += 1
                assert step == path.get_last_step()
        assert handoffs <= 1
        total_handoffs += handoffs
    assert total_handoffs == 2


@requires_native
def test_compare_cython_python_paths():
    """
    This test compares paths calculated by cython and python. They should be same.
    It only compares destination, probability and number of steps, not each individual step.
    """
    assert hasattr(cython_pathfinding, 'Pathfinder'), 'Cython pathfinding was not imported'
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.away_team)[0]

    player.extra_skills.append(Skill.DODGE)
    player.extra_skills.append(Skill.SURE_FEET)

    player.role.ma = 7
    position = Square(7, 7)
    game.put(player, position)

    for sq in [Square(8, 8), Square(5, 7), Square(8, 5)]:
        opp_player = game.get_reserves(game.state.home_team)[0]
        game.put(opp_player, sq)

    game.move(game.get_ball(), Square(9, 9))
    game.get_ball().is_carried = False

    cython_paths = cython_pathfinding.Pathfinder(game, player, trr=True).get_paths()
    python_paths = python_pathfinding.Pathfinder(game, player, directly_to_adjacent=True, trr=True).get_paths()

    assert len(python_paths) == len(cython_paths)
    for python_path, cython_path in zip(python_paths, cython_paths):
        assert python_path.get_last_step() == cython_path.get_last_step()
        assert len(python_path.steps) == len(cython_path.steps)
        assert python_path.prob == pytest.approx(cython_path.prob, rel=0, abs=PROP_PRECISION)

@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_straight_paths(pf):
    game = get_game_turn(empty=True)
    player = game.get_reserves(game.state.away_team)[0]
    game.put(player, Square(7, 7))
    paths = pf.get_all_paths(game, player)

    for path in paths:
        if path.get_last_step().x == 7:
            assert all(step.x == 7 for step in path.steps)
        if path.get_last_step().y == 7:
            assert all(step.y == 7 for step in path.steps)


@pytest.mark.parametrize("pf_enabled", [False, True])
def test_blitz_one_move_left(pf_enabled):
    game, (player, opp_player) = get_custom_game_turn(player_positions=[(5, 5)],
                                                      opp_player_positions=[(6, 6)],
                                                      pathfinding_enabled=pf_enabled)

    player.role.ma = 1

    game.step(Action(ActionType.START_BLITZ, player=player))
    assert player.num_moves_left() == 1

    game.step(Action(ActionType.BLOCK, position=opp_player.position))

    assert not game.has_report_of_type(botbowl.OutcomeType.FAILED_GFI)
    assert not game.has_report_of_type(botbowl.OutcomeType.SUCCESSFUL_GFI)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("as_home", [True, False])
def test_scoring_paths(pf, as_home):
    home_positions = [(2, 1),
                      (2, 2),
                      (3, 1),
                      (3, 2)]

    away_positions = [(25, 1),
                      (25, 2),
                      (24, 1),
                      (24, 2)]

    ball_pos = home_positions[0] if as_home else away_positions[0]
    end_zone_x = 1 if as_home else 26

    game, _ = get_custom_game_turn(player_positions=home_positions,
                                   opp_player_positions=away_positions,
                                   ball_position=ball_pos)

    ball_carrier = game.get_ball_carrier()

    paths = pf.get_all_paths(game, ball_carrier)

    assert len(paths) == 2

    for path in paths:
        # we make sure that there are no steps after passing home_td zone
        found_td = False
        for step in path.steps:
            assert not found_td
            if step.x == end_zone_x:
                found_td = True


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_forced_pickup_path(pf):
    game, (player1, player2, player3) = get_custom_game_turn(player_positions=[(1, 1), (1, 2), (2, 2)],
                                                             ball_position=(2, 1),
                                                             pathfinding_enabled=True)
    paths = pf.get_all_paths(game, player1)
    assert len(paths) == 1
    assert paths[0].get_last_step() == game.get_ball_position()
    assert_path(paths[0], [(2, 1)], [[3]], 4 / 6)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_airborne_ball_does_not_force_pickup(pf):
    game, (player,) = get_custom_game_turn(player_positions=[(1, 1)],
                                           ball_position=(3, 3),
                                           pathfinding_enabled=True)
    ball = game.get_ball()
    ball.on_ground = False

    paths = pf.get_all_paths(game, player)
    path: python_pathfinding.Path = first(filter(lambda p: p.get_last_step() == ball.position, paths))

    assert path.prob == 1


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_pickle_path(pf):
    game, (player,) = get_custom_game_turn(player_positions=[(1, 1)],
                                           ball_position=(3, 3),
                                           pathfinding_enabled=True)
    game.step(Action(ActionType.START_MOVE, position=player.position))
    paths = game.get_available_actions()[0].paths
    pickled_bytes = pickle.dumps(paths)

    
@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_snow_for_it(pf):
    game, (player, ) = get_custom_game_turn(player_positions=[(1, 1)],
                                            weather=botbowl.WeatherType.BLIZZARD)

    player.role.ma = 1
    paths = pf.get_all_paths(game, player)

    assert len(paths) == 15
    path1 = first(filter(lambda p: p.get_last_step() == Square(3, 3), paths))
    assert path1.rolls == ([], [3])
    assert path1.prob == approx(4/6)  # prob of 3+

    path2 = first(filter(lambda p: p.get_last_step() == Square(4, 4), paths))
    assert path2.rolls == ([], [3], [3])
    assert path2.prob == approx((4 / 6)**2)  # prob of 3+ 3+


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_pouring_rain_pickup(pf):
    game, (player, ) = get_custom_game_turn(player_positions=[(1, 1)],
                                            ball_position=(3, 3),
                                            weather=botbowl.WeatherType.POURING_RAIN)

    paths = pf.get_all_paths(game, player)
    path = first(filter(lambda p: p.get_last_step() == Square(3, 3), paths))
    assert path.rolls == ([], [4])
    assert path.prob == approx(0.5)  # corresponding to 4+


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_pouring_rain_handoff(pf):
    game, (player, catcher) = get_custom_game_turn(player_positions=[(2, 2), (4, 4)],
                                                   ball_position=(2, 2),
                                                   weather=botbowl.WeatherType.POURING_RAIN)

    paths = pf.Pathfinder(game, player, can_handoff=True).get_paths()
    assert len(paths) > 0
    path = first(filter(lambda p: p.get_last_step() == catcher.position, paths))
    assert path.rolls == ([], [])
    assert path.handoff_roll == 4


@pytest.fixture(params=[(size, home, snow, reroll, sure_feet)
                       for size in SIZES for home in (False, True)
                       for snow in (False, True) for reroll in (False, True)
                       for sure_feet in (False, True)])
def fixed_pathfinding_corpus(request):
    size, home, snow, reroll, sure_feet = request.param
    with scenario(size=size, seed=17) as probe:
        game = probe.game
        team = game.state.home_team if home else game.state.away_team
        probe.until(lambda g: g.active_team is team)
        game.clear_board()
        player = team.players[0]
        player.role.ma = 1
        player.extra_skills = [Skill.SURE_FEET] if sure_feet else []
        player.role.skills = []
        player.state.moves = 0
        direction = -1 if home else 1
        origin = game.get_square(game.arena.width - 2 if home else 1, 1)
        game.put(player, origin)
        ball = game.get_ball()
        ball.move_to(origin)
        ball.is_carried = True
        game.state.weather = botbowl.WeatherType.BLIZZARD if snow else botbowl.WeatherType.NICE
        team.state.rerolls = int(reroll)
        game.enable_forward_model()
        yield game, player, direction, snow, reroll, sure_feet


def gfi_probability(gfis, snow, reroll, sure_feet):
    p = 4 / 6 if snow else 5 / 6
    if gfis == 0:
        return 1.0
    if not (reroll or sure_feet):
        return p ** gfis
    if gfis == 1:
        return p + (1 - p) * p
    # Two rolls: one retry resource can recover either failure; two can recover both.
    return p * p + 2 * p * (1 - p) * p + (int(reroll and sure_feet) * ((1 - p) * p) ** 2)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
def test_fixed_corpus_reference(pf, fixed_pathfinding_corpus):
    game, player, direction, snow, reroll, sure_feet = fixed_pathfinding_corpus
    origin = player.position
    before = pickle.dumps(game)
    for gfis in (0, 1, 2):
        steps = [(origin.x + direction * n, 1) for n in range(1, gfis + 2)]
        path = pf.get_safest_path(game, player, Square(*steps[-1]), allow_team_reroll=reroll)
        assert_path(path, steps, [[]] + [[3 if snow else 2]] * gfis,
                    gfi_probability(gfis, snow, reroll, sure_feet))
        assert pickle.dumps(game) == before


@requires_native
def test_fixed_corpus_differential(fixed_pathfinding_corpus):
    game, player, _, _, reroll, _ = fixed_pathfinding_corpus
    before = pickle.dumps(game)
    python_paths = python_pathfinding.get_all_paths(game, player, allow_team_reroll=reroll)
    assert pickle.dumps(game) == before
    native_paths = cython_pathfinding.get_all_paths(game, player, allow_team_reroll=reroll)
    assert pickle.dumps(game) == before
    assert len(python_paths) == len(native_paths)
    expected = {(p.get_last_step().x, p.get_last_step().y): p for p in python_paths}
    actual = {(p.get_last_step().x, p.get_last_step().y): p for p in native_paths}
    assert len(expected) == len(python_paths)
    assert len(actual) == len(native_paths)
    assert actual.keys() == expected.keys()
    for destination, reference in expected.items():
        assert_path(actual[destination], [(sq.x, sq.y) for sq in reference.steps],
                    reference.rolls, reference.prob, reference.handoff_roll)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("moves_used", [6, 7, 8])
def test_handoff_query_at_movement_limit(pf, moves_used):
    distance = 9 - moves_used
    game, (player, receiver) = get_custom_game_turn(
        player_positions=[(2, 2), (2 + distance, 2)], ball_position=(2, 2))
    player.role.ma = 6
    game.step(Action(ActionType.START_HANDOFF, player=player))
    player.state.moves = moves_used
    path = pf.get_safest_path(game, player, receiver.position)
    assert_path(path, [(x, 2) for x in range(3, 3 + distance)],
                [[2]] * (distance - 1) + [[]], (5 / 6) ** (distance - 1), 3)


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("forward_model", [False, True])
@pytest.mark.parametrize("bad_input", ["occupied", "negative_moves", "out_of_bounds"])
def test_invalid_hypothetical_query_is_unchanged(pf, forward_model, bad_input):
    game, (player, other) = get_custom_game_turn(
        player_positions=[(5, 2), (4, 2)], ball_position=(5, 2),
        forward_model_enabled=forward_model)
    from_position = other.position if bad_input == "occupied" else Square(3, 2)
    if bad_input == "out_of_bounds":
        from_position = Square(0, 2)
    moves = -1 if bad_input == "negative_moves" else 1
    before = pickle.dumps(game)
    with pytest.raises(AssertionError):
        pf.get_all_paths(game, player, from_position=from_position, num_moves_used=moves)
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("forward_model", [False, True])
def test_hypothetical_pickup_restores_ball(pf, forward_model):
    game, (player,) = get_custom_game_turn(
        player_positions=[(5, 1)], ball_position=(3, 1),
        forward_model_enabled=forward_model)
    before = pickle.dumps(game)
    path = pf.get_safest_path_to_endzone(game, player, from_position=Square(3, 1), num_moves_used=2)
    assert_path(path, [(2, 1), (1, 1)], [[], []], 1.0)
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("pf", pathfinding_modules_to_test)
@pytest.mark.parametrize("forward_model", [False, True])
def test_hypothetical_movement_restores_prone_player(pf, forward_model):
    game, (player,) = get_custom_game_turn(
        player_positions=[(5, 1)], ball_position=(5, 1),
        forward_model_enabled=forward_model)
    player.state.up = False
    before = pickle.dumps(game)
    path = pf.get_safest_path(game, player, Square(2, 1), from_position=Square(3, 1), num_moves_used=1)
    assert_path(path, [(2, 1)], [[]], 1.0)
    assert pickle.dumps(game) == before
