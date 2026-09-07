"""Version 5: one external engine decision per Gymnasium step.

Import this module (or call register_gymnasium_envs) before gymnasium.make.
Legacy Gym IDs and wrappers remain in botbowl.ai.env.
"""
from copy import deepcopy
from itertools import product

import gymnasium as gym
import numpy as np

from botbowl.api import create_game
from botbowl.ai.env_conf import EnvConf
from botbowl.ai.layers import AvailablePositionLayer
from botbowl.core import (Action, ActionType, GameTruncatedError, PlayerActionType,
                          StepBudget, WeatherType, load_arena, load_rule_set,
                          load_team_by_filename)


class GymnasiumEnv(gym.Env):
    """Control both seats; use SinglePlayerWrapper for an explicit opponent.

    Scalar reward belongs to the seat submitting the decision. info['rewards']
    always contains both seats. The default reward is the team's score delta.
    """
    metadata = {"render_modes": ["ansi"], "render_fps": 1}

    def __init__(self, size=11, *, config=None, pathfinding=False,
                 max_decisions=10000, max_steps=100000, render_mode=None):
        super().__init__()
        if render_mode not in (None, "ansi"):
            raise ValueError("render_mode must be None or 'ansi'")
        for name, value in (("max_decisions", max_decisions), ("max_steps", max_steps)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.env_conf = EnvConf(size=size, pathfinding=pathfinding)
        if config is not None:
            self.env_conf.config = deepcopy(config)
        if self.env_conf.config.pitch_max != size:
            raise ValueError("config and size must agree")
        self.render_mode = render_mode
        self.max_decisions = max_decisions
        self.max_steps = max_steps
        arena = load_arena(self.env_conf.config.arena)
        self.width, self.height = arena.width, arena.height
        self.board_squares = self.width * self.height
        rules = load_rule_set(self.env_conf.config.ruleset)
        self.home_team = load_team_by_filename('human', rules, board_size=size)
        self.away_team = load_team_by_filename('human', rules, board_size=size)
        self.player_slots = max(len(self.home_team.players), len(self.away_team.players))
        # Stable enum declaration order; aliases keep their engine identity.
        self.action_types = tuple(t for t in ActionType if t is not ActionType.CONTINUE)
        self.action_stride = 1 + self.board_squares + 2 * self.player_slots
        self.placement_offset = len(self.action_types) * self.action_stride
        self.action_space = gym.spaces.Discrete(
            self.placement_offset + 2 * self.player_slots * (self.board_squares + 1))
        self.num_non_spatial_observables = 52 + len(self.env_conf.procedures) + len(self.action_types)
        # These are normalized features, not probabilities. Rerolls, movement,
        # attributes and extra resources can exceed 1. Never clip valid values.
        limit = np.finfo(np.float32).max
        self.observation_space = gym.spaces.Dict({
            "spatial": gym.spaces.Box(-limit, limit,
                                      (len(self.env_conf.layers), self.height, self.width), np.float32),
            "non_spatial": gym.spaces.Box(-limit, limit,
                                          (self.num_non_spatial_observables,), np.float32),
            "action_mask": gym.spaces.MultiBinary(int(self.action_space.n)),
        })
        self.game = None
        self._closed = False
        self._terminated = self._truncated = False

    def _seat(self, team):
        if team is None:
            return None
        return "home" if team is self.game.state.home_team else "away"

    def _players(self, flip):
        teams = (self.game.state.away_team, self.game.state.home_team) if flip else self.game.state.teams
        return [p for team in teams for p in list(team.players) + [None] * (self.player_slots - len(team.players))]

    def _flip(self):
        return self._seat(self.game.active_team) == "away"

    def encode_action(self, action, *, flip=None):
        """Encode a canonical Action; legality is checked separately by step."""
        flip = self._flip() if flip is None else flip
        # Preserve the engine's accepted player/square shorthand. Encode only
        # the target required by the matching choice (e.g. BLOCK(player=...)).
        if self.game.is_action_allowed(action):
            normalized = self.game._validated_action(action)
            if normalized is None:
                raise ValueError("Automatic CONTINUE has no decision index")
            for choice in self.game.get_available_actions():
                if choice.disabled or choice.action_type is not action.action_type:
                    continue
                if choice.players and normalized.player not in choice.players:
                    continue
                if choice.positions and normalized.position not in choice.positions:
                    continue
                action = Action(action.action_type,
                                player=normalized.player if choice.players else None,
                                position=normalized.position if choice.positions else None)
                break
        position, player = action.position, action.player
        square = 0
        if position is not None:
            if not (0 <= position.x < self.width and 0 <= position.y < self.height):
                raise ValueError("position outside the board")
            x = self.width - 1 - position.x if flip else position.x
            square = 1 + position.y * self.width + x
        players = self._players(flip)
        player_index = None
        if player is not None:
            player_index = next((i for i, p in enumerate(players)
                                 if p is not None and p.player_id == player.player_id), None)
            if player_index is None:
                raise ValueError("player outside this game's roster")
        if action.action_type is ActionType.PLACE_PLAYER:
            if player_index is None:
                raise ValueError("PLACE_PLAYER requires a player")
            return self.placement_offset + player_index * (self.board_squares + 1) + square
        if player is not None and position is not None:
            raise ValueError("Only PLACE_PLAYER uses a player and square together")
        target = square if player is None else 1 + self.board_squares + player_index
        return self.action_types.index(action.action_type) * self.action_stride + target

    def decode_action(self, index, *, flip=None):
        """Decode a fixed index, without mutating the game or testing legality."""
        if isinstance(index, (bool, np.bool_)) or not self.action_space.contains(index):
            raise ValueError("action index outside Discrete space")
        index = int(index)
        flip = self._flip() if flip is None else flip
        player = None
        if index >= self.placement_offset:
            player_index, square = divmod(index - self.placement_offset, self.board_squares + 1)
            action_type = ActionType.PLACE_PLAYER
            player = self._players(flip)[player_index]
        else:
            type_index, target = divmod(index, self.action_stride)
            action_type = self.action_types[type_index]
            if target > self.board_squares:
                player = self._players(flip)[target - self.board_squares - 1]
                square = 0
            else:
                square = target
        position = None
        if square:
            y, x = divmod(square - 1, self.width)
            if flip:
                x = self.width - 1 - x
            position = self.game.get_square(x, y)
        return Action(action_type, player=player, position=position)

    def _legal_actions(self):
        legal = {}
        if self._terminated or self._truncated or self.game.state.game_over or self._closed:
            return legal
        for choice in self.game.get_available_actions():
            if choice.disabled:
                continue
            if choice.action_type is ActionType.END_SETUP and not self.game.is_setup_legal(choice.team):
                continue
            for player, position in product(choice.players or [None], choice.positions or [None]):
                action = Action(choice.action_type, player=player, position=position)
                if self.game.is_action_allowed(action):
                    legal[self.encode_action(action)] = action
        if not legal:
            raise RuntimeError("Nonterminal decision has no encodable legal actions")
        return legal

    def get_state(self):
        if self.game is None:
            raise gym.error.ResetNeeded("Call reset before observing")
        game = self.game
        actor = game.active_team
        # No actor means no own/opponent perspective. Retain neutral board data
        # and fixed home/away team statistics, with two explicit actor flags.
        spatial = []
        actor_layers = {"own players", "opp players", "own tackle zones", "opp tackle zones",
                        "own half", "own touchdown", "opp touchdown"}
        for layer in self.env_conf.layers:
            if actor is None and layer.name() in actor_layers:
                value = np.zeros((self.height, self.width))
            elif isinstance(layer, AvailablePositionLayer):
                value = np.zeros((self.height, self.width))
                for choice in game.get_available_actions():
                    if choice.disabled or choice.action_type is not layer.action_type:
                        continue
                    for position in choice.positions:
                        if position is not None:
                            value[position.y, position.x] = 1
                    if not choice.positions:
                        for player in choice.players:
                            if player.position is not None:
                                value[player.position.y, player.position.x] = 1
            else:
                value = layer.get(game)
            spatial.append(value)
        spatial = np.asarray(spatial, dtype=np.float32)
        if self._flip():
            spatial = spatial[:, :, ::-1].copy()
        own = actor if actor is not None else game.state.home_team
        opp = game.get_opp_team(own)
        values = [game.state.half - 1, game.state.round / 8]
        values += [float(game.state.weather is w) for w in WeatherType]
        values += [float(actor is not None and t is actor) for t in
                   (game.state.current_team, game.state.kicking_first_half, game.state.kicking_this_drive)]
        for team in (own, opp):
            values += [len(f(team)) / 16 for f in (game.get_reserves, game.get_knocked_out, game.get_casualties)]
        for team in (own, opp):
            s = team.state
            values += [s.score / 16, s.turn / 8, s.rerolls_start / 8, s.rerolls / 8,
                       s.ass_coaches / 8, s.cheerleaders / 8, s.bribes / 4, s.babes / 4,
                       s.apothecaries / 2, float(not s.reroll_used), s.fame / 2]
        values += ([float(f()) for f in (game.is_blitz_available, game.is_pass_available,
                    game.is_handoff_available, game.is_foul_available, game.is_blitz, game.is_quick_snap)]
                   if game.current_turn() is not None else [0.] * 6)
        player_action = game.get_player_action_type() if game.state.active_player is not None else None
        values += [float(player_action is t) for t in (PlayerActionType.MOVE, PlayerActionType.BLOCK,
                   PlayerActionType.BLITZ, PlayerActionType.PASS, PlayerActionType.HANDOFF, PlayerActionType.FOUL)]
        values += [float(actor is team) for team in game.state.teams]
        proc_types = {type(p) for p in game.state.stack.items}
        values += [float(p in proc_types) for p in self.env_conf.procedures]
        legal = self._legal_actions()
        offered = {a.action_type for a in legal.values()}
        values += [float(t in offered) for t in self.action_types]
        mask = np.zeros(self.action_space.n, dtype=np.int8)
        mask[list(legal)] = 1
        return {"spatial": spatial, "non_spatial": np.asarray(values, dtype=np.float32), "action_mask": mask}

    def _info(self, acting_team=None, events=(), rewards=None):
        return {"acting_team": acting_team, "next_team": self._seat(self.game.active_team),
                "rewards": rewards or {"home": 0., "away": 0.},
                "events": tuple(deepcopy(event.to_json()) for event in events),
                "truncation_reason": self._truncation_reason}

    def reset(self, *, seed=None, options=None):
        if options is not None and (not isinstance(options, dict) or options):
            raise ValueError("No reset options are supported; configure the environment at construction")
        super().reset(seed=seed)
        if self.game is not None:
            self.game.close()
        self.game = create_game(self.env_conf.config, self.home_team, self.away_team,
                                size=self.env_conf.size, control="external",
                                seed=int(self.np_random.integers(0, 2**32)))
        self._closed = self._terminated = self._truncated = False
        self._truncation_reason = None
        self._steps = 0
        self._budget = StepBudget(self.max_steps)
        return self.get_state(), self._info()

    def step(self, action):
        if self.game is None or self._closed or self._terminated or self._truncated:
            raise gym.error.ResetNeeded("Call reset before stepping a new episode")
        if isinstance(action, (bool, np.bool_)) or not self.action_space.contains(action):
            raise ValueError("action index outside Discrete space")
        legal = self._legal_actions()
        if int(action) not in legal:
            raise ValueError("action index is masked")
        team = self._seat(self.game.active_team)
        before = [t.state.score for t in self.game.state.teams]
        report_start = len(self.game.state.reports)
        try:
            self.game.advance(legal[int(action)], max_steps=self._budget)
        except GameTruncatedError as error:
            self._truncated = True
            self._truncation_reason = error.code
        self._steps += 1
        self._terminated = bool(self.game.state.game_over)
        if not self._terminated and self._steps >= self.max_decisions:
            self._truncated = True
            self._truncation_reason = self._truncation_reason or "decision_budget"
        rewards = {seat: float(t.state.score - score) for seat, t, score in
                   zip(("home", "away"), self.game.state.teams, before)}
        obs = self.get_state()
        info = self._info(team, self.game.state.reports[report_start:], rewards)
        return obs, rewards[team], self._terminated, self._truncated, info

    def render(self):
        if self.render_mode == "ansi" and self.game is not None:
            home, away = self.game.state.teams
            return f"{home.name} {home.state.score} - {away.state.score} {away.name}; actor={self._seat(self.game.active_team)}"

    def close(self):
        if self.game is not None and not self._closed:
            self.game.close()
        self._closed = True


from botbowl.ai import register_gymnasium_envs

register_gymnasium_envs()


class RewardWrapper(gym.Wrapper):
    """Add independently evaluated home/away rewards at every decision.

    Put this wrapper inside ScriptedActionWrapper/SinglePlayerWrapper so reward
    functions observe every intermediate game state. Functions take Game and
    must bind their own seat, rather than infer it from the next actor.
    """
    def __init__(self, env, home_reward_func, away_reward_func=None):
        inner = env
        while isinstance(inner, gym.Wrapper):
            if isinstance(inner, ScriptedActionWrapper):
                raise ValueError("Place RewardWrapper inside decision-advancing wrappers")
            inner = inner.env
        super().__init__(env)
        self.reward_funcs = {"home": home_reward_func, "away": away_reward_func}

    def reset(self, *, seed=None, options=None):
        obs, info = self.env.reset(seed=seed, options=options)
        for func in self.reward_funcs.values():
            reset = getattr(func, "reset", None)
            if reset is not None:
                reset()
        return obs, info

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        rewards = dict(info["rewards"])
        for seat, func in self.reward_funcs.items():
            if func is not None:
                rewards[seat] += float(func(self.unwrapped.game))
        info = {**info, "rewards": rewards}
        return obs, rewards[info["acting_team"]], terminated, truncated, info


class ScriptedActionWrapper(gym.Wrapper):
    """Explicitly batch decisions and retain every intermediate transition.

    scripted_func(Game) returns an Action or None (pause). All decisions travel
    through the wrapped step, preserving rewards and episode/engine budgets.
    info['transitions'] contains detached observations and infos in order.
    """
    def __init__(self, env, scripted_func):
        super().__init__(env)
        self.scripted_func = scripted_func

    @staticmethod
    def _record(action, result):
        obs, reward, terminated, truncated, info = result
        return {"action": int(action), "observation": deepcopy(obs), "reward": reward,
                "terminated": terminated, "truncated": truncated, "info": deepcopy(info)}

    def _run(self, obs, info, transitions, terminated=False, truncated=False):
        while not (terminated or truncated):
            action = self.scripted_func(self.unwrapped.game)
            if action is None:
                break
            index = self.unwrapped.encode_action(action)
            result = self.env.step(index)
            transitions.extend(result[4].get("transitions", [self._record(index, result)]))
            obs, _, terminated, truncated, info = result
        rewards = {seat: sum(t["info"]["rewards"][seat] for t in transitions) for seat in ("home", "away")}
        events = tuple(e for t in transitions for e in t["info"]["events"])
        return obs, terminated, truncated, {**info, "rewards": rewards, "events": events,
                                          "transitions": tuple(transitions)}

    def step(self, action):
        team = self.unwrapped._seat(self.unwrapped.game.active_team)
        result = self.env.step(action)
        obs, _, terminated, truncated, info = result
        transitions = list(info.get("transitions", [self._record(action, result)]))
        obs, terminated, truncated, info = self._run(obs, info, transitions, terminated, truncated)
        info["acting_team"] = team
        return obs, info["rewards"][team], terminated, truncated, info

    def reset(self, *, seed=None, options=None):
        obs, info = self.env.reset(seed=seed, options=options)
        self._reset_script()
        transitions = list(info.get("transitions", ()))
        obs, terminated, truncated, info = self._run(
            obs, info, transitions, info.get("terminated", False), info.get("truncated", False))
        # Reset has no reward/termination return slots: retain these explicitly.
        info.update(reset_rewards=dict(info["rewards"]), terminated=terminated, truncated=truncated)
        return obs, info

    def _reset_script(self):
        pass


class SinglePlayerWrapper(ScriptedActionWrapper):
    """Run only the supplied opponent callable; human flags never schedule it.

    opponent(Game) -> Action. A stateful policy may implement reset(seed=...).
    Policy exceptions propagate. It must return in bounded time. Opponent setup,
    rerolls and turn decisions appear in info['transitions'], including reset.
    """
    def __init__(self, env, opponent, *, learner="home"):
        if learner not in ("home", "away") or not callable(opponent):
            raise ValueError("learner must be home/away and opponent must be callable")
        self.opponent = opponent
        self.learner = learner
        super().__init__(env, self._opponent_action)

    def _opponent_action(self, game):
        if self.unwrapped._seat(game.active_team) == self.learner:
            return None
        return self.opponent(game)

    def _reset_script(self):
        reset = getattr(self.opponent, "reset", None)
        if reset is not None:
            reset(seed=int(self.unwrapped.np_random.integers(0, 2**32)))

    def step(self, action):
        result = super().step(action)
        obs, _, terminated, truncated, info = result
        return obs, info["rewards"][self.learner], terminated, truncated, info
