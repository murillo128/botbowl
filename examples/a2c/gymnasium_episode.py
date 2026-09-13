"""A bounded A2C worker rollout on v5; no model, training or torch required.

The full a2c_example/a2c_agent and historical checkpoints explicitly retain v4's
feature/action encoding. This example exercises the migrated worker boundary.
"""
import numpy as np
from botbowl import Action, ActionType
from botbowl.ai.gymnasium_env import GymnasiumEnv, RewardWrapper, SinglePlayerWrapper
from examples.a2c.a2c_env import A2C_Reward, validate_action_mask
from examples.a2c.vec_env import VecEnv


def opponent(game):
    """Explicit finite opponent: set up, then prefer ending the turn."""
    for action_type in (ActionType.END_SETUP, ActionType.SETUP_FORMATION_SPREAD,
                        ActionType.SETUP_FORMATION_WEDGE, ActionType.END_TURN,
                        ActionType.END_PLAYER_TURN):
        if action_type is ActionType.END_SETUP and not game.is_setup_legal(game.active_team):
            continue
        action = Action(action_type)
        if game.is_action_allowed(action):
            return action
    for choice in game.get_available_actions():
        if choice.disabled or choice.action_type is ActionType.PLACE_PLAYER:
            continue
        for player in choice.players or [None]:
            for position in choice.positions or [None]:
                action = Action(choice.action_type, player=player, position=position)
                if game.is_action_allowed(action):
                    return action
    raise RuntimeError('Opponent has no legal continuation')


def make_env():
    # Both reward functions run inside opponent scheduling on every decision.
    env = RewardWrapper(GymnasiumEnv(size=1), A2C_Reward('home'), A2C_Reward('away'))
    return SinglePlayerWrapper(env, opponent)


def main():
    envs = VecEnv([make_env(), make_env()], reset_steps=10)
    try:
        _, _, masks, *_ = envs.reset()
        for _ in range(10):
            validate_action_mask(masks)
            actions = [int(np.flatnonzero(mask)[0]) for mask in masks]
            _, _, masks, rewards, _, _, terminated, truncated = envs.step(actions)
            print(rewards, terminated, truncated)
            # Auto-reset preserves final observations and infos in last_infos.
            if np.any(terminated | truncated):
                assert all('final_observation' in info for info in envs.last_infos)
    finally:
        envs.close()


if __name__ == '__main__':
    main()
