"""Save an episode, then resume its natural pregame prefix in a fresh process."""
from dataclasses import asdict
import os

import botbowl as bb
from botbowl.examples._common import arguments, write
from botbowl.lab.episodes import EpisodeContext
from botbowl.lab.snapshot_io import read_snapshot, snapshot_hash, write_snapshot
from botbowl.lab.snapshots import LogicalTime, capture_snapshot, clone_from_snapshot


ACTIONS = ('START_GAME', 'HEADS', 'RECEIVE')


def continuation(episode):
    results = []
    while episode.decisions < episode.manifest()['max_decisions']:
        results.append(asdict(episode.step({'action_type': ACTIONS[episode.decisions]})))
    return {'results': results, 'state_hash': snapshot_hash(capture_snapshot(episode, scope='episode')),
            'manifest': episode.manifest(), 'decisions': episode.decisions}


def main():
    parser = arguments(__doc__)
    parser.set_defaults(max_decisions=3)
    parser.add_argument('operation', choices=('save', 'resume'))
    args = parser.parse_args()
    if not 2 <= args.max_decisions <= 3:
        parser.error('--max-decisions must be 2 or 3 for this pregame prefix')
    path = args.output / 'episode.snapshot.json'
    if args.operation == 'save':
        config = bb.load_config('gym-1')
        config.pathfinding_enabled = False
        rules = bb.load_rule_set(config.ruleset)
        episode = EpisodeContext(config, rules, bb.load_arena(config.arena),
            bb.load_team_by_filename('human', rules, board_size=1),
            bb.load_team_by_filename('human', rules, board_size=1),
            episode_key='quickstart-snapshot', master_seed=args.seed,
            max_decisions=args.max_decisions, max_steps=args.max_steps)
        try:
            episode.game.time_source = LogicalTime()
            episode.step({'action_type': ACTIONS[0]})
            args.output.mkdir(parents=True, exist_ok=True)
            write_snapshot(path, capture_snapshot(episode, scope='episode'))
            write(args.output / 'expected.json', continuation(episode))
            write(args.output / 'save-process.json', {'pid': os.getpid()})
        finally:
            episode.close()
    else:
        episode = clone_from_snapshot(read_snapshot(path))
        try:
            manifest = episode.manifest()
            if (episode.master_seed != args.seed or manifest['max_decisions'] != args.max_decisions
                    or manifest['max_steps_per_decision'] != args.max_steps):
                parser.error('arguments must match the saved episode; budgets and RNG are restored')
            write(args.output / 'actual.json', continuation(episode))
            write(args.output / 'resume-process.json', {'pid': os.getpid()})
        finally:
            episode.close()


if __name__ == '__main__':
    main()
