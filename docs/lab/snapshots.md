# Executable in-memory snapshots (SIM-02)

`botbowl.lab.snapshots` captures a settled `Game` or `EpisodeContext` and creates
independent continuations. This is a privileged controller API, not observation
data or a persistent format. Keep the same process, engine implementation,
NumPy generator, loaded rule configuration and pathfinding backend. SIM-03 owns
cross-process persistence; snapshots explicitly reject pickling and foreign PIDs.

```python
from botbowl.lab.snapshots import capture_snapshot, clone_from_snapshot, restore_snapshot

saved = capture_snapshot(game)
left = clone_from_snapshot(saved, branch_id="left")  # branch_id requires Timeline
right = clone_from_snapshot(saved, branch_id="right")
left.advance(left_action)  # use left's players or coordinates
restore_snapshot(game, saved)  # returns the same Game object
```

The default `scope="engine"` retains seat IDs/names/human flags, with inert
lifecycle callbacks and an `act` method that explicitly requires supplied actions.
It never copies policy objects. Continue using `Game.advance`; selecting snapshot
scope does not change `external_control` or the rules of a legacy game. With an
`EpisodeContext`, the result is a context with its engine, four additional owned
streams, decision budget, provenance and roster bindings, with policies/scenario
callbacks omitted. Its continuation uses `step`. The retained manifest identifies
the captured episode recipe; engine scope does not promise policy decisions or
callback-dependent `reset` behavior.

## Boundaries and failure behavior

Capture requires successful `init` or a settled decision/terminal advance. Engine
operation guards reject capture during init, a procedure step, or finalization;
episode guards also cover policy, observer and scenario callbacks. Automatic-step
exhaustion, unfinished timelines/macros, a closed live game, pending route steps,
and failed finalizers cannot be captured. An invalid external action preserves
capture eligibility. Raw property `revert`/`forward` invalidate eligibility: they
are legacy state-only operations, not complete simulator restoration. A subsequent
successful advance or full snapshot restore supplies a new boundary. Calling an
already initialized `init` cannot make partial state eligible again.

Restore first validates version, process, graph integrity, loaded resources,
shared rule-table identity, backend, source structure and target compatibility.
It constructs the complete replacement graph, regenerates route caches and runs
all required component decoders privately before replacing the live target.
`SnapshotError(code="snapshot_invalid")` identifies a rejected capture/restore;
its cause retains underlying diagnostic detail. Rejection leaves the target's
engine, clocks, RNG, counters, policies, streams and trajectory untouched.

Atomicity is at the synchronous API boundary. Callers must serialize access to
the target; this module does not add locks, threads or a Python security sandbox.
Codecs are trusted code with a no-I/O/no-live-mutation contract. It cannot roll
back external effects deliberately performed by a misbehaving codec.

## State and reference inventory

The executable allowlists are `_GAME_DATA`, `_GAME_SPECIAL`, `_EPISODE_DATA`,
`_EPISODE_SPECIAL`, `_MODEL_TYPES`, `_PROCEDURE_TYPES`, `_DATA_TYPES`, and
`_INSTANCE_FIELDS` in `snapshots.py`. Instance fields are unioned across each
accepted class's inheritance chain. Unknown classes/fields fail explicitly;
`Procedure.context` remains an explicitly traversed engine-graph slot. The
[procedure inventory](../reports/snapshots-inventory.md) lists continuation fields.

| State family | Capture / reconstruction |
| --- | --- |
| Game root | Game ID, configuration, arena, ruleset, state, retained action, external-control flag, initialized/closed/finalized flags, audit timestamps and optional seed. |
| Effective resources | All loaded configuration fields (including loader-added `dungeon`/`kick_scatter_dice`), limits, formation matrices, rule races/roles/skills/inducements/SPP tables, arena geometry/tiles. Arena tile-group class lists become owned instance copies. Shared `Rules` tables are compatibility checked, never globally overwritten. |
| Board and roster | Pitch board/squares/balls/bomb; team/player identity, roles, attributes, injuries, resources and statuses; dugout groups; turn order, active player and player-action type. Canonical indexes are rebuilt in place, preserving references to those dictionaries. |
| Phase and continuation | Half/round, drive/kick/receive/current-team flags, weather/gust/spectators, complete procedure stack and every declared continuation field, contexts, reroll cycles and `rerolled_procs` (including popped procedures). |
| Decisions and events | Available choices and their player/team/position/roll references, retained action, reports and complete DiceRoll/die-result fields. Viewer JSON is never used to reconstruct state. |
| RNG | Complete MT19937 keys/position/Gaussian cache; all four dice queues in every frame and strict flags. A single memo also preserves explicitly retained RNG aliases. |
| Timeline | Existing episode/branch IDs and all #52 counters, event/decision/macro/error data, pending/open flags and initial observation roster mapping. Game/entity bindings are remapped to the replacement. Clone `branch_id` uses `Timeline.fork`, preserving prefix IDs and requiring a new branch ID. |
| Episode | Seed recipe, source IDs, initial inputs/pristine teams, manifest, limits, accepted decision count, four non-engine streams and initial roster binding. This is #35's decision budget, not a second #52 event or coach-decision clock. |
| Derived state | Fresh empty trajectory and reconstructed `square_shortcut`, canonical indexes, arena JSON cache and active MoveAction path/choice caches. Suspended MoveAction path maps are cleared; the engine recomputes them when the procedure offers actions again. Selected route `steps` are continuation state and are copied. |
| Explicit exclusions | Old trajectory log/CallableStep functions, replay recorder, `ff_map`, live policy objects, renderers, clients, files, sockets, threads, functions and arbitrary custom objects. Failed-finalizer exceptions/tracebacks are rejected. Seat metadata is copied separately. |

Every graph copy uses one memo across engine, context data and adapter payloads.
It allocates model objects without invoking constructors, `__deepcopy__` or
`__reduce__`. Reversible container mutations during construction bypass logging;
every copied `_trajectory` points to the new trajectory. Procedure, report,
action, board, team/player and observation references preserve internal aliases.
The commit step remaps the staging Game to the existing target Game, including
Game references inside container-valued procedure contexts.

Tuples are traversed; they are not assumed deeply immutable. Cyclic tuples are
outside the declared engine inventory and rejected; engine procedure/object and
mutable-container cycles use the memo. NumPy arrays are copied (object arrays
are traversed), and MT streams are restored into separate generators. Enum
singletons are reused only after checking their scalar/tuple values and absence
of extension state. Forward-model "immutable" annotations are not a snapshot
ownership guarantee: squares, choices, outcomes, rolls, formations and their
mutable children are copied. PlayerState display lists promoted by forward-model
initialization also become owned copies.

Each captured graph has an integrity seal over executable values and reference
edges, excluding only declared rebuilt/excluded state. It is an accidental
corruption check for trusted memory, not an authenticated untrusted-input codec.

## Clocks, undo and transport revisions

Default wall clocks are sampled without mutation and converted to logical elapsed
time, with pause/running state, limits, team and audit timestamp retained. Restored
clocks share an owned `LogicalTime`; decision execution and wall time do not tick
it. `game.time_source.advance(seconds)` explicitly advances lab seconds. An
existing LogicalTime's value is captured exactly. Arbitrary clock callables are
rejected. No expired competitive wall deadline silently resumes. Wall audit
start/end timestamps remain audit data; future terminal wall timestamps need not
match a previous run.

`undo_origin` is explicitly zero. The snapshot preserves whether the forward model
is enabled, with an empty owned action log; no earlier undo history survives.
New `revert`/`forward` operations work on subsequent trajectory changes, retaining
their legacy state-only RNG/timeline behavior. `capture_checkpoint` and
`restore_checkpoint` still provide the narrower #14/#52 ancestor operation.
Use another full snapshot for a complete engine/clock/episode rewind.

A live `dice.force` restore is accepted only with the same still-active source
scope tokens; its DiceSource identity is retained so context-manager exit unwinds
the correct source. Clones and out-of-context restores own copies of every
captured frame and fresh tokens. Their captured top frame remains active data;
they do not resume or later exit the original Python context managers. New
`force` contexts nest above it normally. Forced queues remain a test facility.

Restore replaces player/stream/view objects. Reacquire external bindings after
restore; the existing target Game and EpisodeContext identities survive. Transport
command revision belongs to the live consumer (#54) and must increment there,
while #52 logical time rewinds. For an existing `ActionControl`, replace its
`entities` binding with the restored timeline/context binding and retain the
control object: its next sync increases its revision and rejects old requests.
Do not reset a session's command counter when rebuilding a consumer. No transport
revision is captured in the snapshot or added to Game.

## Episode component adapters

`scope="episode"` requires an `EpisodeContext` and a local `SnapshotAdapters`
registry. Every active policy, scenario callback and custom Game agent must have
an exact-type named adapter. A policy wrapper's adapter must recursively capture
and restore every active child; external consumer wrappers must be supplied as
such an adapted component, rather than assuming caller-owned state is discoverable.
Unknown context fields are rejected. Stateless callbacks also require registration.

```python
from botbowl.lab.snapshots import SnapshotAdapters

adapters = SnapshotAdapters().register(
    "counter-policy-v1", CounterPolicy,
    lambda policy, registry: policy.count,
    lambda count, registry: CounterPolicy(count),
)
saved = capture_snapshot(context, scope="episode", adapters=adapters)
copy = clone_from_snapshot(saved, adapters=adapters)
restore_snapshot(context, saved, adapters=adapters)
```

Capture callbacks must return only detached-data-compatible primitives,
containers, NumPy arrays/RandomState, or nested `registry.capture(child)` records.
Returning a supplied RandomState explicitly declares a retained stream alias;
the shared memo binds it to the corresponding restored context stream. Encoding
only its value with `capture_stream` instead declares independent RNG state.
Engine objects and callable/resource payloads are rejected, even when the engine
object already appears in the copy memo. Adapter names are implementation/version
identities supplied by the caller; registry functions are never stored in snapshots.

Decode callbacks construct fresh objects of the registered exact type from
private data and use `registry.restore(child)` for nested components. A capture
also checks that the registered decoders can construct the payload before
publishing it. Multiple seats/wrappers referencing the same component preserve
that identity within each clone. No component instance is shared with the source
or a sibling when adapters honor this construction contract. In-memory data does
not implicitly save code, closures, global RNG, an external renderer or an
undeclared observer supplied to a later `transform` call.
