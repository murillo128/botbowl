# SIM-03 schema and codec inspection inventory

This is executor-produced technical inventory, not an independent review verdict.
The wire grammar, bounds, hash normalization and reconstruction contract live in
[snapshot-files.md](../lab/snapshot-files.md). The implementation is
[`snapshot_io.py`](../../botbowl/lab/snapshot_io.py).

The file codec reuses the exact model/continuation field inventory owned by
SIM-02. The complete per-procedure inherited/local field listing is in
[snapshots-inventory.md](snapshots-inventory.md). File schema drift changes the
schema digest in `component_versions`; a file with a different digest is rejected.
`CODEC_FIELDS` provides the complete computed field inventory for inspection.
`CODEC_SCHEMA` now provides 1,277 inherited/local field contracts over 133 object
codecs (26 models, 88 procedures, 10 records, 9 special owners).
[`snapshot_schema.py`](../../botbowl/lab/snapshot_schema.py) declares every local
field and every fieldless subclass explicitly; import-time consistency checks
reject omissions and inventory drift. Its `LOCAL`, `LAZY` and `EPISODE` tables
are the full machine-readable domain and required-presence inventory. All 8
containers, 3 array/RNG/dice codecs and 13 enum types retain their closed grammar.

Required-field handling is shared across codec families. Nullable means a required
field whose value may be null. The only lazy fields are Game.seed, the three
start-created procedure lists, and the declined-Loner result described in the
wire specification. In particular every Reroll local field is required. Domain
contracts specify scalar type/range, exact enum family or member names, allowed
reference tags, tuple arity, record members, mapping key/value and collection item
types. Formation matrices accept the engine's lists of Unicode array rows as well
as rectangular list/tuple or Unicode array matrices, validating every symbol.
Existing resource representations are retained: inducement cost -1 is a sentinel;
Role.feeder can contain the loader's SkillCategory list; Outcome.n can contain a
CasualtyEffect name or the engine's boolean marker. These are explicit unions.

The corrected schema digest includes field domains and lazy presence policies,
episode data constraints, the finite ActionChoice producer-family target table,
enum membership, key-work version 1, semantic version 3 and resource-limit
version 1. Earlier schema identities are incompatible; there is no migration or
format-family change.

| Codec family | Inventory |
| --- | --- |
| Engine model | Configuration, TimeLimits, GameState, Pitch, Team, TeamState, Player, PlayerState, Role, Race, RuleSet, Inducement, Formation, Dugout, Square, Ball, Bomb, Action, ActionChoice, Outcome, DiceRoll, D3, D6, D8, BBDie, TwoPlayerArena |
| Procedures | Every named row in the SIM-02 procedure inventory; `_PROCEDURE_NAMES` is an explicit closed list, with equality to the current engine inventory tested |
| Data records | SeedSpec, ActionV1, PositionV1, EmptyOptionsV1, SkillOptionsV1, PathOptionsV1, TimelineContext, TimelineEvent, DecisionEnvelope, TimelineCheckpoint |
| Special graph owners | Game, Clock, inert Agent, Trajectory, Stack, Timeline, ObservationControl, LogicalTime, ComponentState |
| Containers | list, tuple, dict, set, frozenset, ReversibleList, ReversibleDict, ReversibleSet |
| Arrays/RNG | dtype/shape/flat NumPy arrays; full MT19937 keys/index/Gaussian cache; DiceSource reference to its shared stream plus every forced queue/strict frame |
| Enums | Tile, BBDieResult, RollType, OutcomeType, PlayerActionType, PhysicalState, CasualtyEffect, CasualtyType, ActionType, WeatherType, SkillCategory, Skill, PassDistance |

Reference-sensitive inspection points:

- Canonical rosters, player→team→board squares, balls, choices/actions and reports.
- Procedure→Game and Reroll↔triggering procedure, including popped continuations.
- Interception passer/interceptors/ball and apothecary rolls/choices/resources.
- Suspended pathfinding routes: retain steps, omit native/Python Path objects,
  recompute paths after the child decision without consuming randomness.
- Object arrays (including zero-dimensional arrays), mutable self-cycles,
  frozenset/dictionary-key aliases and shared owned engine/context streams.
- Registered nested policy/wrapper component aliases; reject engine references
  inside component data even when already reachable through another graph root.
- Fresh trajectory and force-frame tokens, reconstructed shortcuts/indexes/cache;
  no old log functions, live Python context managers, sockets or callbacks.
- Data validation before engine allocation, then SIM-02 private boundary and
  resource checks before publication; live restore remains transactional.

`tests/lab/snapshot_process.py` is the executable A/B worker. A creates a fixture,
writes the snapshot, executes prescribed actions and emits an independent trace.
A exits. B starts a fresh interpreter, reads only the snapshot and repeats the
actions without fixture creation or Game.init. Parent compares the full executable
walker (state and all procedure fields/references), events, choices/path values,
RNG arrays/queues and logical timeline; episode cases additionally compare all
five streams, policy counters, wrapper RNG, aliasing and truncation. Both workers
also assert that the transient, non-wire `Game.rule_trace` slot is explicitly
initialized to `None` before continuation.

The matrix covers all five sizes at pre-randomness, setup and terminal boundaries,
forward model off/on; reroll, push, interception and apothecary decisions for both
teams at size 3; a real suspended two-step route; and both scopes with EpisodeContext
at all five sizes. Additional tests cover double loads, mutation isolation,
semantic/digest distinctions, no constructor/global-RNG effects, malformed types/
enums/IDs/keys/refs/versions, corruption, limits, truncation, module sentinels,
atomic write failures at each stage, and late restore-adapter failure.

Correction regressions in `tests/lab/test_snapshot_schema.py` recompute both
digests after deleting Reroll fields or corrupting model/data/procedure scalar,
enum, reference and item domains. They guard decoder construction and compare
live state plus object identities after rejection. Genuine start-created and
Loner lazy states round-trip before phase-inconsistent variants reject.

The shared-DAG controls separate cheap memoized key identity checks from expanded
Python tuple hash cost: 26 repeated tuple nodes reject before decoder construction,
while an eight-level graph round-trips and resaves with aliases intact. Work
preflight also precedes the writer's private clone and preserves an existing file.
Exact typed incidence labeling replaces recursive key-digest sorting. Incoming
and outgoing refinement plus complete bounded individualization handles tied
identity keys/members, anchored associations, mutable aliases, cycles and true
symmetry. Normal/opaque component views preserve common object identity. Direct
hash/read/write budget rejection precedes decoder/adapters and destination
replacement; the large-symmetry probe uses an explicit 500,000-unit budget that
also accepts the three-object symmetric fixture (412,349 shared-ledger units).

Resource-admission regressions compare iterative Tarjan SCC/depth results with an
independent transitive-closure/condensation oracle over chains, diamonds, shared
suffixes, multiple roots, self-loops, duplicate edges and connected cycles. They
permute vertices, adjacency and roots while checking one edge examination,
bounded tree returns/pops, the exact `6V + E + 2U + 32` allowance, reachability,
immutable-cycle handling and D-1/D/D+1 boundaries. Instrumentation also confirms
one immutable-key computation per direct/read/write operation and retention of
the second materialization reserve only on validator-prepared hashing.

Fresh-process resource families retain the exact depth-163 shared-tail boundary,
an affordable short shared DAG, the one-million-character canonical boundary,
two large/shared/non-ASCII scalar groups and a shared cyclic normal/opaque
component projection. Each family runs under hash seeds 1..8 with node, field
and unordered permutations. Direct/read/write succeed at measured byte, depth
and work boundaries and reject one unit below before decoder/adapters/private
clone, preserving the destination and leaving no temporary file.

The finite target inventory contains 14 target-bearing sites among 92 engine
ActionChoice constructors, exercised through 20 branches including three pass
variants and five path variants. Producer fixtures retained in Procedure.context
prove shape/order persistence separately from playable continuations. Armor
extremes, initial/Frenzy STAKES behavior and Foul assist clamps exercise their
actual producers. Independent signed negative probes reject before decoding.

The executable A/B matrix retains the original 58 pairs and adds 12 Foul/Stab
boundaries across both seats and forward-model settings plus Frenzy/STAKES,
path HANDOFF and path FOUL: 73 pairs per backend. Ten tied-identity graph families
and one shared engine/component projection family each resave across hash seeds
1..8, with node renumbering, field order and individual unordered-container
reversals. Two independent producers per identity family compare semantic
identity and labeled incidence receipts; all small symmetric mapping permutations
retain distinct identities. Anchored association, alias, cycle, tuple-order and
opaque-literal changes have distinct semantic hashes. These finite probes do
not assert exhaustive skill-combination or arbitrary graph-size coverage.

Required review order remains: independent schema/codec checkpoint against an
exact published commit, then a distinct fresh final-capable review covering the
complete final diff and multiprocess/ordinary-suite evidence. The coordinator
owns those reviews and workflow transitions. This inventory does not satisfy
either independent gate by itself.
