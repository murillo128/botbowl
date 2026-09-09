"""Fresh-process ReplayV1 acceptance worker."""

import json
import sys
from pathlib import Path

from botbowl.lab.replays import ReplayReader
from botbowl.lab.snapshot_io import snapshot_hash
from botbowl.lab.snapshots import capture_snapshot


def main():
    root, relative, decision, output = sys.argv[1:]
    reader = ReplayReader(root, relative)
    import botbowl as bb

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay called init or a policy")

    bb.Game.init = forbidden
    bb.Agent.act = forbidden
    game = reader.replay_all()
    assert game.timeline.context.decision_seq == int(decision)
    Path(output).write_text(
        json.dumps(
            {
                "context": game.timeline.context.to_json(),
                "events": [event.to_json() for event in game.timeline.events],
                "hash": snapshot_hash(capture_snapshot(game)),
                "replayed": reader.last_seek_replayed,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
