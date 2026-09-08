"""Validated, inert DATA-02 records. See docs/lab/recording.md for the wire contract."""
from dataclasses import dataclass
import json
import math
import re

from .actions import ActionV1, macro_from_json
from .channels import InputProfile, project_inputs, select_channels


MAX_DEPTH = 40
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_EPISODE_BYTES = 128 * 1024 * 1024
MAX_ROWS = 100000
CHANNEL_FILES = {
    'primary': 'inputs/primary.jsonl', 'derived': 'inputs/derived.jsonl',
    'control': 'control/inputs.jsonl', 'transitions': 'control/transitions.jsonl',
    'macros': 'control/macros.jsonl', 'events': 'events/events.jsonl',
    'diagnostics': 'diagnostics/operations.jsonl',
    'evaluation': 'evaluation/targets.jsonl', 'privileged': 'privileged/audit.jsonl',
}
SCHEMAS = {name: 1 for name in ('EpisodeManifestV1', 'TransitionV1', 'EventV1',
                              'ObservationV1', 'ActionV1', 'InputProfile', 'TimelineContext')}
_CONTEXT = ('episode_id', 'branch_id', 'event_seq', 'decision_seq', 'activation_seq',
            'team_turn_seq', 'drive_seq', 'half', 'round')
_SCOPES = ('activation_seq', 'team_turn_seq', 'drive_seq', 'half', 'round')


class RecordError(ValueError):
    """Malformed, incompatible, oversized or causally inconsistent data."""


def require(condition, message):
    if not condition:
        raise RecordError(message)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'Unknown or missing fields')


def integer(value, minimum=0):
    require(type(value) is int and minimum <= value < 2**63, 'Expected bounded integer')


def identifier(value):
    require(type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value)),
            'Expected public identifier')


def safe_relative(value):
    require(type(value) is str and len(value) <= 240 and all(
        re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', part) and part not in ('.', '..')
        for part in value.split('/')), 'Expected safe relative path')
    return value


def _plain(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    require(depth <= MAX_DEPTH and count[0] <= MAX_ROWS * 10, 'JSON depth/node limit')
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        require(-2**256 < value < 2**256, 'JSON integer limit')
    elif kind is float:
        require(math.isfinite(value), 'Non-finite JSON number')
    elif kind is str:
        require(len(value) <= MAX_RECORD_BYTES, 'JSON string limit')
    elif kind in (list, dict):
        require(len(value) <= MAX_ROWS, 'JSON collection limit')
        if kind is dict:
            for key in value:
                require(type(key) is str, 'JSON object keys must be strings')
                _plain(key, depth + 1, count)
        for item in (value.values() if kind is dict else value):
            _plain(item, depth + 1, count)
    else:
        raise RecordError('Only plain JSON data is accepted')


def encode_json(value):
    _plain(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (ValueError, UnicodeError) as error:
        raise RecordError('Invalid UTF-8 JSON') from error
    require(len(encoded) <= MAX_RECORD_BYTES, 'JSON record byte limit')
    return encoded


def decode_json(payload):
    require(type(payload) is bytes and len(payload) <= MAX_RECORD_BYTES, 'JSON record byte limit')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(RecordError('Non-finite JSON')),
                           parse_int=lambda token: int(token) if len(token) <= 79 else
                           (_ for _ in ()).throw(RecordError('JSON integer limit')))
        _plain(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RecordError('Invalid bounded UTF-8 JSON') from error


def context(value):
    keys(value, _CONTEXT)
    identifier(value['episode_id'])
    identifier(value['branch_id'])
    for field in ('event_seq', 'decision_seq'):
        integer(value[field])
    for field in ('activation_seq', 'team_turn_seq', 'drive_seq', 'half', 'round'):
        if value[field] is not None:
            integer(value[field], 0 if field == 'round' else 1)
    require(value['half'] in (None, 1, 2), 'Unknown half')


def identity(value, ctx, sequence):
    require(type(value) is list and len(value) == 3, 'Malformed record ID')
    identifier(value[0])
    identifier(value[1])
    integer(value[2], 1)
    require(value == [ctx['episode_id'], ctx['branch_id'], ctx[sequence]], 'Corrupt record ID')


def _end(value):
    if value is not None:
        keys(value, ('kind', 'reason'))
        require(value['kind'] in ('terminal', 'truncated'), 'Unknown end kind')
        identifier(value['reason'])
        if value['kind'] == 'terminal':
            require(value['reason'] == 'game_over', 'Unknown natural end reason')


@dataclass(frozen=True, init=False)
class _Record:
    """Canonical JSON storage prevents borrowed nested mutable state."""
    _encoded: bytes

    def __init__(self, data):
        encoded = encode_json(data)
        try:
            self._validate(data)
        except (ValueError, TypeError, KeyError) as error:
            raise RecordError(str(error)) from error
        object.__setattr__(self, '_encoded', encoded)

    @classmethod
    def from_json(cls, data):
        return cls(data)

    def to_json(self):
        return decode_json(self._encoded)


@dataclass(frozen=True, init=False)
class EventV1(_Record):
    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'event_id', 'context', 'decision_seq', 'kind', 'payload_version', 'data'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1, 'Unknown Event version')
        require(type(data['payload_version']) is int and data['payload_version'] == 1, 'Unknown event payload version')
        ctx = data['context']
        context(ctx)
        integer(ctx['event_seq'], 1)
        identity(data['event_id'], ctx, 'event_seq')
        if data['decision_seq'] is not None:
            integer(data['decision_seq'], 1)
            require(data['decision_seq'] == ctx['decision_seq'], 'Invalid event causation')
        kinds = {
            'report': ('outcome_type', 'pos', 'player_id', 'opp_player_id', 'rolls', 'team_id', 'n', 'skill'),
            'half_started': (), 'round_started': (), 'drive_started': (),
            'drive_ended': ('reason',), 'team_turn_started': ('team_id', 'turn_kind'),
            'team_turn_ended': ('reason',), 'activation_started': ('player_id', 'action_type'),
            'activation_ended': ('reason',),
        }
        require(type(data['kind']) is str and data['kind'] in kinds, 'Unknown event kind')
        payload = data['data']
        expected = kinds[data['kind']]
        if data['kind'] == 'activation_ended' and 'player_id' in payload:
            expected += ('player_id',)
        if data['kind'] == 'team_turn_ended' and 'team_id' in payload:
            expected = ('team_id',)
        keys(payload, expected)
        for field in ('player_id', 'opp_player_id'):
            if payload.get(field) is not None:
                require(bool(re.fullmatch(r'(home|away):[0-9]+', payload[field])), 'Invalid player reference')
        if 'team_id' in payload:
            require(payload['team_id'] in ('home', 'away', None), 'Invalid team reference')
        if data['kind'] == 'report':
            from botbowl.core.table import BBDieResult, CasualtyEffect, OutcomeType, RollType, Skill
            require(payload['outcome_type'] in OutcomeType.__members__, 'Unknown outcome')
            require(payload['skill'] is None or payload['skill'] in Skill.__members__, 'Unknown skill')
            if payload['pos'] is not None:
                keys(payload['pos'], ('x', 'y'))
                for coordinate in payload['pos'].values():
                    require(type(coordinate) is int and abs(coordinate) < 2**63, 'Invalid position')
            require(payload['n'] is None or type(payload['n']) in (int, float, bool) or
                    type(payload['n']) is str and payload['n'] in CasualtyEffect.__members__,
                    'Invalid report quantity/effect')
            require(type(payload['rolls']) is list, 'Expected report rolls')
            for roll in payload['rolls']:
                keys(roll, ('dice', 'sum', 'target', 'modifiers', 'modified_target', 'result',
                            'roll_type', 'target_higher', 'target_lower', 'highest_succeed', 'lowest_fail'))
                require(type(roll['dice']) is list, 'Expected dice list')
                require(roll['roll_type'] in RollType.__members__, 'Unknown roll type')
                for field in ('sum', 'target', 'modifiers', 'modified_target', 'result'):
                    require((roll[field] is None and field in ('target', 'modified_target')) or
                            type(roll[field]) is int and abs(roll[field]) < 2**63,
                            'Invalid roll quantity')
                for die in roll['dice']:
                    keys(die, ('die_type', 'result'))
                    require(die['die_type'] in ('D3', 'D6', 'D8', 'BB'), 'Unknown die type')
                    if die['die_type'] == 'BB':
                        require(die['result'] in BBDieResult.__members__, 'Unknown block face')
                    else:
                        require(type(die['result']) is int and 1 <= die['result'] <= int(die['die_type'][1:]),
                                'Invalid die result')
                for field in ('target_higher', 'target_lower', 'highest_succeed', 'lowest_fail'):
                    require(type(roll[field]) is bool, 'Invalid roll flag')
        elif 'reason' in payload:
            identifier(payload['reason'])
        elif data['kind'] == 'team_turn_started':
            require(payload['turn_kind'] in ('regular', 'blitz', 'quick_snap'), 'Unknown turn kind')
        elif data['kind'] == 'activation_started':
            require(type(payload['action_type']) is str and payload['action_type'].startswith('START_'),
                    'Invalid activation action')


@dataclass(frozen=True, init=False)
class TransitionV1(_Record):
    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'transition_id', 'source_family', 'scenario_id', 'before', 'after',
                    'actor_id', 'action', 'pre_observation', 'post_observation', 'event_start', 'event_stop',
                    'next_actor_id', 'status', 'end', 'macro_id', 'primitive_order'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1, 'Unknown Transition version')
        for field in ('source_family', 'scenario_id'):
            identifier(data[field])
        before, after = data['before'], data['after']
        context(before)
        context(after)
        identity(data['transition_id'], after, 'decision_seq')
        require(all(before[f] == after[f] for f in ('episode_id', 'branch_id')), 'Cross-branch transition')
        require(after['decision_seq'] == before['decision_seq'] + 1, 'Invalid decision increment')
        require(after['event_seq'] >= before['event_seq'], 'Events move backwards')
        for field in ('activation_seq', 'team_turn_seq', 'drive_seq', 'half'):
            require(before[field] is None or after[field] is not None and after[field] >= before[field],
                    'Logical scope counter moves backwards')
        for field in ('pre_observation', 'post_observation', 'event_start', 'event_stop'):
            integer(data[field], 1)
        require(data['pre_observation'] <= data['post_observation'], 'Observation order moves backwards')
        require(data['event_start'] == before['event_seq'] + 1 and
                data['event_stop'] == after['event_seq'] + 1, 'Invalid event interval')
        action = ActionV1.from_json(data['action'])
        require(action.type != 'CONTINUE' and action.actor_id == data['actor_id'], 'Invalid accepted actor/action')
        require(data['next_actor_id'] in ('home', 'away', None), 'Invalid next actor')
        require(data['status'] in ('resolved', 'pending'), 'Unknown decision status')
        _end(data['end'])
        if data['end'] is not None and data['end']['kind'] == 'terminal':
            require(data['status'] == 'resolved' and data['next_actor_id'] is None, 'Invalid terminal boundary')
        require((data['macro_id'] is None) == (data['primitive_order'] is None), 'Incomplete macro parentage')
        if data['macro_id'] is not None:
            identifier(data['macro_id'])
            integer(data['primitive_order'])


def validate_provenance(provenance):
    keys(provenance, ('rules', 'policies', 'seed_plan'))
    keys(provenance['rules'], ('ruleset_id', 'ruleset_version', 'engine_version', 'config_id',
                               'config_digest', 'backend_id', 'capabilities_version'))
    for value in provenance['rules'].values():
        require(type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,255}', value)),
                'Invalid rules descriptor field')
    require(bool(re.fullmatch(r'sha256:[0-9a-f]{64}', provenance['rules']['config_digest'])), 'Invalid config digest')
    keys(provenance['policies'], ('home', 'away'))
    for policy in provenance['policies'].values():
        keys(policy, ('id', 'version'))
        identifier(policy['id'])
        if policy['version'] is not None:
            identifier(policy['version'])
    plan = provenance['seed_plan']
    keys(plan, ('schema_version', 'algorithm', 'sources'))
    require(type(plan['schema_version']) is int and plan['schema_version'] == 1, 'Unknown seed plan version')
    identifier(plan['algorithm'])
    require(type(plan['sources']) is dict and bool(plan['sources']), 'Missing seed sources')
    for name in plan['sources']:
        identifier(name)


@dataclass(frozen=True, init=False)
class EpisodeManifestV1(_Record):
    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'episode_id', 'source_family', 'scenario_id', 'initial_context',
                    'final_context', 'initial_observation', 'final_observation', 'end', 'provenance',
                    'profile', 'schemas', 'branches', 'files'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1, 'Unknown Manifest version')
        for field in ('episode_id', 'source_family', 'scenario_id'):
            identifier(data[field])
        for field in ('initial_context', 'final_context'):
            context(data[field])
            require(data[field]['episode_id'] == data['episode_id'], 'Foreign episode context')
        require(data['final_context']['decision_seq'] <= MAX_ROWS and
                data['final_context']['event_seq'] <= MAX_ROWS, 'Episode sequence limit')
        require(data['initial_context']['decision_seq'] == data['initial_context']['event_seq'] == 0,
                'A recording must start at the empty timeline prefix')
        for field in ('initial_observation', 'final_observation'):
            integer(data[field], 1)
        _end(data['end'])
        require(data['end'] is not None, 'Unconfirmed episode end')
        require(data['schemas'] == SCHEMAS and all(type(v) is int for v in data['schemas'].values()),
                'Unknown schema versions')
        InputProfile.from_json(data['profile'])
        require(type(data['branches']) is list and bool(data['branches']), 'Missing branches')
        seen = set()
        for index, branch in enumerate(data['branches']):
            keys(branch, ('branch_id', 'parent'))
            identifier(branch['branch_id'])
            require(branch['branch_id'] not in seen, 'Duplicate branch')
            if index == 0:
                require(branch['parent'] is None and branch['branch_id'] == data['initial_context']['branch_id'],
                        'Invalid initial branch')
            else:
                context(branch['parent'])
                require(branch['parent']['episode_id'] == data['episode_id'] and
                        branch['parent']['branch_id'] in seen, 'Invalid branch parent')
            seen.add(branch['branch_id'])
        validate_provenance(data['provenance'])
        files = data['files']
        require(type(files) is dict and not set(files) - set(CHANNEL_FILES), 'Unknown storage channels')
        require({'primary', 'transitions', 'events', 'macros', 'diagnostics'} <= set(files), 'Missing required files')
        total = 0
        for name, info in files.items():
            keys(info, ('path', 'schema_version', 'rows', 'bytes', 'sha256'))
            safe_relative(info['path'])
            require(info['path'] == CHANNEL_FILES[name], 'Channel path alias or mismatch')
            require(type(info['schema_version']) is int and info['schema_version'] == 1, 'Unknown file version')
            integer(info['rows'])
            integer(info['bytes'])
            require(info['rows'] <= MAX_ROWS and info['bytes'] <= MAX_EPISODE_BYTES, 'File limit')
            require(type(info['sha256']) is str and bool(re.fullmatch(r'[0-9a-f]{64}', info['sha256'])),
                    'Invalid file digest')
            total += info['bytes']
        require(total <= MAX_EPISODE_BYTES, 'Episode byte limit')


def _validate_row(name, row):
    """Validate a single channel row; whole-episode reference checks are separate."""
    encode_json(row)
    if name == 'transitions':
        TransitionV1(row)
    elif name == 'events':
        EventV1(row)
    elif name in ('primary', 'derived', 'control'):
        keys(row, ('schema_version', 'observation_id', 'context', 'channel'))
        require(type(row['schema_version']) is int and row['schema_version'] == 1, 'Unknown observation row version')
        integer(row['observation_id'], 1)
        context(row['context'])
        select_channels({name: row['channel']}, [name])
        if name == 'primary':
            require(row['channel']['data']['observer_team'] == 'home', 'Canonical public observer must be home')
    elif name in ('evaluation', 'privileged'):
        keys(row, ('schema_version', 'observation_id', 'channel'))
        require(type(row['schema_version']) is int and row['schema_version'] == 1, 'Unknown target row version')
        integer(row['observation_id'], 1)
        select_channels({name: row['channel']}, [name])
    elif name == 'diagnostics':
        keys(row, ('schema_version', 'context', 'error_type', 'code'))
        require(type(row['schema_version']) is int and row['schema_version'] == 1, 'Unknown diagnostic version')
        context(row['context'])
        identifier(row['error_type'])
        identifier(row['code'])
    elif name == 'macros':
        keys(row, ('schema_version', 'macro', 'before', 'after', 'status', 'decision_seqs', 'interruption'))
        require(type(row['schema_version']) is int and row['schema_version'] == 1, 'Unknown macro row version')
        macro_from_json(row['macro'])
        context(row['before'])
        context(row['after'])
        require(row['status'] in ('completed', 'interrupted'), 'Unknown macro status')
        require(type(row['decision_seqs']) is list, 'Invalid macro child list')
        for value in row['decision_seqs']:
            integer(value, 1)
        if row['interruption'] is not None:
            keys(row['interruption'], ('reason', 'next_order', 'actor_id', 'context'))
            identifier(row['interruption']['reason'])
            integer(row['interruption']['next_order'])
            require(row['interruption']['actor_id'] in ('home', 'away', None), 'Invalid interrupted actor')
            context(row['interruption']['context'])
        require((row['interruption'] is None) == (row['status'] == 'completed'), 'Invalid macro interruption')
    else:
        raise RecordError('Unknown channel')


def validate_row(name, row):
    try:
        _validate_row(name, row)
    except (ValueError, KeyError, TypeError) as error:
        raise RecordError(str(error)) from error


def _scope_prefixes(events):
    """Fold only API-02 counter events; no rules, legality or game simulation."""
    scopes = dict.fromkeys(_SCOPES)
    prefixes = [scopes.copy()]
    counters = {'activation': 'activation_seq', 'team_turn': 'team_turn_seq', 'drive': 'drive_seq'}
    opened = dict.fromkeys(counters, False)
    for event in events:
        kind = event['kind']
        if kind == 'half_started':
            scopes['half'] = (scopes['half'] or 0) + 1
            require(scopes['half'] <= 2, 'Half scope starts beyond second half')
            scopes['round'] = 0
        elif kind == 'round_started':
            require(scopes['half'] is not None, 'Round scope starts before a half')
            scopes['round'] += 1
        else:
            for name, field in counters.items():
                if kind == name + '_started':
                    scopes[field] = (scopes[field] or 0) + 1
                    opened[name] = True
                elif kind == name + '_ended':
                    require(opened[name], 'Ending a closed or unstarted scope')
                    opened[name] = False
        require(all(event['context'][field] == scopes[field] for field in _SCOPES),
                'Event scope context disagrees with phase stream')
        prefixes.append(scopes.copy())
    return prefixes


def validate_episode(manifest, rows):
    """Check a complete causal trace, including detached observation references."""
    data = EpisodeManifestV1(manifest).to_json()
    require(set(rows) == set(data['files']), 'Episode channel set mismatch')
    branches = data['branches']
    branch_starts = {b['branch_id']: b['parent'] or data['initial_context'] for b in branches}
    require(data['final_context']['branch_id'] == branches[-1]['branch_id'], 'Invalid final branch')
    for previous_branch, branch in zip(branches, branches[1:]):
        parent = branch['parent']
        start = branch_starts[previous_branch['branch_id']]
        require(parent['branch_id'] == previous_branch['branch_id'] and
                parent['decision_seq'] >= start['decision_seq'] and parent['event_seq'] >= start['event_seq'],
                'Branch history must be a forward continuation')

    def emission_branch(seq, field):
        return next(b['branch_id'] for b in reversed(branches)
                    if seq > branch_starts[b['branch_id']][field])

    for name, channel_rows in rows.items():
        require(len(channel_rows) == data['files'][name]['rows'], 'Row count mismatch')
        for row in channel_rows:
            validate_row(name, row)
    events = rows['events']
    require([e['context']['event_seq'] for e in events] == list(range(1, data['final_context']['event_seq'] + 1)),
            'Event IDs have gaps/duplicates')
    scope_prefixes = _scope_prefixes(events)
    branch_ends = {branch['branch_id']: branches[index + 1]['parent'] if index + 1 < len(branches)
                   else data['final_context'] for index, branch in enumerate(branches)}

    def check_context(ctx):
        context(ctx)
        require(ctx['episode_id'] == data['episode_id'] and ctx['branch_id'] in branch_starts,
                'Foreign episode/branch')
        start = branch_starts[ctx['branch_id']]
        require(ctx['event_seq'] >= start['event_seq'] and ctx['decision_seq'] >= start['decision_seq'],
                'Context predates branch')
        require(ctx['event_seq'] <= data['final_context']['event_seq'] and
                ctx['decision_seq'] <= data['final_context']['decision_seq'], 'Context beyond episode')
        end = branch_ends[ctx['branch_id']]
        require(ctx['event_seq'] <= end['event_seq'] and ctx['decision_seq'] <= end['decision_seq'],
                'Context beyond branch')
        expected = scope_prefixes[ctx['event_seq']]
        require(all(ctx[field] == expected[field] for field in _SCOPES),
                'Scope context disagrees with event prefix')

    check_context(data['initial_context'])
    check_context(data['final_context'])
    for branch in branches[1:]:
        check_context(branch['parent'])

    for channel_rows in rows.values():
        for row in channel_rows:
            if 'context' in row:
                check_context(row['context'])
    observations = rows['primary']
    require([r['observation_id'] for r in observations] == list(range(1, len(observations) + 1)),
            'Observation IDs have gaps/duplicates')
    lookup = {row['observation_id']: row for row in observations}
    for field in ('initial', 'final'):
        require(data[field + '_observation'] in lookup, 'Missing episode boundary observation')
        require(lookup[data[field + '_observation']]['context'] == data[field + '_context'],
                'Episode observation/context mismatch')
    initial_players = [p['id'] for p in observations[0]['channel']['data']['players']]
    require(len(initial_players) == len(set(initial_players)), 'Duplicate entity IDs')
    for row in observations:
        require([p['id'] for p in row['channel']['data']['players']] == initial_players, 'Entity binding changed')
    transitions = rows['transitions']
    require([t['after']['decision_seq'] for t in transitions] == list(range(1, data['final_context']['decision_seq'] + 1)),
            'Decision IDs have gaps/duplicates')
    causes = {}
    previous = None
    for index, transition in enumerate(transitions):
        before, after = transition['before'], transition['after']
        check_context(before)
        check_context(after)
        require(after['branch_id'] == emission_branch(after['decision_seq'], 'decision_seq'),
                'Decision emitted outside branch lifetime')
        require(transition['source_family'] == data['source_family'] and
                transition['scenario_id'] == data['scenario_id'], 'Transition provenance mismatch')
        require(transition['pre_observation'] in lookup and transition['post_observation'] in lookup,
                'Missing transition observation')
        pre, post = (lookup[transition[field + '_observation']] for field in ('pre', 'post'))
        require(pre['context'] == before and post['context'] == after, 'Observation/decision misalignment')
        pre_data, post_data = pre['channel']['data'], post['channel']['data']
        require(pre_data['decision']['pending'] and
                pre_data['decision']['actor_team']['value'] == transition['actor_id'], 'Pre-observation actor mismatch')
        if transition['status'] == 'resolved':
            require(post_data['decision']['actor_team']['value'] == transition['next_actor_id'],
                    'Post-observation actor mismatch')
        for field in ('player_id', 'target_id'):
            require(transition['action'][field] is None or transition['action'][field] in initial_players,
                    'Unknown action entity')
        terminal = post_data['match']['game_over']
        require(terminal == (transition['end'] is not None and transition['end']['kind'] == 'terminal'),
                'Terminal flag/end mismatch')
        if previous is not None:
            require(before['event_seq'] >= previous['after']['event_seq'], 'Overlapping event intervals')
            if before == previous['after']:
                require(pre['channel'] == lookup[previous['post_observation']]['channel'],
                        'Broken pre/post observation continuity')
        if index < len(transitions) - 1:
            require(transition['status'] == 'resolved' and transition['end'] is None, 'Decision after ended/pending step')
        for event in events[transition['event_start'] - 1:transition['event_stop'] - 1]:
            require(event['context']['branch_id'] == after['branch_id'] and
                    event['decision_seq'] == after['decision_seq'], 'Event outside causing branch/decision')
            causes[event['context']['event_seq']] = after['decision_seq']
        previous = transition
    for event in events:
        require(event['context']['branch_id'] == emission_branch(event['context']['event_seq'], 'event_seq'),
                'Event emitted outside branch lifetime')
        require(causes.get(event['context']['event_seq']) == event['decision_seq'], 'Unowned or duplicate causal event')
        for field in ('player_id', 'opp_player_id'):
            require(event['data'].get(field) is None or event['data'][field] in initial_players,
                    'Unknown event entity')
    final_data = lookup[data['final_observation']]['channel']['data']
    require(final_data['match']['game_over'] == (data['end']['kind'] == 'terminal'), 'Episode end mismatch')
    if transitions:
        last = transitions[-1]
        if last['after'] == data['final_context']:
            require(last['end'] == data['end'] and
                    lookup[last['post_observation']]['channel'] == lookup[data['final_observation']]['channel'],
                    'Final transition end/observation mismatch')
        else:
            require(last['end'] is None, 'Uncaused episode end attributed to prior decision')
    parents = {}
    for parent in rows['macros']:
        check_context(parent['before'])
        check_context(parent['after'])
        require(parent['before']['branch_id'] == parent['after']['branch_id'] and
                parent['before']['event_seq'] <= parent['after']['event_seq'] and
                parent['before']['decision_seq'] <= parent['after']['decision_seq'], 'Invalid macro boundary')
        if parent['interruption'] is not None:
            interruption_context = parent['interruption']['context']
            check_context(interruption_context)
            # The child may have resumed. Its new after is not this historical boundary.
            require(interruption_context == parent['after'], 'Macro interruption must match historical after')
        key = (parent['before']['branch_id'], parent['macro']['macro_id'])
        require(key not in parents, 'Duplicate macro ID')
        parents[key] = parent
        children = [t for t in transitions if (t['before']['branch_id'], t['macro_id']) == key]
        require(parent['decision_seqs'] == [t['after']['decision_seq'] for t in children], 'Macro child mismatch')
        require([t['primitive_order'] for t in children] == list(range(len(children))), 'Macro order gaps/duplicates')
        for child in children:
            require(child['actor_id'] == parent['macro']['actor_id'] and
                    child['before']['decision_seq'] >= parent['before']['decision_seq'] and
                    child['after']['decision_seq'] <= parent['after']['decision_seq'], 'Child outside macro')
    for transition in transitions:
        if transition['macro_id'] is not None:
            require((transition['before']['branch_id'], transition['macro_id']) in parents, 'Missing macro parent')
    for name in ('derived', 'control', 'evaluation', 'privileged'):
        seen = set()
        for row in rows.get(name, []):
            ref = row['observation_id']
            require(ref in lookup and ref not in seen, 'Unknown or duplicate side-channel observation')
            seen.add(ref)
            if 'context' in row:
                require(row['context'] == lookup[ref]['context'], 'Side-channel context mismatch')

    profile = InputProfile.from_json(data['profile'])
    selected = set(path.split('.')[0] for path, _ in profile.fields)
    side_lookup = {name: {row['observation_id']: row['channel'] for row in rows.get(name, [])}
                   for name in selected}
    for row in observations:
        ref = row['observation_id']
        require(all(ref in side_lookup[name] for name in selected), 'Incomplete recorded input profile')
        project_inputs({name: side_lookup[name][ref] for name in selected}, profile)
    return data
