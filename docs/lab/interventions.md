# Experimental interventions (SIM-08)

`apply_intervention(BranchSnapshot, spec)` returns an `InterventionResult` with
an independent `BranchSnapshot` and copied data provenance. `tree.intervene(point,
spec)` additionally registers that result in the existing `BranchTree`, enforcing
its parent ownership, duplicate-ID, depth and node limits. The canonical
`origin_family_id` is inherited. Standalone callers must supply a trusted
`BranchSnapshot`; raw engine snapshots contain no family attestation.

The [v1 JSON schema](interventions.schema.json) describes input shape. The Python
validator is authoritative for entity membership, exact old values, domains,
board geometry, pending procedures and executable witness validation. Only plain
JSON data is accepted; unknown keys, attributes, Python paths, imports, reducers,
expressions, callables and RNG fields are rejected. Reason and author are declared
text, not authenticated identity. Limits are 256 operations and 4 MiB of patch data.

## Supported edits and boundaries

Version 1 admits only regular `Turn` decisions between player activations, with
no active player, airborne piece, bomb or unresolved procedure context. Both
teams may be edited. Setup, kickoff blitz/quick-snap, active movement, rerolls,
pushes, interceptions, Apothecary and terminal states are rejected explicitly.
Their retained procedure references are not safely editable by this version.

Every operation supplies `entity`, `entity_id`, `field`, `old_value`, `new_value`.
Old values all refer to the parent state, with exact JSON types (true is not 1).
Duplicate targets are rejected. The complete transaction is applied privately;
a late collision or possession failure publishes nothing and leaves original
state, RNG, forced queues, timeline and undo history intact.

| Entity | Field | Domain |
| --- | --- | --- |
| `player` | `position` | `{x,y}` in playable interior; on-pitch players only |
| `player` | `extra_ma`, `extra_st`, `extra_ag`, `extra_av` | Integer −9..9 with base role statistic + extra in 1..10 |
| `team` | `rerolls`, `bribes`, `babes`, `apothecaries` | Integer 0..2³¹−1 on TeamState; experiment resources, not a natural-resource attestation |
| `ball`, ID `ball` | `placement` | `{position:{x,y}, carrier_id:player-id-or-null}` for the existing single ball |
| `configuration`, ID `rules` | `registered_config` | Old effective config digest; new `{config_id,version}` from the closed registry |

Player/team IDs are the engine IDs in the snapshot, not roster indices or the
semantic action codec's player IDs. Simultaneous swaps are supported. A carried
ball follows its existing carrier when that player moves; an explicit ball
operation overrides possession after all moves. The named carrier must occupy
the final square and be standing. The engine marks settled carried balls as
`on_ground=True`, so the editor retains that engine convention. A loose ball can
share a player's square without automatically running a pickup procedure. Dugout
transfers, role/skill changes, score edits and resource side effects are outside v1.

The versioned configuration registry in `botbowl.lab.rules` contains
`pathfinding-disabled` and `pathfinding-enabled`, version **1**. These copy the
parent's effective configuration and set only `pathfinding_enabled`; they retain
its geometry, ruleset, formations and other options. A changed configuration
receives a newly derived CORE-03 descriptor, including its effective digest and
config identity. Unknown registry IDs/versions and arbitrary config paths fail.
New variants require trusted code and review; this is not a general rules editor.

After edits the engine rebuilds legal actions. The editor does not execute an
action, add a no-op choice, advance logical counters, or consume RNG. The result
has a fresh undo origin through the snapshot API. Source undo history is retained.

## Provenance and reachability

The result's provenance includes the complete normalized patch, editor version,
reason/author within the patch, inherited family, parent snapshot and branch,
parent logical context, effective rules before/after and hashes. `pre_hash` is the
parent snapshot; `edited_hash` is the complete edited state before assigning the
new branch context; `post_hash` is the actual returned/persisted branch snapshot.
Forking changes timeline alias relationships, so these last two hashes can differ
although no decision or RNG was consumed. Provenance is inert metadata, not a
cryptographic authorship certificate.

`reachability` defaults to **synthetic**. **unknown** may be requested when origin
reachability is undetermined. Structural validity never establishes natural
reachability. **validated_recipe** requires `recipe: {actions: [ActionV1, ...]}`
and a separate trusted `recipe_start=BranchSnapshot` in the same family/episode.
The editor clones that start, executes 1..256 legal actions using the existing
ActionControl and engine (100,000 automatic steps per action), then requires the
complete semantic snapshot hash to equal `edited_hash`. It retains the start
snapshot ID/hash, actions and resulting hash as witness evidence. The tree API
also requires that start reference to be registered in the same tree.

This is a conservative witness of reachability **from the declared start**, not
proof that the start was naturally reached from game initialization. A board-only
match, user-supplied success label, empty sequence or borrowed witness fails. Most
position/resource patches cannot reproduce their unchanged history and RNG via a
legal sequence and therefore remain synthetic. A guard that retains an already
reached value can be verified using its actual legal prefix. This does not make
such a guard a legal no-op action or change the branch's `intervened` kind.

No continuation chance mode is chosen by editing: `continuation_chance` is null.
Fork the registered result with a SIM-06 `ChancePolicy` and declared continuation
policy/horizon to execute it; the default fork mode is independent. Equal seeds
are not evidence of matched events or removal of confounders. These APIs do not
estimate causal effects. SIM-08 remains optional for the first M0–M1 laboratory.

## API and CLI

```python
point = tree.root_snapshot
result = tree.intervene(point, {
    'schema_version': 1, 'branch_id': 'extra-reroll', 'snapshot_id': 'extra-reroll-0',
    'reason': 'Sensitivity to one extra team reroll', 'author': 'experiment-owner',
    'operations': [{
        'entity': 'team', 'entity_id': team_id, 'field': 'rerolls',
        'old_value': 2, 'new_value': 3,
    }],
})
result.write('/tmp/edited.json')  # SIM-03 atomic file with complete provenance
# result.snapshot is a registered point accepted by tree.fork(...).
```

The CLI consumes a SIM-03 snapshot file whose `provenance` includes
`snapshot_id`, `branch_id`, and `origin_family_id`. When first saving an existing
experiment's raw snapshot, provide these known IDs to `write_snapshot(...,
provenance=...)`; do not invent a new family for a derived state. Editor output
already contains the required IDs and can be edited again.

```sh
python -m examples.lab.interventions /tmp/intervention-demo
python -m botbowl.lab.interventions /tmp/intervention-demo/source.json \
  /tmp/intervention-demo/patch.json /tmp/intervention-demo/edited.json
# Optional exact legal witness:
python -m botbowl.lab.interventions source.json witnessed-patch.json edited.json \
  --recipe-start earlier-snapshot.json
```

CLI failure exits 2 and preserves any previous output file. Restoring executable
state uses `read_snapshot` / `clone_from_snapshot`; retain the enclosing provenance
when reopening a tree with `kind='intervened'`. Raw engine state alone does not
carry classification metadata. The tests cover both sides, sizes 1/3/5, forward
model on/off, new-process CLI persistence, pending decisions and failed recipes.
