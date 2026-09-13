#!/usr/bin/env python3
"""A bounded v5 episode, with both teams controlled explicitly by the caller."""
import gymnasium as gym
import numpy as np
from botbowl.ai import register_gymnasium_envs


def main():
    register_gymnasium_envs()
    env = gym.make('botbowl-1-v5', max_decisions=100, render_mode='ansi')
    try:
        obs, info = env.reset(seed=17)
        rng = np.random.default_rng(17)
        for _ in range(100):
            action = int(rng.choice(np.flatnonzero(obs['action_mask'])))
            obs, reward, terminated, truncated, info = env.step(action)
            print(env.render(), reward, info['rewards'])
            if terminated or truncated:
                break
    finally:
        env.close()


if __name__ == '__main__':
    main()
