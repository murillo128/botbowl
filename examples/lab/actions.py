"""Run with python examples/lab/actions.py; no Gym, display or GPU required."""

import json

import botbowl as bb
from botbowl.lab.actions import ActionControl, ActionRequestV1, ActionV1


def main():
    game = bb.create_game(size=1, seed=17, control="external")
    control = ActionControl(game)

    decision = control.legal_actions()
    print(json.dumps(decision.to_json(), indent=2))

    # A transport may round-trip only strict, copied data.
    selected = ActionV1.from_json(json.loads(json.dumps(decision.actions[0].to_json())))
    request = ActionRequestV1.from_json(
        json.loads(json.dumps(control.request(selected).to_json()))
    )
    engine_action = control.decode(request)
    game.advance(engine_action)

    next_decision = control.legal_actions()
    print("accepted:", selected.type)
    print("next:", next_decision.decision_id, next_decision.actor_id)
    game.close()


if __name__ == "__main__":
    main()
