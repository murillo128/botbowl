# Causal windows (DATA-03)

`botbowl.lab.windows` builds passive or action-conditioned samples from a
confirmed DATA-02 `EpisodeReader`. It reads inert records, constructs no Game,
and opens only transitions, primary observations, and profile-authorized
observation channels. It does not read events, macro proposals/results, oracle
labels, privileged data, RNG state, or portable snapshots. There is no training,
normalizer fitting, reward inference, or evaluation metric in this API.

## API and cutoffs

`WindowSpecV1(history_length, horizon, ...)` records the complete sampling policy:

| Field | V1 meaning |
| --- | --- |
| `schema_version`, `data_version`, `availability_version` | Integer 1; unknown versions fail closed. Data is DATA-02 / EpisodeManifestV1. |
| `profile` | API-04 `InputProfile`, including its version and exact typed fields. Must be a subset of the recording's authorized profile. Defaults to primary. |
| `history_length` | Number of pre-decision observations in an input history. |
| `history_stride` | Separation of history entries in primitive decisions, default 1. |
| `stride` | Separation of cutoffs within each uninterrupted segment, default 1. |
| `horizon` | Number of consecutive future post-decision observations. |
| `unit` | `decisions`. `events`, `turns`, seconds, and other units are rejected: V1 defines no conversion or aggregation for them. |
| `mode` | `passive` (default) or `action_conditioned`. |
| `short_history`, `short_targets` | Independently `mask` (default) or `exclude`. |

All lengths/strides are positive integers; booleans are rejected. `to_json()`
and `from_json()` round-trip the spec with strict keys and versions.

A cutoff is the exact `before` context of an accepted primitive decision D.
Its `decision_seq` is D−1. History ends at D's **pre-observation**, never D's
post-observation. Earlier entries are the pre-observations of D−history_stride,
D−2×history_stride, and so on. V1 samples decision boundaries, not intermediate
snapshots or an assumed alternation of seats. A history is ordered oldest first.

Passive inputs contain only these projected observations and their presence
mask. Action-conditioned inputs add exactly `action`, the accepted `ActionV1`
selected at D, known after selection but before its consequences resolve. No
historical or future selected commands are implicitly included. ActionV1's
actor/entity references are part of its explicit semantic command contract;
they are not automatic observation features. Macro execution is sampled from
its actual child primitive transitions, including reroll, defender and budget
interruptions. A whole proposed route or macro outcome never enters its first
child's inputs.

Target slot zero is the post-state of D; slot one is the post-state of D+1,
etc. Targets project the **same explicit profile**. They are future public
observations, not rewards or evaluation/oracle labels. A resolved terminal
post-state is present. A pending transition's partial post-state is not a
resolved target: that slot and unavailable subsequent slots are absent. The
original status, end and source references remain audit metadata.

An absent slot is JSON `null` with `presence: false`. There are no synthetic
observations or numeric zero-filled futures. With `exclude`, any sample with
an absent slot on that side is omitted. Observations retain the profile's own
field-level Presence semantics in addition to the window's slot mask.

## Availability and validation

`availability_rules(spec)` publishes a closed versioned rule for **every**
selected feature path: ObservationV1/DerivedV1/ControlV1 fields become available
at their recorded capture context. ActionV1 becomes available at this cutoff's
selection, never at an earlier cutoff. All descendants of an authorized
observation inherit that source context. There is no rule allowing transition
outcomes, terminal reasons, next actor, future length, RNG, oracle, macro results
or arbitrary metadata into inputs. API-04 still rejects unknown feature paths
and requires explicit authorization for aids and action masks.

`validate_availability(field, available_at, cutoff, spec)` checks the closed
rule, matching episode/branch, and both logical decision and event counters.
The builder derives `available_at` from the actual source observation, joins
its exact pre/post reference to the transition, and requires their full contexts
to match. Authorized aid/mask rows must have the same ID and capture context as
the primary row. Thus placing a future record into an earlier history slot, or
relabeling its row index while retaining its actual context, fails validation.
The rule is independent of position within an array, including decisions with
no emitted events and consecutive choices by one actor.

As with DATA-02 and API-04, the producer owns truthful payloads and capture
contexts. This reader cannot detect a producer that fabricates an internally
consistent observation and all its provenance. A future estimate disguised as
a legal primary value with forged capture context requires source auditing;
merely attaching an earlier timestamp is not evidence that it was available.

`validate_window_source(reader, spec, split_manifest=...)` exhausts the same
stream, checks selected file hashes/schemas, references, availability, bounds,
actor/terminal consistency, continuity, and split membership, and returns the
number of eligible samples. It does not claim DATA-02's full event/scope/macro
validation. Use `read_episode()` separately when that full audit is required.
A generator consumer that stops early has validated only the consumed rows;
selected file bytes are hash-checked before their first row is yielded.

## Boundaries, origin and memory

A window never crosses a branch, episode, missing decision, or discontinuous
pre/post context. Each such boundary flushes pending targets according to policy
and starts a new history. Unowned scenario/clock work between decision contexts
also starts a segment conservatively. Foreign family/scenario metadata,
backwards time, inconsistent same-context public observations, and decisions
after an ended or pending transition are errors. No ancestor prefix is imported
across a fork. An episode with zero accepted decisions yields no windows.

Each returned JSON mapping separates:

- `inputs`: `observations`, `presence`, and only in conditioned mode `action`;
- `targets`: future `observations` and `presence`;
- `origin`, `split`: the exact DATA-04 membership shape;
- `metadata`: spec, availability rule, cutoff, branch, origin-component family,
  split version, observation IDs/availability contexts, projection/entity
  metadata, and source transition IDs, contexts, half-open event ranges,
  primitive parentage, status and end markers.

**Feed only `inputs` to a model.** Source identity, family, branch, split, entity
alignment metadata, terminal reason, and the number of future source records
are not automatic features. The copied `inputs` are invariant under valid
changes confined to the suffix after the cutoff. Changing the selected action
changes only the explicit action input in conditioned mode.

`split_manifest` is required. The source must already belong to a frozen
DATA-04 split, with matching episode/family and exact manifest content digest.
`source_id` defaults to the episode ID. Custom DATA-04 source identities can be
passed explicitly. Membership is inherited, never randomized per window or
reassigned when history, stride or horizon changes. Samples are directly
accepted by `validate_window_membership()` from `botbowl.lab.splits`.

`EpisodeReader.iter_channel(name)` authenticates one selected JSONL file in
64 KiB chunks, then yields schema-validated bounded lines. It also checks count
and digest on exhaustion. It holds one open descriptor per selected stream and
closes it on exhaustion or generator `close()`. No full-channel list is built.
Window reading requires primary and selected aid/mask rows in increasing,
complete observation-ID order; unordered or incomplete side channels are
rejected explicitly by the streaming join. This is a streaming-reader
precondition, not a change to DATA-02's general writer or full reader.

The builder retains at most `(history_length−1)×history_stride + horizon`
primitive frames, plus a constant number of joined records. Batching retains
at most `batch_size` copied samples. Memory also includes the caller's frozen
split inventory, manifest and individual bounded records; it does not scale
with the number of episode rows. Call `close()` on a partially consumed window
or batch generator. Each sample owns its nested containers, so editing one
sample cannot rewrite another or the stored trace.

## Two modes

Given an existing confirmed episode and its frozen split manifest:

```python
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.windows import WindowSpecV1, iter_windows, iter_window_batches

reader = EpisodeReader('/tmp/recordings', 'episode-17')
passive = WindowSpecV1(history_length=4, horizon=2, mode='passive')
for sample in iter_windows(reader, passive, split_manifest=splits):
    consume(sample['inputs'], sample['targets'])

conditioned = WindowSpecV1(history_length=4, horizon=2,
                          mode='action_conditioned', short_history='exclude')
for batch in iter_window_batches(reader, conditioned, split_manifest=splits,
                                 batch_size=16):
    # Each input adds only the command selected at its own cutoff.
    consume_batch([sample['inputs'] for sample in batch])
```

Run `python -m examples.lab.windows` for a temporary recorded episode and both
modes, without training. The demonstration freezes one source into a single
train split; real experiments supply their previously frozen DATA-04 inventory.
