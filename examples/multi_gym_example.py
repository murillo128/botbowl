#!/usr/bin/env python3
"""Gymnasium spawn workers; keep the final observation before resetting."""
import gymnasium as gym
import numpy as np
from botbowl.ai.gymnasium_env import GymnasiumEnv


def make_env():
    return GymnasiumEnv(size=1, max_decisions=20)


def main():
    envs = gym.vector.AsyncVectorEnv([make_env, make_env], context='spawn',
                                   autoreset_mode=gym.vector.AutoresetMode.DISABLED)
    try:
        obs, info = envs.reset(seed=17)
        for _ in range(20):
            actions = [int(np.flatnonzero(mask)[0]) for mask in obs['action_mask']]
            obs, rewards, terminated, truncated, info = envs.step(actions)
            print(rewards, terminated, truncated)
            if np.any(terminated | truncated):
                break
    finally:
        envs.close()


if __name__ == '__main__':
    main()
