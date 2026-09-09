"""Save, then continue in another interpreter (same installed engine/backend).

python examples/lab/snapshot_files.py save /tmp/game.snapshot.json
python examples/lab/snapshot_files.py resume /tmp/game.snapshot.json
"""
import argparse
import json

import botbowl as bb
from botbowl.lab.snapshot_io import read_snapshot, write_snapshot
from botbowl.lab.snapshots import LogicalTime, capture_snapshot, clone_from_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('save', 'resume'))
    parser.add_argument('path')
    args = parser.parse_args()
    if args.operation == 'save':
        config = bb.load_config('gym-1')
        config.pathfinding_enabled = False
        game = bb.create_game(config, size=1, seed=17, control='external')
        game.time_source = LogicalTime()
        envelope = write_snapshot(args.path, capture_snapshot(game))
        print(json.dumps({'payload_digest': envelope.payload_digest,
                          'semantic_state_hash': envelope.semantic_state_hash}))
    else:
        game = clone_from_snapshot(read_snapshot(args.path))
        result = game.advance(bb.Action(bb.ActionType.START_GAME))
        print(json.dumps({'events': [event.to_json() for event in result.events],
                          'next_actions': [choice.action_type.name for choice in game.get_available_actions()]}))


if __name__ == '__main__':
    main()
