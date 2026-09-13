"""Registered policies run only when the existing driver is explicitly called."""
import argparse

from botbowl import (Action, Agent, Game, PolicyDriver, Team, create_game,
                     make_bot, register_bot)


class FirstChoice(Agent):
    def new_game(self, game: Game, team: Team) -> None:
        pass

    def act(self, game: Game) -> Action:
        # The first three pregame decisions need no player or position target.
        action = Action(game.get_available_actions()[0].action_type)
        assert game.is_action_allowed(action)
        return action

    def end_game(self, game: Game) -> None:
        pass


def run(max_steps: int = 100) -> None:
    register_bot("public-first-choice", FirstChoice)
    game = create_game("gym-3", seed=17, control="policy",
                       home_agent=make_bot("public-first-choice"),
                       away_agent=make_bot("public-first-choice"))
    try:
        driver = PolicyDriver(game, {
            game.state.home_team.team_id: game.home_agent.act,
            game.state.away_team.team_id: game.away_agent.act,
        })
        result = driver.run(max_decisions=3, max_steps=max_steps)
        assert len(driver.trace) == 3 and not result.terminal
        print("policy: 3 traced decisions; next decision pending")
    finally:
        game.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=100)
    run(parser.parse_args().max_steps)
