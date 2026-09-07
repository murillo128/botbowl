from copy import deepcopy
from pathlib import Path

import pytest

import botbowl as bb
from botbowl.core.util import get_data_path
from tests.formation_helpers import reduce_roster, setup_game, snapshot


FORMATIONS = [(size, path.stem) for size in (1, 3, 5, 7, 11)
              for path in sorted(Path(get_data_path(f"formations/{size}")).glob("*.txt"))]


@pytest.mark.parametrize("size,name", FORMATIONS)
@pytest.mark.parametrize("home", (False, True))
@pytest.mark.parametrize("roster", ("normal", "reduced", "one", "empty"))
def test_stock_formations_are_legal_and_do_not_change_opponents(size, name, home, roster):
    game = setup_game(size, home)
    team, opponent = game.active_team, game.get_opp_team(game.active_team)
    if roster != "normal":
        reduce_roster(game, {"reduced": max(0, size - 1), "one": 1, "empty": 0}[roster])
    available = len(game.get_reserves(team))
    for player, position in zip(game.get_reserves(opponent)[:size], game.get_team_side(opponent)):
        game.reserves_to_pitch(player, position)
    opponents_before = [(player, player.position) for player in opponent.players]
    formation = bb.load_formation(name, size=size)
    before = snapshot(game)
    actions = formation.actions(game, team)
    assert snapshot(game) == before
    placements = [action for action in actions if action.position is not None]
    assert len(placements) == min(available, size)
    assert len({action.position for action in placements}) == len(placements)
    assert len({action.player for action in placements}) == len(placements)
    # All available scrimmage slots precede the rest, even with a reduced team.
    flags = [game.is_scrimmage(action.position) for action in placements]
    assert flags == sorted(flags, reverse=True)
    for action in actions:
        assert game.is_action_allowed(action)
        game.step(action)
    assert game.is_setup_legal(team)
    assert all(game.is_team_side(player.position, team) for player in game.get_players_on_pitch(team))
    assert [(player, player.position) for player in opponent.players] == opponents_before
    game.step(bb.Action(bb.ActionType.END_SETUP))
    assert game.get_procedure().team != team


@pytest.mark.parametrize("home", (False, True))
@pytest.mark.parametrize("available", (1, 2, 4))
@pytest.mark.parametrize("geometry", ("padded", "moved_scrimmage"))
def test_scrimmage_is_not_assumed_to_be_the_last_file_column(home, available, geometry):
    arena = bb.load_arena("ff-pitch-3")
    rows = ["-------", "---m---", "-----0-", "---m---", "-------"]
    if geometry == "moved_scrimmage":
        # Move each side's actual scrimmage tiles one square toward its endzone.
        for row in arena.board[2:5]:
            row[5], row[6] = row[6], row[5]
            row[7], row[8] = row[8], row[7]
        rows = ["------", "---m--", "----0-", "---m--", "------"]
    game = setup_game(3, home, arena)
    reduce_roster(game, available)
    team = game.active_team
    actions = bb.Formation("Custom", rows).actions(game, team)
    assert game.is_scrimmage(actions[0].position)
    for action in actions:
        game.step(action)
    assert game.is_setup_legal(team)
    assert len(game.get_players_on_pitch(team)) == min(3, available)


@pytest.mark.parametrize("home", (False, True))
@pytest.mark.parametrize("reorganize", (False, True))
def test_reapply_formation_with_players_already_on_pitch(home, reorganize):
    game = setup_game(11, home)
    team = game.active_team
    for action in bb.load_formation("off_line").actions(game, team):
        game.step(action)
    opponent = game.get_opp_team(team)
    game.reserves_to_pitch(game.get_reserves(opponent)[0], game.get_team_side(opponent)[0])
    opponents_before = [(p, p.position) for p in opponent.players]
    reserves_before = list(game.get_reserves(team))
    game.get_procedure().reorganize = reorganize
    game.set_available_actions()
    before = snapshot(game)
    actions = bb.load_formation("def_zone").actions(game, team)
    assert snapshot(game) == before
    if reorganize:
        assert all(action.position is not None and action.player not in reserves_before for action in actions)
    for action in actions:
        game.step(action)
    assert game.is_setup_legal(team)
    assert len({p.position for p in game.get_players_on_pitch(team)}) == 11
    assert all(game.get_player_at(a.position) == a.player for a in actions if a.position is not None)
    assert [(p, p.position) for p in opponent.players] == opponents_before


@pytest.mark.parametrize("rows,diagnostic", [
    (["------"], "dimensions"),
    (["-" * 13] * 5, "dimensions"),
    (["------"] * 5, "player count"),
    (["S-----", "---S--", "-----0", "---S--", "------"], "player count"),
    (["------", "---S--", "----0-", "---S--", "------"], "scrimmage"),
    (["------", "------", "------0", "------", "------"], "rectangular"),
    (["-------", "---S---", "------0", "---S---", "-------"], "team's side"),
    (["-xx---", "------", "-----0", "------", "------"], "wide zone"),
])
@pytest.mark.parametrize("home", (False, True))
def test_invalid_template_fails_before_removing_or_placing_players(rows, diagnostic, home):
    game = setup_game(3, home)
    team = game.active_team
    for action in bb.load_formation("def_spread", size=3).actions(game, team):
        game.step(action)
    # Mutated templates must also be revalidated when the macro is requested.
    formation = bb.load_formation("def_spread", size=3)
    formation.formation[:] = rows
    game.get_procedure().formations = [formation]
    before = deepcopy(game.state.to_json(ignore_clocks=True))
    with pytest.raises(ValueError, match=diagnostic):
        game.step(bb.Action(bb.ActionType.SETUP_FORMATION_SPREAD))
    # Game.step retains its submitted macro, but the board, dugouts and reports
    # must be untouched by the failed expansion.
    assert game.state.to_json(ignore_clocks=True) == before
    before = snapshot(game)
    with pytest.raises(ValueError, match=diagnostic):
        formation.actions(game, team)
    assert snapshot(game) == before


@pytest.mark.parametrize("home", (False, True))
def test_reorganization_rejects_insufficient_slots_or_players(home):
    game = setup_game(3, home)
    team = game.active_team
    for action in bb.load_formation("def_spread", size=3).actions(game, team):
        game.step(action)
    game.get_procedure().reorganize = True
    one_slot = bb.Formation("One", ["------", "------", "-----0", "------", "------"])
    before = snapshot(game)
    with pytest.raises(ValueError, match="reorganize all players"):
        one_slot.actions(game, team)
    assert snapshot(game) == before
    for player in list(game.get_players_on_pitch(team)):
        game.pitch_to_reserves(player)
    game.set_available_actions()
    before = snapshot(game)
    with pytest.raises(ValueError, match="insufficient players"):
        one_slot.actions(game, team)
    assert snapshot(game) == before


@pytest.mark.parametrize("matrix", (None, [], [[]], "x", [0], [[None]], [["xx"]], [["?"]],
                                    ["--", "-"], [[['x']]]))
def test_formation_constructor_rejects_malformed_matrices(matrix):
    with pytest.raises(ValueError, match="Formation 'Invalid'"):
        bb.Formation("Invalid", matrix)


@pytest.mark.parametrize("home", (False, True))
def test_formation_does_not_displace_an_opponent_on_a_target_square(home):
    game = setup_game(3, home)
    team = game.active_team
    opponent = game.get_opp_team(team)
    target = next(p for p in game.get_team_side(team) if game.is_scrimmage(p) and p.y == 3)
    game.reserves_to_pitch(game.get_reserves(opponent)[0], target)
    before = snapshot(game)
    with pytest.raises(ValueError, match="occupied by an opponent"):
        bb.load_formation("def_spread", size=3).actions(game, team)
    assert snapshot(game) == before


def test_formation_rejects_the_other_team_during_setup():
    game = setup_game()
    before = snapshot(game)
    with pytest.raises(ValueError, match="team's setup procedure"):
        bb.load_formation("def_spread", size=3).actions(game, game.get_opp_team(game.active_team))
    assert snapshot(game) == before


def test_small_custom_formation_example():
    directory = Path(__file__).resolve().parents[2] / "examples" / "formations"
    formation = bb.load_formation("compact_three", directory=str(directory), size=3)
    game = setup_game()
    team = game.active_team
    for action in formation.actions(game, team):
        game.step(action)
    assert game.is_setup_legal(team)
