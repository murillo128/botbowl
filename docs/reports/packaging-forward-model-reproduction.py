"""Reproduce inherited setup undo drift; expected exit 1, not a package test.

Run with an installed Bot Bowl from any cwd. Issue #20 owns the investigation.
Seed 3 yields the same divergence on the accepted baseline and issue #5, on
CPython 3.11 and 3.14. No rule or forward-model correction is made here.
"""
import json
import botbowl as bb

config = bb.load_config("bot-bowl")
config.fast_mode = True
rules = bb.load_rule_set(config.ruleset)
team = bb.load_team_by_filename("human", rules)
game = bb.Game(1, team, bb.load_team_by_filename("human", rules),
               bb.Agent("Human 2", human=True, agent_id=2),
               bb.Agent("Human 1", human=True, agent_id=1), config)
game.init()
game.enable_forward_model()
game.set_seed(3)
game.config.pathfinding_enabled = False


def positions():
    return [(side, player.nr, None if player.position is None else
             [player.position.x, player.position.y])
            for side, team in enumerate(game.state.teams) for player in team.players]


def random_action():
    # Match the inherited test's sampling and RNG consumption order exactly.
    for _ in range(1000):
        choice = game.rng.choice(game.state.available_actions)
        if choice.action_type != bb.ActionType.PLACE_PLAYER:
            break
    else:
        raise RuntimeError("Action sampling budget exhausted")
    position = game.rng.choice(choice.positions) if choice.positions else None
    player = game.rng.choice(choice.players) if choice.players else None
    return bb.Action(choice.action_type, position=position, player=player)


for step in range(256):
    checkpoint = game.get_step()
    before = positions()
    phase = type(game.get_procedure()).__name__
    action = random_action()
    game.step(action)
    game.revert(checkpoint)
    after = positions()
    if before != after:
        print(json.dumps({"seed": 3, "step": step, "phase": phase,
                          "action": action.action_type.name,
                          "changed": [[old, new] for old, new in zip(before, after)
                                      if old != new]}, sort_keys=True))
        raise SystemExit(1)
    game.step(random_action())
    if game.state.game_over:
        break
raise SystemExit("Expected inherited divergence was not reproduced")
