"""Pure NumPy views of ObservationV1. See docs/lab/views.md for V1 losses.

Only primary data is accepted. Targets are aligned separately by entity ID;
neither evaluation channels nor a Game can enter a view constructor.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .actions import ActionV1, PositionV1
from .channels import PRIMARY_PROFILE, make_channel
from .observations import ObservationV1


TILES = (
    "HOME", "HOME_TOUCHDOWN", "HOME_WING_LEFT", "HOME_WING_RIGHT", "HOME_SCRIMMAGE",
    "AWAY", "AWAY_TOUCHDOWN", "AWAY_WING_LEFT", "AWAY_WING_RIGHT", "AWAY_SCRIMMAGE",
    "CROWD", "MIDFIELD",
)
_ATTRIBUTES = tuple(group + "." + attr for group in
                    ("attributes", "role_attributes", "extra_attributes")
                    for attr in ("ma", "st", "ag", "av"))
_FLAGS = (
    "up", "in_air", "used", "stunned", "bone_headed", "hypnotized", "really_stupid",
    "heated", "knocked_out", "ejected", "wild_animal", "taken_root", "blood_lust",
    "picked_up", "has_blocked", "failed_nega_trait_this_turn",
)
_PLAYER = _ATTRIBUTES + ("mng",) + tuple("status." + key for key in _FLAGS) + (
    "status.moves", "status.spp_earned")
ENTITY_CHANNELS = ("x", "y", "is_player", "is_ball") + _PLAYER + ("on_ground", "is_carried")
ENTITY_UNITS = ("arena_cell", "arena_cell", "boolean", "boolean") + (
    ("attribute_point",) * len(_ATTRIBUTES) + ("boolean",) * (1 + len(_FLAGS))
    + ("movement_step", "spp", "boolean", "boolean"))
EDGE_CHANNELS = ("dx", "dy", "manhattan", "chebyshev", "adjacent_8")
_RESOURCES = ("rerolls", "rerolls_start", "reroll_used", "apothecaries", "bribes", "babes",
              "wizard_available", "masterchef", "ass_coaches", "cheerleaders", "fame")


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(name + " must be a positive integer")


def _primary(observation):
    if type(observation) is ObservationV1:
        observation = observation.to_json()
    data = make_channel("primary", observation)["data"]
    geometry = data["geometry"]
    w, h = geometry["width"], geometry["height"]
    _positive(w, "width")
    _positive(h, "height")
    for field in ("tiles", "playable"):
        if len(geometry[field]) != h or any(len(row) != w for row in geometry[field]):
            raise ValueError("Geometry dimensions do not match " + field)
    for tiles, playable in zip(geometry["tiles"], geometry["playable"]):
        if any(tile not in TILES or mask != (tile != "CROWD")
               for tile, mask in zip(tiles, playable)):
            raise ValueError("Unknown tile or inconsistent playable mask")
    ids = [p["id"] for p in data["players"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate player IDs")
    for p in data["players"]:
        if p["slot"] < 0 or p["id"] != p["team"] + ":" + str(p["slot"]):
            raise ValueError("Invalid episode-local player ID")
    if [t["id"] for t in data["teams"]] != ["home", "away"]:
        raise ValueError("Expected home and away teams in schema order")
    references = [b["carrier"]["value"] for b in data["balls"]]
    references += [data["decision"][key]["value"]
                   for key in ("active_player", "subject", "target_player")]
    if any(ref is not None and ref not in ids for ref in references):
        raise ValueError("Unknown referenced player ID")
    return data


@dataclass(frozen=True)
class CoordinateFrame:
    """V1 arena reflection; the same map is its exact inverse, including exterior cells."""
    width: int
    height: int
    reflected: bool = False
    team: Optional[str] = None
    schema_version: int = 1

    def __post_init__(self):
        _positive(self.width, "width")
        _positive(self.height, "height")
        if (type(self.reflected) is not bool or self.team not in (None, "home", "away")
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError("Invalid coordinate frame/version")

    def position(self, position):
        """Map PositionV1 from absolute to this frame (or back); never clamp."""
        if type(position) is not PositionV1:
            raise ValueError("Expected PositionV1")
        position = PositionV1.from_json(position.to_json())
        return PositionV1(self.width - 1 - position.x if self.reflected else position.x,
                          position.y)

    def inverse_position(self, position):
        return self.position(position)

    def action(self, action):
        """Copy ActionV1 and transform its position and every declared path step."""
        if type(action) is not ActionV1:
            raise ValueError("Expected ActionV1")
        data = ActionV1.from_json(action.to_json()).to_json()
        if data["position"] is not None:
            data["position"] = self.position(PositionV1.from_json(data["position"])).to_json()
        if "path" in data["options"]:
            data["options"]["path"] = [self.position(PositionV1.from_json(p)).to_json()
                                       for p in data["options"]["path"]]
        return ActionV1.from_json(data)

    def inverse_action(self, action):
        return self.action(action)

    def to_json(self):
        return {"schema_version": 1, "width": self.width, "height": self.height,
                "reflected": self.reflected, "team": self.team,
                "convention": "arena-xy-v1", "origin": "upper_left",
                "relative_attack": "decreasing_x" if self.team else None}


def _frame(data, team):
    if team not in (None, "home", "away"):
        raise ValueError("team must be home, away or None (absolute)")
    g = data["geometry"]
    reflected = False
    if team is not None:
        other = "away" if team == "home" else "home"
        own = [x for row in g["tiles"] for x, tile in enumerate(row)
               if tile == team.upper() + "_TOUCHDOWN"]
        opp = [x for row in g["tiles"] for x, tile in enumerate(row)
               if tile == other.upper() + "_TOUCHDOWN"]
        if not own or not opp or not (max(own) < min(opp) or max(opp) < min(own)):
            raise ValueError("Team-relative frame requires horizontally separated endzones")
        reflected = max(own) < min(opp)
    return CoordinateFrame(g["width"], g["height"], reflected, team)


def coordinate_frame(observation, team=None):
    """Absolute by default; explicit team selection never depends on the pending actor."""
    return _frame(_primary(observation), team)


def _xy(presence, frame):
    p = presence["value"]
    return None if p is None else frame.position(PositionV1(p["x"], p["y"]))


def _metadata(data, frame, view_id, channels, units):
    return {
        "schema_version": 1, "view_id": view_id,
        "input_profile": PRIMARY_PROFILE.to_json(), "frame": frame.to_json(),
        "channels": channels, "units": units,
        "dtypes": {"features": "float32", "present": "bool"},
        # ObservationV1 has no decision tick or authoritative drive counter.
        "logical_time": {"half": data["match"]["half"], "round": data["match"]["round"],
                         "drive": deepcopy(data["match"]["drive"]),
                         "team_turns": tuple(t["turn"] for t in data["teams"])},
        "observer_team": data["observer_team"],
        "decision": {"phase": data["decision"]["phase"],
                     **{key: data["decision"][key]["value"] for key in (
                         "actor_team", "active_team", "active_player", "subject", "target_player")}},
    }


def _context(data, frame):
    names, values, units = [], [], []

    def add(name, value, unit):
        names.append(name)
        values.append(value)
        units.append(unit)

    for key in ("half", "round", "drive", "game_over"):
        value = data["match"][key]
        add("match." + key, value["value"] if key == "drive" else value,
            "boolean" if key == "game_over" else "count")
    add("decision.pending", data["decision"]["pending"], "boolean")
    position = _xy(data["decision"]["target_position"], frame)
    add("decision.target_x", None if position is None else position.x, "arena_cell")
    add("decision.target_y", None if position is None else position.y, "arena_cell")
    for i, team in enumerate(data["teams"]):
        for key in ("score", "turn"):
            add("teams[" + str(i) + "]." + key, team[key], "count")
        for key in _RESOURCES:
            value = team["resources"][key]
            add("teams[" + str(i) + "].resources." + key, value,
                "boolean" if type(value) is bool else "count")
    return (np.array([0 if v is None else v for v in values], dtype=np.float32),
            np.array([v is not None for v in values], dtype=np.bool_), tuple(names), tuple(units))


@dataclass(frozen=True)
class EntityView:
    metadata: dict
    features: np.ndarray
    present: np.ndarray
    row_mask: np.ndarray
    entity_ids: tuple
    entities: tuple
    context: np.ndarray
    context_present: np.ndarray


def entity_view(observation, *, team=None, pad_to=None, permutation_seed=None):
    """Players then snapshot-local balls, optionally padded and externally permuted."""
    data = _primary(observation)
    frame = _frame(data, team)
    count = len(data["players"]) + len(data["balls"])
    if pad_to is not None and (type(pad_to) is not int or pad_to < count):
        raise ValueError("pad_to must be an integer at least the entity count")
    rows = count if pad_to is None else pad_to
    features = np.zeros((rows, len(ENTITY_CHANNELS)), dtype=np.float32)
    present = np.zeros(features.shape, dtype=np.bool_)
    entities = []
    for index, entity in enumerate(data["players"] + data["balls"]):
        player = index < len(data["players"])
        p = _xy(entity["position"], frame)
        values = {"is_player": player, "is_ball": not player}
        if p is not None:
            values.update(x=p.x, y=p.y)
        if player:
            for path in _PLAYER:
                value = entity
                for key in path.split("."):
                    value = value[key]
                values[path] = value
            refs = {key: deepcopy(entity[key]) for key in (
                "id", "team", "location", "role", "skills", "used_skills", "injuries", "injuries_gained")}
            refs["kind"] = "player"
        else:
            values.update(on_ground=entity["on_ground"], is_carried=entity["is_carried"])
            refs = {"id": "ball:" + str(index - len(data["players"])), "kind": "ball",
                    "carrier_id": entity["carrier"]["value"]}
        entities.append(refs)
        for channel, value in values.items():
            column = ENTITY_CHANNELS.index(channel)
            features[index, column], present[index, column] = value, True
    entities.extend([None] * (rows - count))
    order = np.arange(rows)
    if permutation_seed is not None:
        if type(permutation_seed) is not int or permutation_seed < 0:
            raise ValueError("permutation_seed must be an external nonnegative integer")
        order = np.random.Generator(np.random.PCG64(permutation_seed)).permutation(rows)
    entities = tuple(entities[i] for i in order)
    metadata = _metadata(data, frame, "entities-v1", ENTITY_CHANNELS, ENTITY_UNITS)
    metadata["dtypes"]["row_mask"] = "bool"
    metadata["permutation"] = {"algorithm": "PCG64" if permutation_seed is not None else None,
                               "seed": permutation_seed}
    context, context_present, names, units = _context(data, frame)
    metadata.update(context_channels=names, context_units=units,
                    context_team_ids=tuple(t["id"] for t in data["teams"]),
                    context_dtype="float32", context_present_dtype="bool")
    return EntityView(metadata, features[order].copy(), present[order].copy(),
                      np.array([e is not None for e in entities], dtype=np.bool_),
                      tuple(None if e is None else e["id"] for e in entities), entities,
                      context, context_present)


@dataclass(frozen=True)
class AlignedTargets:
    """Separate target output; never part of a view's features or metadata."""
    values: np.ndarray
    present: np.ndarray


def align_targets(view, targets_by_id):
    """Align numeric scalars/equal-shaped arrays by ID, including permuted padding."""
    if type(view) is not EntityView or type(targets_by_id) is not dict:
        raise ValueError("Expected EntityView and an ID-keyed target dictionary")
    if set(targets_by_id) - (set(view.entity_ids) - {None}):
        raise ValueError("Unknown target entity ID")
    arrays = {key: np.asarray(value, dtype=np.float32) for key, value in targets_by_id.items()}
    shape = next(iter(arrays.values())).shape if arrays else ()
    if any(a.shape != shape or not np.isfinite(a).all() for a in arrays.values()):
        raise ValueError("Targets must be finite and have a common shape")
    values = np.zeros((len(view.entity_ids),) + shape, dtype=np.float32)
    present = np.zeros(values.shape, dtype=np.bool_)
    for row, entity_id in enumerate(view.entity_ids):
        if entity_id in arrays:
            values[row], present[row] = arrays[entity_id], True
    return AlignedTargets(values, present)


@dataclass(frozen=True)
class GraphView:
    metadata: dict
    nodes: EntityView
    edge_index: np.ndarray
    edge_features: np.ndarray


def graph_view(observation, *, team=None, pad_to=None, permutation_seed=None):
    """Complete directed geometry between positioned entities; no self edges or oracle."""
    nodes = entity_view(observation, team=team, pad_to=pad_to, permutation_seed=permutation_seed)
    positioned = np.flatnonzero(nodes.row_mask & nodes.present[:, 0] & nodes.present[:, 1])
    edges, values = [], []
    for source in positioned:
        for target in positioned:
            if source == target:
                continue
            dx, dy = nodes.features[target, :2] - nodes.features[source, :2]
            distance = max(abs(dx), abs(dy))
            edges.append((source, target))
            values.append((dx, dy, abs(dx) + abs(dy), distance, distance == 1))
    metadata = deepcopy(nodes.metadata)
    metadata.update(view_id="geometry-graph-v1", channels=EDGE_CHANNELS,
                    units=("arena_cell",) * 4 + ("boolean",),
                    dtypes={"edge_index": "int64", "edge_features": "float32"},
                    edge_rule="all ordered positioned pairs except self; no playable filtering")
    return GraphView(metadata, nodes, np.array(edges, dtype=np.int64).reshape(-1, 2).T.copy(),
                     np.array(values, dtype=np.float32).reshape(-1, len(EDGE_CHANNELS)))


@dataclass(frozen=True)
class GridView:
    metadata: dict
    features: np.ndarray
    present: np.ndarray
    arena_mask: np.ndarray
    playable: np.ndarray
    cell_entity_ids: tuple
    off_grid_ids: tuple
    context: np.ndarray
    context_present: np.ndarray


def grid_view(observation, *, team=None, pad_to: Optional[Tuple[int, int]] = None):
    """CHW raw tile indicators and entity-channel sums; pad_to is (height, width)."""
    data = _primary(observation)
    frame = _frame(data, team)
    nodes = entity_view(data, team=team)
    h, w = frame.height, frame.width
    if pad_to is not None:
        if (type(pad_to) is not tuple or len(pad_to) != 2
                or any(type(n) is not int for n in pad_to) or pad_to[0] < h or pad_to[1] < w):
            raise ValueError("pad_to must be (height, width) covering the arena")
        h, w = pad_to
    channels = tuple("tile." + tile for tile in TILES) + tuple(c + ".sum" for c in ENTITY_CHANNELS)
    features = np.zeros((len(channels), h, w), dtype=np.float32)
    present = np.zeros(features.shape, dtype=np.bool_)
    arena = np.zeros((h, w), dtype=np.bool_)
    playable = np.zeros_like(arena)
    arena[:frame.height, :frame.width] = True
    cells = [[[] for _ in range(w)] for _ in range(h)]
    for y, row in enumerate(data["geometry"]["tiles"]):
        for x, tile in enumerate(row):
            tx = frame.position(PositionV1(x, y)).x
            features[TILES.index(tile), y, tx] = 1
            present[:len(TILES), y, tx] = True
            playable[y, tx] = data["geometry"]["playable"][y][x]
    off_grid = []
    for index, entity_id in enumerate(nodes.entity_ids):
        x, y = nodes.features[index, :2]
        if not nodes.present[index, :2].all() or not (0 <= x < frame.width and 0 <= y < frame.height):
            off_grid.append(entity_id)
            continue
        x, y = int(x), int(y)
        cells[y][x].append(entity_id)
        features[len(TILES):, y, x] += nodes.features[index]
        present[len(TILES):, y, x] |= nodes.present[index]
    metadata = deepcopy(nodes.metadata)
    metadata.update(view_id="raw-grid-v1", channels=channels,
                    units=("boolean",) * len(TILES) + tuple(
                        "count" if unit == "boolean" else unit for unit in ENTITY_UNITS),
                    dtypes={"features": "float32", "present": "bool", "arena_mask": "bool",
                            "playable": "bool"},
                    aggregation="sum of present entity values, including x/y; empty has no contributors")
    return GridView(metadata, features, present, arena, playable,
                    tuple(tuple(tuple(ids) for ids in row) for row in cells), tuple(off_grid),
                    nodes.context.copy(), nodes.context_present.copy())
