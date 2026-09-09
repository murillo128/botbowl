"""Two CPU policies, with a strict decision budget and no training.

Install botbowl[multiagent], then run this file from any directory.
"""
import argparse

import numpy as np

from botbowl.lab import SessionConfig
from botbowl.lab.adapters.pettingzoo_aec import BotBowlAECEnv


def random_policy(env, observation, rng):
    return int(rng.choice(np.flatnonzero(observation["action_mask"])))


def progress_policy(env, observation, rng):
    choices = np.flatnonzero(observation["action_mask"])
    for name in ("START_GAME", "HEADS", "RECEIVE", "END_SETUP", "SETUP_FORMATION_WEDGE",
                 "END_TURN", "END_PLAYER_TURN"):
        for index in choices:
            if env.decode_action(index).type == name:
                return int(index)
    return int(rng.choice(choices))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-decisions", type=int, default=40)
    args = parser.parse_args()
    env = BotBowlAECEnv(SessionConfig(size=1, max_decisions=args.max_decisions))
    policies = {"home": progress_policy, "away": random_policy}
    streams = {"home": np.random.default_rng(18), "away": np.random.default_rng(19)}
    totals = {side: 0.0 for side in env.possible_agents}
    decisions = 0
    try:
        env.reset(seed=17)
        for side in env.agent_iter():
            observation, reward, terminated, truncated, info = env.last()
            totals[side] += reward
            if terminated or truncated:
                action = None
            else:
                # Only observation['observation'] is the default model vector;
                # the sibling action_mask controls legal action selection.
                action = policies[side](env, observation, streams[side])
                decisions += 1
            env.step(action)
        print("decisions=%d rewards=%s end=%s" % (decisions, totals, info["end_reason"]))
    finally:
        env.close()


if __name__ == "__main__":
    main()
