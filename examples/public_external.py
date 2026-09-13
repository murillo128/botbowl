"""Both seats controlled externally: three decisions, then close."""
import argparse

from botbowl import Action, ActionType, DecisionResult, StepBudget, create_game


def run(max_steps: int = 100) -> None:
    game = create_game(size=3, seed=17, control="external")
    budget = StepBudget(max_steps)
    actors = set()
    try:
        for action_type in (ActionType.START_GAME, ActionType.HEADS, ActionType.KICK):
            actor = game.actor
            assert actor is not None
            actors.add(actor.agent_id)
            action = Action(action_type)
            assert game.is_action_allowed(action)
            result: DecisionResult = game.advance(action, max_steps=budget)
            assert not result.terminal and result.actor is not None
        assert len(actors) == 2
        print("external: 3 decisions across both seats; next decision pending")
    finally:
        game.close()
    assert game.closed and not game.state.game_over


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=100)
    run(parser.parse_args().max_steps)
