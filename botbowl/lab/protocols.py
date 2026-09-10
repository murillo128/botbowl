"""Structural lab boundaries, independent of engine and data implementations.

Inputs are borrowed for a call; implementations must not mutate them, except
for the explicitly supplied callback RNG. Retained mutable inputs must be
copied. Outputs are caller-owned detached values (or deeply immutable values),
never borrowed mutable Game objects. These are contracts, not a Python sandbox.
See docs/lab/protocols.md for authority, lifecycle and snapshot boundaries.
"""
from typing import Optional, Protocol, Sequence, TypeVar


Observation_co = TypeVar("Observation_co", covariant=True)
Observation_contra = TypeVar("Observation_contra", contravariant=True)
Control_contra = TypeVar("Control_contra", contravariant=True)
Action = TypeVar("Action")
Action_co = TypeVar("Action_co", covariant=True)
Result_co = TypeVar("Result_co", covariant=True)
Snapshot = TypeVar("Snapshot")
RNG_contra = TypeVar("RNG_contra", contravariant=True)
Transformed_co = TypeVar("Transformed_co", covariant=True)
Record_contra = TypeVar("Record_contra", contravariant=True)
Factory_contra = TypeVar("Factory_contra", contravariant=True)
Built_co = TypeVar("Built_co", covariant=True)
Context_contra = TypeVar("Context_contra", contravariant=True)
Evaluation_co = TypeVar("Evaluation_co", covariant=True)


class Simulation(Protocol[Observation_co, Control_contra, Action, Result_co, Snapshot]):
    """Controller-owned simulation; never handed to restricted callbacks.

Control identifies the authorized view/decision scope, not an engine handle.
Snapshot/restore are privileged controller operations, outside policy inputs.
Concrete implementations own seed validation, step results and snapshot format.
"""

    def reset(self, seed: Optional[int] = None) -> None:
        """Reset owned state; obtain the authorized view separately with observe."""
        ...

    def observe(self, control: Control_contra, /) -> Observation_co:
        """Return a detached view authorized by control, without advancing."""
        ...

    def legal_actions(self, control: Control_contra, /) -> Sequence[Action]:
        """Return detached actions for the authorized decision, without advancing."""
        ...

    def step(self, action: Action, /) -> Result_co:
        """Apply one supplied action; copy mutable input if retaining it."""
        ...

    def snapshot(self) -> Snapshot:
        """Return detached restoration data; it may contain privileged state."""
        ...

    def restore(self, snapshot: Snapshot, /) -> None:
        """Restore compatible owned state without borrowing mutable input."""
        ...

    def close(self) -> None:
        """Release owned resources; no implicit policy/evaluator/recorder calls."""
        ...


class Policy(Protocol[Observation_contra, Control_contra, RNG_contra, Action_co]):
    """Choose from authorized observation/control using a dedicated callback RNG."""

    def act(self, observation: Observation_contra, control: Control_contra,
            rng: RNG_contra, /) -> Action_co:
        """Return a detached action; no Game, simulation or engine RNG is granted."""
        ...


class Observer(Protocol[Observation_contra, RNG_contra, Transformed_co]):
    """Transform an already authorized view, with no extra observation authority."""

    def transform(self, observation: Observation_contra, rng: RNG_contra, /) -> Transformed_co:
        """Return detached derived data using only the supplied view and RNG."""
        ...


class Recorder(Protocol[Record_contra]):
    """Caller-owned record sink; retaining mutable records requires a copy."""

    def append(self, record: Record_contra, /) -> None:
        """Accept one explicitly supplied record, without mutating it."""
        ...

    def flush(self) -> None:
        """Explicitly flush pending records according to the sink's contract."""
        ...

    def close(self) -> None:
        """Explicitly release sink resources; concrete sinks define flush policy."""
        ...


class Scenario(Protocol[Factory_contra, Built_co]):
    """Trusted construction callback using a factory supplied directly by code."""

    def build(self, factory: Factory_contra, seed: Optional[int], /) -> Built_co:
        """Build a caller-owned instance; no factory discovery or config execution."""
        ...


class Evaluator(Protocol[Context_contra, Evaluation_co]):
    """Explicit evaluation over a separate context, outside training inputs."""

    def evaluate(self, context: Context_contra, /) -> Evaluation_co:
        """Return detached evaluation data; never installed as a training callback."""
        ...


__all__ = ["Simulation", "Policy", "Observer", "Recorder", "Scenario", "Evaluator"]
