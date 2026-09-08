"""Field boundary, stable resource identity and capability evidence regressions."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from enum import Enum
import importlib
from importlib import machinery, metadata
import json
from pathlib import Path
import pickle
import random

import numpy as np
import pytest

import botbowl as bb
from botbowl.core import load, procedure
from botbowl.lab.rules import (
    CAPABILITIES_VERSION, IncoherentResourceError, RulesDescriptor,
    UnsupportedBackendError, UnsupportedSizeError, capability_catalogue, describe_rules,
)


@pytest.fixture(autouse=True)
def isolated_legacy_loader_defaults(monkeypatch):
    # Core RuleSet owns shared mutable defaults, and other tests load different
    # editions into them. Give this test fresh containers and restore the exact
    # defaults afterward. Production descriptor/loader code never changes them.
    monkeypatch.setattr(bb.RuleSet.__init__, "__defaults__", ([], [], [], {}, {}, {}, 0, 0, 0))


def inputs(size=11, config_name=None):
    config = bb.load_config(config_name or "gym-" + str(size))
    ruleset = bb.load_rule_set(config.ruleset)
    arena = bb.load_arena(config.arena)
    home = bb.load_team_by_filename("human", ruleset, board_size=size)
    away = bb.load_team_by_filename("orc" if size == 11 else "human", ruleset, board_size=size)
    return config, ruleset, arena, home, away


@pytest.mark.parametrize("size", (1, 3, 5, 7, 11))
def test_duplicate_loads_and_catalogue(size):
    first_inputs = inputs(size)
    first = describe_rules(*first_inputs)
    second_inputs = inputs(size)
    assert first_inputs[3].team_id != second_inputs[3].team_id
    assert first_inputs[3].players[0].player_id != second_inputs[3].players[0].player_id
    assert first == describe_rules(*second_inputs) == describe_rules(*first_inputs)
    assert first.ruleset_id == "BB2016"
    assert first.capabilities_version == CAPABILITIES_VERSION
    assert first.config_id == "BB2016/" + first.config_digest
    assert len(first.config_digest.removeprefix("sha256:")) == 64
    assert set(first.to_json()) == {
        "ruleset_id", "ruleset_version", "engine_version", "config_id",
        "config_digest", "backend_id", "capabilities_version",
    }
    assert RulesDescriptor(**json.loads(json.dumps(first.to_json()))) == first
    with pytest.raises(FrozenInstanceError):
        first.config_id = "changed"
    catalogue = capability_catalogue(size)
    config, _, arena, _, _ = first_inputs
    variant = catalogue["variant"]
    for field in ("roster_size", "pitch_min", "scrimmage_min", "wing_max", "rounds",
                  "kick_off_table", "kick_scatter_dice", "throw_in_dice"):
        assert variant[field] == getattr(config, field)
    assert (variant["width"], variant["height"]) == (arena.width, arena.height)
    claims = {item["id"]: item for item in catalogue["capabilities"]}
    assert claims["bb2016"]["status"] == "partial"
    assert claims["pregame-inducements"]["status"] == "unsupported"
    assert claims["postgame-progression"]["status"] == "unsupported"
    assert all(item["evidence"] and item["limits"] for item in claims.values())
    catalogue["variant"]["width"] = -1
    assert capability_catalogue(size)["variant"]["width"] == arena.width


@pytest.mark.parametrize("name,size", [("bot-bowl", 11)] + [("web-" + str(n), n) for n in (1, 3, 5, 7, 11)])
def test_other_shipped_configurations(name, size):
    assert describe_rules(*inputs(size, name)).ruleset_id == "BB2016"


def test_key_order_in_loaded_json_and_enum_maps(tmp_path, monkeypatch):
    args = inputs()
    original = describe_rules(*args)
    path = Path(load.get_data_path("config/gym-11.json"))
    data = json.loads(path.read_text())
    data["time_limits"] = dict(reversed(list(data["time_limits"].items())))
    reordered = tmp_path / "config.json"
    reordered.write_text(json.dumps(dict(reversed(list(data.items())))))
    get_data_path = load.get_data_path
    monkeypatch.setattr(load, "get_data_path", lambda name: str(reordered) if name == "config/reordered.json"
                        else get_data_path(name))
    config = bb.load_config("reordered")
    monkeypatch.setattr(bb.Rules, "pass_modifiers", dict(reversed(list(bb.Rules.pass_modifiers.items()))))
    args[1].spp_actions = dict(reversed(list(args[1].spp_actions.items())))
    assert describe_rules(config, *args[1:]) == original


def test_named_roster_order_is_irrelevant_and_enums_are_not_ordinals(monkeypatch):
    args = inputs()
    before = describe_rules(*args)
    args[1].races.reverse()
    for race in args[1].races:
        race.roles.reverse()
    assert describe_rules(*args) == before
    # An enum's ordinal is not its semantic identity, including as a map key.
    modifiers = {key.value: value for key, value in bb.Rules.pass_modifiers.items()}
    monkeypatch.setattr(bb.Rules, "pass_modifiers", modifiers)
    assert describe_rules(*args).ruleset_version != before.ruleset_version
    from botbowl.lab.rules import _canonical
    assert _canonical(bb.Skill.BLOCK) == {"enum": "botbowl.core.table.Skill", "name": "BLOCK"}
    unrelated = Enum("DifferentSkill", {"BLOCK": bb.Skill.BLOCK.value})
    assert _canonical(unrelated.BLOCK) != _canonical(bb.Skill.BLOCK)


def test_identity_does_not_depend_on_python_hash_seed(tmp_path):
    import os
    import subprocess
    import sys

    script = """
import json
import botbowl as bb
from botbowl.lab.rules import describe_rules
c = bb.load_config('gym-1')
r = bb.load_rule_set(c.ruleset)
a = bb.load_arena(c.arena)
h = bb.load_team_by_filename('human', r, board_size=1)
v = bb.load_team_by_filename('human', r, board_size=1)
print(json.dumps(describe_rules(c, r, a, h, v).to_json(), sort_keys=True))
"""
    # Keep the invoking test's import root for source runs; installed CI runs
    # start from the separate suite directory and resolve the installed wheel.
    outputs = [subprocess.check_output([sys.executable, "-c", script],
                                      env={**os.environ, "PYTHONHASHSEED": seed}, text=True)
               for seed in ("1", "271")]
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("field,value", [
    ("rounds", 4), ("roster_size", 17), ("pitch_min", 2), ("scrimmage_min", 2), ("wing_max", 1),
    ("kick_off_table", False), ("fast_mode", False), ("debug_mode", True), ("competition_mode", True),
    ("kick_scatter_dice", "d3"), ("throw_in_dice", "d3"), ("pathfinding_enabled", True),
    ("pathfinding_directly_to_adjacent", True),
])
def test_behavior_configuration_changes_identity(field, value):
    args = inputs()
    original = describe_rules(*args)
    assert getattr(args[0], field) != value
    setattr(args[0], field, value)
    changed = describe_rules(*args)
    assert changed.config_digest != original.config_digest
    assert changed.config_id != original.config_id
    assert changed.ruleset_id == original.ruleset_id
    assert changed.ruleset_version == original.ruleset_version


@pytest.mark.parametrize("field", ("turn", "secondary", "init", "end"))
def test_time_limit_durations_are_behavior_not_timestamps(field):
    args = inputs()
    before = describe_rules(*args)
    setattr(args[0].time_limits, field, 999)
    assert describe_rules(*args).config_digest != before.config_digest


@pytest.mark.parametrize("resource", ("role", "star", "inducement", "spp", "table"))
def test_rule_resource_contents_change_version_and_digest(resource, monkeypatch):
    args = inputs()
    before = describe_rules(*args)
    if resource == "role":
        args[3].players[0].role.ma += 1  # same loaded role shared with ruleset
    elif resource == "star":
        args[1].star_players[0].ma += 1
    elif resource == "inducement":
        args[1].inducements[0].cost += 1
    elif resource == "spp":
        args[1].spp_actions["Completion"] = 123
    else:
        monkeypatch.setattr(bb.Rules, "pass_modifiers", {**bb.Rules.pass_modifiers, bb.PassDistance.QUICK_PASS: 2})
    after = describe_rules(*args)
    assert before.config_digest != after.config_digest
    assert before.ruleset_version != after.ruleset_version


def test_team_file_contents_not_filename(tmp_path):
    args = inputs()
    original = describe_rules(*args)
    data = json.loads(Path(load.get_data_path("teams/11/human.json")).read_text())
    data["players"][0]["extra_ma"] += 1
    path = tmp_path / "human.json"
    path.write_text(json.dumps(data))
    home = bb.load_team(str(path), args[1])
    assert describe_rules(*args[:3], home, args[4]).config_digest != original.config_digest


@pytest.mark.parametrize("resource", ("tile", "geometry", "formation", "formation_name", "formation_order",
                                      "team_order", "player_order", "extra_skill", "injury", "rerolls"))
def test_geometry_rosters_formations_and_sequence_order(resource):
    args = inputs()
    before = describe_rules(*args)
    config, _, arena, home, away = args
    if resource == "tile":
        arena.board[2][2] = bb.Tile.HOME
    elif resource == "geometry":
        arena.board = np.insert(arena.board, 2, arena.board[2], axis=0)
        arena.height += 1
        config.offensive_formations = [bb.Formation(f.name, np.insert(f.formation, 2, f.formation[2], axis=0))
                                       for f in config.offensive_formations]
        config.defensive_formations = [bb.Formation(f.name, np.insert(f.formation, 2, f.formation[2], axis=0))
                                       for f in config.defensive_formations]
    elif resource == "formation":
        config.offensive_formations[0].formation[0][0] = "S"
    elif resource == "formation_name":
        f = config.offensive_formations[0]
        config.offensive_formations[0] = bb.Formation("Line", f.formation)
    elif resource == "formation_order":
        config.offensive_formations.reverse()
    elif resource == "team_order":
        args = config, args[1], arena, away, home
    elif resource == "player_order":
        home.players.reverse()
    elif resource == "extra_skill":
        home.players[0].extra_skills.append(bb.Skill.DODGE)
    elif resource == "injury":
        home.players[0].injuries.append(bb.CasualtyEffect.MA)
    else:
        home.rerolls += 1
    assert describe_rules(*args).config_digest != before.config_digest


def test_metadata_paths_ids_clocks_credentials_and_state_excluded():
    args = inputs()
    before = describe_rules(*args)
    config, ruleset, arena, home, away = args
    for obj in (config, ruleset, arena, home, away, home.players[0]):
        obj.uuid = "267697c3-b593-48d8-983c-2fe7b8671aef"
        obj.path = "/private/absolute/path"
        obj.timestamp = 1234567890
        obj.credentials = {"token": "do-not-record"}
    config.name = "/another/path"
    config.arena = "/renamed/resource/location"
    config.kick_scatter_distance = "unused-legacy-field"
    home.name = "renamed display label"
    home.team_id = "changed-uuid"
    home.players[0].name = "renamed player"
    home.players[0].player_id = "another-uuid"
    home.players[0].state.moves = 3
    home.players[0].position = bb.Square(3, 3)
    home.state.rerolls = 0
    assert describe_rules(*args) == before
    assert "do-not-record" not in json.dumps(before.to_json())


def test_custom_configuration_retains_ruleset_reference():
    args = inputs()
    args[0].name = "my experiment"
    args[0].rounds = 3
    first = describe_rules(*args)
    assert first == describe_rules(*deepcopy(args))
    assert first.ruleset_id == "BB2016"
    assert first.config_id.startswith("BB2016/sha256:")
    args[0].ruleset = args[1].name = "Custom-Research-Rules"
    custom = describe_rules(*args)
    assert custom.ruleset_id == "Custom-Research-Rules"
    assert custom.config_id.startswith("Custom-Research-Rules/sha256:")
    assert custom.config_digest != first.config_digest


def test_jersey_numbers_change_pitch_invasion_roll_assignment_and_identity():
    def run(swapped):
        config, ruleset, arena, home, away = inputs(3)
        if swapped:
            home.players[0].nr, home.players[1].nr = home.players[1].nr, home.players[0].nr
        descriptor = describe_rules(config, ruleset, arena, home, away)
        game = bb.Game("pitch-invasion", home, away, bb.Agent("h", human=True),
                       bb.Agent("a", human=True), config, arena=arena, ruleset=ruleset, seed=17)
        players = game.state.home_team.players[:2]
        for y, player in enumerate(players, start=2):
            game.put(player, game.get_square(2, y))
        for roll in (6, 6, 1, 6):
            game.dice.fix(bb.D6, roll)
        procedure.KickoffTable(game, bb.Ball(game.get_square(4, 2))).step(None)
        # Resolve just the scheduled invasion rolls in the engine's stack order.
        for proc in reversed(list(game.state.stack.items)):
            if isinstance(proc, procedure.PitchInvasionRoll):
                proc.step(None)
        assert game.dice.pending(bb.D6) == ()
        return descriptor, [player.state.stunned for player in players]

    first, first_stunned = run(False)
    swapped, swapped_stunned = run(True)
    assert first_stunned == [False, True]
    assert swapped_stunned == [True, False]
    assert first.config_digest != swapped.config_digest


@pytest.mark.parametrize("size", (0, 2, 9, 12, True, "11", None))
def test_unsupported_sizes_are_typed(size):
    args = inputs()
    args[0].pitch_max = size
    with pytest.raises(UnsupportedSizeError):
        describe_rules(*args)
    with pytest.raises(UnsupportedSizeError):
        capability_catalogue(size)


@pytest.mark.parametrize("resource", ("ruleset", "duplicate", "arena", "tile", "formation", "selector",
                                      "race", "role", "roster", "ownership", "limits", "missing", "nan",
                                      "duration", "flag", "dice", "team_id", "player_id"))
def test_incoherent_resources_are_typed(resource):
    args = inputs()
    config, ruleset, arena, home, _ = args
    if resource == "ruleset":
        ruleset.name = "different"
    elif resource == "duplicate":
        duplicate = deepcopy(ruleset.races[0])
        duplicate.roles[0].ma += 1
        ruleset.races.append(duplicate)
    elif resource == "arena":
        arena.width += 2
    elif resource == "tile":
        arena.board[0][0] = bb.Tile.HOME
    elif resource == "formation":
        config.offensive_formations = [bb.load_formation("off_wedge", size=1)]
    elif resource == "selector":
        config.offensive_formations[0].formation[0][0] = "?"
    elif resource == "race":
        home.race = "Unknown"
    elif resource == "role":
        home.players[0].role = deepcopy(home.players[0].role)
        home.players[0].role.ma += 1
    elif resource == "roster":
        home.players = []
    elif resource == "ownership":
        home.players[0].team = args[4]
    elif resource == "limits":
        config.roster_size = 1
    elif resource == "missing":
        del config.kick_scatter_dice
    elif resource == "duration":
        config.time_limits.turn = -1
    elif resource == "flag":
        config.pathfinding_enabled = "yes"
    elif resource == "dice":
        config.throw_in_dice = "d2"
    elif resource == "team_id":
        home.team_id = args[4].team_id
    elif resource == "player_id":
        home.players[0].player_id = args[4].players[0].player_id
    else:
        config.time_limits.turn = float("nan")
    with pytest.raises(IncoherentResourceError):
        describe_rules(*args)


def test_actual_backend_and_engine_metadata(monkeypatch):
    args = inputs()
    expected = "native" if procedure.Pathfinder.__module__.endswith("cython_pathfinding") else "python"
    module = importlib.import_module(procedure.Pathfinder.__module__)
    if expected == "native":
        assert any(module.__file__.endswith(suffix) for suffix in machinery.EXTENSION_SUFFIXES)
    monkeypatch.setenv("BOTBOWL_BUILD_NATIVE", "0" if expected == "native" else "1")
    args[0].pathfinding_enabled = False
    descriptor = describe_rules(*args)
    assert descriptor.backend_id == expected
    try:
        assert descriptor.engine_version == metadata.version("botbowl")
    except metadata.PackageNotFoundError:
        assert descriptor.engine_version == "unknown"
    from botbowl.core.pathfinding.python_pathfinding import Pathfinder
    monkeypatch.setattr(procedure, "Pathfinder", Pathfinder)
    assert describe_rules(*args).backend_id == "python"
    monkeypatch.setattr(procedure, "Pathfinder", object)
    with pytest.raises(UnsupportedBackendError):
        describe_rules(*args)


def test_construction_does_not_mutate_game_rng_or_resources(monkeypatch):
    args = inputs(3)
    config, ruleset, arena, home, away = args
    game = bb.Game("id", home, away, bb.Agent("h", human=True), bb.Agent("a", human=True),
                   config, arena=arena, ruleset=ruleset, seed=17)
    game.init()
    game.dice.fix(bb.D6, 4)
    before = pickle.dumps((game, args, bb.Rules.pass_modifiers, random.getstate(), np.random.get_state()))

    def forbidden(*args, **kwargs):
        raise AssertionError("Descriptor called a loader, serializer or RNG")

    with monkeypatch.context() as guard:
        for name in ("load_rule_set", "load_arena", "load_team", "load_config"):
            guard.setattr(load, name, forbidden)
        guard.setattr(bb.Game, "to_json", forbidden)
        guard.setattr(bb.TwoPlayerArena, "to_json", forbidden)
        first = describe_rules(*args)
        assert describe_rules(*args) == first
    assert pickle.dumps((game, args, bb.Rules.pass_modifiers, random.getstate(), np.random.get_state())) == before
    assert arena.json is None
