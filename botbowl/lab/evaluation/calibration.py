"""EVAL-04: held-out probabilistic scores over matched SIM-07 continuations.

ForecastV1 remains inert. No fitting, model loading, clipping or bin selection is
performed here. See docs/lab/calibration.md for provenance and claim boundaries.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
import math

from botbowl.lab.evaluation.temporal import (
    TemporalTask, _domain, forecast_request, validate_forecast,
)
from botbowl.lab.records import decode_json, encode_json, keys, require
from botbowl.lab.rollouts import Horizon, SamplePlan, binary_summary, scalar_summary


def _finite(value):
    require(type(value) in (int, float) and math.isfinite(value), 'Expected finite number')
    return value


def _probability(value):
    require(0 <= _finite(value) <= 1, 'Probability outside [0, 1]')
    return value


def _support(values):
    require(type(values) in (list, tuple) and len(values) >= 2, 'Declare at least two categories')
    codes = [encode_json(v) for v in values]
    require(len(set(codes)) == len(codes), 'Duplicate support')
    return codes


def categorical_scores(probabilities, support, outcome):
    """Sum-of-classes Brier and negative natural log score (lower is better).

    An impossible observed outcome has log score +inf, serialized as 'infinity'.
    Binary Brier uses (p-y)^2 in binary_scores, half the two-class convention.
    """
    codes = _support(support)
    require(type(probabilities) in (list, tuple) and len(probabilities) == len(codes),
            'Probability/support dimensions differ')
    for p in probabilities:
        _probability(p)
    require(math.isclose(math.fsum(probabilities), 1., rel_tol=0., abs_tol=1e-12),
            'Probabilities do not sum to one')
    code = encode_json(outcome)
    require(code in codes, 'Outcome outside declared support')
    index = codes.index(code)
    p = probabilities[index]
    return {'brier': math.fsum((v - int(i == index)) ** 2 for i, v in enumerate(probabilities)),
            'log_score': -math.log(p) if p else 'infinity'}


def binary_scores(probability, outcome):
    _probability(probability)
    require(type(outcome) is bool, 'Expected binary outcome')
    scores = categorical_scores([1 - probability, probability], [False, True], outcome)
    scores['brier'] = (probability - outcome) ** 2
    return scores


def empirical_crps(samples, outcome):
    """CRPS of the empirical CDF, including diagonal pairs (not fair CRPS)."""
    require(type(samples) in (list, tuple) and bool(samples), 'Empty scalar samples')
    ordered = sorted(_finite(x) for x in samples)
    _finite(outcome)
    n = len(ordered)
    # Half the pairwise absolute-distance sum in O(n log n), no n-by-n array.
    spread = math.fsum((2 * i - n + 1) * x for i, x in enumerate(ordered)) / (n * n)
    return max(0., math.fsum(abs(x - outcome) for x in ordered) / n - spread)


def _quantile(ordered, q):
    index = (len(ordered) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def interval_scores(lower, upper, outcome):
    for value in (lower, upper, outcome):
        _finite(value)
    require(lower <= upper, 'Inverted interval')
    return {'coverage': float(lower <= outcome <= upper), 'width': upper - lower}


@dataclass(frozen=True)
class CalibrationTarget:
    task: TemporalTask
    kind: str
    support: tuple = ()

    def __post_init__(self):
        require(type(self.task) is TemporalTask, 'Expected TemporalTask')
        require(self.kind in ('binary', 'categorical', 'scalar'), 'Unknown calibration kind')
        require(type(self.support) is tuple, 'Support must be a tuple')
        if self.kind == 'categorical':
            _support(self.support)
        else:
            require(not self.support, 'Only categorical targets declare support')
        require(self.kind != 'binary' or self.task.kind == 'boolean', 'Binary task required')
        require(self.kind != 'scalar' or self.task.kind in ('integer', 'probability', 'signed_score'),
                'Scalar task required')
        _observable(self.task)  # Only reviewed SIM-07 semantic mappings.

    def to_json(self):
        return {'task': self.task.to_json(), 'kind': self.kind,
                'support': list(self.support), 'unit': self.task.unit}


def _observable(task):
    if task.name == 'event.TOUCHDOWN':
        return 'touchdown'
    if task.name == 'team.possession':
        return 'ball_possession'
    if task.name in ('team.score', 'team.rerolls') and task.entity_id in ('home', 'away'):
        return task.name[5:] + '_' + task.entity_id
    raise ValueError('No reviewed continuation observable for this task')


def _outcome(row, target):
    if row['status'] != 'valid':
        return None, row['status'] + ':' + row['reason']
    value = row['values'][_observable(target.task)]
    if value is None:
        return None, 'missing_observable'
    if target.task.name == 'team.possession':
        value = value == target.task.entity_id
    return value, None


def validate_prediction(value, target, case, levels=(.5, .8, .95)):
    """Validate semantics separately from ForecastV1 so failures are countable."""
    if type(value) is not dict:
        value = {'kind': 'point', 'unit': target.task.unit, 'value': value}
    kind = value.get('kind')
    if kind == 'point':
        keys(value, ('kind', 'unit', 'value'))
        require(value['unit'] == target.task.unit, 'Point unit mismatch')
        require(_domain(target.task, value['value'], case) is None, 'Invalid point domain')
        return value
    if target.kind == 'binary':
        keys(value, ('kind', 'probability'))
        require(kind == 'binary', 'Expected binary probability')
        _probability(value['probability'])
    elif target.kind == 'categorical':
        keys(value, ('kind', 'support', 'probabilities'))
        require(kind == 'categorical', 'Expected categorical distribution')
        require(encode_json(value['support']) == encode_json(list(target.support)), 'Support mismatch')
        for category in target.support:
            require(_domain(target.task, category, case) is None, 'Invalid category domain')
        categorical_scores(value['probabilities'], value['support'], target.support[0])
    else:
        keys(value, ('kind', 'unit', 'samples', 'intervals') if 'intervals' in value else ('kind', 'unit', 'samples'))
        require(kind == 'samples' and value['unit'] == target.task.unit, 'Scalar kind/unit mismatch')
        empirical_crps(value['samples'], 0.)
        for sample in value['samples']:
            require(_domain(target.task, sample, case) is None, 'Invalid sample domain')
        ordered = sorted(value['samples'])
        intervals = value.get('intervals', [
            {'level': level, 'lower': _quantile(ordered, (1 - level) / 2),
             'upper': _quantile(ordered, (1 + level) / 2)} for level in levels])
        require(type(intervals) is list and len(intervals) == len(levels), 'Interval dimensions differ')
        for interval, level in zip(intervals, levels):
            keys(interval, ('level', 'lower', 'upper'))
            require(interval['level'] == level, 'Interval levels differ from frozen levels')
            interval_scores(interval['lower'], interval['upper'], 0.)
        value = dict(value, intervals=intervals)
    return value


def _plan(report):
    require(report['format'] == 'ContinuationEstimateV1' and report['schema_version'] == 1 and
            report['method'] == 'monte_carlo' and report['chance_mode'] == 'independent',
            'Expected independent SIM-07 report')
    raw = report['sample_plan']
    plan = SamplePlan(tuple(raw['sample_ids']), raw['master_seed'], raw['experiment_id'],
                      raw['derivation_version'])
    rows = report['records']
    require([r['sample_id'] for r in rows] == list(plan.sample_ids), 'Changed sample IDs/order')
    counts = Counter(r['status'] for r in rows)
    require(set(counts) <= {'valid', 'failed', 'truncated'}, 'Unknown sample status')
    require(report['counts'] == dict(attempted=len(rows), **{
        k: counts[k] for k in ('valid', 'failed', 'truncated')}), 'Changed sample denominators')
    streams, origins = set(), set()
    for row in rows:
        sample_id = row['sample_id']
        require(row['seed'] == plan.seed(sample_id).to_json(), 'Changed engine seed')
        for side in ('home', 'away'):
            require(row['policy_seeds'][side] == plan.seed(sample_id, 'policy-' + side).to_json(),
                    'Changed policy seed')
        streams.update(encode_json(s) for s in [row['seed']] + list(row['policy_seeds'].values()))
        origin = (report['snapshot']['context']['episode_id'],
                  report['snapshot']['state_hash'], row['branch_id'])
        require(origin not in origins, 'Duplicate continuation origin')
        origins.add(origin)
        require((row['values'] is not None) == (row['status'] == 'valid'), 'Invalid outcome status')
        if row['status'] == 'valid':
            final = row['final_context']
            require(final['episode_id'] == report['snapshot']['context']['episode_id'] and
                    final['branch_id'] == row['branch_id'], 'Foreign sample origin')
            require(row['reason'] == 'horizon' or row['reason'] == 'terminal_absorbed' and
                    row['terminated'] and report['horizon']['terminal'] == 'absorb',
                    'Unresolved/terminal sample mismatch')
    return streams, origins


def validate_continuation_pair(case, horizon, reference, evaluation):
    """Same estimand; disjoint indexed streams AND continuation branch origins.

    Reports are trusted products of SIM-07, not authenticated untrusted traces.
    The matching parent snapshot is deliberately shared; sampled suffixes aren't.
    """
    request = forecast_request(case)
    require(horizon in request['horizons'], 'Undeclared horizon')
    require(request['emitted_at'] == request['last_visible_at'],
            'Later autonomous emissions need a continuation-prefix contract')
    streams, origins = [], []
    for report in (reference, evaluation):
        h = Horizon(**report['horizon'])
        require(h.unit == 'decisions' and h.limit == horizon and h.max_decisions <= request['budget'],
                'Continuation horizon/budget mismatch')
        require(report['snapshot']['context'] == request['emitted_at'], 'Continuation context mismatch')
        require(report['policy'] == request['continuation_policy'], 'Continuation policy mismatch')
        require(report['first_action'] == request['inputs'].get('action'), 'Continuation action mismatch')
        stream, origin = _plan(report)
        streams.append(stream)
        origins.append(origin)
    for field in ('snapshot', 'first_action', 'policy', 'horizon', 'randomness'):
        require(reference[field] == evaluation[field], 'Reference/evaluation estimands differ: ' + field)
    require(streams[0].isdisjoint(streams[1]), 'Reference/evaluation seeds overlap')
    require(origins[0].isdisjoint(origins[1]), 'Reference/evaluation origins overlap')
    return streams, origins


def _family_summary(pairs, bounded=False):
    families = defaultdict(list)
    for family, value in pairs:
        families[family].append(value)
    means = [math.fsum(values) / len(values) for _, values in sorted(families.items())]
    n = len(means)
    mean = math.fsum(means) / n if n else None
    se = math.sqrt(math.fsum((v - mean) ** 2 for v in means) / (n * (n - 1))) if n > 1 else None
    # Hoeffding on independent bounded family means stays wide at small N,
    # including all-zero/all-one cohorts where bootstrap intervals collapse.
    radius = math.sqrt(math.log(40.) / (2 * n)) if n and bounded else None
    return {'families': n, 'mean': mean, 'standard_error': se,
            'hoeffding95': [max(0., mean - radius), min(1., mean + radius)] if radius is not None else None,
            'status': 'insufficient_families' if n < 2 else 'descriptive_across_families'}


def _metric(rows, name, bounded=False):
    selected = [r for r in rows if name in r['scores']]
    infinities = sum(r['scores'][name] == 'infinity' for r in selected)
    if infinities:
        family = {'families': len({r['family_id'] for r in selected}), 'mean': 'infinity',
                  'standard_error': None, 'hoeffding95': None, 'status': 'infinite_loss'}
    else:
        family = _family_summary([(r['family_id'], r['scores'][name]) for r in selected], bounded)
    return {'n': len(selected), 'infinite': infinities,
            'mean': ('infinity' if infinities else math.fsum(r['scores'][name] for r in selected) /
                     len(selected)) if selected else None, 'by_family': family}


def _reliability(rows, category, edges):
    bins = []
    for i, (lower, upper) in enumerate(zip(edges, edges[1:])):
        members = [(r, r['reliability'][category]) for r in rows if r['reliability'] and
                   lower <= r['reliability'][category][0] and
                   (r['reliability'][category][0] < upper or i == len(edges) - 2)]
        n = len(members)
        bins.append({'lower': lower, 'upper': upper, 'upper_inclusive': i == len(edges) - 2,
                     'n': n, 'cases': len({r['case_id'] for r, _ in members}),
                     'mean_probability': math.fsum(p for _, (p, _) in members) / n if n else None,
                     'frequency': math.fsum(y for _, (_, y) in members) / n if n else None,
                     'frequency_by_family': _family_summary(
                         [(r['family_id'], y) for r, (_, y) in members], True)})
    n = sum(b['n'] for b in bins)
    return {'bins': bins, 'ece': math.fsum(b['n'] * abs(b['mean_probability'] - b['frequency'])
            for b in bins if b['n']) / n if n else None}


def _reference_summary(report, target, case):
    outcomes = [_outcome(r, target) for r in report['records']]
    values = [v for v, reason in outcomes if reason is None]
    for value in values:
        require(_domain(target.task, value, case) is None, 'Invalid reference outcome domain')
        if target.kind == 'categorical':
            require(encode_json(value) in _support(target.support), 'Reference outcome outside support')
    if target.kind == 'scalar':
        summary = scalar_summary(values, target.task.unit)
        variability = {'sample_variance': summary['sample_variance']}
        estimation = {'mean_standard_error': summary['standard_error']}
    else:
        support = [False, True] if target.kind == 'binary' else list(target.support)
        summaries = [binary_summary([encode_json(v) == encode_json(c) for v in values]) for c in support]
        ps = [s['frequency'] for s in summaries]
        summary = {'support': support, 'probabilities': ps}
        variability = {'gini_impurity': 1 - math.fsum(p * p for p in ps) if values else None}
        estimation = {'frequency_wilson95': [s['wilson95'] for s in summaries]}
    return {'attempted': len(outcomes), 'valid': len(values), 'excluded': len(outcomes) - len(values),
            'causes': dict(sorted(Counter(reason for _, reason in outcomes if reason).items())),
            'distribution': summary, 'engine_variability': variability, 'monte_carlo_error': estimation,
            'scope': 'valid_nonmissing_under_fixed_policy',
            'status': 'insufficient_samples' if len(values) < 2 else 'descriptive'}


def evaluate_calibration(cases, forecasts, targets, *, models, continuations,
                         bins=(0., .2, .4, .6, .8, 1.), levels=(.5, .8, .95), fit_families=()):
    """continuations: {(case_id, h): {'reference': report, 'evaluation': report}}.

    Declare model roster, targets, bins, levels and fit/tuning families before
    inspecting held-out outcomes. Every case/model/task/horizon remains counted.
    """
    cases, forecasts, targets = list(cases), list(forecasts), list(targets)
    models, bins, levels = [tuple(m) for m in models], tuple(bins), tuple(levels)
    fit_families = tuple(fit_families)
    require(len(bins) >= 2 and bins[0] == 0 and bins[-1] == 1, 'Bins must span [0, 1]')
    for x in bins + levels:
        _probability(x)
    require(all(a < b for a, b in zip(bins, bins[1:])), 'Bins must increase')
    require(bool(levels) and len(set(levels)) == len(levels) and all(0 < x < 1 for x in levels),
            'Declare unique interior interval levels')
    require(bool(targets) and all(type(t) is CalibrationTarget for t in targets), 'Declare targets')
    require(len({encode_json(t.task.to_json()) for t in targets}) == len(targets), 'Duplicate targets')
    require(bool(models) and len(set(models)) == len(models) and
            all(len(m) == 2 and all(type(s) is str and s for s in m) for m in models), 'Invalid model roster')
    index = {c['case_id']: c for c in cases}
    require(len(index) == len(cases), 'Duplicate cases')
    expected = {(c['case_id'], h) for c in cases for h in c['request']['horizons']}
    require(set(continuations) == expected, 'Missing/foreign continuation pair')
    seen_streams, seen_origins = [set(), set()], [set(), set()]
    stream_families = [{}, {}]
    sampling = []
    for c in cases:
        request = forecast_request(c)
        require(request['split'] == 'test' and request['family_id'] not in fit_families,
                'Calibration requires held-out test families, absent from fitting/tuning')
        for h in request['horizons']:
            pair = continuations[(c['case_id'], h)]
            keys(pair, ('reference', 'evaluation'))
            streams, origins = validate_continuation_pair(c, h, **pair)
            reference = pair['reference']
            sampling.append({'case_id': c['case_id'], 'horizon': h,
                'family_id': request['family_id'], 'origin': request['origin'],
                'snapshot_hash': reference['snapshot']['state_hash'],
                'context': reference['snapshot']['context'], 'randomness': reference['randomness'],
                'sample_plans': {role: report['sample_plan'] for role, report in pair.items()}})
            for i in (0, 1):
                require(streams[i].isdisjoint(seen_streams[1 - i]) and
                        origins[i].isdisjoint(seen_origins[1 - i]), 'Cross-case reference/evaluation leakage')
                for stream in streams[i]:
                    owner = stream_families[i].setdefault(stream, request['family_id'])
                    require(owner == request['family_id'], 'Shared streams require the same origin family')
                seen_streams[i].update(streams[i])
                seen_origins[i].update(origins[i])
    predictions = {}
    for forecast in forecasts:
        data = forecast.to_json()
        case_id = data['request']['case_id']
        require(case_id in index, 'Foreign forecast case')
        validate_forecast(forecast, index[case_id])
        model = (data['model_id'], data['model_revision'])
        key = (case_id, model)
        require(model in models and key not in predictions, 'Foreign/duplicate forecast model')
        predictions[key] = data
    groups, references = defaultdict(list), []
    for c in sorted(cases, key=lambda c: c['case_id']):
        req = c['request']
        # No cross-protocol, view, split, policy, terminal semantics or unit pooling.
        stratum = {k: req[k] for k in ('protocol', 'window_spec', 'split', 'split_version',
                                      'continuation_policy', 'budget')}
        for h in sorted(req['horizons']):
            pair = continuations[(c['case_id'], h)]
            for target in targets:
                ref = _reference_summary(pair['reference'], target, c)
                references.append({'case_id': c['case_id'], 'horizon': h, 'target': target.to_json(), **ref})
                for model in models:
                    data = predictions.get((c['case_id'], model))
                    reason, value = None, None
                    if data is None:
                        reason = 'missing_forecast'
                    elif data['failure']:
                        reason = 'producer_failure:' + data['failure']
                    elif data['representation'] != 'semantic':
                        reason = 'nonprobabilistic_embedding'
                    else:
                        pred = next((r for r in data['predictions'] if r['horizon'] == h and
                                     r['task'] == target.task.to_json()), None)
                        if pred is None:
                            reason = 'missing_prediction'
                        else:
                            try:
                                value = validate_prediction(pred['value'], target, c, levels)
                            except (ValueError, TypeError, KeyError, OverflowError) as error:
                                reason = 'invalid_prediction:' + str(error)
                    group = {'model': list(model), 'target': target.to_json(), 'horizon': h,
                             'continuation_horizon': pair['evaluation']['horizon'], **stratum}
                    group_key = encode_json(group)
                    # Empty sample plans still retain the attempted forecast.
                    samples = pair['evaluation']['records'] or [None]
                    for sample in samples:
                        outcome, target_reason = _outcome(sample, target) if sample else (None, 'empty_evaluation')
                        if target_reason is None:
                            require(_domain(target.task, outcome, c) is None, 'Invalid evaluation outcome domain')
                            if target.kind == 'categorical':
                                require(encode_json(outcome) in _support(target.support), 'Evaluation outcome outside support')
                        row = {'case_id': c['case_id'], 'family_id': req['family_id'],
                               'sample_id': sample['sample_id'] if sample else None,
                               'prediction_reason': reason, 'target_reason': target_reason,
                               'prediction_kind': value['kind'] if value else None,
                               'scores': {}, 'reliability': []}
                        if reason is None and target_reason is None:
                            if value['kind'] == 'point':
                                error = (abs(value['value'] - outcome) if target.kind == 'scalar'
                                         else float(value['value'] != outcome))
                                row['scores'] = {'absolute_error': error, 'squared_error': error ** 2}
                            elif target.kind == 'binary':
                                p = value['probability']
                                row['scores'] = binary_scores(p, outcome)
                                row['reliability'] = [[p, int(outcome)]]
                            elif target.kind == 'categorical':
                                row['scores'] = categorical_scores(value['probabilities'], target.support, outcome)
                                row['reliability'] = [[p, int(encode_json(outcome) == encode_json(category))]
                                    for p, category in zip(value['probabilities'], target.support)]
                            else:
                                row['scores'] = {'crps': empirical_crps(value['samples'], outcome),
                                                'sample_variance': scalar_summary(value['samples'], target.task.unit)['sample_variance']}
                                if row['scores']['sample_variance'] is None:
                                    del row['scores']['sample_variance']
                                for interval in value['intervals']:
                                    for name, score in interval_scores(interval['lower'], interval['upper'], outcome).items():
                                        row['scores'][name + '@' + str(interval['level'])] = score
                        groups[group_key].append(row)
    metrics = []
    for key, rows in sorted(groups.items()):
        group = decode_json(key)
        valid = [r for r in rows if r['scores']]
        causes = Counter(r['prediction_reason'] or r['target_reason'] for r in rows if not r['scores'])
        names = sorted({name for r in valid for name in r['scores']})
        categories = ([True] if group['target']['kind'] == 'binary' else group['target']['support'])
        metrics.append({**group, 'attempted_forecasts': len({r['case_id'] for r in rows}),
                        'invalid_forecasts': len({r['case_id'] for r in rows if r['prediction_reason']}),
                        'attempted_samples': sum(r['sample_id'] is not None for r in rows),
                        'valid_samples': len(valid), 'excluded_rows': len(rows) - len(valid),
                        'causes': dict(sorted(causes.items())),
                        'scores': {name: _metric(rows, name, name.startswith('coverage@')) for name in names},
                        'reliability': [{'category': category, **_reliability(rows, i, bins)}
                                        for i, category in enumerate(categories)], 'rows': rows})
    return {'format': 'CalibrationReportV1', 'schema_version': 1, 'bins': list(bins), 'levels': list(levels),
            'fit_families': sorted(fit_families), 'case_count': len(cases), 'metrics': metrics,
            'sampling': sorted(sampling, key=lambda r: (r['case_id'], r['horizon'])),
            'references': references, 'zero_probability': 'infinite log loss; no clipping',
            'uncertainty_unit': 'origin_family', 'coverage_claim': 'marginal only; no conditional guarantee',
            'limitations': ['Reference samples never score or fit the predictor.',
                'Engine variability and Monte Carlo estimation error are not predictor epistemic uncertainty.',
                'Sample variance measures diversity, not coverage.',
                'Family independence and truthful external training provenance are caller assumptions.',
                'Missing outcomes condition scores on valid samples; ECE is descriptive, never a sole gate.']}
