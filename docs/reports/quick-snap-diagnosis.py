"""Issue-20 bounded controls; run from repository root with PYTHONPATH=.

setup/helper/original intentionally exit 1 while the inherited defects remain. consumer
exits 0 when its controlled hash collision and rejected stale target are observed.
No production monkeypatch, repair, or default-suite expected failure is installed.
"""
import argparse
import json
from pathlib import Path
import runpy

import botbowl as bb
from botbowl.core.forward_model import MovementStep
from botbowl.core.util import compare_iterable
from examples.hash_example import gamestate_hash
from tests.framework.test_quick_snap_forward_model import (
    action_for, ball_decision, choices, consistency, decision, kickoff_fixture, play, semantic,
)


def setup_game(side):
    game = kickoff_fixture(size=3, receiving=1 - side)
    game.dice.clear(bb.D6)
    game.dice.fix(bb.D6, 2, 2)  # Perfect Defence: legitimate Setup(reorganize=True).
    play(game, ball_decision(game))
    assert game.get_procedure().reorganize
    assert game.active_team is game.state.teams[side]
    assert game.is_setup_legal(game.active_team)
    return game


def positions(game):
    return [[p.player_id, None if p.position is None else [p.position.x, p.position.y]]
            for team in game.state.teams for p in team.players]


def setup(side):
    game = setup_game(side)
    a, b = game.get_players_on_pitch(game.active_team)[:2]
    before = semantic(game)
    before_positions = positions(game)
    checkpoint = game.capture_checkpoint()
    spec = decision(bb.ActionType.PLACE_PLAYER, a, b.position)
    play(game, spec)
    assert game.is_setup_legal(game.active_team)
    log = game.trajectory.action_log[checkpoint.step:]
    movement_count = sum(isinstance(step, MovementStep) for step in log)
    game.restore_checkpoint(checkpoint)
    consistency(game)  # Both board and positions stay swapped: internal consistency alone misses it.
    assert game.capture_rng_state() == checkpoint.rng_state
    # Negative control: a legal move into an empty square round-trips correctly.
    control = setup_game(side)
    player = control.get_players_on_pitch(control.active_team)[0]
    empty = next(p for p in control.get_team_side(control.active_team)
                 if control.get_player_at(p) is None)
    original = semantic(control)
    cp = control.capture_checkpoint()
    play(control, decision(bb.ActionType.PLACE_PLAYER, player, empty))
    control.restore_checkpoint(cp)
    assert semantic(control) == original
    failed = semantic(game) != before
    print(json.dumps(dict(
        finding="legal occupied-square setup placement is not undone", side=side, seed=0,
        fixture="stock gym-3; legal formations; forced Perfect Defence (2+2)",
        decision=spec, before=before_positions, after_restore=positions(game),
        movement_steps=movement_count, trajectory_entries=len(log),
        empty_square_control="pass", rng_control="restored exactly",
        criterion="restore_checkpoint returns the original semantic state and original occupants",
        correction_proposal="Log the player/player swap in Game.swap through reversible movement operations",
        pending="parent ownership decision and independent diagnostic review; no correction applied",
        reproduced=failed), sort_keys=True))
    return int(failed)


def helper():
    try:
        result = compare_iterable({bb.Square(1, 2): 1}, {bb.Square(10, 8): 1})
    except KeyError as error:
        print(json.dumps(dict(finding="comparison helper indexes a missing equal-length dictionary key",
                              exception=repr(error), criterion="return a nonempty difference list",
                              correction_proposal="compare dictionary key sets before indexing",
                              pending="separate helper scope decision; no correction applied"), sort_keys=True))
        return 1
    print(json.dumps(dict(reproduced=False, difference=result)))
    return 0


def consumer(side):
    game = kickoff_fixture(receiving=side)
    play(game, ball_decision(game))
    root = game.capture_checkpoint()
    starts = [spec for spec in choices(game) if spec[0] == "START_MOVE"]
    play(game, starts[0])
    first_hash = gamestate_hash(game)
    first_state = semantic(game)
    first_moves = [spec for spec in choices(game) if spec[0] == "MOVE"]
    game.restore_checkpoint(root)
    consistency(game)
    play(game, starts[1])
    second_hash = gamestate_hash(game)
    assert semantic(game) != first_state
    assert first_hash == second_hash
    stale = next(spec for spec in first_moves if not game.validate_action(action_for(game, spec)).allowed)
    before = semantic(game)
    rng = game.capture_rng_state()
    try:
        game.step(action_for(game, stale))
    except bb.InvalidActionError as error:
        code = error.code
    else:
        raise AssertionError("Expected stale target rejection")
    assert semantic(game) == before and game.capture_rng_state() == rng
    consistency(game)
    print(json.dumps(dict(
        finding="example MCTS hash aliases distinct active-player states", side=side, seed=0,
        first_activation=starts[0], second_activation=starts[1], cached_move=stale,
        same_example_hash=True, different_semantic_state=True, rejection=code,
        criterion="cached search decisions require equivalent full decision state, not this approximate hash alone",
        pending="controlled consumer risk only; historical Quick Snap None-position crash not established",
        correction_proposal="separately scope example search identity/action-cache correction if adopted"
    ), sort_keys=True))
    return 0


def original(trace_path):
    """Observe the unchanged #5 reduction, preserving its sampling/RNG order."""
    original_step, original_swap = bb.Game.step, bb.Game.swap
    events = []

    def step(game, action):
        player = action.player if action is not None else None
        event = dict(phase=type(game.get_procedure()).__name__,
                     reorganize=getattr(game.get_procedure(), "reorganize", None),
                     action=action.action_type.name if action is not None else None,
                     player=[game.state.teams.index(player.team), player.nr] if player is not None else None,
                     position=[action.position.x, action.position.y]
                     if action is not None and action.position is not None else None,
                     swaps=[])
        events.append(event)
        start = game.get_step()
        result = original_step(game, action)
        event["movement_steps"] = sum(isinstance(item, MovementStep)
                                     for item in game.trajectory.action_log[start:])
        return result

    def swap(game, first, second):
        events[-1]["swaps"].append([first.nr, second.nr])
        return original_swap(game, first, second)

    bb.Game.step, bb.Game.swap = step, swap
    try:
        runpy.run_path(str(Path(__file__).with_name("packaging-forward-model-reproduction.py")))
    except SystemExit as error:
        return error.code
    finally:
        bb.Game.step, bb.Game.swap = original_step, original_swap
        Path(trace_path).write_text(json.dumps(dict(seed=3, decisions=events), sort_keys=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("setup", "helper", "consumer", "original"))
    parser.add_argument("--side", type=int, choices=(0, 1), default=0)
    parser.add_argument("--trace", default="/tmp/botbowl-issue20-original-trace.json")
    args = parser.parse_args()
    raise SystemExit(original(args.trace) if args.mode == "original" else
                     helper() if args.mode == "helper" else globals()[args.mode](args.side))
