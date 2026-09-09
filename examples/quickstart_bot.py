"""Run two registered policies for three bounded decisions."""
import argparse

from botbowl import Action, Agent, Game, PolicyDriver, Team, create_game, make_bot, register_bot


class FirstLegalChoice(Agent):
    def new_game(self, game: Game, team: Team) -> None:
        pass

    def act(self, game: Game) -> Action:
        return Action(game.get_available_actions()[0].action_type)

    def end_game(self, game: Game) -> None:
        pass


def run(max_steps: int = 100) -> None:
    register_bot("quickstart-first", FirstLegalChoice)
    game = create_game(size=3, seed=17, control="policy",
                       home_agent=make_bot("quickstart-first"),
                       away_agent=make_bot("quickstart-first"))
    try:
        driver = PolicyDriver(game, {
            game.state.home_team.team_id: game.home_agent.act,
            game.state.away_team.team_id: game.away_agent.act,
        })
        driver.run(max_decisions=3, max_steps=max_steps)
        print("bot: decisions traced =", len(driver.trace))
    finally:
        game.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=100)
    run(parser.parse_args().max_steps)
