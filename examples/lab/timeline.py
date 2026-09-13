"""Complete bounded timeline example: python examples/lab/timeline.py."""
import json

import botbowl as bb
from botbowl.lab.actions import ActionControl
from botbowl.lab.timeline import Timeline


def main():
    config = bb.load_config('gym-1')
    config.rounds = 1
    config.pathfinding_enabled = False
    game = bb.create_game(config, size=1, seed=17, control='external')
    timeline = Timeline(game, episode_id='example-17')
    game.enable_forward_model()

    # The trusted checkpoint owns engine/RNG state and the timeline payload.
    checkpoint = game.capture_checkpoint()
    first = game.advance(bb.Action(bb.ActionType.START_GAME)).decisions[0]
    game.restore_checkpoint(checkpoint)
    assert game.advance(bb.Action(bb.ActionType.START_GAME)).decisions[0] == first
    timeline.fork('continuation')

    control = ActionControl(game)
    for _ in range(100):
        if game.state.game_over:
            break
        legal = control.legal_actions()
        types = {action.type: action for action in legal.actions}
        if 'END_SETUP' in types and game.is_setup_legal(game.active_team):
            selected = types['END_SETUP']
        elif legal.macros:
            control.execute_macro(legal.macros[0])
            continue
        else:
            selected = next((types[name] for name in ('HEADS', 'RECEIVE', 'END_TURN')
                             if name in types), legal.actions[0])
        game.advance(control.decode(control.request(selected)))
    assert game.state.game_over, 'example decision limit exceeded'
    print(json.dumps(timeline.to_json(), indent=2))
    game.close()


if __name__ == '__main__':
    main()
