"""EVAL-01 known facts, independent finite counts, purity and channel boundaries."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields, is_dataclass, replace
from itertools import product
import json
from pathlib import Path
import pickle
import subprocess
import sys

import pytest

import botbowl as bb
from botbowl.lab.channels import (
    ENRICHED_PROFILE, InputProfile, export_channels, import_channels, make_channel, project_inputs,
)
from botbowl.lab.evaluation import (
    EvaluationContext, EvaluationOracle, EvaluationRecord, LABEL_SPECS, LabelRequest,
    UnknownLabelError, evaluation_channel, label_spec,
)
from botbowl.lab.observations import ObservationControl, observe
from botbowl.lab.protocols import Evaluator
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.records import RecordError
from botbowl.lab.rules import describe_rules
from botbowl.lab.timeline import Timeline, TimelineContext
from botbowl.lab.views import entity_view, graph_view, grid_view
from tests.lab.test_recording import record_game
from tests.lab.test_semantic_actions import semantic_game


FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'oracle-v1.json').read_text())
BLOCK_FIELDS = tuple(FIXTURE['one_die_no_skills_home_carrier'])


@pytest.fixture
def state():
    game = semantic_game(11)
    binding = ObservationControl(game)
    # Initial descriptor is retained, not regenerated from changing episode state.
    rules = describe_rules(game.config, game.ruleset, game.arena, game.state.home_team, game.state.away_team)
    game.init()
    participants = []
    for side, team in zip(('home', 'away'), game.state.teams):
        team.state.score = FIXTURE['scores'][side]
        for name, value in FIXTURE['resources'][side].items():
            setattr(team.state, name, value)
        player = team.players[0]
        player.role = deepcopy(player.role)
        player.role.skills = []
        player.extra_skills = []
        pos = FIXTURE['positions'][side + ':0']
        game.put(player, game.get_square(**pos))
        participants.append(player)
    ball = bb.Ball(participants[0].position, is_carried=True, on_ground=True)
    game.state.pitch.balls.append(ball)
    return game, binding, rules, participants, ball


def capture(state, requests, **kwargs):
    game, binding, rules, _, _ = state
    time = game.timeline.context if game.timeline is not None else TimelineContext('oracle-fixture', 'root')
    return EvaluationContext.capture(game, binding, time, requests, rules=rules, **kwargs)


def query(state, label, entity, related=None):
    return EvaluationOracle().evaluate(capture(state, [LabelRequest(label, entity, related)]))[0]


def available(record, expected):
    row = record.to_json()
    assert row['status'] == 'available' and row['unavailability'] is None
    assert row['value'] == expected
    assert type(row['value']) is type(expected)
    assert EvaluationRecord.from_json(row) == record
    return row


@pytest.mark.parametrize('side', ['home', 'away'])
def test_known_score_possession_resources_and_metadata(state, side):
    available(query(state, 'team.score', side), FIXTURE['scores'][side])
    available(query(state, 'team.possession', side), side == 'home')
    for name, value in FIXTURE['resources'][side].items():
        row = available(query(state, 'team.' + name, side), value)
        assert row['label']['observation_path'] == 'teams[].resources.' + name
        assert row['label']['visibility'] == 'evaluation_only'
        assert row['label']['category'] == 'state_fact'
        assert row['rules'] == state[2].to_json()
        assert row['context'] == row['available_at'] == TimelineContext('oracle-fixture', 'root').to_json()
    assert query(state, 'team.score', side).key == ('oracle-fixture', 'root', 0, 0, side, None, 'team.score', 1)


@pytest.mark.parametrize('side', ['home', 'away'])
def test_entity_status_location_and_terminal_facts(state, side):
    game, _, _, participants, _ = state
    player = participants[side == 'away']
    ref = side + ':0'
    available(query(state, 'player.position', ref), FIXTURE['positions'][ref])
    available(query(state, 'player.location', ref), 'pitch')
    for flag in ('up', 'used', 'stunned'):
        setattr(player.state, flag, True)
        available(query(state, 'player.' + flag, ref), True)
        setattr(player.state, flag, False)
        available(query(state, 'player.' + flag, ref), False)
    game.pitch_to_reserves(player)
    for terminal in (False, True):
        game.state.game_over = terminal
        available(query(state, 'match.terminal', 'match'), terminal)
        available(query(state, 'player.location', ref), 'reserves')
        assert query(state, 'player.position', ref).to_json()['unavailability']['code'] == 'off_pitch'
        available(query(state, 'player.up', ref), True)
        available(query(state, 'team.score', side), FIXTURE['scores'][side])


@pytest.mark.parametrize('location,field', [('ko', 'kod'), ('casualties', 'casualties'), ('dungeon', 'dungeon'), ('unplaced', None)])
def test_off_pitch_compartments(state, location, field):
    game, _, _, players, _ = state
    player = players[0]
    game.pitch_to_reserves(player)
    dugout = game.state.dugouts[player.team.team_id]
    dugout.reserves.remove(player)
    if field:
        getattr(dugout, field).append(player)
    available(query(state, 'player.location', 'home:0'), location)


def test_ball_possession_is_carried_not_merely_occupied(state):
    _, _, _, players, ball = state
    available(query(state, 'ball.position', 'ball:0'), FIXTURE['positions']['home:0'])
    available(query(state, 'ball.carrier', 'ball:0'), 'home:0')
    ball.position = players[1].position
    available(query(state, 'team.possession', 'away'), True)
    available(query(state, 'team.possession', 'home'), False)
    ball.is_carried, ball.on_ground = False, True
    available(query(state, 'ball.carrier', 'ball:0'), None)
    available(query(state, 'team.possession', 'away'), False)
    ball.position = None
    assert query(state, 'ball.position', 'ball:0').to_json()['status'] == 'unavailable'


@pytest.mark.parametrize('mode', ['none', 'multiple', 'missing_carrier', 'air_and_carried'])
def test_ambiguous_ball_is_unavailable_not_zero(state, mode):
    game, _, _, _, ball = state
    if mode == 'none':
        game.state.pitch.balls.clear()
    elif mode == 'multiple':
        game.state.pitch.balls.append(bb.Ball(game.get_square(8, 8)))
    elif mode == 'missing_carrier':
        ball.position = game.get_square(8, 8)
    else:
        ball.on_ground = False
    row = query(state, 'team.possession', 'home').to_json()
    assert row['status'] == 'unavailable' and row['value'] is None


@pytest.mark.parametrize('dx,dy', [(0, 0), (1, 0), (1, 1), (3, 2), (-2, -3)])
def test_geometry_independent_integer_results_and_symmetry(state, dx, dy):
    game, _, _, _, ball = state
    ball.is_carried = False
    ball.position = game.get_square(5 + dx, 5 + dy)
    for suffix, result in [('manhattan', abs(dx) + abs(dy)), ('chebyshev', max(abs(dx), abs(dy))),
                           ('adjacent', max(abs(dx), abs(dy)) == 1)]:
        for a, b in [('home:0', 'ball:0'), ('ball:0', 'home:0')]:
            available(query(state, 'geometry.' + suffix, a, b), result)
    ball.position = game.get_square(0, 0)
    assert query(state, 'geometry.adjacent', 'home:0', 'ball:0').to_json()['unavailability']['code'] == 'off_pitch'


def enumerate_block(n, chooser, a_block, d_block, dodge, tackle, strip, sure, carrier):
    """Six physical faces; exhaustive tuples and successive preference filters.

    No production face evaluator, rank/count formula or query is imported here.
    Geometry guarantees a safe empty direct push. Tie order: pow, stumble,
    push, both, skull. Duplicate physical push faces remain in the experiment.
    """
    faces = ('skull', 'both', 'push', 'push', 'stumble', 'pow')
    effects = {}
    for face in set(faces):
        events = set()
        if face == 'skull' or face == 'both' and not a_block:
            events.add('attacker_down')
        if face == 'pow' or face == 'both' and not d_block or face == 'stumble' and (not dodge or tackle):
            events.add('defender_down')
        if carrier == 'attacker' and 'attacker_down' in events:
            events.add('attacker_ball_loss')
        if carrier == 'defender' and ('defender_down' in events or strip and not sure and face in ('pow', 'stumble', 'push')):
            events.add('defender_ball_loss')
        effects[face] = events
    counts = dict.fromkeys(BLOCK_FIELDS, 0)
    other = 'defender' if chooser == 'attacker' else 'attacker'
    preferences = [(chooser + '_down', False), (other + '_down', True),
                   (chooser + '_ball_loss', False), (other + '_ball_loss', True)]
    for roll in product(faces, repeat=n):
        candidates = set(roll)
        for event, wanted in preferences:
            matching = {f for f in candidates if (event in effects[f]) == wanted}
            if matching:
                candidates = matching
        face = next(f for f in ('pow', 'stumble', 'push', 'both', 'skull') if f in candidates)
        for event in effects[face]:
            counts[event] += 1
    return {field: count / 6**n for field, count in counts.items()}


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('a_strength,d_strength,dice', [(1, 3, -3), (2, 3, -2), (3, 3, 1), (3, 2, 2), (3, 1, 3)])
def test_exact_blocks_exhaustive_physical_dice_and_supported_skills(state, side, a_strength, d_strength, dice):
    _, _, _, players, ball = state
    attacker, defender = players if side == 'home' else players[::-1]
    attacker.extra_st = a_strength - attacker.role.st
    defender.extra_st = d_strength - defender.role.st
    other = 'away' if side == 'home' else 'home'
    requests = [LabelRequest('block.' + field, side + ':0', other + ':0') for field in BLOCK_FIELDS]
    for flags in product((False, True), repeat=6):
        a_block, d_block, dodge, tackle, strip, sure = flags
        attacker.extra_skills = [s for s, enabled in [(bb.Skill.BLOCK, a_block), (bb.Skill.TACKLE, tackle), (bb.Skill.STRIP_BALL, strip)] if enabled]
        defender.extra_skills = [s for s, enabled in [(bb.Skill.BLOCK, d_block), (bb.Skill.DODGE, dodge), (bb.Skill.SURE_HANDS, sure)] if enabled]
        for carrier in ('none', 'attacker', 'defender'):
            ball.is_carried = carrier != 'none'
            ball.on_ground = True
            ball.position = defender.position if carrier == 'defender' else attacker.position
            expected = enumerate_block(abs(dice), 'attacker' if dice > 0 else 'defender', *flags, carrier)
            results = EvaluationOracle().evaluate(capture(state, requests))
            for field, record in zip(BLOCK_FIELDS, results):
                row = record.to_json()
                assert row['status'] == 'available', row
                assert type(row['value']) is float
                assert row['value'] == pytest.approx(expected[field], abs=1e-12, rel=0)
                assert row['provenance']['signed_dice'] == dice
                assert row['label']['unit'] == 'probability'
                assert row['label']['observation_path'] is None


def test_known_block_fixture(state):
    for name, (numerator, denominator) in FIXTURE['one_die_no_skills_home_carrier'].items():
        available(query(state, 'block.' + name, 'home:0', 'away:0'), numerator / denominator)


@pytest.mark.parametrize('mode', ['terminal', 'off_pitch', 'prone', 'stunned', 'air', 'rooted', 'skill', 'other_skill',
                                  'nonadjacent', 'same_side', 'crowd', 'neighbour', 'ruleset', 'multiple_balls'])
def test_block_unavailability_is_explicit(state, mode):
    game, _, _, players, _ = state
    related = 'away:0'
    if mode == 'terminal':
        game.state.game_over = True
    elif mode == 'off_pitch':
        game.pitch_to_reserves(players[1])
    elif mode in ('prone', 'stunned', 'air', 'rooted'):
        setattr(players[1].state, {'prone': 'up', 'stunned': 'stunned', 'air': 'in_air', 'rooted': 'taken_root'}[mode], mode != 'prone')
    elif mode == 'skill':
        players[0].extra_skills = [bb.Skill.FRENZY]
    elif mode == 'other_skill':
        extra = game.state.home_team.players[1]
        game.put(extra, game.get_square(10, 10))
        extra.extra_skills = [bb.Skill.GUARD]
    elif mode == 'nonadjacent':
        game.move(players[1], game.get_square(9, 5))
    elif mode == 'same_side':
        related = 'home:0'
    elif mode == 'crowd':
        game.move(players[1], game.get_square(1, 5))
        game.move(players[0], game.get_square(2, 5))
    elif mode == 'neighbour':
        extra = game.state.home_team.players[1]
        extra.role = deepcopy(extra.role)
        extra.role.skills = []
        game.put(extra, game.get_square(7, 5))
    elif mode == 'ruleset':
        game.config.ruleset = 'custom'
    elif mode == 'multiple_balls':
        game.state.pitch.balls.append(bb.Ball(game.get_square(8, 8)))
    row = query(state, 'block.attacker_down', 'home:0', related).to_json()
    assert row['status'] == 'unavailable' and row['value'] is None and row['provenance'] is None
    assert row['unavailability']['reason']


def test_unknown_labels_entities_requests_and_closed_registry(state):
    with pytest.raises(UnknownLabelError):
        LabelRequest('tactical.ground_truth', 'home')
    with pytest.raises(TypeError):
        LABEL_SPECS['new'] = label_spec('team.score')
    with pytest.raises(FrozenInstanceError):
        label_spec('team.score').unit = 'points'
    for label, entity, related in [('team.score', 'home:0', None), ('player.up', 'home:99', None),
                                   ('ball.carrier', 'ball:99', None), ('block.attacker_down', 'home:99', 'away:0')]:
        row = query(state, label, entity, related).to_json()
        assert row['status'] == 'unavailable' and row['unavailability']['code'] == 'unknown_entity'
    for args in [('team.score', 'home', 'away'), ('geometry.adjacent', 'home:0'), ('team.score', True)]:
        with pytest.raises(RecordError):
            LabelRequest(*args)
    request = LabelRequest('team.score', 'home')
    with pytest.raises(RecordError):
        capture(state, [request, request])


def estimate_provenance():
    return {'policy': 'fixed-baseline-v1', 'horizon': {'kind': 'terminal', 'decisions': None},
            'method': 'external-rollouts-v1', 'uncertainty': {
                'kind': 'interval', 'lower': 0.2, 'upper': 0.8, 'coverage': 0.95, 'method': 'external-Wilson'}}


def external(state, label='estimated.win_probability', entity='home', value=0.5, provenance=None):
    request = LabelRequest(label, entity)
    context = capture(state, [request])
    return EvaluationRecord.external(context, request, value,
                                     available_at=TimelineContext('oracle-fixture', 'root', event_seq=3, decision_seq=1),
                                     provenance=estimate_provenance() if provenance is None else provenance)


def test_external_data_attribution_time_and_detachment(state):
    data = estimate_provenance()
    record = external(state, provenance=data)
    row = available(record, 0.5)
    assert row['label']['category'] == 'estimated' and row['label']['origin'] == 'external'
    assert row['available_at']['decision_seq'] == 1 and row['context']['decision_seq'] == 0
    data['uncertainty']['lower'] = 0.4
    assert record.to_json()['provenance']['uncertainty']['lower'] == 0.2
    for label, entity, value in [('heuristic.positional_advantage', 'away', -0.25),
                                  ('human.tactical_annotation', 'home:0', 'Consider marking the carrier.')]:
        p = {'policy': 'not_applicable', 'horizon': {'kind': 'decisions', 'decisions': 0},
             'method': 'annotator-or-method-v1', 'uncertainty': {'kind': 'not_quantified', 'reason': 'Subjective judgement'}}
        available(external(state, label, entity, value, p), value)
    row = query(state, 'estimated.win_probability', 'home').to_json()
    assert row['status'] == 'unavailable' and row['unavailability']['code'] == 'external_required'


@pytest.mark.parametrize('mode', ['policy', 'horizon', 'method', 'uncertainty', 'empty_policy', 'module',
                                  'exact_uncertainty', 'interval', 'coverage', 'horizon_count', 'finite_horizon'])
def test_invalid_estimates_rejected(state, mode):
    p = estimate_provenance()
    if mode in ('policy', 'horizon', 'method', 'uncertainty'):
        del p[mode]
    elif mode == 'empty_policy':
        p['policy'] = ' '
    elif mode == 'module':
        p['module'] = 'os.system'
    elif mode == 'exact_uncertainty':
        p['uncertainty'] = {'kind': 'exact'}
    elif mode == 'interval':
        p['uncertainty']['lower'] = 0.6
    elif mode == 'coverage':
        p['uncertainty']['coverage'] = 0
    elif mode == 'horizon_count':
        p['horizon']['decisions'] = True
    else:
        p['horizon'] = {'kind': 'decisions', 'decisions': 2}
    with pytest.raises(RecordError):
        external(state, provenance=p)


@pytest.mark.parametrize('value', [True, -0.1, 1.1, float('nan'), float('inf'), '0.5', {}, [0.5]])
def test_estimate_numeric_domain_is_closed(state, value):
    with pytest.raises(RecordError):
        external(state, value=value)


@pytest.mark.parametrize('field,value', [('category', 'exact_rule_quantity'), ('version', 2), ('version', True),
                                         ('unit', 'ground_truth'), ('visibility', 'primary'),
                                         ('origin', 'Game'), ('logical_availability', 'captured_boundary')])
def test_external_cannot_be_relabelled_exact_or_input(state, field, value):
    row = external(state).to_json()
    row['label'][field] = value
    with pytest.raises(RecordError):
        EvaluationRecord.from_json(row)
    with pytest.raises(RecordError):
        external(state, 'team.score', value=1)


def test_record_rejects_corrupt_time_descriptor_and_value_types(state):
    original = query(state, 'team.score', 'home').to_json()
    for path, value in [(('value',), True), (('schema_version',), True), (('rules', 'config_digest'), 'invalid'),
                        (('context', 'event_seq'), True), (('available_at', 'branch_id'), 'future'),
                        (('available_at', 'event_seq'), 1), (('label', 'unknown'), 'extra')]:
        row = deepcopy(original)
        target = row
        for name in path[:-1]:
            target = target[name]
        target[path[-1]] = value
        with pytest.raises(RecordError):
            EvaluationRecord.from_json(row)
    request = LabelRequest('team.score', 'home')
    for time in [TimelineContext('fixture', 'root', event_seq=-1),
                 TimelineContext('fixture', 'root', half=2)]:
        with pytest.raises(RecordError):
            EvaluationContext.capture(state[0], state[1], time, [request], rules=state[2])


def test_stale_and_foreign_timeline_rejected():
    game = semantic_game(1)
    binding = ObservationControl(game)
    rules = describe_rules(game.config, game.ruleset, game.arena, *game.state.teams)
    timeline = Timeline(game, episode_id='timeline')
    request = LabelRequest('team.score', 'home')
    for time in [replace(timeline.context, branch_id='foreign'), replace(timeline.context, decision_seq=1)]:
        with pytest.raises(RecordError):
            EvaluationContext.capture(game, binding, time, [request], rules=rules)


@pytest.mark.parametrize('forward_model', [False, True])
def test_purity_detachment_nested_results_forced_rng_and_failures(state, forward_model, monkeypatch):
    game, _, _, _, _ = state
    if forward_model:
        game.enable_forward_model()
    requests = [LabelRequest('block.' + field, 'home:0', 'away:0') for field in BLOCK_FIELDS]
    requests += [LabelRequest('player.position', 'home:0'), LabelRequest('team.score', 'home')]
    oracle: Evaluator[EvaluationContext, tuple] = EvaluationOracle()
    with game.dice.force(d6=[2, 5], block_dice=[bb.BBDieResult.ATTACKER_DOWN], strict=True):
        before, rng = pickle.dumps(game), game.capture_rng_state()
        context = capture(state, requests, privileged={'oracle': 'ORACLE_CANARY', 'rng': 'RNG_CANARY', 'futures': {'nested': ['FUTURE_CANARY']}})
        records = oracle.evaluate(context)
        for _ in range(3):
            assert oracle.evaluate(context) == records
            assert oracle.evaluate(capture(state, requests)) == records
        data = records[-2].to_json()
        data['value']['x'] = 99
        data['rules'].clear()
        evaluation_channel(records)['labels']['records'].clear()
        assert pickle.dumps(game) == before and game.capture_rng_state() == rng
        assert records[-2].to_json()['value']['x'] == 5
        # Even in dataclass recursion the context retains bytes, never Game.
        assert type(asdict(context)['_encoded']) is bytes
        assert all(type(getattr(context, f.name)) is bytes for f in fields(context))
        with pytest.raises(FrozenInstanceError):
            context._encoded = b'{}'
        def fail(*args, **kwargs):
            raise RuntimeError('injected helper failure')
        monkeypatch.setattr(bb.Game, 'get_block_outcome_probs', fail)
        before, rng = pickle.dumps(game), game.capture_rng_state()
        with pytest.raises(RuntimeError, match='injected helper failure'):
            capture(state, requests)
        assert pickle.dumps(game) == before and game.capture_rng_state() == rng
    # Old snapshot remains old after the engine changes.
    game.state.home_team.state.score = 9
    assert records[-1].to_json()['value'] == 2
    assert oracle.evaluate(context) == records


def _plain_view(value):
    if is_dataclass(value):
        return _plain_view(asdict(value))
    if hasattr(value, 'tolist'):
        return value.tolist()
    if isinstance(value, dict):
        return {key: _plain_view(v) for key, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_view(v) for v in value]
    return value


def test_leakage_canaries_through_nested_channels_standard_reader_and_views(tmp_path, monkeypatch):
    game, recorder = record_game(tmp_path)
    time, binding = recorder.timeline.context, recorder.timeline._entities
    from botbowl.lab.rules import RulesDescriptor
    rules = RulesDescriptor(**recorder._provenance['rules'])
    canaries = {'oracle': 'ORACLE_CANARY', 'rng': ['RNG_CANARY'], 'futures': {'nested': ['FUTURE_CANARY']}}
    requests = [LabelRequest('team.score', 'home')]
    context = EvaluationContext.capture(game, binding, time, requests, rules=rules, privileged=canaries)
    records = EvaluationOracle().evaluate(context)
    target = evaluation_channel(records)
    target['provenance']['nested'] = canaries
    primary = observe(game, binding, 'home').to_json()
    channels = {'primary': make_channel('primary', primary),
                'derived': make_channel('derived', {name: [[0.0]] for name in (
                    'own_tackle_zones', 'opp_tackle_zones', 'roll_probabilities', 'block_dice')}),
                'evaluation': make_channel('evaluation', target),
                'privileged': make_channel('privileged', canaries)}
    manifest, blobs = export_channels(channels, list(channels))
    assert 'FUTURE_CANARY' in blobs['evaluation'] and 'RNG_CANARY' in blobs['privileged']
    loaded = []
    def load(name):
        assert name in ('primary', 'derived')
        loaded.append(name)
        return blobs[name]
    selected, _ = import_channels(manifest, load, ['primary', 'derived'])
    outputs = [project_inputs(channels), project_inputs(channels, ENRICHED_PROFILE),
               project_inputs(selected, ENRICHED_PROFILE)]
    for view in (entity_view, graph_view, grid_view):
        outputs.append(_plain_view(view(primary)))
        with pytest.raises(ValueError):
            view(channels)
    for path in ('evaluation.labels.records[].value', 'privileged.rng[]', 'primary.players[].oracle'):
        with pytest.raises(ValueError):
            InputProfile('custom', ((path, 'number'),))
    observation_id = recorder.records['primary'][-1]['observation_id']
    recorder.append_channel('evaluation', observation_id, target)
    recorder.append_channel('privileged', observation_id, canaries)
    recorder.finish(truncation_reason='fixture_complete')
    reader = EpisodeReader(tmp_path, 'episode')
    original = reader.read_channels
    def guarded(names):
        assert 'evaluation' not in names and 'privileged' not in names
        return original(names)
    monkeypatch.setattr(reader, 'read_channels', guarded)
    outputs.append(reader.read_inputs())
    serialized = json.dumps(outputs)
    assert all(canary not in serialized for canary in ('ORACLE_CANARY', 'RNG_CANARY', 'FUTURE_CANARY'))
    assert loaded == ['primary', 'derived']
    explicit = original(['evaluation'])['evaluation'][0]['channel']['data']
    assert EvaluationRecord.from_json(explicit['labels']['records'][0]) == records[0]
    # Payload injection at known and unknown nested primary leaves is rejected.
    for location in ('root', 'position', 'score'):
        injected = deepcopy(primary)
        if location == 'root':
            injected['evaluation'] = target
        elif location == 'position':
            injected['players'][0]['position']['value'] = canaries
            injected['players'][0]['position']['present'] = True
        else:
            injected['teams'][0]['score'] = {'nested': canaries}
        with pytest.raises(ValueError):
            project_inputs({'primary': {'descriptor': channels['primary']['descriptor'], 'metadata': {}, 'data': injected}})


def test_sidecar_duplicate_keys_rejected_and_imports_remain_explicit(state):
    record = query(state, 'team.score', 'home')
    with pytest.raises(RecordError):
        evaluation_channel([record, record])
    code = """
import sys
import botbowl.lab.observations, botbowl.lab.channels, botbowl.lab.views, botbowl.lab.recording
assert 'botbowl.lab.evaluation' not in sys.modules
import botbowl.lab.evaluation
assert not any(name in sys.modules for name in ('torch', 'gym', 'gymnasium', 'flask', 'pygame'))
"""
    subprocess.run([sys.executable, '-c', code], check=True)


def test_catalogue_all_metadata_and_typed_accessors(state):
    assert len(LABEL_SPECS) == 25
    for name, spec in LABEL_SPECS.items():
        assert spec.name == name and type(spec.version) is int and spec.version == 1
        assert spec.category in ('state_fact', 'exact_rule_quantity', 'estimated', 'heuristic', 'human_annotation')
        assert all(getattr(spec, field) for field in ('value_type', 'unit', 'domain', 'origin',
                                                     'logical_availability', 'visibility', 'conditions'))
        assert spec.visibility == 'evaluation_only'
    record = query(state, 'team.score', 'home')
    assert record.spec is LABEL_SPECS['team.score']
    assert record.value == 2 and record.unavailability is None
    assert record.context == TimelineContext('oracle-fixture', 'root')
    missing = query(state, 'player.up', 'home:999')
    assert missing.value is None and missing.unavailability.code == 'unknown_entity'
    with pytest.raises(TypeError):
        EvaluationContext()


@pytest.mark.parametrize('label,entity,value', [('heuristic.positional_advantage', 'home', 0.1),
                                                ('human.tactical_annotation', 'away:0', 'annotation')])
def test_invalid_heuristic_and_human_claims(state, label, entity, value):
    valid = {'policy': 'not_applicable', 'method': 'fixture',
             'horizon': {'kind': 'decisions', 'decisions': 0},
             'uncertainty': {'kind': 'not_quantified', 'reason': 'Subjective'}}
    for field in valid:
        p = deepcopy(valid)
        del p[field]
        with pytest.raises(RecordError):
            external(state, label, entity, value, p)
    p = deepcopy(valid)
    p['uncertainty'] = {'kind': 'exact', 'reason': 'Computed by a heuristic'}
    with pytest.raises(RecordError):
        external(state, label, entity, value, p)
    row = external(state, label, entity, value, valid).to_json()
    row['label']['category'] = 'exact_rule_quantity'
    with pytest.raises(RecordError):
        EvaluationRecord.from_json(row)
    for bad in ([True, -1.1, 1.1] if label.startswith('heuristic') else ['', ' ', 42]):
        with pytest.raises(RecordError):
            external(state, label, entity, bad, valid)


def test_external_availability_entities_and_executable_objects_rejected(state):
    ctx = capture(state, [LabelRequest('team.score', 'home')])
    request = LabelRequest('estimated.win_probability', 'home')
    for time in [TimelineContext('other', 'root'), TimelineContext('oracle-fixture', 'other'),
                 TimelineContext('oracle-fixture', 'root', event_seq=-1)]:
        with pytest.raises(RecordError):
            EvaluationRecord.external(ctx, request, 0.5, available_at=time, provenance=estimate_provenance())
    with pytest.raises(RecordError):
        external(state, 'human.tactical_annotation', 'home:999', 'unknown')
    class Executable:
        def __reduce__(self):
            pytest.fail('External object executed')
    p = estimate_provenance()
    p['method'] = Executable()
    with pytest.raises(RecordError):
        external(state, provenance=p)
    with pytest.raises(RecordError):
        capture(state, [], privileged={'object': Executable()})


def test_no_advancement_refresh_clock_or_trajectory_callbacks(state, monkeypatch):
    game = state[0]
    game.enable_forward_model()
    trajectory, entries = game.trajectory, tuple(game.trajectory.action_log)
    def fail(*args, **kwargs):
        pytest.fail('Query attempted an advancing callback')
    for method in ('step', 'advance', 'set_available_actions'):
        monkeypatch.setattr(bb.Game, method, fail)
    before, rng = pickle.dumps(game), game.capture_rng_state()
    available(query(state, 'block.attacker_down', 'home:0', 'away:0'), 1 / 3)
    assert game.trajectory is trajectory and tuple(game.trajectory.action_log) == entries
    assert pickle.dumps(game) == before and game.capture_rng_state() == rng


def test_invalid_stored_quantity_is_unavailable(state):
    state[0].state.home_team.state.rerolls = -1
    record = query(state, 'team.rerolls', 'home')
    assert record.value is None and record.unavailability.code == 'unsupported_state'
