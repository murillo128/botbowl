"""Fresh-process #39 continuation probe for the public scenario envelope."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from botbowl.lab.scenarios import ScenarioSnapshot, create_scenario
from botbowl.lab.snapshot_io import read_snapshot, snapshot_hash
from tests.lab.scenario_scripts import activate, finish

metadata_path, engine_path, output_path = map(Path, sys.argv[1:])
saved = ScenarioSnapshot.from_metadata(json.loads(metadata_path.read_text()), read_snapshot(engine_path))
session = create_scenario(saved.spec)
try:
    session.restore(saved, session.state_revision)
    before = snapshot_hash(session.snapshot().session.engine)
    if saved.session.accepted_decisions == 0:
        activate(session)
    result = finish(session)
    after = snapshot_hash(session.snapshot().session.engine)
    Path(output_path).write_text(json.dumps({"before": before, "after": after,
                                           "result": result.to_json()}))
finally:
    session.close()
