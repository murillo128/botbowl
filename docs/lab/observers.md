# Observation transforms and OOD protocols (DATA-08)

`botbowl.lab.observers` transforms copied public observations or explicitly
selected channel data. It never accepts a `Game`, executes an action, refreshes
legality, or draws from game/policy RNG. Run the reproducible CPU example with
`python -m examples.lab.observers`: it records one episode, then compares full
and partial histories with identical cutoffs and targets.

## Masked projection contract

`transform_observation(channels, spec, available_at=..., observer_seed=...,
profile=PRIMARY_PROFILE)` accepts an `ObservationV1`, its JSON representation,
or channel envelopes. `available_at` is the complete DATA-02 `TimelineContext`
with episode/branch IDs, event/decision counters and nullable scope counters.
Only the returned `features` and `present` mappings belong in model input.
`retained` reports observation availability. Metadata is separate and includes
the privileged observer seed recipe; do not feed it to the model.

The output is a **masked projection**, not an `ObservationV1`. Feature keys and
nested array shapes match the selected `InputProfile`; `present` has parallel
Boolean leaves. A missing/hidden leaf is JSON `null` with `false` presence,
never an observed zero. An originally absent Presence value stays absent.
Empty arrays stay empty. Present leaves retain the profile's string, Boolean,
integer or number dtype; this API does not implicitly pad or cast them to NumPy.
The numeric entity field units reuse `views.ENTITY_UNITS`. Existing full entity,
grid and graph views remain available for comparison. They require complete
ObservationV1 data: do not pass masked projections to them or rebuild geometry
from hidden fields. This V1 does not supply masked grid/graph encoders.

`ObservationTransformSpecV1` stores schema version 1, a positive caller-managed
`revision`, exact `hidden_fields` profile paths, stable `hidden_entities` IDs,
zero or one `NumericNoiseV1` per numeric field, `every_k` and `offset`. Round-trip
through `to_json()` / `from_json()` retains the contract. Unknown fields/entities,
unsupported versions, mismatched units/dtypes and duplicate selectors fail.
Changing protocol semantics requires a new revision; retain the whole spec as
provenance, not just the revision number. There is no global revision registry.

Composition is fixed: select/validate the input profile; determine temporal
retention; hide fields/entities; add noise to remaining present leaves; round
integers; apply the declared bounds policy. Hidden entities mask every selected
leaf under their player/ball row while preserving array alignment. Player IDs
are episode-local `home:N` / `away:N`; ball IDs are snapshot-local `ball:N` as in
the entity view. Ball order must be stable within a snapshot. Referencing a
missing entity is an error, including in an empty roster. A field selector is
an exact selected leaf (including `[]` dimensions), not a glob or parent path.

Entity hiding does **not** claim to hide roster length, array positions,
references in other rows, or correlated information. Hide those fields explicitly
where authorized. A true Presence flag is itself an observable feature unless
selected for hiding; the separate transform presence mask describes whether
that flag can be read. Neither partial context nor strict partial observability
or identifiability is asserted.

## Noise and reproducibility

Noise has explicit `path`, `distribution`, `scale`, `unit`, `minimum`, `maximum`
and `bounds`. Normal scale is standard deviation with zero mean; uniform noise
is symmetric on `[-scale, scale)`. Scale uses the original field's units.
Integer fields round to nearest integer, ties to even; their declared endpoints
must be integral. Bounds are limited to the exactly representable integer
range `[-2**53, 2**53]`. `bounds='error'` rejects excursions; `'clip'` explicitly
clips after rounding. Visible original values outside the declared interval always fail. Clipping/rounding changes the resulting distribution; the manifest records
that composition. Non-finite parameters/results fail. This guarantees the
masked projection's leaf types and declared bounds, not a physically realizable
joint state. Choose intervals meaningful for the experiment and arena.

Coordinates use `arena_cell`, attributes use `attribute_point`, movement uses
`movement_step`, earned SPP uses `spp`; other primary numeric fields use `count`.
Derived roll probabilities use `probability`, block dice use `signed_dice`,
and tackle zones use `count`. Boolean flags and IDs cannot receive numeric noise.

Each noisy leaf gets a fresh `SeedSpec` stream with purpose `observation`, the
explicit observer master seed, episode ID, and a SHA-256 component key over
complete capture context, full transform specification, field, entity and
remaining array indices. Algorithm/generator/address revisions are recorded.
Roster row ordinals are replaced by stable player/team IDs; other nested array
indices remain part of the address. No worker, request counter, iteration order,
or mutable stream enters it. Reading a frame again, reversing reads, or changing
worker count cannot change its observation. Branch/event/decision identity and
transform revision separate noise streams. A changed observer seed changes only
perception; generation and policy decisions are unchanged.

## Causal subsampling and auxiliary inputs

Retention is `decision_seq % every_k == offset`, with `every_k >= 1` and
`0 <= offset < every_k`. It is based on the original logical counter, including
when a branch begins partway through an episode; it never restarts at a window
boundary. Event sequence still distinguishes multiple instants at one decision.

`iter_observed_windows(reader, window_spec, transform_spec, observer_seed=...,
split_manifest=...)` wraps DATA-03 `iter_windows`. It transforms only histories,
adds parallel `inputs.field_presence`, and marks unretained history slots
`None` / false. Every eligible cutoff, original history reference, transition
range, gap, target, and split membership remains intact. DATA-03 branch/episode
and availability validation still runs. Horizon 1 remains one actual decision;
k decisions are never relabeled as a one-step transition. Window stride and
history stride remain independent of perception sampling. Source/target
projection metadata remains privileged; feed only `inputs` to the model.

The channel projector validates selected channels before any hiding, even for
a dropped frame. Injected labels/futures inside primary data fail. Evaluation
and privileged payloads are never selected by the wrapper. Their envelopes need
not be read. Legal masks remain control data; using one as input requires the
existing explicit profile path/option and is recorded under `aids`. Derived
aids can reveal hidden information and are recorded separately. Action-conditioned
windows still expose the selected cutoff action, even at an unobserved cutoff;
choose passive mode if that information is excluded by the experiment.

These are offline data transforms. A policy with restricted perception needs
an explicit caller opt-in and a separately versioned policy/experiment; the
wrapper does not change the generating policy or label it as restricted.

## OOD manifests

First freeze whole-origin families with DATA-04 `build_split_manifest`. Then
call `build_ood_manifest` with that frozen split, one protocol per source,
explicit held-out identities, a `WindowSpecV1` history contract, and an experiment
`protocol_version`. Each protocol contains plain JSON `scenario` (ScenarioSpecV1),
`profile` (InputProfile), `transform` (ObservationTransformSpecV1), and home/away
`policies` with non-null versions. Source versions and family membership come
from the frozen split. Scenario/policy groups must match the declared protocol.

`ood_protocol_axes(protocol, source_version=...)` gives identities for axes
`scenario` (the exact variant ID), `policy` (DATA-04 ID/version keys), `profile`,
`transform`, `rules` (SHA-256 identities), and `source_version`. Pass holdouts as
`{axis: [identity, ...]}`. All occurrences of each held-out value must belong
to the declared evaluation split. Every evaluation family must have a held-out
factor, including siblings grouped by DATA-04. Missing identities, contamination,
unjustified evaluation families, malformed configurations or versions fail.
No source is moved to another split. DATA-04 directly supports scenario/policy
holdouts; other axes must already have a compatible family assignment.

The manifest retains the frozen split and complete per-source protocols,
expanded history (with each source's input profile), explicit hidden fields and
noise, rules descriptors and independent variation axes. It is privileged
provenance: ScenarioSpec includes its seed. `validate_ood_manifest` recomputes
these declarations and rejects stale/corrupt manifests. Source declarations
must come from trusted producers and authenticated artifacts; this validator
does not prove that an arbitrary supplied ScenarioSpec generated a given file.

Arena size, roster/rules/configuration changes belong to the scenario variant
and its RulesDescriptor, never to observation noise or a scalar difficulty
label. Fit preprocessing only on training families. Retain protocol/source
versions and the frozen split with evaluation results.

## Observational aliasing

Two observations differing only in team score become identical model inputs
when `primary.teams[].score` is hidden. The tests demonstrate this with distinct
public states. Recovering that score is then unidentifiable from those inputs
alone; report the aliasing and available history rather than attributing it to
an encoder bug. Retained aids, action history and temporal context may change
what is identifiable, so include them in the experimental claim.
