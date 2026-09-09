"""Replay a bounded natural pregame prefix with the recorded five-source recipe."""
import json

import botbowl as bb
from botbowl.lab.episodes import EpisodeContext


def main():
    config = bb.load_config("gym-1")
    rules = bb.load_rule_set(config.ruleset)
    arena = bb.load_arena(config.arena)
    home = bb.load_team_by_filename("human", rules, board_size=1)
    away = bb.load_team_by_filename("human", rules, board_size=1)
    context = EpisodeContext(config, rules, arena, home, away,
                             episode_key="example-1", master_seed=17, max_decisions=3)
    actions = [{"action_type": name} for name in ("START_GAME", "HEADS", "RECEIVE")]
    try:
        manifest = context.manifest()
        first = context.replay(actions, manifest)
        context.reset()
        assert context.replay(actions, manifest) == first
        assert first[-1].truncated and not first[-1].terminal
        print(json.dumps({"manifest": manifest, "decisions": first[-1].decisions,
                          "truncated": first[-1].truncated}, sort_keys=True))
    finally:
        context.close()


if __name__ == "__main__":
    main()
