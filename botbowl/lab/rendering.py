"""Optional synthetic RGB geometry; no legacy assets, display library or RNG.

Import explicitly. Neither the core nor views imports this module.
"""
import numpy as np

from .views import GridView


# Original flat colours and shapes; these are not inherited artwork.
PADDING_RGB = (0, 0, 0)
CROWD_RGB = (45, 48, 52)
PITCH_RGB = (46, 104, 63)
ENDZONE_RGB = (64, 126, 82)
PLAYER_RGB = (104, 185, 247)
BALL_RGB = (247, 190, 62)
TARGET_RGB = (245, 245, 245)


def render_grid(grid, *, cell_size=12):
    """Return uint8 RGB[height*cell_size, width*cell_size, 3].

    Cell (x,y) occupies [y*s:(y+1)*s, x*s:(x+1)*s]. Players are large
    squares; balls are smaller centered squares painted above players. Counts,
    IDs, off-grid entities and individual co-located players are not rendered.
    """
    if type(grid) is not GridView:
        raise ValueError("Expected GridView")
    if type(cell_size) is not int or cell_size < 4:
        raise ValueError("cell_size must be an integer at least four")
    h, w = grid.arena_mask.shape
    rgb = np.zeros((h * cell_size, w * cell_size, 3), dtype=np.uint8)
    channels = grid.metadata["channels"]
    endzones = (grid.features[channels.index("tile.HOME_TOUCHDOWN")]
                + grid.features[channels.index("tile.AWAY_TOUCHDOWN")]) > 0
    players = grid.features[channels.index("is_player.sum")] > 0
    balls = grid.features[channels.index("is_ball.sum")] > 0
    for y in range(h):
        for x in range(w):
            cell = rgb[y * cell_size:(y + 1) * cell_size, x * cell_size:(x + 1) * cell_size]
            cell[:] = (PADDING_RGB if not grid.arena_mask[y, x] else
                       CROWD_RGB if not grid.playable[y, x] else
                       ENDZONE_RGB if endzones[y, x] else PITCH_RGB)
            if players[y, x]:
                margin = max(1, cell_size // 4)
                cell[margin:cell_size - margin, margin:cell_size - margin] = PLAYER_RGB
            if balls[y, x]:
                start = cell_size // 2 - 1
                cell[start:start + 2, start:start + 2] = BALL_RGB
    names = grid.metadata["context_channels"]
    xi, yi = names.index("decision.target_x"), names.index("decision.target_y")
    if grid.context_present[xi] and grid.context_present[yi]:
        x, y = int(grid.context[xi]), int(grid.context[yi])
        frame = grid.metadata["frame"]
        if 0 <= x < frame["width"] and 0 <= y < frame["height"]:
            cell = rgb[y * cell_size:(y + 1) * cell_size, x * cell_size:(x + 1) * cell_size]
            cell[0, :] = cell[-1, :] = cell[:, 0] = cell[:, -1] = TARGET_RGB
    return rgb
