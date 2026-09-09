"""Public local simulation session composed from the accepted lab boundaries."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Literal, Optional, Tuple, Union

from botbowl.core.game import Game, GameTruncatedError, InvalidActionError, NoProgressError
from botbowl.core.model import Configuration, Team

from .actions import (
    ActionControl,
    ActionV1,
    LegalActionsV1,
    SemanticActionError,
)
from .channels import make_channel
from .observations import observe
from .randomness import SeedSpec
from .snapshots import (
    Snapshot, SnapshotError, capture_snapshot, clone_from_snapshot, restore_snapshot,
)
from .timeline import Timeline


Side = Literal["home", "away"]


@dataclass(frozen=True)
class SessionDiagnostic:
    """Detached controller diagnostic; it is never inserted into player data."""

    error_type: str
    code: str
    message: str

    def to_json(self) -> Dict[str, str]:
        return asdict(self)


class SessionError(Exception):
    code = "session_error"

    def __init__(self, message: str, diagnostic: Optional[SessionDiagnostic] = None):
        super().__init__(message)
        self.diagnostic = deepcopy(diagnostic)


class InvalidConfiguration(SessionError, ValueError):
    code = "invalid_configuration"


class InvalidAction(SessionError, ValueError):
    code = "invalid_action"


class StaleRevision(InvalidAction):
    code = "stale_revision"


class IncompatibleSnapshot(SessionError, ValueError):
    code = "incompatible_snapshot"


class SessionClosed(SessionError, RuntimeError):
    code = "session_closed"


class NoProgress(SessionError, RuntimeError):
    code = "no_progress"


class ExecutionFailure(SessionError, RuntimeError):
    code = "execution_failure"


@dataclass(frozen=True)
class SessionConfig:
    """Validated inputs for one externally controlled game."""

    game_config: Optional[Union[str, Configuration]] = None
    home_team: Union[str, Team] = "human"
    away_team: Union[str, Team] = "human"
    size: Optional[int] = None
    max_decisions: int = 1000
    max_steps: int = 100000

    def __post_init__(self) -> None:
        if type(self.max_decisions) is not int or self.max_decisions < 0:
            raise InvalidConfiguration("max_decisions must be a nonnegative integer")
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise InvalidConfiguration("max_steps must be a positive integer")


@dataclass(frozen=True)
class SessionResult:
    """Detached state returned by reset, observe and step."""

    primary: Dict[str, Any]
    derived: Dict[str, Any]
    control: Dict[str, Any]
    events: Tuple[Dict[str, Any], ...]
    next_actor: Optional[Side]
    terminated: bool
    truncated: bool
    end_reason: Optional[str]
    decision_id: Optional[str]
    state_revision: int

    def to_json(self) -> Dict[str, Any]:
        return {
            "primary": deepcopy(self.primary),
            "derived": deepcopy(self.derived),
            "control": deepcopy(self.control),
            "events": deepcopy(list(self.events)),
            "next_actor": self.next_actor,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "end_reason": self.end_reason,
            "decision_id": self.decision_id,
            "state_revision": self.state_revision,
        }


@dataclass(frozen=True)
class SessionSnapshot:
    """Privileged trusted snapshot plus session-owned budget state."""

    schema_version: Literal[1]
    scope: Literal["engine"]
    accepted_decisions: int
    max_decisions: int
    max_steps: int
    truncation_reason: Optional[str]
    engine: Snapshot = field(repr=False, compare=False)


def _diagnostic(error: BaseException) -> SessionDiagnostic:
    return SessionDiagnostic(
        type(error).__name__, str(getattr(error, "code", "engine_error")), str(error)
    )


class SimulationSession:
    """Own one local externally controlled simulation without exposing ``Game``.

    Policies are intentionally absent. A caller that wants policy scheduling
    must install and invoke a separate explicit driver outside this facade.
    """

    def __init__(
        self,
        config: Optional[SessionConfig] = None,
        seed_plan: Optional[SeedSpec] = None,
    ) -> None:
        self._game: Optional[Game] = None
        self._timeline: Optional[Timeline] = None
        self._actions: Optional[ActionControl] = None
        self._config: Optional[SessionConfig] = None
        self._seed_plan: Optional[SeedSpec] = None
        self._accepted_decisions = 0
        self._truncation_reason: Optional[str] = None
        self._revision = 0
        self._closed = False
        if config is not None or seed_plan is not None:
            if config is None or seed_plan is None:
                raise InvalidConfiguration("config and seed_plan must be supplied together")
            self.reset(config, seed_plan)

    @property
    def state_revision(self) -> int:
        return self._revision

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_initialized(self) -> None:
        if self._game is None:
            raise InvalidConfiguration("Session has not been reset")

    def _current(self) -> Tuple[Game, Timeline, ActionControl, SessionConfig]:
        self._require_initialized()
        assert self._game is not None
        assert self._timeline is not None
        assert self._actions is not None
        assert self._config is not None
        return self._game, self._timeline, self._actions, self._config

    def _require_mutable(self) -> None:
        if self._closed:
            raise SessionClosed("Session is closed")
        self._require_initialized()

    @staticmethod
    def _validated_config(config: SessionConfig) -> SessionConfig:
        if type(config) is not SessionConfig:
            raise InvalidConfiguration("config must be a SessionConfig")
        try:
            config.__post_init__()
        except (TypeError, ValueError) as error:
            raise InvalidConfiguration(
                "Session configuration is invalid", _diagnostic(error)
            ) from error
        return deepcopy(config)

    @staticmethod
    def _validated_seed(seed_plan: SeedSpec) -> SeedSpec:
        if type(seed_plan) is not SeedSpec:
            raise InvalidConfiguration("seed_plan must be a SeedSpec")
        try:
            result = SeedSpec(**seed_plan.to_json())
        except (TypeError, ValueError) as error:
            raise InvalidConfiguration(
                "Session seed plan is invalid", _diagnostic(error)
            ) from error
        if result.purpose != "engine":
            raise InvalidConfiguration("seed_plan purpose must be 'engine'")
        return result

    def reset(self, config: SessionConfig, seed_plan: SeedSpec) -> SessionResult:
        """Build and validate a replacement completely before swapping it in."""
        if self._closed:
            raise SessionClosed("Session is closed")
        config = self._validated_config(config)
        seed_plan = self._validated_seed(seed_plan)
        game: Optional[Game] = None
        try:
            # Imported here because botbowl.api loads lab rule metadata while the
            # package root is initializing. The session still uses only #24.
            from botbowl.api import create_game

            game = create_game(
                config.game_config,
                config.home_team,
                config.away_team,
                control="external",
                size=config.size,
                seed=seed_plan,
            )
            timeline = Timeline(game, episode_id=seed_plan.episode_key)
            actions = ActionControl(game, timeline._entities)
            candidate = SimulationSession()
            candidate._game, candidate._timeline, candidate._actions = game, timeline, actions
            candidate._config, candidate._seed_plan = config, seed_plan
            candidate._revision = self._revision + 1
            result = candidate._result()
        except Exception as error:
            if game is not None:
                game.close()
            if isinstance(error, InvalidConfiguration):
                raise
            raise InvalidConfiguration(
                "Session configuration could not be initialized", _diagnostic(error)
            ) from error
        previous = self._game
        self._game = game
        self._timeline = timeline
        self._actions = actions
        self._config = config
        self._seed_plan = seed_plan
        self._accepted_decisions = 0
        self._truncation_reason = None
        self._revision += 1
        if previous is not None:
            previous.close()
        return result

    def _active_side(self) -> Optional[Side]:
        game, timeline, _, _ = self._current()
        if game.state.game_over or game.active_team is None:
            return None
        return timeline._entities._team(game.active_team)

    def _status(self) -> Tuple[bool, bool, Optional[str]]:
        game, _, _, config = self._current()
        terminated = bool(game.state.game_over)
        budget = (
            not terminated
            and self._accepted_decisions >= config.max_decisions
        )
        truncated = not terminated and (
            self._closed or self._truncation_reason is not None or budget
        )
        reason = None
        if terminated:
            reason = "game_over"
        elif self._closed:
            reason = "session_closed"
        elif self._truncation_reason is not None:
            reason = self._truncation_reason
        elif budget:
            reason = "decision_budget"
        return terminated, truncated, reason

    def _decision_id(self) -> Optional[str]:
        terminated, truncated, _ = self._status()
        if terminated or truncated or self._active_side() is None:
            return None
        _, timeline, _, _ = self._current()
        context = timeline.context
        return "%s:%s:session-%d" % (
            context.episode_id,
            context.branch_id,
            self._revision,
        )

    def _legal(self) -> LegalActionsV1:
        decision_id = self._decision_id()
        if decision_id is None:
            return LegalActionsV1(
                1, "boundary-%d" % self._revision, self._revision, None, [], []
            )
        _, _, actions, _ = self._current()
        current = actions.legal_actions()
        return replace(
            current,
            decision_id=decision_id,
            state_revision=self._revision,
            macros=[],  # This facade accepts exactly one primitive decision.
        )

    def _result(
        self,
        events: Tuple[Dict[str, Any], ...] = (),
        observer_team: Optional[Side] = None,
    ) -> SessionResult:
        terminated, truncated, reason = self._status()
        game, timeline, _, _ = self._current()
        next_actor = None if terminated or truncated else self._active_side()
        side = observer_team if observer_team is not None else (next_actor or "home")
        try:
            primary_data = observe(game, timeline._entities, side).to_json()
        except ValueError as error:
            raise InvalidAction(
                "observer_team must be 'home' or 'away'", _diagnostic(error)
            ) from error
        legal = self._legal()
        action_ids = [
            "%s:action:%d" % (legal.decision_id, index)
            for index in range(len(legal.actions))
        ]
        return SessionResult(
            primary=make_channel("primary", primary_data),
            derived=make_channel("derived", {}),
            control=make_channel(
                "control",
                {
                    "action_ids": action_ids,
                    "action_mask": [True] * len(action_ids),
                    "context": deepcopy(primary_data["decision"]),
                },
            ),
            events=deepcopy(tuple(events)),
            next_actor=next_actor,
            terminated=terminated,
            truncated=truncated,
            end_reason=reason,
            decision_id=self._decision_id(),
            state_revision=self._revision,
        )

    def observe(self, observer_team: Optional[Side] = None) -> SessionResult:
        """Return copied channel data without advancing state, clocks or RNG."""
        self._require_initialized()
        return self._result(observer_team=observer_team)

    def legal_actions(self, observer_team: Optional[Side] = None) -> LegalActionsV1:
        """Return copied semantic actions for the current decision."""
        self._require_initialized()
        if observer_team not in (None, "home", "away"):
            raise InvalidAction("observer_team must be 'home' or 'away'")
        return deepcopy(self._legal())

    @staticmethod
    def _action(value: Union[ActionV1, Dict[str, Any]]) -> ActionV1:
        try:
            data = value.to_json() if isinstance(value, ActionV1) else deepcopy(value)
            return ActionV1.from_json(data)
        except (AttributeError, TypeError, ValueError, SemanticActionError) as error:
            raise InvalidAction("Action data is invalid", _diagnostic(error)) from error

    def _check_revision(self, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision != self._revision:
            raise StaleRevision("Expected state revision is stale")

    def step(
        self,
        action: Union[ActionV1, Dict[str, Any]],
        expected_revision: int,
    ) -> SessionResult:
        """Accept one semantic decision and resolve to the next decision boundary."""
        self._require_mutable()
        self._check_revision(expected_revision)
        terminated, truncated, _ = self._status()
        if terminated or truncated:
            return self._result()
        semantic = self._action(action)
        game, timeline, actions, config = self._current()
        try:
            core = actions.decode(actions.request(semantic))
        except (InvalidActionError, SemanticActionError, TypeError, ValueError) as error:
            raise InvalidAction(
                "Action is not legal at this decision", _diagnostic(error)
            ) from error
        event_start = timeline.context.event_seq
        try:
            game.advance(core, max_steps=config.max_steps)
        except GameTruncatedError as error:
            self._accepted_decisions += 1
            self._truncation_reason = "execution_budget"
            self._revision += 1
            raise NoProgress("Engine execution budget was exhausted", _diagnostic(error)) from error
        except NoProgressError as error:
            self._accepted_decisions += 1
            self._truncation_reason = "no_progress"
            self._revision += 1
            raise NoProgress(
                "Engine could not reach another decision", _diagnostic(error)
            ) from error
        except InvalidActionError as error:
            raise InvalidAction(
                "Action is not legal at this decision", _diagnostic(error)
            ) from error
        except Exception as error:
            self._accepted_decisions += 1
            self._truncation_reason = "execution_failure"
            self._revision += 1
            raise ExecutionFailure("Engine execution failed", _diagnostic(error)) from error
        self._accepted_decisions += 1
        self._revision += 1
        events = tuple(
            event.to_json() for event in timeline.events[event_start:]
        )
        return self._result(events)

    def snapshot(self, scope: str = "engine") -> SessionSnapshot:
        """Capture privileged executable state; it is never a player channel."""
        if self._closed:
            raise SessionClosed("Session is closed")
        self._require_initialized()
        if scope != "engine":
            raise IncompatibleSnapshot("SimulationSession supports engine snapshots")
        game, _, _, config = self._current()
        try:
            engine = capture_snapshot(game, scope=scope)
        except (SnapshotError, TypeError, ValueError) as error:
            raise IncompatibleSnapshot(
                "Session snapshot could not be captured", _diagnostic(error)
            ) from error
        return SessionSnapshot(
            1,
            "engine",
            self._accepted_decisions,
            config.max_decisions,
            config.max_steps,
            self._truncation_reason,
            engine,
        )

    def restore(self, snapshot: SessionSnapshot, expected_revision: int) -> SessionResult:
        """Atomically restore compatible state while keeping revision monotonic."""
        self._require_mutable()
        self._check_revision(expected_revision)
        game, _, _, config = self._current()
        if (
            type(snapshot) is not SessionSnapshot
            or type(snapshot.schema_version) is not int or snapshot.schema_version != 1
            or snapshot.scope != "engine"
            or type(snapshot.accepted_decisions) is not int
            or type(snapshot.max_decisions) is not int
            or type(snapshot.max_steps) is not int
            or not 0 <= snapshot.accepted_decisions <= snapshot.max_decisions
            or snapshot.max_decisions != config.max_decisions
            or snapshot.max_steps != config.max_steps
            or snapshot.truncation_reason
            not in (None, "execution_budget", "no_progress", "execution_failure")
        ):
            raise IncompatibleSnapshot("Snapshot is incompatible with this session")
        try:
            # Validate facade-specific bindings and build the returned channels
            # on a detached graph before #37's atomic live-target commit.
            prepared = clone_from_snapshot(snapshot.engine)
            if type(prepared) is not Game or type(prepared.timeline) is not Timeline:
                raise SnapshotError("Snapshot has no compatible timeline")
            if snapshot.accepted_decisions != prepared.timeline.context.decision_seq:
                raise SnapshotError("Snapshot decision budget disagrees with its timeline")
            candidate = SimulationSession()
            candidate._game, candidate._timeline = prepared, prepared.timeline
            candidate._actions = ActionControl(prepared, prepared.timeline._entities)
            candidate._config = config
            candidate._accepted_decisions = snapshot.accepted_decisions
            candidate._truncation_reason = snapshot.truncation_reason
            candidate._revision = self._revision + 1
            result = candidate._result()
            restore_snapshot(game, snapshot.engine)
        except (SnapshotError, TypeError, ValueError) as error:
            raise IncompatibleSnapshot(
                "Snapshot is incompatible with this session", _diagnostic(error)
            ) from error
        self._timeline = game.timeline
        self._actions = ActionControl(game, self._timeline._entities)
        self._accepted_decisions = snapshot.accepted_decisions
        self._truncation_reason = snapshot.truncation_reason
        self._revision += 1
        return result

    def close(self) -> None:
        """Administratively close once; never invoke a policy or finish the match."""
        if self._closed:
            return
        self._closed = True
        if self._game is not None:
            self._game.close()
        self._revision += 1


__all__ = [
    "ExecutionFailure",
    "IncompatibleSnapshot",
    "InvalidAction",
    "InvalidConfiguration",
    "NoProgress",
    "SessionClosed",
    "SessionConfig",
    "SessionDiagnostic",
    "SessionError",
    "SessionResult",
    "SessionSnapshot",
    "SimulationSession",
    "StaleRevision",
]
