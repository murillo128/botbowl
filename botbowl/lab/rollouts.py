"""Sequential, reproducible continuation estimates; never policy input data.

See docs/lab/rollouts.md for horizon, censoring and independence semantics.
Trusted policy factories receive only copied configuration and their own RNG.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import product
import math
import hashlib
from types import MappingProxyType

from .chance import ChancePolicy, DOMAINS, branch_from_snapshot
from .channels import _json_copy
from .randomness import DERIVATION_ALGORITHM, GENERATOR, SeedSpec, _identifier
from .rules import describe_rules
from .session import NoProgress, SessionDiagnostic, SessionError, SessionSnapshot, SimulationSession
from .snapshot_io import snapshot_hash
from .snapshots import Snapshot, capture_snapshot, clone_from_snapshot


def _natural(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('%s must be an integer >= %d' % (name, minimum))


@dataclass(frozen=True)
class Horizon:
    unit: str
    limit: int
    max_decisions: int = 1000
    max_steps: int = 100000
    terminal: str = 'absorb'

    def __post_init__(self):
        if self.unit not in ('decisions', 'events', 'turns'):
            raise ValueError('Unknown horizon unit')
        _natural(self.limit, 'limit')
        _natural(self.max_decisions, 'max_decisions')
        _natural(self.max_steps, 'max_steps', 1)
        if self.terminal not in ('absorb', 'censor'):
            raise ValueError('terminal must be absorb or censor')


@dataclass(frozen=True)
class ContinuationPolicy:
    """factory(config, rng) -> driver(observation, legal_actions).

    A separate factory invocation/stream for each sample and team prevents
    policy memory and randomness from depending on sample scheduling order.
    Factories must honor that ownership contract; this is not a Python sandbox.
    """
    name: str
    version: str
    config: dict
    factory: object

    def __post_init__(self):
        _identifier(self.name)
        _identifier(self.version)
        if type(self.config) is not dict or not callable(self.factory):
            raise ValueError('Expected JSON policy config and a trusted factory')
        _json_copy(self.config)


@dataclass(frozen=True)
class SamplePlan:
    """Unique nonnegative sample indices; reorder/subset to replay, never reseed."""
    sample_ids: tuple
    master_seed: int
    experiment_id: str = 'rollouts'
    derivation_version: int = 1

    def __post_init__(self):
        if type(self.sample_ids) is not tuple:
            raise ValueError('sample_ids must be a tuple')
        for sample_id in self.sample_ids:
            _natural(sample_id, 'sample_id')
            _identifier(str(sample_id))
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError('Duplicate sample_id: retries are not new samples')
        if self.master_seed is None:
            raise ValueError('Retain an explicit master seed')
        self.seed(0)

    def seed(self, sample_id, purpose='engine'):
        return SeedSpec(self.master_seed, str(sample_id), purpose,
                        self.experiment_id, self.derivation_version)


@dataclass(frozen=True)
class Observable:
    kind: str
    units: str
    timing: str
    missing: str


# Extensions require reviewed code here and in _values; no import/callback names
# from untrusted data. All summaries use only valid, nonmissing outcomes.
_definitions = {
    'touchdown': Observable('binary', 'indicator', 'any team scores during the prefix',
                            'excluded on any failure or censoring'),
    'possession_loss': Observable('binary', 'indicator',
        'initial carrier team ceases possession at a settled boundary',
        'excluded without initial possession, or on failure/censoring'),
    'ball_position': Observable('categorical', 'pitch squares', 'last settled boundary',
                                'no ball is an explicit category; failures/censoring excluded'),
    'ball_possession': Observable('categorical', 'team', 'last settled boundary',
                                  'loose/no ball explicit; failures/censoring excluded'),
}
for _side in ('home', 'away'):
    _definitions['score_' + _side] = Observable('scalar', 'touchdowns', 'last settled boundary',
                                               'excluded on failure/censoring')
    for _resource in ('rerolls', 'apothecaries', 'bribes'):
        _definitions[_resource + '_' + _side] = Observable(
            'scalar', 'uses', 'last settled boundary', 'excluded on failure/censoring')
OBSERVABLES = MappingProxyType(_definitions)
del _definitions, _side, _resource


def _wilson95(k, n):
    if not n:
        return None
    p, z = k / n, 1.959963984540054
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0., center - radius), min(1., center + radius)]


def binary_summary(values):
    """Two-sided 95% Wilson score interval, with no invented zero-N estimate."""
    values = list(values)
    if any(type(value) is not bool for value in values):
        raise ValueError('Expected binary observations')
    n, k = len(values), sum(values)
    return {'n': n, 'positive': k, 'frequency': k / n if n else None,
            'wilson95': _wilson95(k, n)}


def scalar_summary(values, units):
    """Unbiased sample variance and linear-interpolated empirical quantiles."""
    values = sorted(values)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Expected finite scalar observations')
    n = len(values)
    mean = math.fsum(values) / n if n else None
    variance = math.fsum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else None
    quantiles = {}
    for q in (0., .25, .5, .75, 1.):
        index = (n - 1) * q
        lo, hi = math.floor(index), math.ceil(index)
        quantiles[str(q)] = (values[lo] + (values[hi] - values[lo]) * (index - lo)) if n else None
    return {'n': n, 'units': units, 'mean': mean, 'sample_variance': variance,
            'standard_error': math.sqrt(variance / n) if n > 1 else None,
            'quantiles': quantiles, 'quantile_method': 'linear-(n-1)q'}


def _ball(data):
    balls = data['balls']
    if len(balls) > 1:
        raise ValueError('Rollout observables support at most one ball')
    if not balls:
        return 'no_ball', None
    ball = balls[0]
    carrier = ball['carrier']['value'] if ball['carrier']['present'] else None
    team = next((p['team'] for p in data['players'] if p['id'] == carrier), None)
    position = ball['position']['value'] if ball['position']['present'] else None
    return team or 'loose', position


def _values(data, touchdown, lost):
    possession, position = _ball(data)
    values = {'touchdown': touchdown, 'possession_loss': lost,
              'ball_position': {'ball': possession != 'no_ball', 'position': position},
              'ball_possession': possession}
    for team in data['teams']:
        side = team['id']
        values['score_' + side] = team['score']
        for resource in ('rerolls', 'apothecaries', 'bribes'):
            values[resource + '_' + side] = team['resources'][resource]
    return values


def _progress(horizon, decisions, events):
    if horizon.unit == 'decisions':
        return decisions
    if horizon.unit == 'events':
        return len(events)
    return sum(event['kind'] == 'team_turn_ended' for event in events)


def _sample(engine, first_action, policy, horizon, plan, sample_id, used_branches):
    seed = plan.seed(sample_id)
    # Stable public index namespace, with no snapshot hash/engine seed in policy
    # action IDs. Avoid collisions when sampling an already sampled branch.
    label = str(sample_id)
    if len(label) > 80:
        label = hashlib.sha256(label.encode('ascii')).hexdigest()
    branch_id = 'rollout-' + label
    suffix = 0
    while branch_id in used_branches:
        suffix += 1
        branch_id = 'rollout-%s-%d' % (label, suffix)
    row = {'sample_id': sample_id, 'branch_id': branch_id, 'seed': seed.to_json(), 'status': 'failed',
           'reason': None, 'diagnostic': None, 'decisions': 0,
           'attempted_decisions': 0, 'progress': 0,
           'terminated': False, 'actions': [], 'values': None,
           'final_context': None, 'final_state_hash': None, 'chance': None,
           'policy_seeds': {side: plan.seed(sample_id, 'policy-' + side).to_json()
                            for side in ('home', 'away')}}
    game, session = None, None
    events, drivers = [], {}
    try:
        game = branch_from_snapshot(engine, branch_id=branch_id,
                                    policy=ChancePolicy(seed=seed))
        seq = game.timeline.context.decision_seq
        session = SimulationSession.from_snapshot(SessionSnapshot(
            1, 'engine', seq, seq + horizon.max_decisions, horizon.max_steps,
            None, capture_snapshot(game)))
        game.close()
        game = None
        result = session.observe()
        initial = result.primary['data']
        possession, _ = _ball(initial)
        lost = False if possession in ('home', 'away') else None
        touchdown = False
        initial_scores = {t['id']: t['score'] for t in initial['teams']}
        while True:
            progress = _progress(horizon, row['decisions'], events)
            row.update(progress=progress, terminated=result.terminated)
            if progress >= horizon.limit:
                row.update(status='valid', reason='horizon')
                break
            if result.terminated:
                row.update(status='valid' if horizon.terminal == 'absorb' else 'truncated',
                           reason='terminal_absorbed' if horizon.terminal == 'absorb' else 'terminal_censored')
                break
            if result.truncated:
                row.update(status='truncated', reason=result.end_reason)
                break
            if row['decisions'] == 0 and first_action is not None:
                action = first_action
            else:
                side = result.next_actor
                if side not in ('home', 'away'):
                    raise ValueError('No policy actor at decision boundary')
                if side not in drivers:
                    drivers[side] = policy.factory(deepcopy(policy.config),
                        plan.seed(sample_id, 'policy-' + side).generator())
                    if not callable(drivers[side]):
                        raise ValueError('Policy factory must return a driver')
                # Detached public data only; no session, snapshot, event futures,
                # engine seed, experiment records or chance source cross here.
                action = drivers[side](deepcopy(result), session.legal_actions())
            semantic = SimulationSession._action(action)
            row['attempted_decisions'] += 1
            result = session.step(semantic, session.state_revision)
            row['actions'].append(semantic.to_json())
            row['decisions'] += 1
            events.extend(result.events)
            data = result.primary['data']
            touchdown = touchdown or any(t['score'] > initial_scores[t['id']] for t in data['teams'])
            if lost is not None:
                lost = lost or _ball(data)[0] != possession
        session._game.dice.chance.finish()
        row['chance'] = session._game.dice.chance.metadata()
        row['final_context'] = session._game.timeline.context.to_json()
        row['final_state_hash'] = snapshot_hash(session.snapshot().engine)
        if row['status'] == 'valid':
            row['values'] = _values(result.primary['data'], touchdown, lost)
    except Exception as error:
        row.update(status='truncated' if isinstance(error, NoProgress) else 'failed',
                   reason=error.code if isinstance(error, SessionError) else type(error).__name__, values=None,
                   diagnostic={'error_type': type(error).__name__, 'message': str(error),
                               'cause': error.diagnostic.to_json() if isinstance(error, SessionError)
                               and type(error.diagnostic) is SessionDiagnostic else None})
    finally:
        if session is not None:
            session.close()
        if game is not None:
            game.close()
    return row


def _summaries(records):
    import json
    result = {}
    for name, definition in OBSERVABLES.items():
        values = [row['values'][name] for row in records
                  if row['status'] == 'valid' and row['values'][name] is not None]
        if definition.kind == 'binary':
            summary = binary_summary(values)
        elif definition.kind == 'scalar':
            summary = scalar_summary(values, definition.units)
        else:
            counts = Counter(json.dumps(value, sort_keys=True) for value in values)
            summary = {'n': len(values), 'categories': [
                {'value': json.loads(value), 'count': count, 'frequency': count / len(values),
                 'wilson95': _wilson95(count, len(values))}
                for value, count in sorted(counts.items())],
                'category_scope': 'observed categories only; unseen does not mean impossible'}
        summary.update(units=definition.units, excluded=len(records) - len(values),
                       denominator='valid_nonmissing_samples')
        result[name] = summary
    return result


def estimate_continuations(snapshot, first_action, continuation_policy, horizon, sample_plan):
    """Estimate independent continuations, retaining one record per requested ID.

    Input contract errors raise before sampling. Illegal actions and callback or
    execution errors retain unsuccessful sample records. No automatic retries.
    """
    if (type(horizon) is not Horizon or type(sample_plan) is not SamplePlan
            or type(continuation_policy) is not ContinuationPolicy):
        raise ValueError('Expected Horizon, SamplePlan and ContinuationPolicy')
    horizon.__post_init__()
    sample_plan.__post_init__()
    continuation_policy.__post_init__()
    # Own config before any trusted callback can mutate caller-owned objects.
    policy = ContinuationPolicy(continuation_policy.name, continuation_policy.version,
                                _json_copy(continuation_policy.config), continuation_policy.factory)
    first_action = None if first_action is None else SimulationSession._action(first_action)
    if horizon.limit == 0 and first_action is not None:
        raise ValueError('An initial action exceeds a zero horizon')
    if type(snapshot) is SessionSnapshot:
        if snapshot.truncation_reason is not None:
            raise ValueError('Failed session is not a continuation origin')
        snapshot = snapshot.engine
    if type(snapshot) is not Snapshot or snapshot.scope != 'engine':
        raise ValueError('Expected a trusted engine snapshot')
    origin = clone_from_snapshot(snapshot)
    try:
        if not origin.external_control or origin.timeline is None:
            raise ValueError('Expected an externally controlled timeline boundary')
        # Independent chance cannot erase a forced queue or fabricate naturality.
        if any(any(frame.values()) for frame in origin.dice._queues) or any(origin.dice._strict):
            raise ValueError('Independent rollouts cannot inherit forced dice queues')
        engine = capture_snapshot(origin)
        used_branches = {event.context.branch_id for event in origin.timeline.events}
        used_branches.add(origin.timeline.context.branch_id)
        provenance = {'state_hash': snapshot_hash(engine),
                      'context': origin.timeline.context.to_json(),
                      'descriptor': describe_rules(origin.config, origin.ruleset, origin.arena,
                          origin.state.home_team, origin.state.away_team).to_json(),
                      'origin_chance': None if origin.dice.chance is None else origin.dice.chance.metadata()}
    finally:
        origin.close()
    records = [_sample(engine, first_action, policy, horizon, sample_plan, sample_id, used_branches)
               for sample_id in sample_plan.sample_ids]
    counts = Counter(row['status'] for row in records)
    return {'format': 'ContinuationEstimateV1', 'schema_version': 1, 'method': 'monte_carlo',
            'snapshot': provenance,
            'first_action': None if first_action is None else first_action.to_json(),
            'policy': {'name': policy.name, 'version': policy.version, 'config': policy.config},
            'horizon': asdict(horizon), 'chance_mode': 'independent',
            'sample_plan': dict(asdict(sample_plan), sample_ids=list(sample_plan.sample_ids)),
            'randomness': {'derivation': DERIVATION_ALGORITHM, 'generator': GENERATOR},
            'counts': dict(attempted=len(records), **{k: counts[k] for k in ('valid', 'truncated', 'failed')}),
            'causes': dict(Counter(row['reason'] for row in records if row['status'] != 'valid')),
            'complete_unconditional_sample': bool(records) and counts['valid'] == len(records),
            'estimand': 'valid nonmissing continuations under the declared policy and horizon',
            'assumptions': ['Independent indexed engine and policy streams; factories own their state.',
                            'Matched samples require separate paired analysis and are unsupported here.',
                            'Conditional summaries with missing samples are not unconditional distributions.'],
            'observables': {name: asdict(definition) for name, definition in OBSERVABLES.items()},
            'summaries': _summaries(records), 'records': records}


def exact_dice_distribution(die, count=1):
    """Enumerate 1..3 independent fair D6 or block dice, including repeated PUSH.

    This exact raw-face distribution makes no tactical/continuation claim. It
    cannot be selected as the method of estimate_continuations().
    """
    if die not in ('D6', 'BBDie'):
        raise ValueError('Exact enumeration supports only D6 and BBDie')
    _natural(count, 'count', 1)
    if count > 3:
        raise ValueError('Exact enumeration is bounded to three dice')
    faces = DOMAINS[die]
    counts = Counter(product(faces, repeat=count))
    total = len(faces) ** count
    return {'format': 'FiniteDiceDistributionV1', 'schema_version': 1, 'method': 'exact',
            'scope': 'ordered independent fair physical dice; no selection, rerolls or game continuation',
            'die': die, 'count': count, 'physical_outcomes': total,
            'outcomes': [{'faces': list(outcome), 'multiplicity': multiplicity,
                          'probability': multiplicity / total}
                         for outcome, multiplicity in sorted(counts.items())]}


__all__ = ['ContinuationPolicy', 'Horizon', 'SamplePlan', 'Observable', 'OBSERVABLES',
           'binary_summary', 'scalar_summary', 'estimate_continuations', 'exact_dice_distribution']
