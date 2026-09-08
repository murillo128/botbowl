# SnapshotFileV1: executable JSON snapshots (SIM-03)

`botbowl.lab.snapshot_io` persists the owned executable graph defined by
[SIM-02](snapshots.md). It supports the declared partial BB2016 implementation,
sizes 1/3/5/7/11, and the same engine, schema, NumPy and actual pathfinding backend
versions. It does not reconstruct a game from presentation JSON or modify rules.

```python
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot, restore_snapshot
from botbowl.lab.snapshot_io import read_snapshot, write_snapshot, SnapshotLimits

write_snapshot("game.snapshot.json", capture_snapshot(game))
# In another interpreter with the same installed implementation:
saved = read_snapshot("game.snapshot.json")
branch = clone_from_snapshot(saved)
restore_snapshot(game, saved)  # atomic replacement of a compatible idle target
```

`write_snapshot(path, snapshot, *, adapters=None, limits=SnapshotLimits(),
provenance=None)` returns a data-only `SnapshotFileV1` envelope.
`read_snapshot(path, *, adapters=None, limits=SnapshotLimits())` returns a private
process-local SIM-02 `Snapshot`. Pass the same trusted adapter registry to read,
clone and restore for episode scope. The two-process executable example is
[`examples/lab/snapshot_files.py`](../../examples/lab/snapshot_files.py).

## Wire grammar and registry

Files are UTF-8 JSON objects with exactly these members. Writers emit compact
ASCII-escaped JSON (a UTF-8 subset), sorted object keys and a final newline.
Readers also accept equivalent UTF-8 JSON whitespace and object-member ordering;
duplicate JSON keys are rejected.

| Member | V1 representation |
| --- | --- |
| `format`, `version` | `"SnapshotFileV1"`, integer `1` (boolean is invalid) |
| `descriptor` | Seven-field `RulesDescriptor` from #30, describing the snapshot's effective loaded resources/rosters; the original episode manifest remains in episode data |
| `scope` | `"engine"` or `"episode"` |
| `component_versions` | `graph=1`, `snapshot=1`, `rng="MT19937-v1"`, exact NumPy/package/backend identities, schema digest and used adapter names mapped to `1` |
| `payload` | Exactly `roots` and `nodes` |
| `provenance` | Optional caller-supplied JSON object, default `{}`; inert and noncausal |
| `semantic_state_hash` | SHA-256 of the normalized executable projection described below |
| `payload_digest` | SHA-256 of canonical JSON for **every envelope member except this digest**, including provenance and the semantic hash |

`roots` has exactly `game`, `episode` and `components`. Game is a `Game` node
reference. Episode is null or a dictionary of the exact SIM-02 episode fields.
Components is a dictionary of `policy-home`, `policy-away`, `scenario`,
`agent-home`, `agent-away` entries; only present active components are stored.

Atoms are JSON null/boolean/integer/finite number/string, `{"ref": id}`,
`{"enum": [tag, member_name]}`, or `{"bytes": "lowercase hex"}`.
Every reference is an integer index into `nodes`; nodes have unique contiguous
`id` values equal to their array position. Every node must be root-reachable.
Reassigning IDs consistently does not change the semantic hash.

| Node tags | Members beyond `id`, `type` |
| --- | --- |
| `list`, `tuple`, `set`, `frozenset` | `items`: atoms |
| `dict` | `items`: ordered `[key_atom, value_atom]` pairs; duplicate keys rejected, including `true`/`1` collisions |
| `rlist`, `rdict`, `rset` | Same container data plus `trajectory`: null or reference |
| `array` | `dtype`, `shape`, flat `items`; numeric, boolean, Unicode, bytes or object elements; object arrays retain references and cycles |
| `rng` | `algorithm="MT19937"`, 624 uint32 `keys`, `position` in 0..624, integer `has_gauss` in 0..1, finite `cached_gaussian` |
| `dice` | Shared `rng` reference, nested `queues` in D3/D6/D8/BBDie order, one boolean `strict` per frame |
| Registered object tags | `fields`: ordered `[field_name, atom]` pairs; no unknown/duplicate fields |

The trusted registry is closed in package code. `CODEC_FIELDS` exposes the
field inventory and `CODEC_SCHEMA` exposes every field's required domain;
[`snapshot_schema.py`](../../botbowl/lab/snapshot_schema.py) owns explicit local
contracts composed over trusted inheritance. No missing contract gets a default.
The schema digest covers these domains, presence rules, episode fields, enum
members and the work/hash algorithm versions; the [codec report](../reports/snapshot-files-inventory.md)
lists the object/enum/procedure coverage. Tags are opaque names. No tag is
resolved through an import, module attribute lookup, reducer, arbitrary class
constructor, callable, pickle, joblib or jsonpickle supplied by the file.
Strings resembling module names are ordinary data. Unknown reachable types,
procedures, enums, fields and unavailable adapters fail explicitly.

Every field is required, including nullable fields, except the five explicit
presence rules: optional `Game.seed`; `Touchback.players_on_pitch_standing`,
`HighKick.available_players` and `EatThrall.victim_pos` exist exactly when the
procedure has started; `Loner.result` exists only after the completed failed
Loner declines its retry. Constructor-created reversible fields and immutable
`__setattr__ = null` sentinels are required. Missing `Reroll.can_use_team_reroll`
is invalid even if both hashes have been recomputed. Scalar constraints distinguish
booleans from integers, constrain counters, die faces, seed namespaces and known
enum domains, and recursively constrain collection items and record references.
`Procedure.context` is the explicit generic registered-graph extension slot;
component payloads remain adapter-owned data with the SIM-02 engine-reference
restriction. Neither slot bypasses validation of reachable registered objects.

`ActionChoice.rolls` stores target thresholds. Let `S(T)` mean an ordered
list/rlist/tuple of T, `D` an exact integer 2..6, `F` 2..12 and `A` 1..12.
Empty outer sequences are valid for every action. Nonempty targets use:

| Action family | Target contract |
| --- | --- |
| MOVE, STAND_UP, LEAP, PICKUP_TEAM_MATE, BLOCK, PASS, THROW_TEAM_MATE, THROW_BOMB, HYPNOTIC_GAZE, SELECT_PLAYER | `S(S(D))`, including empty inner rows |
| HANDOFF | `S(S(D))` or flat `S(D)` |
| FOUL | `S(S(F))` or flat `S(F)` |
| STAB | Ordered nonempty rows of zero or more D targets followed by one A armor target |
| All other ActionTypes | Empty only |

Mixed flat/nested shapes and noninteger leaves (including bool and 2.0) reject.
No target is clamped, reshaped or inferred from live modifiers during validation.
Initial STAKES Stab currently adds one, while Frenzy Stab subtracts one; the
armor domain retains both producers' union, including 1 and 12. Block counts
are exact integers in {-3, -2, 1, 2, 3}, and nonempty counts require BLOCK.
Saved paths stay empty under SIM-02, including the flat pathfinder HANDOFF/FOUL
targets; suspended steps and private choice rebuilding retain their contract.

Procedure fields include inherited continuation state, including popped procedures
still reached by reroll/context cycles and suspended route `steps`. All current
engine procedure classes are registered, and a test compares the closed inventory
against the engine. This is codec coverage; the executable acceptance fixtures
exercise specified decision boundaries, not every possible combination of skills.

## Ownership, scopes and rebuilding

The graph preserves player/team/board/ball/report aliases, procedure-to-Game
references, mutable cycles, tuples/frozensets, scalar/object arrays, all owned
MT streams and nested forced queues. Cycles requiring an unfinished immutable
tuple/frozenset are rejected, as in SIM-02. Engine and procedure `init`, clock
constructors, action execution and source/global random draws are never used to
rebuild state. Trusted RNG reconstruction uses `RandomState(0)` plus `set_state`;
SIM-02 also constructs a private `DiceSource(0)`. Neither consumes the restored
stream or global randomness.

Saved force frames become owned data with fresh private tokens. A read cannot
resume a Python context manager from another process; restoring into unrelated
live forced contexts is rejected by SIM-02. Two reads share no mutable state.

Trajectory logs and their functions are excluded; an empty owned trajectory
retains the enabled flag and undo starts at zero. Arena JSON, square shortcuts,
entity indexes and path caches follow SIM-02 rebuilding. The saved snapshot has
empty derived paths; clone/restore recomputes executable choices privately.
Suspended `MoveAction.steps` survives until its child decision resolves.
Logical clocks retain elapsed/running/paused state; no wall deadline resumes.
Transport revisions remain owned by the caller, as in SIM-02.
Live rule traces are rejected at the snapshot boundary and are never wire data;
file loads initialize the private decoded game's transient `rule_trace` slot to
`None` before compatibility checks and detached cloning.

Engine scope contains inert seat metadata and external-action continuation.
For an `EpisodeContext`, its four other streams, budgets, seed recipe, initial
inputs/rosters and observation bindings survive; active callbacks are omitted.
Episode scope also retains every registered active component as `ComponentState`
data. Adapter functions are caller-provided trusted code, never file contents.
Names are implementation/version identities: register a new name when an adapter's
data contract changes. Nested wrappers use the existing `SnapshotAdapters` API;
shared components and retained supplied-stream aliases survive. Every decode
constructs private components. Adapter callbacks must honor the SIM-02 no-I/O,
no-live-mutation contract. Component data cannot reference engine objects.

## Integrity and semantic identity

`payload_digest` covers all parsed file data except itself. Whitespace, equivalent
JSON escapes and object-key order are not data and do not affect the digest.
It detects accidental corruption, **not authentication**; an attacker can recompute
it. Every structural/compatibility check still runs for a correctly re-signed file.

`semantic_state_hash` uses exact canonical labeling of the projected typed graph.
Equality means graph isomorphism preserving named roots, codec/scalar types,
field and ordered-sequence edge labels, multiplicity, aliases and cycles. Mapping
entries are unordered key/value associations; set/rset/frozenset membership is
unordered. Distinct equal-field Procedure identities remain distinct, including
identity keys and tuple/frozenset composites. An outside ordered anchor can
distinguish associations; swapping wholly interchangeable identities cannot.
Team/player/seat IDs normalize to side/roster slots. It excludes
Game local ID and wall audit timestamps, clock audit `started_at`, configuration
name/arena path hints (loaded geometry is retained), timeline episode/branch labels,
the episode provenance manifest, and envelope provenance. Logical time, procedure
fields, counters, reports, resources, loaded rules/configuration, forced queues,
RNG position/cache and opaque component state remain causal. Changing RNG or
replacing one shared list by two equal independent lists changes this hash.
Opaque adapter literals are not interpreted as entity IDs. A container shared
with engine data has normal/opaque internal views linked to one common object
identity, preserving both projections and their cross-view alias independently
of discovery order. No additional wire nodes or adapter format are introduced.
A seed recipe is retained
because it affects episode reset. The hash does not assert arbitrary policy
equivalence or authenticate a simulation.

The internal semantic algorithm stamp is 3, key-work stamp is 1 and corrected
resource-limit stamp is 1; all contribute to the schema digest. Files carrying
the earlier resource policy identity are incompatible rather than migrated. The
envelope, graph and SIM-02 versions remain 1. Canonicalization starts from
exact scalar/codec colors and refines labeled incoming/outgoing neighbor
multisets. Remaining ties use bounded individualization and complete enumeration.
Only the lexicographically least whole candidate encoding is hashed. No digest,
wire order, object address or first-encounter order resolves a structural tie.
Consistent node renumbering, field/unordered permutations and fresh hash seeds
preserve semantic identity. Cryptographic SHA-256 collisions are outside this
equality guarantee; no intermediate fingerprint collision is assumed impossible.

## Validation, bounds and failures

Defaults: 32 MiB file bytes, 100,000 graph nodes, depth 128 and 1,000,000
work units (`max_work`). `SnapshotLimits`
accepts positive integer overrides; depth has a hard implementation ceiling of
256. The reader reads at most `max_bytes + 1`. A quote-aware scanner bounds JSON
nesting before parsing. Parsed data values are additionally limited to
`64 * max_nodes`. Reference traversal depth is bounded independently of JSON
nesting. It is the largest weighted path through the wire graph's SCC
condensation, where each SCC weight is its vertex count. One iterative Tarjan
traversal from the named roots computes SCCs, reachability and depth while
preserving the separate rejection of references to unfinished immutable
containers. Shared suffixes, cycles and root/adjacency order therefore cannot
change the result. All array allocations together
are bounded by `max_bytes`; each dimension is bounded, rank is at most 32,
shape products must match flat lengths, dtype widths are restricted and numeric
overflow/string truncation is rejected before array allocation.

A shared work ledger bounds atom/edge visits, domain checks, key identity checks
and canonical graph construction, sorting, refinement rounds, search branches,
complete candidate encoding/comparison and variable-sized scalar processing.
Temporary incidence records, normal/opaque views and search buffers also count
against the byte/work budgets. Topology prep reserves
`4096 + 512V + 128E + 256U` bytes, where V is wire vertices, E is non-root
reference occurrences and U is named root slots. Its fixed work prepayment is
`6V + E + 2U + 32`; the validator reuses the adjacency/counts it already built.
Standalone semantic hashing safely counts and builds its own adjacency instead.
Read/write reuse one operation-local successful immutable-key computation while
charging its materialization reserve a second time as before; no result is
trusted across payloads, mutations, limits or public operations. Topology
buffers are released before canonicalization, leaving one charged 256-byte
summary.

Canonical workspace admission uses `S + max(T)`: S is monotone retained storage
and T is the greatest complete scratch request, including scalar escaping, color
assembly, refinement, candidates and comparison. Neither is reset or refunded.
The retained one-million-character boundary requires 18,655,071 bytes, including
the summary, identically under equivalent field orders. At 12,300,000 and at one
byte below that sufficient threshold every entrypoint rejects before
materialization. Search uses an explicit stack and retains one
best complete encoding, never all candidate encodings. Work accounting is
invariant across equivalent wire permutations; a limit rejection never returns
a best-so-far hash. Large symmetry can exhaust the budget while ordinary
fixtures and two/three-object symmetric cases remain supported at defaults.
Immutable keys use memoized graph identities with shallow integer child tokens;
validation never constructs recursively nested Python tuple/frozenset keys.
A separate memoized cost recurrence counts repeated immutable child references
with multiplicity, saturates at the work limit, and reserves eight expanded
traversals per immutable node for Python key hashing/equality during decoding
and SIM-02 copies. Eight ordinary atom traversals are also prepaid before object
construction. Semantic hashing finishes against the same ledger before decoding.
This is a conservative operation budget, not a wall-clock deadline or a budget
for trusted adapter callbacks. Affordable shared DAGs preserve aliases; the
26-node repeated-tuple regression is rejected with `SnapshotLimitError` despite
its small byte/node/depth footprint. Writers run graph preflight before SIM-02's
private clone, and a work-limit failure leaves the destination unchanged.

Before allocating engine objects or invoking adapters, validation checks the
envelope, digest, closed tags, field inventory, required fields, scalar/reference
types, enums, MT array/index/cache, dice queues, array bounds, duplicate/dangling
references, unreachable nodes, safe hashable keys, canonical team/player/board
links, pitch indexes, component data boundaries and version identities.
SIM-02 then validates executable boundary/configuration/timeline coherence and
rebuilds derived state on private objects before the reader returns. A failed read
does not expose a partial snapshot or alter a caller's game. Final live replacement
still uses `restore_snapshot`'s atomic preparation and compatibility gate.

Malformed data raises `SnapshotFileError`; incompatible format/schema/component/
ruleset versions raise `SnapshotIncompatibleError`; exceeded bounds raise
`SnapshotLimitError`. These extend SIM-02 `SnapshotError`. Filesystem errors
remain `OSError`. There is no migration or fallback to legacy saves.

Writing finishes capture/codec validation and bounded encoding first, then writes
a unique temporary file in the destination directory, flushes, fsyncs, closes and
uses `os.replace`. Errors before replacement preserve the prior valid file and
remove the temporary. Replacement is the commit point. This promises atomic file
visibility on filesystems implementing atomic rename; it does not promise a
directory entry survives sudden power loss on every filesystem.

Legacy web/replay pickle remains a separate explicitly trusted/local API. See
Python's [pickle security warning](https://docs.python.org/3/library/pickle.html)
and [JSON resource-limit guidance](https://docs.python.org/3/library/json.html).
