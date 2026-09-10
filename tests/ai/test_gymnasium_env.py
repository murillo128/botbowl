"""Bounded v5 acceptance: no torch, training, display or GPU required."""
from copy import deepcopy
from itertools import product
import pickle
from unittest.mock import patch

import numpy as np
import pytest

gym = pytest.importorskip('gymnasium', reason='Optional Gymnasium extra')
from gymnasium.utils.env_checker import check_env, data_equivalence

import botbowl as bb
from botbowl.ai.gymnasium_env import (GymnasiumEnv, RewardWrapper,
                                    ScriptedActionWrapper, SinglePlayerWrapper)
from examples.a2c.a2c_env import A2C_Reward
from tests.baseline import progress_action

SIZES = (1, 3, 5, 7, 11)


def fast_env(size=1, **kwargs):
    config = bb.load_config(f'gym-{size}')
    config.rounds = 1
    config.kick_off_table = False
    return GymnasiumEnv(size, config=config, **kwargs)


def assert_observation(env, obs, ended=False):
    assert env.observation_space.contains(obs)
    mask = obs['action_mask']
    assert mask.shape == (env.action_space.n,) and mask.dtype == np.int8
    assert bool(mask.any()) != ended
    for index in np.flatnonzero(mask):
        action = env.decode_action(index)
        assert env.game.validate_action(action).allowed
        assert env.encode_action(action) == index
        reverse = env.encode_action(action, flip=not env._flip())
        decoded = env.decode_action(reverse, flip=not env._flip())
        assert action.to_json() == decoded.to_json()


@pytest.mark.parametrize('size', SIZES)
def test_official_checker(size):
    env = gym.make(f'botbowl-{size}-v5').unwrapped
    # The upstream checker samples without a mask. Constrain only its sample
    # source to this environment's legal set; all official checks run unchanged.
    sample = env.action_space.sample
    reset_obs, _ = env.reset(seed=17)
    # Its determinism probe samples before reset, so use reset-boundary legality.
    with patch.object(env.action_space, 'sample', lambda: sample(mask=reset_obs['action_mask'])):
        check_env(env)
    env.close()


@pytest.mark.parametrize('size', SIZES)
def test_full_episode_every_boundary_mask_decode_and_terminal(size):
    env = fast_env(size)
    obs, info = env.reset(seed=17)
    observed_teams, formation_boundaries = set(), 0
    for _ in range(100):
        assert_observation(env, obs)
        observed_teams.add(info['next_team'])
        action = progress_action(env.game)
        obs, reward, terminated, truncated, info = env.step(env.encode_action(action))
        assert isinstance(reward, float) and not truncated
        assert info['acting_team'] in ('home', 'away')
        if action.action_type.name.startswith('SETUP_FORMATION_'):
            formation_boundaries += 1
            assert info['acting_team'] == info['next_team']
            assert obs['action_mask'][env.encode_action(bb.Action(bb.ActionType.END_SETUP))]
        if terminated:
            break
    assert terminated and observed_teams == {'home', 'away'} and formation_boundaries >= 4
    assert info['next_team'] is None
    assert_observation(env, obs, ended=True)
    assert env.game.end_time is not None
    with pytest.raises(gym.error.ResetNeeded):
        env.step(0)
    env.close()
    env.close()


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('home', (False, True))
def test_all_setup_choices_formations_and_no_position(size, home):
    env = fast_env(size)
    env.reset(seed=17)
    from tests.formation_helpers import setup_game, reduce_roster
    env.game.close()
    env.game = setup_game(size, home)
    env.game.external_control = True
    for reduced in (False, True):
        if reduced:
            reduce_roster(env.game, 1)
        obs = env.get_state()
        assert_observation(env, obs)
        # Every offered player/square combination, including bench None, has an index.
        for choice in env.game.get_available_actions():
            for player, square in product(choice.players or [None], choice.positions or [None]):
                action = bb.Action(choice.action_type, player=player, position=square)
                index = env.encode_action(action)
                if action.action_type is bb.ActionType.END_SETUP:
                    continue
                assert obs['action_mask'][index]
        for choice in env.game.get_available_actions():
            if choice.action_type.name.startswith('SETUP_FORMATION_'):
                clone = deepcopy(env)
                clone.step(clone.encode_action(bb.Action(choice.action_type)))
                assert clone.game.is_setup_legal(clone.game.active_team)
                assert_observation(clone, clone.get_state())
                clone.close()
    env.close()


@pytest.mark.parametrize('invalid', [-1, 10000000, True, 0.5, None, '0', 'masked'])
def test_invalid_indices_leave_all_state_unchanged(invalid):
    env = fast_env()
    obs, _ = env.reset(seed=17)
    if invalid == 'masked':
        invalid = int(np.flatnonzero(obs['action_mask'] == 0)[0])
    env.game.enable_forward_model()
    env.game.dice.fix(bb.D6, 2)
    before = pickle.dumps((env.game, env._steps, env._budget, env.np_random.bit_generator.state))
    with pytest.raises(ValueError):
        env.step(invalid)
    assert pickle.dumps((env.game, env._steps, env._budget, env.np_random.bit_generator.state)) == before
    env.close()


def test_rerolls_actor_changes_offsets_and_unclipped_resources():
    from tests.util import get_custom_game_turn
    env = fast_env(11)
    env.reset(seed=17)
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)], rerolls=10)
    game.external_control = True
    env.game.close()
    env.game = game
    attacker.team.state.rerolls = attacker.team.state.rerolls_start = 10
    attacker.extra_st = 1 - attacker.get_st()
    for die in (bb.BBDieResult.ATTACKER_DOWN,) * 2 + (bb.BBDieResult.DEFENDER_DOWN,):
        game.dice.fix(bb.BBDie, die)
    for action in (bb.Action(bb.ActionType.START_BLOCK, player=attacker),
                   bb.Action(bb.ActionType.BLOCK, position=defender.position)):
        obs, _, _, _, _ = env.step(env.encode_action(action))
        assert_observation(env, obs)
    assert game.active_team is attacker.team
    assert obs['non_spatial'][18] == obs['non_spatial'][19] == 1.25
    select = bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN)
    assert not obs['action_mask'][env.encode_action(select)]
    obs, _, _, _, _ = env.step(env.encode_action(bb.Action(bb.ActionType.DONT_USE_REROLL)))
    assert game.active_team is defender.team
    assert_observation(env, obs)
    assert obs['action_mask'][env.encode_action(select)]
    env.step(env.encode_action(select))
    game.state.active_player = None
    no_player = env.get_state()['non_spatial']
    assert not no_player[44:50].any()
    # Procedure flags start after all 52 fixed fields, even without an actor.
    game.state.available_actions = []
    env._truncated = True
    no_actor = env.get_state()
    assert env.observation_space.contains(no_actor)
    assert not no_actor['non_spatial'][50:52].any()
    for i, proc in enumerate(env.env_conf.procedures):
        assert no_actor['non_spatial'][52+i] == (proc in {type(p) for p in game.state.stack.items})
    env.close()


@pytest.mark.parametrize('kind', ['episode', 'engine'])
def test_step_budget_is_truncation_not_match_result(kind):
    env = fast_env(max_decisions=1) if kind == 'episode' else fast_env(max_steps=1)
    obs, _ = env.reset(seed=17)
    obs, _, terminated, truncated, info = env.step(int(np.flatnonzero(obs['action_mask'])[0]))
    assert truncated and not terminated and not env.game.state.game_over
    assert info['truncation_reason'] == ('decision_budget' if kind == 'episode' else 'step_budget')
    assert env.game.end_time is None
    assert_observation(env, obs, ended=True)
    with pytest.raises(gym.error.ResetNeeded):
        env.step(0)
    env.close()


def test_no_silent_empty_mask_and_seeded_rollout():
    env = fast_env()
    histories = []
    for _ in range(2):
        obs, _ = env.reset(seed=77)
        history = [deepcopy(obs)]
        for _ in range(20):
            result = env.step(env.encode_action(progress_action(env.game)))
            history.append(result)
            if result[2] or result[3]:
                break
        histories.append(history)
    assert data_equivalence(*histories, exact=True)
    env.reset(seed=77)
    env.game.state.available_actions = []
    with pytest.raises(RuntimeError, match='no encodable'):
        env.get_state()
    env.close()
    env.reset(seed=77)
    assert not env.game.closed
    env.close()


def home_reward(game):
    return 2.0


def away_reward(game):
    return -3.0


def script_pregame(game):
    if game.get_procedure().__class__ in (bb.CoinTossFlip, bb.CoinTossKickReceive):
        return progress_action(game)
    return None


def test_numeric_rewards_scripted_states_and_wrapper_order():
    root = fast_env()
    env = ScriptedActionWrapper(RewardWrapper(root, home_reward, away_reward), script_pregame)
    env.reset(seed=17)
    obs, reward, terminated, truncated, info = env.step(root.encode_action(bb.Action(bb.ActionType.START_GAME)))
    assert len(info['transitions']) == 3
    assert info['rewards'] == {'home': 6., 'away': -9.}
    assert reward == -9. and not terminated and not truncated
    for transition in info['transitions']:
        assert root.observation_space.contains(transition['observation'])
        assert transition['info']['rewards'] == {'home': 2., 'away': -3.}
    assert data_equivalence(obs, info['transitions'][-1]['observation'])
    with pytest.raises(ValueError, match='inside'):
        RewardWrapper(env, home_reward)
    env.close()


def test_explicit_single_player_and_reset_rewards():
    env = SinglePlayerWrapper(RewardWrapper(fast_env(), home_reward, away_reward), progress_action)
    obs, info = env.reset(seed=17)
    assert env.unwrapped.game.external_control and info['next_team'] == 'home'
    assert len(info['transitions']) >= 1
    assert info['reset_rewards']['home'] == 2 * len(info['transitions'])
    for _ in range(30):
        action = progress_action(env.unwrapped.game)
        obs, reward, terminated, truncated, info = env.step(env.unwrapped.encode_action(action))
        assert reward == info['rewards']['home'] == 2 * len(info['transitions'])
        assert env.observation_space.contains(obs)
        if terminated or truncated:
            break
        assert info['next_team'] == 'home'
    assert terminated and not truncated
    env.close()


def test_a2c_rewards_are_seat_bound_including_terminal_and_new_game():
    env = fast_env()
    env.reset(seed=17)
    home, away = A2C_Reward('home'), A2C_Reward('away')
    game = env.game
    game.report(bb.Outcome(bb.OutcomeType.TOUCHDOWN, team=game.state.home_team))
    assert home(game) == 1. and away(game) == -1.
    game.state.available_actions = []
    game.state.game_over = True
    game.report(bb.Outcome(bb.OutcomeType.TOUCHDOWN, team=game.state.away_team))
    assert home(game) == -1. and away(game) == 1.
    assert home(game) == away(game) == 0.
    env.reset(seed=17)
    env.game.report(bb.Outcome(bb.OutcomeType.TOUCHDOWN, team=env.game.state.home_team))
    assert home(env.game) == 1. and away(env.game) == -1.
    env.close()


def make_worker_env():
    return fast_env(max_decisions=2)


def test_gymnasium_spawn_workers():
    envs = gym.vector.AsyncVectorEnv([make_worker_env, make_worker_env], context='spawn',
                                    autoreset_mode=gym.vector.AutoresetMode.DISABLED)
    try:
        obs, _ = envs.reset(seed=17)
        for _ in range(2):
            actions = [int(np.flatnonzero(mask)[0]) for mask in obs['action_mask']]
            obs, rewards, terminated, truncated, _ = envs.step(actions)
            assert envs.observation_space.contains(obs)
        assert not terminated.any() and truncated.all()
    finally:
        envs.close()
    assert all(not p.is_alive() for p in envs.processes)


def test_invalid_a2c_episode_is_not_a_sporting_result_and_retains_final_state():
    from examples.a2c.vec_env import VecEnv, WorkerError
    envs = VecEnv([SinglePlayerWrapper(fast_env(), progress_action)], reset_steps=1, timeout=10)
    try:
        *_, masks, reward, scored, conceded, terminated, truncated = envs.reset()
        action = int(np.flatnonzero(masks[0])[0])
        *_, terminated, truncated = envs.step([action])
        assert not terminated[0] and truncated[0]
        assert 'final_observation' in envs.last_infos[0]
        assert 'final_info' in envs.last_infos[0]
        with pytest.raises(WorkerError, match='outside Discrete'):
            envs.step([-1])
    finally:
        envs.close()
    assert all(not p.is_alive() and p.exitcode == 0 for p in envs.ps)


@pytest.mark.parametrize('invalid', [np.zeros((1, 3)), np.ones(3), np.array([[0, 2, 1]]),
                                   np.array([[0, np.nan, 1]]), np.empty((1, 0))])
def test_a2c_rejects_invalid_masks_before_softmax_or_retry(invalid):
    from examples.a2c.a2c_env import validate_action_mask
    with pytest.raises(ValueError):
        validate_action_mask(invalid)
    validate_action_mask(np.array([[0, 1, 0]], dtype=np.int8))


@pytest.mark.parametrize('seat', ('home', 'away'))
def test_base_score_reward_survives_touchdown_and_actor_change(seat):
    from tests.util import get_custom_game_turn
    env = fast_env(11)
    env.reset(seed=17)
    game, (player,) = get_custom_game_turn([(2, 2)], ball_position=(2, 2))
    game.external_control = True
    env.game.close()
    env.game = game
    team = game.state.home_team if seat == 'home' else game.state.away_team
    if player.team is not team:
        game.clear_board()
        player = team.players[0]
        game.put(player, game.get_square(2, 2))
        game.get_procedure().team = team
        game.state.current_team = team
    x = game.get_opp_endzone_x(team)
    start = x + (1 if x == 1 else -1)
    if player.position != game.get_square(start, 2):
        game.move(player, game.get_square(start, 2))
    ball = game.get_ball()
    ball.move_to(player.position)
    ball.is_carried = True
    game.set_available_actions()
    env.step(env.encode_action(bb.Action(bb.ActionType.START_MOVE, player=player)))
    obs, reward, terminated, truncated, info = env.step(
        env.encode_action(bb.Action(bb.ActionType.MOVE, position=game.get_square(x, 2))))
    assert reward == 1.0 and info['rewards'][seat] == 1.0
    assert info['rewards']['away' if seat == 'home' else 'home'] == 0.0
    assert env.observation_space.contains(obs)
    assert game.has_report_of_type(bb.OutcomeType.TOUCHDOWN)
    env.close()


def test_action_shorthand_and_disabled_positional_choices():
    env = fast_env(3)
    env.reset(seed=17)
    game = env.game
    team = game.active_team
    player = team.players[0]
    square = game.get_square(2, 2)
    game.put(player, square)
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.SELECT_PLAYER, team, players=[player])]
    canonical = env.encode_action(bb.Action(bb.ActionType.SELECT_PLAYER, player=player))
    assert env.encode_action(bb.Action(bb.ActionType.SELECT_PLAYER, position=square)) == canonical
    game.state.available_actions = [bb.ActionChoice(bb.ActionType.BLOCK, team, positions=[square])]
    canonical = env.encode_action(bb.Action(bb.ActionType.BLOCK, position=square))
    assert env.encode_action(bb.Action(bb.ActionType.BLOCK, player=player)) == canonical
    game.state.available_actions = [
        bb.ActionChoice(bb.ActionType.MOVE, team, positions=[square], disabled=True),
        bb.ActionChoice(bb.ActionType.SELECT_NONE, team)]
    obs = env.get_state()
    assert not obs['action_mask'][env.encode_action(bb.Action(bb.ActionType.MOVE, position=square))]
    layer = env.env_conf.positional_action_types.index(bb.ActionType.MOVE)
    assert not obs['spatial'][layer].any()
    assert obs['action_mask'][env.encode_action(bb.Action(bb.ActionType.SELECT_NONE))]
    env.close()


class SeededOpponent:
    def reset(self, *, seed=None):
        self.seed = seed

    def __call__(self, game):
        return progress_action(game)


def test_opponent_seed_and_reset_continuation_are_reproducible():
    opponent = SeededOpponent()
    env = SinglePlayerWrapper(fast_env(), opponent)
    seeds = []
    for _ in range(2):
        env.reset(seed=55)
        first = opponent.seed
        env.reset()
        seeds.append((first, opponent.seed))
    assert seeds[0] == seeds[1] and seeds[0][0] != seeds[0][1]
    env.close()


@pytest.mark.parametrize('options', [{'unknown': 1}, [], 1])
def test_unsupported_reset_options_are_rejected_without_closing_game(options):
    env = fast_env()
    env.reset(seed=17)
    before = pickle.dumps(env.game)
    with pytest.raises(ValueError, match='reset options'):
        env.reset(options=options)
    assert pickle.dumps(env.game) == before
    env.close()


def test_registered_decision_budget_reaches_adapter():
    env = gym.make('botbowl-1-v5', max_decisions=1)
    obs, _ = env.reset(seed=17)
    obs, _, terminated, truncated, info = env.step(int(np.flatnonzero(obs['action_mask'])[0]))
    assert not terminated and truncated and not obs['action_mask'].any()
    assert info['truncation_reason'] == 'decision_budget'
    assert env.unwrapped.max_decisions == 1
    env.close()
