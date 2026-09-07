"""Check variance and compatibility with the existing engine-aware callable."""
from random import Random
from typing import Tuple

from botbowl import Agent
from botbowl.core.driver import Policy as LegacyPolicy
from botbowl.lab import Evaluator, LegacyBotAdapter, Observer, Policy, Recorder, Simulation
from examples.lab_protocols import (Control, EvaluationContext, Increment, IncrementPolicy,
                                   MemorySimulation, StepResult, View)


class AnyInput:
    def act(self, observation: object, control: object, rng: object) -> Increment:
        return Increment(1)

    def transform(self, observation: object, rng: object) -> View:
        return View([])

    def append(self, record: object) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def evaluate(self, context: object) -> int:
        return 0


def boundaries(bot: Agent) -> None:
    legacy: LegacyPolicy = LegacyBotAdapter(bot)
    policy: Policy[View, Control, Random, Increment] = AnyInput()
    observer: Observer[View, Random, View] = AnyInput()
    recorder: Recorder[StepResult] = AnyInput()
    evaluator: Evaluator[EvaluationContext, int] = AnyInput()
    wide_result: Policy[View, Control, Random, object] = IncrementPolicy()
    simulation: Simulation[object, Control, Increment, object, Tuple[int, ...]] = MemorySimulation()
