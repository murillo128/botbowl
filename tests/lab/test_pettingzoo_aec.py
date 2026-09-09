"""AEC conformance and engine-backed boundary/equivalence evidence."""
import multiprocessing
import pickle
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip("pettingzoo", reason="Optional multiagent extra")
from pettingzoo.test import api_test, seed_test

import botbowl as bb
from botbowl.ai.gymnasium_env import GymnasiumEnv
from botbowl.lab import SessionConfig, SimulationSession
from botbowl.lab.adapters.pettingzoo_aec import BotBowlAECEnv
from botbowl.lab.randomness import SeedSpec
from tests.baseline import Scenario, place_players, progress_action

SIZES = (1, 3, 5, 7, 11)


def fast_config(size=1, **kwargs):
    config = bb.load_config("gym-%d" % size)
    config.rounds = 1
    config.kick_off_table = False
    config.pathfinding_enabled = False
    return SessionConfig(game_config=config, size=size, **kwargs)


def selected(env, name):
    return next(i for i in np.flatnonzero(env.observe(env.agent_selection)["action_mask"])
                if env.decode_action(i).type == name)


def submit_core(env, action):
    semantic = env._session._actions.encode(action)
    env.step(env.encode_action(semantic))


def until_turn(env):
    for _ in range(100):
        if env._session._game.current_turn() is not None:
            return Scenario(env._session._game, 17, 1)
        submit_core(env, progress_action(env._session._game))
    pytest.fail("turn boundary not reached")


def refresh_fixture(env):
    """Re-read after deliberate test-only engine scenario construction."""
    env._session._game.set_available_actions()
    env._refresh(env._session.observe())


def drain(env, terminated):
    dead = []
    for side in env.agent_iter(max_iter=3):
        _, _, term, trunc, _ = env.last()
        assert term is terminated and trunc is (not terminated)
        assert not env.observe(side)["action_mask"].any()
        with pytest.raises(ValueError):
            env.step(0)
        env.step(None)
        dead.append(side)
    assert dead == ["home", "away"]
    assert env.agents == [] and not env.rewards and not env._cumulative_rewards


@pytest.mark.parametrize("size", SIZES)
def test_official_validators(size):
    def factory():
        return BotBowlAECEnv(fast_config(size, max_decisions=30))
    env = factory()
    try:
        api_test(env, num_cycles=40)
    finally:
        env.close()
    seed_test(factory, num_cycles=40)


@pytest.mark.parametrize("size", SIZES)
def test_all_boundaries_masks_spaces_and_direct_session_equivalence(size):
    config = fast_config(size)
    env = BotBowlAECEnv(config)
    env.reset(seed=17)
    direct = SimulationSession(config, SeedSpec(17, "aec-v1"))
    gym = GymnasiumEnv(size)
    seen, setup_sides = set(), set()
    try:
        for _ in range(220):
            if env._result.terminated:
                break
            side = env.agent_selection
            seen.add(side)
            legal = direct.legal_actions()
            assert side == direct.observe().next_actor == legal.actor_id
            for observer in env.possible_agents:
                obs = env.observe(observer)
                assert env.observation_space(observer).contains(obs)
                assert bool(obs["action_mask"].any()) == (observer == side)
            mask = env.observe(side)["action_mask"]
            enabled = np.flatnonzero(mask)
            assert len(enabled) == len(legal.actions)
            gym.game = env._session._game  # read-only codec comparison with #17
            for index in enabled:
                action = env.decode_action(index)
                assert action in legal.actions
                assert env.encode_action(action) == index
                core = env._session._actions.decode(env._session._actions.request(action))
                assert gym.game.is_action_allowed(core)
                assert gym.encode_action(core) == index
            before = pickle.dumps((env._session._game, env.rewards, env._cumulative_rewards))
            rng = env._session._game.capture_rng_state()
            for bad in (None, True, 1.5, -1, env.codec.n, int(np.flatnonzero(mask == 0)[0])):
                with pytest.raises(ValueError):
                    env.step(bad)
                assert pickle.dumps((env._session._game, env.rewards, env._cumulative_rewards)) == before
                assert env._session._game.capture_rng_state() == rng
            core = progress_action(env._session._game)
            action = env._session._actions.encode(core)
            if action.type.startswith("SETUP_FORMATION_"):
                setup_sides.add(side)
            expected = direct.step(action, legal.state_revision)
            env.step(env.encode_action(action))
            assert env._result.to_json() == expected.to_json()
        else:
            pytest.fail("bounded match did not finish")
        assert seen == setup_sides == {"home", "away"}
        assert env._session._timeline.to_json() == direct._timeline.to_json()
        for side in env.agents:
            assert env.observation_space(side).contains(env.observe(side))
        drain(env, terminated=True)
    finally:
        env.close()
        direct.close()


def test_consecutive_actor_and_numeric_cumulative_rewards(monkeypatch):
    env = BotBowlAECEnv(fast_config(max_decisions=3))
    env.reset(seed=17)
    original = env._session.step
    # Script the public score signal at real decision boundaries to measure
    # accumulation independently of the game scoring rules.
    increments = iter(((2, 3), (5, 7), (11, 13)))

    def scoring_step(action, revision):
        for team, increment in zip(env._session._game.state.teams, next(increments)):
            team.state.score += increment
        return original(action, revision)

    monkeypatch.setattr(env._session, "step", scoring_step)
    assert env.agent_selection == "away"
    env.step(selected(env, "START_GAME"))
    assert env.agent_selection == "away" and env.last()[1] == 3.0
    assert env.rewards == {"home": 2.0, "away": 3.0}
    assert env._cumulative_rewards == env.rewards
    env.step(selected(env, "HEADS"))
    assert env.rewards == {"home": 5.0, "away": 7.0}
    assert env._cumulative_rewards == {"home": 7.0, "away": 7.0}
    actor = env.agent_selection
    assert env.last()[1] == 7.0
    env.step(selected(env, "RECEIVE"))
    expected = {"home": 18.0, "away": 20.0}
    expected[actor] = {"home": 11.0, "away": 13.0}[actor]
    assert env.rewards == {"home": 11.0, "away": 13.0}
    assert env._cumulative_rewards == expected
    assert env.last()[1] == expected["home"]
    env.step(None)
    assert env.last()[1] == expected["away"]
    assert env.rewards == {"away": 0.0}
    env.step(None)
    assert not env.agents
    env.reset(seed=17)
    assert env.last()[1] == 0.0 and env.rewards == {"home": 0.0, "away": 0.0}
    env.close()


def test_casualty_reports_are_not_additional_reward(monkeypatch):
    env = BotBowlAECEnv(fast_config())
    env.reset(seed=17)
    game = env._session._game
    advance = game.advance

    def reported_advance(action, **kwargs):
        player = game.state.home_team.players[0]
        for _ in range(2):
            game.report(bb.Outcome(bb.OutcomeType.CASUALTY, player=player, team=player.team))
        return advance(action, **kwargs)

    monkeypatch.setattr(game, "advance", reported_advance)
    env.step(selected(env, "START_GAME"))
    assert sum(event["kind"] == "report" for event in env._result.events) >= 2
    assert env.rewards == env._cumulative_rewards == {"home": 0.0, "away": 0.0}
    env.step(selected(env, "HEADS"))
    assert env.last()[1] == 0.0
    env.close()


@pytest.mark.parametrize("kind", ("defender", "reroll"))
def test_interruption_selection_is_session_actor(kind):
    env = BotBowlAECEnv(fast_config())
    env.reset(seed=17)
    probe = until_turn(env)
    game = env._session._game
    if kind == "defender":
        attacker, defender = place_players(probe, [(2, 2)], [(3, 2)])
        attacker.extra_st = 1 - attacker.get_st()
        refresh_fixture(env)
        submit_core(env, bb.Action(bb.ActionType.START_BLITZ, player=attacker))
        actor = env.agent_selection
        with game.dice.force(block_dice=[bb.BBDieResult.PUSH] * 3, strict=True):
            env.step(selected(env, "BLOCK"))
        assert env.agent_selection != actor
        assert env.agent_selection == ("home" if defender.team is game.state.home_team else "away")
    else:
        player = place_players(probe, [(2, 2)])[0]
        reserves = game.get_reserves(player.team)
        if player in reserves:
            reserves.remove(player)
        refresh_fixture(env)
        submit_core(env, bb.Action(bb.ActionType.START_MOVE, player=player))
        player.state.moves = player.get_ma()
        player.team.state.rerolls = 1
        refresh_fixture(env)
        actor = env.agent_selection
        with game.dice.force(d6=[1], strict=True):
            env.step(selected(env, "MOVE"))
        assert env.agent_selection == actor
        assert env._result.primary["data"]["decision"]["phase"] == "reroll"
        assert env.observe(actor)["action_mask"][selected(env, "USE_REROLL")]
    assert env.agent_selection == env._session.observe().next_actor
    other = "away" if env.agent_selection == "home" else "home"
    assert not env.observe(other)["action_mask"].any()
    env.close()


@pytest.mark.parametrize("seat", ("home", "away"))
def test_touchdown_reward_is_counted_once_across_automatic_steps(seat):
    env = BotBowlAECEnv(fast_config())
    env.reset(seed=17)
    probe = until_turn(env)
    game = env._session._game
    team = game.state.home_team if seat == "home" else game.state.away_team
    game.get_procedure().team = team
    game.state.current_team = team
    game.set_available_actions()
    x = game.get_opp_endzone_x(team)
    start = x + (1 if x == 1 else -1)
    player = place_players(probe, [(start, 2)], ball=(start, 2))[0]
    ball = game.get_ball()
    ball.is_carried = True
    refresh_fixture(env)
    submit_core(env, bb.Action(bb.ActionType.START_MOVE, player=player))
    submit_core(env, bb.Action(bb.ActionType.MOVE, position=game.get_square(x, 2)))
    assert game.has_report_of_type(bb.OutcomeType.TOUCHDOWN)
    assert env.rewards[seat] == env._cumulative_rewards[seat] == 1.0
    other = "away" if seat == "home" else "home"
    assert env.rewards[other] == 0.0
    submit_core(env, progress_action(game))
    assert env.rewards == {"home": 0.0, "away": 0.0}
    env.close()


@pytest.mark.parametrize("kwargs,steps,reason", [
    ({"max_decisions": 0}, 0, "decision_budget"),
    ({"max_decisions": 1}, 1, "decision_budget"),
    ({"max_steps": 1}, 1, "execution_budget"),
])
def test_administrative_limits(kwargs, steps, reason):
    env = BotBowlAECEnv(fast_config(**kwargs))
    env.reset(seed=17)
    for _ in range(steps):
        env.step(selected(env, "START_GAME"))
    assert env.last()[4]["end_reason"] == reason
    drain(env, terminated=False)
    env.close()


def test_detached_views_clean_close_and_reset():
    env = BotBowlAECEnv(fast_config())
    with pytest.raises(RuntimeError):
        env.observe("home")
    env.reset(seed=17, options={"unused": True})
    original = env.observe("away")
    snapshot = pickle.dumps(env._session._game)
    for side in env.agents:
        obs = env.observe(side)
        obs["observation"][:] = 999
        obs["action_mask"][:] = 0
        assert env.observation_space(side).contains(env.observe(side))
    assert pickle.dumps(env._session._game) == snapshot
    assert np.array_equal(env.observe("away")["observation"], original["observation"])
    with pytest.raises(ValueError):
        env.reset(seed=-1)
    assert pickle.dumps(env._session._game) == snapshot
    previous = env._session
    env.close()
    env.close()
    assert previous.closed and all(env.truncations.values())
    with pytest.raises(RuntimeError):
        env.step(None)
    env.reset(seed=17)
    assert previous.closed and not env._session.closed
    assert np.array_equal(env.observe("away")["observation"], original["observation"])
    assert env.metadata["is_parallelizable"] is False
    env.close()


def spawn_episode(connection):
    env = BotBowlAECEnv(fast_config(max_decisions=3))
    try:
        env.reset(seed=17)
        for agent in env.agent_iter():
            obs, _, term, trunc, _ = env.last()
            env.step(None if term or trunc else int(np.flatnonzero(obs["action_mask"])[0]))
        env.close()
        env.close()
        connection.send((env.agents, multiprocessing.active_children()))
    finally:
        env.close()
        connection.close()


def test_spawn_workers_leave_no_processes():
    context = multiprocessing.get_context("spawn")
    for _ in range(2):
        parent, child = context.Pipe(duplex=False)
        worker = context.Process(target=spawn_episode, args=(child,))
        worker.start()
        child.close()
        try:
            assert parent.poll(30), "spawn worker did not complete"
            assert parent.recv() == ([], [])
            worker.join(10)
            assert worker.exitcode == 0 and not worker.is_alive()
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(10)
            parent.close()
            worker.close()


def test_bounded_example_from_another_directory(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples" / "lab" / "pettingzoo_aec.py"
    subprocess.run([sys.executable, str(script), "--max-decisions", "8"],
                   cwd=tmp_path, check=True, timeout=30)
