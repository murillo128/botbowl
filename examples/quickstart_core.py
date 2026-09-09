"""Minimal headless engine quickstart: three decisions, then close."""
import argparse

from botbowl import Action, ActionType, StepBudget, create_game


def run(max_steps: int = 100) -> None:
    game = create_game(size=3, seed=17, control="external")
    budget = StepBudget(max_steps)
    try:
        for action_type in (ActionType.START_GAME, ActionType.HEADS, ActionType.KICK):
            game.advance(Action(action_type), max_steps=budget)
        print("core: 3 decisions; next actor is", game.actor.agent_id)
    finally:
        game.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=100)
    run(parser.parse_args().max_steps)
