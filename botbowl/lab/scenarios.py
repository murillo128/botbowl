"""Closed, versioned synthetic lab recipes; see docs/lab/scenarios.md."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from types import MappingProxyType

from botbowl.core.load import load_arena, load_config, load_rule_set, load_team_by_filename
from botbowl.core.model import Ball
from botbowl.core.procedure import EndGame, Half, Turn
from botbowl.core.table import WeatherType

from .randomness import SeedSpec
from .rules import RulesDescriptor, SUPPORTED_SIZES, describe_rules
from .session import (
    IncompatibleSnapshot, InvalidConfiguration, SessionConfig, SessionResult,
    SessionSnapshot, SimulationSession,
)
from .snapshots import LogicalTime, clone_from_snapshot


class ScenarioError(InvalidConfiguration):
    """Unknown, incompatible or structurally invalid scenario input."""

    code = "invalid_scenario"


@dataclass(frozen=True)
class ScenarioRecipeV1:
    scenario_id: str
    version: int
    sizes: tuple
    objective: str
    participants: tuple


# Package-owned inventory. IDs never resolve modules, paths, objects or callbacks.
SCENARIO_RECIPES = MappingProxyType({recipe.scenario_id: recipe for recipe in (
    ScenarioRecipeV1("movement", 1, SUPPORTED_SIZES, "reach_target", (1, 0)),
    ScenarioRecipeV1("pickup", 1, SUPPORTED_SIZES, "gain_possession", (1, 0)),
    ScenarioRecipeV1("pass_receive", 1, (3, 5, 7, 11), "receiver_has_ball", (2, 0)),
    ScenarioRecipeV1("block", 1, SUPPORTED_SIZES, "resolve_block", (1, 1)),
    ScenarioRecipeV1("possession_recovery", 1, SUPPORTED_SIZES, "gain_possession", (1, 1)),
    ScenarioRecipeV1("touchdown", 1, SUPPORTED_SIZES, "score_touchdown", (1, 0)),
)})


def _integer(value, lower, upper, name):
    if type(value) is not int or not lower <= value <= upper:
        raise ScenarioError("%s must be an integer in [%d, %d]" % (name, lower, upper))


@dataclass(frozen=True)
class ScenarioSpecV1:
    scenario_id: str
    version: int
    rules: RulesDescriptor
    size: int
    side: str
    parameters: dict
    scenario_seed: int
    end_condition: str
    max_decisions: int = 32
    max_steps: int = 1000

    def __post_init__(self):
        if type(self.scenario_id) is not str or self.scenario_id not in SCENARIO_RECIPES:
            raise ScenarioError("Unknown registered scenario_id")
        recipe = SCENARIO_RECIPES[self.scenario_id]
        if type(self.version) is not int or self.version != recipe.version:
            raise ScenarioError("Unsupported recipe version")
        if type(self.rules) is not RulesDescriptor:
            raise ScenarioError("Expected RulesDescriptor")
        if any(type(value) is not str or not 1 <= len(value) <= 512
               for value in self.rules.to_json().values()):
            raise ScenarioError("Rules descriptor fields must be bounded nonempty strings")
        if type(self.size) is not int or self.size not in recipe.sizes:
            raise ScenarioError("Size is incompatible with this recipe")
        if type(self.side) is not str or self.side not in ("home", "away"):
            raise ScenarioError("side must be home or away")
        if self.end_condition != recipe.objective:
            raise ScenarioError("Unsupported end condition for this recipe")
        _integer(self.scenario_seed, 0, 2**256 - 1, "scenario_seed")
        _integer(self.max_decisions, 1, 256, "max_decisions")
        _integer(self.max_steps, 1, 10000, "max_steps")
        if type(self.parameters) is not dict or set(self.parameters) - {"distance", "rerolls"}:
            raise ScenarioError("Only distance and rerolls parameters are supported")
        parameters = {"distance": 1, "rerolls": 0, **deepcopy(self.parameters)}
        maximum = 1 if self.size == 1 or self.scenario_id == "block" else 3
        _integer(parameters["distance"], 1, maximum, "distance")
        _integer(parameters["rerolls"], 0, 3, "rerolls")
        object.__setattr__(self, "parameters", parameters)

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, data):
        try:
            if type(data) is not dict or set(data) != set(cls.__dataclass_fields__):
                raise ScenarioError("Expected exact ScenarioSpecV1 fields")
            fields = deepcopy(data)
            fields["rules"] = RulesDescriptor(**fields["rules"])
            return cls(**fields)
        except (TypeError, ValueError) as error:
            raise ScenarioError("Invalid ScenarioSpecV1 data") from error

    @property
    def variant_id(self):
        # Seed selects a replicate; policy seeds never enter scenario identity.
        data = self.to_json()
        del data["scenario_seed"]
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return "%s-v%d-sha256:%s" % (self.scenario_id, self.version, digest)


def _resources(size):
    if type(size) is not int or size not in SUPPORTED_SIZES:
        raise ScenarioError("Unsupported size")
    config = load_config("gym-%d" % size)
    config.pathfinding_enabled = False
    rules = load_rule_set(config.ruleset)
    home = load_team_by_filename("human", rules, board_size=size)
    away = load_team_by_filename("human", rules, board_size=size)
    descriptor = describe_rules(config, rules, load_arena(config.arena), home, away)
    return config, home, away, descriptor


def scenario_spec(scenario_id, *, size=3, side="home", scenario_seed=0,
                  parameters=None, max_decisions=32, max_steps=1000):
    """Describe one shipped recipe with the actual loaded rules/backend identity."""
    if type(scenario_id) is not str or scenario_id not in SCENARIO_RECIPES:
        raise ScenarioError("Unknown registered scenario_id")
    descriptor = _resources(size)[3]
    return ScenarioSpecV1(scenario_id, 1, descriptor, size, side,
                          {} if parameters is None else parameters, scenario_seed,
                          SCENARIO_RECIPES[scenario_id].objective, max_decisions, max_steps)


def _layout(spec, game):
    rng = SeedSpec(spec.scenario_seed, spec.scenario_id, "scenario", "recipes-v1").generator()
    y = game.arena.height // 2
    if spec.size != 1:
        y += int(rng.randint(-1, 2))
    distance = spec.parameters["distance"]
    x = game.arena.width // 2
    if spec.scenario_id == "touchdown":
        x = 1 + distance
    target = (x - distance, y)
    own = [(x, y)]
    opponents = []
    if spec.scenario_id == "pass_receive":
        own.append(target)
    elif spec.scenario_id == "block":
        opponents.append(target)
    elif spec.scenario_id == "possession_recovery":
        opponents.append((target[0] - 1, y))
    if spec.side == "away":
        def mirror(point):
            return game.arena.width - 1 - point[0], point[1]
        own = [mirror(point) for point in own]
        opponents = [mirror(point) for point in opponents]
        target = mirror(target)
    return own, opponents, target


def _construct(session, spec):
    """Owned synthetic constructor, never a user-supplied procedure stack."""
    game = session._game
    game.time_source = LogicalTime()
    state = game.state
    own = state.home_team if spec.side == "home" else state.away_team
    opponent = game.get_opp_team(own)
    state.half, state.round = 1, 1
    state.kicking_first_half = opponent
    state.receiving_first_half = own
    state.kicking_this_drive = opponent
    state.receiving_this_drive = own
    game.set_turn_order_from(own)
    game.start_time = 0.0  # Synthetic marker, not evidence of a played pregame.
    state.weather = WeatherType.NICE
    for team in state.teams:
        team.state.rerolls = spec.parameters["rerolls"]
        team.state.apothecaries = 0
        state.dugouts[team.team_id].reserves[:] = team.players
    own_positions, opponents, target = _layout(spec, game)
    for team, positions in ((own, own_positions), (opponent, opponents)):
        for player, position in zip(team.players, positions):
            game.reserves_to_pitch(player, game.get_square(*position))
    carried = spec.scenario_id in ("pass_receive", "touchdown")
    ball_position = own_positions[0] if carried else target
    # Movement/block still have exactly one loose ball, away from the exercise.
    if spec.scenario_id in ("movement", "block"):
        ball_position = (game.arena.width - 2, 1) if spec.side == "home" else (1, 1)
    state.pitch.balls[:] = [Ball(game.get_square(*ball_position), on_ground=True, is_carried=carried)]
    state.stack.items.clear()
    EndGame(game)
    Half(game, 2)
    first_half = Half(game, 1)
    first_half.kicked_off = first_half.prepared = True
    Turn(game, opponent, 1, 1)
    turn = Turn(game, own, 1, 1)
    game._timeline_phase("half_started")
    game._timeline_phase("round_started")
    game._timeline_phase("drive_started")
    turn.start()
    turn.started = True
    state.available_actions = []
    game.advance(None, max_steps=1000)  # Resolve TurnStunned to the first coach decision.


def _validate_start(session, spec):
    game = session._game
    state = game.state
    expected = state.home_team if spec.side == "home" else state.away_team
    if type(game.get_procedure()) is not Turn or game.active_team is not expected:
        raise ScenarioError("Recipe did not establish the requested turn boundary")
    for team in state.teams:
        pitch = game.get_players_on_pitch(team)
        if len(pitch) > spec.size:
            raise ScenarioError("Recipe exceeds the pitch participant limit")
        dugout = state.dugouts[team.team_id]
        groups = [pitch, dugout.reserves, dugout.kod, dugout.casualties, dugout.dungeon]
        members = [p for group in groups for p in group]
        if len(members) != len(team.players) or {id(p) for p in members} != {id(p) for p in team.players}:
            raise ScenarioError("Player occupancy and dugout membership disagree")
        if team.state.rerolls != spec.parameters["rerolls"] or team.state.apothecaries != 0:
            raise ScenarioError("Recipe resources disagree")
        if any(p.team is not team or game.is_out_of_bounds(p.position) for p in pitch):
            raise ScenarioError("Foreign or out-of-bounds participant")
    if len(state.pitch.balls) != 1:
        raise ScenarioError("Expected exactly one ball")
    ball = game.get_ball()
    if game.is_out_of_bounds(ball.position) or not ball.on_ground:
        raise ScenarioError("Invalid ball position/state")
    carrier = game.get_player_at(ball.position)
    if ball.is_carried and (carrier is None or not carrier.state.up):
        raise ScenarioError("Carried ball has no standing carrier")
    if not ball.is_carried and carrier is not None:
        raise ScenarioError("Loose ball overlaps a standing participant")
    if state.weather != WeatherType.NICE or not session.legal_actions().actions:
        raise ScenarioError("Missing legal decision or incompatible weather")
    # Reuse #37's canonical board/index, team, procedure and timeline checks.
    clone_from_snapshot(session.snapshot().engine).close()
    for action in session.legal_actions().actions:
        session._actions.decode(session._actions.request(action))


@dataclass(frozen=True)
class ScenarioResult(SessionResult):
    scenario_terminal: bool
    scenario_success: bool

    def to_json(self):
        return {**super().to_json(), "scenario_terminal": self.scenario_terminal,
                "scenario_success": self.scenario_success}


@dataclass(frozen=True)
class ScenarioSnapshot:
    spec: ScenarioSpecV1
    session: SessionSnapshot

    def metadata(self):
        """Data sidecar for #39; persist session.engine separately via write_snapshot."""
        return {"spec": self.spec.to_json(), "session": {
            name: getattr(self.session, name) for name in (
                "schema_version", "scope", "accepted_decisions", "max_decisions",
                "max_steps", "truncation_reason")}}

    @classmethod
    def from_metadata(cls, data, engine):
        try:
            if type(data) is not dict or set(data) != {"spec", "session"}:
                raise ScenarioError("Invalid scenario snapshot metadata")
            return cls(ScenarioSpecV1.from_json(data["spec"]), SessionSnapshot(engine=engine, **data["session"]))
        except (TypeError, ValueError) as error:
            raise ScenarioError("Invalid scenario snapshot metadata") from error


class ScenarioSession:
    """Bounded exercise composed over SimulationSession; policies stay external."""

    def __init__(self, spec):
        if type(spec) is not ScenarioSpecV1:
            raise ScenarioError("Expected ScenarioSpecV1")
        self._spec = ScenarioSpecV1.from_json(spec.to_json())
        candidate = None
        try:
            config, home, away, descriptor = _resources(self._spec.size)
            if self._spec.rules != descriptor:
                raise ScenarioError("Rules descriptor differs from the registered recipe inputs")
            candidate = SimulationSession(
                SessionConfig(config, home, away, self._spec.size,
                              self._spec.max_decisions, self._spec.max_steps),
                SeedSpec(self._spec.scenario_seed, self._spec.scenario_id, "engine", "recipes-v1"),
            )
            _construct(candidate, self._spec)
            _validate_start(candidate, self._spec)
            self._session = candidate
        except Exception as error:
            if candidate is not None:
                candidate.close()
            if isinstance(error, ScenarioError):
                raise
            raise ScenarioError("Recipe construction failed validation") from error

    @property
    def spec(self):
        return ScenarioSpecV1.from_json(self._spec.to_json())

    @property
    def state_revision(self):
        return self._session.state_revision

    @property
    def closed(self):
        return self._session.closed

    @property
    def metadata(self):
        own, opponents, target = _layout(self._spec, self._session._game)
        return {"spec": self._spec.to_json(), "variant_id": self._spec.variant_id,
                "construction": "synthetic-turn-v1", "reachability": "structurally_validated",
                "legal_reachability_proven": False, "initial_positions": {
                    "acting": own, "opponents": opponents, "target": target},
                "difficulty": {"participants": SCENARIO_RECIPES[self._spec.scenario_id].participants,
                    "space": {"size": self._spec.size, "rules": self._spec.rules.to_json()},
                    "distance": self._spec.parameters["distance"], "obstacles": len(opponents),
                    "resources": {"rerolls_per_team": self._spec.parameters["rerolls"],
                                  "apothecaries": 0, "weather": "NICE"},
                    "horizon": self._spec.max_decisions, "policy": "external"}}

    def _outcome(self):
        game = self._session._game
        own = game.state.home_team if self._spec.side == "home" else game.state.away_team
        reports = game.state.reports
        kinds = {report.outcome_type.name for report in reports}
        target = game.get_square(*_layout(self._spec, game)[2])
        family = self._spec.scenario_id
        if family == "movement":
            success = own.players[0].position == target and own.players[0].state.up
        elif family in ("pickup", "possession_recovery"):
            success = game.get_ball_carrier() is own.players[0]
        elif family == "pass_receive":
            success = bool(kinds & {"ACCURATE_PASS", "INACCURATE_PASS"}) and game.get_ball_carrier() is own.players[1]
        elif family == "block":
            success = "BLOCK_ROLL" in kinds and type(game.get_procedure()) is Turn
        else:
            success = any(r.outcome_type.name == "TOUCHDOWN" and r.team is own for r in reports)
        turn_ended = bool(kinds & {"TURNOVER", "END_OF_TURN", "TOUCHDOWN"})
        return bool(success or turn_ended), bool(success)

    def _result(self, result):
        # Engine failure/close and natural completion retain their own status.
        if result.terminated or (result.truncated and result.end_reason != "decision_budget"):
            terminal, success = False, False
        else:
            terminal, success = self._outcome()
        data = dict(vars(result))
        if terminal:
            control = deepcopy(result.control)
            control["data"]["action_ids"] = []
            control["data"]["action_mask"] = []
            data.update(truncated=False, end_reason="scenario_terminal", next_actor=None,
                        decision_id=None, control=control)
        return ScenarioResult(**data, scenario_terminal=terminal, scenario_success=success)

    def observe(self, observer_team=None):
        return self._result(self._session.observe(observer_team))

    def legal_actions(self, observer_team=None):
        legal = self._session.legal_actions(observer_team)
        if self.observe(observer_team).scenario_terminal:
            return replace(legal, actor_id=None, actions=[], macros=[])
        return legal

    def step(self, action, expected_revision):
        self._session._require_mutable()
        self._session._check_revision(expected_revision)
        if self.observe().scenario_terminal:
            return self.observe()
        return self._result(self._session.step(action, expected_revision))

    def snapshot(self):
        return ScenarioSnapshot(self.spec, self._session.snapshot())

    def restore(self, snapshot, expected_revision):
        if type(snapshot) is not ScenarioSnapshot or snapshot.spec != self._spec:
            raise IncompatibleSnapshot("Scenario snapshot belongs to a different recipe/variant/seed")
        return self._result(self._session.restore(snapshot.session, expected_revision))

    def close(self):
        self._session.close()


def create_scenario(spec):
    """Construct a private validated exercise, or raise ScenarioError atomically."""
    return ScenarioSession(spec)
