"""Generate a CPU-only, legally reached source and a SIM-08 CLI patch.

python -m examples.lab.interventions /tmp/intervention-demo
python -m botbowl.lab.interventions /tmp/intervention-demo/source.json \
    /tmp/intervention-demo/patch.json /tmp/intervention-demo/edited.json
"""
import json
import sys
from pathlib import Path

import botbowl as bb
from botbowl.core import procedure
from botbowl.lab.actions import ActionControl
from botbowl.lab.branches import BranchTree
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig, SimulationSession
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot
from botbowl.lab.snapshot_io import write_snapshot


def main(destination):
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    session = SimulationSession(SessionConfig(size=1), SeedSpec(63, 'interventions-demo'))
    game = clone_from_snapshot(session.snapshot().engine)
    session.close()
    tree = None
    try:
        for _ in range(100):
            current = game.get_procedure()
            if type(current) is procedure.Turn and not current.blitz and not current.quick_snap:
                break
            if type(current) is procedure.Setup:
                formation = next(choice.action_type for choice in game.state.available_actions
                                 if choice.action_type.name.startswith('SETUP_FORMATION_'))
                action = bb.Action(bb.ActionType.END_SETUP if game.is_setup_legal(current.team)
                                   else formation)
            else:
                control = ActionControl(game)
                choices = control.legal_actions().actions
                # End temporary kickoff turns/activations instead of exploring.
                chosen = next((a for a in choices if a.type in ('END_TURN', 'END_PLAYER_TURN')), choices[0])
                action = control.decode(control.request(chosen))
            game.advance(action, max_steps=100000)
        else:
            raise RuntimeError('Did not reach a regular Turn within demo budget')
        tree = BranchTree(capture_snapshot(game), kind='observed', origin_family_id='intervention-demo-63')
        point = tree.root_snapshot
        write_snapshot(output / 'source.json', point.engine, provenance={
            'snapshot_id': point.snapshot_id, 'branch_id': point.branch_id,
            'origin_family_id': point.origin_family_id,
        })
        team = game.state.home_team
        patch = {
            'schema_version': 1, 'branch_id': 'extra-reroll', 'snapshot_id': 'extra-reroll-0',
            'author': 'SIM-08 CPU example', 'reason': 'Inspect one additional home reroll',
            'operations': [{'entity': 'team', 'entity_id': team.team_id, 'field': 'rerolls',
                            'old_value': team.state.rerolls, 'new_value': team.state.rerolls + 1}],
        }
        (output / 'patch.json').write_text(json.dumps(patch, indent=2) + '\n', encoding='utf-8')
        result = tree.intervene(point, patch)
        result.write(output / 'api-edited.json')
        (output / 'tree.json').write_text(json.dumps(tree.export(), indent=2) + '\n', encoding='utf-8')
        print('Wrote source.json, patch.json, api-edited.json and tree.json to', output)
    finally:
        if tree is not None:
            tree.close()
        game.close()


if __name__ == '__main__':
    main(sys.argv[1])
