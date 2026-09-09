"""Separate discrete surprise, observed impact and pre-outcome expected quality.

All records are detached evaluation sidecars. See docs/lab/decision-axes.md.
Inputs are trusted experiment declarations, not proof of model authenticity.
"""
from dataclasses import asdict, dataclass
import math

from botbowl.lab.channels import make_channel
from botbowl.lab.records import _Record, encode_json, keys, require
from botbowl.lab.rollouts import Horizon, binary_summary
from .oracle import EvaluationRecord


def _text(value):
    require(type(value) is str and bool(value.strip()), 'Explicit nonempty text required')


def _number(value):
    require(type(value) in (int, float) and math.isfinite(value), 'Finite scalar required; latent data rejected')


def _names(values):
    require(type(values) is list and bool(values), 'Nonempty declared set required')
    for value in values:
        _text(value)
    require(len(set(values)) == len(values), 'Duplicate members')


@dataclass(frozen=True, init=False)
class AssessmentContext(_Record):
    """Common state/model/policy/horizon and per-action evaluation budget."""
    @staticmethod
    def _validate(data):
        keys(data, ('state_id', 'model', 'policy', 'horizon', 'budget'))
        _text(data['state_id'])
        keys(data['model'], ('id', 'version'))
        keys(data['policy'], ('name', 'version', 'config'))
        for value in list(data['model'].values()) + [data['policy']['name'], data['policy']['version']]:
            _text(value)
        require(type(data['policy']['config']) is dict, 'Policy configuration required')
        require(type(data['horizon']) is dict, 'Logical horizon required')
        keys(data['horizon'], ('unit', 'limit', 'max_decisions', 'max_steps', 'terminal'))
        Horizon(**data['horizon'])
        require(type(data['budget']) is int and data['budget'] >= 0, 'Nonnegative per-action budget required')


@dataclass(frozen=True, init=False)
class DiscreteReference(_Record):
    """Finite exact probabilities or complete IID categorical sample receipts."""
    @staticmethod
    def _validate(data):
        keys(data, ('context', 'action_id', 'variable', 'support', 'method', 'probabilities', 'samples', 'origin'))
        context = AssessmentContext(data['context']).to_json()
        _text(data['action_id'])
        _text(data['variable'])
        _names(data['support'])
        require(type(data['origin']) is dict and bool(data['origin']), 'Measure origin required')
        _text(data['origin'].get('description'))
        if data['method'] == 'exact':
            require(data['samples'] is None, 'Exact references cannot carry Monte Carlo samples')
            p = data['probabilities']
            require(type(p) is list and len(p) == len(data['support']), 'Probability/support mismatch')
            for value in p:
                _number(value)
                require(0 <= value <= 1, 'Probability outside [0,1]')
            require(math.isclose(math.fsum(p), 1., rel_tol=0., abs_tol=1e-12), 'Distribution must normalize to one')
            require(len(p) <= context['budget'], 'Enumeration exceeds declared per-action budget')
        else:
            require(data['method'] == 'monte_carlo', 'Only discrete exact or Monte Carlo distributions accepted; latent distances are not probabilities')
            require(data['probabilities'] is None, 'Monte Carlo probabilities must be computed from samples')
            samples = data['samples']
            keys(samples, ('ids', 'outcomes', 'seed_plan'))
            require(type(samples['ids']) is list and type(samples['outcomes']) is list, 'Sample arrays required')
            require(len(samples['ids']) == len(samples['outcomes']) == context['budget'], 'Complete equal-budget samples required')
            require(all(type(i) is int and i >= 0 for i in samples['ids']), 'Nonnegative sample IDs required')
            require(len(set(samples['ids'])) == len(samples['ids']), 'Retries are not independent samples')
            require(all(v in data['support'] for v in samples['outcomes']), 'Undefined sampled outcome')
            require(type(samples['seed_plan']) is dict and bool(samples['seed_plan']), 'Seed recipe required')
            _text(samples['seed_plan'].get('algorithm'))
            require('master_seed' in samples['seed_plan'] and type(samples['seed_plan']['master_seed']) is int,
                    'Explicit master seed required')


def exact_reference(context, action_id, variable, probabilities, *, origin):
    """The caller declares the exhaustive support and exact probability model."""
    return DiscreteReference({'context': context.to_json(), 'action_id': action_id, 'variable': variable,
        'support': list(probabilities), 'method': 'exact', 'probabilities': list(probabilities.values()),
        'samples': None, 'origin': origin})


def sampled_reference(context, action_id, variable, support, outcomes, *, sample_ids, seed_plan, origin):
    """No failed/censored/missing rows may silently enter this complete sample."""
    return DiscreteReference({'context': context.to_json(), 'action_id': action_id, 'variable': variable,
        'support': list(support), 'method': 'monte_carlo', 'probabilities': None,
        'samples': {'ids': list(sample_ids), 'outcomes': list(outcomes), 'seed_plan': seed_plan}, 'origin': origin})


def surprise(reference, event):
    """Self-information of a predeclared nonempty subset of discrete support, in nats."""
    require(isinstance(reference, DiscreteReference), 'Explicit discrete reference required; latent data rejected')
    require(type(event) in (list, tuple), 'Explicit discrete event required')
    data = reference.to_json()
    event = list(event)
    _names(event)
    require(set(event) <= set(data['support']), 'Event must be defined in the reference support')
    result = {'reference': data, 'variable': data['variable'], 'event': event, 'units': 'nats', 'method': data['method'],
              'origin': data['origin'], 'probability': None, 'nats': None, 'probability_interval95': None,
              'nats_interval95': None, 'reason': None, 'status': 'available'}
    if data['method'] == 'exact':
        p = math.fsum(p for value, p in zip(data['support'], data['probabilities']) if value in event)
        # Serialization tolerates rounding in a normalized finite table only.
        p = min(1., p)
        result.update(probability=p, nats=-math.log(p) if p else None,
                      status='available' if p else 'model_incompatible',
                      reason='Exact reference probability.' if p else 'Exact zero: event incompatible with the declared model.')
    else:
        stats = binary_summary(value in event for value in data['samples']['outcomes'])
        result.update(n=stats['n'], successes=stats['positive'], probability=stats['frequency'],
                      probability_interval95=stats['wilson95'], interval_method='pointwise Wilson score, 95%')
        if stats['n'] == 0:
            result.update(status='insufficient_samples', reason='No samples; event probability is unknown.')
        else:
            p = stats['frequency']
            lo, hi = stats['wilson95']
            # At zero successes the lower probability endpoint is exactly zero.
            lo = 0. if stats['positive'] == 0 else lo
            result['probability_interval95'] = [lo, hi]
            result['nats_interval95'] = [-math.log(hi), -math.log(lo) if lo > 0 else None]
            result.update(nats=-math.log(p) if p else None,
                          status='available' if p else 'censored',
                          reason='Empirical frequency; sampling error is not model error.' if p else
                          'Zero successes do not prove impossibility; null upper nats bound is unbounded.')
    return result


def _utility_stats(reference, utility, comparisons):
    """Simultaneous bounded-mean intervals avoid treating a sampled maximum as exact."""
    values = utility['values']
    lo, hi = min(values.values()), max(values.values())
    if reference['method'] == 'exact':
        mean = math.fsum(p * values[o] for o, p in zip(reference['support'], reference['probabilities']))
        return {'expected_utility': mean, 'interval95': [mean, mean], 'standard_error': 0.,
                'n': None, 'error_method': 'exact finite enumeration'}
    samples = [values[o] for o in reference['samples']['outcomes']]
    n = len(samples)
    if not n:
        return {'expected_utility': None, 'interval95': [lo, hi], 'standard_error': None,
                'n': 0, 'error_method': 'no samples; declared utility range only'}
    mean = math.fsum(samples) / n
    variance = math.fsum((v - mean) ** 2 for v in samples) / (n - 1) if n > 1 else None
    radius = (hi - lo) * math.sqrt(math.log(2 * comparisons / .05) / (2 * n))
    return {'expected_utility': mean, 'interval95': [max(lo, mean - radius), min(hi, mean + radius)],
            'standard_error': math.sqrt(variance / n) if variance is not None else None,
            'n': n, 'error_method': 'Hoeffding 95% simultaneous over compared actions (union bound)'}


def _quality(chosen_action, legal_actions, references, utility, origin):
    _names(legal_actions)
    require(type(references) is list and bool(references), 'References required')
    rows = [DiscreteReference(r).to_json() for r in references]
    compared = [r['action_id'] for r in rows]
    _names(compared)
    require(set(compared) <= set(legal_actions) and chosen_action in compared, 'Illegal or unevaluated chosen action/alternative')
    context = rows[0]['context']
    require(all(r['context'] == context and r['method'] == rows[0]['method'] and
                r['variable'] == rows[0]['variable'] and set(r['support']) == set(rows[0]['support']) for r in rows),
            'Alternatives require the same state, reference model, policy, horizon, budget, variable, support and method')
    keys(utility, ('id', 'version', 'definition', 'unit', 'values'))
    for field in ('id', 'version', 'definition', 'unit'):
        _text(utility[field])
    require(type(utility['values']) is dict and set(utility['values']) == set(rows[0]['support']),
            'Utility must define every outcome in the common finite support')
    for value in utility['values'].values():
        _number(value)
    _text(origin)
    scores = {r['action_id']: _utility_stats(r, utility, len(rows)) for r in rows}
    selected = scores[chosen_action]
    means = [r['expected_utility'] for r in scores.values()]
    regret = None if any(v is None for v in means) else max(means) - selected['expected_utility']
    alternatives = [row for action, row in scores.items() if action != chosen_action]
    lower = max([0.] + [r['interval95'][0] - selected['interval95'][1] for r in alternatives])
    upper = max([0.] + [r['interval95'][1] - selected['interval95'][0] for r in alternatives])
    return {'format': 'DecisionQualityV1', 'schema_version': 1, 'context': context,
            'chosen_action': chosen_action, 'legal_actions': legal_actions, 'compared_actions': compared,
            'reference_distributions': rows, 'utility': utility, 'origin': origin,
            'available_at': 'before_realized_outcome', 'method': rows[0]['method'],
            'estimates': scores, 'regret': regret, 'regret_interval95': [lower, upper],
            'comparison_scope': 'all_declared_legal_actions' if set(compared) == set(legal_actions) else 'evaluated_subset',
            'reason': 'Higher expected declared utility is better; regret is relative only to compared actions. '
                      'Sampled rankings do not certify global optimality.'}


@dataclass(frozen=True, init=False)
class DecisionQualityV1(_Record):
    """Immutable pre-outcome evaluation, retaining inputs needed to reproduce it."""
    @staticmethod
    def _validate(data):
        expected = _quality(data['chosen_action'], data['legal_actions'], data['reference_distributions'],
                            data['utility'], data['origin'])
        require(encode_json(data) == encode_json(expected), 'Quality record differs from its pre-outcome inputs')


def evaluate_decision(chosen_action, legal_actions, references, *, utility, origin):
    """Freeze expected utility and subset regret before observing the actual result.

    legal_actions is the exhaustive trusted legal action ID list at this state;
    engine callers obtain it from the semantic legal-action API.
    """
    return DecisionQualityV1(_quality(chosen_action, list(legal_actions), [r.to_json() for r in references], utility, origin))


def observed_impact(state_id, result_id, variable, unit, before, after, *, origin):
    """Observed scalar/indicator difference, with no causal interpretation."""
    for value in (state_id, result_id, variable, unit, origin):
        _text(value)
    for value in (before, after):
        if type(value) is not bool:
            _number(value)
    require((type(before) is bool) == (type(after) is bool), 'Mixed indicator/scalar values')
    return {'method': 'observed_difference', 'state_id': state_id, 'result_id': result_id,
            'variable': variable, 'unit': unit, 'before': before, 'after': after, 'delta': after - before,
            'origin': origin, 'reason': 'Observed after minus before; not a causal effect.'}


def oracle_impact(state_id, result_id, before, after):
    """Score, possession and resource deltas from two captured EVAL-01 facts."""
    require(isinstance(before, EvaluationRecord) and isinstance(after, EvaluationRecord), 'Oracle records required')
    a, b = before.to_json(), after.to_json()
    allowed = ('team.score', 'team.possession', 'team.rerolls', 'team.apothecaries', 'team.bribes')
    require(a['label']['name'] in allowed and a['label'] == b['label'], 'Expected matching score/possession/resource facts')
    require(a['status'] == b['status'] == 'available' and a['entity_id'] == b['entity_id'] and a['rules'] == b['rules'],
            'Matching available facts and rules required')
    require(all(a['context'][k] == b['context'][k] for k in ('episode_id', 'branch_id')) and
            all(a['context'][k] <= b['context'][k] for k in ('decision_seq', 'event_seq')), 'Unordered or foreign observation boundaries')
    result = observed_impact(state_id, result_id, a['label']['name'] + ':' + a['entity_id'], a['label']['unit'],
                             a['value'], b['value'], origin='EVAL-01 captured state facts')
    result['oracle_records'] = [a, b]
    return result


HUMAN_RUBRIC = {
    'id': 'spectacle-v1',
    'definition': 'Personal perceived attractiveness/spectacle of this displayed outcome; not tactical correctness.',
    'ratings': {'1': 'little appeal', '2': 'moderately engaging', '3': 'highly engaging'},
    'consent_scope': 'store and share this pseudonymous annotation with the assessment',
}


def _assessment(quality, result_id, outcome, event, impacts, annotations):
    quality = DecisionQualityV1(quality).to_json()
    _text(result_id)
    _text(outcome)
    require(outcome in event, 'Realized outcome must belong to the assessed event')
    chosen = next(r for r in quality['reference_distributions'] if r['action_id'] == quality['chosen_action'])
    rarity = surprise(DiscreteReference(chosen), event)
    require(type(impacts) is list and bool(impacts), 'At least one declared observed impact required')
    seen = set()
    for row in impacts:
        expected = observed_impact(row['state_id'], row['result_id'], row['variable'], row['unit'],
                                   row['before'], row['after'], origin=row['origin'])
        if 'oracle_records' in row:
            a, b = row['oracle_records']
            expected = oracle_impact(row['state_id'], row['result_id'], EvaluationRecord(a), EvaluationRecord(b))
        require(encode_json(row) == encode_json(expected), 'Invalid observed impact; counterfactual/causal claims unsupported')
        require(row['state_id'] == quality['context']['state_id'] and row['result_id'] == result_id, 'Foreign impact identity')
        require(row['variable'] not in seen, 'Duplicate impact variable')
        seen.add(row['variable'])
    require(type(annotations) is list, 'Human annotation list required')
    authors = set()
    for row in annotations:
        keys(row, ('author', 'consent', 'rating', 'rationale', 'rubric_id'))
        _text(row['author'])
        _text(row['rationale'])
        require(row['consent'] is True and row['rubric_id'] == HUMAN_RUBRIC['id'], 'Explicit annotation consent and rubric required')
        require(type(row['rating']) is int and 1 <= row['rating'] <= 3, 'Rubric rating must be 1, 2 or 3')
        require(row['author'] not in authors, 'One annotation per author per assessment')
        authors.add(row['author'])
    ratings = [a['rating'] for a in annotations]
    return {'format': 'DecisionAssessmentV1', 'schema_version': 1, 'visibility': 'evaluation_only',
            'context': quality['context'], 'action_id': quality['chosen_action'], 'result_id': result_id,
            'outcome': outcome, 'surprise': rarity, 'impact': impacts, 'decision_quality': quality,
            'human': {'rubric': HUMAN_RUBRIC, 'annotations': annotations,
                      'disagreement': None if len(ratings) < 2 else max(ratings) - min(ratings),
                      'disagreement_method': 'rating range; null with fewer than two authors',
                      'reason': 'Subjective human judgments, never automatic simulator ground truth.'}}


@dataclass(frozen=True, init=False)
class DecisionAssessmentV1(_Record):
    """Validated immutable sidecar; has no aggregate score or policy-input projection."""
    @staticmethod
    def _validate(data):
        expected = _assessment(data['decision_quality'], data['result_id'], data['outcome'],
                               data['surprise']['event'], data['impact'], data['human']['annotations'])
        require(encode_json(data) == encode_json(expected), 'Assessment differs from its retained evidence')


def assess_outcome(quality, result_id, outcome, *, event, impacts, annotations=()):
    require(isinstance(quality, DecisionQualityV1), 'Frozen pre-outcome decision quality required')
    require(type(event) in (list, tuple), 'Explicit discrete event required')
    return DecisionAssessmentV1(_assessment(quality.to_json(), result_id, outcome, list(event), list(impacts), list(annotations)))


def assessment_channel(assessment):
    require(isinstance(assessment, DecisionAssessmentV1), 'DecisionAssessmentV1 required')
    return make_channel('evaluation', {'labels': {}, 'estimates': {'decision_assessment': assessment.to_json()},
                                       'provenance': {'protocol': 'DecisionAssessmentV1'}})


def semantic_action_id(action):
    """Canonical data identity for an ActionV1, scoped by AssessmentContext.state_id."""
    from botbowl.lab.actions import ActionV1
    require(type(action) is ActionV1, 'Semantic ActionV1 required')
    return encode_json(action.to_json()).decode('utf-8')


def reference_from_continuations(report, *, model_id, model_version, observable, categories):
    """Adapt complete SIM-07 samples without treating frequency zero as exact.

    categories maps predeclared string names to distinct raw observable values.
    Retain the original report/source snapshot separately for reproduction.
    """
    import hashlib
    from botbowl.lab.actions import ActionV1
    from botbowl.lab.evaluation.calibration import _plan
    from botbowl.lab.randomness import DERIVATION_ALGORITHM, GENERATOR
    from botbowl.lab.rollouts import OBSERVABLES
    _plan(report)  # Reuse EVAL-04 sample/seed/status/origin receipt validation.
    require(report['randomness'] == {'derivation': DERIVATION_ALGORITHM, 'generator': GENERATOR}, 'Unknown randomness metadata')
    require(observable in OBSERVABLES and report['observables'][observable] == asdict(OBSERVABLES[observable]),
            'Unknown/changed observable definition')
    require(report['counts']['failed'] == report['counts']['truncated'] == 0, 'Incomplete continuations cannot rank unconditional action values')
    require(report['complete_unconditional_sample'] is bool(report['records']), 'Changed sample completeness')
    require(report['first_action'] is not None and report['horizon']['limit'] > 0, 'An explicit evaluated first action and positive horizon are required')
    require(type(categories) is dict, 'Declared discrete categories required')
    _names(list(categories))
    codes = [encode_json(v) for v in categories.values()]
    require(len(set(codes)) == len(codes), 'Duplicate category values')
    names = dict(zip(codes, categories))
    outcomes = []
    for row in report['records']:
        require(bool(row['actions']) and row['actions'][0] == report['first_action'],
                'Continuation must actually apply the declared first action')
        value = row['values'][observable]
        require(value is not None and encode_json(value) in names, 'Missing or out-of-support observable')
        outcomes.append(names[encode_json(value)])
    context = AssessmentContext({'state_id': report['snapshot']['state_hash'],
        'model': {'id': model_id, 'version': model_version}, 'policy': report['policy'],
        'horizon': report['horizon'], 'budget': report['counts']['attempted']})
    plan = dict(report['sample_plan'], algorithm=report['randomness']['derivation'])
    return sampled_reference(context, semantic_action_id(ActionV1.from_json(report['first_action'])),
        observable, list(categories), outcomes, sample_ids=plan['sample_ids'], seed_plan=plan,
        origin={'description': 'SIM-07 complete nonmissing independent continuation samples',
                'report_sha256': hashlib.sha256(encode_json(report)).hexdigest(),
                'snapshot': report['snapshot'], 'first_action': report['first_action'],
                'categories': categories, 'observable': report['observables'][observable],
                'randomness': report['randomness']})
