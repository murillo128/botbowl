"""Falsifiable SIM-01 recipes, scheduling, authority and restoration contracts."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import multiprocessing
import os
import pickle
import random
import subprocess
import sys

import numpy as np
import pytest

import botbowl as bb
from botbowl.core import procedure
from botbowl.lab.adapters import LegacyBotAdapter
from botbowl.lab.channels import make_channel, project_inputs
from botbowl.lab.episodes import EpisodeCompatibilityError, EpisodeContext
from botbowl.lab.randomness import PURPOSES, SeedSpec, capture_stream
from botbowl.lab.rules import describe_rules
from tests.baseline import progress_action
from tests.lab.test_rules_descriptor import inputs


SIZES = (1, 3, 5, 7, 11)
PREFIX = [{"action_type": name} for name in ("START_GAME", "HEADS", "RECEIVE")]


def episode(size=1, key="episode", seed=0, pathfinding=False, **kwargs):
    resources = inputs(size)
    resources[0].pathfinding_enabled = pathfinding
    return EpisodeContext(*resources, episode_key=key, master_seed=seed, **kwargs)


def fingerprint(context):
    dice = context.game.capture_rng_state()
    return {"views": [context.observe(side) for side in ("home", "away")],
            "control": context.decision_control(),
            "rng": {name: getattr(dice, name) for name in ("rng_state", "queues", "strict", "_scopes")},
            "streams": {name: capture_stream(rng) for name, rng in context._streams.items()},
            "decisions": context.decisions, "truncated": context.truncated}


def test_canonical_recipe_and_known_answer():
    recipe = SeedSpec(0, "episode-7", "engine", "dice")
    assert recipe.canonical_bytes() == (
        b'{"component_id":"dice","derivation_version":1,"episode_key":"episode-7",'
        b'"master_seed":0,"purpose":"engine"}')
    assert recipe.seed_words() == (2768529318, 666394704, 3105048887, 814156850,
                                   3866148329, 3914011016, 3738053069, 21235154)
    reference = np.random.RandomState(recipe.seed_words())
    actual = recipe.generator()
    assert capture_stream(actual) == capture_stream(reference)
    np.testing.assert_array_equal(actual.normal(size=9), reference.normal(size=9))
    assert SeedSpec(**json.loads(json.dumps(recipe.to_json()))) == recipe


def test_every_recipe_axis_separates_the_declared_stream():
    recipe = SeedSpec(0, "e", "engine", "c")
    variants = [recipe, replace(recipe, master_seed=1), replace(recipe, episode_key="f"),
                replace(recipe, component_id="d"), replace(recipe, derivation_version=2)]
    variants += [replace(recipe, purpose=purpose) for purpose in PURPOSES if purpose != "engine"]
    assert len({spec.seed_words() for spec in variants}) == len(variants)
    # Compare deterministic digests, not a statistical claim about sampled values.
    assert SeedSpec(2**256 - 1).master_seed == 2**256 - 1


@pytest.mark.parametrize("field,value", [
    ("master_seed", value) for value in (-1, 2**256, True, 1.0, "1", np.int64(1))
] + [("episode_key", value) for value in ("", "../private", "https://x", "secret=x", "a" * 129, 1)]
  + [("component_id", value) for value in (None, "a/b", "a\\b")]
  + [("purpose", value) for value in ("policy", None, [])]
  + [("derivation_version", value) for value in (0, -1, True, 1.0, "1", 2**32)])
def test_invalid_recipes(field, value):
    with pytest.raises(ValueError):
        SeedSpec(**{field: value})


def test_omitted_seed_is_returned_and_does_not_seed_globals(monkeypatch):
    before = pickle.dumps((random.getstate(), np.random.get_state()))
    monkeypatch.setattr("botbowl.lab.randomness.secrets.randbits", lambda bits: 2**255 + bits)
    context = episode(seed=None)
    assert context.master_seed == 2**255 + 256
    assert {spec["master_seed"] for spec in context.manifest()["sources"].values()} == {context.master_seed}
    assert fingerprint(context) == fingerprint(episode(seed=context.master_seed))
    assert pickle.dumps((random.getstate(), np.random.get_state())) == before


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("pathfinding", (False, True))
def test_reverse_creation_and_permuted_worker_doubles(size, pathfinding):
    keys = ("first", "second", "third")
    expected = {}
    for key in keys:
        context = episode(size, key, pathfinding=pathfinding)
        results = context.replay(PREFIX, context.manifest())
        expected[key] = results, fingerprint(context), context.manifest()

    # Worker-count doubles are lists of owned contexts, no actual pool product.
    for worker_count in (1, 2, 3):
        contexts = {key: episode(size, key, pathfinding=pathfinding) for key in reversed(keys)}
        workers = [list(reversed(keys))[index::worker_count] for index in range(worker_count)]
        traces = {key: [] for key in keys}
        for action in PREFIX:
            for worker in reversed(workers):
                for key in worker:
                    traces[key].append(contexts[key].step(action))
        for key, context in contexts.items():
            assert (traces[key], fingerprint(context), context.manifest()) == expected[key]


def _process_job(connection, jobs):
    try:
        results = {}
        for size, key in jobs:
            context = episode(size, key, pathfinding=True)
            trace = context.replay(PREFIX, context.manifest())
            results[key] = {"trace": [asdict(item) for item in trace],
                            "state": fingerprint(context), "manifest": context.manifest()}
        connection.send(results)
    finally:
        connection.close()


def process_probe(method, worker_count):
    jobs = [(size, "size-" + str(size)) for size in SIZES]
    ctx = multiprocessing.get_context(method)
    workers = []
    try:
        for index in range(worker_count):
            reader, writer = ctx.Pipe(duplex=False)
            process = ctx.Process(target=_process_job,
                                  args=(writer, list(reversed(jobs))[index::worker_count]))
            process.start()
            writer.close()
            workers.append((process, reader))
        results = {}
        for process, reader in workers:
            assert reader.poll(40), "episode worker did not return"
            results.update(reader.recv())
            process.join(10)
            assert process.exitcode == 0
        return results
    finally:
        for process, reader in workers:
            reader.close()
            if process.is_alive():
                process.terminate()
            process.join(10)
            process.close()


@pytest.mark.parametrize("method", [name for name in ("spawn", "fork", "forkserver")
                                    if name in multiprocessing.get_all_start_methods()])
@pytest.mark.parametrize("workers", (1, 3))
def test_process_start_worker_count_and_pythonhashseed(method, workers):
    # Each interpreter starts with a different hash seed; spawn/fork/forkserver
    # then create episodes locally. No serialized engine snapshots or seed filters.
    script = ("import json; from tests.lab.test_reproducibility import process_probe; "
              "print(json.dumps(process_probe(%r, %r),sort_keys=True))" % (method, workers))
    observed = []
    for hash_seed in ("1", "98765"):
        result = subprocess.run([sys.executable, "-c", script],
                                env={**os.environ, "PYTHONHASHSEED": hash_seed},
                                capture_output=True, text=True, timeout=90, check=True)
        observed.append(json.loads(result.stdout))
    reference = {}
    for size in SIZES:
        key = "size-" + str(size)
        context = episode(size, key, pathfinding=True)
        trace = context.replay(PREFIX, context.manifest())
        reference[key] = {"trace": [asdict(item) for item in trace],
                          "state": fingerprint(context), "manifest": context.manifest()}
    assert observed[0] == observed[1] == json.loads(json.dumps(reference))


class NoisyPolicy:
    def __init__(self):
        self.received = []

    def act(self, view, control, rng):
        self.received.append((view, control, rng))
        rng.normal(size=101)
        priorities = ("START_GAME", "HEADS", "RECEIVE")
        kind = next(name for name in priorities
                    if any(c["action_type"] == name for c in control["choices"]))
        view["players"].clear()
        control["choices"].clear()
        return {"action_type": kind}


class NoisyObserver:
    def transform(self, view, rng):
        rng.normal(size=103)
        view["players"].clear()
        return view


def test_restricted_callbacks_both_seats_and_observer_have_only_their_sources():
    policies = {side: NoisyPolicy() for side in ("home", "away")}
    context = episode(policies=policies)
    reference = episode()
    for action in PREFIX:
        engine = context.game.capture_rng_state()
        policy_states = {side: capture_stream(context._streams["policy-" + side]) for side in policies}
        context.transform(NoisyObserver(), "home")
        assert context.game.capture_rng_state() == engine
        assert policy_states == {side: capture_stream(context._streams["policy-" + side]) for side in policies}
        actor = context.decision_control()["team"]
        assert context.act() == reference.step(action)
        other = "away" if actor == "home" else "home"
        assert capture_stream(context._streams["policy-" + other]) == policy_states[other]
        assert context.game.capture_rng_state() == reference.game.capture_rng_state()
        assert context.observe("home") == reference.observe("home")
    assert all(policy.received for policy in policies.values())
    for side, policy in policies.items():
        for view, control, rng in policy.received:
            assert rng is context._streams["policy-" + side] and rng is not context.game.rng
            assert set(view) == set(context.observe(side))
            assert set(control) == {"team", "choices"}


@pytest.mark.parametrize("purpose", [name for name in PURPOSES if name != "engine"])
def test_direct_source_consumption_does_not_move_any_other_stream(purpose):
    context = episode()
    before = fingerprint(context)
    context._streams[purpose].normal(size=7)
    after = fingerprint(context)
    assert after["streams"].pop(purpose) != before["streams"].pop(purpose)
    assert after == before


def test_scenario_uses_its_own_stream_before_effective_identity_and_reset():
    samples = []

    def scenario(resources, rng):
        samples.append(rng.randint(0, 100, size=8).tolist())
        resources[0].rounds = 2

    resources = inputs(1)
    before = pickle.dumps(resources)
    context = EpisodeContext(*resources, episode_key="scenario", master_seed=0, scenario=scenario)
    assert pickle.dumps(resources) == before
    effective = deepcopy(resources)
    effective[0].rounds = 2
    assert context.manifest()["rules"] == describe_rules(*effective).to_json()
    assert context.manifest()["sources"]["engine"]["seed_words"] == list(SeedSpec(0, "scenario").seed_words())
    original = fingerprint(context)
    context.step(PREFIX[0])
    previous = context.game
    context.reset()
    assert previous.closed and fingerprint(context) == original
    assert samples[0] == samples[1]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("side", ("home", "away"))
@pytest.mark.parametrize("pathfinding", (False, True))
def test_bounded_movement_checkpoint_continuation(size, side, pathfinding):
    context = episode(size, pathfinding=pathfinding)
    game = context.game
    # Explicit microposition, not a random-seed search or full-match claim.
    game.state.stack.items.clear()
    team = game.state.home_team if side == "home" else game.state.away_team
    game.state.current_team = team
    game.state.half = 1
    game.state.weather = bb.WeatherType.NICE
    game.state.pitch.balls.append(bb.Ball(game.get_square(1, 1)))
    turn = procedure.Turn(game, team, half=1, turn=1)
    turn.started = True
    player = next(player for player in team.players if player.role.name == "Blitzer")
    game.put(player, game.get_square(2, 2))
    player.state.moves = player.get_ma()
    team.state.rerolls = 1
    game.set_available_actions()
    context.step({"action_type": "START_MOVE", "player_id": context._control._player(player)})
    if pathfinding:
        assert any(choice.paths for choice in game.get_available_actions())
    backend = "native" if procedure.Pathfinder.__module__.endswith("cython_pathfinding") else "python"
    assert context.manifest()["rules"]["backend_id"] == backend
    with game.dice.force(d3=[3], d6=[1, 6], d8=[8], block_dice=[bb.BBDieResult.PUSH]):
        game.rng.normal()
        for rng in context._streams.values():
            rng.normal()  # Include populated Gaussian caches.
        checkpoint = context.capture_checkpoint()
        initial = fingerprint(context)
        game_initial = deepcopy(game)
        actions = [{"action_type": "MOVE", "position": {"x": 3, "y": 2}},
                   {"action_type": "USE_REROLL"}]

        def continuation():
            results = [context.step(action) for action in actions]
            for rng in context._streams.values():
                rng.normal(size=3)
            return results, fingerprint(context)

        first = continuation()
        assert player.position == game.get_square(3, 2) and player.state.up
        assert team.state.rerolls == 0
        assert game.has_report_of_type(bb.OutcomeType.FAILED_GFI)
        assert game.has_report_of_type(bb.OutcomeType.REROLL_USED)
        assert game.has_report_of_type(bb.OutcomeType.SUCCESSFUL_GFI)
        advanced = deepcopy(game)
        context.restore_checkpoint(checkpoint)
        assert fingerprint(context) == initial
        assert not game.state.compare(game_initial.state)
        assert continuation() == first
        assert not game.state.compare(advanced.state)
        assert game.dice.pending(bb.D3) == (3,) and game.dice.pending(bb.D8) == (8,)
        assert game.dice.pending(bb.BBDie) == (bb.BBDieResult.PUSH,)


def test_engine_consumption_and_observer_exception_do_not_move_other_sources_or_globals():
    class Failure:
        def transform(self, view, rng):
            rng.normal()
            raise LookupError("observer cause")

    global_before = pickle.dumps((random.getstate(), np.random.get_state()))
    context = episode()
    initial = context.capture_checkpoint()
    streams = fingerprint(context)["streams"]
    context.game.rng.normal(size=17)
    assert fingerprint(context)["streams"] == streams
    engine = context.game.capture_rng_state()
    with pytest.raises(LookupError, match="observer cause"):
        context.transform(Failure(), "away")
    assert context.game.capture_rng_state() == engine
    context.restore_checkpoint(initial)
    assert context.capture_checkpoint() == initial
    assert pickle.dumps((random.getstate(), np.random.get_state())) == global_before


def test_liveness_error_keeps_existing_cause_and_can_restore_an_ancestor():
    context = episode(max_steps=1)
    initial = context.capture_checkpoint()
    before = fingerprint(context)
    with pytest.raises(bb.GameTruncatedError) as caught:
        context.step(PREFIX[0])
    assert caught.value.code == "step_budget"
    assert not context.game.state.game_over and context.decisions == 0
    advanced = fingerprint(context)
    with pytest.raises(EpisodeCompatibilityError, match="fresh"):
        context.replay(PREFIX, context.manifest())
    assert fingerprint(context) == advanced
    context.restore_checkpoint(initial)
    assert fingerprint(context) == before


def test_example_runs_against_available_package():
    from examples.lab_reproducibility import main
    main()


def test_manifest_allowlist_and_privileged_state_never_enters_observations_or_features():
    resources = inputs(1)
    resources[0].private_path = "/secret/config"
    resources[0].credentials = {"token": "do-not-export"}
    context = EpisodeContext(*resources, episode_key="public-label", master_seed=17)
    manifest = context.manifest()
    assert set(manifest) == {"manifest_version", "rules", "derivation_algorithm", "generator", "sources",
                             "policies", "scenario_mode", "clock_mode", "max_decisions", "max_steps_per_decision"}
    assert "/secret" not in json.dumps(manifest) and "do-not-export" not in json.dumps(manifest)
    assert "rng_state" not in json.dumps(manifest) and "queues" not in json.dumps(manifest)
    context.game.dice.fix(bb.D6, 6)
    checkpoint = context.capture_checkpoint()
    assert checkpoint.game.rng_state.queues[0][1] == (6,)
    primary = make_channel("primary", context.observe("home"))
    before = project_inputs({"primary": primary})
    assert project_inputs({"primary": primary, "privileged": {"checkpoint": checkpoint},
                           "evaluation": {"provenance": manifest}}) == before
    assert "master_seed" not in json.dumps(before) and "rng_state" not in json.dumps(primary)
    manifest["sources"]["engine"]["seed_words"].clear()
    assert len(context.manifest()["sources"]["engine"]["seed_words"]) == 8


@pytest.mark.parametrize("field", ("manifest_version", "rules", "derivation_algorithm", "generator",
                                   "sources", "policies", "clock_mode", "max_decisions"))
def test_incompatible_replay_fails_before_actions_or_rng(field):
    context = episode()
    manifest = context.manifest()
    manifest[field] = "incompatible"
    before = fingerprint(context)
    with pytest.raises(EpisodeCompatibilityError) as caught:
        context.replay(PREFIX, manifest)
    assert caught.value.code == "episode_incompatible"
    assert fingerprint(context) == before


@pytest.mark.parametrize("field", ("config_digest", "config_id", "backend_id", "engine_version",
                                   "ruleset_version", "capabilities_version"))
def test_exact_configuration_backend_and_version_rejection(field):
    context = episode()
    manifest = context.manifest()
    manifest["rules"][field] = "different"
    with pytest.raises(EpisodeCompatibilityError):
        context.require_compatible(manifest)


@pytest.mark.parametrize("change", ("config", "backend", "rules"))
def test_changed_live_inputs_cannot_replay_or_restore_old_provenance(change, monkeypatch):
    context = episode()
    manifest = context.manifest()
    checkpoint = context.capture_checkpoint()
    if change == "config":
        context.game.config.rounds += 1
    elif change == "backend":
        monkeypatch.setattr(procedure, "Pathfinder", object)
    else:
        monkeypatch.setattr(bb.Rules, "agility_table", [1, 2, 3])
    before = fingerprint(context)
    for operation in (lambda: context.replay(PREFIX, manifest),
                      lambda: context.restore_checkpoint(checkpoint)):
        with pytest.raises(EpisodeCompatibilityError):
            operation()
        assert fingerprint(context) == before


@pytest.mark.parametrize("action", (None, {}, {"action_type": []}, {"action_type": "BOGUS"},
                                    {"action_type": "START_GAME", "extra": 1},
                                    {"action_type": "START_GAME", "player_id": "home:999"},
                                    {"action_type": "MOVE", "position": {"x": True, "y": 2}},
                                    {"action_type": "END_TURN"}))
def test_invalid_action_is_atomic(action):
    context = episode()
    before = fingerprint(context)
    with pytest.raises(bb.InvalidActionError):
        context.step(action)
    assert fingerprint(context) == before


@pytest.mark.parametrize("kwargs", ({"max_decisions": -1}, {"max_decisions": True},
                                    {"max_steps": 0}, {"max_steps": True},
                                    {"component_ids": {"engine": "x"}},
                                    {"policies": {"third": NoisyPolicy()}},
                                    {"policies": {"home": LegacyBotAdapter(bb.Agent("bot"))}},
                                    {"scenario": 7}))
def test_invalid_context_arguments(kwargs):
    with pytest.raises(ValueError):
        episode(**kwargs)


def test_decision_budget_reset_and_clock_do_not_manufacture_a_defeat(monkeypatch):
    contexts = []
    for timestamp in (0.0, 10**12):
        resources = inputs(1)
        resources[0].competition_mode = True
        resources[0].time_limits.turn = 0
        context = EpisodeContext(*resources, master_seed=17, episode_key="clock", max_decisions=3)
        monkeypatch.setattr("botbowl.core.game.time.time", lambda: timestamp)

        def forbidden(*args, **kwargs):
            pytest.fail("Lab episode enforced competitive clocks")

        monkeypatch.setattr(context.game, "_check_clocks", forbidden)
        trace = context.replay(PREFIX, context.manifest())
        assert trace[-1].truncated and not trace[-1].terminal
        before = fingerprint(context)
        assert context.step({"action_type": "END_TURN"}).truncated
        assert fingerprint(context) == before
        assert all(team.state.score == 0 for team in context.game.state.teams)
        contexts.append((trace, before))
    assert contexts[0] == contexts[1]
    context.reset()
    assert context.decisions == 0 and not context.truncated
    assert context.replay(PREFIX, context.manifest()) == contexts[0][0]
    zero = episode(max_decisions=0)
    assert zero.truncated and zero.replay(iter(PREFIX), zero.manifest()) == []


def test_natural_terminal_is_distinct_from_budget_and_replay_is_detached():
    resources = inputs(1)
    resources[0].rounds = 1
    resources[0].pathfinding_enabled = False
    context = EpisodeContext(*resources, master_seed=0, episode_key="episode", max_decisions=100)
    actions, trace = [], []
    while not context.game.state.game_over and not context.truncated:
        action = progress_action(context.game)
        data = action.to_json()
        data["player_id"] = context._control._player(action.player)
        actions.append(data)
        trace.append(context.step(data))
    assert trace[-1].terminal and not trace[-1].truncated
    assert context.step(PREFIX[0]).terminal
    context.reset()
    assert context.replay(actions, context.manifest()) == trace
    if trace[0].events:
        trace[0].events[0]["event"] = "changed"
        assert context.game.state.reports[0].outcome_type.name != "changed"


def test_callback_exceptions_reset_failure_close_and_checkpoint_rejections():
    class FailingPolicy:
        def act(self, view, control, rng):
            rng.normal()
            raise RuntimeError("policy cause")

    context = episode(policies={"away": FailingPolicy()})
    checkpoint = context.capture_checkpoint()
    initial = fingerprint(context)
    with pytest.raises(RuntimeError, match="policy cause"):
        context.act()
    assert context.decisions == 0 and context.game.capture_rng_state() == checkpoint.game.rng_state
    context.restore_checkpoint(checkpoint)
    assert fingerprint(context) == initial
    foreign = episode(policies={"away": FailingPolicy()})
    with pytest.raises(ValueError, match="another"):
        foreign.restore_checkpoint(checkpoint)
    assert fingerprint(foreign) == initial
    context.step(PREFIX[0])
    future = context.capture_checkpoint()
    context.restore_checkpoint(checkpoint)
    with pytest.raises(ValueError, match="ancestor"):
        context.restore_checkpoint(future)
    with context.game.dice.force(d6=[6]):
        expired = context.capture_checkpoint()
    with pytest.raises(ValueError, match="contexts"):
        context.restore_checkpoint(expired)
    assert fingerprint(context) == initial
    malformed = replace(checkpoint, streams=(("scenario", ("bad",)),))
    with pytest.raises(EpisodeCompatibilityError):
        context.restore_checkpoint(malformed)
    assert fingerprint(context) == initial

    def failure(inputs, rng):
        rng.normal()
        raise RuntimeError("scenario cause")

    context._scenario = failure
    with pytest.raises(RuntimeError, match="scenario cause"):
        context.reset()
    assert fingerprint(context) == initial and not context.game.closed
    context._scenario = None
    context.reset()
    with pytest.raises(ValueError, match="another"):
        context.restore_checkpoint(checkpoint)
    context.close()
    context.close()
    with pytest.raises(bb.InvalidActionError, match="closed"):
        context.step(PREFIX[0])
