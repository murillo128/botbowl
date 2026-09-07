"""Policy scheduling, separate from the engine's one-decision boundary."""
from copy import deepcopy
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Protocol, Tuple, Union

from botbowl.core.model import Action

if TYPE_CHECKING:
    from botbowl.core.game import DecisionResult, Game, StepBudget


class Policy(Protocol):
    """The existing PolicyDriver callable boundary; lifecycle remains with Game."""

    def __call__(self, game: "Game", /) -> Action: ...


@dataclass(frozen=True)
class DecisionTrace:
    """Detached JSON snapshots of a submitted decision and its consequences."""

    actor_id: Optional[str]
    team_id: Optional[str]
    choices: Tuple[Dict[str, Any], ...]
    action: Dict[str, Any]
    events: Tuple[Dict[str, Any], ...]
    next_actor_id: Optional[str]
    terminal: bool


class PolicyDriver:
    """Drive initialized external games with team-ID -> callable policies.

    Each callable receives the game and returns one Action. Missing policies
    pause the driver. run(max_decisions=N) bounds accepted decisions; run()
    continues until a missing policy, terminal or max_steps exhaustion. No agent lifecycle callbacks
    or competition clock enforcement are performed by this driver.
    """

    def __init__(self, game: "Game", policies: Mapping[str, Policy]) -> None:
        if not game.external_control:
            raise ValueError("PolicyDriver requires external_control=True")
        self.game = game
        self.policies = dict(policies)
        self.trace: List[DecisionTrace] = []

    def run(self, max_decisions: Optional[int] = None, *,
            max_steps: Union[int, "StepBudget"] = 100000) -> "DecisionResult":
        from botbowl.core.game import DecisionResult, _step_budget

        if max_decisions is not None and (type(max_decisions) is not int or max_decisions < 0):
            raise ValueError("max_decisions must be a nonnegative integer or None")
        game = self.game
        if game.closed:
            from botbowl.core.game import InvalidActionError
            raise InvalidActionError("Game is closed", code="game_closed")
        budget = _step_budget(max_steps)
        result = DecisionResult(game.actor if not game.state.game_over else None, (), game.state.game_over)
        count = 0
        while not result.terminal and (max_decisions is None or count < max_decisions):
            if not game.state.available_actions:
                result = game.advance(max_steps=budget)
                continue
            team = game.active_team
            policy = self.policies.get(team.team_id)
            if policy is None:
                break
            actor_id = game.actor.agent_id
            choices = deepcopy(tuple(choice.to_json() for choice in game.state.available_actions))
            budget.consume(game)
            action = policy(game)
            # Normalize without mutating the policy's Action, before tracing it.
            action = game._validated_action(action)
            submitted = deepcopy(action.to_json())
            result = game.advance(action, max_steps=budget)
            self.trace.append(DecisionTrace(
                actor_id, team.team_id, choices, submitted,
                deepcopy(tuple(event.to_json() for event in result.events)),
                result.actor.agent_id if result.actor is not None else None, result.terminal))
            count += 1
        return result


class LegacyPolicyDriver:
    """Compatibility scheduler used by Game.init/step by default.

    Keep human pauses, slow-mode ticks and competition clock handling here.
    Explicit external policies use PolicyDriver instead.
    """

    def __init__(self, game):
        self.game = game

    def run(self, action: Optional[Action] = None, *, max_steps=100000) -> None:
        from botbowl.core.game import _step_budget

        game = self.game
        budget = _step_budget(max_steps)
        while True:
            if game.config.fast_mode:
                result = game.advance(action, max_steps=budget)
            else:
                result = game._advance(action, single_step=True, budget=budget)
            if result.terminal or result.actor is None or result.actor.human:
                return
            while True:
                if game.config.competition_mode:
                    game._check_clocks(max_steps=budget)
                if game.state.game_over or game.actor is None or game.actor.human:
                    return
                budget.consume(game)
                game.last_request_time = time.time()
                try:
                    action = game._safe_act()
                finally:
                    game.last_action_time = time.time()
                if not game.config.competition_mode or not game._check_clocks(max_steps=budget):
                    break
                # The late action belongs to the old boundary. Ask the new owner.
