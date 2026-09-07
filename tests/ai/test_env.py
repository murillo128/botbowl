import pytest
from numpy.random import RandomState
from multiprocessing import get_context
import itertools
from random import randint
from typing import Optional
import numpy as np

import botbowl
from botbowl.ai.env import BotBowlEnv, ScriptedActionWrapper, RewardWrapper, EnvConf
from examples.a2c.a2c_env import A2C_Reward
import gym

from tests.formation_helpers import reduce_roster, setup_game, snapshot


@pytest.mark.parametrize("name", ['botbowl-v4',
                                  'botbowl-11-v4',
                                  'botbowl-7-v4',
                                  'botbowl-5-v4',
                                  'botbowl-3-v4',
                                  'botbowl-1-v4'])
def test_gym_registry(name):
    env = gym.make(name)
    _, _, mask = env.reset()
    done = False
    while not done:
        aa = np.where(mask > 0.0)[0]
        action_idx = np.random.choice(aa, 1)[0]
        (_, _, mask), _, done, _ = env.step(action_idx)


@pytest.mark.parametrize("envs", [('botbowl-v4', 11+1),
                                  ('botbowl-11-v4', 11+1),
                                  ('botbowl-7-v4', 7+1),
                                  ('botbowl-5-v4', 5+1),
                                  ('botbowl-3-v4', 3+1),
                                  ('botbowl-1-v4', 1+1)])
def test_team_sizes(envs):
    env_name, num_players = envs
    env = gym.make(env_name)
    _, _, mask = env.reset()
    for team in env.game.state.teams:
        assert len(team.players) == num_players


def test_seed():
    report_strings = set()
    for i in range(2):
        env = gym.make('botbowl-3-v4')
        env.seed(1)
        policy_rng = RandomState(1)
        done = False
        spatial_obs, non_spatial_obs, mask = env.reset()
        while not done:
            aa = np.where(mask > 0.0)[0]
            action_idx = policy_rng.choice(aa, 1)[0]
            (spatial_obs, non_spatial_obs, mask), reward, done, info = env.step(action_idx)
        report_strings.add("-".join([str(report.outcome_type.value) for report in env.game.state.reports]))
    assert len(report_strings) == 1


def test_compute_action():
    env = BotBowlEnv()

    for action_type in itertools.chain(env.env_conf.positional_action_types, env.env_conf.simple_action_types):
        if type(action_type) is botbowl.Formation:
            continue

        sq = None
        if action_type in env.env_conf.positional_action_types:
            sq = botbowl.Square(x=randint(0, env.width-1), y=randint(0, env.height-1))

        action = botbowl.Action(action_type, position=sq)
        same_action = env._compute_action(env._compute_action_idx(action))[0]
        assert action.action_type == same_action.action_type, f"Wrong type: {action} != {same_action}"
        assert action.position == same_action.position, f"Wrong position: {action} != {same_action}"


@pytest.mark.parametrize("size", (1, 3, 5, 7, 11))
@pytest.mark.parametrize("home", (False, True))
@pytest.mark.parametrize("reduced", (False, True))
def test_extra_formation_generator_expands_to_legal_setup(size, home, reduced):
    stock = botbowl.load_formation("def_spread", size=size)
    custom = botbowl.Formation("Custom", [list(row) + ['-'] for row in stock.formation])
    conf = EnvConf(size=size, extra_formations=(f for f in [custom]))
    assert conf.formations[-1] is custom
    env = BotBowlEnv(conf)
    env.game = setup_game(size, home)
    if reduced:
        reduce_roster(env.game, 1)
    team = env.game.active_team
    before = snapshot(env.game)
    actions = env._compute_action(conf.simple_action_types.index(custom))
    assert snapshot(env.game) == before
    assert actions[-1].action_type is botbowl.ActionType.END_SETUP
    for action in actions[:-1]:
        assert env.game.is_action_allowed(action)
        env.game.step(action)
    assert env.game.is_setup_legal(team)
    env.game.step(actions[-1])


@pytest.mark.parametrize("invalid", ("type", "dimensions", "symbol", "count", "scrimmage", "wings"))
def test_env_conf_rejects_invalid_extra_formations(invalid):
    formation = botbowl.load_formation("def_spread", size=3)
    if invalid == "type":
        formation = "def_spread"
    elif invalid == "dimensions":
        formation.formation[:] = ["--", "-x", "--"]
    elif invalid == "symbol":
        formation.formation[0][0] = '?'
    elif invalid == "count":
        formation.formation[0][0] = 'x'
    elif invalid == "scrimmage":
        formation.formation[2][5] = '-'
    elif invalid == "wings":
        formation.formation[:] = ["-xx---", "------", "-----0", "------", "------"]
    with pytest.raises(TypeError if invalid == "type" else ValueError):
        EnvConf(size=3, extra_formations=(f for f in [formation]))


def test_reward_and_scripted_wrapper():

    reward_func = A2C_Reward()

    def scripted_func(game) -> Optional[botbowl.Action]:
        available_action_types = [action_choice.action_type for action_choice in game.get_available_actions()]

        if len(available_action_types) == 1 and len(game.get_available_actions()[0].positions) == 0 and len(game.get_available_actions()[0].players) == 0:
            return botbowl.Action(available_action_types[0])

        if botbowl.ActionType.END_PLAYER_TURN in available_action_types and randint(1, 5) == 2:
            return botbowl.Action(botbowl.ActionType.END_PLAYER_TURN)

        return None

    env = BotBowlEnv(EnvConf(size=1))
    env = ScriptedActionWrapper(env, scripted_func)
    env = RewardWrapper(env, home_reward_func=reward_func)

    rewards = []
    own_tds = []
    opp_tds = []

    for _ in range(10):
        _, _, mask = env.reset()
        done = False
        ep_reward = 0.0

        while not done:
            aa = np.where(mask)[0]
            action_idx = np.random.choice(aa, 1)[0]
            (_, _, mask), reward, done, _ = env.step(action_idx)
            ep_reward += reward

        rewards.append(ep_reward)
        own_tds.append(env.game.state.home_team.state.score)
        opp_tds.append(env.game.state.away_team.state.score)


@pytest.mark.parametrize("pathfinding", [True, False])
def test_observation_ranges(pathfinding):
    def find_first_index(array_: np.ndarray, value_: float):
        indices = (array_ == value_).nonzero()
        return [x[0] for x in indices]

    env = BotBowlEnv(EnvConf(pathfinding=pathfinding))

    for _ in range(2):
        done = False
        spatial_obs, non_spatial_obs, mask = env.reset()

        while not done:

            # Spatial observation are within [0, 1]
            for layer, array in zip(env.env_conf.layers, spatial_obs):
                layer_name = layer.name()
                #array = layer.produce(env.game)

                max_val = np.max(array)
                min_val = np.min(array)

                assert max_val <= 1.0, \
                    f"['{layer_name}'][{find_first_index(array, max_val)}] is too high ({max_val})"

                assert min_val >= 0.0, \
                    f"['{layer_name}'][{find_first_index(array, min_val)}] is too low ({min_val})"

            max_val = np.max(non_spatial_obs)
            min_val = np.min(non_spatial_obs)

            assert min_val >= 0.0, \
                f"non_spatial_obs[{find_first_index(non_spatial_obs, min_val)}] is too low ({min_val})"

            assert max_val <= 1.0, \
                f"non_spatial_obs[{find_first_index(non_spatial_obs, max_val)}] is too high ({max_val})"

            aa = np.where(mask)[0]
            action_idx = np.random.choice(aa, 1)[0]
            (spatial_obs, non_spatial_obs, mask), reward, done, _ = env.step(action_idx)

    env.close()


def worker(remote, parent_remote, env: BotBowlEnv):
    parent_remote.close()
    try:
        rnd = np.random.RandomState(env._seed)
        _, _, mask = env.reset()
        while True:
            try:
                command = remote.recv()
            except EOFError:
                break
            if command == 'step':
                aa = np.where(mask > 0.0)[0]
                action_idx = rnd.choice(aa, 1)[0]
                obs, reward, done, info = env.step(action_idx)
                if done:
                    obs = env.reset()
                mask = obs[2]
                remote.send((obs, reward, done, info))
            elif command == 'close':
                break
            else:
                raise ValueError(command)
    finally:
        try:
            env.close()
        finally:
            remote.close()


def test_multiple_gyms():
    ctx = get_context('spawn')
    ps = []
    remotes = []
    try:
        for _ in range(2):
            env = BotBowlEnv()
            remote, work_remote = ctx.Pipe()
            p = ctx.Process(target=worker, args=(work_remote, remote, env))
            p.start()
            work_remote.close()
            ps.append(p)
            remotes.append(remote)
        for _ in range(20):
            for remote in remotes:
                remote.send('step')
            for remote in remotes:
                assert remote.poll(10), 'Gym worker did not respond within its budget'
                obs, reward, done, info = remote.recv()
                assert reward is not None and obs is not None
    finally:
        for remote in remotes:
            try:
                remote.send('close')
            except (BrokenPipeError, EOFError):
                pass
        for p in ps:
            p.join(timeout=10)
        for remote in remotes:
            remote.close()
    assert all(not p.is_alive() and p.exitcode == 0 for p in ps)


def test_a2c_spawn_worker_with_real_gym():
    from botbowl.ai.env import BotBowlWrapper
    from examples.a2c.vec_env import VecEnv

    envs = VecEnv([BotBowlWrapper(BotBowlEnv(EnvConf(size=1, pathfinding=False), seed=0))],
                  reset_steps=1, timeout=10)
    try:
        _, _, masks, *rest = envs.reset()
        action = int(np.flatnonzero(masks[0])[0])
        *obs, terminated, truncated = envs.step([action])
        assert not terminated[0] and truncated[0]
    finally:
        envs.close()
    assert all(not p.is_alive() and p.exitcode == 0 for p in envs.ps)
    assert all(r.closed for r in envs.remotes)
