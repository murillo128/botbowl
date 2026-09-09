"""Run a bounded synthetic movement exercise through public lab interfaces."""
from botbowl.lab import create_scenario, scenario_spec


def main():
    session = create_scenario(scenario_spec("movement", size=3, scenario_seed=17))
    try:
        legal = session.legal_actions()
        start = next(a for a in legal.actions if a.type == "START_MOVE")
        session.step(start, legal.state_revision)
        target = session.metadata["initial_positions"]["target"]
        legal = session.legal_actions()
        move = next(a for a in legal.actions if a.type == "MOVE" and a.position
                    and (a.position.x, a.position.y) == target)
        result = session.step(move, legal.state_revision)
        assert result.scenario_terminal and result.scenario_success
        assert not result.terminated
        print("synthetic movement: scenario_terminal; match remains unfinished")
    finally:
        session.close()


if __name__ == "__main__":
    main()
