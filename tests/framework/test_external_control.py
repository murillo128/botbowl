"""One input per decision, independent of presentation flags and policy scheduling."""
from copy import deepcopy
import pickle

import pytest

import botbowl as bb
from tests.baseline import progress_action
from tests.util import get_custom_game_turn


class Spy(bb.Agent):
    def __init__(self, name, human=False):
        super().__init__(name, human=human, agent_id=name)
        self.starts = 0
        self.ends = 0

    def new_game(self, game, team):
        self.starts += 1

    def end_game(self, game):
        self.ends += 1

    def act(self, game):
        pytest.fail("External control called an agent policy")


def fresh(external=True, human=False, fast=True):
    config = bb.load_config("gym-3")
    config.rounds = 1
    config.kick_off_table = False
    config.pathfinding_enabled = False
    config.fast_mode = fast
    rules = bb.load_rule_set(config.ruleset)
    return bb.Game("external", bb.load_team_by_filename("human", rules, board_size=3),
                   bb.load_team_by_filename("human", rules, board_size=3),
                   Spy("home", human), Spy("away", human), config, seed=17,
                   external_control=external)


def pair(game):
    game.external_control = True
    game.home_agent = Spy("home", human=True)
    game.away_agent = Spy("away", human=True)
    other = deepcopy(game)
    other.home_agent.human = False
    other.away_agent.human = False
    return game, other


def choices(game):
    return [choice.to_json() for choice in game.get_available_actions()]


def assert_equivalent(left, right):
    assert choices(left) == choices(right)
    assert left.state.to_json(ignore_clocks=True) == right.state.to_json(ignore_clocks=True)
    assert left.get_procedure_names() == right.get_procedure_names()
    assert left.capture_rng_state() == right.capture_rng_state()
    assert (left.actor.agent_id if left.actor else None) == (right.actor.agent_id if right.actor else None)


def advance_pair(games, action=None):
    results = []
    for game in games:
        start = len(game.state.reports)
        if game.external_control:
            result = game.advance(action)
        else:
            game.step(action)
            result = bb.DecisionResult(game.actor, tuple(game.state.reports[start:]), game.state.game_over)
        assert result.events == tuple(game.state.reports[start:])
        assert result.terminal == game.state.game_over
        assert result.actor is (None if result.terminal else game.actor)
        results.append(result)
    for game, result in zip(games[1:], results[1:]):
        assert [event.to_json() for event in results[0].events] == [event.to_json() for event in result.events]
        assert_equivalent(games[0], game)
    return results[0]


@pytest.mark.parametrize("fast", [True, False])
def test_paired_setup_consecutive_decisions_and_terminal(fast):
    games = pair(fresh(fast=fast))
    for game in games:
        game.init()
        before = pickle.dumps(game)
        game.init()
        assert pickle.dumps(game) == before
    assert_equivalent(*games)
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.START_GAME]
    all_events = []
    setup_boundary = False
    for _ in range(100):
        game = games[0]
        if game.state.game_over:
            break
        actor = game.actor
        action = progress_action(game)
        result = advance_pair(games, action)
        all_events.extend(result.events)
        if action.action_type in (bb.ActionType.SETUP_FORMATION_SPREAD, bb.ActionType.SETUP_FORMATION_WEDGE):
            assert result.actor is actor
            assert bb.ActionType.END_SETUP in [c.action_type for c in game.get_available_actions()]
            setup_boundary = True
    assert setup_boundary
    assert games[0].state.game_over
    assert all_events == games[0].state.reports
    for game in games:
        assert game.actor is None
        assert not game.state.available_actions
        before = pickle.dumps(game)
        with pytest.raises(bb.InvalidActionError, match="game is over"):
            game.advance(bb.Action(bb.ActionType.START_GAME))
        assert game.advance() == bb.DecisionResult(None, (), True)
        game.init()
        game.step()
        assert pickle.dumps(game) == before
        for agent in (game.home_agent, game.away_agent):
            assert agent.starts == agent.ends == (0 if agent.human else 1)


def test_external_step_wrapper_never_calls_bots():
    game = fresh(fast=False)
    game.init()
    twin = deepcopy(game)
    assert game.step(bb.Action(bb.ActionType.START_GAME)) is None
    twin.advance(bb.Action(bb.ActionType.START_GAME))
    assert_equivalent(game, twin)


def test_legacy_slow_mode_still_advances_one_automatic_tick():
    game = fresh(external=False, human=True, fast=False)
    game.init()
    game.step(bb.Action(bb.ActionType.START_GAME))
    assert not game.state.available_actions
    before = len(game.state.reports)
    game.step()
    assert len(game.state.reports) > before
    assert not game.state.available_actions
    game.step()
    assert game.actor is not None


@pytest.mark.parametrize("action", [None, bb.Action(bb.ActionType.CONTINUE),
                                    bb.Action(bb.ActionType.PLACE_BALL),
                                    bb.Action(bb.ActionType.START_GAME, position=bb.Square(-1, 0)), {}])
@pytest.mark.parametrize("entry", ["advance", "step"])
def test_invalid_input_preserves_entire_game_and_caller(action, entry):
    game = fresh()
    game.init()
    game.enable_forward_model()
    game.replay = bb.Replay("external-rejection")
    game.dice.fix(bb.D6, 3)
    before = pickle.dumps(game), pickle.dumps(action)
    with pytest.raises(bb.InvalidActionError):
        getattr(game, entry)(action)
    assert (pickle.dumps(game), pickle.dumps(action)) == before


def movement(skills=(), rerolls=1):
    game, (player, opponent) = get_custom_game_turn([(5, 5)], [(6, 5)])
    player.extra_skills = list(skills)
    player.team.state.rerolls = rerolls
    return pair(game), player


@pytest.mark.parametrize("use", [True, False])
@pytest.mark.parametrize("legacy_human", [True, False])
def test_team_dodge_reroll_pair(use, legacy_human):
    games, player = movement()
    if legacy_human:
        legacy = deepcopy(games[0])
        legacy.external_control = False
        games += (legacy,)
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    for game in games:
        game.dice.fix(bb.D6, 1)
        # Declining falls over; armor is also held constant.
        for value in ([6] if use else [1, 1]):
            game.dice.fix(bb.D6, value)
    result = advance_pair(games, bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    assert result.actor is games[0].get_team_agent(player.team)
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.USE_REROLL, bb.ActionType.DONT_USE_REROLL]
    advance_pair(games, bb.Action(bb.ActionType.USE_REROLL if use else bb.ActionType.DONT_USE_REROLL))
    assert player.team.state.rerolls == (0 if use else 1)
    assert player.state.up == use


@pytest.mark.parametrize("skill", [bb.Skill.DODGE, bb.Skill.SURE_FEET, bb.Skill.SURE_HANDS])
@pytest.mark.parametrize("legacy_human", [True, False])
def test_automatic_skill_reroll_pair(skill, legacy_human):
    games, player = movement([skill])
    if legacy_human:
        legacy = deepcopy(games[0])
        legacy.external_control = False
        games += (legacy,)
    for game in games:
        p = game.get_player(player.player_id)
        if skill != bb.Skill.DODGE:
            game.remove(game.get_player_at(bb.Square(6, 5)))
        if skill == bb.Skill.SURE_FEET:
            p.state.moves = p.get_ma()
        if skill == bb.Skill.SURE_HANDS:
            game.get_ball().move_to(bb.Square(5, 6))
            game.get_ball().is_carried = False
        game.set_available_actions()
        game.dice.fix(bb.D6, 1)
        game.dice.fix(bb.D6, 6)
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    result = advance_pair(games, bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    assert any(event.skill == skill for event in result.events)
    assert player.state.up
    assert player.team.state.rerolls == 1
    assert not games[0].has_report_of_type(bb.OutcomeType.REROLL_USED)
    assert not games[0].dice.pending(bb.D6)


def test_pro_decline_then_team_reroll_is_two_decisions():
    games, player = movement([bb.Skill.PRO])
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    for game in games:
        game.dice.fix(bb.D6, 1)
        game.dice.fix(bb.D6, 6)
    advance_pair(games, bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.USE_SKILL, bb.ActionType.DONT_USE_SKILL]
    rng_before = games[0].capture_rng_state()
    result = advance_pair(games, bb.Action(bb.ActionType.DONT_USE_SKILL))
    assert result.actor is games[0].get_team_agent(player.team)
    assert not result.events
    assert games[0].capture_rng_state() == rng_before
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.USE_REROLL, bb.ActionType.DONT_USE_REROLL]
    advance_pair(games, bb.Action(bb.ActionType.USE_REROLL))
    assert player.state.up and player.team.state.rerolls == 0
    assert player.can_use_skill(bb.Skill.PRO)


@pytest.mark.parametrize("success", [True, False])
def test_external_loner_consumes_team_reroll_once(success):
    games, player = movement([bb.Skill.LONER])
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    for game in games:
        for roll in ([1, 4, 6] if success else [1, 3, 1, 1]):
            game.dice.fix(bb.D6, roll)
    advance_pair(games, bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    result = advance_pair(games, bb.Action(bb.ActionType.USE_REROLL))
    assert player.team.state.rerolls == 0
    assert player.state.up == success
    assert sum(e.outcome_type == bb.OutcomeType.REROLL_USED for e in result.events) == 1
    assert not games[0].dice.pending(bb.D6)


def test_failed_automatic_skill_cannot_be_rerolled_again():
    games, player = movement([bb.Skill.DODGE])
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    for game in games:
        for roll in [1, 1, 1, 1]:
            game.dice.fix(bb.D6, roll)
    advance_pair(games, bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    assert not player.state.up
    assert player.team.state.rerolls == 1
    assert not games[0].has_report_of_type(bb.OutcomeType.REROLL_USED)
    assert sum(e.skill == bb.Skill.DODGE for e in games[0].state.reports) == 1
    assert not isinstance(games[0].get_procedure(), bb.Reroll)


def test_block_reroll_then_die_selection_is_same_actor_next_decision():
    games, attacker = movement()
    defender = games[0].get_player_at(bb.Square(6, 5))
    advance_pair(games, bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    for game in games:
        game.dice.fix(bb.BBDie, bb.BBDieResult.PUSH)
    advance_pair(games, bb.Action(bb.ActionType.BLOCK, position=defender.position))
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.USE_REROLL, bb.ActionType.DONT_USE_REROLL]
    result = advance_pair(games, bb.Action(bb.ActionType.DONT_USE_REROLL))
    assert result.actor == games[0].get_team_agent(attacker.team)
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.SELECT_PUSH]
    assert attacker.team.state.rerolls == 1
    advance_pair(games, bb.Action(bb.ActionType.SELECT_PUSH))


@pytest.mark.parametrize("pro_success", [True, False])
def test_pro_resolution_does_not_ask_again(pro_success):
    games, player = movement([bb.Skill.PRO])
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    for game in games:
        for roll in ([1, 6, 6] if pro_success else [1, 1, 6, 6]):
            game.dice.fix(bb.D6, roll)
    advance_pair(games, bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    advance_pair(games, bb.Action(bb.ActionType.USE_SKILL))
    if not pro_success:
        assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.USE_REROLL, bb.ActionType.DONT_USE_REROLL]
        advance_pair(games, bb.Action(bb.ActionType.USE_REROLL))
    assert games[0].has_report_of_type(bb.OutcomeType.SUCCESSFUL_PRO)
    assert games[0].has_report_of_type(bb.OutcomeType.SUCCESSFUL_DODGE)
    assert player.team.state.rerolls == (1 if pro_success else 0)
    assert not player.can_use_skill(bb.Skill.PRO)
    assert not games[0].dice.pending(bb.D6)
    assert not isinstance(games[0].get_procedure(), bb.Reroll)


def test_opponent_pro_owns_decision_during_other_teams_turn():
    game, (_, defender) = get_custom_game_turn([(5, 5)], [(8, 5)])
    defender.extra_skills = [bb.Skill.PRO]
    ball = game.get_ball()
    ball.move_to(defender.position)
    ball.is_carried = False
    bb.Catch(game, defender, ball)
    game.set_available_actions()
    games = pair(game)
    for current in games:
        for value in [1, 6, 6]:
            current.dice.fix(bb.D6, value)
    result = advance_pair(games)
    assert games[0].active_team == defender.team
    assert games[0].active_team != games[0].state.current_team
    assert result.actor == games[0].get_team_agent(defender.team)
    assert all(c.team == defender.team for c in games[0].get_available_actions())
    assert not games[0].can_use_reroll(defender.team)
    advance_pair(games, bb.Action(bb.ActionType.USE_SKILL))
    assert ball.is_carried
    assert games[0].has_report_of_type(bb.OutcomeType.SUCCESSFUL_CATCH)


def test_defender_block_die_decision_during_opponents_turn():
    game, (attacker, defender) = get_custom_game_turn([(5, 5)], [(6, 5)])
    defender.extra_st = 1
    games = pair(game)
    advance_pair(games, bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    for current in games:
        current.dice.fix(bb.BBDie, bb.BBDieResult.PUSH)
        current.dice.fix(bb.BBDie, bb.BBDieResult.PUSH)
    result = advance_pair(games, bb.Action(bb.ActionType.BLOCK, position=defender.position))
    assert result.actor == games[0].get_team_agent(defender.team)
    assert games[0].state.current_team == attacker.team
    advance_pair(games, bb.Action(bb.ActionType.SELECT_PUSH))
    assert games[0].active_team == attacker.team
    assert isinstance(games[0].get_procedure(), bb.Push)


def test_only_end_player_turn_is_still_an_external_decision():
    games, player = movement()
    for game in games:
        game.get_player(player.player_id).state.moves = player.get_ma() + 2
    advance_pair(games, bb.Action(bb.ActionType.START_MOVE, player=player))
    # Undo is legal immediately after selection; once declined by a stand-up
    # action on a prone player there is only END_PLAYER_TURN.
    for game in games:
        p = game.get_player(player.player_id)
        p.state.up = False
        game.get_procedure().can_undo = False
        game.set_available_actions()
    advance_pair(games, bb.Action(bb.ActionType.STAND_UP))
    assert [c.action_type for c in games[0].get_available_actions()] == [bb.ActionType.END_PLAYER_TURN]
    assert games[0].active_team == player.team
    advance_pair(games, bb.Action(bb.ActionType.END_PLAYER_TURN))
    assert isinstance(games[0].get_procedure(), bb.Turn)


class Deterministic(Spy):
    def __init__(self, name):
        super().__init__(name)
        self.inputs = []

    def act(self, game):
        action = progress_action(game)
        self.inputs.append((choices(game), action.to_json()))
        return action


def test_explicit_two_policy_driver_matches_legacy_complete_trace():
    legacy = fresh(external=False)
    legacy.home_agent = Deterministic("home")
    legacy.away_agent = Deterministic("away")
    external = deepcopy(legacy)
    external.external_control = True
    # Capture legacy calls to the public boundary, including implicit START_GAME.
    legacy_trace = []
    advance = legacy.advance

    def capture(action=None):
        before = (legacy.actor.agent_id, choices(legacy), action.to_json())
        result = advance(action)
        legacy_trace.append((before, [e.to_json() for e in result.events],
                             result.actor.agent_id if result.actor else None, result.terminal,
                             deepcopy(legacy.state.to_json(ignore_clocks=True))))
        return result

    legacy.advance = capture
    legacy.init()
    assert legacy.state.game_over
    external.init()
    driver = bb.PolicyDriver(external, {
        external.state.home_team.team_id: external.home_agent.act,
        external.state.away_team.team_id: external.away_agent.act,
    })
    for expected in legacy_trace:
        result = driver.run(max_decisions=1)
        trace = driver.trace[-1]
        assert ((trace.actor_id, list(trace.choices), trace.action), list(trace.events),
                trace.next_actor_id, trace.terminal,
                external.state.to_json(ignore_clocks=True)) == expected
        assert result.terminal == trace.terminal
    assert_equivalent(legacy, external)
    assert len(driver.trace) == len(legacy_trace)
    before = deepcopy(driver.trace)
    driver.run()
    external.state.reports.clear()
    assert driver.trace == before
    for game in (legacy, external):
        for agent in (game.home_agent, game.away_agent):
            assert agent.starts == agent.ends == 1


def test_driver_pauses_and_rejects_without_trace_mutation():
    game = fresh()
    game.init()
    driver = bb.PolicyDriver(game, {})
    before = pickle.dumps(game)
    assert driver.run().actor is game.actor
    assert driver.run(max_decisions=0).actor is game.actor
    assert pickle.dumps(game) == before
    assert not driver.trace
    driver.policies[game.active_team.team_id] = lambda game: bb.Action(bb.ActionType.PLACE_BALL)
    with pytest.raises(bb.InvalidActionError):
        driver.run()
    assert pickle.dumps(game) == before
    assert not driver.trace


def test_rejection_at_reroll_preserves_clock_rng_stack_and_replay():
    games, player = movement()
    game = games[1]
    game.enable_forward_model()
    game.replay = bb.Replay("external-reroll")
    game.add_primary_clock(game.active_team)
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    game.dice.fix(bb.D6, 1)
    game.dice.fix(bb.D6, 6)
    game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 6)))
    assert isinstance(game.get_procedure(), bb.Reroll)
    action = bb.Action(bb.ActionType.MOVE, position=bb.Square(5, 7))
    before = pickle.dumps(game), pickle.dumps(action)
    with pytest.raises(bb.InvalidActionError):
        game.advance(action)
    assert (pickle.dumps(game), pickle.dumps(action)) == before
