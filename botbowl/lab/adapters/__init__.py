"""Explicit compatibility with engine-aware legacy bots."""
from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from botbowl.core.game import Game
    from botbowl.core.model import Action, Agent, Team


class LegacyBotAdapter:
    """Borrow an Agent and forward only explicitly invoked legacy callbacks.

This adapter grants the bot the actual mutable Game, including its RNG and
teams. Returned Actions may borrow engine objects. It satisfies the legacy
PolicyDriver callable boundary, NOT lab.Policy's restricted data contract.
The caller owns the bot, game, initialization, finalization and bot RNG.
"""

    requires_game_access: ClassVar[Literal[True]] = True

    def __init__(self, bot: "Agent") -> None:
        self.bot = bot

    def new_game(self, game: "Game", team: "Team") -> None:
        """Explicitly forward initialization with the caller's actual team."""
        self.bot.new_game(game, team)

    def act(self, game: "Game") -> "Action":
        """Explicitly forward a decision with full engine access."""
        return self.bot.act(game)

    def __call__(self, game: "Game") -> "Action":
        """Support the existing PolicyDriver callable without changing its API."""
        return self.act(game)

    def end_game(self, game: "Game") -> None:
        """Explicitly forward finalization; construction never calls this."""
        self.bot.end_game(game)
