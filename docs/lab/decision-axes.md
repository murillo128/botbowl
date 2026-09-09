# Separate decision assessment axes (EVAL-05)

`botbowl.lab.evaluation.decision_axes` produces immutable, finite-JSON
`DecisionAssessmentV1` sidecars. Surprise, observed impact and expected decision
quality have separate values, units, provenance and reasons. There is no default
aggregate score, tactical objective, reward change or claim of universal tactical
intelligence. Human appeal judgments remain a fourth, subjective annotation field.

Run `python -m examples.lab.decision_axes` for small synthetic tables and an
engine START_GAME fixture, without training. Run
`python -m pytest -q tests/lab/test_decision_axes.py` for focused validation.

## Freeze the decision experiment

1. Capture a stable state identity (the engine adapter uses the persistent
   snapshot hash), its exhaustive legal action set, and the chosen action.
   `semantic_action_id(ActionV1)` encodes the full semantic action canonically;
   identical actions in different states are distinguished by the state identity.
2. Declare an `AssessmentContext`: state ID, reference model ID/version, policy
   name/version/configuration, complete SIM-07 `Horizon`, and per-action budget.
   The budget is the number of IID samples for Monte Carlo, or an upper bound on
   the number of enumerated support entries for exact tables. Horizon includes
   logical unit/limit, terminal treatment and engine decision/step caps.
3. Define a finite semantic variable and its entire discrete support, including
   possible categories not seen in a sample. Names are strings; each names an
   outcome of that variable, not a latent coordinate. Construct `exact_reference`
   with normalized probabilities or `sampled_reference` with sample outcomes,
   unique IDs, seed recipe and explicit origin. Exactness is a caller declaration
   about a finite model, never inferred from a deterministic sample. Reference
   probability normalization uses absolute tolerance `1e-12`, without
   renormalizing the supplied table.
4. Define utility ID/version, definition, unit and a finite value for **every**
   support category. Fix this utility, policy, horizon, legal and compared sets,
   sampling budget and event definition before examining the realized result.
   V1 supports utilities measurable on one finite outcome variable; composite
   outcomes may be explicitly named in a finite joint support. It does not infer
   utility from score, probability, embeddings, a learned reward or game victory.
5. Call `evaluate_decision(chosen_action, legal_actions, references,
   utility=..., origin=...)` **before** observing the actual outcome. This yields
   `DecisionQualityV1`. It retains the entire experiment and computed estimates
   in canonical bytes. Input and output mutations cannot alter it. Loading a
   record recomputes and checks its numerical claims.
6. After observing the result, call `assess_outcome(quality, result_id, outcome,
   event=[...], impacts=[...], annotations=[...])`. The result ID identifies this
   particular realized outcome. The outcome must belong to the explicitly
   declared event and chosen reference's support. Changing the realized outcome
   never updates the retained expected quality.

These APIs validate declared identities and numerical contracts; they do not
cryptographically authenticate model versions, the completeness of an external
legal list, capture times, or whether a human caller actually froze inputs before
looking at outcomes. Engine callers must obtain legality from `ActionControl` at
the same captured state. Code, rules, source snapshot and external experiment
receipts must remain available for reproduction. The record is not a snapshot
loader or a model-training interface.

## Surprise

For an event (a nonempty, duplicate-free subset of the declared discrete support),
`surprise(reference, event)` returns `p` and `-ln(p)` in **nats**. Compound events
sum mutually exclusive support probabilities. Standalone surprise output retains
its reference, including state, action, model/version, policy and horizon.

| Evidence | Status and numerical meaning |
| --- | --- |
| Exact positive probability | `available`, exact-reference self-information |
| Exact zero probability | `model_incompatible`, probability 0, null nats with explicit reason |
| Monte Carlo with successes | `available`, empirical frequency and plug-in self-information |
| Monte Carlo with zero successes | `censored`, frequency 0, null point nats; no impossibility claim |
| No samples | `insufficient_samples`, no probability or nats estimate |

For sampled events the probability interval is the two-sided 95%
[Wilson score interval](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)
reused from SIM-07. Transform `[L,U]` to `[-ln(U),-ln(L)]`. With zero successes,
`L=0` and the upper nats bound is null, explicitly meaning **unbounded**, rather
than an exact infinite surprise. No prior or smoothing is applied. Wilson
coverage is pointwise and approximate; sampling uncertainty is not model error
or evidence of calibration. All JSON remains finite; even an exact incompatible
outcome uses a status rather than a nonfinite JSON number. EVAL-04 log-score
values agree for positive probabilities, but its forecast-zero convention must
not be applied to zero Monte Carlo counts here.

## Impact and causal limits

`observed_impact(state_id, result_id, variable, unit, before, after, origin=...)`
computes the observed `after - before`. Booleans are indicator changes in
`{-1,0,1}`. `oracle_impact` accepts matching available EVAL-01 score, possession,
reroll, apothecary or bribe records; it preserves both records and their units,
entity IDs, rules and ordered capture boundaries. Those stored records are the
source of the engine demo's default impact measurements.

An observed difference is **not a causal effect**. V1 implements only this
observational method and rejects a relabeled causal/counterfactual row. A future
counterfactual method would need a separate API with explicit intervention,
comparison distribution, identification assumptions and uncertainty; these cannot
be inferred from two observations or from regret. State/result bindings on scalar
fixture inputs are trusted caller declarations.

## Expected decision quality

Every compared action must be in the declared legal set; the chosen action must
be evaluated. References must share state, model/version, policy/configuration,
full horizon, budget, method, variable and support. Mixed exact/sampled action
comparisons and unequal sample budgets are rejected. Exact expected utility is
`sum(p(outcome) * utility(outcome))`, with zero numerical sampling error. Sampled
expected utility is the sample mean; standard error uses unbiased sample variance
and is null for fewer than two samples.

Regret is `max(expected utility over compared actions) - chosen expected utility`,
in utility units. Partial evaluation reports `evaluated_subset`, retaining both
the exhaustive declared legal list and evaluated list. Full coverage reports only
`all_declared_legal_actions`: a sampled winner still does not certify optimality.
The compared set and budget must be fixed, without stopping or selecting a subset
because its noisy estimates look favorable.

For `m` compared actions, `n` IID samples per action and declared utility range
`[a,b]`, each sampled mean has radius
`(b-a) * sqrt(ln(2*m/0.05)/(2*n))`, clipped to `[a,b]`.
This applies the bounded-sum inequality from
[Hoeffding (1963)](https://www.tandfonline.com/doi/abs/10.1080/01621459.1963.10500830)
with a union bound over the fixed action set. Consequently the intervals jointly
cover the action expectations with at least 95% probability under the stated
sampling assumptions. They need independent samples **within** each action;
shared random streams **between** actions do not invalidate the union bound.
No independence-based standard error for a difference is invented.

Regret bounds are the maximum of zero and each alternative's lower bound minus
the chosen upper bound (lower endpoint), or alternative upper bound minus chosen
lower bound (upper endpoint). The chosen action is excluded from these differences
because its self-regret is exactly zero. This accounts conservatively for choosing
a maximum. With no samples, expected utilities and regret are null and intervals
retain only declared utility ranges. All-success samples can have zero empirical
SE while their bounded-mean intervals remain wide. These guarantees concern
sampling under the reference model, not model bias or tactical applicability.

## SIM-07 and EVAL-04 integration

`reference_from_continuations(report, model_id=..., model_version=...,
observable=..., categories={name: raw_value, ...})` adapts SIM-07 reports. It
reuses EVAL-04's sample/seed/status/origin receipt validator, checks the code-owned
observable definition, and retains policy, horizon, snapshot/rules provenance,
first action, sample plan and a SHA-256 digest of the original report. The original
report and snapshot should be retained outside the assessment, especially for
large experiments. No stored summaries are trusted as exact probabilities.

V1 requires an explicit first action at a positive horizon and complete,
nonmissing continuations. Failed/truncated/censored rows and missing or
out-of-support observables reject the adapter: silently comparing different
successful-completion populations could reverse action rankings. An absorbed
natural terminal state remains valid under the declared SIM-07 terminal rule.
Zero-sample plans retain uncertainty. Seed IDs must be unique; retries are not
new independent observations. Trusted factories must honor SIM-07 independence.
Forecast calibration remains the separate EVAL-04 protocol; this adapter does not
claim that engine or supplied reference probabilities are calibrated.

## Human rubric and sidecar isolation

The minimal `spectacle-v1` rubric asks for personal perceived appeal of the
**displayed outcome**: 1 little appeal, 2 moderately engaging, 3 highly engaging.
An annotation includes pseudonymous `author`, `rubric_id`, integer `rating`, a
free-text `rationale`, and explicit `consent: true` to store/share it with the
assessment. Obtain that consent from the annotator; do not manufacture it from
the presence of an annotation. Do not include identifying personal information.
One response per author is retained without adjudicating a true rating.
Disagreement is the rating range; fewer than two authors gives null. Neither
ratings nor disagreement change any of the three numerical axes.

`assessment_channel(record)` builds an explicit evaluation-channel envelope.
It may be included alongside public channels, but `project_inputs`' standard
profile excludes it. For `EpisodeRecorder.append_channel`, pass the envelope's
`data` payload. Nothing automatically attaches these records to observations,
policies, encoders, training inputs, rewards or simulator state.
