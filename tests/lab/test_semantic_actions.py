"""Semantic action fidelity, rejection purity and observable macro expansion."""

import pickle
from copy import deepcopy
from dataclasses import replace

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
    AmbiguousActionError,
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
        "START_MOVE",
        current.actor_id,
        "home:999",
        None,
        None,
        EmptyOptionsV1(),
    )
    with pytest.raises(UnknownEntityError):
        control.decode(control.request(unknown))
    outside = ActionV1(
        1,
        "MOVE",
        current.actor_id,
        None,
        None,
        PositionV1(-1, 0),
        EmptyOptionsV1(),
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
def test_gym_translation_at_every_episode_boundary_is_pure(size):
    pytest.importorskip("gymnasium")
    from botbowl.ai.gymnasium_env import GymnasiumEnv

    env = GymnasiumEnv(size, config=semantic_game(size).config)
    observation, _ = env.reset(seed=17)
    control = ActionControl(env.game)
    seen = set()
    for _ in range(160):
        before = pickle.dumps(env.game)
        indices = np.flatnonzero(observation["action_mask"])
        # Check every legal index in one representative decision per side and
        # procedure, including the player/square placement product.
        boundary = (env.game.active_team.team_id, type(env.game.get_procedure()))
        if boundary not in seen:
            seen.add(boundary)
            for index in map(int, indices):
                semantic = gym_to_semantic(env, index, control)
                assert semantic_to_gym(env, semantic, control) == index
                assert_round_trip(control, semantic)
        assert pickle.dumps(env.game) == before
        core = progress_action(env.game)
        observation, _, terminated, truncated, _ = env.step(env.encode_action(core))
        assert not truncated
        if terminated:
            break
    else:
        pytest.fail("Gym semantic episode did not terminate")
    assert len(seen) >= 8
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
    assert macro_from_json(route.to_json()) == route
    with game.dice.force(d6=[1], strict=True):
        result = control.execute_macro(route)
    assert result.status == "interrupted"
    assert result.interruption == "unplanned_decision"
    assert len(result.steps) == 1
    assert game.get_procedure().__class__.__name__ == "Reroll"
    frozen = result.steps[0].to_json()
    route.path.append(PositionV1(999, 999))
    assert result.steps[0].to_json() == frozen
    assert isinstance(result.steps[0].action.options, PathOptionsV1)


def test_route_final_block_stops_at_unplanned_selection():
    game = semantic_game(1, pathfinding=True)
    probe = Scenario(game, 17, 1)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    attacker, defender = place_players(probe, [(2, 2)], [(3, 2)])
    probe.step(bb.Action(bb.ActionType.START_BLITZ, player=attacker))
    control = ActionControl(game)
    route = next(
        item
        for item in control.legal_actions().macros
        if item.to_json()["kind"] == "route"
        and item.type == "BLOCK"
        and len(item.path) == 1
    )
    with game.dice.force(block_dice=[bb.BBDieResult.PUSH], strict=True):
        result = control.execute_macro(route)
    assert result.status == "interrupted"
    assert result.interruption == "unplanned_decision"
    assert len(result.steps) == 1
    assert game.get_procedure().__class__.__name__ == "Block"
    assert [choice.action_type for choice in game.get_available_actions()] == [
        bb.ActionType.SELECT_PUSH
    ]
    assert defender.position == game.get_square(3, 2)


def test_route_final_block_stops_when_defender_becomes_actor():
    game = semantic_game(1, pathfinding=True)
    probe = Scenario(game, 17, 1)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    attacker, defender = place_players(probe, [(2, 2)], [(3, 2)])
    attacker.extra_st = 1 - attacker.get_st()
    probe.step(bb.Action(bb.ActionType.START_BLITZ, player=attacker))
    control = ActionControl(game)
    route = next(
        item
        for item in control.legal_actions().macros
        if item.to_json()["kind"] == "route"
        and item.type == "BLOCK"
        and len(item.path) == 1
    )
    with game.dice.force(
        block_dice=[bb.BBDieResult.PUSH] * 3, strict=True
    ):
        result = control.execute_macro(route)
    assert result.status == "interrupted"
    assert result.interruption == "actor_changed"
    assert len(result.steps) == 1
    assert game.active_team is defender.team


def test_route_final_primitive_classifies_terminal_before_actor_change(monkeypatch):
    game = semantic_game(1, pathfinding=True)
    probe = Scenario(game, 17, 1)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    player = place_players(probe, [(2, 2)])[0]
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    control = ActionControl(game)
    route = next(
        item
        for item in control.legal_actions().macros
        if item.to_json()["kind"] == "route"
        and item.type == "MOVE"
        and len(item.path) == 1
    )
    advance = game.advance

    def terminal_advance(action, *, max_steps=100000):
        result = advance(action, max_steps=max_steps)
        game.state.game_over = True
        return result

    monkeypatch.setattr(game, "advance", terminal_advance)
    result = control.execute_macro(route)
    assert result.status == "interrupted"
    assert result.interruption == "terminal"
    assert len(result.steps) == 1


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
    malformed_macro = {
        "schema_version": 1,
        "macro_id": "route",
        "actor_id": "home",
        "kind": "route",
        "type": [],
        "path": [{"x": 1, "y": 1}],
    }
    with pytest.raises(ActionSchemaError):
        macro_from_json(malformed_macro)
    altered = ActionV1(
        1, "END_TURN", semantic.actor_id, None, None, None, EmptyOptionsV1()
    )
    with pytest.raises(ActionNotOfferedError):
        control.decode(control.request(altered))


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("home", (False, True))
def test_all_engine_action_families_match_validated_choices(size, home):
    game = semantic_game(size)
    team = game.state.home_team if home else game.state.away_team
    own, target = team.players[0], game.get_opp_team(team).players[0]
    game.put(own, game.get_square(2, 2))
    game.put(target, game.get_square(3, 2))
    control = ActionControl(game)
    player_types = {
        "START_MOVE", "START_BLOCK", "START_BLITZ", "START_PASS", "START_FOUL",
        "START_HANDOFF", "START_THROW_BOMB", "SELECT_PLAYER",
    }
    target_types = {"BLOCK", "STAB", "HANDOFF", "FOUL", "HYPNOTIC_GAZE"}
    position_types = {
        "PLACE_BALL", "MOVE", "PASS", "PUSH", "FOLLOW_UP", "LEAP",
        "THROW_BOMB", "PICKUP_TEAM_MATE", "THROW_TEAM_MATE",
    }
    for action_type in bb.ActionType:
        if action_type == bb.ActionType.CONTINUE:
            continue  # Automatic advancement is not a coach decision.
        kwargs = {}
        if action_type.name in player_types:
            kwargs["players"] = [own]
        elif action_type.name in target_types:
            kwargs["positions"] = [target.position]
        elif action_type.name in position_types:
            kwargs["positions"] = [game.get_square(2, 3)]
        elif action_type == bb.ActionType.PLACE_PLAYER:
            kwargs = {"players": [own], "positions": [None, game.get_square(2, 3)]}
        if action_type.name in {"USE_SKILL", "DONT_USE_SKILL"}:
            kwargs["skill"] = Skill.PRO
        if action_type == bb.ActionType.HYPNOTIC_GAZE:
            own.extra_skills.append(Skill.HYPNOTIC_GAZE)
            choices = game.get_hypnotic_gaze_actions(own)
        else:
            choices = [bb.ActionChoice(action_type, team, **kwargs)]
        game.state.available_actions = choices
        before = pickle.dumps(game)
        offered = control.legal_actions().actions
        expected = [
            bb.Action(action_type, player=p, position=s)
            for choice in choices
            for p in choice.players or [None]
            for s in choice.positions or [None]
        ]
        assert len(offered) == sum(game.validate_action(a).allowed for a in expected)
        for semantic in offered:
            assert_round_trip(control, semantic)
        assert pickle.dumps(game) == before
    # SELECT_PLAYER also has a square-target shape (e.g. EatThrall).
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.SELECT_PLAYER, team, positions=[target.position])
    ]
    assert_round_trip(control, control.legal_actions().actions[0])


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1.0),
    ("state_revision", False), ("state_revision", 0.0), ("action", None),
])
def test_typed_requests_cannot_bypass_strict_parser(field, value):
    game = semantic_game()
    game.init()
    control = ActionControl(game)
    request = replace(control.request(control.legal_actions().actions[0]), **{field: value})
    before = pickle.dumps(game), pickle.dumps(request)
    with pytest.raises(ActionSchemaError):
        control.decode(request)
    assert (pickle.dumps(game), pickle.dumps(request)) == before


@pytest.mark.parametrize("action_type,payload", [
    ("END_TURN", {"player_id": "home:0"}),
    ("USE_REROLL", {"position": {"x": 2, "y": 2}}),
    ("START_MOVE", {}),
    ("MOVE", {"player_id": "home:0"}),
    ("SELECT_PLAYER", {}),
])
def test_family_parser_rejects_prohibited_or_missing_payload(action_type, payload):
    wire = ActionV1(1, action_type, "home", None, None, None, EmptyOptionsV1()).to_json()
    wire.update(payload)
    before = deepcopy(wire)
    with pytest.raises(ActionSchemaError):
        ActionV1.from_json(wire)
    assert wire == before


def test_cached_decode_still_delegates_current_legality_to_engine():
    game = semantic_game()
    game.init()
    control = ActionControl(game)
    request = control.request(control.legal_actions().actions[0])
    game.close()
    before = pickle.dumps(game)
    with pytest.raises(ActionNotOfferedError):
        control.decode(request)
    assert pickle.dumps(game) == before


def test_joint_choices_are_not_crossed_and_queries_preserve_forward_model():
    game = semantic_game(3)
    game.init()
    game.enable_forward_model()
    game.replay = bb.Replay("semantic-purity")
    team = game.active_team
    players = team.players[:2]
    positions = [game.get_square(2, 2), game.get_square(3, 2)]
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.PLACE_PLAYER, team, players=[p], positions=[s])
        for p, s in zip(players, positions)
    ]
    control = ActionControl(game)
    before = pickle.dumps(game), game.capture_rng_state(), len(game.trajectory)
    offered = control.legal_actions().actions
    assert len(offered) == 2
    for action in offered:
        assert_round_trip(control, action)
    crossed = replace(offered[0], position=offered[1].position)
    with pytest.raises(ActionNotOfferedError):
        control.decode(control.request(crossed))
    assert (pickle.dumps(game), game.capture_rng_state(), len(game.trajectory)) == before
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.START_MOVE, team, players=players, positions=positions)
    ]
    before = pickle.dumps(game)
    with pytest.raises(AmbiguousActionError):
        control.legal_actions()
    assert pickle.dumps(game) == before


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("prone", (False, True))
def test_completed_route_matches_primitives_and_retains_every_event(size, prone):
    game = semantic_game(size, pathfinding=True)
    probe = Scenario(game, 17, size)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    player = place_players(probe, [(2, 2)])[0]
    player.state.up = not prone
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    control = ActionControl(game)
    route = next(
        m for m in control.legal_actions().macros
        if m.type == "MOVE" and len(m.path) == (3 if prone else 2)
        and m.path[-1].x > 1 and m.path[-1].y > 1
    )
    manual = deepcopy(game)
    report_start = len(game.state.reports)
    expected = []
    for order, position in enumerate(route.path):
        action = (
            bb.Action(bb.ActionType.STAND_UP)
            if prone and order == 0
            else bb.Action(bb.ActionType.MOVE, position=manual.get_square(position.x, position.y))
        )
        expected.append(action.to_json())
        manual.advance(action)
    result = control.execute_macro(route)
    assert result.status == "completed" and result.interruption is None
    assert len(result.steps) == len(expected)
    assert [step.order for step in result.steps] == list(range(len(expected)))
    assert {step.macro_id for step in result.steps} == {route.macro_id}
    assert [step.action.type for step in result.steps] == [a["action_type"] for a in expected]
    assert [event for step in result.steps for event in step.events] == [
        event.to_json() for event in game.state.reports[report_start:]
    ]
    assert game.state.to_json(ignore_clocks=True) == manual.state.to_json(ignore_clocks=True)
    assert game.capture_rng_state() == manual.capture_rng_state()
    prefix = result.to_json()
    route.path.clear()
    assert result.to_json() == prefix


def test_route_rejects_new_detour_to_the_same_endpoint(monkeypatch):
    game = semantic_game(1, pathfinding=True)
    probe = Scenario(game, 17, 1)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    player = place_players(probe, [(2, 2)])[0]
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    control = ActionControl(game)
    route = next(m for m in control.legal_actions().macros if m.type == "MOVE" and len(m.path) == 2)
    encode = control.encode

    def detour(action):
        semantic = encode(action)
        if game.state.active_player.position != game.get_square(2, 2):
            return replace(semantic, options=PathOptionsV1([PositionV1(1, 1), semantic.position]))
        return semantic

    monkeypatch.setattr(control, "encode", detour)
    result = control.execute_macro(route)
    assert result.status == "interrupted" and result.interruption == "route_invalidated"
    assert len(result.steps) == 1
    assert player.position == game.get_square(route.path[0].x, route.path[0].y)


def test_lazy_path_caches_and_forward_trajectory_survive_queries_and_rejections():
    game = semantic_game(3, pathfinding=True)
    probe = Scenario(game, 17, 3)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    player = place_players(probe, [(2, 2)])[0]
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    game.enable_forward_model()
    game.replay = bb.Replay("path-query-purity")
    control = ActionControl(game)
    paths = [path for choice in game.get_available_actions() for path in choice.paths]
    assert paths
    before = pickle.dumps(game), game.capture_rng_state(), len(game.trajectory)
    # Serialization can itself materialize native paths. Restore lazy state
    # after the snapshot so it cannot mask query-induced cache writes.
    for path in paths:
        path._steps = path._rolls = None
    actions_ref = game.state.available_actions
    path_map = game.get_procedure().paths
    decision = control.legal_actions()
    for semantic in decision.actions:
        assert_round_trip(control, semantic)
    with pytest.raises(WrongActorError):
        control.decode(control.request(replace(decision.actions[0], actor_id="away")))
    assert game.state.available_actions is actions_ref
    assert game.get_procedure().paths is path_map
    assert all(path._steps is None and path._rolls is None for path in paths)
    # Match the snapshot's materialized representation only after checking
    # exact cache identities above; both backends must leave the game intact.
    assert (pickle.dumps(game), game.capture_rng_state(), len(game.trajectory)) == before


def test_route_stops_before_sending_a_now_occupied_next_square(monkeypatch):
    game = semantic_game(3, pathfinding=True)
    probe = Scenario(game, 17, 3)
    game.init()
    probe.until(lambda candidate: candidate.current_turn() is not None)
    player = place_players(probe, [(2, 2)])[0]
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    control = ActionControl(game)
    route = next(m for m in control.legal_actions().macros if m.type == "MOVE" and len(m.path) == 2)
    advance = game.advance

    def occupy_next(action, **kwargs):
        result = advance(action, **kwargs)
        game.put(game.get_opp_team(player.team).players[0],
                 game.get_square(route.path[1].x, route.path[1].y))
        game.set_available_actions()
        return result

    monkeypatch.setattr(game, "advance", occupy_next)
    result = control.execute_macro(route)
    assert result.status == "interrupted" and result.interruption == "unplanned_decision"
    assert len(result.steps) == 1
    assert player.position == game.get_square(route.path[0].x, route.path[0].y)


@pytest.mark.parametrize("version", (True, 1.0, 2))
def test_typed_macro_versions_reject_before_formation_mutation(version):
    game = setup_game(size=1)
    control = ActionControl(game)
    macro = replace(control.legal_actions().macros[0], schema_version=version)
    before = pickle.dumps(game)
    with pytest.raises(ActionSchemaError):
        control.execute_macro(macro)
    assert pickle.dumps(game) == before
