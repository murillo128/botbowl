"""API-06 geometry, identity alignment, declared losses and primary-only purity."""
from copy import deepcopy
import json
import pickle
import subprocess
import sys

import numpy as np
import pytest

from botbowl.lab.actions import ActionControl, ActionV1, EmptyOptionsV1, PathOptionsV1, PositionV1
from botbowl.lab.channels import PRIMARY_PROFILE, make_channel
from botbowl.lab.observations import observe
from botbowl.lab.rendering import BALL_RGB, CROWD_RGB, ENDZONE_RGB, PLAYER_RGB, TARGET_RGB, render_grid
from botbowl.lab.views import (
    ENTITY_CHANNELS, TILES, CoordinateFrame, align_targets, coordinate_frame,
    entity_view, graph_view, grid_view,
)
from tests.baseline import SIZES
from tests.lab.test_observations import at_state, episode


def presence(x=None, y=None):
    return {"value": None if x is None else {"x": x, "y": y}, "present": x is not None}


def snapshot(size=3, side="home"):
    probe, binding = episode(size)
    data = observe(probe.game, binding, side).to_json()
    w, h = data["geometry"]["width"], data["geometry"]["height"]
    positions = [(0, 0), (w - 1, h - 1), (1, 2), (w - 2, 2)]
    for player, (x, y) in zip(data["players"], positions):
        player.update(position=presence(x, y), location="pitch")
    data["balls"] = [dict(position=presence(1, 2), carrier={"value": None, "present": False},
                          on_ground=True, is_carried=False)]
    data["decision"]["target_position"] = presence(w - 2, 1)
    return data


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("side", ("home", "away"))
@pytest.mark.parametrize("relative", (False, True))
def test_raw_correspondence_all_sizes_sides_borders_endzones(size, side, relative):
    data = snapshot(size, side)
    team = side if relative else None
    table = entity_view(data, team=team)
    grid = grid_view(data, team=team)
    graph = graph_view(data, team=team)
    frame = coordinate_frame(data, team)
    np.testing.assert_array_equal(graph.nodes.features, table.features)
    np.testing.assert_array_equal(graph.nodes.present, table.present)
    assert graph.nodes.entity_ids == table.entity_ids
    assert table.metadata["logical_time"] == grid.metadata["logical_time"] == graph.metadata["logical_time"]
    assert table.metadata["input_profile"] == grid.metadata["input_profile"] == PRIMARY_PROFILE.to_json()
    for view in (table, grid):
        assert view.features.dtype == np.float32 and view.present.dtype == np.bool_
        assert len(view.metadata["channels"]) == view.features.shape[1 if view is table else 0]
        assert len(view.metadata["units"]) == len(view.metadata["channels"])
        np.testing.assert_array_equal(view.context, table.context)
        np.testing.assert_array_equal(view.context_present, table.context_present)
    assert table.metadata["decision"]["actor_team"] is None
    target = frame.position(PositionV1.from_json(data["decision"]["target_position"]["value"]))
    assert table.context[table.metadata["context_channels"].index("decision.target_x")] == target.x
    for i, player in enumerate(data["players"]):
        assert table.entity_ids[i] == player["id"]
        assert table.entities[i]["team"] == player["team"]
        for attr in ("ma", "st", "ag", "av"):
            assert table.features[i, ENTITY_CHANNELS.index("attributes." + attr)] == player["attributes"][attr]
        p = player["position"]["value"]
        if p is None:
            assert not table.present[i, :2].any()
        else:
            p = frame.position(PositionV1(**p))
            np.testing.assert_array_equal(table.features[i, :2], [p.x, p.y])
    for y, row in enumerate(data["geometry"]["tiles"]):
        for x, tile in enumerate(row):
            tx = frame.position(PositionV1(x, y)).x
            assert grid.features[TILES.index(tile), y, tx] == 1
            assert grid.playable[y, tx] == data["geometry"]["playable"][y][x]
            ids = grid.cell_entity_ids[y][tx]
            rows = [table.entity_ids.index(entity_id) for entity_id in ids]
            if rows:
                np.testing.assert_array_equal(grid.features[len(TILES):, y, tx], table.features[rows].sum(axis=0))
                np.testing.assert_array_equal(grid.present[len(TILES):, y, tx], table.present[rows].any(axis=0))
            else:
                assert not grid.present[len(TILES):, y, tx].any()
    positioned = sum(table.present[:, :2].all(axis=1))
    assert graph.edge_index.shape == (2, positioned * (positioned - 1))
    assert graph.edge_index.dtype == np.int64 and graph.edge_features.dtype == np.float32
    for (source, destination), edge in zip(graph.edge_index.T, graph.edge_features):
        assert source != destination
        dx, dy = table.features[destination, :2] - table.features[source, :2]
        np.testing.assert_array_equal(edge, [dx, dy, abs(dx) + abs(dy), max(abs(dx), abs(dy)),
                                             max(abs(dx), abs(dy)) == 1])
    if relative:
        own = grid.features[TILES.index(side.upper() + "_TOUCHDOWN")].nonzero()[1]
        other = "away" if side == "home" else "home"
        opp = grid.features[TILES.index(other.upper() + "_TOUCHDOWN")].nonzero()[1]
        assert max(opp) < min(own)


@pytest.mark.parametrize("kind", ("setup", "movement", "reroll", "defender_choice", "ground", "carried",
                                 "ko_reserve", "drive", "terminal", "unstarted"))
@pytest.mark.parametrize("side", ("home", "away"))
def test_real_observation_boundaries(kind, side):
    probe, binding = at_state(kind, 3, side)
    data = observe(probe.game, binding, side)
    table = entity_view(data, team=side)
    grid = grid_view(data, team=side)
    assert table.row_mask.sum() == len(data.players) + len(data.balls)
    assert grid.metadata["decision"]["phase"] == data.decision.phase
    if data.balls:
        i = table.entity_ids.index("ball:0")
        assert table.entities[i]["carrier_id"] == data.balls[0].carrier.value
        assert table.features[i, ENTITY_CHANNELS.index("is_carried")] == data.balls[0].is_carried


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("side", ("home", "away"))
@pytest.mark.parametrize("kind", ("setup", "movement", "defender_choice"))
def test_action_inverse_exact_and_in_original_legal_options(size, side, kind):
    probe, binding = at_state(kind, size, side)
    control = ActionControl(probe.game, binding)
    legal = control.legal_actions()
    data = observe(probe.game, binding, side)
    frame = coordinate_frame(data, side)
    before = pickle.dumps(probe.game)
    for action in legal.actions:
        wire = deepcopy(action.to_json())
        mapped = frame.action(action)
        restored = frame.inverse_action(mapped)
        assert restored == action and restored in legal.actions
        assert restored.to_json() == wire == action.to_json()
        assert probe.game.validate_action(control.decode(control.request(restored))).allowed
        assert mapped.actor_id == action.actor_id
        assert (mapped.player_id, mapped.target_id) == (action.player_id, action.target_id)
    assert pickle.dumps(probe.game) == before


@pytest.mark.parametrize("reflected", (False, True))
def test_positions_paths_target_ids_and_nonpositional_actions_are_copied(reflected):
    frame = CoordinateFrame(14, 9, reflected)
    for x, y in ((0, 0), (13, 8), (-3, -1), (17, 12)):
        p = PositionV1(x, y)
        assert frame.inverse_position(frame.position(p)) == p
        assert frame.position(p) == PositionV1(13 - x if reflected else x, y)
    actions = [
        ActionV1(1, "MOVE", "away", None, None, PositionV1(7, 4),
                 PathOptionsV1([PositionV1(5, 4), PositionV1(6, 4), PositionV1(7, 4)])),
        ActionV1(1, "BLOCK", "home", None, "away:0", None, EmptyOptionsV1()),
        ActionV1(1, "END_TURN", "away", None, None, None, EmptyOptionsV1()),
    ]
    for action in actions:
        assert frame.inverse_action(frame.action(action)) == action
    mapped = frame.action(actions[0])
    assert mapped.options.path == [frame.position(p) for p in actions[0].options.path]
    mapped.options.path.clear()
    assert len(actions[0].options.path) == 3


def test_reflection_padding_origin_and_double_reflection():
    data = snapshot()
    w, h = data["geometry"]["width"], data["geometry"]["height"]
    absolute = grid_view(data, pad_to=(h + 3, w + 5))
    relative = grid_view(data, team="away", pad_to=(h + 3, w + 5))
    # Tiles reflect only the true arena; padding remains at bottom/right.
    np.testing.assert_array_equal(relative.features[:len(TILES), :h, :w],
                                  absolute.features[:len(TILES), :h, :w][:, :, ::-1])
    np.testing.assert_array_equal(relative.playable[:h, :w][:, ::-1], absolute.playable[:h, :w])
    assert not relative.arena_mask[h:, :].any() and not relative.arena_mask[:, w:].any()
    assert not relative.present[:, h:, :].any() and not relative.present[:, :, w:].any()
    assert not relative.playable[0, 0]  # real crowd is distinct from padding
    assert relative.arena_mask[0, 0]
    # Geometry-driven normalization also supports an already mirrored arena.
    mirrored = deepcopy(data)
    for field in ("tiles", "playable"):
        mirrored["geometry"][field] = [row[::-1] for row in data["geometry"][field]]
    assert coordinate_frame(mirrored, "home").reflected
    assert not coordinate_frame(mirrored, "away").reflected
    frame = CoordinateFrame(w, h, True)
    for entity in mirrored["players"] + mirrored["balls"]:
        if entity["position"]["present"]:
            entity["position"]["value"] = frame.position(PositionV1(**entity["position"]["value"])).to_json()
    mirrored["decision"]["target_position"]["value"] = frame.position(
        PositionV1(**mirrored["decision"]["target_position"]["value"])).to_json()
    twice = grid_view(mirrored, team="home")
    np.testing.assert_array_equal(twice.features, grid_view(data).features)
    np.testing.assert_array_equal(twice.context, grid_view(data).context)


@pytest.mark.parametrize("location", ("reserves", "ko", "casualties", "dungeon", "unplaced"))
def test_off_pitch_exterior_missing_ball_and_collocated_entities(location):
    data = snapshot()
    data["players"][0].update(position=presence(), location=location)
    data["players"][1].update(position=presence(-2, 1), location="pitch")
    data["players"][2]["position"] = presence(1, 2)
    data["players"][3]["position"] = presence(1, 2)
    data["balls"].append(dict(position=presence(), carrier={"value": None, "present": False},
                              on_ground=False, is_carried=False))
    table, grid, graph = entity_view(data), grid_view(data), graph_view(data)
    assert table.entities[0]["location"] == location and table.row_mask[0]
    assert not table.present[0, :2].any()
    assert table.present[1, :2].all() and table.features[1, 0] == -2
    assert {table.entity_ids[0], table.entity_ids[1], "ball:1"} <= set(grid.off_grid_ids)
    assert grid.cell_entity_ids[2][1] == (table.entity_ids[2], table.entity_ids[3], "ball:0")
    assert grid.features[grid.metadata["channels"].index("is_player.sum"), 2, 1] == 2
    assert not any(i == 0 for i in graph.edge_index.flat)
    assert 1 in graph.edge_index  # exterior geometry is retained, never clamped
    zero_edges = graph.edge_features[graph.edge_features[:, 3] == 0]
    assert len(zero_edges) == 6 and not zero_edges[:, 4].any()


def test_external_seed_permutation_keeps_masks_ids_graph_and_targets_aligned():
    data = snapshot()
    original = entity_view(data, pad_to=20)
    shuffled = entity_view(data, pad_to=20, permutation_seed=91)
    graph = graph_view(data, pad_to=20, permutation_seed=91)
    np.testing.assert_array_equal(graph.nodes.features, shuffled.features)
    assert graph.nodes.entity_ids == shuffled.entity_ids != original.entity_ids
    assert not np.array_equal(shuffled.row_mask, original.row_mask)
    targets = {p["id"]: [987654, i] for i, p in enumerate(data["players"])}
    aligned = align_targets(shuffled, targets)
    assert 987654 not in shuffled.features and 987654 not in shuffled.context
    for i, entity_id in enumerate(shuffled.entity_ids):
        if entity_id is None:
            assert not shuffled.row_mask[i] and not shuffled.present[i].any()
            assert not shuffled.features[i].any() and not aligned.present[i].any()
        else:
            j = original.entity_ids.index(entity_id)
            np.testing.assert_array_equal(shuffled.features[i], original.features[j])
            np.testing.assert_array_equal(shuffled.present[i], original.present[j])
            assert shuffled.entities[i] == original.entities[j]
            if entity_id in targets:
                np.testing.assert_array_equal(aligned.values[i], targets[entity_id])
                assert aligned.present[i].all()
            else:
                assert not aligned.present[i].any()
    def edge_map(view):
        return {(view.nodes.entity_ids[s], view.nodes.entity_ids[t]): tuple(values)
                for (s, t), values in zip(view.edge_index.T, view.edge_features)}
    assert edge_map(graph) == edge_map(graph_view(data, pad_to=20))
    np.testing.assert_array_equal(shuffled.features, entity_view(data, pad_to=20, permutation_seed=91).features)


@pytest.mark.parametrize("construct", (entity_view, grid_view, graph_view))
def test_leakage_allowlist_and_source_purity(construct):
    data = snapshot()
    sentinel = "EVALUATION-future-RNG-987654321"
    channel = make_channel("primary", data, {"episode_id": sentinel})
    channels = {"primary": channel, "evaluation": object(), "privileged": object()}
    # Consumers explicitly select primary data; a whole channel bundle is rejected.
    with pytest.raises(ValueError):
        construct(channels)
    for path in ((), ("players", 0), ("decision",), ("geometry",)):
        dirty = deepcopy(data)
        node = dirty
        for key in path:
            node = node[key]
        node["evaluation"] = {"future": sentinel}
        with pytest.raises(ValueError):
            construct(dirty)
    before = deepcopy(data)
    result = construct(channel["data"])
    metadata = result.metadata
    assert sentinel not in json.dumps(metadata)
    metadata["logical_time"]["drive"]["present"] = True
    if construct is graph_view:
        result = result.nodes
    result.features[:] = 99
    result.present[:] = False
    result.context[:] = -9
    assert channel["data"] == data == before


def test_missing_is_distinct_from_zero_empty_graph_and_fixed_metadata_order():
    data = snapshot()
    table = entity_view(data)
    assert table.features[0, 0] == 0 and table.present[0, 0]
    drive = table.metadata["context_channels"].index("match.drive")
    round_index = table.metadata["context_channels"].index("match.round")
    assert table.context[drive] == table.context[round_index] == 0
    assert not table.context_present[drive] and table.context_present[round_index]
    reordered = deepcopy(data)
    reordered["teams"][0]["resources"] = dict(reversed(list(data["teams"][0]["resources"].items())))
    other = entity_view(reordered)
    assert table.metadata["context_channels"] == other.metadata["context_channels"]
    np.testing.assert_array_equal(table.context, other.context)
    data["players"].clear()
    data["balls"].clear()
    graph = graph_view(data)
    assert graph.nodes.features.shape == (0, len(ENTITY_CHANNELS))
    assert graph.edge_index.shape == (2, 0) and graph.edge_features.shape == (0, 5)
    assert not grid_view(data).features[len(TILES):].any()


@pytest.mark.parametrize("side", ("home", "away"))
@pytest.mark.parametrize("size", SIZES)
def test_synthetic_pixels_match_cells_and_targets(size, side):
    data = snapshot(size, side)
    w, h = data["geometry"]["width"], data["geometry"]["height"]
    frame = coordinate_frame(data, side)
    grid = grid_view(data, team=side, pad_to=(h + 1, w + 1))
    before = pickle.dumps(grid)
    rgb = render_grid(grid, cell_size=8)
    assert rgb.dtype == np.uint8 and rgb.shape == ((h + 1) * 8, (w + 1) * 8, 3)
    def cell(x, y):
        x = frame.position(PositionV1(x, y)).x
        return rgb[y * 8:(y + 1) * 8, x * 8:(x + 1) * 8]
    np.testing.assert_array_equal(cell(0, 0)[0, 0], CROWD_RGB)
    np.testing.assert_array_equal(cell(0, 0)[3, 3], PLAYER_RGB)
    np.testing.assert_array_equal(cell(1, 2)[0, 0], ENDZONE_RGB)
    np.testing.assert_array_equal(cell(1, 2)[3, 3], BALL_RGB)
    assert np.all(cell(w - 2, 1)[0] == TARGET_RGB)
    assert not rgb[h * 8:, :].any() and not rgb[:, w * 8:].any()
    assert pickle.dumps(grid) == before


def test_queries_render_and_permutations_do_not_change_rng_engine_or_actions():
    probe, binding = at_state("movement", 3, "away")
    control = ActionControl(probe.game, binding)
    action = control.legal_actions().actions[0]
    data = observe(probe.game, binding, "away")
    before = pickle.dumps((probe.game, data, action, np.random.get_state()))
    for _ in range(2):
        frame = coordinate_frame(data, "away")
        assert frame.inverse_action(frame.action(action)) == action
        entity_view(data, permutation_seed=42)
        graph_view(data, permutation_seed=42)
        render_grid(grid_view(data, team="away"))
    assert pickle.dumps((probe.game, data, action, np.random.get_state())) == before


def test_core_and_views_do_not_import_renderer_or_display_libraries():
    subprocess.run([sys.executable, "-c", "import sys; import botbowl; import botbowl.lab.views; "
                    "assert not {'botbowl.lab.rendering', 'matplotlib', 'PIL', 'pygame'} & set(sys.modules)"],
                   check=True)


@pytest.mark.parametrize("change", (
    {"schema_version": 2}, {"schema_version": True}, {"evaluation": 123},
))
def test_unknown_observation_versions_and_extras_fail(change):
    with pytest.raises(ValueError):
        entity_view({**snapshot(), **change})


@pytest.mark.parametrize("change", ("dimensions", "tile", "playable", "duplicate_id", "reference", "presence"))
def test_incoherent_geometry_identity_and_presence_fail(change):
    data = snapshot()
    if change == "dimensions":
        data["geometry"]["width"] += 1
    elif change == "tile":
        data["geometry"]["tiles"][0][0] = "UNKNOWN"
    elif change == "playable":
        data["geometry"]["playable"][0][0] = True
    elif change == "duplicate_id":
        data["players"][1]["id"] = data["players"][0]["id"]
    elif change == "reference":
        data["balls"][0]["carrier"] = {"value": "away:999", "present": True}
    else:
        data["players"][0]["position"]["present"] = False
    with pytest.raises(ValueError):
        grid_view(data)


@pytest.mark.parametrize("kwargs", ({"team": "actor"}, {"pad_to": 1}, {"pad_to": True},
                                    {"permutation_seed": -1}, {"permutation_seed": True}))
def test_invalid_entity_options_fail(kwargs):
    with pytest.raises(ValueError):
        entity_view(snapshot(), **kwargs)


def test_invalid_grid_frame_and_target_options_fail():
    data = snapshot()
    for padding in ((1, 1), (True, 100), (100,), [100, 100]):
        with pytest.raises(ValueError):
            grid_view(data, pad_to=padding)
    for version in (2, True):
        with pytest.raises(ValueError):
            CoordinateFrame(10, 10, schema_version=version)
    for targets in ({"alien:0": 3}, {"home:0": float("nan")}, {"home:0": [1], "home:1": [1, 2]}):
        with pytest.raises(ValueError):
            align_targets(entity_view(data), targets)
    with pytest.raises(ValueError):
        render_grid(grid_view(data), cell_size=3)
