"""Two externally controlled coaches; optional PettingZoo AEC v1 adapter.

See docs/lab/pettingzoo.md for the action, primary-view and reward contracts.
"""
from copy import deepcopy

import numpy as np
from gymnasium import spaces
from pettingzoo import AECEnv

from botbowl.ai.action_codec import ActionIndexCodecV1
from botbowl.core import ActionType
from botbowl.lab.actions import PositionV1
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import NoProgress, SessionConfig, SimulationSession
from botbowl.lab.views import coordinate_frame, entity_view, grid_view


class BotBowlAECEnv(AECEnv):
    metadata = {"name": "botbowl_aec_v1", "render_modes": [], "is_parallelizable": False}
    possible_agents = ["home", "away"]
    render_mode = None

    def __init__(self, config=None):
        super().__init__()
        self.config = deepcopy(config if config is not None else SessionConfig(size=11))
        # Derive spaces from the same validated session configuration as reset.
        probe = SimulationSession(self.config, SeedSpec(0, "aec-v1"))
        try:
            primary = probe.observe("home").primary["data"]
            geometry = primary["geometry"]
            self.player_slots = max(sum(p["team"] == side for p in primary["players"])
                                    for side in self.possible_agents)
            self.entity_rows = 2 * self.player_slots + 1
            self.codec = ActionIndexCodecV1(geometry["width"], geometry["height"],
                                            self.player_slots)
            vector, _, layout = self._view(primary, "home")
            self.observation_layout = layout
            limit = np.finfo(np.float32).max
            self.observation_spaces = {
                side: spaces.Dict({
                    "observation": spaces.Box(-limit, limit, vector.shape, np.float32),
                    "action_mask": spaces.MultiBinary(self.codec.n),
                }) for side in self.possible_agents
            }
            self.action_spaces = {side: spaces.Discrete(self.codec.n)
                                  for side in self.possible_agents}
        finally:
            probe.close()
        self._session = None
        self._closed = False
        self.agents = []

    def observation_space(self, agent):
        return self.observation_spaces[agent]

    def action_space(self, agent):
        return self.action_spaces[agent]

    def _view(self, primary, side):
        table = entity_view(primary, team=side, pad_to=self.entity_rows)
        grid = grid_view(primary, team=side)
        arrays = (
            ("entities.features", table.features), ("entities.present", table.present),
            ("entities.row_mask", table.row_mask), ("context", table.context),
            ("context_present", table.context_present), ("grid.features", grid.features),
            ("grid.present", grid.present), ("grid.playable", grid.playable),
        )
        layout, offset = [], 0
        for name, value in arrays:
            layout.append({"name": name, "shape": value.shape,
                           "start": offset, "stop": offset + value.size})
            offset += value.size
        vector = np.concatenate([a.reshape(-1) for _, a in arrays]).astype(np.float32)
        metadata = {"entities": table.metadata, "grid": grid.metadata,
                    "entity_ids": table.entity_ids, "entity_data": table.entities}
        return vector, metadata, tuple(layout)

    def _index(self, action, frame, players):
        """Translate offered ActionV1 to the shared v5 layout without Game access."""
        side = action.actor_id
        position = action.position
        if action.target_id is not None:
            target = players[action.target_id]
            position = PositionV1.from_json(target["position"]["value"])
        square = 0
        if position is not None:
            position = frame.position(position)
            square = 1 + position.y * self.codec.width + position.x
        player_index = None
        if action.player_id is not None:
            player = players[action.player_id]
            player_index = player["slot"] + (0 if player["team"] == side else self.player_slots)
        return self.codec.encode(ActionType[action.type], square, player_index)

    def _refresh(self, result):
        self._result = result
        primary = result.primary["data"]
        self._legal = {}
        frame = coordinate_frame(primary, result.next_actor)
        players = {p["id"]: p for p in primary["players"]}
        for action in self._session.legal_actions().actions:
            index = self._index(action, frame, players)
            if index in self._legal and self._legal[index] != action:
                raise ValueError("Offered semantic actions collide in the v1 index layout")
            self._legal[index] = action
        self._observations = {}
        for side in self.agents:
            state = self._session.observe(side)
            vector, metadata, layout = self._view(state.primary["data"], side)
            if layout != self.observation_layout:
                raise ValueError("Session changed the configured observation layout")
            mask = np.zeros(self.codec.n, dtype=np.int8)
            if side == result.next_actor:
                mask[list(self._legal)] = 1
            self._observations[side] = {"observation": vector, "action_mask": mask}
            self.terminations[side] = result.terminated
            self.truncations[side] = result.truncated
            self.infos[side] = {"state_revision": result.state_revision,
                                "decision_id": result.decision_id,
                                "next_actor": result.next_actor,
                                "end_reason": result.end_reason,
                                "view_metadata": metadata}
        if result.next_actor is not None:
            self.agent_selection = result.next_actor
        else:
            # Both coaches receive one final last()/step(None), even if no
            # playable decision was permitted by the reset budget.
            self.agent_selection = self.agents[0]
            self._deads_step_first()

    def reset(self, seed=None, options=None):
        # PettingZoo's api_test explicitly probes an unused options dictionary.
        # Options carry no configuration overrides; construction fixes spaces.
        if options is not None and not isinstance(options, dict):
            raise ValueError("options must be a dictionary or None")
        candidate = SimulationSession(self.config, SeedSpec(seed, "aec-v1"))
        previous = self._session
        self._session = candidate
        if previous is not None:
            previous.close()
        self._closed = False
        self.agents = self.possible_agents[:]
        self.rewards = {side: 0.0 for side in self.agents}
        self._cumulative_rewards = self.rewards.copy()
        self.terminations = {side: False for side in self.agents}
        self.truncations = self.terminations.copy()
        self.infos = {side: {} for side in self.agents}
        self._skip_agent_selection = None
        self._refresh(candidate.observe())

    def observe(self, agent):
        if self._session is None:
            raise RuntimeError("Call reset before observe")
        return deepcopy(self._observations[agent])

    def decode_action(self, index):
        """Return a detached, currently legal absolute ActionV1 for the actor."""
        if self._session is None or self._closed or not self.agents:
            raise RuntimeError("Call reset before decoding a playable action")
        if (isinstance(index, (bool, np.bool_))
                or not self.action_space(self.agent_selection).contains(index)):
            raise ValueError("action index outside Discrete space")
        if int(index) not in self._legal:
            raise ValueError("action index is masked")
        return deepcopy(self._legal[int(index)])

    def encode_action(self, action):
        """Return the current legal index of an absolute ActionV1."""
        if self._session is None or self._closed or not self.agents:
            raise RuntimeError("Call reset before encoding a playable action")
        for index, offered in self._legal.items():
            if action == offered:
                return index
        raise ValueError("semantic action is not currently offered")

    def step(self, action):
        if self._session is None or self._closed or not self.agents:
            raise RuntimeError("Call reset before stepping a new episode")
        actor = self.agent_selection
        if self.terminations[actor] or self.truncations[actor]:
            self._was_dead_step(action)
            return
        # Validate before changing even AEC reward bookkeeping. None is not play.
        semantic = self.decode_action(action)
        before = [team["score"] for team in self._result.primary["data"]["teams"]]
        try:
            result = self._session.step(semantic, self._result.state_revision)
        except NoProgress:
            # The session has already consumed this decision and records a
            # bounded administrative stop; expose it through AEC truncations.
            result = self._session.observe()
        self._cumulative_rewards[actor] = 0.0
        self._refresh(result)
        self.rewards = {side: float(team["score"] - score)
                        for side, team, score in zip(self.possible_agents,
                                                    result.primary["data"]["teams"], before)}
        self._accumulate_rewards()

    def close(self):
        if self._session is not None and not self._closed:
            self._session.close()
            # Keep final observations readable and expose close as truncation.
            if self.agents:
                self._refresh(self._session.observe())
        self._closed = True
