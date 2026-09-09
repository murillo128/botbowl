"""EVAL-03: inert, causally bound multihorizon forecasts. No model is loaded.

Cases must originate in DATA-03 iter_windows; metadata is for the evaluator,
never automatic model input. See docs/lab/temporal.md for the trust boundary.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import math

from botbowl.core.table import OutcomeType
from botbowl.lab.actions import ActionV1
from botbowl.lab.evaluation.oracle import EvaluationRecord, LabelRequest, label_spec, _value
from botbowl.lab.evaluation.probes import _window
from botbowl.lab.records import (_Record, EventV1, context, encode_json, decode_json,
                                 identifier, integer, keys, require, RecordError)
from botbowl.lab.splits import validate_split_manifest, validate_window_membership
from botbowl.lab.windows import WindowSpecV1, validate_availability


DEFAULT_HORIZONS = (1, 2, 4, 8)
PROTOCOLS = ('teacher_forced', 'autonomous')


def _copy(value):
    return decode_json(encode_json(value))


def _time_key(ctx):
    return tuple(ctx[f] for f in ('episode_id', 'branch_id', 'event_seq', 'decision_seq'))


def _before(left, right):
    return (all(left[f] == right[f] for f in ('episode_id', 'branch_id')) and
            all(left[f] <= right[f] for f in ('event_seq', 'decision_seq')))


def _policy(value):
    keys(value, ('name', 'version', 'config'))
    identifier(value['name'])
    identifier(value['version'])
    require(type(value['config']) is dict, 'Expected policy configuration')
    encode_json(value)


@dataclass(frozen=True)
class TemporalTask:
    """Catalogue label, next actor, or report-event occurrence in (t, t+h]."""
    name: str
    entity_id: str = 'match'
    related_entity_id: str = None

    def __post_init__(self):
        if self.name in ('decision.actor', 'decision.actor_changed') or self.name.startswith('event.'):
            require(self.entity_id == 'match' and self.related_entity_id is None,
                    'Actor/event tasks have match scope')
            if self.name.startswith('event.'):
                require(self.name[6:] in OutcomeType.__members__, 'Unknown report event')
        else:
            LabelRequest(self.name, self.entity_id, self.related_entity_id)
            require(label_spec(self.name).category in ('state_fact', 'exact_rule_quantity'),
                    'Temporal reference tasks require facts or exact rule quantities')

    def to_json(self):
        return dict(self.__dict__)

    @property
    def kind(self):
        if self.name == 'decision.actor':
            return 'actor'
        if self.name.startswith('event.') or self.name == 'decision.actor_changed':
            return 'boolean'
        return label_spec(self.name).value_type

    @property
    def unit(self):
        if self.name == 'decision.actor':
            return 'team_id'
        if self.name.startswith('event.') or self.name == 'decision.actor_changed':
            return 'boolean'
        return label_spec(self.name).unit

    @property
    def family(self):
        return self.name.split('.')[0]


def prepare_case(window, *, split_manifest, protocol, continuation_policy,
                 budget, horizons=DEFAULT_HORIZONS, initial_window=None):
    """Freeze sample selection BEFORE looking at targets or external forecasts.

    Autonomous emissions may occur later than the initial window's cutoff, but
    only its observations and (in conditioned mode) selected initial action are
    known. V1 has no contract for future command sequences.
    """
    window = _copy(window)
    require(protocol in PROTOCOLS, 'Unknown information protocol')
    _policy(continuation_policy)
    integer(budget, 1)
    horizons = list(horizons)
    require(bool(horizons) and len(set(horizons)) == len(horizons), 'Empty/duplicate horizons')
    for h in horizons:
        integer(h, 1)
    spec = WindowSpecV1.from_json(window['metadata']['spec'])
    require(max(horizons) <= spec.horizon and max(horizons) <= budget, 'Horizon exceeds window/budget')
    _window(window, spec)
    if initial_window is None:
        initial_window = window
    initial = _copy(initial_window)
    initial_spec = WindowSpecV1.from_json(initial['metadata']['spec'])
    _window(initial, initial_spec)
    require(initial_spec == spec, 'Initial and emission views/windows differ')
    at, start = window['metadata']['cutoff'], initial['metadata']['cutoff']
    require(_before(start, at), 'Initial context is future or foreign')
    require(protocol == 'autonomous' or initial == window, 'Teacher-forced uses the emission window')
    require(at['decision_seq'] - start['decision_seq'] + max(horizons) <= budget,
            'Autonomous emission exceeds initial decision budget')
    split = validate_split_manifest(split_manifest).to_json()
    validate_window_membership(split, [window, initial])
    source = next(e for e in split['sources'] if e['source']['source_id'] == window['origin']['source_id'])
    require(initial['origin'] == window['origin'], 'Initial context has a different origin')
    for sample in (window, initial):
        require(sample['metadata']['family_id'] == source['family_id'] and
                sample['metadata']['split_version'] == split['split_version'], 'Family/split version mismatch')
    inputs = initial['inputs']
    keys(inputs, ('observations', 'presence', 'action') if spec.mode == 'action_conditioned'
         else ('observations', 'presence'))
    if spec.mode == 'action_conditioned':
        ActionV1.from_json(inputs['action'])
        validate_availability('action', start, start, spec)
    # Source-bound availability is checked again even for autonomous later emissions.
    for ref in initial['metadata']['history']:
        if ref is not None:
            for field, _ in spec.profile.fields:
                validate_availability(field, ref['available_at'], start, spec)
    request = {
        'schema_version': 1, 'origin': window['origin'], 'family_id': source['family_id'],
        'split': window['split'], 'split_version': split['split_version'],
        'emitted_at': at, 'last_visible_at': start, 'protocol': protocol,
        'window_spec': spec.to_json(), 'horizons': sorted(horizons),
        'continuation_policy': continuation_policy, 'budget': budget,
        'known_actions': 'initial_selected_only' if spec.mode == 'action_conditioned' else 'none',
        'history': initial['metadata']['history'], 'inputs': inputs,
    }
    # No targets, target presence, future actors, end reasons or labels enter the ID.
    case_id = hashlib.sha256(encode_json(request)).hexdigest()
    return _copy({'case_id': case_id, 'request': request, 'window': window, 'initial_window': initial})


def _check_case(case):
    keys(case, ('case_id', 'request', 'window', 'initial_window'))
    request = case['request']
    require(case['case_id'] == hashlib.sha256(encode_json(request)).hexdigest(), 'Changed case receipt')
    spec = WindowSpecV1.from_json(request['window_spec'])
    for name in ('window', 'initial_window'):
        sample = case[name]
        require(sample['metadata']['spec'] == spec.to_json(), 'Changed case specification')
        _window(sample, spec)
        require(sample['origin'] == request['origin'] and sample['split'] == request['split'] and
                sample['metadata']['family_id'] == request['family_id'] and
                sample['metadata']['split_version'] == request['split_version'], 'Changed case origin')
    require(request['emitted_at'] == case['window']['metadata']['cutoff'] and
            request['last_visible_at'] == case['initial_window']['metadata']['cutoff'] and
            request['inputs'] == case['initial_window']['inputs'] and
            request['history'] == case['initial_window']['metadata']['history'], 'Changed case inputs')


def forecast_request(case):
    """Audit envelope; pass ONLY the nested inputs to a predictor."""
    _check_case(case)
    return _copy({'case_id': case['case_id'], **case['request']})


@dataclass(frozen=True, init=False)
class ForecastV1(_Record):
    """External semantic predictions or undecoded embeddings, with input receipt.

    Semantic values are checked per task during evaluation so invalid predictions
    remain in the attempted denominator. Structural/causal violations reject the
    bundle instead of producing a misleading score.
    """
    @staticmethod
    def _validate(data):
        keys(data, ('schema_version', 'model_id', 'model_revision', 'request',
                    'representation', 'predictions', 'failure'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'Unknown forecast version')
        identifier(data['model_id'])
        identifier(data['model_revision'])
        request = data['request']
        keys(request, ('case_id', 'schema_version', 'origin', 'family_id', 'split', 'split_version',
                       'emitted_at', 'last_visible_at', 'protocol', 'window_spec', 'horizons',
                       'continuation_policy', 'budget', 'known_actions', 'history', 'inputs'))
        require(type(request['schema_version']) is int and request['schema_version'] == 1, 'Unknown request version')
        require(request['protocol'] in PROTOCOLS, 'Unknown protocol')
        for field in ('emitted_at', 'last_visible_at'):
            context(request[field])
        require(_before(request['last_visible_at'], request['emitted_at']), 'Future last-visible context')
        if request['protocol'] == 'teacher_forced':
            require(request['last_visible_at'] == request['emitted_at'], 'Stale teacher-forced context')
        spec = WindowSpecV1.from_json(request['window_spec'])
        inputs = request['inputs']
        keys(inputs, ('observations', 'presence', 'action') if spec.mode == 'action_conditioned'
             else ('observations', 'presence'))
        require(len(inputs['observations']) == len(inputs['presence']) == len(request['history']) ==
                spec.history_length, 'History dimensions disagree')
        for obs, present, ref in zip(inputs['observations'], inputs['presence'], request['history']):
            require(type(present) is bool and present == (obs is not None) == (ref is not None),
                    'History presence mismatch')
            if ref is not None:
                keys(ref, ('observation_id', 'available_at'))
                integer(ref['observation_id'], 1)
                require(set(obs) == set(dict(spec.profile.fields)), 'Unauthorized observation field')
                for field, _ in spec.profile.fields:
                    validate_availability(field, ref['available_at'], request['last_visible_at'], spec)
        require(request['history'][-1] is not None and
                request['history'][-1]['available_at'] == request['last_visible_at'], 'Missing last-visible observation')
        if spec.mode == 'action_conditioned':
            ActionV1.from_json(inputs['action'])
            validate_availability('action', request['last_visible_at'], request['last_visible_at'], spec)
        require(request['known_actions'] == ('initial_selected_only' if spec.mode == 'action_conditioned'
                                             else 'none'), 'Unknown future-action contract')
        _policy(request['continuation_policy'])
        integer(request['budget'], 1)
        require(type(request['horizons']) is list and bool(request['horizons']), 'Missing horizons')
        require(len(set(request['horizons'])) == len(request['horizons']), 'Duplicate horizons')
        for h in request['horizons']:
            integer(h, 1)
            require(h <= spec.horizon and h + request['emitted_at']['decision_seq'] -
                    request['last_visible_at']['decision_seq'] <= request['budget'], 'Horizon exceeds budget')
        require(data['representation'] in ('semantic', 'embedding'), 'Unknown representation')
        require(data['failure'] is None or type(data['failure']) is str and bool(data['failure'].strip()),
                'Failure needs a reason')
        require(type(data['predictions']) is list, 'Expected prediction rows')
        require(data['failure'] is None or not data['predictions'], 'Failed forecast cannot assert predictions')
        seen = set()
        for row in data['predictions']:
            keys(row, ('horizon', 'task', 'value') if data['representation'] == 'semantic'
                 else ('horizon', 'values'))
            integer(row['horizon'], 1)
            require(row['horizon'] in request['horizons'], 'Undeclared prediction horizon')
            if data['representation'] == 'semantic':
                keys(row['task'], TemporalTask.__dataclass_fields__)
                task = TemporalTask(**row['task'])
                key = (row['horizon'], encode_json(task.to_json()))
            else:
                require(type(row['values']) is list and bool(row['values']) and
                        all(type(v) in (int, float) and math.isfinite(v) for v in row['values']),
                        'Expected finite embedding vector')
                key = row['horizon']
            require(key not in seen, 'Duplicate forecast prediction')
            seen.add(key)


def make_forecast(case, predictions, *, model_id, model_revision, representation='semantic', failure=None):
    return ForecastV1({'schema_version': 1, 'model_id': model_id, 'model_revision': model_revision,
                       'request': forecast_request(case), 'representation': representation,
                       'predictions': predictions, 'failure': failure})


def validate_forecast(forecast, case):
    require(type(forecast) is ForecastV1, 'Expected ForecastV1')
    data = forecast.to_json()
    ForecastV1.from_json(data)
    require(data['request'] == forecast_request(case),
            'Forecast receipt differs from authorized source inputs, time, view or budget')
    return True


def _fields(projection):
    return projection['channels']['primary']['fields']


def _absent(window, h):
    frames = window['metadata']['source_transitions'][:h]
    if any(f['status'] != 'resolved' for f in frames):
        return 'pending_transition'
    if frames and frames[-1]['end'] is not None:
        return 'early_terminal' if frames[-1]['end']['kind'] == 'terminal' else 'truncated'
    return 'trace_boundary_or_gap'


def _target(case, task, h, labels, events):
    window = case['window']
    ref = window['metadata']['target_observations'][h - 1]
    if ref is None:
        return None, _absent(window, h)
    ctx = ref['available_at']
    if task.name in ('decision.actor', 'decision.actor_changed'):
        actor = _fields(window['metadata']['target_projection'][h - 1])['primary.decision.actor_team.value']
        if task.name == 'decision.actor_changed':
            actor = actor != _fields(window['metadata']['history_projection'][-1])['primary.decision.actor_team.value']
        return actor, None
    if task.name.startswith('event.'):
        before = case['request']['emitted_at']
        expected = range(before['event_seq'] + 1, ctx['event_seq'] + 1)
        rows = [events.get((ctx['episode_id'], ctx['branch_id'], i)) for i in expected]
        if any(row is None for row in rows):
            return None, 'missing_events'
        if any(not _before(before, row['context']) or not _before(row['context'], ctx) for row in rows):
            return None, 'misaligned_events'
        return any(row['kind'] == 'report' and row['data']['outcome_type'] == task.name[6:] for row in rows), None
    key = _time_key(ctx) + (task.entity_id, task.related_entity_id, task.name, label_spec(task.name).version)
    record = labels.get(key)
    if record is None:
        return None, 'missing_label'
    if record['context'] != ctx or record['available_at'] != ctx:
        return None, 'misaligned_label'
    if record['status'] != 'available':
        return None, 'label_' + record['unavailability']['code']
    return record['value'], None


def _domain(task, value, case):
    try:
        if task.kind == 'actor':
            require(value is None or value in ('home', 'away'), 'Unknown actor')
        else:
            _value(task.kind, value)
        initial = case['initial_window']
        entity_ids = _fields(initial['metadata']['history_projection'][-1])['primary.players[].id']
        if task.kind == 'nullable_player_id':
            require(value is None or value in entity_ids, 'Unknown entity')
        if task.name.startswith('player.'):
            require(task.entity_id in entity_ids, 'Unknown entity')
    except (RecordError, TypeError, KeyError, ValueError):
        return 'invalid_domain_or_entity'
    return None


def _loss(task, prediction, target):
    if task.kind in ('integer', 'probability', 'signed_score'):
        error = abs(prediction - target)
    elif task.kind == 'position':
        error = abs(prediction['x'] - target['x']) + abs(prediction['y'] - target['y'])
    else:
        error = float(prediction != target)
    return {'absolute_error': error, 'squared_error': error ** 2}


def _observed(case, task):
    """Read only authorized feature leaves; IDs select slots in evaluator metadata."""
    if task.name in ('decision.actor', 'decision.actor_changed') or task.name.startswith('event.'):
        return []
    spec = label_spec(task.name)
    if spec.observation_path is None or task.name == 'team.possession':
        return []
    path = 'primary.' + spec.observation_path
    initial = case['initial_window']
    values = []
    for obs, ref, projection in zip(initial['inputs']['observations'], initial['metadata']['history'],
                                     initial['metadata']['history_projection']):
        if obs is None:
            continue
        field = path + '.value' if task.kind == 'nullable_player_id' else path
        if task.kind == 'position':
            # Projection represents Presence fields as separate leaf arrays.
            parts = [path + '.value.' + c for c in ('x', 'y')]
            if not all(p in obs for p in parts):
                continue
            value = {c: obs[p] for c, p in zip(('x', 'y'), parts)}
        elif field in obs:
            value = obs[field]
        else:
            continue
        index = None
        if spec.domain == 'team':
            index = _fields(projection)['primary.teams[].id'].index(task.entity_id)
        elif spec.domain == 'player':
            ids = _fields(projection)['primary.players[].id']
            if task.entity_id not in ids:
                continue
            index = ids.index(task.entity_id)
        elif spec.domain == 'ball':
            index = int(task.entity_id.split(':')[1])
        try:
            if index is not None:
                value = {c: v[index] for c, v in value.items()} if task.kind == 'position' else value[index]
            if value is None or task.kind == 'position' and any(v is None for v in value.values()):
                continue
            values.append((ref['available_at']['decision_seq'], value))
        except (IndexError, TypeError):
            continue
    return values


def baseline_forecast(case, tasks, *, method='persistence'):
    """Same views/history/actions/budget; no fitting, targets or future metadata."""
    require(method in ('persistence', 'linear'), 'Unknown baseline')
    rows = []
    for task in tasks:
        values = _observed(case, task)
        for h in case['request']['horizons']:
            if task.name.startswith('event.'):
                value = False  # no-event baseline for both methods
            elif not values:
                continue
            else:
                time, value = values[-1]
                if method == 'linear' and task.kind in ('integer', 'probability', 'signed_score'):
                    if len(values) < 2:
                        continue
                    prev_time, prev = values[-2]
                    destination = case['request']['emitted_at']['decision_seq'] + h
                    value = value + (value - prev) / (time - prev_time) * (destination - time)
                    if task.kind == 'integer' and value == int(value):
                        value = int(value)
            rows.append({'horizon': h, 'task': task.to_json(), 'value': value})
    return make_forecast(case, rows, model_id='baseline.' + method, model_revision='v1')


def _summary(rows):
    valid = [row for row in rows if row['reason'] is None]
    n = len(valid)
    families = defaultdict(list)
    for row in valid:
        families[row['family_id']].append(row['loss']['absolute_error'])
    means = [math.fsum(values) / len(values) for _, values in sorted(families.items())]
    f = len(means)
    mean = math.fsum(means) / f if f else None
    return {'attempted': len(rows), 'valid': n, 'excluded': len(rows) - n,
            'causes': dict(sorted(Counter(row['reason'] for row in rows if row['reason']).items())),
            'mae_or_error_rate': math.fsum(r['loss']['absolute_error'] for r in valid) / n if n else None,
            'rmse': math.sqrt(math.fsum(r['loss']['squared_error'] for r in valid) / n) if n else None,
            'family_count': f, 'family_mean_error': mean,
            'family_standard_error': math.sqrt(math.fsum((v - mean) ** 2 for v in means) / (f - 1) / f)
            if f > 1 else None}


def evaluate_forecasts(cases, forecasts, tasks, *, models, records=(), events=()):
    """Deterministic semantic report; attempted cases never depend on target values.

    models is the predeclared [(id, revision), ...] roster, including absent or
    failed producers. Malformed/foreign/causally invalid receipts reject the run.
    Metrics retain missing tasks and labels; matched baseline comparisons use
    the identical valid intersection and retain attempted/pair counts.
    """
    cases = _copy(list(cases))
    tasks = list(tasks)
    require(bool(tasks) and all(type(t) is TemporalTask for t in tasks), 'Expected temporal tasks')
    task_keys = [encode_json(t.to_json()) for t in tasks]
    require(len(set(task_keys)) == len(tasks), 'Duplicate task')
    model_keys = [tuple(m) for m in models]
    require(bool(model_keys) and len(set(model_keys)) == len(model_keys), 'Empty/duplicate model roster')
    for model in model_keys:
        require(len(model) == 2 and not model[0].startswith('baseline.'), 'Reserved/invalid model identity')
        for part in model:
            identifier(part)
    for c in cases:
        _check_case(c)
    case_index = {c['case_id']: c for c in cases}
    require(len(case_index) == len(cases), 'Duplicate case')
    labels = {}
    for record in records:
        require(type(record) is EvaluationRecord, 'Expected EvaluationRecord')
        require(record.key not in labels, 'Duplicate evaluation label')
        labels[record.key] = record.to_json()
    event_index = {}
    for event in events:
        row = event.to_json() if type(event) is EventV1 else EventV1.from_json(event).to_json()
        key = tuple(row['event_id'])
        require(key not in event_index, 'Duplicate event ID')
        event_index[key] = row
    forecast_index = {}
    for forecast in forecasts:
        require(type(forecast) is ForecastV1, 'Expected ForecastV1')
        data = forecast.to_json()
        cid = data['request']['case_id']
        require(cid in case_index, 'Foreign forecast case')
        validate_forecast(forecast, case_index[cid])
        if data['representation'] == 'semantic':
            require(all(encode_json(r['task']) in task_keys for r in data['predictions']), 'Undeclared prediction task')
        model = (data['model_id'], data['model_revision'])
        require(model in model_keys, 'Undeclared model')
        key = (cid, model)
        require(key not in forecast_index, 'Duplicate model forecast')
        forecast_index[key] = data
    rows, group_keys = [], {}
    for case in sorted(cases, key=lambda c: c['case_id']):
        req = case['request']
        # Comparison strata include context age, view, split, policy and budget.
        stratum = {k: req[k] for k in ('protocol', 'window_spec', 'split', 'split_version',
                                      'continuation_policy', 'budget', 'known_actions')}
        stratum['context_age'] = req['emitted_at']['decision_seq'] - req['last_visible_at']['decision_seq']
        sid = hashlib.sha256(encode_json(stratum)).hexdigest()
        group_keys[sid] = stratum
        baselines = [baseline_forecast(case, tasks, method=m).to_json() for m in ('persistence', 'linear')]
        producers = [(m, forecast_index.get((case['case_id'], m))) for m in model_keys]
        producers += [((d['model_id'], d['model_revision']), d) for d in baselines]
        for model, data in producers:
            predictions = {} if data is None or data['representation'] != 'semantic' else {
                (r['horizon'], encode_json(r['task'])): r['value'] for r in data['predictions']}
            for task, tk in zip(tasks, task_keys):
                for h in req['horizons']:
                    target, target_reason = _target(case, task, h, labels, event_index)
                    key = (h, tk)
                    reason = ('missing_forecast' if data is None else
                              'forecast_failed' if data['failure'] else
                              'no_decoder' if data['representation'] == 'embedding' else
                              ('baseline_history_or_field_unavailable' if model[0].startswith('baseline.')
                               else 'missing_prediction') if key not in predictions else
                              _domain(task, predictions[key], case))
                    prediction_reason = reason
                    reason = reason or target_reason
                    rows.append({'case_id': case['case_id'], 'family_id': req['family_id'],
                                 'stratum': sid, 'model': list(model), 'task': task.to_json(),
                                 'task_family': task.family, 'unit': task.unit, 'horizon': h,
                                 'reason': reason, 'prediction_reason': prediction_reason,
                                 'target_reason': target_reason, 'failure': data['failure'] if data else None,
                                 'loss': None if reason else _loss(task, predictions[key], target)})
    groups = defaultdict(list)
    for row in rows:
        key = (row['stratum'], tuple(row['model']), encode_json(row['task']), row['horizon'])
        groups[key].append(row)
    metrics = []
    for key, members in sorted(groups.items()):
        sid, model, task, h = key
        metrics.append({'stratum': sid, 'model': list(model), 'task': decode_json(task), 'task_family': members[0]['task_family'],
                        'unit': members[0]['unit'], 'horizon': h,
                        **_summary(members), 'by_family': {f: _summary([r for r in members if r['family_id'] == f])
                        for f in sorted({r['family_id'] for r in members})}})
    comparisons = []
    for key, members in sorted(groups.items()):
        sid, model, task, h = key
        if model not in model_keys:
            continue
        for method in ('persistence', 'linear'):
            other = groups[(sid, ('baseline.' + method, 'v1'), task, h)]
            index = {r['case_id']: r for r in other}
            paired = [r for r in members if r['reason'] is None and index[r['case_id']]['reason'] is None]
            deltas = [{**r, 'loss': {'absolute_error': r['loss']['absolute_error'] -
                       index[r['case_id']]['loss']['absolute_error'], 'squared_error': 0}} for r in paired]
            summary = _summary(deltas)
            comparisons.append({'stratum': sid, 'model': list(model), 'baseline': method,
                                'task': decode_json(task), 'horizon': h, 'attempted': len(members),
                                'paired': len(paired), 'unpaired': len(members) - len(paired),
                                'family_count': summary['family_count'],
                                'family_mean_error_delta': summary['family_mean_error'],
                                'family_standard_error': summary['family_standard_error']})
    return {'schema_version': 1, 'case_count': len(cases), 'forecast_count': len(forecast_index),
            'strata': dict(sorted(group_keys.items())), 'rows': rows, 'metrics': metrics,
            'comparisons': comparisons, 'uncertainty_unit': 'origin_family',
            'domain_checks': 'catalogue value domains and captured player IDs only; not rule legality',
            'latent_metrics': None}
