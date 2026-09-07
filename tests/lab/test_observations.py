"""Public field fidelity, context limits, isolation and episode-local identity."""
from copy import deepcopy
from dataclasses import fields, is_dataclass
import json
import pickle
from typing import Literal, Union, get_args, get_origin

import pytest
import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.observations import ObservationControl, ObservationV1, Presence, observe
from tests.baseline import SIZES, Scenario, place_players as baseline_place_players


def place_players(probe, own, opponents=(), ball=None):
    players = baseline_place_players(probe, own, opponents, ball)
    # The #4 helper deliberately uses raw put() for micropositions; that leaves
    # reserve membership in place. Observation location fixtures need coherent
    # compartment membership as well as a board position.
    for player in players:
        reserves = probe.game.state.dugouts[player.team.team_id].reserves
        if player in reserves:
            reserves.remove(player)
    return players


def episode(size=11, seed=0):
    config = bb.load_config("gym-" + str(size))
    config.kick_off_table = False
    config.pathfinding_enabled = False
    config.rounds = 2
    rules = bb.load_rule_set(config.ruleset)
    home = bb.load_team_by_filename("human", rules, board_size=size)
    away = bb.load_team_by_filename("human", rules, board_size=size)
    game = bb.Game("observation-test", home, away, bb.Agent("home", human=True),
                   bb.Agent("away", human=True), config, seed=seed)
    control = ObservationControl(game)  # Bind before init/any actions.
    return Scenario(game, seed, size), control


def at_state(kind, size, side):
    probe, control = episode(size)
    game = probe.game
    if kind == "unstarted":
        return probe, control
    game.init()
    team = game.state.home_team if side == "home" else game.state.away_team
    if kind == "setup":
        probe.until(lambda g: type(g.get_procedure()) is proc.Setup and g.active_team is team)
        return probe, control
    probe.until(lambda g: type(g.get_procedure()) is proc.Turn and g.active_team is team)
    if kind == "terminal":
        probe.until(lambda g: g.state.game_over)
    elif kind == "drive":
        x = game.get_opp_endzone_x(team)
        start = x + (1 if x == 1 else -1)
        player, = place_players(probe, [(start, 2)], ball=(start, 2))
        probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
        probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(x, 2)))
        assert type(game.get_procedure()) is proc.Setup
    elif kind == "defender_choice":
        attacker, defender = place_players(probe, [(2, 2)], [(3, 2)])
        defender.extra_st = 3  # Two dice, defender chooses.
        probe.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
        with game.dice.force(block_dice=[bb.BBDieResult.PUSH, bb.BBDieResult.DEFENDER_DOWN], strict=True):
            probe.step(bb.Action(bb.ActionType.BLOCK, position=defender.position))
        assert game.active_team is defender.team
    else:
        player, = place_players(probe, [(2, 2)], ball=(2, 2) if kind == "carried" else None)
        if kind == "movement":
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(3, 2)))
        elif kind == "reroll":
            player.state.moves = player.get_ma()
            player.team.state.rerolls = 1
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            with game.dice.force(d6=[1], strict=True):
                probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(3, 2)))
            assert type(game.get_procedure()) is proc.Reroll
        elif kind == "ko_reserve":
            game.pitch_to_kod(player)
            assert game.state.dugouts[team.team_id].reserves
        else:
            assert kind in ("ground", "carried")
    return probe, control


def assert_schema(hint, value, bindings=None):
    """Validate serialized output against every typed schema field, recursively."""
    bindings = {} if bindings is None else bindings
    hint = bindings.get(hint, hint)
    origin, args = get_origin(hint), get_args(hint)
    if origin is Union:
        if value is None:
            assert type(None) in args
        else:
            assert_schema(next(item for item in args if item is not type(None)), value, bindings)
    elif origin is Literal:
        assert any(type(value) is type(item) and value == item for item in args)
    elif origin is list:
        assert type(value) is list
        for item in value:
            assert_schema(args[0], item, bindings)
    elif is_dataclass(origin or hint):
        schema = origin or hint
        if origin:
            bindings = {**bindings, **dict(zip(origin.__parameters__, args))}
        assert type(value) is dict
        assert set(value) == {field.name for field in fields(schema)}
        for field in fields(schema):
            assert_schema(field.type, value[field.name], bindings)
        if schema is Presence:
            assert value["present"] is (value["value"] is not None)
    else:
        assert type(value) is hint


def assert_raw_equivalence(game, control, data):
    assert_schema(ObservationV1, data)
    geometry = data["geometry"]
    assert (geometry["width"], geometry["height"]) == (game.arena.width, game.arena.height)
    assert geometry["tiles"] == [[tile.name for tile in row] for row in game.arena.board]
    assert geometry["playable"] == [[tile != bb.Tile.CROWD for tile in row] for row in game.arena.board]
    assert len({player["id"] for player in data["players"]}) == len(game.state.player_by_id)
    for entity in data["players"]:
        player = game.state.player_by_id[control.internal_player_id(entity["id"])]
        assert entity["position"]["value"] == (None if player.position is None else
                                                {"x": player.position.x, "y": player.position.y})
        if player.position is not None:
            assert 0 <= player.position.x < geometry["width"]
            assert 0 <= player.position.y < geometry["height"]
        assert entity["team"] == ("home" if player.team is game.state.home_team else "away")
        assert entity["role"] == player.role.name
        assert entity["skills"] == [skill.name for skill in player.role.skills + player.extra_skills]
        assert entity["attributes"] == {attr: getattr(player, "get_" + attr)() for attr in ("ma", "st", "ag", "av")}
        assert entity["role_attributes"] == {attr: getattr(player.role, attr) for attr in ("ma", "st", "ag", "av")}
        assert entity["extra_attributes"] == {attr: getattr(player, "extra_" + attr) for attr in ("ma", "st", "ag", "av")}
        assert entity["status"] == {attr: getattr(player.state, attr) for attr in entity["status"]}
    for entity, team in zip(data["teams"], game.state.teams):
        assert entity["score"] == team.state.score
        assert entity["turn"] == team.state.turn
        assert entity["resources"] == {attr: getattr(team.state, attr) for attr in entity["resources"]}
    for entity, ball in zip(data["balls"], game.state.pitch.balls):
        assert entity["is_carried"] == ball.is_carried
        assert entity["on_ground"] == ball.on_ground
        carrier = game.get_player_at(ball.position) if ball.is_carried and ball.position is not None else None
        assert entity["carrier"]["value"] == control._player(carrier)
    assert data["match"]["half"] == game.state.half
    assert data["match"]["round"] == game.state.round
    assert data["match"]["weather"] == game.state.weather.name
    assert data["match"]["drive"] == {"value": None, "present": False}
    if not game.state.game_over:
        assert data["decision"]["actor_team"]["value"] == control._team(game.active_team)
        assert data["decision"]["options"] == [
            dict(action_type=choice.action_type.name,
                 team=dict(value=control._team(choice.team), present=choice.team is not None),
                 skill=dict(value=None if choice.skill is None else choice.skill.name, present=choice.skill is not None),
                 disabled=choice.disabled) for choice in game.state.available_actions]


STATES = ("unstarted", "setup", "movement", "reroll", "defender_choice", "ground", "carried",
          "ko_reserve", "drive", "terminal")


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("side", ("home", "away"))
@pytest.mark.parametrize("kind", STATES)
def test_states_schema_raw_fields_and_pure_copied_reads(kind, size, side):
    probe, control = at_state(kind, size, side)
    game = probe.game
    game.enable_forward_model()
    game.add_primary_clock(game.state.home_team)
    game.dice.fix(bb.D6, 4)
    before = pickle.dumps(game)
    rng_before = game.capture_rng_state()
    first = observe(game, control, side)
    data = first.to_json()
    assert data == observe(game, control, side).to_json()
    assert game.capture_rng_state() == rng_before
    assert pickle.dumps(game) == before  # Includes stack, RNG/queues, trajectory and raw clocks.
    assert_raw_equivalence(game, control, data)
    other = observe(game, control, "away" if side == "home" else "home").to_json()
    other["observer_team"] = side
    assert other == data  # No coordinate flips or opponent-private information.
    if kind == "reroll":
        assert data["decision"]["phase"] == "reroll"
        assert data["decision"]["reroll_of"]["value"] == "gfi"
        assert data["decision"]["target_position"]["value"] == {"x": 3, "y": 2}
    elif kind == "defender_choice":
        assert data["decision"]["phase"] == "block"
        assert data["decision"]["actor_team"]["value"] != data["decision"]["active_team"]["value"]
        assert data["decision"]["target_player"]["present"]
    elif kind == "ko_reserve":
        assert {p["location"] for p in data["players"]} >= {"ko", "reserves"}
    elif kind == "terminal":
        assert data["decision"]["phase"] == "terminal"
        assert not data["decision"]["pending"]
        assert not data["decision"]["actor_team"]["present"]
    elif kind == "drive":
        assert data["match"]["kicking_team"]["value"] == side
        assert sum(team["score"] for team in data["teams"]) == 1
    # Mutate every serialized container, as well as nested dataclass containers.
    def corrupt(value):
        if isinstance(value, dict):
            for child in list(value.values()):
                corrupt(child)
            value.clear()
        elif isinstance(value, list):
            for child in list(value):
                corrupt(child)
            value.append("mutated")
    corrupt(data)
    first.geometry.tiles[0].clear()
    first.players[0].skills.append("mutated")
    first.players.clear()
    assert pickle.dumps(game) == before
    assert observe(game, control, side).to_json() == other


@pytest.mark.parametrize("size", SIZES)
def test_ids_survive_locations_roster_reordering_and_full_episode(size):
    probe, control = episode(size)
    game = probe.game
    initial = observe(game, control, "home").to_json()
    ids = [player["id"] for player in initial["players"]]
    game.init()
    probe.until(lambda g: type(g.get_procedure()) is proc.Turn)
    player, = place_players(probe, [(2, 2)])
    player_id = control._player(player)
    for move, location in ((game.pitch_to_kod, "ko"), (game.pitch_to_reserves, "reserves"),
                           (game.pitch_to_casualties, "casualties"), (game.pitch_to_dungeon, "dungeon")):
        if player.position is None:
            # Synthetic transitions across every dugout compartment; clear the
            # preceding compartment before re-placing the same roster entity.
            dugout = game.state.dugouts[player.team.team_id]
            for compartment in (dugout.kod, dugout.reserves, dugout.casualties, dugout.dungeon):
                if player in compartment:
                    compartment.remove(player)
            game.put(player, game.get_square(2, 2))
        move(player)
        data = observe(game, control, "home").to_json()
        assert next(p for p in data["players"] if p["id"] == player_id)["location"] == location
        assert [p["id"] for p in data["players"]] == ids
    # Restore the roster through engine operations before completing the game.
    game.state.dugouts[player.team.team_id].dungeon.remove(player)
    game.state.dugouts[player.team.team_id].reserves.append(player)
    for team in game.state.teams:
        team.players.reverse()
    assert [p["id"] for p in observe(game, control, "home").to_json()["players"]] == ids
    probe.until(lambda g: g.state.game_over)
    assert [p["id"] for p in observe(game, control, "home").to_json()["players"]] == ids
    fresh, binding = episode(size, seed=17)
    assert ids == [p["id"] for p in observe(fresh.game, binding, "away").to_json()["players"]]
    assert {control.internal_player_id(item) for item in ids}.isdisjoint(
        binding.internal_player_id(item) for item in ids)
    game.state.home_team.players.pop()
    assert [p["id"] for p in observe(game, control, "home").to_json()["players"]] == ids


def test_same_board_distinct_pending_public_context():
    probe, control = at_state("movement", 11, "home")
    game = probe.game
    moving = observe(game, control, "home").to_json()
    # Same public board/entities/resources, but a declared reroll of GFI at a
    # different destination. This synthetic fixture tests context, not legality.
    player = game.state.active_player
    context = proc.GFI(game, player, game.get_square(4, 2))
    proc.Reroll(game, player, context)
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.USE_REROLL, player.team)]
    reroll = observe(game, control, "home").to_json()
    assert moving["decision"] != reroll["decision"]
    assert reroll["decision"]["target_position"]["value"] == {"x": 4, "y": 2}
    assert {key: value for key, value in moving.items() if key != "decision"} == {
        key: value for key, value in reroll.items() if key != "decision"}


def test_private_and_future_sentinels_never_appear_at_any_serialized_depth(monkeypatch):
    probe, control = at_state("reroll", 11, "home")
    game = probe.game
    baseline = observe(game, control, "home").to_json()
    marker = "PRIVATE-FUTURE-SENTINEL-issue33"
    private = {"nested": [{"secret": marker}], "weights": [987654321]}
    for obj in (game, game.state, game.state.pitch, game.arena, game.config, game.dice,
                game.home_agent, game.away_agent, game.state.home_team, game.state.home_team.state,
                game.state.home_team.players[0], game.state.home_team.players[0].role,
                game.state.home_team.players[0].state, game.state.pitch.balls[0],
                game.state.stack.items[-1], game.state.stack.items[-1].context):
        monkeypatch.setattr(obj, "private_policy", private, raising=False)
        monkeypatch.setattr(obj, "future", private, raising=False)
    monkeypatch.setattr(game, "seed", 987654321, raising=False)
    monkeypatch.setattr(game.dice, "rng", private)
    monkeypatch.setattr(game.dice, "_queues", [private])
    monkeypatch.setattr(game.state.stack.items[-1].context, "roll", private)
    for context in game.state.stack.items:
        if type(context) is proc.MoveAction:
            monkeypatch.setattr(context, "steps", [private])
            monkeypatch.setattr(context, "paths", private)
    monkeypatch.setattr(game, "ff_map", private)
    monkeypatch.setattr(game, "replay", private)
    for choice in game.state.available_actions:
        for attr in ("rolls", "block_dice", "paths"):
            getattr(choice, attr).append(private)
    def forbidden(*args, **kwargs):
        raise AssertionError("Observation called an advancing, refreshing or feature API")
    for attr in ("step", "set_available_actions", "to_json", "get_dodge_prob", "get_block_probs"):
        monkeypatch.setattr(game, attr, forbidden, raising=False)
    monkeypatch.setattr(game.arena, "to_json", forbidden)
    result = observe(game, control, "home").to_json()
    assert result == baseline
    serialized = json.dumps(result, allow_nan=False)
    for secret in (marker, "987654321", game.game_id, game.home_agent.agent_id,
                   game.away_agent.agent_id, *game.state.player_by_id, *game.state.team_by_id):
        assert str(secret) not in serialized


def test_unknown_procedure_is_partial_and_does_not_publish_its_name():
    probe, control = at_state("ground", 1, "home")
    game = probe.game
    class PrivateFutureProcedure(proc.Procedure):
        pass
    PrivateFutureProcedure(game)
    game.state.available_actions = []
    data = observe(game, control, "home").to_json()
    assert data["decision"]["phase"] == "other"
    assert data["decision"]["sufficiency"] == "partial"
    assert not data["decision"]["subject"]["present"]
    assert "PrivateFutureProcedure" not in json.dumps(data)


def test_reads_preserve_nonempty_trajectory_and_the_next_random_transition():
    probe, control = at_state("ground", 11, "home")
    game = probe.game
    player = game.get_player_at(game.get_square(2, 2))
    game.enable_forward_model()
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    assert game.get_step() > 0
    reference = deepcopy(game)
    before = pickle.dumps(game)
    for _ in range(3):
        observe(game, control, "home").to_json()
    assert pickle.dumps(game) == before
    # GFI uses natural RNG. Compare actual successor state/RNG to the unobserved
    # clone; wall-clock timestamps are excluded from this successor comparison.
    for candidate in (game, reference):
        candidate.state.active_player.state.moves = candidate.state.active_player.get_ma()
        candidate.step(bb.Action(bb.ActionType.MOVE, position=candidate.get_square(3, 2)))
    assert game.capture_rng_state() == reference.capture_rng_state()
    assert observe(game, control, "home").to_json() == observe(
        reference, ObservationControl(reference), "home").to_json()


def test_null_and_zero_are_distinct_and_binding_rejects_wrong_episode():
    probe, control = episode(1)
    data = observe(probe.game, control, "home").to_json()
    assert data["match"]["round"] == 0
    assert data["match"]["drive"] == {"value": None, "present": False}
    assert data["players"][0]["slot"] == 0
    assert data["players"][0]["position"] == {"value": None, "present": False}
    with pytest.raises(ValueError, match="different episode"):
        observe(deepcopy(probe.game), control, "home")
    with pytest.raises(ValueError, match="Observer team"):
        observe(probe.game, control, "invalid")
    with pytest.raises(ValueError, match="Unknown"):
        control.internal_player_id("home:999")
    probe.game.state.home_team.players.append(deepcopy(probe.game.state.home_team.players[0]))
    with pytest.raises(ValueError, match="roster additions"):
        observe(probe.game, control, "home")
