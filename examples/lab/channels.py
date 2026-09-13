"""Run with python -m examples.lab.channels; no learning, Gym or GPU required."""
import json

import botbowl as bb
from botbowl.lab.channels import (
    export_channels, import_channels, make_channel, project_inputs, select_channels,
)
from botbowl.lab.observations import ObservationControl, observe


def main():
    config = bb.load_config("gym-1")
    rules = bb.load_rule_set(config.ruleset)
    home = bb.load_team_by_filename("human", rules, board_size=1)
    away = bb.load_team_by_filename("human", rules, board_size=1)
    game = bb.Game("channels-example", home, away, bb.Agent("home", human=True),
                   bb.Agent("away", human=True), config, seed=17)
    binding = ObservationControl(game)
    game.init()
    channels = {
        "primary": make_channel("primary", observe(game, binding, "home").to_json()),
        # This mask indexes cached choices, NOT flattened Gym action indices.
        "control": make_channel("control", {
            "action_ids": [str(i) for i in range(len(game.state.available_actions))],
            "action_mask": [not choice.disabled for choice in game.state.available_actions]}),
        "evaluation": make_channel("evaluation", {
            "labels": {"demonstration_target": 1.0}, "estimates": {},
            "provenance": {"source": "synthetic example; not a game prediction"}}),
        "privileged": make_channel("privileged", {"audit": "not read by the encoder"}),
    }
    controller = select_channels(channels, ["control"])["control"]["data"]
    inputs = project_inputs(channels)
    assert "control.action_mask[]" not in inputs["features"]
    print("Controller mask:", controller["action_mask"])
    print("Primary feature paths:", len(inputs["features"]))

    manifest, blobs = export_channels(channels, ["primary", "evaluation", "privileged"])
    manifest = json.loads(json.dumps(manifest))
    requested = []

    def load_blob(name):
        assert name != "privileged"
        requested.append(name)
        return blobs[name]

    public, _ = import_channels(manifest, load_blob, ["primary"])
    encoder_features = project_inputs(public)["features"]
    assert encoder_features == inputs["features"]
    assert requested == ["primary"]
    targets, _ = import_channels(manifest, load_blob, ["evaluation"])
    print("Targets loaded separately:", targets["evaluation"]["data"]["labels"])
    assert requested == ["primary", "evaluation"]


if __name__ == "__main__":
    main()
