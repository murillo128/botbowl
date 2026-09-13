"""Logical boundaries from real procedures, independent of property tracing."""
from copy import deepcopy
from dataclasses import asdict
import json
import pickle

import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.actions import ActionControl, PositionV1, StaleDecisionError
from botbowl.lab.timeline import Timeline, TimelineContext
from tests.baseline import Scenario, place_players, progress_action
from tests.framework.test_external_control import assert_equivalent, fresh as external_game
from tests.framework.test_forced_action import FakeTime
from tests.lab.test_semantic_actions import semantic_game


def fresh(size=1, pathfinding=False, rounds=1):
    game = semantic_game(size, pathfinding)
    game.config.rounds = rounds
    Timeline(game, episode_id='timeline-test')
    game.init()
    return game


def until(game, predicate):
    for _ in range(180):
        if predicate(game):
            return
        assert not game.state.game_over
        game.advance(progress_action(game))
    pytest.fail('bounded scenario did not reach its boundary')


def turn(size=3, pathfinding=False, rounds=1):
    game = fresh(size, pathfinding, rounds)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    return game


def players(game, own, opponents=(), ball=None):
    # The fixture edits a microposition; retain the real episode report prefix.
    reports = list(game.state.reports)
    result = place_players(Scenario(game, 17, game.config.pitch_max), own, opponents, ball)
    game.state.reports.extend(reports)
    return result


def phase(game, kind):
    return [event for event in game.timeline.events if event.kind == kind]


def assert_integrity(game):
    timeline = game.timeline
    assert [e.context.event_seq for e in timeline.events] == list(range(1, timeline.context.event_seq + 1))
    assert [d.after.decision_seq for d in timeline.decisions] == list(range(1, timeline.context.decision_seq + 1))
    cursor = 1
    for decision in timeline.decisions:
        assert decision.before.decision_seq + 1 == decision.after.decision_seq
        assert decision.actor_id == decision.team_id == decision.action.actor_id
        assert decision.event_start == cursor == decision.before.event_seq + 1
        assert decision.event_stop == decision.after.event_seq + 1
        assert list(decision.events) == list(timeline.events[cursor - 1:decision.event_stop - 1])
        assert all(e.decision_seq == decision.after.decision_seq for e in decision.events)
        assert decision.status == 'resolved'
        cursor = decision.event_stop
    assert cursor == timeline.context.event_seq + 1
    reports = [e.data for e in timeline.events if e.kind == 'report']
    assert [e['outcome_type'] for e in reports] == [e.outcome_type.name for e in game.state.reports]
    assert len(reports) == len(game.state.reports)
    assert json.loads(json.dumps(timeline.to_json()))['context'] == timeline.context.to_json()


@pytest.mark.parametrize('forward_model', [False, True])
def test_initial_setup_turn_half_terminal_and_exact_event_ranges(forward_model):
    game = fresh()
    timeline = game.timeline
    assert timeline.context == TimelineContext('timeline-test', 'root')
    if forward_model:
        game.enable_forward_model()
    initial = pickle.dumps(game)
    game.init()
    assert pickle.dumps(game) == initial
    start = game.advance(bb.Action(bb.ActionType.START_GAME)).decisions[0]
    assert start.before == TimelineContext('timeline-test', 'root')
    assert start.actor_id == start.next_actor_id == 'away'
    assert start.after.decision_seq == 1
    assert [e.data['outcome_type'] for e in start.events] == [
        'GAME_STARTED', 'WEATHER_NICE', 'TEAM_SPECTATORS', 'TEAM_SPECTATORS', 'SPECTATORS', 'FAME', 'FAME']
    assert start.after.drive_seq is start.after.team_turn_seq is start.after.activation_seq is None
    until(game, lambda g: isinstance(g.get_procedure(), proc.Setup))
    assert timeline.context.half == timeline.context.drive_seq == 1
    assert timeline.context.round == 0
    assert timeline.context.team_turn_seq is timeline.context.activation_seq is None
    setup = game.advance(progress_action(game)).decisions[0]
    assert setup.actor_id == setup.next_actor_id
    assert setup.before.drive_seq == setup.after.drive_seq == 1
    until(game, lambda g: g.state.game_over)
    assert [(e.context.half, e.context.round) for e in phase(game, 'half_started')] == [(1, 0), (2, 0)]
    assert [(e.context.half, e.context.round) for e in phase(game, 'round_started')] == [(1, 1), (2, 1)]
    assert [e.context.drive_seq for e in phase(game, 'drive_started')] == [1, 2]
    assert len(phase(game, 'drive_ended')) == 2
    assert [e.context.team_turn_seq for e in phase(game, 'team_turn_started')] == [1, 2, 3, 4]
    assert len(phase(game, 'team_turn_ended')) == 4
    assert timeline.context.activation_seq is None
    assert timeline.decisions[-1].terminal and timeline.decisions[-1].next_actor_id is None
    assert timeline.events[-1].data['outcome_type'] == 'END_OF_GAME_DRAW'
    assert_integrity(game)
    assert game.get_step() != timeline.context.decision_seq != timeline.context.event_seq
    before = pickle.dumps(game)
    assert game.advance().decisions == ()
    with pytest.raises(bb.InvalidActionError):
        game.advance(bb.Action(bb.ActionType.END_TURN))
    assert pickle.dumps(game) == before


def test_same_team_activation_undo_and_trait_failure_start_exactly_once():
    game = turn()
    player = players(game, [(5, 5)])[0]
    start = game.advance(bb.Action(bb.ActionType.START_MOVE, player=player)).decisions[0]
    assert start.before.activation_seq is None
    assert start.after.activation_seq == 1
    assert start.events[0].kind == 'activation_started'
    undo = game.advance(bb.Action(bb.ActionType.UNDO)).decisions[0]
    assert start.actor_id == undo.actor_id == undo.next_actor_id
    assert undo.before.activation_seq == undo.after.activation_seq == 1
    assert [e.data['reason'] for e in phase(game, 'activation_ended')] == ['undo']
    player.extra_skills = [bb.Skill.BONE_HEAD]
    with game.dice.force(d6=[1], strict=True):
        failed = game.advance(bb.Action(bb.ActionType.START_MOVE, player=player)).decisions[0]
    assert failed.after.activation_seq == 2
    assert failed.events[0].kind == 'activation_started'
    assert any(e.data.get('outcome_type') == 'FAILED_BONE_HEAD' for e in failed.events)
    assert len(phase(game, 'activation_started')) == 2
    assert_integrity(game)


@pytest.mark.parametrize('automatic', [False, True])
def test_rerolls_share_activation_and_automatic_consequences_share_decision(automatic):
    game = turn()
    player, _ = players(game, [(3, 3)], [(4, 3)])
    player.extra_skills = [bb.Skill.DODGE if automatic else bb.Skill.PRO]
    player.team.state.rerolls = 1
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    before = game.timeline.context
    with game.dice.force(d6=[1, 6], strict=True):
        moved = game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4))).decisions[0]
        if not automatic:
            declined = game.advance(bb.Action(bb.ActionType.DONT_USE_SKILL)).decisions[0]
            assert declined.events == () and declined.event_start == declined.event_stop
            assert declined.before.event_seq == declined.after.event_seq
            assert declined.after.decision_seq == moved.after.decision_seq + 1
            reroll = game.advance(bb.Action(bb.ActionType.USE_REROLL)).decisions[0]
            assert moved.actor_id == declined.actor_id == reroll.actor_id
            assert reroll.after.decision_seq == before.decision_seq + 3
        else:
            assert moved.after.decision_seq == before.decision_seq + 1
            assert any(e.data.get('outcome_type') == 'SKILL_USED' for e in moved.events)
    assert player.position == bb.Square(3, 4) and player.state.up
    assert game.timeline.context.activation_seq == before.activation_seq == 1
    assert game.timeline.context.team_turn_seq == before.team_turn_seq
    assert_integrity(game)


def test_defender_decides_inside_attacking_team_activation():
    game = turn()
    attacker, defender = players(game, [(5, 5)], [(6, 5)])
    attacker.extra_st = 1 - attacker.get_st()
    game.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    with game.dice.force(block_dice=[bb.BBDieResult.PUSH] * 3, strict=True):
        block = game.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position)).decisions[0]
    selection = game.advance(bb.Action(bb.ActionType.SELECT_PUSH)).decisions[0]
    assert block.next_actor_id == selection.actor_id != block.actor_id
    assert game.state.current_team is attacker.team
    assert selection.before.activation_seq == selection.after.activation_seq == 1
    assert selection.before.team_turn_seq == selection.after.team_turn_seq == block.before.team_turn_seq
    assert_integrity(game)


@pytest.mark.parametrize('terminal', [False, True])
def test_touchdown_closes_drive_and_starts_only_an_actual_next_drive(terminal):
    game = turn(rounds=1 if terminal else 2)
    if terminal:
        until(game, lambda g: type(g.get_procedure()) is proc.Turn and g.state.half == 2
              and g.get_opp_team(g.active_team).state.turn == 1)
    team = game.active_team
    end_x = game.get_opp_endzone_x(team)
    near_x = end_x + (1 if end_x == 1 else -1)
    player = players(game, [(near_x, 5)], ball=(near_x, 5))[0]
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    before = game.timeline.context
    result = game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(end_x, 5)))
    decision = result.decisions[0]
    assert sum(e.data.get('outcome_type') == 'TOUCHDOWN' for e in decision.events) == 1
    assert sum(e.kind == 'drive_ended' for e in decision.events) == 1
    assert sum(e.kind == 'activation_ended' for e in decision.events) == 1
    assert sum(e.kind == 'team_turn_ended' for e in decision.events) == 1
    assert result.terminal == terminal
    assert decision.after.drive_seq == before.drive_seq + (0 if terminal else 1)
    if not terminal:
        assert isinstance(game.get_procedure(), proc.Setup)
        assert decision.after.team_turn_seq == before.team_turn_seq
    assert_integrity(game)


@pytest.mark.parametrize('kind', ['blitz', 'quick_snap'])
def test_kickoff_special_turns_are_real_team_turns_without_advancing_round(kind):
    game = fresh()
    until(game, lambda g: isinstance(g.get_procedure(), proc.PlaceBall))
    before = game.timeline.context
    with game.dice.force(d6=[1, 5, 5 if kind == 'blitz' else 4], d8=[1], strict=True):
        game.advance(progress_action(game))
    assert game.timeline.context.team_turn_seq == 1 and before.team_turn_seq is None
    assert game.timeline.context.round == before.round == 0
    assert phase(game, 'team_turn_started')[-1].data['turn_kind'] == kind
    game.advance(bb.Action(bb.ActionType.END_TURN))
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    assert game.timeline.context.team_turn_seq == 2
    assert game.timeline.context.round == 1
    assert_integrity(game)


def normalized_decisions(records):
    return [{k: v for k, v in record.to_json().items() if k not in ('macro_id', 'primitive_order')}
            for record in records]


@pytest.mark.parametrize('kind', ['formation', 'route'])
def test_macro_and_policy_driver_equal_primitive_decisions_and_events(kind):
    game = fresh(pathfinding=kind == 'route')
    if kind == 'formation':
        until(game, lambda g: isinstance(g.get_procedure(), proc.Setup))
    else:
        until(game, lambda g: type(g.get_procedure()) is proc.Turn)
        player = players(game, [(2, 2)])[0]
        game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    control = ActionControl(game)
    macros = control.legal_actions().macros
    macro = next(m for m in macros if m.to_json()['kind'] == kind
                 and (kind == 'formation' or m.type == 'MOVE' and len(m.path) == 2))
    plan = control._formation_plan(macro) if kind == 'formation' else control._route_plan(macro)
    manual, driven = deepcopy(game), deepcopy(game)
    expected = [manual.advance(action).decisions[0] for action in plan]
    actions = iter(plan)
    policy = lambda g: next(actions)
    driver = bb.PolicyDriver(driven, {t.team_id: policy for t in driven.state.teams})
    aggregated = driver.run(max_decisions=len(plan))
    result = control.execute_macro(macro)
    assert result.status == 'completed'
    assert len(aggregated.decisions) == len(result.steps) == len(plan)
    assert normalized_decisions(expected) == normalized_decisions(aggregated.decisions)
    assert normalized_decisions(expected) == normalized_decisions([step.decision for step in result.steps])
    for order, step in enumerate(result.steps):
        assert step.decision.macro_id == macro.macro_id and step.decision.primitive_order == order
        assert step.action == step.decision.action
    parent = game.timeline.to_json()['macros'][-1]
    assert parent['decision_seqs'] == [d.after.decision_seq for d in expected]
    assert parent['interruption'] is None
    assert game.timeline.events == manual.timeline.events == driven.timeline.events
    assert game.state.to_json(ignore_clocks=True) == manual.state.to_json(ignore_clocks=True)
    assert game.capture_rng_state() == manual.capture_rng_state() == driven.capture_rng_state()
    assert_integrity(game)


@pytest.mark.parametrize('boundary', ['reroll', 'defender', 'terminal'])
def test_macro_records_primitive_and_interruption_without_answering_prompt(boundary):
    game = turn(size=1 if boundary != 'terminal' else 3, pathfinding=True)
    if boundary == 'terminal':
        until(game, lambda g: type(g.get_procedure()) is proc.Turn and g.state.half == 2
              and g.get_opp_team(g.active_team).state.turn == 1)
        end_x = game.get_opp_endzone_x(game.active_team)
        near_x = end_x + (1 if end_x == 1 else -1)
        player = players(game, [(near_x, 5)], ball=(near_x, 5))[0]
    elif boundary == 'defender':
        player, _ = players(game, [(2, 2)], [(3, 2)])
        player.extra_st = 1 - player.get_st()
    else:
        player = players(game, [(2, 2)])[0]
    game.advance(bb.Action(bb.ActionType.START_BLITZ if boundary == 'defender' else bb.ActionType.START_MOVE,
                           player=player))
    if boundary == 'reroll':
        player.state.moves = player.get_ma()
        player.team.state.rerolls = 1
        game.set_available_actions()
    control = ActionControl(game)
    route = next(m for m in control.legal_actions().macros if m.to_json()['kind'] == 'route'
                 and m.type == ('BLOCK' if boundary == 'defender' else 'MOVE')
                 and len(m.path) == 1
                 and (boundary != 'terminal' or m.path[-1] == PositionV1(end_x, 5)))
    with game.dice.force(d6=[1] if boundary == 'reroll' else [],
                         block_dice=[bb.BBDieResult.PUSH] * 3 if boundary == 'defender' else [], strict=True):
        result = control.execute_macro(route)
    assert result.interruption == {'reroll': 'unplanned_decision', 'defender': 'actor_changed',
                                   'terminal': 'terminal'}[boundary]
    assert len(result.steps) == 1
    parent = game.timeline.to_json()['macros'][-1]
    assert parent['interruption']['reason'] == result.interruption
    assert parent['interruption']['next_order'] == 1
    assert parent['decision_seqs'] == [result.steps[0].decision.after.decision_seq]
    snapshot = game.timeline.to_json()
    route.path.append(PositionV1(999, 999))
    result.steps[0].action.options.path.clear()
    assert game.timeline.to_json() == snapshot
    assert_integrity(game)


def test_queries_render_rejected_and_repeated_requests_are_pure():
    game = fresh()
    game.enable_forward_model()
    control = ActionControl(game)
    request = control.request(control.legal_actions().actions[0])
    trace, rng, step = game.timeline.to_json(), game.capture_rng_state(), game.get_step()
    game.to_json()  # The legacy arena serializer populates its own render cache.
    assert (game.timeline.to_json(), game.capture_rng_state(), game.get_step()) == (trace, rng, step)
    before = pickle.dumps(game)
    for _ in range(2):
        game.get_available_actions()
        game.to_json()
        control.legal_actions().to_json()
        control.decode(request)
        game.timeline.to_json()
        with pytest.raises(bb.InvalidActionError):
            game.advance(bb.Action(bb.ActionType.END_TURN))
    assert pickle.dumps(game) == before
    game.advance(control.decode(request))
    before = pickle.dumps(game)
    with pytest.raises(StaleDecisionError):
        control.decode(request)
    assert pickle.dumps(game) == before
    data = game.timeline.to_json()
    data['decisions'][0]['action']['options']['unexpected'] = True
    assert data != game.timeline.to_json()


def assert_data(value):
    if isinstance(value, dict):
        assert all(type(key) is str for key in value)
        for item in value.values():
            assert_data(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_data(item)
    else:
        assert type(value) in (type(None), str, int, float, bool), type(value)


def test_instrumentation_preserves_full_game_rules_rng_reports_and_trajectory():
    game = semantic_game(3)
    plain = deepcopy(game)
    Timeline(game)
    for candidate in (game, plain):
        candidate.init()
        candidate.enable_forward_model()
    for _ in range(180):
        if game.state.game_over:
            break
        action = progress_action(game)
        result, baseline = game.advance(action), plain.advance(action)
        assert [e.to_json() for e in result.events] == [e.to_json() for e in baseline.events]
        assert game.state.to_json(ignore_clocks=True) == plain.state.to_json(ignore_clocks=True)
        assert game.capture_rng_state() == plain.capture_rng_state()
        assert game.get_step() == plain.get_step()
    assert game.state.game_over
    assert_data(game.timeline.to_json())
    assert_data(asdict(game.timeline.capture()))


@pytest.mark.parametrize('human', [False, True])
@pytest.mark.parametrize('boundary', ['coin_toss', 'setup', 'turn', 'activation'])
def test_refresh_clock_forcing_keeps_events_without_coach_decisions(human, boundary):
    game = external_game(human=human)  # Both seats fail if their act() is called.
    game.time_source = FakeTime()
    game.config.competition_mode = True
    plain = deepcopy(game)
    Timeline(game, episode_id='clock-test')
    for candidate in (game, plain):
        candidate.init()
        target = {'coin_toss': proc.CoinTossFlip, 'setup': proc.Setup,
                  'turn': proc.Turn, 'activation': proc.Turn}[boundary]
        until(candidate, lambda g: type(g.get_procedure()) is target)
        if boundary == 'activation':
            player = candidate.get_players_on_pitch(candidate.active_team)[0]
            candidate.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    assert_equivalent(game, plain)
    timeline = game.timeline
    before, decisions = timeline.context, timeline.decisions
    report_start = len(game.state.reports)
    for candidate in (game, plain):
        clock = candidate.get_agent_clock(candidate.actor)
        assert clock.is_primary == (boundary != 'coin_toss')
        assert not clock.is_done()
        candidate.time_source.now += 10000
        assert clock.is_done()
        candidate.refresh(max_steps=100)
        assert clock not in candidate.state.clocks
    assert_equivalent(game, plain)
    assert timeline.context.decision_seq == before.decision_seq
    assert timeline.decisions == decisions
    assert timeline.decisions_since(before.decision_seq) == ()
    events = timeline.events[before.event_seq:]
    assert events and all(event.decision_seq is None for event in events)
    assert all(event.context.decision_seq == before.decision_seq for event in events)
    assert [event.context.event_seq for event in events] == list(
        range(before.event_seq + 1, timeline.context.event_seq + 1))
    assert [event.data['outcome_type'] for event in events if event.kind == 'report'] == [
        report.outcome_type.name for report in game.state.reports[report_start:]]
    if boundary in ('turn', 'activation'):
        assert timeline.context.team_turn_seq == before.team_turn_seq + 1
        assert [event.kind for event in events if event.kind != 'report'] == (
            (['activation_ended'] if boundary == 'activation' else []) +
            ['team_turn_ended', 'team_turn_started'])
    assert timeline.to_json()['operational_errors'] == []

    # The same public submission remains a coach decision after clock forcing,
    # whether supplied directly or by PolicyDriver; unowned events stay outside it.
    driven = deepcopy(game)
    result = game.advance(progress_action(game))
    plain.advance(progress_action(plain))
    driver = bb.PolicyDriver(driven, {team.team_id: progress_action for team in driven.state.teams})
    aggregated = driver.run(max_decisions=1)
    assert len(result.decisions) == len(aggregated.decisions) == 1
    assert result.decisions == aggregated.decisions
    decision = result.decisions[0]
    assert decision.after.decision_seq == before.decision_seq + 1
    assert decision.event_start == events[-1].context.event_seq + 1
    assert all(event.decision_seq == decision.after.decision_seq for event in decision.events)
    assert timeline.events == driven.timeline.events
    assert_equivalent(game, plain)
    assert_equivalent(game, driven)


def test_trusted_checkpoint_double_and_real_checkpoint_restore_prefix_and_branch():
    game = fresh()
    game.enable_forward_model()
    checkpoint = game.capture_checkpoint()
    # A future codec owner can capture these two independent channels. This
    # double deliberately implements no encoding or engine snapshot format.
    double = (checkpoint, game.timeline.capture())
    actions = []
    for _ in range(5):
        actions.append(progress_action(game))
        game.advance(actions[-1])
    expected = game.timeline.to_json()
    state, rng = game.state.to_json(ignore_clocks=True), game.capture_rng_state()
    game.restore_checkpoint(double[0])
    game.timeline.restore(double[1])
    assert game.timeline.context == TimelineContext('timeline-test', 'root')
    for action in actions:
        game.advance(action)
    assert game.timeline.to_json() == expected
    assert game.state.to_json(ignore_clocks=True) == state and game.capture_rng_state() == rng
    branch_point = game.capture_checkpoint()
    prefix = game.timeline.to_json()
    old_count = game.timeline.context.decision_seq
    action = progress_action(game)
    game.advance(action)
    game.restore_checkpoint(branch_point)
    assert game.timeline.to_json() == prefix
    game.timeline.fork('alternative')
    assert game.timeline.to_json()['events'] == prefix['events']
    assert game.timeline.to_json()['decisions'] == prefix['decisions']
    decision = game.advance(action).decisions[0]
    assert decision.before.branch_id == decision.after.branch_id == 'alternative'
    assert decision.after.decision_seq == old_count + 1
    assert all(event.context.branch_id == 'alternative' for event in decision.events)
    with pytest.raises(ValueError):
        game.timeline.fork('root')
    assert_integrity(game)


def test_operational_limits_have_separate_records_and_resume_same_decision():
    game = fresh()
    with pytest.raises(bb.GameTruncatedError):
        game.advance(bb.Action(bb.ActionType.START_GAME), max_steps=0)
    assert game.timeline.context == TimelineContext('timeline-test', 'root')
    assert game.timeline.decisions == () and game.timeline.events == ()
    assert len(game.timeline.to_json()['operational_errors']) == 1
    with pytest.raises(bb.GameTruncatedError):
        game.advance(bb.Action(bb.ActionType.START_GAME), max_steps=1)
    assert game.timeline.context.decision_seq == 1
    assert game.timeline.decisions[-1].status == 'pending'
    assert [e.data['outcome_type'] for e in game.timeline.events] == ['GAME_STARTED']
    result = game.advance()
    assert result.decisions[0].after.decision_seq == 1
    assert game.timeline.decisions[-1].status == 'resolved'
    assert len(game.timeline.to_json()['operational_errors']) == 2
    assert_integrity(game)


def test_casualty_report_is_copied_once_with_its_causing_coach_decision():
    game = turn()
    attacker, defender = players(game, [(3, 3)], [(4, 3)])
    for player in (attacker, defender):
        reserves = game.get_reserves(player.team)
        if player in reserves:
            reserves.remove(player)
    defender.team.state.apothecaries = 0
    game.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    with game.dice.force(block_dice=[bb.BBDieResult.DEFENDER_DOWN], strict=True):
        game.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position))
    game.advance(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
    with game.dice.force(d6=[6, 6, 5, 5, 4], d8=[3], strict=True):
        game.advance(bb.Action(bb.ActionType.PUSH, position=bb.Square(5, 3)))
        decision = game.advance(bb.Action(bb.ActionType.FOLLOW_UP, position=attacker.position)).decisions[0]
    casualty = [e for e in decision.events if e.data.get('outcome_type') == 'CASUALTY']
    assert len(casualty) == 1
    assert casualty[0].decision_seq == decision.after.decision_seq
    assert casualty[0].data['team_id'] != decision.actor_id
    assert sum(r.outcome_type is bb.OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert_integrity(game)


def test_checkpoint_rejections_and_state_only_revert_preserve_timeline_semantics():
    game, other = fresh(), fresh()
    for candidate in (game, other):
        candidate.enable_forward_model()
    foreign = other.capture_checkpoint()
    local = game.capture_checkpoint()
    game.advance(bb.Action(bb.ActionType.START_GAME))
    before = pickle.dumps(game)
    with pytest.raises(ValueError):
        game.restore_checkpoint(foreign)
    assert pickle.dumps(game) == before
    trace = game.timeline.to_json()
    undone = game.revert(local.step)
    assert game.timeline.to_json() == trace
    game.forward(undone)
    assert game.timeline.to_json() == trace
    game.restore_checkpoint(local)
    assert game.timeline.to_json()['events'] == []


def test_macro_operational_failure_retains_parent_and_partial_child():
    game = turn(pathfinding=True)
    player = players(game, [(3, 3)])[0]
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    control = ActionControl(game)
    route = next(m for m in control.legal_actions().macros
                 if m.to_json()['kind'] == 'route' and m.type == 'MOVE' and len(m.path) == 1)
    before = game.timeline.context
    with pytest.raises(bb.GameTruncatedError):
        control.execute_macro(route, max_steps=1)
    trace = game.timeline.to_json()
    assert trace['macros'][-1]['interruption']['reason'] == 'operational_error'
    assert trace['macros'][-1]['decision_seqs'] == [before.decision_seq + 1]
    assert trace['decisions'][-1]['status'] == 'pending'
    assert trace['decisions'][-1]['macro_id'] == route.macro_id
    assert trace['macros'][-1]['interruption']['next_order'] == trace['decisions'][-1]['primitive_order'] == 0
    game.advance()
    assert game.timeline.decisions[-1].status == 'resolved'
    assert_integrity(game)


def test_finalizer_exception_keeps_terminal_decision_and_separate_failure(monkeypatch):
    game = turn()
    until(game, lambda g: type(g.get_procedure()) is proc.Turn and g.state.half == 2
          and g.get_opp_team(g.active_team).state.turn == 1)

    def fail():
        raise RuntimeError('operational finalizer failure')

    monkeypatch.setattr(game, '_end_game', fail)
    with pytest.raises(RuntimeError, match='operational finalizer'):
        game.advance(bb.Action(bb.ActionType.END_TURN))
    assert game.timeline.decisions[-1].terminal
    assert game.timeline.decisions[-1].status == 'resolved'
    assert game.timeline.to_json()['operational_errors'][-1]['error_type'] == 'RuntimeError'
    assert_data(game.timeline.to_json())
    assert_integrity(game)


def test_single_player_wrapper_returns_all_actors_and_render_is_pure():
    pytest.importorskip('gymnasium')
    from botbowl.ai.gymnasium_env import GymnasiumEnv, SinglePlayerWrapper
    env = GymnasiumEnv(size=1, max_decisions=100, record_timeline=True, render_mode='ansi')
    wrapped = SinglePlayerWrapper(env, progress_action, learner='home')
    _, info = wrapped.reset(seed=17)
    records = list(info['decisions'])
    assert records and all(d['actor_id'] == 'away' for d in records)
    while not (env._terminated or env._truncated):
        before = pickle.dumps(env.game)
        env.render()
        assert pickle.dumps(env.game) == before
        result = wrapped.step(env.encode_action(progress_action(env.game)))
        info = result[-1]
        assert list(info['decisions']) == [d for t in info['transitions'] for d in t['info']['decisions']]
        records.extend(info['decisions'])
    assert env._terminated
    assert records == env.game.timeline.to_json()['decisions']
    assert {d['actor_id'] for d in records} == {'home', 'away'}
    wrapped.close()
