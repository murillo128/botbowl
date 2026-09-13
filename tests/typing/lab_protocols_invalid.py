"""Each assignment rejects a wrong role signature, including the legacy adapter."""
from random import Random
from typing import Callable, Optional, Tuple

from botbowl.core.driver import Policy as LegacyPolicy
from botbowl.lab import Evaluator, LegacyBotAdapter, Observer, Policy, Recorder, Scenario, Simulation
from examples.lab_protocols import (Control, EvaluationContext, Increment, MemorySimulation,
                                   StepResult, View)


class WrongSimulation:
    def reset(self, seed: Optional[int] = None) -> None:
        pass

    def observe(self, control: Control) -> View:
        return View([])

    def legal_actions(self, control: Control) -> Tuple[Increment, ...]:
        return ()

    def step(self, action: str) -> StepResult:
        return StepResult([])

    def snapshot(self) -> Tuple[int, ...]:
        return ()

    def restore(self, snapshot: Tuple[int, ...]) -> None:
        pass

    def close(self) -> None:
        pass


class WrongPolicy:
    def act(self, observation: View, control: Control, rng: Random) -> str:
        return "not an action"


class WrongObserver:
    def transform(self, observation: View) -> View:
        return observation


class WrongRecorder:
    def append(self, record: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class WrongScenario:
    def build(self, factory: Callable[[Optional[int]], MemorySimulation], seed: str) -> MemorySimulation:
        return factory(None)


class WrongEvaluator:
    def evaluate(self, context: View) -> int:
        return 0


simulation: Simulation[View, Control, Increment, StepResult, Tuple[int, ...]] = WrongSimulation()  # expect: assignment
policy: Policy[View, Control, Random, Increment] = WrongPolicy()  # expect: assignment
observer: Observer[View, Random, View] = WrongObserver()  # expect: assignment
recorder: Recorder[StepResult] = WrongRecorder()  # expect: assignment
scenario: Scenario[Callable[[Optional[int]], MemorySimulation], MemorySimulation] = WrongScenario()  # expect: assignment
evaluator: Evaluator[EvaluationContext, int] = WrongEvaluator()  # expect: assignment


def incompatible_legacy(adapter: LegacyBotAdapter, restricted: Policy[View, Control, Random, Increment]) -> None:
    policy: Policy[View, Control, Random, Increment] = adapter  # expect: assignment
    legacy: LegacyPolicy = restricted  # expect: assignment
