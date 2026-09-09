"""CPU demo: two decisions from one factual boundary plus an immutable forecast.

Run: python -m examples.lab.branches /tmp/botbowl-branches-demo
The destination must not contain existing left/right replay directories.
"""
import json
import sys
from pathlib import Path

from botbowl.lab.branches import BranchSpec, BranchTree
from botbowl.lab.chance import ChancePolicy
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig, SimulationSession


def main(destination):
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    source = SimulationSession(SessionConfig(size=1), SeedSpec(46, 'branches-demo'))
    factual = SimulationSession.from_snapshot(source.snapshot())
    source.close()
    tree = None
    try:
        legal = factual.legal_actions()
        factual.step(legal.actions[0], legal.state_revision)
        tree = BranchTree(factual.snapshot(), origin_family_id='demo-origin', max_decisions=4)
        boundary = tree.export()['snapshots'][0]['context']
        prediction = tree.import_prediction({
            'schema_version': 1, 'kind': 'predicted', 'prediction_id': 'forecast',
            'origin_family_id': tree.origin_family_id, 'branch_id': boundary['branch_id'],
            'parent_snapshot_id': 'origin', 'model': {'model_id': 'example-data', 'version': 'v1'},
            'issued_at': boundary, 'available_history': {'through': boundary, 'references': []},
            'horizon': 2, 'output': {'example_score': 0.6},
            'metadata': {'note': 'Illustrative imported data; no model was run.'}, 'revision_of': None,
        })
        for name, action in zip(('left', 'right'), factual.legal_actions().actions):
            branch = tree.fork(tree.root_snapshot, BranchSpec(
                branch_id=name, parent_branch_id=tree.root_snapshot.branch_id,
                parent_snapshot_id='origin', policy={'policy_id': 'first-legal', 'version': 'v1'},
                horizon=2, initial_action=action,
                chance=ChancePolicy(seed=SeedSpec(46, 'branches-demo', component_id=name)),
            ))
            branch.run(lambda observation, legal: legal.actions[0],
                       policy_id='first-legal', version='v1')
            branch.export_replay(output, name, replay_id=name)
        legal = factual.legal_actions()
        factual.step(legal.actions[0], legal.state_revision)
        tree.observe(factual.snapshot(), snapshot_id='later-observed')
        assert tree.export()['predictions'][0] == prediction.to_json()
        (output / 'tree.json').write_text(json.dumps(tree.export(), indent=2) + '\n', encoding='utf-8')
        print('Wrote tree.json and two executable ReplayV1 directories to', output)
    finally:
        if tree is not None:
            tree.close()
        factual.close()


if __name__ == '__main__':
    main(sys.argv[1])
