"""One bounded JSONL episode, no training: python -m examples.lab.recording."""
import argparse
import json
from tempfile import TemporaryDirectory

import botbowl as bb
from botbowl.lab.recording import EpisodeReader, EpisodeRecorder


def record(destination):
    config = bb.load_config('gym-1')
    config.rounds = 1
    config.kick_off_table = False
    config.pathfinding_enabled = False
    game = bb.create_game(config, size=1, seed=17, control='external')
    recorder = EpisodeRecorder(
        game, destination, 'example-17', episode_id='example-17', source_family='example',
        scenario_id='gym-1-short',
        policies={side: {'id': 'end-turn-script', 'version': '1'} for side in ('home', 'away')},
        seed_plan={'schema_version': 1, 'algorithm': 'legacy-numpy-seed', 'sources': {'engine': 17}})
    try:
        for _ in range(100):
            if game.state.game_over:
                break
            legal = recorder.actions.legal_actions()
            choices = {action.type: action for action in legal.actions}
            if 'END_SETUP' in choices and game.is_setup_legal(game.active_team):
                selected = choices['END_SETUP']
            elif legal.macros:
                recorder.execute_macro(legal.macros[0])
                continue
            else:
                selected = next((choices[name] for name in ('HEADS', 'RECEIVE', 'END_TURN')
                                 if name in choices), legal.actions[0])
            recorder.advance(recorder.actions.request(selected))
        manifest = recorder.finish(**({} if game.state.game_over else {'truncation_reason': 'decision_limit'}))
        reader = EpisodeReader(destination, 'example-17')
        episode = reader.read_episode()
        # Only features go to an encoder. Targets/audit require a separate explicit read.
        inputs = reader.read_inputs()
        assert inputs and inputs[0]['features']
        print(json.dumps({'end': manifest.to_json()['end'],
                          'decisions': len(episode['channels']['transitions']),
                          'events': len(episode['channels']['events']),
                          'observations': len(inputs)}, sort_keys=True))
    finally:
        recorder.close()
        game.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', help='Optional output root; example-17 must not exist')
    args = parser.parse_args()
    if args.destination is None:
        with TemporaryDirectory(prefix='botbowl-recording-') as destination:
            record(destination)
    else:
        record(args.destination)


if __name__ == '__main__':
    main()
