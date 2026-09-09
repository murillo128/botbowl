"""Short acceptance scripts. Forced dice are TEST intervention, not natural play."""
from botbowl.core.table import BBDieResult


def submit(session, name, position=None, player_id=None):
    legal = session.legal_actions()
    action = next(a for a in legal.actions if a.type == name
                  and (position is None or (a.position and (a.position.x, a.position.y) == tuple(position)))
                  and (player_id is None or a.player_id == player_id))
    return session.step(action, legal.state_revision)


def activate(session):
    family = session.spec.scenario_id
    name = "START_BLOCK" if family == "block" else "START_PASS" if family == "pass_receive" else "START_MOVE"
    return submit(session, name, player_id=session.spec.side + ":0")


def finish(session):
    """Finish an activated exercise using game-local, explicitly test-only dice."""
    family = session.spec.scenario_id
    target = session.metadata["initial_positions"]["target"]
    game = session._session._game  # Privileged test instrumentation only.
    with game.dice.force(d6=[6] * 8, block_dice=[BBDieResult.PUSH], strict=True):
        if family == "block":
            submit(session, "BLOCK")
            if any(a.type == "DONT_USE_REROLL" for a in session.legal_actions().actions):
                submit(session, "DONT_USE_REROLL")
            submit(session, "SELECT_PUSH")
            submit(session, "PUSH")
            result = submit(session, "FOLLOW_UP")
        elif family == "pass_receive":
            result = submit(session, "PASS", target)
        else:
            start = session.metadata["initial_positions"]["acting"][0]
            direction = 1 if session.spec.side == "away" else -1
            for x in range(start[0] + direction, target[0] + direction, direction):
                result = submit(session, "MOVE", (x, target[1]))
    return result
