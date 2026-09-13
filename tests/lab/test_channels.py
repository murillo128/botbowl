"""API-04 leakage boundary, explicit feature authority and independent storage."""
from copy import deepcopy
import json
import pickle

import pytest
import botbowl as bb
from botbowl.lab.channels import (
    CHANNELS, ENRICHED_PROFILE, PRIMARY_PROFILE, InputProfile, channel_schema,
    export_channels, import_channels, input_profile, make_channel, project_inputs,
    select_channels,
)
from botbowl.lab.observations import ObservationControl, observe
from tests.baseline import SIZES, progress_action, semantic_action, semantic_reports
from tests.lab.test_observations import at_state, episode


SENTINEL = "SECRET-label-RNG-future-987654321"


@pytest.fixture
def channels():
    probe, binding = episode(1)
    primary = observe(probe.game, binding, "home").to_json()
    return {
        "primary": make_channel("primary", primary, {
            "episode_id": SENTINEL, "content_hash": SENTINEL, "provenance": SENTINEL}),
        "derived": make_channel("derived", {
            "own_tackle_zones": [[0, 1]], "opp_tackle_zones": [[2, 0]],
            "roll_probabilities": [[0.5, 1.0]], "block_dice": [[-2, 3]]}),
        "control": make_channel("control", {
            "action_ids": [SENTINEL, "action-2"], "action_mask": [True, False],
            "context": primary["decision"]}),
        "evaluation": make_channel("evaluation", {
            "labels": {"nested": [{"future_result": SENTINEL}]},
            "estimates": {"value": SENTINEL}, "provenance": {"oracle": SENTINEL}}),
        "privileged": make_channel("privileged", {
            "rng": {"state": SENTINEL}, "forced_queues": [[SENTINEL]],
            "snapshot": {"future": {"labels": [SENTINEL]}}}),
    }


def test_default_is_primary_only_with_identity_and_provenance_outside_features(channels):
    result = project_inputs(channels)
    assert result["features"]
    assert all(path.startswith("primary.") for path in result["features"])
    assert SENTINEL not in json.dumps(result["features"])
    metadata = result["metadata"]
    assert metadata["profile"] == PRIMARY_PROFILE.to_json()
    assert set(metadata["channels"]) == {"primary"}
    assert metadata["channels"]["primary"]["source"]["content_hash"] == SENTINEL
    ids = metadata["channels"]["primary"]["fields"]
    assert ids["primary.players[].id"] == [p["id"] for p in channels["primary"]["data"]["players"]]
    assert not set(ids).intersection(result["features"])
    assert "home:0" not in json.dumps(result["features"])
    assert result["features"]["primary.players[].position.value.x"] == [None] * 4
    assert result["features"]["primary.match.round"] == 0
    assert result["features"]["primary.match.drive.value"] is None


def test_enrichment_and_mask_only_add_declared_paths(channels):
    default = project_inputs(channels)["features"]
    enriched = project_inputs(channels, ENRICHED_PROFILE)["features"]
    assert {path: enriched[path] for path in default} == default
    assert set(enriched) - set(default) == {
        "derived.own_tackle_zones[][]", "derived.opp_tackle_zones[][]",
        "derived.roll_probabilities[][]", "derived.block_dice[][]"}
    assert "control.action_mask[]" not in enriched
    controller = select_channels(channels, ["control"])["control"]["data"]
    assert controller["action_mask"] == [True, False]
    opted = input_profile("enriched", include_action_mask=True)
    result = project_inputs(channels, opted)
    assert result["metadata"]["profile"]["include_action_mask"] is True
    assert set(result["features"]) - set(enriched) == {"control.action_mask[]"}
    assert result["features"]["control.action_mask[]"] == controller["action_mask"]
    assert SENTINEL not in json.dumps(result["features"])
    partial = InputProfile("custom", (("derived.own_tackle_zones[][]", "number"),))
    assert project_inputs(channels, partial)["features"] == {"derived.own_tackle_zones[][]": [[0, 1]]}


@pytest.mark.parametrize("path", [
    "*", "primary.*", "primary.players[*].attributes.ma", "primary.players[].*",
    "primary", "primary.players", "primary.players[0].attributes.ma", "primary.match.unknown",
    "evaluation.labels.value", "privileged.rng", "primary.rng", "primary.seed",
    "derived.seed", "derived.rng", "derived.future_result", "derived.evaluation.value",
    "control.context.phase", "control.action_ids[]", "primary.players[].id",
    "primary.players[].slot", "primary.schema_version", "primary.decision.subject.value",
    "primary.metadata.provenance", "primary..match.round", "../privileged",
])
def test_forbidden_unknown_and_wildcard_paths_cannot_be_authorized(path):
    with pytest.raises(ValueError):
        InputProfile("custom", ((path, "integer"),))


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"schema_version": True}, {"schema_version": "1"},
    {"name": "unknown"}, {"name": ["primary"]}, {"fields": "primary"},
    {"fields": [{"path": "primary.match.round", "type": "number"}]},
    {"fields": [{"path": "primary.match.round", "type": "integer", "extra": True}]},
    {"fields": [{"path": ["primary"], "type": "integer"}]},
    {"fields": [{"path": "primary.match.round", "type": "integer"}] * 2, "name": "custom"},
    {"include_action_mask": True}, {"include_action_mask": 1},
    {"fields": [{"path": "control.action_mask[]", "type": "boolean"}], "name": "custom"},
    {"fields": []}, {"unknown": "ignored?"},
])
def test_invalid_profiles_rejected(change):
    data = {**PRIMARY_PROFILE.to_json(), **change}
    with pytest.raises(ValueError):
        InputProfile.from_json(data)


@pytest.mark.parametrize("name", ("unknown", "primary-v2", "*", None, []))
def test_unknown_named_profile_rejected(name):
    with pytest.raises(ValueError):
        input_profile(name)


@pytest.mark.parametrize("location", [
    (), ("players", 0), ("players", 0, "attributes"), ("decision",),
    ("decision", "subject"), ("match",), ("geometry",),
])
@pytest.mark.parametrize("field", ("evaluation", "privileged", "rng", "future", "labels"))
def test_nested_contamination_of_primary_rejected_even_on_unselected_fields(channels, location, field):
    data = channels["primary"]["data"]
    target = data
    for part in location:
        target = target[part]
    target[field] = {"nested": [SENTINEL]}
    with pytest.raises(ValueError):
        project_inputs(channels)
    # Selecting one leaf still validates the entire selected primary schema.
    with pytest.raises(ValueError):
        project_inputs(channels, InputProfile("custom", (("primary.match.round", "integer"),)))


@pytest.mark.parametrize("path,value", [
    (("schema_version",), 2), (("schema_version",), True),
    (("players", 0, "attributes", "ma"), True),
    (("players", 0, "skills"), [{"evaluation": SENTINEL}]),
    (("players", 0, "position", "present"), True),
    (("decision", "phase"), "future_private_procedure"),
    (("geometry", "tiles"), [[{"rng": SENTINEL}]]),
    (("match", "round"), 1.0), (("match", "half"), None),
])
def test_primary_types_versions_presence_and_literal_domains(channels, path, value):
    target = channels["primary"]["data"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        project_inputs(channels)


@pytest.mark.parametrize("bad", (float("nan"), float("inf"), True, {"label": SENTINEL}, "0.5"))
def test_derived_leaves_are_finite_numeric_values(channels, bad):
    channels["derived"]["data"]["roll_probabilities"][0][0] = bad
    with pytest.raises(ValueError):
        project_inputs(channels, ENRICHED_PROFILE)


def test_missing_paths_channels_and_mask_types_are_errors(channels):
    del channels["derived"]["data"]["block_dice"]
    with pytest.raises(ValueError, match="Missing selected path"):
        project_inputs(channels, ENRICHED_PROFILE)
    del channels["primary"]["data"]["match"]["round"]
    with pytest.raises(ValueError):
        project_inputs(channels)
    del channels["primary"]
    with pytest.raises(ValueError, match="Missing selected channel"):
        project_inputs(channels)
    with pytest.raises(ValueError):
        make_channel("control", {"action_mask": [1, 0]})


def test_outputs_descriptors_profiles_and_source_containers_are_independent(channels):
    before = deepcopy(channels)
    projected = project_inputs(channels, input_profile("enriched", True))
    projected["features"]["derived.own_tackle_zones[][]"][0][0] = 100
    projected["features"]["control.action_mask[]"].clear()
    projected["metadata"]["channels"]["primary"]["fields"]["primary.players[].id"].clear()
    projected["metadata"]["profile"]["fields"].clear()
    selected = select_channels(channels, ["control", "evaluation"])
    selected["evaluation"]["data"]["labels"].clear()
    selected["control"]["data"]["context"]["options"].append({})
    assert channels == before
    payload = {"action_mask": [True]}
    made = make_channel("control", payload)
    payload["action_mask"].clear()
    assert made["data"]["action_mask"] == [True]
    schema = channel_schema("primary")
    schema["properties"].clear()
    assert channel_schema("primary")["properties"]
    assert InputProfile.from_json(PRIMARY_PROFILE.to_json()) == PRIMARY_PROFILE


def test_default_does_not_touch_unselected_payloads(channels):
    # Poison payloads model storage handles that must not be read, copied or
    # deserialized by default. They are deliberately not projector inputs.
    class Unreadable:
        def __deepcopy__(self, memo):
            pytest.fail("Unselected payload copied")
    baseline = project_inputs(channels)
    for name in ("derived", "control", "evaluation", "privileged"):
        channels[name] = Unreadable()
    assert project_inputs(channels) == baseline


def test_round_trip_and_partial_loading_never_request_privileged_blob(channels):
    profile = input_profile("enriched", True)
    manifest, blobs = export_channels(channels, list(CHANNELS), profile)
    assert SENTINEL not in json.dumps(manifest)
    assert all(type(blob) is str for blob in blobs.values())
    complete, full_profile = import_channels(manifest, blobs.__getitem__, list(CHANNELS))
    assert complete == channels
    assert full_profile == profile
    assert project_inputs(complete, full_profile) == project_inputs(channels, profile)
    requested = []
    # No valid privileged blob exists at read time; JSON parsing it would fail.
    blobs["privileged"] = "not even JSON"

    def load(name):
        assert name in ("primary", "evaluation"), "Unexpected blob read: " + name
        requested.append(name)
        return blobs[name]

    restored, restored_profile = import_channels(json.loads(json.dumps(manifest)), load, ["primary"])
    assert requested == ["primary"]
    assert restored_profile == profile
    assert project_inputs(restored) == project_inputs(channels)
    targets, _ = import_channels(manifest, load, ["evaluation"])
    assert requested == ["primary", "evaluation"]
    assert targets["evaluation"] == channels["evaluation"]
    assert SENTINEL not in json.dumps(project_inputs(restored)["features"])
    restored["primary"]["data"]["players"].clear()
    assert channels["primary"]["data"]["players"]
    public_manifest, public_blobs = export_channels(channels, ["primary"])
    assert set(public_blobs) == set(public_manifest["channels"]) == {"primary"}


@pytest.mark.parametrize("mutation", ("format", "schema", "profile", "channel", "descriptor", "extra"))
def test_invalid_export_manifest_fails_before_loading(channels, mutation):
    manifest, blobs = export_channels(channels, ["primary"])
    if mutation == "format":
        manifest["format_version"] = True
    elif mutation == "schema":
        manifest["channels"]["primary"]["schema_version"] = 2
    elif mutation == "profile":
        manifest["profile"]["name"] = "unknown"
    elif mutation == "channel":
        manifest["channels"]["rng"] = {}
    elif mutation == "descriptor":
        manifest["channels"]["primary"]["channel"] = "evaluation"
    else:
        manifest["ignored"] = SENTINEL
    with pytest.raises(ValueError):
        import_channels(manifest, lambda name: pytest.fail("Read before validation"), ["primary"])


@pytest.mark.parametrize("value", (object(), (1, 2), {1: "invalid"}, float("nan")))
def test_non_json_values_rejected(value):
    with pytest.raises(ValueError):
        make_channel("privileged", {"snapshot": value})


@pytest.mark.parametrize("metadata", (
    {"evaluation": {"labels": SENTINEL}}, {"provenance": {"rng": SENTINEL}},
    {"episode_id": [SENTINEL]}, {"seed": 123}, {"content_hash": None},
))
def test_metadata_cannot_nest_payloads_or_unknown_fields(channels, metadata):
    channels["primary"]["metadata"] = metadata
    with pytest.raises(ValueError):
        project_inputs(channels)


@pytest.mark.parametrize("channel", ("derived", "control"))
@pytest.mark.parametrize("field", ("evaluation", "privileged", "rng", "future_result"))
def test_optional_channels_cannot_smuggle_new_fields(channels, channel, field):
    channels[channel]["data"][field] = SENTINEL
    with pytest.raises(ValueError):
        project_inputs(channels, input_profile("enriched", True))


@pytest.mark.parametrize("names", ("primary", ["*"], ["primary", "primary"], ["rng"], [1]))
def test_channel_selection_is_explicit_and_validated(channels, names):
    with pytest.raises(ValueError):
        select_channels(channels, names)
    with pytest.raises(ValueError):
        export_channels(channels, names)


def test_wrong_blob_channel_duplicate_keys_and_missing_selection_rejected(channels):
    manifest, blobs = export_channels(channels, ["primary", "evaluation"])
    with pytest.raises(ValueError):
        import_channels(manifest, lambda name: blobs["evaluation"], ["primary"])
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        import_channels(manifest, lambda name: '{"data": {}, "data": {}}', ["primary"])
    with pytest.raises(ValueError):
        import_channels(manifest, lambda name: pytest.fail("Read missing blob"), ["privileged"])
    with pytest.raises(ValueError):
        select_channels({"primary": channels["primary"]}, ["evaluation"])
    with pytest.raises(ValueError):
        project_inputs(channels, PRIMARY_PROFILE.to_json())
    channels["primary"]["descriptor"]["schema_version"] = True
    with pytest.raises(ValueError):
        project_inputs(channels)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("side", ("home", "away"))
def test_profiles_leave_same_actions_state_rng_events_and_results(size, side):
    probe, binding = at_state("ground", size, side)
    game = probe.game
    player = game.get_player_at(game.get_square(2, 2))
    game.enable_forward_model()
    probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    player.state.moves = player.get_ma()  # Next action consumes natural GFI RNG.
    game.dice.fix(bb.D6, 6)  # Nonempty forced queue is also preserved by reads.
    reference = deepcopy(game)
    reference_binding = ObservationControl(reference)
    action_traces = [[], []]
    while True:
        before = pickle.dumps(game)
        primary = observe(game, binding, side).to_json()
        available = [not choice.disabled for choice in game.state.available_actions]
        data = {"primary": make_channel("primary", primary),
                "derived": make_channel("derived", {name: [[0.0]] for name in (
                    "own_tackle_zones", "opp_tackle_zones", "roll_probabilities", "block_dice")}),
                "control": make_channel("control", {"action_mask": available})}
        default = project_inputs(data)
        project_inputs(data, ENRICHED_PROFILE)
        project_inputs(data, input_profile("enriched", True))
        assert pickle.dumps(game) == before  # state, queues, trajectory, clocks, events
        reference_inputs = project_inputs({"primary": make_channel(
            "primary", observe(reference, reference_binding, side).to_json())})
        assert default == reference_inputs
        assert game.capture_rng_state() == reference.capture_rng_state()
        assert semantic_reports(game) == semantic_reports(reference)
        assert game.state.game_over == reference.state.game_over
        if game.state.game_over:
            break
        for i, candidate in enumerate((game, reference)):
            # First GFI consumes the forced die; the second consumes natural
            # RNG. Both transitions follow reads using all three profiles.
            count = len(action_traces[i])
            action = (bb.Action(bb.ActionType.MOVE, position=candidate.get_square(3 + count, 2))
                      if count < 2 else progress_action(candidate))
            action_traces[i].append(semantic_action(candidate, action))
            candidate.step(action)
        assert len(action_traces[0]) < 256
    assert action_traces[0] == action_traces[1]
    assert [team.state.score for team in game.state.teams] == [team.state.score for team in reference.state.teams]


def test_headless_inputs_targets_example(capsys):
    from examples.lab.channels import main
    main()
    assert "Targets loaded separately" in capsys.readouterr().out
