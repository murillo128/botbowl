"""Public recipes, honest end semantics and isolated persistent continuation."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from botbowl.core.procedure import EndGame
from botbowl.lab.randomness import SeedSpec, capture_stream
from botbowl.lab.scenarios import (
    SCENARIO_RECIPES, ScenarioError, ScenarioSnapshot, ScenarioSpecV1,
    create_scenario, scenario_spec,
)
from botbowl.lab.session import IncompatibleSnapshot, NoProgress, SessionClosed, StaleRevision
from botbowl.lab.snapshot_io import read_snapshot, snapshot_hash, write_snapshot
from tests.lab.scenario_scripts import activate, finish, submit


CASES = [(name, size, side) for name, recipe in SCENARIO_RECIPES.items()
         for size in recipe.sizes for side in ("home", "away")]


@pytest.mark.parametrize("family,size,side", CASES)
def test_repeatability_policy_independence_and_isolation(family, size, side):
    spec = scenario_spec(family, size=size, side=side, scenario_seed=17)
    a = create_scenario(spec)
    policy = SeedSpec(99, family, "policy-" + side).generator()
    policy.random_sample(100)
    b = create_scenario(spec)
    try:
        assert a.observe() == b.observe()
        assert a.legal_actions() == b.legal_actions()
        assert a.observe().next_actor == side
        assert a._session._game is not b._session._game
        assert a._session._game.rng is not b._session._game.rng
        assert capture_stream(a._session._game.rng) == capture_stream(b._session._game.rng)
        assert snapshot_hash(a.snapshot().session.engine) == snapshot_hash(b.snapshot().session.engine)
        before = b.observe()
        rng_before = capture_stream(b._session._game.rng)
        activate(a)
        a._session._game.rng.random_sample()
        a._session._game.state.home_team.state.rerolls = 3
        assert b.observe() == before
        assert capture_stream(b._session._game.rng) == rng_before
        spec.parameters["rerolls"] = 2
        a.spec.parameters["rerolls"] = 3
        assert a.spec.parameters["rerolls"] == b.spec.parameters["rerolls"] == 0
        metadata = a.metadata
        assert metadata["construction"] == "synthetic-turn-v1"
        assert metadata["reachability"] == "structurally_validated"
        assert not metadata["legal_reachability_proven"]
        assert set(metadata["difficulty"]) == {"participants", "space", "distance", "obstacles", "resources", "horizon", "policy"}
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("family,size,side", CASES)
def test_short_script_demonstrates_expected_interaction(family, size, side):
    s = create_scenario(scenario_spec(family, size=size, side=side))
    try:
        activate(s)
        result = finish(s)
        assert result.scenario_terminal and result.scenario_success
        assert not result.terminated and not result.truncated
        assert result.end_reason == "scenario_terminal"
        assert not s._session._game.state.game_over
        assert result.next_actor is None and result.decision_id is None
        assert not s.legal_actions().actions
        assert result.control["data"]["action_ids"] == []
        assert result.control["data"]["action_mask"] == []
        kinds = {r.outcome_type.name for r in s._session._game.state.reports}
        expected = {"pickup": {"SUCCESSFUL_PICKUP"}, "pass_receive": {"ACCURATE_PASS", "SUCCESSFUL_CATCH"},
                    "block": {"BLOCK_ROLL", "PUSHED"}, "possession_recovery": {"SUCCESSFUL_PICKUP"},
                    "touchdown": {"TOUCHDOWN"}, "movement": set()}
        assert expected[family] <= kinds
        assert s._session._accepted_decisions <= s.spec.max_decisions
        revision = s.state_revision
        assert s.step({}, revision) == s.observe()
        assert s.state_revision == revision
        with pytest.raises(StaleRevision):
            s.step({}, revision - 1)
    finally:
        s.close()


@pytest.mark.parametrize("family", SCENARIO_RECIPES)
@pytest.mark.parametrize("side", ["home", "away"])
def test_distance_and_resource_variants(family, side):
    distance = 1 if family == "block" else 3
    s = create_scenario(scenario_spec(family, side=side, parameters={"distance": distance, "rerolls": 2}))
    try:
        activate(s)
        assert finish(s).scenario_success
    finally:
        s.close()


@pytest.mark.parametrize("family", SCENARIO_RECIPES)
@pytest.mark.parametrize("checkpoint", ["initial", "activation"])
def test_snapshot_start_and_fresh_process_continuation(family, checkpoint, tmp_path):
    s = create_scenario(scenario_spec(family, size=3, scenario_seed=42))
    restored = create_scenario(s.spec)
    try:
        initial = s.snapshot()
        engine_path = tmp_path / "state.json"
        metadata_path = tmp_path / "session.json"
        output_path = tmp_path / "continued.json"
        write_snapshot(engine_path, initial.session.engine)
        decoded = ScenarioSnapshot.from_metadata(initial.metadata(), read_snapshot(engine_path))
        restored.restore(decoded, restored.state_revision)
        assert snapshot_hash(restored.snapshot().session.engine) == snapshot_hash(initial.session.engine)
        assert restored.legal_actions().actions == s.legal_actions().actions
        if checkpoint == "activation":
            activate(s)
        saved = s.snapshot()
        metadata_path.write_text(json.dumps(saved.metadata()))
        write_snapshot(engine_path, saved.session.engine)
        before = snapshot_hash(saved.session.engine)
        if checkpoint == "initial":
            activate(s)
        result = finish(s)
        after = snapshot_hash(s.snapshot().session.engine)
        subprocess.run([sys.executable, str(Path(__file__).with_name("scenario_process.py")),
                        str(metadata_path), str(engine_path), str(output_path)],
                       check=True, cwd=tmp_path, timeout=30)
        output = json.loads(output_path.read_text())
        assert output["before"] == before and output["after"] == after
        assert output["result"]["events"] == list(result.events)
        assert output["result"]["scenario_success"]
        # Terminal state is derived from the restored engine, not a lost wrapper flag.
        restored.restore(s.snapshot(), restored.state_revision)
        assert restored.observe().scenario_terminal
        restored.restore(initial, restored.state_revision)
        assert not restored.observe().scenario_terminal
    finally:
        s.close()
        restored.close()


@pytest.mark.parametrize("change", [
    {"scenario_id": "os.system"}, {"version": True}, {"version": 2}, {"size": 2},
    {"side": "../home"}, {"parameters": {"module": "os"}}, {"parameters": {"distance": 0}},
    {"parameters": {"distance": 4}}, {"parameters": {"rerolls": True}},
    {"parameters": {"rerolls": -1}}, {"parameters": {"rerolls": 4}},
    {"scenario_seed": -1}, {"scenario_seed": True}, {"scenario_seed": 2**256},
    {"max_decisions": 0}, {"max_decisions": 257}, {"max_steps": 0}, {"max_steps": 10001},
    {"end_condition": "eval"}, {"parameters": []},
])
def test_closed_spec_rejects_invalid_fields(change):
    with pytest.raises(ScenarioError):
        replace(scenario_spec("movement"), **change)


def test_spec_json_and_incompatible_rules_and_sizes():
    spec = scenario_spec("movement")
    assert ScenarioSpecV1.from_json(spec.to_json()) == spec
    for field in ("procedure_stack", "path", "pickle", "callback"):
        data = spec.to_json()
        data[field] = "untrusted"
        with pytest.raises(ScenarioError):
            ScenarioSpecV1.from_json(data)
    with pytest.raises(ScenarioError):
        scenario_spec("pass_receive", size=1)
    with pytest.raises(ScenarioError):
        scenario_spec("movement", size=1, parameters={"distance": 2})
    with pytest.raises(ScenarioError):
        create_scenario(replace(spec, rules=replace(spec.rules, config_digest="sha256:wrong")))
    with pytest.raises(TypeError):
        SCENARIO_RECIPES["untrusted"] = SCENARIO_RECIPES["movement"]


def test_variant_identity_and_scenario_seed_are_separate():
    spec = scenario_spec("movement")
    assert spec.variant_id == replace(spec, scenario_seed=42).variant_id
    for variant in (replace(spec, parameters={"distance": 2}), replace(spec, max_decisions=8),
                    replace(spec, side="away"), replace(spec, parameters={"rerolls": 1}),
                    scenario_spec("movement", size=5)):
        assert variant.variant_id != spec.variant_id
    assert spec.rules != scenario_spec("movement", size=5).rules


@pytest.mark.parametrize("damage", ["occupancy", "dugout", "ball", "resources", "actions"])
def test_invalid_construction_closes_candidate_without_partial_publication(monkeypatch, damage):
    from botbowl.lab import scenarios
    good = create_scenario(scenario_spec("pickup"))
    before = snapshot_hash(good.snapshot().session.engine)
    original = scenarios._construct
    candidates = []

    def broken(candidate, spec):
        original(candidate, spec)
        candidates.append(candidate)
        game = candidate._game
        player = game.state.home_team.players[0]
        if damage == "occupancy":
            game.state.pitch.board[player.position.y][player.position.x] = None
        elif damage == "dugout":
            game.get_reserves(player.team).append(player)
        elif damage == "ball":
            game.get_ball().is_carried = True
        elif damage == "resources":
            player.team.state.rerolls = -1
        else:
            game.state.available_actions = []

    monkeypatch.setattr(scenarios, "_construct", broken)
    try:
        with pytest.raises(ScenarioError):
            create_scenario(good.spec)
        assert candidates[0].closed
        assert snapshot_hash(good.snapshot().session.engine) == before
    finally:
        good.close()


def test_budgets_failure_close_and_restore_are_distinct():
    s = create_scenario(scenario_spec("pickup", max_decisions=1))
    try:
        initial = s.snapshot()
        result = activate(s)
        assert result.truncated and result.end_reason == "decision_budget"
        assert not result.scenario_terminal and not result.terminated
        assert not s.legal_actions().actions
        s.restore(initial, s.state_revision)
        assert not s.observe().truncated
        foreign = create_scenario(scenario_spec("movement", max_decisions=1))
        try:
            before = s.observe()
            with pytest.raises(IncompatibleSnapshot):
                s.restore(foreign.snapshot(), s.state_revision)
            assert s.observe() == before
        finally:
            foreign.close()
    finally:
        s.close()
    assert s.observe().end_reason == "session_closed"
    with pytest.raises(SessionClosed):
        s.step({}, s.state_revision)

    s = create_scenario(scenario_spec("movement", max_steps=1))
    try:
        activate(s)
        with pytest.raises(NoProgress):
            submit(s, "MOVE", s.metadata["initial_positions"]["target"])
        assert s.observe().end_reason == "execution_budget"
        assert s.observe().truncated and not s.observe().scenario_terminal
    finally:
        s.close()


@pytest.mark.parametrize("failure", ["end_turn", "pickup_failure"])
def test_failed_exercise_is_scenario_terminal_without_a_match_result(failure):
    s = create_scenario(scenario_spec("pickup"))
    try:
        if failure == "end_turn":
            result = submit(s, "END_TURN")
        else:
            activate(s)
            with s._session._game.dice.force(d6=[1], d8=[1], strict=True):
                result = submit(s, "MOVE", s.metadata["initial_positions"]["target"])
        assert result.scenario_terminal and not result.scenario_success
        assert not result.terminated and not result.truncated
        assert result.end_reason == "scenario_terminal"
    finally:
        s.close()


def test_success_on_last_decision_wins_over_budget():
    s = create_scenario(scenario_spec("movement", max_decisions=2))
    try:
        activate(s)
        assert finish(s).end_reason == "scenario_terminal"
    finally:
        s.close()


@pytest.mark.parametrize("family", SCENARIO_RECIPES)
def test_natural_end_stays_natural_after_an_exercise(family):
    s = create_scenario(scenario_spec(family))
    try:
        activate(s)
        finish(s)
        # Test instrumentation of the underlying session's natural terminal path.
        game = s._session._game
        game.state.stack.items.clear()
        EndGame(game)
        game.state.available_actions = []
        game.advance(None)
        result = s.observe()
        assert result.terminated and result.end_reason == "game_over"
        assert not result.scenario_terminal and not result.truncated
    finally:
        s.close()
