"""Known semantic outcomes and repeatability across all supported pitch sizes."""
import pytest
import botbowl as bb
from botbowl.core.procedure import Setup, Turn
from tests.baseline import SIZES, SEEDS, scenario, place_players
from tests.util import only_fixed_rolls


def run_scenario(kind, size, seed):
    with scenario(size, seed) as probe:
        game = probe.game
        if kind == "movement":
            player, = place_players(probe, [(2, 2)])
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(3, 2)))
            assert player.position == game.get_square(3, 2), probe.diagnostic()
            assert player.state.up and player.state.moves == 1, probe.diagnostic()
        elif kind == "block":
            attacker, defender = place_players(probe, [(2, 2)], [(3, 2)])
            probe.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
            with only_fixed_rolls(game, block_dice=[bb.BBDieResult.DEFENDER_DOWN]):
                probe.step(bb.Action(bb.ActionType.BLOCK, position=defender.position))
            probe.step(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
            with only_fixed_rolls(game, d6=[1, 1]):
                probe.step(bb.Action(bb.ActionType.PUSH, position=game.get_square(4, 2)))
                probe.step(bb.Action(bb.ActionType.FOLLOW_UP, position=attacker.position))
            assert defender.position == game.get_square(4, 2), probe.diagnostic()
            assert not defender.state.up, probe.diagnostic()
            assert game.has_report_of_type(bb.OutcomeType.KNOCKED_DOWN), probe.diagnostic()
        elif kind == "pass":
            passer, catcher = place_players(probe, [(2, 2), (3, 2)], ball=(2, 2))
            probe.step(bb.Action(bb.ActionType.START_PASS, player=passer))
            with only_fixed_rolls(game, d6=[6, 6]):
                probe.step(bb.Action(bb.ActionType.PASS, position=catcher.position))
            assert game.get_ball_carrier() is catcher, probe.diagnostic()
            assert game.has_report_of_type(bb.OutcomeType.ACCURATE_PASS), probe.diagnostic()
            assert game.has_report_of_type(bb.OutcomeType.SUCCESSFUL_CATCH), probe.diagnostic()
        elif kind == "reroll":
            player, = place_players(probe, [(2, 2)])
            player.state.moves = player.get_ma()
            player.team.state.rerolls = 1
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            with only_fixed_rolls(game, d6=[1, 6]):
                probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(3, 2)))
                probe.step(bb.Action(bb.ActionType.USE_REROLL))
            assert player.position == game.get_square(3, 2) and player.state.up, probe.diagnostic()
            assert player.team.state.rerolls == 0, probe.diagnostic()
            for event in (bb.OutcomeType.FAILED_GFI, bb.OutcomeType.REROLL_USED, bb.OutcomeType.SUCCESSFUL_GFI):
                assert game.has_report_of_type(event), probe.diagnostic()
        elif kind == "drive":
            x = game.get_opp_endzone_x(game.active_team)
            start_x = x + (1 if x == 1 else -1)
            player, = place_players(probe, [(start_x, 2)], ball=(start_x, 2))
            team = player.team
            probe.step(bb.Action(bb.ActionType.START_MOVE, player=player))
            probe.step(bb.Action(bb.ActionType.MOVE, position=game.get_square(x, 2)))
            assert team.state.score == 1 and game.has_report_of_type(bb.OutcomeType.TOUCHDOWN), probe.diagnostic()
            assert type(game.get_procedure()) is Setup, probe.diagnostic()
            assert game.get_kicking_team() is team, probe.diagnostic()
            probe.until(lambda g: type(g.get_procedure()) is Turn)
            assert game.active_team is game.get_opp_team(team), probe.diagnostic()
        else:
            assert kind == "end_game"
            probe.until(lambda g: g.state.game_over)
            assert game.has_report_of_type(bb.OutcomeType.END_OF_GAME_DRAW), probe.diagnostic()
            assert all(team.state.turn == game.config.rounds for team in game.state.teams), probe.diagnostic()
        return probe.trace()


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("kind", ("movement", "block", "pass", "reroll", "drive", "end_game"))
def test_semantic_scenario(kind, size, seed):
    first = run_scenario(kind, size, seed)
    second = run_scenario(kind, size, seed)
    assert first == second, f"kind={kind} size={size} seed={seed} last_actions={second['actions'][-12:]}"


def test_step_budget_reports_reproduction_context():
    with scenario(size=1, seed=17) as probe:
        probe.max_steps = len(probe.actions)
        with pytest.raises(AssertionError, match=r"size=1 seed=17.*last_actions="):
            probe.step(bb.Action(bb.ActionType.END_TURN))


def test_fixed_rolls_restored_after_failure():
    with scenario(size=1) as outer:
        outer.game.dice.fix(bb.D6, 3)
        with pytest.raises(RuntimeError):
            with scenario(size=1) as inner:
                inner.game.dice.fix(bb.D6, 6)
                raise RuntimeError("fixture cleanup")
        assert outer.game.dice.pending(bb.D6) == (3,)
        assert inner.game.dice.pending(bb.D6) == ()
    assert outer.game.dice.pending(bb.D6) == ()


def test_one_turn_game_configuration():
    with scenario(size=1, seed=0, turns=1) as probe:
        probe.until(lambda game: game.state.game_over)
        assert probe.game.config.rounds == 1, probe.diagnostic()
        assert [team.state.turn for team in probe.game.state.teams] == [1, 1], probe.diagnostic()
        assert sum(action[0] == "END_TURN" for action in probe.actions) == 4, probe.diagnostic()


def test_human_game_records_end_time():
    with scenario(size=1, seed=0, turns=1) as probe:
        probe.until(lambda game: game.state.game_over)
        assert probe.game.end_time is not None, probe.diagnostic()
