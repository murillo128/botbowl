"""Run python -m examples.lab.views; public APIs only, no GPU or display."""
import botbowl as bb
from botbowl.lab.observations import ObservationControl, observe
from botbowl.lab.views import entity_view, grid_view


def main():
    game = bb.create_game(size=3, seed=17, control="external")
    try:
        binding = ObservationControl(game)
        snapshot = observe(game, binding, "home")
        entities = entity_view(snapshot, team="home")
        grid = grid_view(snapshot, team="home")
        assert entities.metadata["logical_time"] == grid.metadata["logical_time"]
        print("Entity features:", entities.features.shape, "present:", entities.row_mask.sum())
        print("Raw grid:", grid.features.shape, "playable cells:", grid.playable.sum())
        print("Player IDs (metadata only):", entities.entity_ids)
    finally:
        game.close()


if __name__ == "__main__":
    main()
