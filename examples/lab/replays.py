"""Record, seek, and fork ReplayV1: python -m examples.lab.replays."""

import json
from tempfile import TemporaryDirectory

import botbowl as bb
from botbowl.lab.replays import ReplayReader, ReplayRecorder


def choose(recorder):
    legal = recorder.actions.legal_actions()
    by_type = {action.type: action for action in legal.actions}
    for name in (
        "START_GAME",
        "HEADS",
        "RECEIVE",
        "END_SETUP",
        "SETUP_FORMATION_WEDGE",
        "END_TURN",
    ):
        action = by_type.get(name)
        if action is not None and (
            name != "END_SETUP"
            or recorder.game.is_setup_legal(recorder.game.active_team)
        ):
            return action
    return legal.actions[0]


def main():
    config = bb.load_config("gym-1")
    config.kick_off_table = False
    config.rounds = 1
    game = bb.create_game(config, size=1, seed=17, control="external")
    with TemporaryDirectory(prefix="botbowl-replay-") as root:
        recorder = ReplayRecorder(
            game,
            root,
            "example",
            replay_id="example",
            origin_family_id="example-family",
            checkpoint_interval=4,
        )
        for _ in range(12):
            if game.state.game_over:
                break
            recorder.advance(recorder.actions.request(choose(recorder)))
        manifest = recorder.close(
            **({} if game.state.game_over else {"truncation_reason": "example_limit"})
        )
        reader = ReplayReader(root, "example")
        middle = manifest["final_context"]["decision_seq"] // 2
        restored = reader.seek_decision(middle)
        branch = reader.resume_at_decision(middle, branch_id="example-branch")
        print(
            json.dumps(
                {
                    "end": manifest["end"],
                    "middle": middle,
                    "middle_context": restored.timeline.context.to_json(),
                    "replayed_from_checkpoint": reader.last_seek_replayed,
                    "branch": branch.game.timeline.context.to_json(),
                    "origin_family_id": branch.origin_family_id,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
