"""Complete local session contract over public observations and semantic actions."""
from pathlib import Path
from dataclasses import replace
import pickle
import subprocess
import sys

import pytest

import botbowl as bb
from botbowl.lab import (
    IncompatibleSnapshot,
    InvalidAction,
    InvalidConfiguration,
    NoProgress,
    SessionClosed,
    SessionConfig,
    SimulationSession,
    StaleRevision,
)
from botbowl.lab.actions import ActionV1, EmptyOptionsV1
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.snapshot_io import read_snapshot, write_snapshot
from botbowl.lab.snapshots import capture_snapshot
from tests.baseline import Scenario, place_players, progress_action


SIZES = (1, 3, 5, 7, 11)


def seed(key="session-test", value=17):
    return SeedSpec(value, key, "engine", "session-v1")


def session(size=1, **kwargs):
    return SimulationSession(SessionConfig(size=size, **kwargs), seed("size-%d" % size))


def action_of(actions, name):
    return next(action for action in actions if action.type == name)


@pytest.mark.parametrize("size", SIZES)
def test_two_clients_control_both_seats_with_only_public_data(size):
    simulation = session(size)
    try:
        actors = []
        for wanted in ("START_GAME", "HEADS", "RECEIVE"):
            legal = simulation.legal_actions()
            actors.append(legal.actor_id)
            # Independent clients obtain only their copied side-specific views.
            home = simulation.observe("home")
            away = simulation.observe("away")
            assert home.primary["data"]["observer_team"] == "home"
            assert away.primary["data"]["observer_team"] == "away"
            assert home.state_revision == away.state_revision == legal.state_revision
            result = simulation.step(action_of(legal.actions, wanted), legal.state_revision)
            assert result.events and result.state_revision == legal.state_revision + 1
        actors.append(result.next_actor)
        assert set(actors) == {"home", "away"}
        assert actors[0] == actors[1] == "away"  # consecutive decisions are not hidden
        assert set(result.to_json()) == {
            "primary", "derived", "control", "events", "next_actor",
            "terminated", "truncated", "end_reason", "decision_id", "state_revision",
        }
        assert result.primary["descriptor"]["channel"] == "primary"
        assert result.derived["descriptor"]["channel"] == "derived"
        assert result.control["descriptor"]["channel"] == "control"
    finally:
        simulation.close()


def test_read_operations_and_returned_data_are_detached_and_pure():
    simulation = session(1)
    game = simulation._game  # evidence probe, never part of the consumer API
    before = pickle.dumps(game)
    rng = game.capture_rng_state()
    first = simulation.observe("home")
    legal = simulation.legal_actions("home")
    second = simulation.observe("home")
    assert first.to_json() == second.to_json()
    assert game.capture_rng_state() == rng and pickle.dumps(game) == before

    first.primary["data"]["players"].clear()
    first.control["data"]["action_ids"].clear()
    legal.actions.clear()
    assert simulation.observe("home").primary["data"]["players"]
    assert simulation.legal_actions().actions
    simulation.close()


def test_invalid_actions_and_stale_revisions_are_atomic():
    simulation = session(1)
    before = simulation.observe().to_json()
    rng = simulation._game.capture_rng_state()
    wrong = ActionV1(1, "HEADS", "away", None, None, None, EmptyOptionsV1())
    with pytest.raises(InvalidAction) as rejected:
        simulation.step(wrong, simulation.state_revision)
    assert rejected.value.diagnostic.code == "action_not_offered"
    assert simulation.observe().to_json() == before
    assert simulation._game.capture_rng_state() == rng

    legal = simulation.legal_actions()
    result = simulation.step(legal.actions[0], legal.state_revision)
    before = simulation.observe().to_json()
    graph = pickle.dumps(simulation._game)
    with pytest.raises(StaleRevision):
        simulation.step(simulation.legal_actions().actions[0], legal.state_revision)
    assert simulation.observe().to_json() == before
    assert pickle.dumps(simulation._game) == graph
    assert simulation.state_revision == result.state_revision
    simulation.close()


def test_reset_is_atomic_and_revision_is_monotonic():
    simulation = session(1)
    before = simulation.observe().to_json()
    revision = simulation.state_revision
    with pytest.raises(InvalidConfiguration) as rejected:
        simulation.reset(SessionConfig(size=2), seed("invalid-reset"))
    assert rejected.value.diagnostic.error_type == "ValueError"
    assert simulation.state_revision == revision
    assert simulation.observe().to_json() == before

    reset = simulation.reset(SessionConfig(size=3), seed("valid-reset"))
    assert reset.state_revision == revision + 1
    assert (
        reset.primary["data"]["geometry"]["width"]
        != before["primary"]["data"]["geometry"]["width"]
    )
    simulation.close()


def test_snapshot_replays_randomness_but_does_not_rewind_transport_revision():
    simulation = session(1)
    start = simulation.legal_actions()
    simulation.step(action_of(start.actions, "START_GAME"), start.state_revision)
    saved = simulation.snapshot("engine")
    decision = simulation.legal_actions()
    action = action_of(decision.actions, "HEADS")
    first = simulation.step(action, decision.state_revision)
    restored = simulation.restore(saved, first.state_revision)
    assert restored.state_revision > first.state_revision
    with pytest.raises(StaleRevision):
        simulation.step(action, decision.state_revision)
    replay = simulation.step(
        action_of(simulation.legal_actions().actions, "HEADS"), restored.state_revision
    )
    assert replay.events == first.events
    assert replay.primary["data"] == first.primary["data"]
    assert replay.state_revision > restored.state_revision
    simulation.close()


def test_incompatible_snapshot_is_atomic():
    first, second = session(1), session(3)
    try:
        saved = first.snapshot()
        before = second.observe().to_json()
        revision = second.state_revision
        with pytest.raises(IncompatibleSnapshot):
            second.restore(saved, revision)
        assert second.state_revision == revision
        assert second.observe().to_json() == before
    finally:
        first.close()
        second.close()


def test_persisted_snapshot_reuses_sim03_and_restores_public_ids(tmp_path):
    simulation = session(1)
    try:
        legal = simulation.legal_actions()
        simulation.step(legal.actions[0], legal.state_revision)
        saved = simulation.snapshot()
        path = tmp_path / "engine.json"
        write_snapshot(path, saved.engine)
        loaded = replace(saved, engine=read_snapshot(path))
        legal = simulation.legal_actions()
        first = simulation.step(action_of(legal.actions, "HEADS"), legal.state_revision)
        simulation.restore(loaded, simulation.state_revision)
        legal = simulation.legal_actions()
        replay = simulation.step(action_of(legal.actions, "HEADS"), legal.state_revision)
        assert replay.primary == first.primary
        assert replay.events == first.events
    finally:
        simulation.close()


def test_invalid_snapshot_binding_and_metadata_do_not_mutate_live_state():
    simulation = session(1)
    bare = bb.create_game(size=1, control="external", seed=17)
    try:
        saved = simulation.snapshot()
        before = simulation.observe().to_json()
        graph = pickle.dumps(simulation._game)
        for invalid in (
            replace(saved, engine=capture_snapshot(bare)),
            replace(saved, accepted_decisions=1),
            replace(saved, max_decisions="1000"),
            replace(saved, schema_version=True),
        ):
            with pytest.raises(IncompatibleSnapshot):
                simulation.restore(invalid, simulation.state_revision)
            assert simulation.observe().to_json() == before
            assert pickle.dumps(simulation._game) == graph
        saved.engine._game.state.home_team.state.score = 999
        assert simulation.observe().to_json() == before
        with pytest.raises(IncompatibleSnapshot):
            simulation.restore(saved, simulation.state_revision)
        assert pickle.dumps(simulation._game) == graph
    finally:
        simulation.close()
        bare.close()


def test_close_is_idempotent_and_mutations_fail_typed():
    simulation = session(1)
    saved = simulation.snapshot()
    legal = simulation.legal_actions()
    simulation.close()
    revision = simulation.state_revision
    simulation.close()
    assert simulation.state_revision == revision
    closed = simulation.observe()
    assert closed.truncated and not closed.terminated and closed.end_reason == "session_closed"
    for operation in (
        lambda: simulation.step(legal.actions[0], revision),
        lambda: simulation.restore(saved, revision),
        lambda: simulation.reset(SessionConfig(size=1), seed("closed")),
        simulation.snapshot,
    ):
        with pytest.raises(SessionClosed):
            operation()


def test_decision_and_engine_budgets_are_administrative_not_defeats():
    zero = session(1, max_decisions=0)
    state = zero.observe()
    assert state.truncated and not state.terminated and state.end_reason == "decision_budget"
    assert not zero.legal_actions().actions
    zero.close()

    bounded = session(1, max_steps=1)
    legal = bounded.legal_actions()
    with pytest.raises(NoProgress) as failure:
        bounded.step(legal.actions[0], legal.state_revision)
    assert failure.value.diagnostic.code == "step_budget"
    state = bounded.observe()
    assert state.truncated and not state.terminated and state.end_reason == "execution_budget"
    bounded.close()


def test_no_hidden_policy_invocation(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("session invoked a policy")

    monkeypatch.setattr(bb.Agent, "act", fail)
    simulation = session(1)
    try:
        for wanted in ("START_GAME", "HEADS", "RECEIVE"):
            legal = simulation.legal_actions()
            simulation.step(action_of(legal.actions, wanted), legal.state_revision)
        assert calls == []
    finally:
        simulation.close()


def _submit_core(simulation, core):
    semantic = simulation._actions.encode(core)
    return simulation.step(semantic, simulation.state_revision)


def _until_turn(simulation):
    probe = Scenario(simulation._game, 17, 1)
    for _ in range(180):
        if simulation._game.current_turn() is not None:
            return probe
        _submit_core(simulation, progress_action(simulation._game))
    pytest.fail("turn boundary not reached")


def test_reroll_boundary_retains_trace_and_same_actor():
    simulation = session(1)
    probe = _until_turn(simulation)
    player = place_players(probe, [(2, 2)])[0]
    reserves = simulation._game.get_reserves(player.team)
    if player in reserves:
        reserves.remove(player)
    _submit_core(simulation, bb.Action(bb.ActionType.START_MOVE, player=player))
    player.state.moves = player.get_ma()
    player.team.state.rerolls = 1
    simulation._game.set_available_actions()
    acting = simulation.legal_actions().actor_id
    move = action_of(simulation.legal_actions().actions, "MOVE")
    with simulation._game.dice.force(d6=[1], strict=True):
        result = simulation.step(move, simulation.state_revision)
    assert result.next_actor == acting
    assert result.primary["data"]["decision"]["phase"] == "reroll"
    assert any(event["kind"] == "report" for event in result.events)
    simulation.close()


def test_defender_interruption_exposes_the_actual_next_actor():
    simulation = session(1)
    probe = _until_turn(simulation)
    attacker, defender = place_players(probe, [(2, 2)], [(3, 2)])
    attacker.extra_st = 1 - attacker.get_st()
    _submit_core(simulation, bb.Action(bb.ActionType.START_BLITZ, player=attacker))
    acting = simulation.legal_actions().actor_id
    block = action_of(simulation.legal_actions().actions, "BLOCK")
    with simulation._game.dice.force(block_dice=[bb.BBDieResult.PUSH] * 3, strict=True):
        result = simulation.step(block, simulation.state_revision)
    assert result.next_actor != acting
    expected = "home" if defender.team is simulation._game.state.home_team else "away"
    assert result.next_actor == expected
    assert result.primary["data"]["decision"]["phase"] == "block"
    simulation.close()


@pytest.mark.parametrize("size", SIZES)
def test_public_clients_finish_a_match_without_internals(size):
    config = bb.load_config("gym-%d" % size)
    config.rounds = 1
    config.kick_off_table = False
    config.pathfinding_enabled = False
    simulation = SimulationSession(SessionConfig(game_config=config, size=size), seed("terminal"))
    actors = set()
    previous = None
    try:
        for _ in range(220):
            state = simulation.observe()
            if state.terminated:
                break
            legal = simulation.legal_actions()
            actors.add(legal.actor_id)
            client_view = simulation.observe(legal.actor_id)
            assert client_view.primary["data"]["observer_team"] == legal.actor_id
            choices = {action.type: action for action in legal.actions}
            priorities = ["START_GAME", "HEADS", "RECEIVE"]
            if previous and previous.startswith("SETUP_FORMATION_"):
                priorities.append("END_SETUP")
            priorities.extend(["SETUP_FORMATION_SPREAD", "SETUP_FORMATION_WEDGE", "END_TURN"])
            selected = next((choices[name] for name in priorities if name in choices), legal.actions[0])
            simulation.step(selected, legal.state_revision)
            previous = selected.type
        else:
            pytest.fail("bounded game did not terminate")
        result = simulation.observe()
        assert result.terminated and not result.truncated and result.end_reason == "game_over"
        assert result.next_actor is None and not simulation.legal_actions().actions
        assert actors == {"home", "away"}
    finally:
        simulation.close()


def test_external_example_runs_from_another_working_directory(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples" / "lab_session.py"
    subprocess.run([sys.executable, str(script)], cwd=tmp_path, check=True, timeout=30)
