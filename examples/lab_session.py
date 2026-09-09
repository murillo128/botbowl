"""Drive both teams through the local public session from any working directory."""
from typing import Sequence

from botbowl.lab import SessionConfig, SimulationSession
from botbowl.lab.actions import ActionV1
from botbowl.lab.randomness import SeedSpec


def choose(actions: Sequence[ActionV1], name: str) -> ActionV1:
    return next(action for action in actions if action.type == name)


def main() -> None:
    simulation = SimulationSession(
        SessionConfig(size=1),
        SeedSpec(17, "session-example", "engine", "example-v1"),
    )
    actors = []
    try:
        for name in ("START_GAME", "HEADS", "RECEIVE"):
            legal = simulation.legal_actions()
            actors.append(legal.actor_id)
            result = simulation.step(choose(legal.actions, name), legal.state_revision)
        actors.append(result.next_actor)
        assert set(actors) == {"home", "away"}
        assert result.primary["descriptor"]["channel"] == "primary"
        assert result.control["descriptor"]["channel"] == "control"
        print("session: both teams supplied decisions; next actor=" + str(result.next_actor))
    finally:
        simulation.close()


if __name__ == "__main__":
    main()
