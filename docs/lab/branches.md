# Simulation branches and shadow futures (SIM-05)

`botbowl.lab.branches` composes engine snapshots, `SimulationSession`, SIM-06
chance policies and `ReplayV1`. A `BranchTree` owns one classified origin, a bounded
tree of independent sessions, and separately stored external predictions.
It does not execute learned models, parse natural language, edit arbitrary
engine state, optimize policies, or infer causal effects.

```python
from botbowl.lab.branches import BranchSpec, BranchTree

# factual is an initialized SimulationSession at a settled decision boundary.
tree = BranchTree(factual.snapshot(), kind='observed', origin_family_id='experiment-17',
                  max_nodes=64, max_depth=8, max_decisions=100)
point = tree.root_snapshot
branch = tree.fork(point, BranchSpec(
    branch_id='alternative', parent_branch_id=point.branch_id,
    parent_snapshot_id=point.snapshot_id,
    policy={'policy_id': 'first-legal', 'version': 'v1'}, horizon=2,
    initial_action=factual.legal_actions().actions[0],
))
branch.run(lambda observation, legal: legal.actions[0],
           policy_id='first-legal', version='v1')
branch.export_replay('/tmp/experiment-17', 'alternative', replay_id='alternative')
lineage = tree.export()
tree.close()
```

The CPU demo is `python -m examples.lab.branches /tmp/branches-demo`. It exports
two coin-toss alternatives with distinct retained seeds, a prediction and a later
factual observation, plus executable replays. No artwork or accelerator is needed.
The tests also compare two actual GFI dice actions and restore a branch replay
in a fresh process.

## Ownership, lineage and budgets

`BranchTree` accepts a trusted engine `Snapshot` or `SessionSnapshot`. Declare
the root's `kind` explicitly: `observed`, `simulated_alternative`, or `intervened`.
Omitting the classification is an error, including for an empty trajectory.
The existing timeline branch ID is retained. Supply the
existing experiment/recording's canonical `origin_family_id` when starting from
a raw snapshot, which carries no independent family claim. The controller is
responsible for that initial identity. `BranchTree.from_replay(reader, decision, kind=...)`
performs a verified seek and inherits the replay's family automatically.
Names, alternative actions, policies, seeds and views never create another family.

Restoration does not establish factuality. Carry `kind`, `intervention` and
`assumptions` from the source tree's exported node when reopening a branch:

```python
source_node = lineage['nodes'][1]
restored = BranchTree.from_replay(
    reader, decision_seq,
    **{key: source_node[key] for key in ('kind', 'intervention', 'assumptions')},
)
```

The root copies that classification and retains restored `chance_result`
metadata (null if no chance policy is installed). Intervened roots require a
nonempty intervention object. ReplayV1 and raw engine snapshots do not contain
the full SIM-05 intervention/assumption declaration, so the caller must retain
it alongside those artifacts; this API does not guess missing provenance.
Known fabricated or matched chance, forked timeline history and derived replay
origins are rejected when declared `observed`. These checks are conservative:
an empty fork can have no distinguishing history, and an arbitrary intervention
can leave no chance marker. The explicit declaration remains trusted controller
input, not an attestation inferred from RNG state or a branch name.

`fork(point, spec)` requires the actual registered `BranchSnapshot` reference,
a matching parent branch/snapshot ID, a new branch ID and a declared policy
identity/version. `branch.snapshot(new_id)` registers a descendant boundary;
`tree.observe(factual.snapshot(), snapshot_id=...)` retains a later factual
boundary only on an observed root and applies the same factuality checks.
IDs are unique within their namespaces; prediction and branch IDs
cannot collide. Parent references always point backward to known branches,
so self-parenting, ancestor cycles, unknown parents and duplicate IDs fail.
Snapshot references are privileged and never appear in policy inputs.

The horizon counts primitive accepted decisions, including the optional initial
action. Zero means an empty prefix. Each branch gets a fresh session budget at
its divergence boundary; the tree also limits total attempted engine decisions
across siblings. Invalid actions and stale revisions consume no budget. Engine
failures consume an attempt and are retained as failed nodes. Node limits count
the root, simulated/intervened branches and prediction revisions. Depth
counts simulated edges from root depth zero. `max_steps` bounds automatic engine
work per decision. A horizon beyond the remaining total budget is rejected;
concurrent open branches share that total budget without reserving it at fork.

Only legal actions are accepted. An illegal initial action leaves no tree node.
An execution failure after a valid initial action retains a failed node and
closes that session. Policy driver failures likewise fail only that branch.
`run(driver, policy_id=..., version=...)` checks the declared identity before
calling an explicitly supplied driver with copied session observations and
semantic legal actions. This is a trusted local callback contract, not a Python
sandbox or an assertion that a supplied function really implements its label.
The caller owns any policy RNG/resources and must supply independent drivers
when policies retain mutable state. No policies are imported from JSON.

Every simulator session owns its RNG, dice queues/tapes/cursors, reports,
timeline, engine graph and caches. `close()` is administrative and idempotent;
it does not advance a game or validate a chance prefix. `finish()` explicitly
validates leftover replay draws/matches and prevents further actions. Reaching
the horizon or a natural end calls `finish()` automatically. Early `finish()`
can seal a shorter prefix, which retains its declared horizon and actual length.
Failed/closed nodes remain inspectable; a failed branch cannot publish a complete
replay or a new snapshot. Closing a tree closes its branches without closing the
source factual session.

## Chance and intervention meaning

`BranchSpec.chance` accepts SIM-06 `ChancePolicy`: `independent` (default),
`replay`, `forced`, or `matched`. Default continuations select and retain a fresh
engine seed. Supply distinct engine `SeedSpec` recipes for reproducible worker
ordering. Equal seeds reproduce streams; they do not establish event matching.
The [chance contract](chance.md) owns tape/context validation and the bounded GFI
matching rules. Failed chance validation never consumes a sibling's randomness.

Kinds are `observed`, `simulated_alternative`, `predicted`, and `intervened`.
An intervened branch requires a nonempty `intervention` provenance object;
`assumptions` is a separate list of text. This describes the experiment. Arbitrary
state patches are not applied here: their validated editor belongs to SIM-07
(#63). Applied forced chance can already be represented and remains fabricated
in `chance_result.natural`. A trajectory computed earlier is not thereby a
causal counterfactual. Predictive output is also not an engine state.

## PredictionV1 and revisions

`tree.import_prediction(data)` validates and stores immutable JSON bytes. Reads
return fresh nested data; later observations cannot overwrite it. Required fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `kind` | Integer `1`, literal `predicted` |
| `prediction_id`, `origin_family_id`, `branch_id` | Unique record ID and inherited source identity |
| `parent_snapshot_id` | Registered emission boundary |
| `model` | `{model_id, version}`, supplied external model identity |
| `issued_at` | Exact API-02 context of that registered boundary |
| `available_history` | `{through, references}`: context no later than emission, plus public history IDs |
| `horizon` | Nonnegative number of future decisions; zero is valid |
| `output` | Arbitrary bounded, finite JSON prediction payload, including null |
| `metadata` | JSON object with external annotations |
| `revision_of` | Null for original; existing prediction ID for metadata correction |

All fields are required. Null is distinct from absence and zero. Unknown fields
and versions, nonfinite numbers, executable objects, future history contexts,
family mismatches, unknown emission boundaries and duplicate IDs are rejected.
JSON reuses DATA-02's 4 MiB record, 40-level depth and collection/node limits.
These are declared imported facts, not cryptographic evidence of emission time
or model execution. History references identify external input artifacts; this
module does not load or certify those artifacts.

`revise_prediction(id, revision_id=..., metadata=...)` appends a linked record.
Only the ID, revision link and metadata may change. The original model, output,
emission time, available history and horizon remain intact in every revision.
Changed model output is a new prediction at a registered emission boundary.
Predictions and their JSON are rejected by tree forks and
`SimulationSession.from_snapshot()`. The latter restores an owned session from
`SessionSnapshot` without constructing/resetting a throwaway match.

## Export and executable restoration

`tree.export()` returns detached bounded JSON with `format: BranchTreeV1` and
integer `schema_version: 1`. It is an inert export, not an importable executable
tree. Consumers must reject unknown versions. It contains:

- `origin_family_id`, configured `limits`, and total `attempted_decisions`;
- `branches`: DATA-02's exact `{branch_id, parent}` rows in parent-first order,
  with a null root parent and API-02 parent contexts for children;
- `nodes`: kinds, depths, divergence context, initial action, policy, horizon,
  intervention, assumptions, chance declaration/result, status, decision counts,
  diagnostic, latest result and optional replay reference. The root retains its
  declared kind/intervention/assumptions and restored chance metadata;
- `snapshots`: IDs, owning branches/family, logical contexts, persistent semantic
  hashes and optional factual replay origin references;
- `predictions`: complete immutable PredictionV1 records, separate from the
  simulator branch rows;
- `results`: simulated branch ID/kind/family/status, chance provenance, latest result and replay
  reference. An empty prefix has a null result, not an invented decision.

The `branches` projection uses the DATA-02 lineage shape; the whole branching
tree is not a linear `EpisodeManifestV1`. DATA-03 can group the same
`origin_family_id` without remapping or becoming a dependency of branch creation.
This export includes privileged chance provenance, potentially including future
tapes. Keep it outside policy/model input channels. Observations passed to a
continuation driver contain none of the tree, predictions, RNG or future tape.

`branch.export_replay(destination, relative_path, replay_id=...)` re-executes the
retained accepted actions on an owned clone of its initial snapshot through
`ReplayRecorder`, checks every resulting persistent state hash, then confirms
its atomic ReplayV1 directory. It never invokes the policy again. Failure leaves
partial replay evidence, never a confirmed divergent result. Source replay
references are retained when starting with `from_replay`; snapshot-only origins
are traced by the tree's snapshot IDs/hashes. Existing destinations are never
overwritten. Replay checkpoints use SnapshotFileV1, so `ReplayReader.replay_all()`
and `seek_decision()` restore branches in another compatible process with their
RNG/timeline intact. The inert tree JSON alone cannot restore executable state.
