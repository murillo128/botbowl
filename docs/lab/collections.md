# Experimental event collections (DATA-09)

`botbowl.lab.collections` composes the generation, scenario, recorder, chance,
snapshot, branch, intervention, split and storage APIs. Its JSON is inert data:
no predicate, policy, patch or chance field imports Python or evaluates an
expression. The [CollectionSpecV1 schema](collections.schema.json) describes
the input envelope; Python validators additionally enforce the existing
generation, dice-tape, editor and record contracts.

## Modes and interpretation

| Mode | Retention and provenance |
| --- | --- |
| `natural` | Retain each successfully generated episode, without an outcome predicate. The recipe, policy, seed and horizon still define the population. |
| `selected` | Retain episodes satisfying a registered event predicate, until the target or search budget is reached. Keep rejected/failed attempts in the audit denominator. |
| `forced` | Retain the factual episode and a SIM-05 continuation with an explicit SIM-06 forced tape. Even an empty forced tape retains `natural: false`. |
| `intervened` | Retain the factual episode, exact SIM-08 patch, edited snapshot and continuation. Independent or explicitly forced continuation chance is recorded separately. |

Here **factual** means the unmodified baseline produced by the declared recipe.
A synthetic scenario does not become a naturally reached game state. `natural`
describes absence of outcome selection, injected dice and editor patches in that
baseline; it does not attest natural initial-state reachability, human play or
an unbiased distribution over all Blood Bowl games. Branch roots remain
`simulated_alternative`, and preserve the synthetic recipe in their generation
plan. Alternatives never create a new `origin_family_id`.

Selection on future outcomes **conditions the dataset even when the selection
metadata is hidden from the encoder**. Sampled rates are not natural game
probabilities. These collections make no claim of human representativeness or
identified causal effects. The API/CLI report always carries this warning.
Experiment labels, exact plans, intervention reasons, future tapes, event anchors
and inclusion information are audit metadata, never model inputs.

## Generation and bounded rejection

```python
from botbowl.lab.collections import CollectionSpecV1, CollectionReader, collect

spec = CollectionSpecV1.create(
    generation={"scenario": "pickup", "home_policy": "possession",
                "master_seed": 17, "max_decisions": 4},
    mode="selected", predicate="possession_gain-v1",
    target=2, max_episodes=8, max_decisions=32, before=2, after=2,
)
report = collect(spec, "/tmp/pickup-collection", attempt_limit=1)
report = collect(spec, "/tmp/pickup-collection")  # Resume the identical plan.
reader = CollectionReader("/tmp/pickup-collection")
reader.verify()
```

`generation` contains DATA-01 `JobConfig` fields except `output` and `episodes`.
The controller materializes a separate one-episode generation plan for each
attempt with a deterministic `episode_prefix + '-NNNN'`; all seeds and origin
identities follow the existing generator. Each success retains that complete
plan in DATA-07. Failed attempts retain its digest, episode/family IDs and
attempt ordinal; reconstruct their exact plan from the immutable spec and
ordinal with the same generator version. `records()` returns detached audit
data, including the generation summary, selection anchors and alternative
provenance. Nested input mutation cannot change an accepted spec.

The selection unit and target are **episodes**, not event occurrences or
windows. One episode with ten matching events counts as one acceptance.
`attempted = accepted + rejected + failed + pending`; failed generation is never
removed from the denominator. Stop with `insufficient_matches` and all counts
when either budget prevents another attempt. There is no forced-dice fallback.
The maximum is 1,000 attempts and 1,000,000 generation decisions. Each episode
also retains DATA-01's decision and automatic-step limits. `attempt_limit` is a
pause after new attempts, not a change to the design or target.

Before an attempt, reserve its full factual decision horizon and, for experimental
modes, its alternative horizon. An attempt is admitted only if that reservation
fits the remaining total budget. Successful attempts charge actual decisions;
failed/interrupted attempts conservatively charge the reservation because the
precise execution prefix may be unavailable. The report labels this accounting
explicitly. Replay/export/verification work is additional, separately bounded
work over the retained prefix; it is not another sampled episode. Zero-decision
prefixes remain bounded by the attempt cap. Infrastructure IO/memory failures
stop the process with the admission preserved; ordinary engine/policy/chance/
patch failures produce stable error-type diagnostics and consume an attempt.

## Closed predicates and causal windows

The versioned registry is deliberately small:

* `possession_gain-v1`: `SUCCESSFUL_PICKUP`, `SUCCESSFUL_CATCH`, or `INTERCEPTION`.
  These are reported acquisition events, not an inferred opponent-to-opponent
  turnover or a claim that every possible possession change is covered.
* `injury-v1`: `KNOCKED_OUT` or `CASUALTY`. The separate `INJURY_CASUALTY` roll and
  Apothecary reports do not add another casualty credit.
* `knockdown_injury-v1`: a `KNOCKED_DOWN` report followed by one of those injury
  reports within the same episode, branch, decision and player. This is an
  ordered event predicate, not causal identification.

`select_events` validates EventV1 sports records. Repeated identical event IDs
are idempotent; conflicting duplicates and reversed report order in a decision
are rejected. A new predicate requires package code and review.

For natural and selected collections, `iter_selected_windows(split_manifest=...)`
uses DATA-03 directly. For a matched decision its cutoff is **before** that
decision: `before + 1` pre-decision observation slots and `after + 1` future
post-decision target slots include the causing decision. Logical contexts,
availability checks, branch boundaries, initial/terminal masks and primary
input projection remain DATA-03's. Multiple events from the same decision share
one window. An uncaused setup event is retained as an audit anchor and creates
no fictitious decision window. Feed only `sample['inputs']` to an encoder.
Future observations remain targets; selection reasons remain metadata.

```python
from botbowl.lab.splits import build_split_manifest

# A one-split fixture, not a train/test evaluation protocol.
splits = build_split_manifest(reader.origin_sources(), proportions={"train": 1},
                              seed=17, split_version="example-v1").to_json()
for sample in reader.iter_selected_windows(split_manifest=splits):
    features = sample["inputs"]
```

`origin_sources()` includes factual episodes and alternative replays, including
rejected factual episodes kept for audit. Alternatives inherit the exact family
and a `parent_episode` relationship. Freeze and persist the returned DATA-04
split manifest using the existing split API. The window reader requires this
manifest and checks content identity. DATA-04 prevents family members from
crossing train/test. A growing collection needs a newly versioned split manifest.

## Forced and intervened continuations

Supply `chance=ChancePolicy('forced', tape=validated_tape).to_json()` for forced
mode. Tapes are exact SIM-06 die/event-context declarations, not positional dice
queues. Divergence or an unconsumed suffix fails the attempt; no independent
fallback is substituted. The declaration must be unused. Explicit independent
chance requires a seed; an omitted chance in intervened mode uses the attempt's
retained engine SeedSpec. Identical seeds are reproducible, not event coupling.

Supply `patch=<SIM-08-shaped JSON>` for intervened mode. Collection plans use
stable entity IDs: `home`/`away` for teams and `home:N`/`away:N` for players,
including a ball placement's carrier. These are bound to the initial snapshot's
engine IDs before invoking SIM-08 with guarded old values. Both the stable plan
and the exact resolved engine patch are retained. Only `synthetic` and
`unknown` reachability are admitted here; this V1 generation API supplies no
external `validated_recipe` witness. Use the editor directly for witness-based
experiments. The exact normalized patch, rules before/after, snapshot hashes,
author/reason and reachability remain in `lineage.json` and `intervention.json`.
Reproduction compares semantic final-state hashes and logical outcomes; physical
replay/snapshot checksums and resolved engine entity IDs can differ between runs.

Each alternative begins at the same untouched initial recipe snapshot as its
factual episode. The source snapshot is persisted and checked unchanged after
branch execution. Continuations reuse both recorded policy specifications,
including their seeds, and a declared decision horizon. Branch execution follows
the engine's terminal/truncation or branch horizon; a synthetic scenario's
exercise-success boundary only ends the factual scenario wrapper. This is an
explicit engine continuation beyond that exercise when the horizon permits it.

The experimental collection's factual DATA-02 episode stays in DATA-07; its
alternative stays in the verified ReplayV1 format produced by SIM-05. This API
does not flatten a branching tree into a linear DATA-02 episode or provide
experimental replay windows through `iter_selected_windows`. Read/seek the
alternative with `ReplayReader(attempt_directory, 'alternative')`. Its recorded
contexts are preserved. Keep the enclosing collection and lineage metadata with
the replay: raw engine snapshots alone do not attest experimental classification.

## Weights with a known finite design

Generated rejection collections report null inclusion probability and weight,
with an explicit reason. Frequencies from accepted events are never used to
estimate either value. Even natural generated prefixes can fail or stop and do
not establish a probability of inclusion in an unspecified game population.

`sample_finite_population` is the separate, checked design for known weights:
provide a complete finite list of `{unit_id, events}`, a registered predicate,
and rational Bernoulli probability `numerator / denominator`. It validates the
whole frame before sampling, visits every member, and independently samples
each eligible unit without target-count stopping. The result retains the frame
digest, seed, predicate, rational probability, total/eligible/accepted counts,
selected event IDs and inverse-probability weights. The weights apply **only to
predicate-positive units in that finite frame**. Noneligible units have zero
inclusion and the weights cannot recover statistics for the unconditioned frame
or natural game distribution. This design has no failed-generation slots;
generation failures belong in the collection audit, not in an invented finite
frame probability. Census (`1/1`) is a supported special case.

## Persistence and CLI

Each attempt has its own DATA-07 one-episode dataset, preserving the writer's
strict generation-plan contract even when earlier attempts failed. Collection
admission and result indexes use DATA-07's bounded JSON, safe paths, atomic
replacement, fsync and POSIX single-writer locking. The committed root points
to checksummed outcome records; all referenced successful artifacts exist before
that commit. On reopen, committed shards and alternative replays are verified.
The reference DATA-02 recordings are also retained as compact reproduction input;
DATA-07 is the collection's factual read interface.

A crash before admission starts nothing. A crash after admission but before
the result commit becomes one failed `InterruptedAttempt` on resume, charged
at the reserved horizon; it is never rerun or inferred successful from orphaned
files. A crash after the atomic commit retains the outcome. Orphan files are
not collection records and are not garbage-collected automatically. Concurrent
writers are rejected. Changing the spec, versions or identities is not resume.
As with DATA-07, this requires local POSIX filesystem semantics and trusted
immutable publication; checksums detect corruption, not producer authenticity.

```sh
python -m examples.lab.event_collections /tmp/collection-demo
python -m botbowl.lab.collections /tmp/collection-demo/spec.json /tmp/new-collection
```

The CLI opens/resumes the identical spec, prints the audit report, and exits 0
for complete/paused work, 3 for `insufficient_matches`, or 2 for rejected input
or IO failure. Large datasets, traces and snapshots remain outside Git.
