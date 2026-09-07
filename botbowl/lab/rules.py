"""Identity of loaded rule/configuration inputs, independent of live game state.

Use the core loaders first, then pass the exact objects intended for Game.
See docs/lab/rules.md for the inclusion boundary and evidence limits.
"""
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import importlib
from importlib import machinery, metadata
import json
import math
from numbers import Integral, Real
import re

from botbowl.core import procedure
from botbowl.core.model import Configuration, RuleSet, Team, TwoPlayerArena
from botbowl.core.table import Rules, Tile


CAPABILITIES_VERSION = "1.0.0"
RULESET_IMPLEMENTATION_VERSION = "1.0.0"
IDENTITY_VERSION = 1
SUPPORTED_SIZES = (1, 3, 5, 7, 11)


class RulesDescriptorError(ValueError):
    """The supplied inputs cannot be described coherently."""


class UnsupportedSizeError(RulesDescriptorError):
    """The pitch player limit is outside the supported size inventory."""


class IncoherentResourceError(RulesDescriptorError):
    """Loaded resources disagree, are malformed, or contain ambiguous records."""


class UnsupportedBackendError(RulesDescriptorError):
    """The pathfinder used by procedures is not a recognized implementation."""


@dataclass(frozen=True)
class RulesDescriptor:
    ruleset_id: str
    ruleset_version: str
    engine_version: str
    config_id: str
    config_digest: str
    backend_id: str
    capabilities_version: str

    def to_json(self):
        return asdict(self)


# Explicit lists are intentional: arbitrary object dictionaries may contain
# credentials, paths, caches or episode state. New behavioral fields need review.
_CONFIG_FIELDS = (
    "roster_size", "pitch_max", "pitch_min", "scrimmage_min", "wing_max",
    "rounds", "kick_off_table", "fast_mode", "debug_mode", "competition_mode",
    "kick_scatter_dice", "throw_in_dice", "pathfinding_enabled",
    "pathfinding_directly_to_adjacent",
)
_ROLE_FIELDS = (
    "name", "races", "ma", "st", "ag", "av", "skills", "cost", "feeder",
    "n_skill_sets", "d_skill_sets", "star_player",
)
_RULE_TABLES = (
    "pass_matrix", "pass_modifiers", "casualty_effect", "agility_table",
    "miss_next_game", "immovable_action_types", "pass_player_actions",
)
_ARENA_TILE_GROUPS = (
    "home_tiles", "away_tiles", "scrimmage_tiles", "wing_right_tiles",
    "wing_left_tiles", "home_td_tiles", "away_td_tiles",
)


def _fields(obj, names):
    return {name: getattr(obj, name) for name in names}


def _canonical(value):
    """Enums use qualified names; mappings sort by key, sequences retain order.

    Non-string map keys use tagged pairs so e.g. an enum cannot collide with a
    literal string. Sets and unsupported objects fail rather than using repr().
    """
    if isinstance(value, Enum):
        kind = type(value)
        return {"enum": kind.__module__ + "." + kind.__qualname__, "name": value.name}
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        if not math.isfinite(value):
            raise IncoherentResourceError("Non-finite numeric input")
        return float(value)
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: _canonical(item) for key, item in sorted(value.items())}
        pairs = [[_canonical(key), _canonical(item)] for key, item in value.items()]
        return {"mapping": sorted(pairs, key=lambda pair: _json(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    raise IncoherentResourceError("Unsupported value in rule/configuration inputs")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(_canonical(value)).encode("utf-8")).hexdigest()


def _named(records, project):
    """Collapse identical legacy-loader duplicates; reject conflicting names."""
    result = {}
    for record in records:
        value = project(record)
        name = record.name
        if not isinstance(name, str) or not name:
            raise IncoherentResourceError("Missing resource record name")
        if name in result and _canonical(result[name]) != _canonical(value):
            raise IncoherentResourceError("Conflicting duplicate resource records")
        result[name] = value
    return result


def _rules(ruleset):
    def race_record(race):
        return {**_fields(race, ("reroll_cost", "apothecary", "stakes")),
                "roles": _named(race.roles, lambda role: _fields(role, _ROLE_FIELDS))}

    result = _fields(ruleset, ("spp_actions", "spp_levels", "improvements",
                               "se_start", "se_interval", "se_pace"))
    result.update(
        races=_named(ruleset.races, race_record),
        star_players=_named(ruleset.star_players, lambda role: _fields(role, _ROLE_FIELDS)),
        inducements=_named(ruleset.inducements,
                          lambda item: _fields(item, ("cost", "max_num", "reduced"))),
        tables=_fields(Rules, _RULE_TABLES),
    )
    if not result["races"]:
        raise IncoherentResourceError("Ruleset has no roster definitions")
    return result


def _arena(arena):
    board = [list(row) for row in arena.board]
    if (arena.width < 6 or arena.width % 2 or arena.height < 3 or
            len(board) != arena.height or any(len(row) != arena.width for row in board)):
        raise IncoherentResourceError("Arena dimensions disagree with its board")
    for y, row in enumerate(board):
        for x, tile in enumerate(row):
            if not isinstance(tile, Tile):
                raise IncoherentResourceError("Arena contains a non-tile value")
            boundary = y in (0, arena.height - 1) or x in (0, arena.width - 1)
            if boundary != (tile == Tile.CROWD):
                raise IncoherentResourceError("Arena must have a crowd border and playable interior")
    tiles = {tile for row in board for tile in row}
    if not {Tile.HOME_TOUCHDOWN, Tile.AWAY_TOUCHDOWN,
            Tile.HOME_SCRIMMAGE, Tile.AWAY_SCRIMMAGE}.issubset(tiles):
        raise IncoherentResourceError("Arena lacks endzones or scrimmage tiles")
    return {"width": arena.width, "height": arena.height, "board": board,
            "tile_groups": _fields(arena, _ARENA_TILE_GROUPS)}


def _formations(formations, arena):
    result = []
    for formation in formations:
        board = [list(row) for row in formation.formation]
        if (len(board) != arena.height - 2 or
                any(len(row) != (arena.width - 2) // 2 for row in board)):
            raise IncoherentResourceError("Formation dimensions disagree with the arena")
        if any(cell not in "-Sspbcmavd0x" for row in board for cell in row):
            raise IncoherentResourceError("Formation contains an unknown selector")
        if formation.name not in ("Wedge", "Line", "Spread", "Zone"):
            raise IncoherentResourceError("Formation has no supported setup action name")
        result.append({"name": formation.name, "board": board})
    return result


def _team(team, rules, roster_size):
    if team.race not in rules["races"]:
        raise IncoherentResourceError("Team race is absent from the ruleset")
    if not 1 <= len(team.players) <= roster_size:
        raise IncoherentResourceError("Team roster is empty or exceeds the configured limit")
    players = []
    for player in team.players:
        role = _fields(player.role, _ROLE_FIELDS)
        expected = rules["races"][team.race]["roles"].get(player.role.name)
        if _canonical(role) != _canonical(expected):
            raise IncoherentResourceError("Player role disagrees with the ruleset")
        if player.team is not team:
            raise IncoherentResourceError("Player belongs to a different team")
        players.append({**_fields(player, ("extra_ma", "extra_st", "extra_ag", "extra_av",
                                          "extra_skills", "injuries", "mng", "spp")),
                        "role": role})
    return {**_fields(team, ("race", "treasury", "apothecaries", "rerolls", "ass_coaches",
                            "cheerleaders", "fan_factor")), "players": players}


def _backend_id():
    # Procedures bind this class on import. Inspect that binding, not an env var,
    # config flag, compiler presence or merely importable optional module.
    pathfinder = procedure.Pathfinder
    name = pathfinder.__module__
    expected = {
        "botbowl.core.pathfinding.python_pathfinding": "python",
        "botbowl.core.pathfinding.cython_pathfinding": "native",
    }
    if name not in expected:
        raise UnsupportedBackendError("Unrecognized procedure pathfinder")
    module = importlib.import_module(name)
    if pathfinder is not module.Pathfinder:
        raise UnsupportedBackendError("Procedure pathfinder does not match its module")
    if expected[name] == "native" and not any(
            (module.__file__ or "").endswith(suffix) for suffix in machinery.EXTENSION_SUFFIXES):
        raise UnsupportedBackendError("Native pathfinder is not a loaded extension")
    return expected[name]


def describe_rules(config: Configuration, ruleset: RuleSet, arena: TwoPlayerArena,
                   home_team: Team, away_team: Team) -> RulesDescriptor:
    """Describe effective initial inputs without loading or modifying any object.

    Pass resources loaded by core.load (including deliberate caller overrides),
    before starting the episode. This is not an identity of a mid-game state or
    of arbitrary engine/bot monkeypatches. No Game or RNG is required or accessed.
    """
    try:
        if type(config.pitch_max) is not int or config.pitch_max not in SUPPORTED_SIZES:
            raise UnsupportedSizeError("Supported pitch sizes are 1, 3, 5, 7 and 11")
        reference = config.ruleset
        if (not isinstance(reference, str) or
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", reference) or
                reference != ruleset.name):
            raise IncoherentResourceError("Configuration and loaded ruleset reference disagree")
        for field in ("roster_size", "pitch_min", "scrimmage_min", "wing_max", "rounds"):
            value = getattr(config, field)
            if type(value) is not int or value < 0:
                raise IncoherentResourceError("Invalid numeric configuration limit")
        if (config.rounds < 1 or config.roster_size < config.pitch_max or
                not 1 <= config.pitch_min <= config.pitch_max or
                config.scrimmage_min > config.pitch_max or config.wing_max > config.pitch_max):
            raise IncoherentResourceError("Configuration limits disagree")
        effective = _fields(config, _CONFIG_FIELDS)
        effective["time_limits"] = _fields(config.time_limits, ("turn", "secondary", "init", "end"))
        for name in ("kick_off_table", "fast_mode", "debug_mode", "competition_mode",
                     "pathfinding_enabled", "pathfinding_directly_to_adjacent"):
            if type(effective[name]) is not bool:
                raise IncoherentResourceError("Configuration flags must be booleans")
        for duration in effective["time_limits"].values():
            if duration is not None and (isinstance(duration, bool) or not isinstance(duration, Real)
                                         or not math.isfinite(duration) or duration < 0):
                raise IncoherentResourceError("Invalid time-limit duration")
        for name in ("kick_scatter_dice", "throw_in_dice"):
            value = effective[name]
            if (name == "kick_scatter_dice" and value == "d2"):
                continue  # Scatter handles this small-board case explicitly.
            if not isinstance(value, str) or not re.fullmatch(r"(?:[1-9][0-9]*)?d[368]", value):
                raise IncoherentResourceError("Unsupported scatter/throw-in dice expression")
        rules = _rules(ruleset)
        payload = {
            "identity_version": IDENTITY_VERSION, "ruleset_id": reference,
            "config": effective, "rules": rules, "arena": _arena(arena),
            "offensive_formations": _formations(config.offensive_formations, arena),
            "defensive_formations": _formations(config.defensive_formations, arena),
            "teams": [_team(home_team, rules, config.roster_size),
                      _team(away_team, rules, config.roster_size)],
        }
        digest = _digest(payload)
        try:
            engine_version = metadata.version("botbowl")
        except metadata.PackageNotFoundError:
            engine_version = "unknown"
        return RulesDescriptor(
            ruleset_id=reference,
            ruleset_version=RULESET_IMPLEMENTATION_VERSION + "+sha256." + _digest(rules),
            engine_version=engine_version, config_id=reference + "/sha256:" + digest,
            config_digest="sha256:" + digest, backend_id=_backend_id(),
            capabilities_version=CAPABILITIES_VERSION,
        )
    except RulesDescriptorError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, IndexError) as error:
        # Do not put arbitrary resource contents (possibly sensitive) in errors.
        raise IncoherentResourceError("Malformed loaded rule/configuration resource") from error


def capability_catalogue(size=11):
    """Return fresh data about shipped BB2016 gym-size inputs, not custom claims."""
    if type(size) is not int or size not in SUPPORTED_SIZES:
        raise UnsupportedSizeError("Supported pitch sizes are 1, 3, 5, 7 and 11")
    # Observed gym-N defaults: width/height include the crowd border.
    variants = {
        1: (6, 5, 4, 1, 1, 1, "d2", "d3"),
        3: (14, 7, 4, 1, 1, 1, "d3", "d6"),
        5: (18, 11, 7, 2, 2, 1, "d3", "d6"),
        7: (22, 11, 9, 2, 3, 2, "d6", "2d6"),
        11: (28, 17, 16, 3, 3, 2, "d6", "2d6"),
    }
    keys = ("width", "height", "roster_size", "pitch_min", "scrimmage_min", "wing_max",
            "kick_scatter_dice", "throw_in_dice")
    return {
        "version": CAPABILITIES_VERSION, "ruleset_id": "BB2016",
        "scope": "Shipped gym configurations and cited tests only; custom inputs are unverified",
        "variant": {"size": size, "config": "gym-" + str(size),
                    "kind": "small-board variant" if size != 11 else "standard board",
                    "rounds": 8, "kick_off_table": True, **dict(zip(keys, variants[size]))},
        "capabilities": [
            {"id": "bb2016", "status": "partial",
             "evidence": ["botbowl/data/rules/BB2016.xml", "botbowl/core/procedure.py"],
             "limits": "Ruleset identity is not full edition conformance or all-skill coverage."},
            {"id": "baseline-scenarios", "status": "implemented",
             "evidence": ["tests/game/test_baseline.py", "docs/reports/baseline-issue-4.md"],
             "limits": "Movement, block, pass, reroll, drive and end-game fixtures at seeds 0/17; "
                       "synthetic micropositions, kickoff/pathfinding disabled. Size-1 pass uses "
                       "two teammates and is not a legal one-player setup."},
            {"id": "pathfinding", "status": "partial",
             "evidence": ["tests/ai/test_pathfinding.py", "docs/reports/pathfinding-issue-13.md"],
             "limits": "Python/native parity is bounded by the tested corpus; optional native "
                       "availability and a config preference do not establish rule completeness."},
            {"id": "kickoff", "status": "partial",
             "evidence": ["tests/kickoff/test_kickoff_table.py", "botbowl/core/procedure.py"],
             "limits": "Dedicated event tests are not exhaustive all-size interaction coverage; "
                       "QuickSnap/forward-model investigation remains issue #20."},
            {"id": "pregame-inducements", "status": "unsupported",
             "evidence": ["botbowl/core/procedure.py:Pregame"],
             "limits": "Inducement procedures are not scheduled; XML data is not execution support."},
            {"id": "postgame-progression", "status": "unsupported",
             "evidence": ["botbowl/core/procedure.py:EndGame"],
             "limits": "EndGame records the result; it does not run league progression."},
            {"id": "headless", "status": "implemented",
             "evidence": ["tests/packaging/smoke.py", "tests/lab/test_rules_descriptor.py"],
             "limits": "Core and descriptor work without web, RL, rendering or artwork packages."},
        ],
    }
