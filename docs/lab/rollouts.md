# Continuation estimates (SIM-07)

`botbowl.lab.rollouts.estimate_continuations(snapshot, first_action,
continuation_policy, horizon, sample_plan)` runs CPU samples sequentially from
independent engine clones. It composes SIM-03 snapshots, the SIM-05/SIM-06 branch
primitive, `SimulationSession`, and SIM-02 indexed `SeedSpec` streams. It estimates
behavior under the supplied policy and horizon; it does not optimize that policy.

```python
from botbowl.lab.rollouts import (
    ContinuationPolicy, Horizon, SamplePlan, estimate_continuations,
)

# source is an initialized, externally controlled SimulationSession.
def first_legal_factory(config, rng):
    return lambda observation, legal: legal.actions[0]

report = estimate_continuations(
    source.snapshot(), source.legal_actions().actions[0],
    ContinuationPolicy('first-legal', 'v1', {}, first_legal_factory),
    Horizon('decisions', 2, max_decisions=100, max_steps=100000,
            terminal='absorb'),
    SamplePlan(tuple(range(10)), master_seed=123, experiment_id='example'),
)
```

An engine `Snapshot` or nonfailed `SessionSnapshot` is required. Keep the source
snapshot artifact: the report contains its persistent semantic hash, logical
context and rules descriptor, not executable state. Snapshot persistence and
compatibility checks belong to [SnapshotFileV1](snapshot-files.md). Existing session
budgets are replaced by the explicitly declared sample budgets. Engine snapshots
must represent compatible externally controlled timeline boundaries. Forced dice
queues are rejected; an inherited chance tape is replaced by independent chance.
Origin chance provenance is retained, including known fabricated history.

## Policy and randomness

A `ContinuationPolicy` identifies trusted code by name, version and finite JSON
configuration. Its factory receives a copied configuration and an owned policy
MT19937 generator, returning `driver(observation, legal_actions)`. A factory is
invoked lazily once per participating team per sample. Drivers receive detached
`SessionResult` public channels and semantic legal actions for the acting team.
They do not receive a game, snapshot, sibling, engine RNG/seed, or future tape.
Consumed public reports are past history, not privileged future data. Factories
must return independent driver state and use the supplied RNG for random choices.
Global mutable state, external randomness, or mislabeled code violate this trusted
callback contract; this is an API boundary, not a Python security sandbox.

Every engine and policy seed uses `SeedSpec(master_seed, str(sample_id), purpose,
experiment_id, derivation_version)`, with purposes `engine`, `policy-home` and
`policy-away`. IDs are unique nonnegative integers. The plan retains the explicit
master seed and derivation version. Execution order never enters seed derivation.
Reordering, splitting or retrying the same IDs reproduces their records under the
same snapshot/code/configuration. A retry is a separate invocation with the same
ID and seed, not another independent observation; do not concatenate retries as
new samples. No automatic retry searches for a successful outcome. No source or
sibling RNG is consumed. `matched`, `replay` and `forced` plans are not accepted by
this estimator; matched experiments from SIM-06 require separate paired analysis.

## Horizon and observables

`Horizon` declares a nonnegative limit and one of these logical units:

| Unit | Measurement from the origin |
| --- | --- |
| `decisions` | Accepted primitive actions, including `first_action` |
| `events` | All newly emitted API-02 timeline events |
| `turns` | Newly emitted `team_turn_ended` events, including an initial partial turn |

Each action settles before measuring progress. An event/turn horizon therefore
includes the complete settling batch that first reaches or exceeds the limit;
its actual progress is retained and can exceed the limit. It never cuts through
an automatic procedure. `max_decisions` bounds decisions independently of the
logical horizon, and `max_steps` bounds automatic engine work per decision. A
trusted callback must return or raise; these budgets do not preempt Python code
that hangs inside a callback.

`terminal='absorb'` treats a natural early end as a valid shorter continuation,
retaining the terminal state and all events observed before it. `terminal='censor'`
excludes a natural end strictly before the horizon and records
`terminal_censored`. A natural end that reaches the limit satisfies the horizon.
A zero horizon is an empty valid prefix and requires `first_action=None`. With
`first_action=None` and a positive horizon, the policy supplies the first action.
An origin already terminal is handled without invoking a policy or applying an
action. A budget exhausted before reaching the horizon produces truncation.

`OBSERVABLES` is a read-only inventory of definitions, extended only by reviewed
Python code together with the evaluator. Reports carry each definition's kind,
units, timing and missing-value treatment:

- `touchdown`: either team's score increases during the sampled prefix.
- `possession_loss`: the team carrying the single ball at the origin ceases to
  carry it at any subsequent settled boundary, including after a score. With no
  initial carrier this observable is inapplicable, not false. Transient changes
  entirely within an automatic settling batch are outside this definition.
- `ball_position`, `ball_possession`: the last settled ball position and owning
  team. No ball and a loose ball are explicit categories; absent positions are
  explicit nulls. Multiple balls are unsupported and fail the sample.
- `score_home`, `score_away`: final scores in touchdowns.
- `rerolls_*`, `apothecaries_*`, `bribes_*`: final remaining uses for each team.

All observables exclude failed and truncated samples, even if an event was seen
before the failure. They never turn an unobserved suffix into a negative event.
Completed records retain raw values; unsuccessful records have `values: null`.

## Statistics and report meaning

`ContinuationEstimateV1` is an inert JSON-compatible controller/evaluation report
with `schema_version: 1` and `method: monte_carlo`. It retains the source identity,
action, policy, horizon, independent chance mode, full sample plan/seed algorithm,
per-sample records, counts, causes, definitions and summaries. Each record retains
its ID, engine/policy seed recipes, accepted action sequence, accepted/attempted engine decisions and
logical progress, status/reason, diagnostic, and settled final state hash/context
when available. Those actions plus the retained source/seed recipes allow replay
without calling the policy. Executable policy code/version and the source snapshot
must be retained separately. The report is not policy input or a snapshot loader.
Large reports/samples should remain outside Git.

`attempted = valid + truncated + failed` counts samples, not retries or decisions.
Illegal initial/continuation actions and callback/engine errors remain failed
records. Engine no-progress/step-budget errors are truncated. Global malformed
contracts/snapshots are rejected before sampling. N=0 yields an empty report.

Every observable has its own `n`, `excluded`, and explicit
`valid_nonmissing_samples` denominator. Binary summaries retain successes,
frequency and a two-sided 95% Wilson score interval using
[z = 1.959963984540054 and the score-inversion formula](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
Zero/all-positive samples still have interval width. N=0 has null estimates and
intervals. Scalar summaries give units, mean, unbiased sample variance (N−1),
standard error, and empirical 0/25/50/75/100% quantiles interpolated at `(N−1)q`.
Variance/SE are null when N<2. Categorical frequencies sum to one when N>0 and carry marginal Wilson intervals
(not simultaneous coverage). Only observed categories are listed; unseen categories
are not claimed impossible;
missing/inapplicable observations do not silently join their denominator.

Uncertainty assumes independent identically configured samples. It describes
sampling error, not policy/rules/model bias. With any failure/censoring, summaries
condition on successful completion and may be selection biased; they cannot be
advertised as complete unconditional continuation distributions. The report's
`complete_unconditional_sample` is false for those cases and for N=0. Even when
true it describes sample completeness, not exactness or applicability of every
observable (possession loss can still have no initial carrier).

## Bounded exact results

`exact_dice_distribution('D6' or 'BBDie', count=1..3)` separately returns
`FiniteDiceDistributionV1` with `method: exact`. It enumerates all `6**count`
ordered equiprobable physical outcomes, aggregating the block die's repeated PUSH
face with its correct multiplicity. Probabilities are multiplicity divided by the
physical outcome count; floating-point serialization can round their sum.

This is an exact raw-dice experiment only: it includes no face selection, rerolls,
skills or continuation policy. Game-specific bounded probability queries remain
in [the probability-query API](../probability-queries.md). General trajectories,
including deterministic sampled prefixes, always stay `monte_carlo`; there is no
flag to relabel sampled results as exact.
