# Executable replays (ReplayV1)

`botbowl.lab.replays` records and replays externally supplied semantic actions.
It never calls a policy. ReplayV1 stores the initial executable snapshot, the
accepted `ActionV1` decisions and their logical events, semantic pre/post state
hashes, and periodic executable checkpoints. It is distinct from the old
pickle-backed `botbowl.Replay`, whose frames remain a visual compatibility mode.
`ReplayRecorder` provides the reusable `Recorder` operations `append`, `flush`,
and `close`; `advance` is the result-returning convenience used below.

```python
from botbowl.lab.replays import ReplayReader, ReplayRecorder

recorder = ReplayRecorder(game, root, "match-17", replay_id="match-17",
                          origin_family_id="experiment-4",
                          checkpoint_interval=64)
while not game.state.game_over:
    legal = recorder.actions.legal_actions()
    recorder.advance(recorder.actions.request(legal.actions[0]))
manifest = recorder.close()

reader = ReplayReader(root, "match-17")
game_at_90 = reader.seek_decision(90)
branch = reader.resume_at_decision(90, branch_id="alternative-90")
```

The complete bounded example is
[`examples/lab/replays.py`](../../examples/lab/replays.py). Construct the
recorder before the first game action. A fresh externally controlled game may
already be initialized and waiting for `START_GAME`; the recorder installs
`LogicalTime` before clocks exist. An already-running real-clock game is rejected
because elapsed wall time is not a reproducible decision input.

## Format and atomicity

A confirmed replay is one directory with `manifest.json`, `blocks/*.json`, and
`checkpoints/*.snapshot.json`. The manifest has exact `format="ReplayV1"` and
integer `version=1`, the rules descriptor, replay and `origin_family_id`
identities, optional origin reference, recorded checkpoint interval, initial and
final timeline contexts, terminal/truncation reason, file digests, and the four
indexes. Unknown members and versions fail closed.

Each `ReplayBlockV1` contains at most `checkpoint_interval` resolved decisions.
A decision retains its actor, semantic action, full `DecisionEnvelope`, events,
and persistent-codec `pre_hash`/`post_hash`. Block-local decision, event and turn
indexes are summarized in the manifest by half-open ranges. The checkpoint
index names a `SnapshotFileV1` at the start and end of every confirmed block.
Checkpoint provenance records replay/family/origin and the matching logical
decision and event counters.

Snapshots and JSON blocks are written to temporary siblings, flushed, and
atomically replaced. Recording occurs under a visible `.partial` directory;
only a completely synced manifest is atomically renamed to the requested final
directory. A write or engine failure leaves partial evidence and cannot be
opened as executable replay. Existing final directories are never overwritten.

## Seeking, events, and divergence

`replay_all()` executes every decision from the initial snapshot and verifies
all checkpoint boundaries. Use it for a complete integrity/fidelity audit after
transport or storage. `seek_decision(k)` validates the nearest checkpoint at or before `k`, then reads
and executes only the blocks needed for the remaining span. `last_seek_replayed`
and `last_seek_blocks` expose the work for diagnostics/tests. Every action is
decoded against the current semantic choice set and sent directly to
`Game.advance`; no policy, visual frame, or saved engine `Action` is used.
Pre/post semantic hashes, exact events, and logical contexts are verified after
each decision. `ReplayDivergenceError.decision_seq` identifies the first affected
decision. Missing/repeated decisions or events, file truncation, digest errors,
invalid indexes, unknown versions, snapshot incompatibility, and unrecorded
direct game advances are errors; readers never repair expectations.
Seeking validates the selected checkpoint and block span; it does not certify
unread blocks elsewhere in the recording. Reader failures expose `decision_seq`
(the first affected decision where identifiable, or the starting boundary of an
unreadable block/checkpoint; a malformed manifest uses zero).

`seek_event(e)` uses the event index and returns `IndexedReplayEvent`: the copied
event/context, `previous_decision`, `next_decision`, and the game restored at the
previous executable boundary. Sporting events emitted during automatic
consequences are therefore inspectable without claiming that the simulator can
resume in the middle of a procedure. The turn index is available as
`ReplayReader.turn_index`; entries retain branch, half, round, logical team-turn,
and decision/event ranges.
Event lookup verifies the causing decision on a private clone before returning
the event; the returned game stays at `previous_decision`.

## Resume, ancestry, and data separation

`resume_at_decision` first performs a verified seek, creates a new independent
game, and forks its preserved timeline. It returns `ReplayContinuation` with an
origin reference to the factual replay. Passing that object to a new
`ReplayRecorder` preserves `origin_family_id` and records the origin; it never
modifies or overwrites the source trajectory.

Checkpoint files contain privileged engine/RNG state. They are not observations,
primary inputs, authorized training channels, or visual frames. Callers must keep
them outside model input paths.

The recorder consumes `ActionV1`, `ActionRequestV1`, or a currently legal engine
action through `append`/`advance`. It records resolved decision envelopes and
reuses DATA-02's `TransitionV1` and `EventV1` domains for validation. It owns its
timeline; do not attach an `EpisodeRecorder` to the same game. When the source
is a DATA-02 recording, supply its `source_family` as `origin_family_id`.
It captures engine scope through the SIM-03 codec, which preserves the timeline
and roster-ID binding natively; policy/scenario adapters are not invoked during
action playback. Unsettled actions after operational budget exhaustion leave a
failed partial recording. Use a new settled snapshot for another recording.

`flush()` seals a shorter block and its checkpoint while keeping the directory
partial. `close(truncation_reason="...")` confirms a nonterminal prefix; terminal
recordings use `close()`. `abort()` leaves partial evidence without confirming it.
Each JSON manifest/block is bounded by DATA-02's 4 MiB record limit; checkpoints
use configurable `SnapshotLimits` (32 MiB by default). Full timeline prefixes
inside checkpoints retain the snapshot codec's own resource limits. Recorded
logical time is fixed while actions run: externally advancing its clock or RNG
between decisions is an unrecorded state change and fails continuity checks.

Legacy `.rep` pickle data has neither complete snapshots nor reproducible RNG and
is never accepted by `ReplayReader`. `open_legacy_replay(source, trusted=True)`
is an explicit trusted-data escape hatch returning `LegacyReplayView`, which has
navigation methods and `executable=False` but no resume operation. Never set
`trusted=True` for untrusted pickle data.
