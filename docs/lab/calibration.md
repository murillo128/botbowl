# Calibration of probabilistic futures (EVAL-04)

`botbowl.lab.evaluation.calibration` scores inert ForecastV1 predictions against
held-out SIM-07 continuations. It does not load, train, select or recalibrate a
model. Run `python -m examples.lab.calibration` on a CPU; the focused check is
`python -m pytest -q tests/lab/test_calibration.py`.

## Prepare and freeze the experiment

1. Prepare DATA-03 cases with the frozen family split and `prepare_case` from
   [temporal evaluation](temporal.md). Calibration requires `test` membership.
   Pass only `forecast_request(case)['inputs']` to the predictor. Fix the model
   roster, immutable model revisions, targets/support, bins and interval levels
   before inspecting evaluation labels. List all training, selection and tuning
   families in `fit_families`; any intersection with test families rejects the
   evaluation. The evaluator has no fitting or parameter-selection operation.
2. Use `CalibrationTarget(TemporalTask(...), kind, support=...)`. The reviewed
   SIM-07 mappings are `event.TOUCHDOWN` (any touchdown during the prefix),
   `team.possession` (home/away possession at the settled endpoint),
   `team.score` and `team.rerolls` (home/away endpoint quantities).
   Kinds are `binary`, `categorical` and `scalar`. Categorical support is an
   ordered tuple of at least two distinct domain-valid outcomes. It is the
   declared full support, not just the classes observed in this sample.
   Numeric quantities can also have a finite categorical distribution.
   Other TemporalTasks require an explicit reviewed observable mapping before
   SIM-07 calibration can evaluate them; they are rejected today.
3. Freeze the ForecastV1 with `make_forecast`. Each semantic prediction retains
   the normal `horizon` and `task`, and uses one of these `value` payloads:

   ```python
   {'kind': 'binary', 'probability': 0.7}
   {'kind': 'categorical', 'support': [0, 1, 2],
    'probabilities': [0.6, 0.3, 0.1]}
   {'kind': 'samples', 'unit': 'touchdown', 'samples': [0, 0, 1, 2]}
   {'kind': 'point', 'unit': 'touchdown', 'value': 1}
   ```

   Scalar units match the TemporalTask catalogue exactly (e.g. `touchdown`,
   `reroll`); the adapter maps SIM-07's plural/descriptive unit names to those
   catalogue semantics. Samples must obey the quantity's domain, so score and
   resource samples are nonnegative integers. A raw semantic value is treated
   as a point prediction. Points receive only absolute and squared errors, or
   categorical mismatch; they never implicitly become calibrated distributions.
   Embeddings and uninterpreted residual dictionaries cannot receive calibration
   scores. A legitimate explicitly declared one-sample distribution can be
   scored, but it supplies no sample-variance estimate.
4. For each `(case_id, horizon)`, obtain two `estimate_continuations` reports,
   named `reference` and `evaluation`. Use the same trusted snapshot, selected
   initial action (or `None` in passive mode), policy name/version/configuration,
   and full `Horizon`. Use disjoint SamplePlan IDs for their sampled suffixes;
   retain master seed, experiment namespace and derivation version. Keep the
   maximum decision budget within the ForecastV1 budget. All case horizons must
   have a pair, including empty or unsuccessful plans.
5. Call `evaluate_calibration(cases, forecasts, targets, models=[(id, revision)],
   continuations={(case_id, h): {'reference': reference, 'evaluation': evaluation}},
   bins=(0, .2, .4, .6, .8, 1), levels=(.5, .8, .95), fit_families=...)`.
   Omitted models/predictions remain in the declared roster's denominators.

The pair validator checks context, action, policy, horizon/budget, snapshot,
randomness metadata, sample IDs, engine and both policy seed recipes, final
branch origins and status counts. Reference and evaluation cannot reuse streams
or sampled origins, including across cases/horizons. The **parent snapshot and
origin family are intentionally shared** to estimate the same conditional
future; the sampled suffix origins and seeds must differ. Disjoint sample IDs
are the simplest way to avoid both stream and branch reuse. Within one role,
reusing a stream across correlated windows does not make it independent evidence;
the validator rejects assigning those shared streams to different families.
Keep every related episode/branch/window in its original family. Families used
for training or tuning must be disjoint from all these held-out families.

Only decision horizons are compatible with ForecastV1. Initial autonomous
emissions are supported; later autonomous emissions are rejected because SIM-07
has no receipt for the intervening autonomous prefix. Both reports must use the
same terminal rule: `absorb` evaluates the final endpoint after early termination;
`censor` excludes it with a reason. An absorbed endpoint is an explicit estimand,
not an invented recorded future observation. Failures, missing observables and
censoring are never converted to negative events.

Cases and reports remain trusted products of the capture/continuation APIs,
not authenticated remote assertions. Matching two report hashes cannot prove
that a producer honestly ran that snapshot or withheld test data during external
training. The caller owns capture authenticity, the complete `fit_families`
declaration and a policy factory that honors the SIM-07 isolation contract.

## Scores and reliability

All scores are losses: smaller is better. Binary Brier is `(p-y)^2`; categorical
Brier sums `(p_k - 1[y=k])^2` over the **entire declared support** without dividing
by class count. Consequently a two-class categorical Brier is twice the binary
convention. Log score is negative natural log of the observed probability. An
exact zero gives positive infinity, serialized as the string `"infinity"`, with
an explicit count and an infinite aggregate. There is no clipping or discarded
infinite observation. Normalization tolerance is absolute `1e-12`, with no
renormalization. CRPS is `mean|X-y| - mean|X-X'|/2`, using all ordered empirical
sample pairs including their diagonal; this scores the empirical CDF, not an
unbiased estimate of an unknown parent distribution's CRPS. These conventions
follow the loss versions of the proper scores discussed by
[Gneiting and Raftery (2007)](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf).

Scalar sample intervals default to central, equal-tail empirical quantiles using
linear interpolation at `(n-1)q`. Alternatively supply `intervals` in the samples
payload, as a list of `{'level': .8, 'lower': 0, 'upper': 2}` matching every
predeclared level and its order. Empty, missing, inverted or nonfinite bounds
are rejected. Equal endpoints are valid closed, zero-width intervals. Coverage
includes endpoints. Width and CRPS retain quantity units; variance and squared
error use squared units. Coverage is marginal for the declared evaluation
population, not a conditional or simultaneous guarantee at a particular context,
entity or collection of horizons.

Reliability is positive-class for binary targets and one-vs-rest for **every**
categorical class. Fixed bins are left-closed/right-open, except the final bin
also includes 1. Empty bins stay visible with null estimates. Each bin retains
outcome count, case count, average probability, observed frequency, number of
families and uncertainty. ECE is a sample-weighted descriptive absolute gap per
class. It is neither the sole acceptance criterion nor evidence of resolution;
a constant forecast may have low ECE and poor proper scores.

## Aggregation and uncertainty

There is no overall scalar that averages incompatible tasks. Report strata
separate model/revision, task/entity/support/units, horizon (including terminal
rule and execution caps), protocol, input profile/window/mode, split/version,
policy/configuration and decision budget. Classes are summed only for the
categorical Brier; reliability remains classwise. Distinct entities/horizons
remain separate. Each stratum reports attempted and invalid **forecasts**, actual
attempted **samples**, valid samples, excluded rows and reasons. For an empty
sample plan one sentinel row preserves its attempted forecast but contributes
zero attempted samples. Every row retains prediction and target failure reasons;
the aggregate cause chooses the prediction reason first. Each metric has its own
scored denominator, including the distinct subsets for points and distributions.

Pooled means weight valid evaluation outcomes equally. Family summaries first
average valid outcomes within each family, then weight families equally. Thus
families with more windows, branches or samples do not dominate that estimate.
Standard errors use the sample variance of family means divided by the number of
families; fewer than two families gives null and `insufficient_families`.
Unbounded-loss standard errors are descriptive, not confidence guarantees.

Reliability frequencies and interval coverage also report a distribution-free
95% Hoeffding interval for the mean of independent bounded family means, with
radius `sqrt(log(40)/(2*families))`, bounded to `[0,1]`. It remains wide for small
cohorts even when all observations agree, unlike a degenerate bootstrap interval.
It targets the **family-weighted** frequency/coverage, not the pooled window
frequency. It assumes independent families and fixed bins; it is pointwise,
not simultaneous across all bins/classes/horizons. Correlated scenarios need a
coarser upstream family assignment. More samples inside a family do not increase
the reported number of independent units.

## Three separate sources of variation

The `sampling` receipts retain origin families, contexts, snapshot hashes, seed
plans and randomness derivation metadata for both roles. Retain the original
ForecastV1, case and SIM-07 inputs alongside the report to reproduce it.

The `references` section is computed **only** from reference continuations. It
reports their denominator and exclusion causes, the empirical distribution,
engine variability (scalar sample variance or categorical Gini impurity), and
Monte Carlo error (scalar mean standard error or classwise Wilson frequency
intervals). These Monte Carlo errors assume the indexed continuations within
that fixed snapshot use independent engine and policy streams; they do not
replace family-level uncertainty across evaluation contexts. Fewer than two
reference outcomes explicitly reports `insufficient_samples`. Summaries with
missing outcomes describe valid nonmissing continuations, not an unconditional
future distribution.

The `metrics` section measures predictor error using **only evaluation**
continuations. Neither the reference distribution nor test labels alter the
frozen forecast, bin edges or parameters. Predictor sample variance is a diversity
diagnostic, reported separately from interval coverage and CRPS. Diverse samples
do not prove coverage. Natural engine randomness, finite-reference Monte Carlo
error and predictive losses do not by themselves identify epistemic uncertainty.
A calibrated predictor is not expected to predict each individual die correctly.

The CPU example checks a known zero-probability START_GAME touchdown event using
separate continuations. It also compares an exactly enumerated fair D6 event
(`D6 >= 2`, probability `5/6`) to its expected Brier (`5/36`). The tests additionally
sample the actual engine D6 implementation. Exact raw dice enumeration makes no
claim about tactical continuations with selection, rerolls or changing policies.
The artificial example is a reproducibility check, not learned-model evidence.
