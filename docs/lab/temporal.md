# Temporal forecast evaluation (EVAL-03)

`botbowl.lab.evaluation.temporal` evaluates external predictions as inert data.
It never loads an encoder, decoder, plugin or checkpoint and never fits a model.
Run the CPU example with `python -m examples.lab.temporal`; run the focused tests
with `python -m pytest -q tests/lab/test_temporal_evaluation.py`.

## Inputs and formats

1. Build validated DATA-03 windows with `iter_windows` and the frozen DATA-04
   split manifest. Keep the window horizon at least as large as the largest
   evaluation horizon. Default evaluation horizons are **1, 2, 4, 8 decisions**;
   positive custom decision horizons are supported. Events, seconds and team
   turns are not interchangeable with primitive decisions.
2. Call `prepare_case(window, split_manifest=..., protocol=...,
   continuation_policy={'name': ..., 'version': ..., 'config': ...}, budget=...)`.
   The budget is a declared maximum number of decisions from the last visible
   cutoff, including any autonomous advance before emission and the horizon.
   This is a logical budget; V1 makes no wall-clock, FLOP or training-cost claim.
3. Obtain `forecast_request(case)`. Pass **only `request['inputs']`** to the
   predictor. All remaining fields are evaluator/controller metadata, including
   origins, split membership and reference IDs. Do not pass the case, targets,
   label sidecars or simulator continuation report to the predictor.
4. Return a `ForecastV1` through `make_forecast(case, predictions,
   model_id=..., model_revision=...)` or `ForecastV1.from_json(...)`. A semantic
   prediction is `{'horizon': 2, 'task': TemporalTask('team.score', 'home').to_json(),
   'value': 1}`. A producer failure uses an empty prediction list and a nonempty
   `failure` reason. An undecoded embedding uses `representation='embedding'`
   and rows `{'horizon': 2, 'values': [0.1, 0.2]}`. Model/revision strings are inert
   public identifiers; producers must supply an immutable revision identity.
5. Call `evaluate_forecasts(cases, forecasts, tasks, models=[(id, revision), ...],
   records=oracle_records, events=event_records)`. Declare the model roster
   before evaluation so completely absent forecasts remain countable.

`ForecastV1` is versioned canonical JSON with detached reads. It includes the
exact input receipt: model/revision, origin/family/split, episode/branch emission
context, last visible context, input profile/window specification, passive or
conditioned mode, information protocol, horizons, continuation policy, decision
budget, known-action contract, history references and feature values. Case IDs
hash this receipt without future targets, target masks, future actors or end
reasons. Cases are controller-owned products of `prepare_case`; their source
windows must come from DATA-03, not hand-authored untrusted JSON. Evaluation
rechecks the causal references, source/request consistency and target alignment.
Family membership is validated against the split manifest during preparation.

The bounded JSON formats inherit DATA-02's per-record size limits. Evaluate
manageable cohorts; this evaluator materializes its cases, label/event indices
and report. It is not a streaming replacement for DATA-03's reader.

## Information protocols and causality

| Protocol | Visible observations | Known actions |
| --- | --- | --- |
| `teacher_forced` | Real history through each emission cutoff | Passive: none. Conditioned: only the action selected at that cutoff. |
| `autonomous` | Fixed `initial_window` history, even for later emissions | Passive: none. Conditioned: only the initial selected action. |

Autonomous emission and target times are still aligned to recorded decision
counters; its context age is emission minus initial cutoff. All horizons are
relative to emission. The initial window must share the emission's origin,
branch, split and window/profile specification. V1 does **not** authorize a
future command sequence. A continuation policy's identity does not make its
future decisions, or an opponent's choices, known inputs. In conditioned mode,
only the initially selected command is carried forward. Predictions can remain
conditioned on the declared continuation policy as a distribution without
receiving its realized future commands.

Availability checks reuse DATA-03 `validate_availability`; source window checks
reuse the EVAL-04 window/reference validator. Future history references,
unauthorized fields/actions, altered feature payloads, changed views or budgets,
and foreign/duplicate receipts reject evaluation. An external producer can lie
about what it actually consumed or about its training data: the receipt validates
its declaration against the authorized source, not the honesty of arbitrary
external code. Authenticating original DATA-02 capture remains the caller's
responsibility, exactly as for DATA-03.

## Targets and absence

Catalogue `state_fact` and `exact_rule_quantity` labels reuse EVAL-01
`EvaluationRecord`, including its version, units, category, domain conditions
and typed unavailability. Records join on episode, branch, event and decision
counters, entity, related entity, label and version; full context must agree.
No estimated tactical quality is promoted to simulator truth.

`decision.actor` predicts the actor at the target boundary (`home`, `away` or
null). `decision.actor_changed` compares that actor with the emission actor;
null at terminal counts as a change. Repeated actors do not advance time twice
or imply alternation. `event.<OutcomeType>` predicts whether that report occurred
in `(emission, target]`, using complete EventV1 IDs over the event interval.
A missing event record makes the task unavailable, never a negative event.
`match.terminal` uses the catalogue's stored terminal flag.

The exact terminal boundary is evaluable. Later horizons stay absent; the
implementation never repeats a final state or zero-fills future truth. Reports
separate early terminal, truncation, pending transitions and trace/branch gaps.
Missing/unavailable/misaligned labels remain explicit. Invalid semantic values,
missing task predictions, completely missing forecasts, failed producers and
embeddings without a decoder also remain in attempted denominators. Every row
retains both `prediction_reason` and `target_reason` when both fail; the aggregate
`causes` uses prediction failure first. Producer error text is retained in rows.
Structural/causal violations reject the run rather than silently dropping rows.

## Metrics, baselines and uncertainty

Metrics remain separate by model/revision, protocol, context age, full window
specification/profile/mode, split/version, policy/configuration, decision budget,
task/entity and horizon. Task family and units accompany each metric. There is
no aggregate mixing teacher-forced and autonomous results, different splits or
incompatible physical units. `by_family` retains episode-family denominators.

Numeric targets report MAE and RMSE. Positions use Manhattan cell error and its
root mean square. Categorical/boolean targets use mismatch error rate (and the
root mean square of its zero/one errors). `valid`, `attempted`, `excluded` and
causes accompany every metric; zero valid samples produce null estimates.
Domain checks validate catalogue types/ranges and captured player identities.
They do not establish transition legality, reachability, tactical value or full
state consistency. There is no invented legality percentage, especially for
undecoded embeddings; no latent distance metric or comparison of arbitrary
latent scales is reported.

Persistence copies the last authorized observable label. The linear baseline
extrapolates the last two available scalar values using their actual decision
spacing to the emission-plus-horizon time; for other observable labels it uses
persistence. Both use false as the simple no-event baseline. Neither fits on
labels or reads targets, later observations or future commands. A label absent
from the profile, such as an actor/entity reference excluded by DATA-01, is
reported unavailable for the baseline. Unavailable history and invalid
extrapolations remain visible. Entity IDs in evaluator metadata select existing
feature slots; they never become predictor features.

Baseline comparisons use the **same cases, view, history, actions, budget and
valid intersection** for each model/baseline pair. Reports retain attempted,
paired and unpaired counts, so a producer cannot hide failures through a better
score on a smaller subset. Negative paired error deltas favor the external
model. This is equivalence of available information and logical rollout budget,
not a claim of equal compute used by different implementations.

Uncertainty uses the DATA-04 origin family as its sampling unit. For each task
and horizon, compute the mean error within each family, then the equally
weighted mean across families and sample standard deviation divided by the
square root of the number of families. Fewer than two valid families gives null
standard error. Paired comparisons apply the same calculation to within-case
error differences. Independence is assumed only **between** those families;
correlated scenarios require a coarser upstream family assignment. Overlapping
windows or unequal sibling branches never count as independent replications.
The pooled window error is descriptive and is reported separately from the
family-weighted estimate.

The example samples SIM-07 continuations from an identically configured initial
state, records a sampled primitive command prefix, captures oracle score targets,
and evaluates an artificial score predictor in both protocols. It requires no
GPU and makes no learned forecasting claim.
