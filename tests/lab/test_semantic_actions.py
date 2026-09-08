"""Semantic action fidelity, rejection purity and observable macro expansion."""

import pickle
from copy import deepcopy

import numpy as np
import pytest

import botbowl as bb
from botbowl.core.table import Skill
from botbowl.lab.actions import (
    ActionControl,
    ActionNotOfferedError,
    ActionRequestV1,
    ActionSchemaError,
    ActionV1,
    EmptyOptionsV1,
    InvalidPositionError,
    PathOptionsV1,
    PositionV1,
    SkillOptionsV1,
    StaleDecisionError,
    UnknownEntityError,
    WrongActorError,
    gym_to_semantic,
    macro_from_json,
    semantic_to_gym,
)
from botbowl.lab.observations import ObservationControl
from tests.baseline import SIZES, Scenario, place_players, progress_action
from tests.formation_helpers import setup_game


def semantic_game(size=1, pathfinding=False):
    config = bb.load_config("gym-" + str(size))
    config.kick_off_table = False
    config.pathfinding_enabled = pathfinding
    config.rounds = 1
    rules = bb.load_rule_set(config.ruleset)
    teams = [
        bb.load_team_by_filename("human", rules, board_size=size) for _ in range(2)
    ]
    game = bb.Game(
        "semantic-actions",
        *teams,
        bb.Agent("home", human=True),
        bb.Agent("away", human=True),
        config,
        seed=17,
        external_control=True,
    )
    return game


def assert_round_trip(control, semantic):
    wire = semantic.to_json()
    assert ActionV1.from_json(deepcopy(wire)) == semantic
    core = control.decode(control.request(semantic))
    assert control.encode(core) == semantic
    assert control._game.validate_action(core).allowed


@pytest.mark.parametrize("size", SIZES)
def test_current_decision_round_trip_is_copied_pure_and_versioned(size):
    game = semantic_game(size)
    binding = ObservationControl(game)
    control = ActionControl(game, binding)
    game.init()
    before = pickle.dumps(game)
    rng = game.capture_rng_state()
    decision = control.legal_actions()
    assert decision.schema_version == 1
    assert decision.actor_id == "away"
    assert decision.actions
    for semantic in decision.actions:
        assert semantic.schema_version == 1
        assert semantic.type in bb.ActionType.__members__
        assert_round_trip(control, semantic)
    copied = decision.to_json()
    copied["actions"].clear()
    assert control.legal_actions().actions
    assert game.capture_rng_state() == rng
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("size", SIZES)
def test_every_boundary_in_full_episode_round_trips_all_offered_families(size):
    game = semantic_game(size)
    control = ActionControl(game)
    game.init()
    actors = set()
    types = set()
    for _ in range(160):
        decision = control.legal_actions()
        if game.state.game_over:
            assert (
                decision.actor_id is None
                and not decision.actions
                and not decision.macros
            )
            break
        actors.add(decision.actor_id)
        for semantic in decision.actions:
            types.add(semantic.type)
            assert_round_trip(control, semantic)
        chosen = progress_action(game)
        assert control.encode(chosen) in decision.actions
        game.advance(chosen)
    else:
        pytest.fail("bounded full episode did not terminate")
    assert actors == {"home", "away"}
    assert {"START_GAME", "PLACE_PLAYER", "END_SETUP", "END_TURN"} <= types


def test_explicit_families_cover_player_target_square_no_position_skill_and_product():
    game = semantic_game(3)
    binding = ObservationControl(game)
    control = ActionControl(game, binding)
    own, other = game.state.home_team.players[:2], game.state.away_team.players[0]
    game.put(own[0], game.get_square(2, 2))
    game.put(other, game.get_square(3, 2))
    square = game.get_square(2, 3)
    game.state.available_actions = [
        bb.ActionChoice(
            bb.ActionType.START_MOVE, game.state.home_team, players=[own[0]]
        ),
        bb.ActionChoice(
            bb.ActionType.BLOCK, game.state.home_team, positions=[other.position]
        ),
        bb.ActionChoice(bb.ActionType.MOVE, game.state.home_team, positions=[square]),
        bb.ActionChoice(bb.ActionType.END_TURN, game.state.home_team),
        bb.ActionChoice(
            bb.ActionType.USE_SKILL,
            game.state.home_team,
            players=[own[0]],
            skill=Skill.BLOCK,
        ),
        bb.ActionChoice(
            bb.ActionType.PLACE_PLAYER,
            game.state.home_team,
            players=[own[1]],
            positions=[None, square],
        ),
    ]
    before = pickle.dumps(game)
    actions = control.legal_actions().actions
    assert len(actions) == 7
    by_type = {}
    for action in actions:
        by_type.setdefault(action.type, []).append(action)
        assert_round_trip(control, action)
    assert by_type["START_MOVE"][0].player_id == binding._player(own[0])
    assert by_type["BLOCK"][0].target_id == binding._player(other)
    assert by_type["BLOCK"][0].position is None
    assert by_type["MOVE"][0].position == PositionV1(2, 3)
    assert isinstance(by_type["END_TURN"][0].options, EmptyOptionsV1)
    assert by_type["USE_SKILL"][0].options == SkillOptionsV1("BLOCK")
    assert {item.position for item in by_type["PLACE_PLAYER"]} == {
        None,
        PositionV1(2, 3),
    }
    shorthand = bb.Action(bb.ActionType.BLOCK, player=other)
    shorthand_before = (shorthand.player, shorthand.position)
    assert control.encode(shorthand) == by_type["BLOCK"][0]
    assert (shorthand.player, shorthand.position) == shorthand_before
    assert pickle.dumps(game) == before


def test_strict_wire_schema_rejects_extra_and_prohibited_options():
    value = ActionV1(
        1, "END_TURN", "home", None, None, None, EmptyOptionsV1()
    ).to_json()
    value["extra"] = True
    with pytest.raises(ActionSchemaError):
        ActionV1.from_json(value)
    value.pop("extra")
    value["options"] = {"skill": "BLOCK"}
    with pytest.raises(ActionSchemaError):
        ActionV1.from_json(value)
    value["options"] = {"path": []}
    value["type"] = "MOVE"
    with pytest.raises(ActionSchemaError):
        ActionV1.from_json(value)
    value["options"] = {}
    value["position"] = {"x": True, "y": 2}
    with pytest.raises(ActionSchemaError):
        ActionV1.from_json(value)
    target = ActionV1(
        1, "BLOCK", "home", None, "away:0", None, EmptyOptionsV1()
    ).to_json()
    target["position"] = {"x": 2, "y": 2}
    with pytest.raises(ActionSchemaError):
        ActionV1.from_json(target)


def test_stale_actor_foreign_ids_and_positions_reject_without_mutation():
    game = semantic_game(1)
    control = ActionControl(game)
    game.init()
    action = control.legal_actions().actions[0]
    request = control.request(action)
    game.advance(control.decode(request))
    before = pickle.dumps(game)
    rng = game.capture_rng_state()
    with pytest.raises(StaleDecisionError):
        control.decode(request)
    current = control.legal_actions().actions[0]
    wrong = ActionV1(
        1,
        current.type,
        "home" if current.actor_id == "away" else "away",
        current.player_id,
        current.target_id,
        current.position,
        current.options,
    )
    with pytest.raises(WrongActorError):
        control.decode(control.request(wrong))
    unknown = ActionV1(
        1,
        current.type,
        current.actor_id,
        "home:999",
        None,
        current.position,
        current.options,
    )
    with pytest.raises(UnknownEntityError):
        control.decode(control.request(unknown))
    outside = ActionV1(
        1,
        current.type,
        current.actor_id,
        current.player_id,
        current.target_id,
        PositionV1(-1, 0),
        current.options,
    )
    with pytest.raises(InvalidPositionError):
        control.decode(control.request(outside))
    assert game.capture_rng_state() == rng
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("wanted_side", ("home", "away"))
def test_gym_index_semantic_engine_correspondence_for_both_sides(size, wanted_side):
    pytest.importorskip("gymnasium")
    from botbowl.ai.gymnasium_env import GymnasiumEnv

    env = GymnasiumEnv(size, config=semantic_game(size).config)
    obs, info = env.reset(seed=17)
    control = ActionControl(env.game)
    for _ in range(40):
        if info["next_team"] == wanted_side:
            indices = np.flatnonzero(obs["action_mask"])
            for index in (int(indices[0]), int(indices[-1])):
                semantic = gym_to_semantic(env, index, control)
                assert semantic_to_gym(env, semantic, control) == index
                assert (
                    control.decode(control.request(semantic)).to_json()
                    == env.decode_action(index).to_json()
                )
            break
        core = progress_action(env.game)
        obs, _, terminated, truncated, info = env.step(env.encode_action(core))
        assert not (terminated or truncated)
    else:
        pytest.fail("requested side never received a decision")
    env.close()


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("home", (False, True))
def test_formation_macro_matches_the_same_primitive_decisions(size, home):
    game = setup_game(size=size, home=home)
    manual = deepcopy(game)
    control = ActionControl(game)
    macros = [
        item
        for item in control.legal_actions().macros
        if item.to_json()["kind"] == "formation"
    ]
    assert macros
    macro = macros[0]
    assert macro_from_json(macro.to_json()) == macro
    invalid_macro = macro.to_json()
    invalid_macro["extra"] = None
    with pytest.raises(ActionSchemaError):
        macro_from_json(invalid_macro)
    top = manual.get_procedure()
    formation = next(item for item in top.formations if item.name == macro.formation)
    plan = formation.actions(manual, top.team)
    report_start = len(manual.state.reports)
    for action in plan:
        manual.advance(action)
    result = control.execute_macro(macro)
    assert result.status == "completed" and result.interruption is None
    assert len(result.steps) == len(plan)
    assert [step.order for step in result.steps] == list(range(len(plan)))
    assert {step.macro_id for step in result.steps} == {macro.macro_id}
    recorded_events = [event for step in result.steps for event in step.events]
    assert recorded_events == [
        event.to_json() for event in game.state.reports[report_start:]
    ]
    assert game.state.to_json(ignore_clocks=True) == manual.state.to_json(
        ignore_clocks=True
    )


def test_route_stops_at_unplanned_reroll_and_freezes_recorded_prefix():
    game = semantic_game(1, pathfinding=True)
    probe = Scenario(game, 17, 1)
    control = ActionControl(game)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    player = place_players(probe, [(2, 2)])[0]
    reserves = game.get_reserves(player.team)
    if player in reserves:
        reserves.remove(player)
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    player.state.moves = player.get_ma()
    player.team.state.rerolls = 1
    game.set_available_actions()
    before = pickle.dumps(game)
    rng = game.capture_rng_state()
    decision = control.legal_actions()
    assert pickle.dumps(game) == before
    assert game.capture_rng_state() == rng
    route = next(
        item
        for item in decision.macros
        if item.to_json()["kind"] == "route" and item.type == "MOVE"
    )
    original_future = list(route.path)
    assert macro_from_json(route.to_json()) == route
    with game.dice.force(d6=[1], strict=True):
        result = control.execute_macro(route)
    assert result.status == "interrupted"
    assert result.interruption == "unplanned_decision"
    assert len(result.steps) == 1
    assert game.get_procedure().__class__.__name__ == "Reroll"
    frozen = result.steps[0].to_json()
    original_future.append(PositionV1(999, 999))
    assert result.steps[0].to_json() == frozen
    assert isinstance(result.steps[0].action.options, PathOptionsV1)


def test_request_parser_and_nonoffered_combination_are_typed():
    game = semantic_game(1)
    control = ActionControl(game)
    game.init()
    semantic = control.legal_actions().actions[0]
    wire = control.request(semantic).to_json()
    assert ActionRequestV1.from_json(wire) == control.request(semantic)
    wire["unknown"] = None
    with pytest.raises(ActionSchemaError):
        ActionRequestV1.from_json(wire)
    altered = ActionV1(
        1, "END_TURN", semantic.actor_id, None, None, None, EmptyOptionsV1()
    )
    with pytest.raises(ActionNotOfferedError):
        control.decode(control.request(altered))
