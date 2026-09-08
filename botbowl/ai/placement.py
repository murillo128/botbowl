"""Controller helpers that expand to ordinary engine actions."""
from botbowl.core.model import Action, Square
from botbowl.core.table import ActionType


def centered_kick(game):
    """Return a legal PLACE_BALL nearest the permitted zone's geometric center.

    The center is the midpoint of the legal squares' bounding rectangle. Ties
    use ascending (y, x) in arena coordinates, independent of choice order or
    the controller's reflected view. No random draw or game mutation occurs.
    Submit the returned action through Game.step; a recording game retains the
    actual PLACE_BALL target in its replay.
    """
    positions = set()
    for choice in game.get_available_actions():
        if choice.action_type is ActionType.PLACE_BALL and not choice.disabled:
            for position in choice.positions:
                if isinstance(position, Square) and game.is_action_allowed(
                        Action(ActionType.PLACE_BALL, position=position)):
                    positions.add(position)
    if not positions:
        raise ValueError("Centered kick requires a nonempty legal PLACE_BALL zone")
    center_x_twice = min(p.x for p in positions) + max(p.x for p in positions)
    center_y_twice = min(p.y for p in positions) + max(p.y for p in positions)
    position = min(positions, key=lambda p: (
        (2 * p.x - center_x_twice) ** 2 + (2 * p.y - center_y_twice) ** 2, p.y, p.x))
    return Action(ActionType.PLACE_BALL, position=game.get_square(position.x, position.y))
