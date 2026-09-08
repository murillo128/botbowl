"""Versioned semantic actions and observable controller macro expansion.

The types in this module contain copied data only.  ``ActionControl`` owns the
episode-local mapping back to engine objects and the current decision token;
it must remain in trusted control code rather than being used as model input.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from botbowl.core import procedure as proc
from botbowl.core.model import Action, ActionChoice, Player, Square
from botbowl.core.table import ActionType, Skill
from botbowl.lab.observations import ObservationControl

Side = Literal["home", "away"]
MacroStatus = Literal["completed", "interrupted"]


class SemanticActionError(ValueError):
    """Base class for rejected semantic action data."""

    code = "semantic_action_error"

    def __init__(self, message: str):
        super().__init__(message)


class ActionSchemaError(SemanticActionError):
    code = "invalid_action_schema"


class StaleDecisionError(SemanticActionError):
    code = "stale_decision"


class WrongActorError(SemanticActionError):
    code = "wrong_actor"


class UnknownEntityError(SemanticActionError):
    code = "unknown_entity"


class InvalidPositionError(SemanticActionError):
    code = "invalid_position"


class AmbiguousActionError(SemanticActionError):
    code = "ambiguous_action"


class ActionNotOfferedError(SemanticActionError):
    code = "action_not_offered"


@dataclass(frozen=True)
class PositionV1:
    x: int
    y: int

    def to_json(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_json(cls, value):
        if type(value) is not dict or set(value) != {"x", "y"}:
            raise ActionSchemaError("position must contain exactly x and y")
        if any(type(value[name]) is not int for name in ("x", "y")):
            raise ActionSchemaError("position coordinates must be integers")
        return cls(value["x"], value["y"])


@dataclass(frozen=True)
class EmptyOptionsV1:
    def to_json(self) -> dict:
        return {}


@dataclass(frozen=True)
class SkillOptionsV1:
    skill: str

    def to_json(self) -> dict[str, str]:
        return {"skill": self.skill}


@dataclass(frozen=True)
class PathOptionsV1:
    """A declared engine path, copied without rolls or probabilities."""

    path: list[PositionV1]

    def to_json(self) -> dict[str, list[dict[str, int]]]:
        return {"path": [position.to_json() for position in self.path]}


ActionOptionsV1 = EmptyOptionsV1 | SkillOptionsV1 | PathOptionsV1


# These sets are a wire contract, deliberately independent of enum ordinals.
_TARGET_TYPES = frozenset({"BLOCK", "STAB", "HANDOFF", "FOUL", "HYPNOTIC_GAZE"})
_SKILL_TYPES = frozenset({"USE_SKILL", "DONT_USE_SKILL"})
_FORMATION_TYPES = {
    "SETUP_FORMATION_WEDGE": "Wedge",
    "SETUP_FORMATION_LINE": "Line",
    "SETUP_FORMATION_SPREAD": "Spread",
    "SETUP_FORMATION_ZONE": "Zone",
}
_PATH_TYPES = frozenset({"MOVE", "BLOCK", "STAB", "HANDOFF", "FOUL"})
_PLAYER_POSITION_PRODUCT_TYPES = frozenset({"PLACE_PLAYER"})


@dataclass(frozen=True)
class ActionV1:
    schema_version: Literal[1]
    type: str
    actor_id: Side
    player_id: str | None
    target_id: str | None
    position: PositionV1 | None
    options: ActionOptionsV1

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "type": self.type,
            "actor_id": self.actor_id,
            "player_id": self.player_id,
            "target_id": self.target_id,
            "position": None if self.position is None else self.position.to_json(),
            "options": self.options.to_json(),
        }

    @classmethod
    def from_json(cls, value):
        expected = {
            "schema_version",
            "type",
            "actor_id",
            "player_id",
            "target_id",
            "position",
            "options",
        }
        if type(value) is not dict or set(value) != expected:
            raise ActionSchemaError("action must contain exactly the ActionV1 fields")
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ActionSchemaError("unsupported action schema_version")
        if (
            type(value["type"]) is not str
            or value["type"] not in ActionType.__members__
        ):
            raise ActionSchemaError("type must be a known ActionType name")
        if value["actor_id"] not in ("home", "away"):
            raise ActionSchemaError("actor_id must be home or away")
        for name in ("player_id", "target_id"):
            if value[name] is not None and type(value[name]) is not str:
                raise ActionSchemaError(name + " must be a string or null")
        position = (
            None
            if value["position"] is None
            else PositionV1.from_json(value["position"])
        )
        raw_options = value["options"]
        if type(raw_options) is not dict:
            raise ActionSchemaError("options must be an object")
        if not raw_options:
            options = EmptyOptionsV1()
        elif set(raw_options) == {"skill"} and value["type"] in _SKILL_TYPES:
            if (
                type(raw_options["skill"]) is not str
                or raw_options["skill"] not in Skill.__members__
            ):
                raise ActionSchemaError("skill option must be an enum name")
            options = SkillOptionsV1(raw_options["skill"])
        elif set(raw_options) == {"path"} and value["type"] in _PATH_TYPES:
            if type(raw_options["path"]) is not list or not raw_options["path"]:
                raise ActionSchemaError("path option must be a nonempty list")
            options = PathOptionsV1(
                [PositionV1.from_json(item) for item in raw_options["path"]]
            )
        else:
            raise ActionSchemaError("options are not valid for this action type")
        action_type = value["type"]
        if action_type in _TARGET_TYPES:
            if (
                value["target_id"] is None
                or value["player_id"] is not None
                or position is not None
            ):
                raise ActionSchemaError(
                    "target actions require only target_id as their entity payload"
                )
        elif value["target_id"] is not None:
            raise ActionSchemaError("target_id is prohibited for this action type")
        if (
            value["player_id"] is not None
            and position is not None
            and action_type not in _PLAYER_POSITION_PRODUCT_TYPES
        ):
            raise ActionSchemaError(
                "only PLACE_PLAYER may combine player_id and position"
            )
        if action_type == "PLACE_PLAYER" and value["player_id"] is None:
            raise ActionSchemaError("PLACE_PLAYER requires player_id")
        if action_type in _SKILL_TYPES and not isinstance(options, SkillOptionsV1):
            raise ActionSchemaError("skill decisions require a typed skill option")
        if isinstance(options, PathOptionsV1) and (
            (action_type in _TARGET_TYPES and value["target_id"] is None)
            or (action_type not in _TARGET_TYPES and position is None)
        ):
            raise ActionSchemaError(
                "path options require their final target or position"
            )
        return cls(
            1,
            value["type"],
            value["actor_id"],
            value["player_id"],
            value["target_id"],
            position,
            options,
        )


@dataclass(frozen=True)
class ActionRequestV1:
    schema_version: Literal[1]
    decision_id: str
    state_revision: int
    action: ActionV1

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "state_revision": self.state_revision,
            "action": self.action.to_json(),
        }

    @classmethod
    def from_json(cls, value):
        expected = {"schema_version", "decision_id", "state_revision", "action"}
        if type(value) is not dict or set(value) != expected:
            raise ActionSchemaError(
                "request must contain exactly the ActionRequestV1 fields"
            )
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ActionSchemaError("unsupported request schema_version")
        if type(value["decision_id"]) is not str:
            raise ActionSchemaError("decision_id must be a string")
        if type(value["state_revision"]) is not int:
            raise ActionSchemaError("state_revision must be an integer")
        return cls(
            1,
            value["decision_id"],
            value["state_revision"],
            ActionV1.from_json(value["action"]),
        )


@dataclass(frozen=True)
class FormationMacroV1:
    schema_version: Literal[1]
    macro_id: str
    actor_id: Side
    formation: str

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "macro_id": self.macro_id,
            "actor_id": self.actor_id,
            "kind": "formation",
            "formation": self.formation,
        }

    @classmethod
    def from_json(cls, value):
        expected = {"schema_version", "macro_id", "actor_id", "kind", "formation"}
        if type(value) is not dict or set(value) != expected:
            raise ActionSchemaError("formation macro has unknown or missing fields")
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ActionSchemaError("unsupported macro schema_version")
        if value["kind"] != "formation" or value["actor_id"] not in ("home", "away"):
            raise ActionSchemaError("invalid formation macro kind or actor")
        if (
            type(value["macro_id"]) is not str
            or value["formation"] not in _FORMATION_TYPES.values()
        ):
            raise ActionSchemaError("invalid formation macro identity")
        return cls(1, value["macro_id"], value["actor_id"], value["formation"])


@dataclass(frozen=True)
class RouteMacroV1:
    schema_version: Literal[1]
    macro_id: str
    actor_id: Side
    type: str
    path: list[PositionV1]

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "macro_id": self.macro_id,
            "actor_id": self.actor_id,
            "kind": "route",
            "type": self.type,
            "path": [position.to_json() for position in self.path],
        }

    @classmethod
    def from_json(cls, value):
        expected = {"schema_version", "macro_id", "actor_id", "kind", "type", "path"}
        if type(value) is not dict or set(value) != expected:
            raise ActionSchemaError("route macro has unknown or missing fields")
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ActionSchemaError("unsupported macro schema_version")
        if value["kind"] != "route" or value["actor_id"] not in ("home", "away"):
            raise ActionSchemaError("invalid route macro kind or actor")
        if type(value["macro_id"]) is not str or value["type"] not in _PATH_TYPES:
            raise ActionSchemaError("invalid route macro identity")
        if type(value["path"]) is not list or not value["path"]:
            raise ActionSchemaError("route path must be a nonempty list")
        return cls(
            1,
            value["macro_id"],
            value["actor_id"],
            value["type"],
            [PositionV1.from_json(item) for item in value["path"]],
        )


MacroV1 = FormationMacroV1 | RouteMacroV1


@dataclass(frozen=True)
class LegalActionsV1:
    schema_version: Literal[1]
    decision_id: str
    state_revision: int
    actor_id: Side | None
    actions: list[ActionV1]
    macros: list[MacroV1]

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "decision_id": self.decision_id,
            "state_revision": self.state_revision,
            "actor_id": self.actor_id,
            "actions": [action.to_json() for action in self.actions],
            "macros": [macro.to_json() for macro in self.macros],
        }


@dataclass(frozen=True)
class MacroStepV1:
    macro_id: str
    order: int
    decision_id: str
    state_revision: int
    action: ActionV1
    events: list[dict]

    def to_json(self) -> dict:
        return {
            "macro_id": self.macro_id,
            "order": self.order,
            "decision_id": self.decision_id,
            "state_revision": self.state_revision,
            "action": self.action.to_json(),
            "result": "accepted",
            "events": deepcopy(self.events),
        }


@dataclass(frozen=True)
class MacroResultV1:
    schema_version: Literal[1]
    macro_id: str
    status: MacroStatus
    steps: list[MacroStepV1]
    interruption: str | None

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "macro_id": self.macro_id,
            "status": self.status,
            "steps": [step.to_json() for step in self.steps],
            "interruption": self.interruption,
        }


def _position(square: Square | None) -> PositionV1 | None:
    return None if square is None else PositionV1(square.x, square.y)


def _path_steps(path) -> list[Square]:
    """Copy backend-neutral path steps and restore its lazy-cache state."""
    cached_steps, cached_rolls = path._steps, path._rolls
    try:
        return list(path.steps)
    finally:
        path._steps, path._rolls = cached_steps, cached_rolls


class ActionControl:
    """Episode-local semantic codec and monotonic decision-token owner."""

    def __init__(self, game, entities: ObservationControl | None = None):
        self._game = game
        self.entities = ObservationControl(game) if entities is None else entities
        self.entities._check(game)
        self._actions_ref = None
        self._choice_refs = ()
        self._top_ref = None
        self._terminal = None
        self._revision = -1
        self._offered_cache = None
        self._macros_cache = None
        self._by_wire = {}
        self._by_core = {}

    def _check(self, game) -> None:
        if game is not self._game:
            raise UnknownEntityError("action control belongs to a different episode")
        self.entities._check(game)

    def _sync(self, game) -> None:
        self._check(game)
        actions = game.state.available_actions
        choices = tuple(actions)
        top = game.state.stack.items[-1] if game.state.stack.items else None
        changed = (
            actions is not self._actions_ref
            or len(choices) != len(self._choice_refs)
            or any(left is not right for left, right in zip(choices, self._choice_refs))
            or top is not self._top_ref
            or game.state.game_over is not self._terminal
        )
        if changed:
            self._actions_ref = actions
            self._choice_refs = choices
            self._top_ref = top
            self._terminal = game.state.game_over
            self._revision += 1
            self._offered_cache = None
            self._macros_cache = None
            self._by_wire = {}
            self._by_core = {}

    @property
    def state_revision(self) -> int:
        self._sync(self._game)
        return self._revision

    @property
    def decision_id(self) -> str:
        return "decision-" + str(self.state_revision)

    def _side(self, team) -> Side | None:
        return self.entities._team(team)

    def _player_id(self, player) -> str | None:
        return self.entities._player(player)

    def _resolve_player(self, local_id: str) -> Player:
        try:
            internal = self.entities.internal_player_id(local_id)
        except (TypeError, ValueError) as error:
            raise UnknownEntityError("unknown episode-local player ID") from error
        player = self._game.state.player_by_id.get(internal)
        if player is None:
            raise UnknownEntityError("player is not registered in this game")
        return player

    def _resolve_position(self, position: PositionV1) -> Square:
        if type(position.x) is not int or type(position.y) is not int:
            raise InvalidPositionError("position coordinates must be integers")
        if not (
            0 <= position.x < self._game.arena.width
            and 0 <= position.y < self._game.arena.height
        ):
            raise InvalidPositionError("position is outside the board")
        return self._game.get_square(position.x, position.y)

    def _options(self, choice: ActionChoice, position) -> ActionOptionsV1:
        if choice.skill is not None:
            return SkillOptionsV1(choice.skill.name)
        if choice.paths and position is not None:
            matches = [
                path for path in choice.paths if path.get_last_step() == position
            ]
            if len(matches) != 1:
                raise AmbiguousActionError(
                    "path endpoint does not identify one offered path"
                )
            return PathOptionsV1(
                [PositionV1(step.x, step.y) for step in _path_steps(matches[0])]
            )
        return EmptyOptionsV1()

    def _semantic(self, choice: ActionChoice, player, position) -> ActionV1:
        action_type = choice.action_type.name
        player_id = target_id = None
        semantic_position = _position(position)
        if action_type in _TARGET_TYPES and position is not None:
            target = self._game.get_player_at(position)
            if target is None:
                raise AmbiguousActionError(action_type + " requires an occupied target")
            target_id = self._player_id(target)
            semantic_position = None
        elif player is not None:
            player_id = self._player_id(player)
        return ActionV1(
            1,
            action_type,
            self._side(choice.team),
            player_id,
            target_id,
            semantic_position,
            self._options(choice, position),
        )

    def _choice_candidates(self, choice: ActionChoice):
        if choice.disabled:
            return
        players, positions = choice.players, choice.positions
        if (
            players
            and positions
            and choice.action_type.name not in _PLAYER_POSITION_PRODUCT_TYPES
        ):
            raise AmbiguousActionError(
                choice.action_type.name + " has no declared player/position pairing"
            )
        if players and positions:
            for player in players:
                for position in positions:
                    yield player, position
        elif players:
            for player in players:
                yield player, None
        elif positions:
            for position in positions:
                yield None, position
        else:
            yield None, None

    def _offered(self, game) -> list[tuple[ActionV1, Action]]:
        self._sync(game)
        if self._offered_cache is not None:
            return self._offered_cache
        offered = []
        seen = set()
        for choice in game.get_available_actions():
            for player, position in self._choice_candidates(choice):
                core = Action(choice.action_type, player=player, position=position)
                if not game.is_action_allowed(core):
                    continue
                semantic = self._semantic(choice, player, position)
                key = repr(semantic.to_json())
                if key in seen:
                    continue
                seen.add(key)
                offered.append((semantic, core))
                self._by_wire.setdefault(key, []).append((semantic, core))
                self._by_core.setdefault(
                    self._choice_core_key(core, choice), []
                ).append(semantic)
        self._offered_cache = offered
        return offered

    def _macros(self, game) -> list[MacroV1]:
        self._sync(game)
        if self._macros_cache is not None:
            return self._macros_cache
        actor = self._side(game.active_team)
        if actor is None:
            self._macros_cache = []
            return []
        macros: list[MacroV1] = []
        ordinal = 0
        for choice in game.get_available_actions():
            if choice.disabled:
                continue
            formation = _FORMATION_TYPES.get(choice.action_type.name)
            if formation is not None:
                macros.append(
                    FormationMacroV1(
                        1, f"{self.decision_id}:formation:{ordinal}", actor, formation
                    )
                )
                ordinal += 1
            for path in choice.paths:
                if choice.action_type.name not in _PATH_TYPES:
                    raise AmbiguousActionError("unsupported path action family")
                macros.append(
                    RouteMacroV1(
                        1,
                        f"{self.decision_id}:route:{ordinal}",
                        actor,
                        choice.action_type.name,
                        [PositionV1(step.x, step.y) for step in _path_steps(path)],
                    )
                )
                ordinal += 1
        self._macros_cache = macros
        return macros

    @staticmethod
    def _choice_core_key(action: Action, choice: ActionChoice):
        player = action.player if choice.players else None
        position = action.position if choice.positions else None
        return (
            action.action_type.name,
            None if player is None else player.player_id,
            None if position is None else (position.x, position.y),
        )

    def legal_actions(self, game=None) -> LegalActionsV1:
        game = self._game if game is None else game
        offered = self._offered(game)
        actor = self._side(game.active_team)
        return LegalActionsV1(
            1,
            self.decision_id,
            self.state_revision,
            actor,
            deepcopy([semantic for semantic, _ in offered]),
            deepcopy(self._macros(game)),
        )

    def request(self, action: ActionV1) -> ActionRequestV1:
        return ActionRequestV1(1, self.decision_id, self.state_revision, action)

    def _validate_request(self, request: ActionRequestV1) -> None:
        if not isinstance(request, ActionRequestV1) or request.schema_version != 1:
            raise ActionSchemaError("expected an ActionRequestV1")
        if (
            request.decision_id != self.decision_id
            or request.state_revision != self.state_revision
        ):
            raise StaleDecisionError("proposal belongs to an obsolete decision")
        if request.action.actor_id != self._side(self._game.active_team):
            raise WrongActorError("action actor does not own the current decision")

    def decode(self, request: ActionRequestV1) -> Action:
        self._sync(self._game)
        self._validate_request(request)
        action = request.action
        if action.player_id is not None:
            player = self._resolve_player(action.player_id)
            if self._side(player.team) != action.actor_id:
                raise WrongActorError("selected player does not belong to the actor")
        if action.target_id is not None:
            self._resolve_player(action.target_id)
        if action.position is not None:
            self._resolve_position(action.position)
        if isinstance(action.options, PathOptionsV1):
            for position in action.options.path:
                self._resolve_position(position)
        self._offered(self._game)
        matches = [
            core
            for semantic, core in self._by_wire.get(repr(action.to_json()), [])
            if semantic == action
        ]
        if not matches:
            raise ActionNotOfferedError(
                "semantic action is not offered at this decision"
            )
        if len(matches) != 1:
            raise AmbiguousActionError(
                "semantic action identifies more than one engine action"
            )
        # Rebuild against canonical episode objects; never return caller data.
        core = matches[0]
        return Action(core.action_type, player=core.player, position=core.position)

    def encode(self, action: Action) -> ActionV1:
        if not self._game.is_action_allowed(action):
            raise ActionNotOfferedError("engine action is not offered at this decision")
        normalized = self._game._validated_action(action)
        self._offered(self._game)
        keys = {
            self._choice_core_key(normalized, choice)
            for choice in self._game.get_available_actions()
            if not choice.disabled and choice.action_type is normalized.action_type
        }
        matches = []
        seen = set()
        for key in keys:
            for semantic in self._by_core.get(key, []):
                wire = repr(semantic.to_json())
                if wire not in seen:
                    seen.add(wire)
                    matches.append(semantic)
        if not matches:
            raise ActionNotOfferedError("engine action has no semantic representation")
        if len(matches) != 1:
            raise AmbiguousActionError(
                "engine action has multiple semantic representations"
            )
        return deepcopy(matches[0])

    def _formation_plan(self, macro: FormationMacroV1) -> list[Action]:
        top = self._game.get_procedure()
        if type(top) is not proc.Setup:
            raise ActionNotOfferedError("formation macro requires a setup decision")
        formation = next(
            (item for item in top.formations if item.name == macro.formation), None
        )
        if formation is None:
            raise ActionNotOfferedError("formation is not offered at this decision")
        offered = _FORMATION_TYPES.get(
            next(
                (
                    choice.action_type.name
                    for choice in self._game.get_available_actions()
                    if not choice.disabled
                    and _FORMATION_TYPES.get(choice.action_type.name) == macro.formation
                ),
                "",
            )
        )
        if offered is None:
            raise ActionNotOfferedError("formation is not offered at this decision")
        return formation.actions(self._game, top.team)

    def _route_plan(self, macro: RouteMacroV1) -> list[Action]:
        if macro.type not in _PATH_TYPES or not macro.path:
            raise ActionSchemaError("route has an unsupported type or empty path")
        squares = [self._resolve_position(position) for position in macro.path]
        actions = [Action(ActionType.MOVE, position=square) for square in squares]
        actions[-1] = Action(ActionType[macro.type], position=squares[-1])
        return actions

    def execute_macro(self, macro: MacroV1, *, max_steps=100000) -> MacroResultV1:
        """Execute only anticipated primitive decisions and retain every result."""
        if (
            not isinstance(macro, (FormationMacroV1, RouteMacroV1))
            or macro.schema_version != 1
        ):
            raise ActionSchemaError("expected a supported version-1 macro")
        self._sync(self._game)
        actor = self._side(self._game.active_team)
        if macro.actor_id != actor:
            raise WrongActorError("macro actor does not own the current decision")
        legal_macros = self._macros(self._game)
        if macro not in legal_macros:
            raise StaleDecisionError("macro belongs to an obsolete or unknown decision")
        plan = (
            self._formation_plan(macro)
            if isinstance(macro, FormationMacroV1)
            else self._route_plan(macro)
        )
        records = []
        for order, core in enumerate(plan):
            if self._game.state.game_over:
                return MacroResultV1(
                    1, macro.macro_id, "interrupted", records, "terminal"
                )
            if self._side(self._game.active_team) != actor:
                return MacroResultV1(
                    1, macro.macro_id, "interrupted", records, "actor_changed"
                )
            try:
                semantic = self.encode(core)
            except SemanticActionError:
                return MacroResultV1(
                    1, macro.macro_id, "interrupted", records, "unplanned_decision"
                )
            request = self.request(semantic)
            decoded = self.decode(request)
            result = self._game.advance(decoded, max_steps=max_steps)
            events = [deepcopy(event.to_json()) for event in result.events]
            records.append(
                MacroStepV1(
                    macro.macro_id,
                    order,
                    request.decision_id,
                    request.state_revision,
                    semantic,
                    events,
                )
            )
            self._sync(self._game)
        if (
            isinstance(macro, RouteMacroV1)
            and type(self._game.get_procedure()) is proc.Reroll
        ):
            return MacroResultV1(
                1, macro.macro_id, "interrupted", records, "unplanned_decision"
            )
        if (
            isinstance(macro, RouteMacroV1)
            and self._side(self._game.active_team) != actor
        ):
            return MacroResultV1(
                1, macro.macro_id, "interrupted", records, "actor_changed"
            )
        return MacroResultV1(1, macro.macro_id, "completed", records, None)


SemanticActionControl = ActionControl


def macro_from_json(value) -> MacroV1:
    if type(value) is not dict:
        raise ActionSchemaError("macro must be an object")
    if value.get("kind") == "formation":
        return FormationMacroV1.from_json(value)
    if value.get("kind") == "route":
        return RouteMacroV1.from_json(value)
    raise ActionSchemaError("unknown macro kind")


def legal_actions(game, control: ActionControl) -> LegalActionsV1:
    return control.legal_actions(game)


def encode_action(game, action: Action, control: ActionControl) -> ActionV1:
    control._check(game)
    return control.encode(action)


def decode_action(game, request: ActionRequestV1, control: ActionControl) -> Action:
    control._check(game)
    return control.decode(request)


def gym_to_semantic(env, index: int, control: ActionControl) -> ActionV1:
    """Translate one current Gymnasium index through its canonical engine action."""
    if env.game is not control._game:
        raise UnknownEntityError(
            "Gymnasium environment and action control use different games"
        )
    return control.encode(env.decode_action(index))


def semantic_to_gym(env, action: ActionV1, control: ActionControl) -> int:
    """Translate one current semantic action to the historical flat index."""
    if env.game is not control._game:
        raise UnknownEntityError(
            "Gymnasium environment and action control use different games"
        )
    return env.encode_action(control.decode(control.request(action)))


__all__ = [
    "ActionControl",
    "ActionNotOfferedError",
    "ActionRequestV1",
    "ActionSchemaError",
    "ActionV1",
    "AmbiguousActionError",
    "EmptyOptionsV1",
    "FormationMacroV1",
    "InvalidPositionError",
    "LegalActionsV1",
    "MacroResultV1",
    "MacroStepV1",
    "PathOptionsV1",
    "PositionV1",
    "RouteMacroV1",
    "SemanticActionControl",
    "SemanticActionError",
    "SkillOptionsV1",
    "StaleDecisionError",
    "UnknownEntityError",
    "WrongActorError",
    "decode_action",
    "encode_action",
    "gym_to_semantic",
    "legal_actions",
    "macro_from_json",
    "semantic_to_gym",
]
