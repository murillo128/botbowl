"""Policy scheduling, separate from the engine's one-decision boundary."""
from copy import deepcopy
from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional, Tuple

from botbowl.core.model import Action


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
    continues until a missing policy or terminal. No agent lifecycle callbacks
    or competition clock enforcement are performed by this driver.
    """

    def __init__(self, game, policies):
        if not game.external_control:
            raise ValueError("PolicyDriver requires external_control=True")
        self.game = game
        self.policies = dict(policies)
        self.trace: List[DecisionTrace] = []

    def run(self, max_decisions: Optional[int] = None):
        from botbowl.core.game import DecisionResult

        if max_decisions is not None and (type(max_decisions) is not int or max_decisions < 0):
            raise ValueError("max_decisions must be a nonnegative integer or None")
        game = self.game
        result = DecisionResult(game.actor if not game.state.game_over else None, (), game.state.game_over)
        count = 0
        while not result.terminal and (max_decisions is None or count < max_decisions):
            if not game.state.available_actions:
                result = game.advance()
                continue
            team = game.active_team
            policy = self.policies.get(team.team_id)
            if policy is None:
                break
            actor_id = game.actor.agent_id
            choices = deepcopy(tuple(choice.to_json() for choice in game.state.available_actions))
            action = policy(game)
            # Normalize without mutating the policy's Action, before tracing it.
            action = game._validated_action(action)
            submitted = deepcopy(action.to_json())
            result = game.advance(action)
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

    def run(self, action: Optional[Action] = None) -> None:
        game = self.game
        while True:
            if game.config.fast_mode:
                result = game.advance(action)
            else:
                result = game._advance(action, single_step=True)
            if result.terminal or result.actor is None or result.actor.human:
                return
            game.last_request_time = time.time()
            game.action = game._safe_act()
            game.last_action_time = time.time()
            if game.config.competition_mode:
                game._check_clocks()
            if game.state.game_over:
                game._end_game()
                return
            action = game.action
