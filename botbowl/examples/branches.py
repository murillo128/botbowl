"""Compare two independently sampled alternatives from one traced origin."""
from botbowl.examples._common import arguments, write
from botbowl.lab.branches import BranchSpec, BranchTree
from botbowl.lab.chance import ChancePolicy
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig, SimulationSession
from botbowl.lab.snapshot_io import snapshot_hash


def main():
    parser = arguments(__doc__)
    parser.set_defaults(max_decisions=2)
    args = parser.parse_args()
    source = SimulationSession(SessionConfig(size=1, max_decisions=args.max_decisions + 1,
                                              max_steps=args.max_steps),
                               SeedSpec(args.seed, 'quickstart-branches'))
    saved = source.snapshot()
    source.close()
    source = SimulationSession.from_snapshot(saved)
    tree = None
    try:
        legal = source.legal_actions()
        source.step(legal.actions[0], legal.state_revision)
        origin = snapshot_hash(source.snapshot().engine)
        tree = BranchTree(source.snapshot(), kind='observed', origin_family_id='quickstart-origin',
                          max_decisions=2 * args.max_decisions, max_steps=args.max_steps)
        branches = []
        for name, action in zip(('left', 'right'), source.legal_actions().actions):
            branches.append(tree.fork(tree.root_snapshot, BranchSpec(
                branch_id=name, parent_branch_id=tree.root_snapshot.branch_id,
                parent_snapshot_id='origin', policy={'policy_id': 'first-legal', 'version': 'v1'},
                horizon=args.max_decisions, initial_action=action,
                chance=ChancePolicy(mode='independent', seed=SeedSpec(
                    args.seed, 'quickstart-branches', component_id=name)))))
        right_before = branches[1].observe().to_json()
        events = {}
        for branch in branches:
            rows = []
            def first_legal(observation, legal):
                return legal.actions[0]
            # fork already executes the declared initial action; the replay
            # contains that transition as well as every continuation below.
            for _ in range(args.max_decisions - 1):
                legal = branch.legal_actions()
                if not legal.actions:
                    break
                result = branch.step(first_legal(branch.observe(), legal), branch.state_revision)
                rows.extend(result.events)
            events[branch.branch_id] = rows
            if branch is branches[0] and branches[1].observe().to_json() != right_before:
                raise ValueError('Sibling branch changed')
            branch.export_replay(args.output, branch.branch_id, replay_id=branch.branch_id)
        if snapshot_hash(source.snapshot().engine) != origin:
            raise ValueError('Factual origin changed')
        write(args.output / 'tree.json', tree.export())
        write(args.output / 'comparison.json', {'origin_hash': origin, 'continuation_events': events,
                                               'source_unchanged': True, 'sibling_unchanged': True})
    finally:
        if tree is not None:
            tree.close()
        source.close()


if __name__ == '__main__':
    main()
