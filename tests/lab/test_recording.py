"""Causal recorder fixtures and physical channel/failure boundaries."""
from copy import deepcopy
import json

import pytest

import botbowl as bb
from botbowl.core import procedure as proc
from botbowl.lab.actions import PositionV1
from botbowl.lab.channels import input_profile
from botbowl.lab.observations import observe
from botbowl.lab.recording import (EpisodeReader, EpisodeRecorder, JsonlEpisodeWriter,
                                   PartialEpisodeError, RecordingError)
from botbowl.lab.records import RecordError
from tests.baseline import progress_action
from tests.lab.test_semantic_actions import semantic_game
from tests.lab.test_timeline import players, until


def record_game(tmp_path, size=1, pathfinding=False, name='episode', profile=None):
    game = semantic_game(size, pathfinding)
    recorder = attach(game, tmp_path, name, profile)
    game.init()
    return game, recorder


def attach(game, tmp_path, name='episode', profile=None):
    kwargs = {} if profile is None else {'profile': profile}
    return EpisodeRecorder(
        game, tmp_path, name, episode_id=name, source_family='fixture', scenario_id='small-game',
        policies={side: {'id': 'script', 'version': None} for side in ('home', 'away')},
        seed_plan={'schema_version': 1, 'algorithm': 'legacy-numpy-seed', 'sources': {'engine': 17}}, **kwargs)


def complete(game, recorder):
    for _ in range(180):
        if game.state.game_over:
            return recorder.finish()
        recorder.advance(progress_action(game))
    pytest.fail('episode failed to terminate within fixture bound')


def scenario_players(game, own, opponents=(), ball=None):
    result = players(game, own, opponents, ball)
    # Trusted microposition construction is explicit uncaused scenario work.
    # Preserve an actual placement report, separating it from coach consequences.
    for player in result:
        game.report(bb.Outcome(bb.OutcomeType.PLAYER_PLACED, player=player, position=player.position))
    return result


@pytest.mark.parametrize('size', [1, 3, 5, 7, 11])
def test_complete_both_actors_continuity_reports_and_round_trip(tmp_path, size):
    game, recorder = record_game(tmp_path, size)
    complete(game, recorder)
    reader = EpisodeReader(tmp_path, 'episode')
    episode = reader.read_episode()
    rows, manifest = episode['channels'], episode['manifest']
    decisions = rows['transitions']
    assert {d['actor_id'] for d in decisions} == {'home', 'away'}
    assert any(a['actor_id'] == b['actor_id'] for a, b in zip(decisions, decisions[1:]))
    observations = {o['observation_id']: o for o in rows['primary']}
    for a, b in zip(decisions, decisions[1:]):
        assert a['after'] == b['before']
        assert observations[a['post_observation']] == observations[b['pre_observation']]
    assert [e['data']['outcome_type'] for e in rows['events'] if e['kind'] == 'report'] == [
        e.outcome_type.name for e in game.state.reports]
    assert decisions[-1]['end'] == manifest['end'] == {'kind': 'terminal', 'reason': 'game_over'}
    assert observations[manifest['final_observation']]['channel']['data'] == observe(
        game, recorder.timeline._entities, 'home').to_json()
    assert reader.read_episode() == episode
    assert len(reader.read_inputs()) == len(rows['primary'])
    assert not (tmp_path / 'episode.partial').exists()


@pytest.mark.parametrize('forward_model', [False, True])
def test_recorder_on_off_same_state_rng_results_and_trajectory(tmp_path, forward_model):
    game = semantic_game(3)
    baseline = deepcopy(game)
    recorder = attach(game, tmp_path)
    for candidate in (game, baseline):
        candidate.init()
        if forward_model:
            candidate.enable_forward_model()
    while not game.state.game_over:
        action = progress_action(game)
        result, expected = recorder.advance(action), baseline.advance(action)
        assert [e.to_json() for e in result.events] == [e.to_json() for e in expected.events]
        assert result.terminal == expected.terminal
        assert game.state.to_json(ignore_clocks=True) == baseline.state.to_json(ignore_clocks=True)
        assert game.capture_rng_state() == baseline.capture_rng_state()
        assert game.get_step() == baseline.get_step()
    rng, state = game.capture_rng_state(), game.state.to_json(ignore_clocks=True)
    recorder.finish()
    EpisodeReader(tmp_path, 'episode').read_episode()
    assert rng == game.capture_rng_state() and state == game.state.to_json(ignore_clocks=True)


@pytest.mark.parametrize('automatic', [False, True])
def test_reroll_empty_event_interval_and_automatic_skill(tmp_path, automatic):
    game, recorder = record_game(tmp_path, 3)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    player, _ = scenario_players(game, [(3, 3)], [(4, 3)])
    player.extra_skills = [bb.Skill.DODGE if automatic else bb.Skill.PRO]
    player.team.state.rerolls = 1
    recorder.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    with game.dice.force(d6=[1, 6], strict=True):
        moved = recorder.advance(bb.Action(bb.ActionType.MOVE, position=bb.Square(3, 4))).decisions[0]
        if not automatic:
            declined = recorder.advance(bb.Action(bb.ActionType.DONT_USE_SKILL)).decisions[0]
            assert declined.event_start == declined.event_stop
            assert declined.events == ()
            recorder.advance(bb.Action(bb.ActionType.USE_REROLL))
    recorder.finish(truncation_reason='fixture_limit')
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    assert any(e['data'].get('outcome_type') == 'FAILED_DODGE' for e in rows['events'])
    if automatic:
        assert all(e['decision_seq'] == moved.after.decision_seq for e in rows['events'][moved.event_start - 1:])
    else:
        primary = {r['observation_id']: r['channel']['data'] for r in rows['primary']}
        decline = rows['transitions'][-2]
        assert primary[decline['pre_observation']]['decision']['phase'] == 'reroll'
        assert primary[decline['post_observation']]['decision']['phase'] == 'reroll'
        assert decline['actor_id'] == rows['transitions'][-1]['actor_id']


@pytest.mark.parametrize('boundary', ['reroll', 'defender', 'terminal'])
def test_interrupted_macro_keeps_children_and_all_observations(tmp_path, boundary):
    game, recorder = record_game(tmp_path, 1 if boundary != 'terminal' else 3, True)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    if boundary == 'terminal':
        until(game, lambda g: type(g.get_procedure()) is proc.Turn and g.state.half == 2
              and g.get_opp_team(g.active_team).state.turn == 1)
        end_x = game.get_opp_endzone_x(game.active_team)
        near_x = end_x + (1 if end_x == 1 else -1)
        player = scenario_players(game, [(near_x, 5)], ball=(near_x, 5))[0]
    elif boundary == 'defender':
        player, _ = scenario_players(game, [(2, 2)], [(3, 2)])
        player.extra_st = 1 - player.get_st()
    else:
        player = scenario_players(game, [(2, 2)])[0]
    recorder.advance(bb.Action(bb.ActionType.START_BLITZ if boundary == 'defender' else bb.ActionType.START_MOVE,
                              player=player))
    if boundary == 'reroll':
        player.state.moves = player.get_ma()
        player.team.state.rerolls = 1
        game.set_available_actions()
        game.report(bb.Outcome(bb.OutcomeType.PLAYER_PLACED, player=player, position=player.position))
    route = next(m for m in recorder.actions.legal_actions().macros if m.to_json()['kind'] == 'route'
                 and m.type == ('BLOCK' if boundary == 'defender' else 'MOVE') and len(m.path) == 1
                 and (boundary != 'terminal' or m.path[-1] == PositionV1(end_x, 5)))
    with game.dice.force(d6=[1] if boundary == 'reroll' else [],
                         block_dice=[bb.BBDieResult.PUSH] * 3 if boundary == 'defender' else [], strict=True):
        result = recorder.execute_macro(route)
    assert result.interruption == {'reroll': 'unplanned_decision', 'defender': 'actor_changed',
                                   'terminal': 'terminal'}[boundary]
    child = recorder.records['transitions'][-1]
    route.path.append(PositionV1(999, 999))
    assert recorder.records['transitions'][-1] == child
    if boundary == 'defender':
        recorder.advance(bb.Action(bb.ActionType.SELECT_PUSH))
        assert recorder.records['transitions'][-1]['actor_id'] != child['actor_id']
    recorder.finish(**({} if boundary == 'terminal' else {'truncation_reason': 'fixture_limit'}))
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    parent = rows['macros'][-1]
    assert parent['decision_seqs'] == [child['after']['decision_seq']]
    assert child['macro_id'] == parent['macro']['macro_id'] and child['primitive_order'] == 0


def test_formation_macro_snapshots_every_primitive(tmp_path):
    game, recorder = record_game(tmp_path, 3)
    until(game, lambda g: isinstance(g.get_procedure(), proc.Setup))
    macro = recorder.actions.legal_actions().macros[0]
    result = recorder.execute_macro(macro)
    assert len(result.steps) > 1
    assert result.status == 'completed'
    recorder.finish(truncation_reason='fixture_limit')
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    children = [t for t in rows['transitions'] if t['macro_id'] == macro.macro_id]
    assert len(children) == len(result.steps)
    assert [c['primitive_order'] for c in children] == list(range(len(children)))
    assert len({c['post_observation'] for c in children}) == len(children)


@pytest.mark.parametrize('resume', [False, True])
def test_pending_decision_replaced_or_explicitly_truncated(tmp_path, resume):
    game, recorder = record_game(tmp_path)
    with pytest.raises(bb.GameTruncatedError):
        recorder.advance(bb.Action(bb.ActionType.START_GAME), max_steps=1)
    assert recorder.records['transitions'][0]['status'] == 'pending'
    assert len(recorder.records['diagnostics']) == 1
    pre = recorder.records['transitions'][0]['pre_observation']
    if resume:
        recorder.advance()
    recorder.finish(truncation_reason='step_budget')
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    assert len(rows['transitions']) == 1
    assert rows['transitions'][0]['status'] == ('resolved' if resume else 'pending')
    assert rows['transitions'][0]['pre_observation'] == pre
    assert rows['transitions'][0]['end']['kind'] == 'truncated'


@pytest.mark.parametrize('terminal', [False, True])
def test_zero_decisions_retains_manifest_without_fabricated_transition(tmp_path, terminal):
    game, recorder = record_game(tmp_path)
    if terminal:
        game.state.game_over = True  # A trusted already-terminal scenario, no coach action.
    else:
        with pytest.raises(bb.GameTruncatedError):
            recorder.advance(bb.Action(bb.ActionType.START_GAME), max_steps=0)
    recorder.finish(**({} if terminal else {'truncation_reason': 'zero_budget'}))
    episode = EpisodeReader(tmp_path, 'episode').read_episode()
    assert episode['channels']['transitions'] == episode['channels']['events'] == []
    assert episode['manifest']['final_context']['decision_seq'] == 0
    assert episode['manifest']['end']['kind'] == ('terminal' if terminal else 'truncated')


def test_rejections_are_operational_and_do_not_advance_game(tmp_path):
    game, recorder = record_game(tmp_path)
    request = recorder.actions.request(recorder.actions.legal_actions().actions[0])
    with pytest.raises(bb.InvalidActionError):
        recorder.advance(bb.Action(bb.ActionType.END_TURN))
    assert not recorder.records['transitions'] and not recorder.records['events']
    recorder.advance(request)
    rng, state = game.capture_rng_state(), game.state.to_json(ignore_clocks=True)
    with pytest.raises(ValueError):
        recorder.advance(request)
    assert rng == game.capture_rng_state() and state == game.state.to_json(ignore_clocks=True)
    recorder.finish(truncation_reason='fixture_limit')
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    assert len(rows['transitions']) == 1 and len(rows['diagnostics']) == 2


def test_finalizer_error_retains_terminal_transition_and_diagnostic(tmp_path, monkeypatch):
    game, recorder = record_game(tmp_path)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn and g.state.half == 2
          and g.get_opp_team(g.active_team).state.turn == 1)

    def fail():
        raise RuntimeError('private traceback canary')

    monkeypatch.setattr(game, '_end_game', fail)
    with pytest.raises(RuntimeError):
        recorder.advance(bb.Action(bb.ActionType.END_TURN))
    recorder.finish()
    episode = EpisodeReader(tmp_path, 'episode').read_episode()
    assert episode['channels']['transitions'][-1]['end']['kind'] == 'terminal'
    assert episode['channels']['diagnostics'][-1]['error_type'] == 'RuntimeError'
    assert 'private traceback canary' not in json.dumps(episode)


def test_fork_uses_new_branch_and_rewind_rejected_before_engine_change(tmp_path):
    game, recorder = record_game(tmp_path)
    game.enable_forward_model()
    checkpoint = game.capture_checkpoint()
    recorder.advance(progress_action(game))
    before = game.state.to_json(ignore_clocks=True), game.capture_rng_state()
    with pytest.raises(RecordingError, match='cannot rewind'):
        game.restore_checkpoint(checkpoint)
    assert before == (game.state.to_json(ignore_clocks=True), game.capture_rng_state())
    recorder.timeline.fork('alternative')
    recorder.advance(progress_action(game))
    recorder.finish(truncation_reason='fixture_limit')
    episode = EpisodeReader(tmp_path, 'episode').read_episode()
    assert [t['after']['branch_id'] for t in episode['channels']['transitions']] == ['root', 'alternative']
    assert episode['manifest']['branches'][1]['parent']['branch_id'] == 'root'


def test_leakage_canaries_selective_read_and_mutation_safety(tmp_path, monkeypatch):
    game, recorder = record_game(tmp_path)
    game.future_oracle = 'FUTURE_CANARY'
    game.rng_canary = 'RNG_CANARY'
    game.home_agent.private_policy = 'POLICY_CANARY'
    recorder._provenance['seed_plan']['sources']['canary'] = 'SEED_METADATA_CANARY'
    recorder.advance(progress_action(game))
    ref = recorder.records['primary'][-1]['observation_id']
    recorder.append_channel('privileged', ref, {'rng': 'RNG_CANARY', 'future': 'FUTURE_CANARY'})
    recorder.append_channel('evaluation', ref, {'labels': {'oracle': 'ORACLE_CANARY'}, 'estimates': {},
                                                'provenance': {'source': 'fixture'}})
    recorder.append_channel('control', ref, {'action_mask': [True], 'action_ids': ['CONTROL_CANARY']})
    recorder.finish(truncation_reason='fixture_limit')
    reader = EpisodeReader(tmp_path, 'episode')
    full = reader.read_episode()
    # Make every non-input blob unreadable/invalid. Input loading must never open it.
    from botbowl.lab import recording
    actual_read = recording._read_file
    opened = []

    def guarded_read(path, limit):
        opened.append(path)
        assert path.name == 'primary.jsonl'
        return actual_read(path, limit)

    monkeypatch.setattr(recording, '_read_file', guarded_read)
    inputs = reader.read_inputs()
    assert len(opened) == 1
    serialized = json.dumps(inputs)
    assert 'CANARY' not in serialized
    assert 'CANARY' not in (tmp_path / 'episode/inputs/primary.jsonl').read_text()
    inputs[0]['features'].clear()
    assert reader.read_inputs()[0]['features']
    manifest = reader.manifest
    manifest['provenance']['policies']['home']['id'] = 'mutated'
    assert reader.manifest['provenance']['policies']['home']['id'] == 'script'
    assert 'ORACLE_CANARY' in json.dumps(full['channels']['evaluation'])
    assert 'RNG_CANARY' in json.dumps(full['channels']['privileged'])


def test_authorized_mask_profile_requires_complete_explicit_data(tmp_path):
    game, recorder = record_game(tmp_path, profile=input_profile(include_action_mask=True))
    recorder.advance(progress_action(game))
    for row in recorder.records['primary']:
        recorder.append_channel('control', row['observation_id'], {'action_mask': [True, False]})
    recorder.finish(truncation_reason='fixture_limit')
    inputs = EpisodeReader(tmp_path, 'episode').read_inputs()
    assert all(i['features']['control.action_mask[]'] == [True, False] for i in inputs)


@pytest.mark.parametrize('failure', ['write', 'rename', 'flush'])
def test_writer_failure_visible_no_engine_retry_or_confirmed_episode(tmp_path, monkeypatch, failure):
    game, recorder = record_game(tmp_path)
    recorder.advance(progress_action(game))
    state, rng, count = game.state.to_json(ignore_clocks=True), game.capture_rng_state(), recorder.timeline.context

    def fail(*args, **kwargs):
        raise OSError('disk failure')

    if failure == 'rename':
        monkeypatch.setattr('botbowl.lab.recording.os.rename', fail)
    elif failure == 'flush':
        monkeypatch.setattr('botbowl.lab.recording.os.fsync', fail)
    else:
        monkeypatch.setattr(recorder.writer, '_open_channel', fail)
    with pytest.raises(RecordingError):
        recorder.finish(truncation_reason='fixture_limit')
    assert recorder.failed and (tmp_path / 'episode.partial').exists()
    assert not (tmp_path / 'episode').exists()
    with pytest.raises(PartialEpisodeError):
        EpisodeReader(tmp_path, 'episode')
    with pytest.raises(PartialEpisodeError):
        EpisodeReader(tmp_path, 'episode.partial')
    with pytest.raises(RecordingError):
        recorder.advance(progress_action(game))
    assert (state, rng, count) == (game.state.to_json(ignore_clocks=True), game.capture_rng_state(), recorder.timeline.context)


def test_close_interruption_and_incomplete_jsonl_rejected(tmp_path):
    game, recorder = record_game(tmp_path)
    recorder.advance(progress_action(game))
    recorder.close()
    with pytest.raises(PartialEpisodeError):
        EpisodeReader(tmp_path, 'episode')
    game, recorder = record_game(tmp_path, name='complete')
    recorder.advance(progress_action(game))
    recorder.finish(truncation_reason='fixture_limit')
    path = tmp_path / 'complete/inputs/primary.jsonl'
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(RecordError):
        EpisodeReader(tmp_path, 'complete').read_inputs()


def test_mutated_live_data_and_returned_records_do_not_rewrite_copied_prefix(tmp_path):
    game, recorder = record_game(tmp_path)
    recorder.advance(progress_action(game))
    before = recorder.records
    exposed = recorder.records
    exposed['primary'][0]['channel']['data']['players'].clear()
    game.state.reports.clear()
    assert recorder.records == before
    recorder.finish(truncation_reason='fixture_limit')
    reader = EpisodeReader(tmp_path, 'episode')
    result = reader.read_episode()
    result['channels']['events'][0]['data']['n'] = 333
    assert reader.read_episode()['channels']['events'][0]['data']['n'] == 0


@pytest.mark.parametrize('path', ['../outside', '/tmp/outside', 'a/../b', 'a//b', 'a\\b', './a', ''])
def test_unsafe_destination_paths_rejected(tmp_path, path):
    with pytest.raises(RecordError):
        JsonlEpisodeWriter(tmp_path, path)


def test_symlink_and_non_regular_files_rejected(tmp_path):
    (tmp_path / 'link').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(RecordError):
        JsonlEpisodeWriter(tmp_path, 'link/episode')
    game, recorder = record_game(tmp_path)
    recorder.advance(progress_action(game))
    recorder.finish(truncation_reason='fixture_limit')
    channel = tmp_path / 'episode/inputs/primary.jsonl'
    original = channel.read_bytes()
    channel.unlink()
    target = tmp_path / 'outside.jsonl'
    target.write_bytes(original)
    channel.symlink_to(target)
    with pytest.raises(RecordError):
        EpisodeReader(tmp_path, 'episode').read_inputs()


def test_pending_macro_resume_retains_original_interruption_and_child_id(tmp_path):
    game, recorder = record_game(tmp_path, 3, True)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    player = scenario_players(game, [(3, 3)])[0]
    recorder.advance(bb.Action(bb.ActionType.START_MOVE, player=player))
    route = next(m for m in recorder.actions.legal_actions().macros
                 if m.to_json()['kind'] == 'route' and m.type == 'MOVE' and len(m.path) == 1)
    with pytest.raises(bb.GameTruncatedError):
        recorder.execute_macro(route, max_steps=1)
    before = recorder.records['transitions'][-1]
    recorder.advance()
    recorder.finish(truncation_reason='fixture_limit')
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    parent, child = rows['macros'][-1], rows['transitions'][-1]
    assert parent['interruption']['reason'] == 'operational_error'
    assert parent['interruption']['next_order'] == child['primitive_order'] == 0
    assert child['transition_id'] == before['transition_id'] and child['status'] == 'resolved'


def test_automatic_clock_terminal_has_no_invented_or_rewritten_coach_transition(tmp_path):
    from tests.framework.test_forced_action import FakeTime
    game = semantic_game()
    game.config.competition_mode = True
    game.time_source = FakeTime()
    recorder = attach(game, tmp_path)
    game.init()
    until(game, lambda g: type(g.get_procedure()) is proc.Turn and g.state.half == 2
          and g.get_opp_team(g.active_team).state.turn == 1)
    before = recorder.records['transitions']
    count = recorder.timeline.context.decision_seq
    game.time_source.now += 10000
    game.refresh(max_steps=100)
    assert game.state.game_over
    assert recorder.timeline.context.decision_seq == count
    recorder.finish()
    episode = EpisodeReader(tmp_path, 'episode').read_episode()
    assert episode['channels']['transitions'] == before
    assert episode['manifest']['end']['kind'] == 'terminal'
    assert episode['channels']['events'][-1]['decision_seq'] is None


def test_real_casualty_copied_once_with_exact_rolls_and_causation(tmp_path):
    game, recorder = record_game(tmp_path, 3)
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    attacker, defender = scenario_players(game, [(3, 3)], [(4, 3)])
    for player in (attacker, defender):
        reserves = game.get_reserves(player.team)
        if player in reserves:
            reserves.remove(player)
    defender.team.state.apothecaries = 0
    recorder.advance(bb.Action(bb.ActionType.START_BLOCK, player=attacker))
    with game.dice.force(block_dice=[bb.BBDieResult.DEFENDER_DOWN], strict=True):
        recorder.advance(bb.Action(bb.ActionType.BLOCK, position=defender.position))
    recorder.advance(bb.Action(bb.ActionType.SELECT_DEFENDER_DOWN))
    with game.dice.force(d6=[6, 6, 5, 5, 4], d8=[3], strict=True):
        recorder.advance(bb.Action(bb.ActionType.PUSH, position=bb.Square(5, 3)))
        recorder.advance(bb.Action(bb.ActionType.FOLLOW_UP, position=attacker.position))
    recorder.finish(truncation_reason='fixture_limit')
    rows = EpisodeReader(tmp_path, 'episode').read_episode()['channels']
    casualty = [e for e in rows['events'] if e['data'].get('outcome_type') == 'CASUALTY']
    assert len(casualty) == 1
    assert casualty[0]['decision_seq'] == rows['transitions'][-1]['after']['decision_seq']
    timeline_casualty = next(e for e in recorder.timeline.events if e.data.get('outcome_type') == 'CASUALTY')
    assert casualty[0]['data'] == timeline_casualty.data


@pytest.mark.parametrize('corrupt', ['missing_parent', 'child_list', 'order', 'actor', 'interruption'])
def test_macro_parent_corruption_rejected(tmp_path, corrupt):
    from botbowl.lab.records import validate_episode
    game, recorder = record_game(tmp_path, 3)
    until(game, lambda g: isinstance(g.get_procedure(), proc.Setup))
    macro = recorder.actions.legal_actions().macros[0]
    recorder.execute_macro(macro)
    recorder.finish(truncation_reason='fixture_limit')
    episode = EpisodeReader(tmp_path, 'episode').read_episode()
    rows = episode['channels']
    if corrupt == 'missing_parent':
        rows['macros'].clear()
        episode['manifest']['files']['macros']['rows'] = 0
    elif corrupt == 'child_list':
        rows['macros'][0]['decision_seqs'].pop()
    elif corrupt == 'order':
        rows['transitions'][-1]['primitive_order'] += 1
    elif corrupt == 'actor':
        rows['macros'][0]['macro']['actor_id'] = 'home'
    else:
        rows['macros'][0]['status'] = 'interrupted'
    with pytest.raises(ValueError):
        validate_episode(episode['manifest'], rows)


@pytest.mark.parametrize('path', ['episode.partial', 'nested/episode.partial'])
def test_partial_suffix_is_reserved_before_creating_writer_files(tmp_path, path):
    with pytest.raises(RecordError, match='reserved'):
        JsonlEpisodeWriter(tmp_path, path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('kind', ['blitz', 'quick_snap'])
def test_recorded_special_turn_scopes_keep_round_zero(tmp_path, kind):
    game, recorder = record_game(tmp_path)
    until(game, lambda g: isinstance(g.get_procedure(), proc.PlaceBall))
    with game.dice.force(d6=[1, 5, 5 if kind == 'blitz' else 4], d8=[1], strict=True):
        recorder.advance(progress_action(game))
    assert recorder.timeline.context.team_turn_seq == 1
    assert recorder.timeline.context.round == 0
    recorder.advance(bb.Action(bb.ActionType.END_TURN))
    until(game, lambda g: type(g.get_procedure()) is proc.Turn)
    recorder.finish(truncation_reason='fixture_limit')
    events = EpisodeReader(tmp_path, 'episode').read_episode()['channels']['events']
    turns = [e for e in events if e['kind'] == 'team_turn_started']
    assert [e['data']['turn_kind'] for e in turns] == [kind, 'regular']
    assert [(e['context']['team_turn_seq'], e['context']['round']) for e in turns] == [(1, 0), (2, 1)]
