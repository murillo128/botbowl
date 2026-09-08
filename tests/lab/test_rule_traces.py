"""Deterministic EVAL-06 sites, graph integrity and engine noninterference."""
from copy import deepcopy
import hashlib
import json
import time

import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.recording import EpisodeReader, EpisodeRecorder, RecordingError
from botbowl.lab.records import EventV1, RecordError, encode_json, validate_episode
from botbowl.lab.rule_traces import RuleTrace, coverage_registry, validate_rule_graph
from botbowl.lab.timeline import Timeline
from tests.baseline import progress_action
from examples.a2c.a2c_env import A2C_Reward
from tests.lab.test_recording import complete
from tests.lab.test_semantic_actions import semantic_game
from tests.lab.test_timeline import players, until


def fresh(size=3, **options):
    game = semantic_game(size)
    trace = RuleTrace(game, **options)
    game.init()
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    return game, trace


def events(trace, emitter=None, outcome=None):
    return [e for e in trace.events if e['data']['coverage'] == 'instrumented'
            and (emitter is None or e['data']['emitter'] == emitter)
            and (outcome is None or e['data']['outcome']['type'] == outcome)]


def dice(event):
    return [d['result'] for r in event['data']['rolls'] for d in r['dice']]


def ancestor(trace, parent, child):
    by_id = {tuple(e['event_id']): e for e in trace.events}
    pending = list(child['data']['parent_event_ids'])
    seen = set()
    while pending:
        key = tuple(pending.pop())
        if key == tuple(parent['event_id']):
            return True
        if key not in seen:
            seen.add(key)
            pending.extend(by_id[key]['data']['parent_event_ids'])
    return False


def assert_trace(trace):
    assert trace.status['complete'], trace.status
    validate_rule_graph(trace.events, rules=trace.rules)
    # Every sports/phase report has exactly one contemporaneous trace anchor.
    assert [e['data']['event_ref'] for e in trace.events if e['data']['event_ref'] is not None] == [
        [e.context.episode_id, e.context.branch_id, e.context.event_seq] for e in trace.timeline.events]
    assert len([e for e in trace.events if e['kind'] == 'rule_decision']) == trace.timeline.context.decision_seq


def test_registry_is_closed_and_unsupported_fields_are_absent():
    registry = coverage_registry()
    engine = {n for n, c in vars(proc).items() if isinstance(c, type) and
              issubclass(c, proc.Procedure) and c is not proc.Procedure}
    assert set(registry['emitters']) == engine | {'external', 'timeline'}
    assert registry['emitters']['Leap']['status'] == 'unsupported'
    assert registry['emitters']['Dodge']['reports'] == ['SUCCESSFUL_DODGE', 'FAILED_DODGE']
    game, trace = fresh()
    unsupported = [e for e in trace.events if e['data']['coverage'] == 'unsupported']
    assert unsupported
    assert all(set(e['data']) == {'coverage', 'emitter', 'event_ref', 'decision_id', 'parent_event_ids'}
               for e in unsupported)
    registry['emitters']['Dodge']['reports'].clear()
    assert coverage_registry()['emitters']['Dodge']['reports']
    assert_trace(trace)


@pytest.mark.parametrize('weather,extra', [(bb.WeatherType.NICE, 0), (bb.WeatherType.BLIZZARD, 1)])
def test_move_gfi_dodge_pickup_exact_rolls_conditions_and_edges(weather, extra):
    game, trace = fresh()
    player, _ = players(game, [(3, 3)], [(4, 3)], ball=(3, 4))
    player.extra_skills = []
    game.get_ball().is_carried = False
    game.state.weather = weather
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    player.state.moves = player.get_ma()
    game.set_available_actions()
    with game.dice.force(d6=[6, 5, 4], strict=True):
        game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
    gfi = events(trace, 'GFI')[-1]
    dodge = events(trace, 'Dodge')[-1]
    move = events(trace, 'Move', 'moved')[-1]
    pickup = events(trace, 'Pickup')[-1]
    assert [dice(e) for e in (gfi, dodge, move, pickup)] == [[6], [5], [], [4]]
    assert [e['data']['modifiers'] for e in (gfi, dodge, pickup)] == [extra, 0, 0]
    assert [e['data']['threshold']['target'] for e in (gfi, dodge, pickup)] == [2, 4, 4]
    assert gfi['data']['conditions']['weather'] == weather.name
    assert dodge['data']['conditions'] == {'include_diving_tackle': False, 'ignore_opp_mods': False,
                                          'prehensile_tails': 0, 'tackle_zones': 1}
    assert move['data']['conditions'] == {'from_x': 3, 'from_y': 3, 'to_x': 3, 'to_y': 4}
    assert all(e['data']['participants']['player'] == 'home:0' for e in (gfi, dodge, move, pickup))
    assert dodge['data']['rule_id'] == 'BB2016:movement.dodge'
    for parent, child in zip((gfi, dodge, move), (dodge, move, pickup)):
        assert ancestor(trace, parent, child)
    assert game.has_ball(player)
    assert_trace(trace)


@pytest.mark.parametrize('mode', ['team', 'automatic', 'decline_pro', 'pro', 'loner', 'decline'])
def test_two_attempts_and_optional_reroll_decision(mode):
    game, trace = fresh()
    player, _ = players(game, [(3, 3)], [(4, 3)])
    player.extra_skills = {'automatic': [bb.Skill.DODGE], 'decline_pro': [bb.Skill.PRO],
                          'pro': [bb.Skill.PRO], 'loner': [bb.Skill.LONER]}.get(mode, [])
    player.team.state.rerolls = 1
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    rolls = [1, 4, 6] if mode in ('pro', 'loner') else [1, 2, 2] if mode == 'decline' else [1, 6]
    with game.dice.force(d6=rolls, strict=True):
        game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
        if mode == 'decline_pro':
            before = game.timeline.context
            game.advance(bb.Action(bb.ActionType.DONT_USE_SKILL))
            assert game.timeline.context.event_seq == before.event_seq
            assert events(trace, 'Reroll', 'DONT_USE_SKILL')[-1]['kind'] == 'rule_decision'
        if mode != 'automatic':
            game.advance(bb.Action(bb.ActionType.USE_SKILL if mode == 'pro' else
                                  bb.ActionType.DONT_USE_REROLL if mode == 'decline' else bb.ActionType.USE_REROLL))
    attempts = events(trace, 'Dodge')
    assert [dice(e) for e in attempts] == ([[1]] if mode == 'decline' else [[1], [6]])
    first = attempts[0]
    resolved = events(trace, 'Reroll', 'resolved')[-1]
    assert ancestor(trace, first, resolved)
    if mode != 'decline':
        assert ancestor(trace, resolved, attempts[1])
    if mode == 'automatic':
        assert not [e for e in events(trace, 'Reroll') if e['kind'] == 'rule_decision']
        assert first['data']['decision_id'] == attempts[1]['data']['decision_id']
    else:
        decision = [e for e in events(trace, 'Reroll') if e['kind'] == 'rule_decision'][-1]
        assert decision['data']['parent_event_ids'] == [first['event_id']] if mode != 'decline_pro' else \
            ancestor(trace, first, decision)
        assert ancestor(trace, decision, resolved)
    if mode in ('pro', 'loner'):
        assert dice(events(trace, 'Pro' if mode == 'pro' else 'Loner')[0]) == [4]
    assert_trace(trace)


@pytest.mark.parametrize('intercept', [None, False, True])
def test_pass_interception_catch_consumed_dice_and_defender_choice(intercept):
    game, trace = fresh()
    roster = players(game, [(3, 3), (7, 3)], [] if intercept is None else [(5, 3)], ball=(3, 3))
    passer, catcher = roster[:2]
    for player in roster:
        player.extra_skills = []
        player.team.state.rerolls = 0
    game.advance(bb.Action(bb.ActionType.START_PASS, player=passer))
    with game.dice.force(d6=[6] if intercept else [6, 6] if intercept is None else [1, 6, 6], strict=True):
        game.advance(bb.Action(bb.ActionType.PASS, position=catcher.position))
        if intercept is not None:
            game.advance(bb.Action(bb.ActionType.SELECT_PLAYER, player=roster[2]))
    if intercept is not None:
        choice = events(trace, 'Interception')[0]
        attempt = events(trace, 'Intercept')[0]
        assert choice['kind'] == 'rule_decision'
        assert choice['data']['participants']['actor'] == 'away'
        assert attempt['data']['participants'] == {'interceptor': 'away:0', 'passer': 'home:0', 'player': 'away:0'}
        assert attempt['data']['conditions']['interception'] is True
        assert attempt['data']['modifiers'] == -2 and attempt['data']['threshold']['target'] == 4
        assert dice(attempt) == [6 if intercept else 1]
        assert ancestor(trace, choice, attempt)
    if not intercept:
        passed, caught = events(trace, 'PassAttempt', 'ACCURATE_PASS')[0], events(trace, 'Catch', 'SUCCESSFUL_CATCH')[0]
        assert dice(passed) == dice(caught) == [6]
        assert caught['data']['conditions']['accurate'] is True
        assert passed['data']['participants']['passer'] == 'home:0'
        assert passed['data']['participants']['catcher'] == 'home:1'
        assert ancestor(trace, passed, caught)
        assert game.has_ball(catcher)
    else:
        turnover = events(trace, 'Turnover')[0]
        assert ancestor(trace, attempt, turnover)
    assert_trace(trace)


@pytest.mark.parametrize('choice', ['decline', 'first', 'second'])
def test_block_push_followup_casualty_apothecary_composite_and_single_reward(choice):
    game, trace = fresh()
    attacker, victim = players(game, [(3, 3)], [(4, 3)])
    attacker.extra_skills = victim.extra_skills = []
    attacker.team.state.rerolls = victim.team.state.rerolls = 0
    victim.team.state.apothecaries = 1
    rewards = [A2C_Reward(side) for side in ('home', 'away')]
    for reward in rewards:
        reward(game)
    game.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    with game.dice.force(block_dice=[bb.BBDieResult.DEFENDER_DOWN],
                         d6=[6, 6, 5, 5, 4] + ([] if choice == 'decline' else [3]),
                         d8=[3] + ([] if choice == 'decline' else [1]), strict=True):
        game.advance(bb.Action(bb.ActionType.BLOCK, position=victim.position))
        game.advance(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
        game.advance(bb.Action(bb.ActionType.PUSH, position=bb.Square(5, 3)))
        game.advance(bb.Action(bb.ActionType.FOLLOW_UP, position=bb.Square(4, 3)))
        game.advance(bb.Action(bb.ActionType.DONT_USE_APOTHECARY if choice == 'decline' else bb.ActionType.USE_APOTHECARY))
        if choice != 'decline':
            game.advance(bb.Action(bb.ActionType.SELECT_FIRST_ROLL if choice == 'first' else bb.ActionType.SELECT_SECOND_ROLL))
    block = events(trace, 'Block', 'BLOCK_ROLL')[0]
    selected = events(trace, 'Block', 'SELECT_DEFENDER_DOWN')[0]
    push = events(trace, 'Push', 'PUSHED')[0]
    followed = events(trace, 'FollowUp', 'FOLLOW_UP')[-1]
    knocked = events(trace, 'KnockDown', 'KNOCKED_DOWN')[0]
    armor = events(trace, 'Armor')[0]
    injury = events(trace, 'Injury')[0]
    casualty = events(trace, 'Casualty', 'CASUALTY')[0]
    applied = events(trace, 'Apothecary', 'BADLY_HURT' if choice == 'second' else 'MISS_NEXT_GAME')[0]
    assert dice(block) == ['DEFENDER_DOWN']
    assert dice(selected) == []
    assert [dice(e) for e in (armor, injury, casualty, applied)] == [[6, 6], [5, 5], [4, 3], []]
    assert injury['data']['conditions']['casualty_threshold'] == 10
    assert casualty['data']['participants']['inflictor'] == 'home:0'
    for parent, child in ((block, selected), (selected, push), (push, followed), (push, knocked),
                          (knocked, armor), (armor, injury), (injury, casualty), (casualty, applied)):
        assert ancestor(trace, parent, child), (parent, child)
    decisions = [e for e in events(trace, 'Apothecary') if e['kind'] == 'rule_decision']
    assert len(decisions) == (1 if choice == 'decline' else 2)
    assert all(e['data']['participants']['actor'] == 'away' for e in decisions)
    assert ancestor(trace, decisions[-1], applied)
    if choice != 'decline':
        assert dice(events(trace, 'Apothecary', 'CASUALTY_APOTHECARY')[0]) == [3, 1]
    assert sum(r.outcome_type == bb.OutcomeType.CASUALTY for r in game.state.reports) == 1
    assert victim.team.state.apothecaries == int(choice == 'decline')
    assert [reward(game) for reward in rewards] == [.6, -.6]  # Knockdown + one casualty.
    assert [reward(game) for reward in rewards] == [0, 0]
    assert_trace(trace)


@pytest.mark.parametrize('roll,skills,niggling,expected,total', [
    ([3, 4], [], 0, 'stunned', 7), ([4, 4], [], 0, 'knock_out', 8),
    ([4, 4], [bb.Skill.THICK_SKULL], 0, 'stunned', 7),
    ([3, 3], [bb.Skill.BALL_AND_CHAIN], 0, 'ball_and_chain_ko', 6),
    ([4, 5, 3], [], 1, 'casualty', 10),
])
def test_injury_evaluated_totals_precede_report_projection(roll, skills, niggling, expected, total):
    game, trace = fresh()
    attacker, victim = players(game, [(3, 3)], [(4, 3)])
    victim.extra_skills = skills
    victim.injuries = [bb.CasualtyEffect.NIGGLING] * niggling
    victim.team.state.apothecaries = 0
    proc.Injury(game, victim, inflictor=attacker)
    game.set_available_actions()
    with game.dice.force(d6=roll, d8=[1] if expected == 'casualty' else [], strict=True):
        game.advance()
    event = events(trace, 'Injury')[0]
    assert event['data']['outcome']['type'] == expected
    assert event['data']['conditions']['casualty_total' if expected == 'casualty' else 'ko_total'] == total
    assert dice(event) == roll[:2]
    if niggling:
        report = next(r for r in game.state.reports if r.outcome_type == bb.OutcomeType.INJURY_CASUALTY)
        assert report.rolls[0].modifiers == 0
        assert event['data']['modifiers'] == 1  # The actually tested total was 10.
    assert_trace(trace)


def attach_record(game, tmp_path, **options):
    return EpisodeRecorder(game, tmp_path, 'episode', episode_id='episode', source_family='fixture',
        scenario_id='rule-trace', policies={s: {'id': 'script', 'version': None} for s in ('home', 'away')},
        seed_plan={'schema_version': 1, 'algorithm': 'seed', 'sources': {'engine': 17}}, rule_trace=True,
        rule_trace_options=options)


@pytest.mark.parametrize('forward', [False, True])
def test_trace_on_off_identical_complete_game_rng_reports_timeline_and_trajectory(tmp_path, forward):
    game = semantic_game(3)
    baseline = deepcopy(game)
    recorder = attach_record(game, tmp_path)
    Timeline(baseline, episode_id='episode')
    for g in (game, baseline):
        g.init()
        if forward:
            g.enable_forward_model()
    for _ in range(180):
        if game.state.game_over:
            break
        action = progress_action(game)
        game.advance(action)
        baseline.advance(action)
        assert game.state.to_json(ignore_clocks=True) == baseline.state.to_json(ignore_clocks=True)
        assert game.capture_rng_state() == baseline.capture_rng_state()
        assert game.get_step() == baseline.get_step()
        assert game.timeline.to_json() == baseline.timeline.to_json()
    assert game.state.game_over
    recorder.finish()
    reader = EpisodeReader(tmp_path, 'episode')
    rows = reader.read_episode()['channels']
    assert rows['rule_traces'] == recorder.rule_trace.events
    assert rows['rule_trace_status'][0]['complete']
    assert reader.read_inputs()
    assert_trace(recorder.rule_trace)


@pytest.mark.parametrize('limit,reason', [({'max_events': 0}, 'event_limit'), ({'max_events': 8}, 'event_limit'),
                                        ({'max_bytes': 10}, 'byte_limit'), ({'consumer': None}, 'consumer_failure')])
def test_limits_and_consumer_failure_persist_incomplete_without_repeating_action(tmp_path, limit, reason):
    calls = []
    def fail(row):
        calls.append(row)
        row['data'].clear()
        raise RuntimeError('PRIVATE_CALLBACK_CANARY')
    if reason == 'consumer_failure':
        limit['consumer'] = fail
    game = semantic_game(1)
    baseline = deepcopy(game)
    recorder = attach_record(game, tmp_path, **limit)
    game.init(); baseline.init()
    while not game.state.game_over:
        action = progress_action(game)
        game.advance(action); baseline.advance(action)
    assert game.state.to_json(ignore_clocks=True) == baseline.state.to_json(ignore_clocks=True)
    assert game.capture_rng_state() == baseline.capture_rng_state()
    recorder.finish()
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    status = rows['rule_trace_status'][0]
    assert not status['complete'] and status['reason'] == reason
    assert len(calls) == int(reason == 'consumer_failure')
    assert 'PRIVATE_CALLBACK_CANARY' not in json.dumps(rows)


@pytest.mark.parametrize('mutation', ['cycle', 'future', 'missing', 'branch', 'episode', 'rule', 'payload', 'private'])
def test_graph_rejects_invalid_edges_and_unknown_payloads(mutation):
    _, trace = fresh()
    rows = trace.events
    row = next(e for e in rows if e['data']['coverage'] == 'instrumented' and e['data']['parent_event_ids'])
    if mutation in ('cycle', 'future'):
        row['data']['parent_event_ids'] = [deepcopy(row['event_id'])]
        if mutation == 'future':
            row['data']['parent_event_ids'][0][-1] += 1
    elif mutation == 'missing':
        rows.pop(0)
    elif mutation in ('branch', 'episode'):
        row['data']['parent_event_ids'][0][int(mutation == 'branch')] = 'foreign'
    elif mutation == 'rule':
        row['data']['rule_id'] = 'BB2016:invented.rule'
    elif mutation == 'payload':
        row['payload_version'] = 99
    else:
        row['data']['conditions']['rng'] = {'future': 'CANARY'}
    with pytest.raises(RecordError):
        validate_rule_graph(rows)


def test_fork_keeps_prefix_but_never_links_across_branches(tmp_path):
    game = semantic_game(1)
    recorder = attach_record(game, tmp_path)
    game.init()
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    prefix = recorder.rule_trace.events
    recorder.timeline.fork('continuation')
    complete(game, recorder)
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']['rule_traces']
    assert rows[:len(prefix)] == prefix
    assert rows[len(prefix)]['data']['parent_event_ids'] == []
    validate_rule_graph(rows)


def test_queries_returned_data_and_privileged_channels_do_not_leak(tmp_path, monkeypatch):
    game = semantic_game(1)
    recorder = attach_record(game, tmp_path)
    game.oracle = {'CANARY': 'ORACLE_FUTURE_RNG'}
    game.init()
    complete(game, recorder)
    original = recorder.rule_trace.events
    original[0]['data']['parent_event_ids'].append(['bad'])
    assert original != recorder.rule_trace.events
    assert 'ORACLE_FUTURE_RNG' not in json.dumps(recorder.rule_trace.events)
    before = recorder.rule_trace.events
    game.to_json(); game.get_available_actions()
    assert recorder.rule_trace.events == before
    reader = EpisodeReader(tmp_path, 'episode')
    # Selective model input reads must not even open historical trace files.
    (tmp_path / 'episode/events/rules.jsonl').write_text('PRIVATE_CORRUPT_FUTURE')
    assert reader.read_inputs()
    with pytest.raises(RecordError):
        reader.read_episode()


def test_incomplete_capture_cannot_be_relabelled_complete(tmp_path):
    game = semantic_game(1)
    recorder = attach_record(game, tmp_path, max_events=1)
    game.init(); complete(game, recorder)
    episode = EpisodeReader(tmp_path, 'episode').read_episode()
    status = episode['channels']['rule_trace_status'][0]
    status.update(complete=True, reason=None)
    with pytest.raises(RecordError, match='Incomplete rule capture'):
        validate_episode(episode['manifest'], episode['channels'])


def test_bounded_overhead_corpus_no_timing_gate(tmp_path):
    samples = []
    for enabled in (False, True):
        for size in (1, 3, 5):
            game = semantic_game(size)
            Timeline(game)
            trace = RuleTrace(game) if enabled else None
            start = time.perf_counter()
            game.init()
            until(game, lambda g: g.state.game_over)
            elapsed = time.perf_counter() - start
            samples.append({'size': size, 'trace': enabled, 'seconds': elapsed,
                            'decisions': game.timeline.context.decision_seq,
                            'sports_events': game.timeline.context.event_seq,
                            'trace_events': 0 if trace is None else len(trace.events),
                            'trace_bytes': 0 if trace is None else trace.status['retained_bytes']})
            if trace:
                assert_trace(trace)
    (tmp_path / 'overhead.json').write_text(json.dumps(samples, indent=2))
    for baseline, traced in zip(samples[:3], samples[3:]):
        assert baseline['decisions'] == traced['decisions']
        assert baseline['sports_events'] == traced['sports_events']


@pytest.mark.parametrize('family', ['move_reroll', 'pass', 'block_injury'])
@pytest.mark.parametrize('forward', [False, True])
def test_instrumented_play_on_off_preserves_state_rolls_counters_execution_and_rewards(family, forward):
    game = semantic_game(3)
    baseline = deepcopy(game)
    trace = RuleTrace(game)
    Timeline(baseline)
    for g in (game, baseline):
        g.init()
        until(g, lambda g: type(g.get_procedure()) is proc.Turn)
        roster = players(g, [(3, 3), (7, 3)] if family == 'pass' else [(3, 3)],
                         [] if family == 'pass' else [(4, 3)], ball=(3, 3) if family == 'pass' else None)
        for player in roster:
            player.extra_skills = []
            player.team.state.rerolls = 0
            player.team.state.apothecaries = 0
        if forward:
            g.enable_forward_model()

    def play(g):
        own = g.state.home_team.players[0]
        if family == 'move_reroll':
            own.team.state.rerolls = 1
            g.advance(bb.Action(bb.ActionType.START_MOVE, player=own))
            with g.dice.force(d6=[1, 6], strict=True):
                g.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
                g.advance(bb.Action(bb.ActionType.USE_REROLL))
        elif family == 'pass':
            g.advance(bb.Action(bb.ActionType.START_PASS, player=own))
            with g.dice.force(d6=[6, 6], strict=True):
                g.advance(bb.Action(bb.ActionType.PASS, position=bb.Square(7, 3)))
        else:
            g.advance(bb.Action(bb.ActionType.START_BLOCK, player=own))
            with g.dice.force(block_dice=[bb.BBDieResult.DEFENDER_DOWN], d6=[6, 6, 5, 5, 3], d8=[1], strict=True):
                g.advance(bb.Action(bb.ActionType.BLOCK, position=bb.Square(4, 3)))
                g.advance(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
                g.advance(bb.Action(bb.ActionType.PUSH, position=bb.Square(5, 3)))
                g.advance(bb.Action(bb.ActionType.FOLLOW_UP, position=bb.Square(4, 3)))
    play(game); play(baseline)
    assert game.state.to_json(ignore_clocks=True) == baseline.state.to_json(ignore_clocks=True)
    assert game.capture_rng_state() == baseline.capture_rng_state()
    assert game.get_step() == baseline.get_step()
    assert game.timeline.to_json() == baseline.timeline.to_json()
    for side in ('home', 'away'):
        reward, expected = A2C_Reward(side), A2C_Reward(side)
        assert reward(game) == expected(baseline)
        assert reward(game) == expected(baseline) == 0
    assert_trace(trace)


@pytest.mark.parametrize('boundary', ['writer', 'reader'])
@pytest.mark.parametrize('corrupt', ['parent', 'participant', 'anchor', 'complete'])
def test_persistence_rejects_graph_corruption_with_truthful_counts_and_hashes(tmp_path, boundary, corrupt):
    game = semantic_game(1)
    recorder = attach_record(game, tmp_path)
    game.init(); complete(game, recorder)
    reader = EpisodeReader(tmp_path, 'episode')
    episode = reader.read_episode()
    manifest, rows = episode['manifest'], episode['channels']
    row = next(e for e in rows['rule_traces'] if e['data']['coverage'] == 'instrumented' and
               e['data']['parent_event_ids'])
    if corrupt == 'parent':
        row['data']['parent_event_ids'] = [row['event_id']]
    elif corrupt == 'participant':
        row['data']['participants']['player'] = 'home:999'
    elif corrupt == 'anchor':
        row['data']['event_ref'] = [row['context']['episode_id'], row['context']['branch_id'], 1]
    else:
        rows['rule_traces'].pop()
    status = rows['rule_trace_status'][0]
    status['retained_events'] = len(rows['rule_traces'])
    status['retained_bytes'] = sum(len(encode_json(r)) + 1 for r in rows['rule_traces'])
    if boundary == 'writer':
        from botbowl.lab.recording import JsonlEpisodeWriter
        writer = JsonlEpisodeWriter(tmp_path, 'corrupt')
        with pytest.raises((RecordError, RecordingError)):
            for name, records in rows.items():
                for record in records:
                    writer.append((name, record))
            writer.confirm(manifest, list(rows))
        writer.close()
        assert not (tmp_path / 'corrupt').exists()
    else:
        for name in ('rule_traces', 'rule_trace_status'):
            info = manifest['files'][name]
            payload = b''.join(encode_json(record) + b'\n' for record in rows[name])
            (tmp_path / 'episode' / info['path']).write_bytes(payload)
            info.update(bytes=len(payload), rows=len(rows[name]), sha256=hashlib.sha256(payload).hexdigest())
        (tmp_path / 'episode/manifest.json').write_bytes(encode_json(manifest))
        with pytest.raises(RecordError):
            EpisodeReader(tmp_path, 'episode').read_episode()


def test_reusing_dodge_roll_with_break_tackle_is_not_another_consumption():
    game, trace = fresh()
    player, _ = players(game, [(3, 3)], [(4, 3)])
    player.extra_skills = [bb.Skill.BREAK_TACKLE]
    player.extra_st = 5 - player.get_st()
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    with game.dice.force(d6=[3], strict=True):
        game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
        game.advance(bb.Action(bb.ActionType.USE_SKILL))
    failed, reused = events(trace, 'Dodge', 'FAILED_DODGE')[0], events(trace, 'Dodge', 'SUCCESSFUL_DODGE')[0]
    assert dice(failed) == [3] and dice(reused) == []
    assert failed['data']['threshold']['target'] == 4 and reused['data']['threshold']['target'] == 2
    assert ancestor(trace, failed, reused)
    assert_trace(trace)


@pytest.mark.parametrize('regen', [False, True])
def test_decay_and_regeneration_keep_one_casualty_and_consumed_rolls(regen):
    game, trace = fresh()
    attacker, victim = players(game, [(3, 3)], [(4, 3)])
    victim.extra_skills = [bb.Skill.REGENERATION, bb.Skill.DECAY]
    victim.team.state.apothecaries = 0
    proc.Injury(game, victim, inflictor=attacker)
    game.set_available_actions()
    with game.dice.force(d6=[5, 5, 4, 6] if regen else [5, 5, 4, 1, 3], d8=[3] if regen else [3, 1], strict=True):
        game.advance()
    casualty = events(trace, 'Casualty', 'CASUALTY')[0]
    recovery = events(trace, 'Regeneration')[0]
    assert dice(casualty) == [4, 3] and dice(recovery) == [6 if regen else 1]
    assert ancestor(trace, casualty, recovery)
    decay = events(trace, 'Casualty', 'DECAYING')
    assert len(decay) == int(not regen)
    if decay:
        assert dice(decay[0]) == [3, 1]
        assert ancestor(trace, recovery, decay[0])
    assert sum(r.outcome_type == bb.OutcomeType.CASUALTY for r in game.state.reports) == 1
    rewards = [A2C_Reward(side) for side in ('home', 'away')]
    # Ignore the completed setup/kickoff prefix, as #12's fixtures do.
    start = next(i for i, r in enumerate(game.state.reports) if r.outcome_type == bb.OutcomeType.INJURY_CASUALTY)
    for reward in rewards:
        reward.game = game
        reward.last_report_idx = start
    assert [reward(game) for reward in rewards] == [.5, -.5]
    assert [reward(game) for reward in rewards] == [0, 0]
    assert_trace(trace)


def test_live_trace_snapshot_boundary_is_explicit_and_rewind_marks_incomplete():
    from botbowl.lab.snapshots import SnapshotError, capture_snapshot
    game, trace = fresh()
    with pytest.raises(SnapshotError, match='Live rule traces'):
        capture_snapshot(game)
    saved = game.timeline.capture()
    game.timeline.restore(saved)
    assert not trace.status['complete'] and trace.status['reason'] == 'timeline_restored'


def test_graph_rejects_missing_antecedent_on_new_branch():
    game, trace = fresh()
    prefix = len(trace.events)
    game.timeline.fork('new')
    game.advance(bb.Action(bb.ActionType.END_TURN))
    rows = trace.events
    child = rows[prefix]
    # Structurally earlier and same branch, but that ID only existed on root.
    child['data']['parent_event_ids'] = [[child['event_id'][0], 'new', 'rule', 1]]
    with pytest.raises(RecordError, match='Missing rule antecedent'):
        validate_rule_graph(rows)


def test_touchdown_links_movement_to_turn_and_drive_boundaries():
    game, trace = fresh()
    end_x = game.get_opp_endzone_x(game.active_team)
    near_x = end_x + (1 if end_x == 1 else -1)
    player = players(game, [(near_x, 3)], ball=(near_x, 3))[0]
    score = player.team.state.score
    drive = game.timeline.context.drive_seq
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(end_x, 3)))
    moved, touchdown = events(trace, 'Move', 'moved')[-1], events(trace, 'Touchdown')[-1]
    turn_end, drive_end = events(trace, 'timeline', 'team_turn_ended')[-1], events(trace, 'timeline', 'drive_ended')[-1]
    next_drive = events(trace, 'timeline', 'drive_started')[-1]
    assert player.team.state.score == score + 1
    assert next_drive['context']['drive_seq'] == drive + 1
    for parent, child in ((moved, touchdown), (touchdown, turn_end), (turn_end, drive_end), (drive_end, next_drive)):
        assert ancestor(trace, parent, child)
    assert touchdown['kind'] == 'rule'
    assert_trace(trace)


def test_unconsumed_forced_dice_and_attached_private_fields_are_not_historical_rolls():
    game, trace = fresh()
    player = players(game, [(3, 3)])[0]
    game.dice.future = {'canary': 'PRIVATE_RNG_FUTURE'}
    game.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    player.state.moves = player.get_ma()
    game.set_available_actions()
    with game.dice.force(d6=[6, 2, 3], strict=True):
        game.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4)))
        event = events(trace, 'GFI')[-1]
        assert dice(event) == [6]
        assert 'PRIVATE_RNG_FUTURE' not in json.dumps(trace.events)
    assert_trace(trace)
