"""Game-local test dice and in-memory trajectory/RNG replay contracts."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import multiprocessing

import numpy as np
import pytest

import botbowl as bb
from botbowl.ai.bots.random_bot import RandomBot
from tests.baseline import semantic_action, semantic_reports
from tests.util import get_custom_game_turn, get_game_coin_toss, get_game_turn, only_fixed_rolls


DICE = (
    (bb.D3, "d3", 1, 3),
    (bb.D6, "d6", 1, 6),
    (bb.D8, "d8", 1, 8),
    (bb.BBDie, "block_dice", bb.BBDieResult.ATTACKER_DOWN, bb.BBDieResult.DEFENDER_DOWN),
)


@pytest.mark.parametrize("die,keyword,low,high", DICE)
def test_interleaved_games_and_third_fixture(die, keyword, low, high):
    a = get_game_coin_toss(seed=17)
    b = get_game_coin_toss(seed=0)
    control = get_game_coin_toss(seed=0)
    a.dice.fix(die, low, low)
    b.dice.fix(die, high, high)
    control.dice.fix(die, high, high)

    # This helper used to clear all four class queues, even on cache hits.
    get_game_turn()
    get_game_turn()
    assert b.dice.pending(die) == (high, high)
    observed = []
    for _ in range(8):
        observed.append(die(b.dice).value)
        die(a.dice)
    assert observed[:2] == [high, high]
    assert observed == [die(control.dice).value for _ in range(8)]
    assert b.capture_rng_state() == control.capture_rng_state()


@pytest.mark.parametrize("die,keyword,low,high", DICE)
def test_scoped_queues_cleanup_on_exception_and_nesting(die, keyword, low, high):
    a = get_game_coin_toss()
    b = get_game_coin_toss()
    a.dice.fix(die, low)
    b.dice.fix(die, high)
    before = a.capture_rng_state()
    with pytest.raises(RuntimeError, match="body failed"):
        with a.dice.force(**{keyword: [high, high]}, strict=True):
            assert die(a.dice).value == high
            with a.dice.force(**{keyword: [low]}, strict=True):
                assert die(a.dice).value == low
            assert a.dice.pending(die) == (high,)
            raise RuntimeError("body failed")
    assert a.capture_rng_state() == before
    assert die(b.dice).value == high
    assert die(a.dice).value == low
    assert not a.dice.pending(die)
    fresh = get_game_coin_toss()
    assert all(not fresh.dice.pending(kind) for kind, *_ in DICE)


@pytest.mark.parametrize("die,keyword,low,high", DICE)
@pytest.mark.parametrize("invalid", [0, -1, 9, 1.0, 1.5, True, np.bool_(False), "1", None, float("nan")])
def test_invalid_results_are_atomic(die, keyword, low, high, invalid):
    game = get_game_coin_toss()
    game.dice.fix(die, low)
    before = game.capture_rng_state()
    with pytest.raises(ValueError, match="Invalid forced result"):
        game.dice.fix(die, high, invalid)
    assert game.capture_rng_state() == before
    with pytest.raises(ValueError, match="Invalid forced result"):
        with game.dice.force(**{keyword: [high, invalid]}):
            pytest.fail("Invalid context entered")
    assert game.capture_rng_state() == before


@pytest.mark.parametrize("die,invalid", [(bb.D3, 4), (bb.D6, 7), (bb.D8, 9), (bb.BBDie, bb.WeatherType.NICE)])
def test_each_upper_domain_bound(die, invalid):
    with pytest.raises(ValueError):
        bb.DiceSource().fix(die, invalid)


@pytest.mark.parametrize("die,keyword,low,high", DICE)
def test_exhaustion_is_explicit_and_natural_fallback_does_not_skip_rng(die, keyword, low, high):
    game = get_game_coin_toss(seed=17)
    reference = np.random.RandomState(17)
    before = game.capture_rng_state()
    with game.dice.force(**{keyword: [high]}, strict=True):
        assert die(game.dice).value == high
        with pytest.raises(bb.ForcedRollExhausted, match=die.__name__):
            die(game.dice)
    assert game.capture_rng_state() == before
    with game.dice.force(**{keyword: [low]}):
        assert die(game.dice).value == low
        assert die(game.dice).value == die(reference).value
    assert die(game.dice).value == die(reference).value


@pytest.mark.parametrize("seed", [0, 17, 2**31 - 1])
def test_unforced_stream_matches_existing_randomstate_algorithm(seed):
    game = get_game_coin_toss(seed=seed)
    reference = np.random.RandomState(seed)
    assert type(game.rng) is np.random.RandomState
    for _ in range(25):
        for die, sides in ((bb.D3, 3), (bb.D6, 6), (bb.D8, 8), (bb.BBDie, 6)):
            expected = reference.randint(1, sides + 1)
            if die is bb.BBDie:
                expected = bb.BBDieResult(3 if expected == 6 else expected)
            assert die(game.dice).value == expected
        assert game.rng.choice(19) == reference.choice(19)
    np.testing.assert_equal(game.rng.get_state(), reference.get_state())


@pytest.mark.parametrize("die,keyword,low,high", DICE)
def test_string_dice_consume_game_queues_and_preserve_exhaustion_error(die, keyword, low, high):
    game = get_game_coin_toss()
    name = "bb" if die is bb.BBDie else die.__name__.lower()
    with game.dice.force(**{keyword: [low, high]}, strict=True):
        assert bb.DiceRoll.from_string(name, game.dice).dice[0].value == low
        assert bb.DiceRoll.from_string(name, game.dice).dice[0].value == high
        with pytest.raises(bb.ForcedRollExhausted, match=die.__name__):
            bb.DiceRoll.from_string(name, game.dice)


def _consume_rng_and_queues(game):
    values = [die(game.dice).value for _ in range(3) for die, *_ in DICE]
    values.extend(game.rng.normal(size=5))
    values.extend(game.rng.choice(100, size=7, replace=False))
    return values, game.capture_rng_state()


def test_capture_restores_full_rng_including_gaussian_cache_and_all_queues():
    game = get_game_coin_toss(seed=17)
    game.rng.normal()  # Leave the second Gaussian cached in RandomState.
    for die, _, low, high in DICE:
        game.dice.fix(die, low, high)
    captured = game.capture_rng_state()
    assert captured.rng_state[3] == 1
    first = _consume_rng_and_queues(game)
    game.set_seed(999)
    game.dice.clear()
    game.restore_rng_state(captured)
    assert game.capture_rng_state() == captured
    assert _consume_rng_and_queues(game) == first
    game.restore_rng_state(captured)
    assert _consume_rng_and_queues(game) == first  # The snapshot itself was not consumed.


def test_capture_inside_nested_context_restores_every_frame_and_expires_on_exit():
    game = get_game_coin_toss()
    game.dice.fix(bb.D6, 2)
    with game.dice.force(d6=[3]):
        with game.dice.force(d6=[4, 5], strict=True):
            captured = game.capture_rng_state()
            assert captured.queues[0][1] == (2,)
            assert captured.queues[1][1] == (3,)
            assert bb.D6(game.dice).value == 4
            game.restore_rng_state(captured)
            assert [bb.D6(game.dice).value for _ in range(2)] == [4, 5]
            with pytest.raises(bb.ForcedRollExhausted):
                bb.D6(game.dice)
        assert bb.D6(game.dice).value == 3
    assert bb.D6(game.dice).value == 2
    current = game.capture_rng_state()
    with pytest.raises(ValueError, match="same active forced-roll contexts"):
        game.restore_rng_state(captured)
    assert game.capture_rng_state() == current


@pytest.mark.parametrize("die,keyword,low,high", DICE)
def test_deepcopy_and_rng_transfer_do_not_share_mutable_queues(die, keyword, low, high):
    game = get_game_coin_toss()
    game.dice.fix(die, low, high)
    snapshot = game.capture_rng_state()
    branch = deepcopy(game)
    other = get_game_coin_toss(seed=99)
    other.restore_rng_state(snapshot)
    for copied in (branch, other):
        assert die(copied.dice).value == low
        copied.dice.fix(die, high)
        copied.rng.randint(100)
    assert game.capture_rng_state() == snapshot
    assert branch.dice.pending(die) == (high, high)
    branch.dice.clear(die)
    assert other.dice.pending(die) == (high, high)
    assert game.dice.pending(die) == (low, high)


def _movement_game():
    game, (player,) = get_custom_game_turn([(5, 5)], rerolls=1)
    game.set_seed(4)
    player.state.moves = player.get_ma()
    game.enable_forward_model()
    return game, player


def _play_movement(game, player, interleave=lambda: None):
    trace = []
    for action in (bb.Action(bb.ActionType.START_MOVE, player=player),
                   bb.Action(bb.ActionType.MOVE, position=game.get_square(6, 5)),
                   bb.Action(bb.ActionType.USE_REROLL),
                   bb.Action(bb.ActionType.MOVE, position=game.get_square(7, 5))):
        trace.append(semantic_action(game, action))
        game.step(action)
        interleave()
    assert player.position == game.get_square(7, 5)
    assert player.state.up and player.team.state.rerolls == 0
    assert game.has_report_of_type(bb.OutcomeType.FAILED_GFI)
    assert game.has_report_of_type(bb.OutcomeType.REROLL_USED)
    assert game.has_report_of_type(bb.OutcomeType.SUCCESSFUL_GFI)
    return trace, semantic_reports(game), game.capture_rng_state()


def test_game_b_event_trace_is_independent_of_game_a():
    b, player_b = _movement_game()
    b.dice.fix(bb.D6, 1)
    alone = _play_movement(b, player_b)
    interleaved, player = _movement_game()
    interleaved.dice.fix(bb.D6, 1)
    a = get_game_coin_toss(seed=999)
    for die, _, low, _ in DICE:
        a.dice.fix(die, low, low)

    def run_a():
        for die, *_ in DICE:
            die(a.dice)
        a.rng.choice(100)
        get_game_turn()

    assert _play_movement(interleaved, player, run_a) == alone
    assert not b.state.compare(interleaved.state)


def test_combined_checkpoint_replays_decisions_events_state_and_final_rng():
    game, player = _movement_game()
    with game.dice.force(d3=[3], d6=[1], d8=[8], block_dice=[bb.BBDieResult.PUSH]):
        checkpoint = game.capture_checkpoint()
        before = deepcopy(game)
        first = _play_movement(game, player)
        after = deepcopy(game)
        assert first[2].rng_state != checkpoint.rng_state.rng_state
        undone = game.restore_checkpoint(checkpoint)
        assert undone
        assert not game.state.compare(before.state)
        assert game.capture_rng_state() == checkpoint.rng_state
        assert _play_movement(game, player) == first
        assert not game.state.compare(after.state)
        assert game.get_step() == after.get_step()
        assert game.dice.pending(bb.D3) == (3,)
        assert game.dice.pending(bb.D8) == (8,)
        assert game.dice.pending(bb.BBDie) == (bb.BBDieResult.PUSH,)


def test_legacy_revert_and_forward_leave_rng_and_queues_advanced():
    game, player = _movement_game()
    game.dice.fix(bb.D6, 1)
    step = game.get_step()
    before = deepcopy(game)
    _play_movement(game, player)
    after = deepcopy(game)
    advanced_rng = game.capture_rng_state()
    undone = game.revert(step)
    assert not game.state.compare(before.state)
    assert game.capture_rng_state() == advanced_rng
    assert game.dice.pending(bb.D6) == ()
    game.forward(undone)
    assert not game.state.compare(after.state)
    assert game.capture_rng_state() == advanced_rng


def test_checkpoint_requires_enabled_own_ancestor_trajectory_and_live_context():
    game = get_game_coin_toss()
    with pytest.raises(RuntimeError, match="forward model"):
        game.capture_checkpoint()
    game, player = _movement_game()
    root = game.capture_checkpoint()
    foreign, _ = _movement_game()
    with pytest.raises(ValueError, match="another"):
        foreign.restore_checkpoint(root)
    game.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    future = game.capture_checkpoint()
    game.restore_checkpoint(root)
    with pytest.raises(ValueError, match="ancestor"):
        game.restore_checkpoint(future)
    game.step(bb.Action(bb.ActionType.START_MOVE, player=player))
    assert game.get_step() == future.step
    with pytest.raises(ValueError, match="ancestor"):
        game.restore_checkpoint(future)  # Same step count, different branch.
    with game.dice.force(d6=[1]):
        expired = game.capture_checkpoint()
    before = deepcopy(game)
    with pytest.raises(ValueError, match="contexts"):
        game.restore_checkpoint(expired)
    assert not game.state.compare(before.state)
    assert game.capture_rng_state() == before.capture_rng_state()


def test_game_seed_and_checkpoint_do_not_seed_or_rewind_policy():
    game, _ = _movement_game()
    policy = RandomBot("policy", seed=123)
    reference = RandomBot("reference", seed=123)
    game.home_agent = policy
    checkpoint = game.capture_checkpoint()
    for _ in range(8):
        assert semantic_action(game, policy.act(game)) == semantic_action(game, reference.act(game))
    assert game.capture_rng_state() == checkpoint.rng_state
    policy_state = deepcopy(policy.rnd.get_state())
    game.dice.fix(bb.D6, 6)
    game.set_seed(42)
    assert game.dice.pending(bb.D6) == (6,)
    np.testing.assert_equal(policy.rnd.get_state(), policy_state)
    game.restore_checkpoint(checkpoint)
    np.testing.assert_equal(policy.rnd.get_state(), policy_state)
    assert game.capture_rng_state() == checkpoint.rng_state


def test_hidden_competition_copy_does_not_expose_or_mutate_test_queues():
    game = get_game_coin_toss()
    game.dice.fix(bb.D6, 6)
    original_source = game.dice
    before = game.capture_rng_state()
    with pytest.raises(RuntimeError, match="copy failed"):
        with game.hide_agents_and_rng():
            assert game.dice is not original_source
            assert game.dice.pending(bb.D6) == ()
            copied = deepcopy(game)
            copied.dice.fix(bb.D6, 1)
            raise RuntimeError("copy failed")
    assert game.dice is original_source
    assert game.capture_rng_state() == before
    assert copied.dice.pending(bb.D6) == (1,)


def test_strict_test_helper_checks_consumption_and_cleans_up_failures():
    game = get_game_coin_toss()
    with pytest.raises(AssertionError, match="Not all fixed D6"):
        with only_fixed_rolls(game, d6=[1, 2]):
            assert bb.D6(game.dice).value == 1
    assert game.dice.pending(bb.D6) == ()
    with pytest.raises(bb.ForcedRollExhausted):
        with only_fixed_rolls(game):
            bb.D6(game.dice)
    with pytest.raises(RuntimeError, match="body failed"):
        with only_fixed_rolls(game, d6=[6]):
            raise RuntimeError("body failed")
    assert game.dice.pending(bb.D6) == ()
    with pytest.raises(AssertionError, match="Non-dice randomness"):
        with only_fixed_rolls(game):
            game.rng.randint(100)


def test_selected_block_result_does_not_consume_a_future_forced_roll():
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)])
    game.dice.fix(bb.BBDie, bb.BBDieResult.PUSH, bb.BBDieResult.DEFENDER_DOWN)
    game.step(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    game.step(bb.Action(bb.ActionType.BLOCK, position=defender.position))
    game.step(bb.Action(bb.ActionType.SELECT_PUSH))
    selected = [r for r in game.state.reports if r.outcome_type == bb.OutcomeType.ACTION_SELECT_DIE]
    assert selected[-1].rolls[0].dice[0].value == bb.BBDieResult.PUSH
    assert game.dice.pending(bb.BBDie) == (bb.BBDieResult.DEFENDER_DOWN,)


def _process_trace(seed):
    # Construct games locally; this does not test a persistent snapshot format.
    game = get_game_coin_toss(seed=seed)
    other = get_game_coin_toss(seed=seed + 1)
    for die, _, low, high in DICE:
        game.dice.fix(die, high)
        other.dice.fix(die, low)
    result = []
    for _ in range(4):
        for die, *_ in DICE:
            result.append(die(game.dice).to_json())
            die(other.dice)
    return result, game.capture_rng_state()


@pytest.mark.parametrize("method", [m for m in ("spawn", "fork") if m in multiprocessing.get_all_start_methods()])
def test_process_execution_is_independent(method):
    expected = [_process_trace(seed) for seed in (0, 17)]
    parent = get_game_coin_toss()
    for die, _, low, _ in DICE:
        parent.dice.fix(die, low, low)
    before = parent.capture_rng_state()
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context(method)) as pool:
        futures = [pool.submit(_process_trace, seed) for seed in (0, 17)]
        assert [future.result(timeout=30) for future in futures] == expected
    assert parent.capture_rng_state() == before
