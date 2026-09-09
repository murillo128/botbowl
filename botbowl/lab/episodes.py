"""Bounded lab composition over Game, its checkpoint, and accepted lab views.

The controller owns this context. Only observe()/decision_control() outputs
and dedicated callback streams go to policies. See docs/lab/randomness.md.
"""
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
import platform
import json

import numpy as np

from botbowl.core.game import Game, GameCheckpoint, InvalidActionError
from botbowl.core.model import Action, Agent, Square
from botbowl.core.table import ActionType
from .chance import ChancePolicy, install_chance
from .observations import ObservationControl, observe
from .randomness import (DERIVATION_ALGORITHM, GENERATOR, PURPOSES, SeedSpec,
                         _canonical, _identifier, capture_stream)
from .rules import describe_rules


def _snapshot_idle(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        self._snapshot_busy += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._snapshot_busy -= 1
    return guarded


class EpisodeCompatibilityError(ValueError):
    """Replay/checkpoint provenance does not match this episode."""

    code = "episode_incompatible"


@dataclass(frozen=True)
class EpisodeResult:
    events: tuple
    terminal: bool
    truncated: bool
    decisions: int
    chance: dict


@dataclass(frozen=True)
class EpisodeCheckpoint:
    """Privileged, trusted in-memory ancestor; NOT a portable snapshot format."""

    game: GameCheckpoint
    streams: tuple
    decisions: int
    provenance: str


class EpisodeContext:
    """Own five declared sources and a fresh, externally controlled Game.

    Inputs are the loaded (config, ruleset, arena, home, away) used by #30.
    A trusted scenario callback may modify their private copies using its RNG
    before identity capture. Policies implement lab.Policy; they must use only
    their supplied RNG and views. Stateful callbacks remain caller-owned.
    """

    def __init__(self, config, ruleset, arena, home_team, away_team, *,
                 episode_key, master_seed=None, derivation_version=1,
                 component_ids=None, max_decisions=1000, max_steps=100000,
                 scenario=None, policies=None, chance_policy=None):
        self._seed = SeedSpec(master_seed, episode_key, derivation_version=derivation_version)
        if type(max_decisions) is not int or max_decisions < 0:
            raise ValueError("max_decisions must be a nonnegative integer")
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        ids = dict.fromkeys(PURPOSES, "default") if component_ids is None else component_ids
        if type(ids) is not dict or set(ids) != set(PURPOSES):
            raise ValueError("Declare exactly the five randomness component IDs")
        self._ids = {purpose: _identifier(ids[purpose]) for purpose in PURPOSES}
        self._policies = {} if policies is None else dict(policies)
        if set(self._policies) - {"home", "away"}:
            raise ValueError("Policy seats must be home or away")
        for policy in self._policies.values():
            if getattr(policy, "requires_game_access", False) or not callable(getattr(policy, "act", None)):
                raise ValueError("EpisodeContext requires restricted lab policies")
        if scenario is not None and not callable(scenario):
            raise ValueError("scenario must be a trusted callable or None")
        self._inputs = deepcopy((config, ruleset, arena, home_team, away_team))
        policy = ChancePolicy() if chance_policy is None else chance_policy
        if type(policy) is not ChancePolicy:
            raise ValueError("Expected ChancePolicy")
        self._chance_spec = _canonical(policy.to_json())
        self._scenario = scenario
        self._max_decisions = max_decisions
        self._max_steps = max_steps
        self.game = None
        self._snapshot_busy = 0
        self.reset()

    @property
    def master_seed(self):
        return self._seed.master_seed

    @_snapshot_idle
    def reset(self):
        """Rebuild from the recorded seed and copied initial inputs.

        Construct first so a scenario exception leaves the current episode
        intact. Callback-internal state is not reset or rolled back.
        """
        specs = {purpose: SeedSpec(self.master_seed, self._seed.episode_key, purpose,
                                  self._ids[purpose], self._seed.derivation_version)
                 for purpose in PURPOSES}
        streams = {purpose: spec.generator() for purpose, spec in specs.items()
                   if purpose != "engine"}
        inputs = deepcopy(self._inputs)
        if self._scenario is not None:
            self._scenario(inputs, streams["scenario"])
        descriptor = describe_rules(*inputs)
        config, ruleset, arena, home, away = inputs
        # Keep configured identity; advance() already ignores competitive clocks.
        game = Game(self._seed.episode_key, home, away, Agent("home", human=True),
                    Agent("away", human=True), config, arena=arena, ruleset=ruleset,
                    seed=specs["engine"].seed_words(), external_control=True)
        install_chance(game, ChancePolicy.from_json(json.loads(self._chance_spec)))
        game.init()
        control = ObservationControl(game)
        game.enable_forward_model()
        manifest = {
            "manifest_version": 1, "rules": descriptor.to_json(),
            "chance": game.dice.chance.metadata(),
            "derivation_algorithm": DERIVATION_ALGORITHM,
            "generator": {"id": GENERATOR, "numpy_version": np.__version__,
                          "python_version": platform.python_version()},
            "sources": {purpose: {**spec.to_json(), "seed_words": list(spec.seed_words())}
                        for purpose, spec in specs.items()},
            "policies": {side: {"component_id": self._ids["policy-" + side],
                                "mode": "restricted" if side in self._policies else "supplied-actions"}
                         for side in ("home", "away")},
            "scenario_mode": "callback" if self._scenario is not None else "loaded-inputs",
            "clock_mode": "decision-budget", "max_decisions": self._max_decisions,
            "max_steps_per_decision": self._max_steps,
        }
        if self.game is not None:
            self.game.close()
        self.game, self._control, self._streams = game, control, streams
        self._initial_teams = deepcopy((home, away))
        self._manifest = manifest
        self.decisions = 0

    def manifest(self):
        """Allowlisted provenance, without advanced RNG state or forced queues."""
        return deepcopy(self._manifest)

    def require_compatible(self, manifest):
        """Reject any recipe/config/backend/version/mode mismatch before replay."""
        try:
            matches = _canonical(manifest) == _canonical(self._manifest)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise EpisodeCompatibilityError("Incompatible episode provenance")
        self._check_runtime()

    def _check_runtime(self):
        # Reuse #30 with pristine rosters: live injuries/resources are state,
        # while config, rule tables and the actual bound backend must still match.
        try:
            current = describe_rules(self.game.config, self.game.ruleset, self.game.arena,
                                     *self._initial_teams)
            if current.to_json() != self._manifest["rules"]:
                raise EpisodeCompatibilityError("Episode configuration or backend changed")
        except ValueError as error:
            raise EpisodeCompatibilityError("Episode configuration or backend changed") from error

    @property
    def truncated(self):
        return not self.game.state.game_over and self.decisions >= self._max_decisions

    def observe(self, side):
        return observe(self.game, self._control, side).to_json()

    @_snapshot_idle
    def transform(self, observer, side):
        """Invoke only with the public view and the observation-owned stream."""
        return observer.transform(self.observe(side), self._streams["observation"])

    def decision_control(self):
        """Detached choice data using stable roster IDs, in engine choice order.

        Choices describe candidates, not a new exhaustive legality API. The
        engine validates the supplied action, including setup restrictions.
        """
        choices = []
        for choice in self.game.get_available_actions():
            data = deepcopy(choice.to_json())
            data["team_id"] = self._control._team(choice.team)
            data["player_ids"] = [self._control._player(player) for player in choice.players]
            choices.append(data)
        return {"team": self._control._team(self.game.active_team), "choices": choices}

    def _decode(self, data):
        if (type(data) is not dict or "action_type" not in data or
                set(data) - {"action_type", "position", "player_id"}):
            raise InvalidActionError("Expected detached lab action data", code="lab_action")
        try:
            if type(data["action_type"]) is not str:
                raise ValueError()
            kind = ActionType[data["action_type"]]
            position = data.get("position")
            if position is not None:
                if (type(position) is not dict or set(position) != {"x", "y"} or
                        any(type(position[key]) is not int for key in ("x", "y"))):
                    raise ValueError()
                position = Square(position["x"], position["y"])
            local_id = data.get("player_id")
            player = None if local_id is None else self.game.get_player(
                self._control.internal_player_id(local_id))
            return Action(kind, position=position, player=player)
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidActionError("Malformed lab action", code="lab_action") from error

    def _result(self, events=()):
        if self.game.state.game_over or self.truncated:
            self.game.dice.chance.finish()
        return EpisodeResult(tuple(events), self.game.state.game_over, self.truncated, self.decisions,
                             self.game.dice.chance.metadata())

    @_snapshot_idle
    def step(self, action):
        """Apply one supplied detached action; retain #16 engine error semantics.

        At the decision limit return truncation without consuming the action.
        Invalid actions do not spend a decision; automatic-step exhaustion can
        leave the game advanced and propagates GameTruncatedError unchanged.
        """
        if self.game.closed:
            raise InvalidActionError("Game is closed", code="game_closed")
        if self.truncated or self.game.state.game_over:
            return self._result()
        result = self.game.advance(self._decode(action), max_steps=self._max_steps)
        self.decisions += 1
        events = []
        for event in result.events:
            events.append({"event": event.outcome_type.name,
                           "player": self._control._player(event.player),
                           "opponent": self._control._player(event.opp_player),
                           "team": self._control._team(event.team),
                           "position": None if event.position is None else event.position.to_json(),
                           "rolls": deepcopy([roll.to_json() for roll in event.rolls]),
                           "n": event.n, "skill": None if event.skill is None else event.skill.name})
        return self._result(events)

    @_snapshot_idle
    def act(self):
        """Call the declared active policy with no engine/context/engine-RNG alias."""
        if self.game.closed:
            raise InvalidActionError("Game is closed", code="game_closed")
        if self.truncated or self.game.state.game_over:
            return self._result()
        side = self._control._team(self.game.active_team)
        if side not in self._policies:
            raise ValueError("No restricted policy declared for this decision")
        action = self._policies[side].act(self.observe(side), self.decision_control(),
                                          self._streams["policy-" + side])
        return self.step(action)

    def replay(self, actions, manifest):
        """Check provenance, then replay a supplied prefix on a fresh/reset context."""
        self.require_compatible(manifest)
        if self.decisions or self.game.get_step():
            raise EpisodeCompatibilityError("Replay requires a fresh or reset episode")
        results = []
        iterator = iter(actions)
        while not self.truncated and not self.game.state.game_over:
            try:
                action = next(iterator)
            except StopIteration:
                break
            results.append(self.step(action))
        return results

    def capture_checkpoint(self):
        """Privileged channel ONLY: reuse #14's live ancestor and all dice queues."""
        self._check_runtime()
        return EpisodeCheckpoint(self.game.capture_checkpoint(),
                                 tuple((name, capture_stream(rng)) for name, rng in self._streams.items()),
                                 self.decisions, _canonical(self._manifest))

    def restore_checkpoint(self, checkpoint):
        """Restore game and owned streams; never restore callback-internal state.

        All stream data is prepared before #14 validates/restores its ancestor,
        so rejected foreign/expired checkpoints do not partly rewind streams.
        """
        if (type(checkpoint) is not EpisodeCheckpoint or
                checkpoint.provenance != _canonical(self._manifest)):
            raise EpisodeCompatibilityError("Incompatible episode checkpoint")
        self._check_runtime()
        prepared = {}
        try:
            if (type(checkpoint.decisions) is not int or
                    not 0 <= checkpoint.decisions <= self._max_decisions or
                    tuple(name for name, _ in checkpoint.streams) != tuple(self._streams)):
                raise ValueError()
            for name, state in checkpoint.streams:
                rng = np.random.RandomState(0)
                rng.set_state(state)
                prepared[name] = rng.get_state()
        except (TypeError, ValueError) as error:
            raise EpisodeCompatibilityError("Malformed episode stream checkpoint") from error
        self.game.restore_checkpoint(checkpoint.game)
        for name, state in prepared.items():
            self._streams[name].set_state(state)
        self.decisions = checkpoint.decisions

    def close(self):
        self.game.close()
