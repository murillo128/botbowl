"""One checked Gymnasium v5 decision with an explicit action mask."""
import argparse

import gymnasium as gym
import numpy as np

from botbowl.ai import register_gymnasium_envs


def run(max_steps: int = 100) -> None:
    register_gymnasium_envs()
    env = gym.make("botbowl-3-v5", max_decisions=3, max_steps=max_steps)
    try:
        observation, info = env.reset(seed=17)
        action = int(np.flatnonzero(observation["action_mask"])[0])
        observation, reward, terminated, truncated, info = env.step(action)
        assert env.observation_space.contains(observation)
        print("gymnasium: actor =", info["acting_team"], "reward =", reward,
              "done =", terminated or truncated)
    finally:
        env.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=100)
    run(parser.parse_args().max_steps)
