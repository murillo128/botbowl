"""Public action boundaries must reject input before changing game or caller state."""
from copy import copy, deepcopy
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

import botbowl as bb
from tests.baseline import place_players, scenario


@pytest.fixture
def game():
    with scenario(size=3) as probe:
        place_players(probe, own=((3, 3), (4, 3)), opponents=((5, 3),))
        probe.game.enable_forward_model()
        probe.game.replay = bb.Replay("validation")
        yield probe.game


def snapshot(game):
    # Clock JSON contains elapsed wall time; raw clocks remain in the serialized
    # stack/game graph, so changing a clock still fails the equality check.
    return (deepcopy(game.state.to_json(ignore_clocks=True)), pickle.dumps(game.state.stack),
            pickle.dumps(game.rng.get_state()), len(game.trajectory),
            len(game.state.reports), id(game.action), pickle.dumps(game.replay))


def select_choices(game, *groups):
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.START_MOVE, game.active_team, players=players)
        for players in groups
    ]


def test_single_eligible_player_rejects_other_player(game):
    own = game.active_team.players
    select_choices(game, [own[0]])
    assert not game._is_action_allowed(bb.Action(bb.ActionType.START_MOVE, player=own[1]))


def test_repeated_action_type_checks_later_choices(game):
    own = game.active_team.players
    select_choices(game, own[:2], own[2:])
    assert game._is_action_allowed(bb.Action(bb.ActionType.START_MOVE, player=own[2]))


def test_query_preserves_player_and_square_input(game):
    player = game.active_team.players[0]
    select_choices(game, [player])
    action = bb.Action(bb.ActionType.START_MOVE, position=copy(player.position))
    before = snapshot(game), pickle.dumps(action), action.position
    assert game._is_action_allowed(action)
    assert (snapshot(game), pickle.dumps(action), action.position) == before
    assert action.player is None


def test_none_is_illegal_at_pending_decision(game):
    assert not game._is_action_allowed(None)


def test_continue_rejection_preserves_game(game):
    before = snapshot(game)
    with pytest.raises(bb.InvalidActionError):
        game.step(bb.Action(bb.ActionType.CONTINUE))
    assert snapshot(game) == before


def test_rejected_step_preserves_caller_references(game):
    player = copy(game.active_team.players[0])
    action = bb.Action(bb.ActionType.PLACE_BALL, player=player, position=bb.Square(2, 2))
    before = snapshot(game), pickle.dumps(action)
    position = action.position
    with pytest.raises(bb.InvalidActionError):
        game.step(action)
    assert action.player is player
    assert action.position is position
    assert (snapshot(game), pickle.dumps(action)) == before


def test_negative_position_does_not_alias_board(game):
    # The baseline indexes before validation and silently aliases (-1, 3).
    target = game.get_square(game.arena.width - 1, 3)
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.PUSH, game.active_team,
                                                    positions=[target])]
    action = bb.Action(bb.ActionType.PUSH, position=bb.Square(-1, 3))
    before = snapshot(game), pickle.dumps(action)
    with pytest.raises(bb.InvalidActionError):
        game.step(action)
    assert (snapshot(game), pickle.dumps(action)) == before


def assert_rejected_without_mutation(game, action, code, capsys):
    before = snapshot(game), pickle.dumps(action)
    for _ in range(2):
        result = game.validate_action(action)
        assert not result.allowed
        assert result.code == code
        assert result == game.validate_action(action)
        assert not game.is_action_allowed(action)
        assert not game._is_action_allowed(action)
    with pytest.raises(bb.InvalidActionError) as error:
        game.step(action)
    assert error.value.code == result.code
    assert str(error.value) == result.message
    assert (snapshot(game), pickle.dumps(action)) == before
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("action,code", [
    ({}, "invalid_action_type"),
    ("START_MOVE", "invalid_action_type"),
    (bb.Action("START_MOVE"), "invalid_action_type"),
    (bb.Action(bb.ActionType.START_MOVE.value), "invalid_action_type"),
    (bb.Action(bb.ActionType.START_MOVE, player="unknown"), "invalid_player_type"),
    (bb.Action(bb.ActionType.START_MOVE, position=(3, 3)), "invalid_position_type"),
    (bb.Action(bb.ActionType.START_MOVE, position=bb.Square(3.0, 3)), "invalid_coordinates"),
    (bb.Action(bb.ActionType.START_MOVE, position=bb.Square(3, True)), "invalid_coordinates"),
    (bb.Action(bb.ActionType.START_MOVE, position=bb.Square("3", 3)), "invalid_coordinates"),
    (None, "action_required"),
    (bb.Action(bb.ActionType.CONTINUE), "action_required"),
    (bb.Action(bb.ActionType.PLACE_BALL), "action_not_available"),
])
def test_malformed_and_unavailable_actions(game, action, code, capsys):
    assert_rejected_without_mutation(game, action, code, capsys)


@pytest.mark.parametrize("coordinates", [(-1, 3), (3, -1), (-100, 3), (14, 3), (3, 9), (100, 100)])
def test_coordinates_rejected_before_indexing(game, coordinates, capsys):
    assert_rejected_without_mutation(
        game, bb.Action(bb.ActionType.START_MOVE, position=bb.Square(*coordinates)),
        "position_out_of_bounds", capsys)


@pytest.mark.parametrize("kind,code", [("unknown", "unknown_player"),
                                        ("bad_id", "invalid_player_id"),
                                        ("wrong_team", "invalid_player_team"),
                                        ("opponent", "invalid_target")])
def test_player_identity_and_team(game, kind, code, capsys):
    player = deepcopy(game.active_team.players[0])
    if kind == "unknown":
        player.player_id = "not-in-this-game"
    elif kind == "bad_id":
        player.player_id = []
    elif kind == "wrong_team":
        player.team = game.get_opp_team(game.active_team)
    else:
        player = game.get_opp_team(game.active_team).players[0]
    assert_rejected_without_mutation(game, bb.Action(bb.ActionType.START_MOVE, player=player), code, capsys)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_player_choice_cardinality(game, count, capsys):
    players = game.active_team.players
    team = game.active_team
    # No eligible players means there is no START_MOVE choice, as in Turn.
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.END_TURN, team)]
    if count:
        game.state.available_actions.append(bb.ActionChoice(bb.ActionType.START_MOVE, team,
                                                            players=players[:count]))
    for index, player in enumerate(players):
        action = bb.Action(bb.ActionType.START_MOVE, player=player)
        if index < count:
            before = snapshot(game), pickle.dumps(action)
            assert game.is_action_allowed(action)
            assert (snapshot(game), pickle.dumps(action)) == before
        else:
            code = "invalid_target" if count else "action_not_available"
            assert_rejected_without_mutation(game, action, code, capsys)
    assert game.is_action_allowed(bb.Action(bb.ActionType.END_TURN))


def test_required_multiple_players_and_empty_square(game, capsys):
    select_choices(game, game.active_team.players[:2])
    assert_rejected_without_mutation(game, bb.Action(bb.ActionType.START_MOVE), "invalid_target", capsys)
    assert_rejected_without_mutation(game, bb.Action(bb.ActionType.START_MOVE, position=bb.Square(2, 2)),
                                     "invalid_target", capsys)


def test_optional_singleton_skill_and_explicit_wrong_player(game, capsys):
    players = game.active_team.players
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.USE_SKILL, game.active_team,
                                                    players=[players[0]], skill=bb.Skill.BREAK_TACKLE)]
    action = bb.Action(bb.ActionType.USE_SKILL)
    before = snapshot(game), pickle.dumps(action)
    assert game.is_action_allowed(action)
    assert (snapshot(game), pickle.dumps(action)) == before
    assert_rejected_without_mutation(game, bb.Action(bb.ActionType.USE_SKILL, player=players[1]),
                                     "invalid_target", capsys)


def test_repeated_types_normalize_independently(game):
    player = game.active_team.players[0]
    team = game.active_team
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.SELECT_PLAYER, team, positions=[game.get_square(2, 2)]),
        bb.ActionChoice(bb.ActionType.SELECT_PLAYER, team, players=[player]),
    ]
    action = bb.Action(bb.ActionType.SELECT_PLAYER, player=player)
    before = snapshot(game), pickle.dumps(action)
    assert game.is_action_allowed(action)
    assert (snapshot(game), pickle.dumps(action)) == before


def test_repeated_types_allow_later_position(game):
    team = game.active_team
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.MOVE, team, positions=[game.get_square(2, 2)]),
        bb.ActionChoice(bb.ActionType.MOVE, team, positions=[game.get_square(2, 3)]),
    ]
    assert game.is_action_allowed(bb.Action(bb.ActionType.MOVE, position=bb.Square(2, 3)))


def test_enabled_alternative_remains_available_after_disabled_choice(game):
    team = game.active_team
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.END_TURN, team, disabled=True),
        bb.ActionChoice(bb.ActionType.END_TURN, team),
    ]
    action = bb.Action(bb.ActionType.END_TURN)
    before = snapshot(game), pickle.dumps(action)
    assert game.is_action_allowed(action)
    assert (snapshot(game), pickle.dumps(action)) == before


@pytest.mark.parametrize("use_reroll", [False, True])
@pytest.mark.parametrize("with_gym", [False, True], ids=["core", "gym-mask"])
def test_disabled_block_choice_and_gym_mask_wait_for_reroll(game, use_reroll, with_gym, capsys):
    if with_gym:
        pytest.importorskip("gym", reason="Issue #17: legacy Gym is a separate 3.11/3.12 capability")
        from botbowl.ai.env import BotBowlEnv, EnvConf

    attacker = game.active_team.players[1]
    defender = game.get_opp_team(game.active_team).players[0]
    attacker.extra_st = 1 - attacker.get_st()
    attacker.team.state.rerolls = 1
    dice = (bb.BBDieResult.ATTACKER_DOWN, bb.BBDieResult.ATTACKER_DOWN, bb.BBDieResult.DEFENDER_DOWN)
    for result in dice:
        game.dice.fix(bb.BBDie, result)
    game.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    game.step(bb.Action(bb.ActionType.BLOCK, player=defender))
    action = bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN)
    choices = [choice for choice in game.get_available_actions() if choice.action_type == action.action_type]
    assert choices and all(choice.disabled for choice in choices)
    assert game.active_team is attacker.team
    assert_rejected_without_mutation(game, action, "action_not_available", capsys)
    forced = game._forced_action()
    assert game.is_action_allowed(forced)
    assert forced.action_type == bb.ActionType.DONT_USE_REROLL

    if with_gym:
        env = BotBowlEnv(EnvConf(size=3), seed=0, away_agent="human")
        env.game = game
        action_index = env._compute_action_idx(action)
        mask = env.get_state()[2]
        assert not mask[action_index]

    if use_reroll:
        for result in dice:
            game.dice.fix(bb.BBDie, result)
    game.step(bb.Action(bb.ActionType.USE_REROLL if use_reroll else bb.ActionType.DONT_USE_REROLL))
    assert game.active_team is defender.team
    assert attacker.team.state.rerolls == (0 if use_reroll else 1)
    assert game.is_action_allowed(action)
    if with_gym:
        assert env.get_state()[2][action_index]
    game.step(action)
    assert isinstance(game.get_procedure(), bb.Push)


@pytest.mark.parametrize("action", [None, bb.Action(bb.ActionType.CONTINUE)])
def test_no_decision_accepts_none_or_continue(game, action, monkeypatch):
    game.state.available_actions = []
    before = snapshot(game), pickle.dumps(action)
    assert game.validate_action(action) == bb.ActionValidationResult(True, "ok", "Action is allowed.")
    assert game.is_action_allowed(action)
    assert (snapshot(game), pickle.dumps(action)) == before
    received = []
    monkeypatch.setattr(type(game.get_procedure()), "step", lambda self, value: received.append(value) or False)
    game.config.fast_mode = False
    game.step(action)
    assert received == [None]


def test_player_square_and_copied_references_execute_equivalently(game):
    twin = deepcopy(game)
    player = deepcopy(game.active_team.players[0])
    action = bb.Action(bb.ActionType.START_MOVE, player=player)
    alternative = bb.Action(bb.ActionType.START_MOVE, position=copy(player.position))
    before = pickle.dumps(action), pickle.dumps(alternative)
    game.step(action)
    twin.step(alternative)
    assert game.state.to_json(ignore_clocks=True) == twin.state.to_json(ignore_clocks=True)
    assert game.state.active_player is game.get_player(player.player_id)
    assert (pickle.dumps(action), pickle.dumps(alternative)) == before


def test_position_target_accepts_opponent_player_without_query_mutation(game):
    attacker = game.active_team.players[1]
    defender = game.get_opp_team(game.active_team).players[0]
    game.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    twin = deepcopy(game)
    action = bb.Action(bb.ActionType.BLOCK, player=deepcopy(defender))
    before = snapshot(game), pickle.dumps(action)
    assert game.is_action_allowed(action)
    assert (snapshot(game), pickle.dumps(action)) == before
    game.step(action)
    twin.step(bb.Action(bb.ActionType.BLOCK, position=copy(defender.position)))
    assert game.state.to_json(ignore_clocks=True) == twin.state.to_json(ignore_clocks=True)
    assert action.position is None


def test_setup_none_position_and_legacy_explicit_actor_destination(game):
    player = game.active_team.players[0]
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.PLACE_PLAYER, game.active_team,
                                                    players=[player], positions=[None, game.get_square(2, 2)])]
    assert game.is_action_allowed(bb.Action(bb.ActionType.PLACE_PLAYER, player=player))
    assert game.is_action_allowed(bb.Action(bb.ActionType.PLACE_PLAYER, player=player, position=bb.Square(2, 2)))
    assert not game.is_action_allowed(bb.Action(bb.ActionType.PLACE_PLAYER, player=player, position=bb.Square(2, 3)))


def test_legal_crowd_edge_remains_available(game):
    edge = game.get_square(0, 3)
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.PUSH, game.active_team, positions=[edge])]
    assert game.is_action_allowed(bb.Action(bb.ActionType.PUSH, position=bb.Square(0, 3)))


def test_internal_procedure_failure_propagates(game, monkeypatch):
    def broken_step(self, action):
        raise RuntimeError("procedure failure")
    monkeypatch.setattr(type(game.get_procedure()), "step", broken_step)
    with pytest.raises(RuntimeError, match="procedure failure"):
        game.step(bb.Action(bb.ActionType.END_TURN))


def test_internal_lookup_failure_is_not_an_illegal_action(game, monkeypatch):
    def broken_lookup(position):
        raise RuntimeError("board lookup failure")
    monkeypatch.setattr(game, "get_player_at", broken_lookup)
    with pytest.raises(RuntimeError, match="board lookup failure"):
        game.is_action_allowed(bb.Action(bb.ActionType.START_MOVE, position=bb.Square(3, 3)))


def test_bot_normalization_preserves_bot_owned_action(game, monkeypatch):
    action = bb.Action(bb.ActionType.START_MOVE, player=deepcopy(game.active_team.players[0]))
    before = pickle.dumps(action)
    monkeypatch.setattr(type(game.actor), "act", lambda self, current: action)
    normalized = game._safe_act()
    assert normalized is not action
    assert normalized.player is game.get_player(action.player.player_id)
    assert pickle.dumps(action) == before


def test_legacy_type_only_target_is_valid_through_step(game):
    # Reroll callers can carry a previous opponent target even though the choice
    # itself has no target list. Both validations inside step must accept it.
    opponent = game.get_opp_team(game.active_team).players[0]
    game.step(bb.Action(bb.ActionType.END_TURN, position=copy(opponent.position)))


def test_proc_bot_compatibility_shim(game):
    from botbowl.ai.proc_bot import ProcBot

    class PositionBot(ProcBot):
        def turn(self, current_game):
            return action

    action = bb.Action(bb.ActionType.START_MOVE, position=copy(game.active_team.players[0].position))
    bot = PositionBot("validation")
    before = snapshot(game), pickle.dumps(action)
    assert bot.act(game) is action
    assert (snapshot(game), pickle.dumps(action)) == before
    game.step(action)
    assert game.state.active_player.position == action.position
    assert action.player is None


def test_web_action_smoke(game, monkeypatch):
    import botbowl.web.server as server
    from botbowl.web.host import InMemoryHost

    host = InMemoryHost()
    host.add_game(game)
    monkeypatch.setattr(server.api, "host", host)
    client = server.app.test_client()
    response = client.post("/games/baseline/act", json={"action": {
        "action_type": "START_MOVE", "position": {"x": 3, "y": 3}, "player_id": None,
    }})
    assert response.status_code == 200
    assert game.state.active_player.position == game.get_square(3, 3)
    before = snapshot(game)
    with pytest.raises(bb.InvalidActionError):
        server.api.step(game.game_id, bb.Action(bb.ActionType.MOVE, position=bb.Square(-1, 3)))
    assert snapshot(game) == before


def test_public_boundary_with_optimized_python():
    # Explicit checks in the subprocess remain active when asserts are disabled.
    script = """
import botbowl as bb
from tests.baseline import scenario
from tests.framework.test_action_validation import snapshot
with scenario(size=3) as probe:
    game = probe.game
    before = snapshot(game)
    for action in [None, {}, bb.Action('START_MOVE'), bb.Action(bb.ActionType.CONTINUE),
                   bb.Action(bb.ActionType.START_MOVE, position=bb.Square(-1, 3)),
                   bb.Action(bb.ActionType.START_MOVE, player='unknown')]:
        if game.is_action_allowed(action):
            raise RuntimeError('optimized validator accepted illegal input')
        try:
            game.step(action)
        except bb.InvalidActionError:
            pass
        else:
            raise RuntimeError('optimized step accepted illegal input')
        if snapshot(game) != before:
            raise RuntimeError('optimized rejection changed the game')
print('optimized boundary passed')
"""
    result = subprocess.run([sys.executable, "-O", "-c", script], cwd=str(Path(__file__).resolve().parents[2]),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "optimized boundary passed" in result.stdout
