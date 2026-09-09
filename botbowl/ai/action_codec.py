"""Pure fixed index layout shared by Gymnasium v5 and lab AEC v1."""
from botbowl.core import ActionType


class ActionIndexCodecV1:
    """Encode canonical target components; callers own perspective and legality."""

    version = 1

    def __init__(self, width, height, player_slots):
        self.width, self.height = width, height
        self.player_slots = player_slots
        self.board_squares = width * height
        self.action_types = tuple(t for t in ActionType if t is not ActionType.CONTINUE)
        self.action_stride = 1 + self.board_squares + 2 * player_slots
        self.placement_offset = len(self.action_types) * self.action_stride
        self.n = self.placement_offset + 2 * player_slots * (self.board_squares + 1)

    def encode(self, action_type, square=0, player_index=None):
        if not 0 <= square <= self.board_squares:
            raise ValueError("position outside the board")
        if player_index is not None and not 0 <= player_index < 2 * self.player_slots:
            raise ValueError("player outside the roster slots")
        if action_type is ActionType.PLACE_PLAYER:
            if player_index is None:
                raise ValueError("PLACE_PLAYER requires a player")
            return self.placement_offset + player_index * (self.board_squares + 1) + square
        if player_index is not None and square:
            raise ValueError("Only PLACE_PLAYER uses a player and square together")
        target = square if player_index is None else 1 + self.board_squares + player_index
        return self.action_types.index(action_type) * self.action_stride + target

    def decode(self, index):
        """Return (ActionType, one-based square or zero, roster index or None)."""
        if not 0 <= index < self.n:
            raise ValueError("action index outside the layout")
        if index >= self.placement_offset:
            player, square = divmod(index - self.placement_offset, self.board_squares + 1)
            return ActionType.PLACE_PLAYER, square, player
        type_index, target = divmod(index, self.action_stride)
        if target > self.board_squares:
            return self.action_types[type_index], 0, target - self.board_squares - 1
        return self.action_types[type_index], target, None
