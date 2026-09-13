"""Exact investigation controls; win rate is never a unit-test oracle."""
from dataclasses import asdict, replace
import pickle

import pytest
import botbowl as bb
import botbowl.core.pathfinding as pf
from examples.side_bias import (
    Episode, MirroredScripted, Scripted, action_record, dumps, make_game,
    paired_episodes, policy_seed, policy_view, reflect_action, reflect_square,
    run_episode, summarize,
)


def microgame(home=True, endzone=False, pathfinding=False):
    game = make_game(Episode(0, 0, 19, pathfinding=pathfinding))
    team = game.state.home_team if home else game.state.away_team
    opponent = game.get_opp_team(team)
    game.state.current_team = team
    team.state.turn = 1
    turn = bb.Turn(game, team, half=1, turn=1)
    turn.started = True
    def square(x, y):
        return game.get_square(x if home else game.arena.width - 1 - x, y)
    attacker = team.players[0]
    attacker.role = next(p.role for p in team.players if p.role.name == "Lineman")
    game.put(attacker, square(3 if endzone else 13, 8))
    defenders = []
    if not endzone:
        for player, x in zip(opponent.players, (12, 14)):
            player.role = attacker.role
            game.put(player, square(x, 8))
            defenders.append(player)
    ball = bb.Ball(attacker.position if endzone else square(7, 4), is_carried=endzone)
    game.state.pitch.balls.append(ball)
    game.set_available_actions()
    return game, attacker, defenders


def test_pair_swaps_identity_and_keeps_seed_and_receiver():
    specs = list(paired_episodes(2, seed=71, policy_a="random-legal", policy_b="scripted"))
    for left, right in zip(specs[::2], specs[1::2]):
        assert left.home == "A" and right.home == "B"
        assert replace(left, leg=1) == right
        g, h = make_game(left), make_game(right)
        assert g.home_agent.name == h.away_agent.name == "A"
        assert type(g.home_agent) is type(h.away_agent)
        assert g.home_agent.rnd.get_state()[1].tolist() == h.away_agent.rnd.get_state()[1].tolist()
        assert g.capture_rng_state() == h.capture_rng_state()
    assert [s.receiver for s in specs] == ["A", "A", "B", "B"]
    assert policy_seed(71, "A") != policy_seed(71, "B") != policy_seed(72, "B")


def test_fresh_rosters_agents_and_mutable_state():
    spec = Episode(0, 0, 1)
    games = make_game(spec), make_game(spec)
    for game in games:
        assert game.state.home_team is not game.state.away_team
        assert game.home_agent is not game.away_agent
    for team, other in zip(games[0].state.teams, games[1].state.teams):
        assert team is not other and team.state is not other.state
        for player, twin in zip(team.players, other.players):
            assert player is not twin and player.state is not twin.state
        team.state.score = 99
        team.players[0].state.used = True
        assert other.state.score == 0 and not other.players[0].state.used
    games[0].home_agent.actions.append(bb.Action(bb.ActionType.END_TURN))
    assert not games[1].home_agent.actions


@pytest.mark.parametrize("size", [1, 3, 5, 7, 11])
def test_reflection_geometry_exhaustive_pitch(size):
    game = make_game(Episode(0, 0, 0, size=size))
    home, away = game.state.teams
    for row in game.state.pitch.squares:
        for square in row:
            reflected = reflect_square(square, game.arena.width)
            assert reflect_square(reflected, game.arena.width) == square
            assert game.is_out_of_bounds(square) == game.is_out_of_bounds(reflected)
            assert game.is_team_side(square, home) == game.is_team_side(reflected, away)
            if not game.is_out_of_bounds(square):
                left = {reflect_square(s, game.arena.width) for s in game.get_adjacent_squares(square)}
                assert left == set(game.get_adjacent_squares(reflected))
                assert abs(square.x - game.get_opp_endzone_x(home)) == abs(
                    reflected.x - game.get_opp_endzone_x(away))


@pytest.mark.parametrize("pathfinding", [False, True])
def test_block_helpers_and_action_reflection(pathfinding):
    left, attacker, defenders = microgame(pathfinding=pathfinding)
    right, twin, opponents = microgame(home=False, pathfinding=pathfinding)
    for defender, opposite in zip(defenders, opponents):
        assert left.num_block_dice(attacker, defender) == right.num_block_dice(twin, opposite)
        assert left.get_block_probs(attacker, defender) == right.get_block_probs(twin, opposite)
        assert left.get_blitz_probs(attacker, attacker.position, defender) == right.get_blitz_probs(
            twin, twin.position, opposite)
        assert left.get_dodge_prob(attacker, left.get_square(13, 7)) == right.get_dodge_prob(
            twin, right.get_square(14, 7))
    action = bb.Action(bb.ActionType.BLOCK, position=defenders[0].position)
    assert action_record(reflect_action(reflect_action(action, left), left)) == action_record(action)
    assert reflect_action(action, left).position == opponents[0].position


@pytest.mark.parametrize("pathfinding", [False, True])
def test_reflected_legal_actions_include_cached_movement_paths(pathfinding):
    game, player, _ = microgame(endzone=True, pathfinding=pathfinding)
    for moving in (False, True):
        if moving:
            game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
        view = policy_view(game, True)
        assert view.get_player(player.player_id).position == reflect_square(player.position, game.arena.width)
        for choice in game.get_available_actions():
            for actor in choice.players or [None]:
                for square in choice.positions or [None]:
                    action = bb.Action(choice.action_type, player=actor, position=square)
                    assert game.is_action_allowed(action)
                    mapped = reflect_action(action, view)
                    assert view.is_action_allowed(mapped)
                    assert action_record(reflect_action(mapped, game)) == action_record(action)
        for row in view.state.pitch.squares:
            for square in row:
                assert view.get_square(square.x, square.y) is square


@pytest.mark.parametrize("pathfinding", [False, True])
def test_endzone_policy_and_helpers_are_mirrored(pathfinding):
    plans = []
    for home in (True, False):
        game, player, _ = microgame(home=home, endzone=True, pathfinding=pathfinding)
        assert game.get_distance_to_endzone(player) == 2
        path = pf.get_safest_path_to_endzone(game, player)
        assert path.prob == 1 and len(path.steps) == 2
        assert path.steps[-1].x == (1 if home else 26)
        stock = Scripted("stock-endzone")
        stock.new_game(game, player.team)
        stock._make_plan(game, player)
        assert stock.actions[0].action_type == bb.ActionType.START_MOVE
        assert stock.actions[-1].position.x == (1 if home else 26)
        policy = MirroredScripted("probe")
        policy.new_game(game, player.team)
        before = pickle.dumps(game)
        action = policy.act(game)
        assert pickle.dumps(game) == before
        assert action.action_type == bb.ActionType.START_MOVE and action.player is player
        # Persistent queue is in canonical home coordinates, on both sides.
        plans.append([action_record(a) for a in policy.policy.actions])
        game.move(player, game.get_square(1 if home else 26, 8))
        game.get_ball().move_to(player.position)
        assert game.is_touchdown(player)
    assert plans[0] == plans[1]


def test_block_tie_is_policy_order_effect_not_probability_asymmetry():
    stock, canonical = [], []
    for home in (True, False):
        game, player, defenders = microgame(home=home)
        policy = Scripted("probe")
        policy.new_game(game, player.team)
        selected = policy._get_safest_block(game)[1]
        stock.append(defenders.index(selected))
        view = policy_view(game, not home)
        policy.my_team = view.get_team_by_id(player.team.team_id)
        policy.opp_team = view.get_opp_team(policy.my_team)
        selected = policy._get_safest_block(view)[1]
        canonical.append([d.player_id for d in defenders].index(selected.player_id))
    assert stock == [1, 0]  # First physical-x neighbor wins an exact tie.
    assert canonical == [1, 1]


def test_canonical_player_selection_sorts_aligned_rolls():
    for home in (False, True):
        game, _, defenders = microgame(home=home)
        game.state.available_actions = [bb.ActionChoice(bb.ActionType.SELECT_PLAYER,
            team=defenders[0].team, players=list(reversed(defenders)), rolls=[4, 3])]
        view = policy_view(game, not home)
        choice = view.get_available_actions()[0]
        assert [p.nr for p in choice.players] == [p.nr for p in defenders]
        assert choice.rolls == [3, 4]


@pytest.mark.parametrize("home", [False, True])
def test_defender_die_choice_characterizes_scripted_policy_defect(home):
    game, attacker, defenders = microgame(home=home)
    game.remove(defenders[1])
    defender = defenders[0]
    defender.extra_st = 1  # Two uphill dice: defender chooses.
    game.state.home_team.state.rerolls = game.state.away_team.state.rerolls = 0
    game.set_available_actions()
    game.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    with game.dice.force():
        game.dice.fix(bb.BBDie, bb.BBDieResult.ATTACKER_DOWN)
        game.dice.fix(bb.BBDie, bb.BBDieResult.DEFENDER_DOWN)
        game.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position))
    assert game.actor is game.get_team_agent(defender.team)
    bot = Scripted("defender")
    bot.new_game(game, defender.team)
    # Known policy defect, intentionally characterized, NOT a rules correction.
    assert bot.act(game).action_type == bb.ActionType.SELECT_DEFENDER_DOWN
    assert game.is_action_allowed(bb.Action(bb.ActionType.SELECT_ATTACKER_DOWN))


@pytest.mark.parametrize("policy", ["random-legal", "scripted", "scripted-mirrored"])
@pytest.mark.parametrize("pathfinding", [False, True])
def test_small_episodes_repeat_and_balance_receiving(policy, pathfinding):
    specs = list(paired_episodes(1, policy_a=policy, policy_b=policy, pathfinding=pathfinding,
                                size=3, rounds=1, kickoff=False))
    rows = [run_episode(spec) for spec in specs]
    assert all(row["end_cause"] == "completed" for row in rows), rows
    assert [row["first_receiver_side"] for row in rows] == ["home", "away"]
    assert all(row["first_receiver"] == "A" for row in rows)
    assert dumps(rows) == dumps([run_episode(spec) for spec in specs])
    assert dumps(summarize(rows)) == dumps(summarize(list(reversed(rows))))


def test_draw_accounting_censoring_and_cluster_uncertainty():
    rows = []
    for spec in paired_episodes(3):
        outcome = ["home", "away", "draw"][spec.pair]
        rows.append(dict(spec=asdict(spec), backend="test", end_cause="completed",
                         outcome=outcome, first_receiver_side="home" if spec.leg == 0 else "away"))
    report = summarize(rows)[0]
    overall = report["strata"]["all"]
    assert (overall["home_wins"], overall["away_wins"], overall["draws"]) == (2, 2, 2)
    assert overall["home_points"] == 0.5
    assert overall["pairs"] == 3 and overall["episodes"] == 6
    assert overall["hoeffding95"] == [0, 1]
    rows[0].update(end_cause="decision_limit", outcome=None)
    report = summarize(rows)[0]
    assert report["excluded_episodes"] == 2
    assert report["strata"]["all"]["draws"] == 2
    assert run_episode(Episode(0, 0, 0, max_decisions=1))["outcome"] is None


def test_policy_exception_is_not_a_draw(monkeypatch):
    def fail(self, game):
        raise RuntimeError("controlled policy failure")
    monkeypatch.setattr(Scripted, "act", fail)
    record = run_episode(Episode(0, 0, 0))
    assert record["end_cause"] == "exception" and record["outcome"] is None
    assert record["error"]["message"] == "controlled policy failure"
