"""Typed in-memory doubles for every role; no engine or persistence implementation.

Run with: python examples/lab_protocols.py
No callbacks execute on import or construction. All calls below are explicit.
"""
from dataclasses import dataclass
from random import Random
from typing import Callable, List, Optional, Sequence, Tuple

from botbowl.lab import Evaluator, Observer, Policy, Recorder, Scenario, Simulation


@dataclass
class View:
    values: List[int]


@dataclass(frozen=True)
class Control:
    can_increment: bool


@dataclass(frozen=True)
class Increment:
    amount: int


@dataclass
class StepResult:
    values: List[int]


@dataclass(frozen=True)
class EvaluationContext:
    final_values: Tuple[int, ...]
    reference_total: int


class MemorySimulation:
    def __init__(self) -> None:
        self._values: List[int] = []
        self._rng = Random(0)
        self.closed = False

    def reset(self, seed: Optional[int] = None) -> None:
        self._rng.seed(seed)
        self._values = [self._rng.randrange(10)]
        self.closed = False

    def observe(self, control: Control) -> View:
        return View(self._values.copy())

    def legal_actions(self, control: Control) -> Sequence[Increment]:
        return [Increment(1)] if control.can_increment else []

    def step(self, action: Increment) -> StepResult:
        if self.closed:
            raise ValueError("simulation is closed")
        if action.amount != 1:
            raise ValueError("expected increment by one")
        self._values.append(action.amount)
        return StepResult(self._values.copy())

    def snapshot(self) -> Tuple[int, ...]:
        # This toy's only future-affecting state; no engine snapshot is provided.
        return tuple(self._values)

    def restore(self, snapshot: Tuple[int, ...]) -> None:
        self._values = list(snapshot)

    def close(self) -> None:
        self.closed = True


class IncrementPolicy:
    def act(self, observation: View, control: Control, rng: Random) -> Increment:
        if not observation.values or not control.can_increment:
            raise ValueError("no authorized increment")
        return Increment(rng.choice((1,)))


class CopyObserver:
    def transform(self, observation: View, rng: Random) -> View:
        values = observation.values.copy()
        rng.shuffle(values)
        return View(values)


class MemoryRecorder:
    def __init__(self) -> None:
        self.records: List[StepResult] = []
        self.flushed = False
        self.closed = False

    def append(self, record: StepResult) -> None:
        if self.closed:
            raise ValueError("recorder is closed")
        self.records.append(StepResult(record.values.copy()))

    def flush(self) -> None:
        self.flushed = True

    def close(self) -> None:
        self.closed = True


class CounterScenario:
    def build(self, factory: Callable[[Optional[int]], MemorySimulation],
              seed: Optional[int]) -> MemorySimulation:
        return factory(seed)


class TotalEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, context: EvaluationContext) -> int:
        self.calls += 1
        return sum(context.final_values) - context.reference_total


def make_simulation(seed: Optional[int]) -> MemorySimulation:
    simulation = MemorySimulation()
    simulation.reset(seed)
    return simulation


def run() -> int:
    # Assign concrete classes structurally: none inherits a protocol or Game.
    scenario: Scenario[Callable[[Optional[int]], MemorySimulation], MemorySimulation] = CounterScenario()
    simulation: Simulation[View, Control, Increment, StepResult, Tuple[int, ...]] = scenario.build(
        make_simulation, 17)
    policy: Policy[View, Control, Random, Increment] = IncrementPolicy()
    observer: Observer[View, Random, View] = CopyObserver()
    recorder: Recorder[StepResult] = MemoryRecorder()
    evaluator: Evaluator[EvaluationContext, int] = TotalEvaluator()
    control = Control(can_increment=True)
    # Independent caller-owned streams; neither is simulation._rng.
    policy_rng, observer_rng = Random(23), Random(29)
    try:
        checkpoint = simulation.snapshot()  # Remains in controller code.
        observation = observer.transform(simulation.observe(control), observer_rng)
        action = policy.act(observation, control, policy_rng)
        assert action in simulation.legal_actions(control)
        result = simulation.step(action)
        recorder.append(result)
        recorder.flush()
        # Evaluation context/targets never enter policy or observer calls.
        context = EvaluationContext(tuple(result.values), sum(checkpoint))
        score = evaluator.evaluate(context)
        simulation.restore(checkpoint)
        return score
    finally:
        recorder.close()
        simulation.close()


if __name__ == "__main__":
    assert run() == 1
    print("lab protocols: all six structural roles executed explicitly")
