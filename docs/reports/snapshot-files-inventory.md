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
five streams, policy counters, wrapper RNG, aliasing and truncation.

The matrix covers all five sizes at pre-randomness, setup and terminal boundaries,
forward model off/on; reroll, push, interception and apothecary decisions for both
teams at size 3; a real suspended two-step route; and both scopes with EpisodeContext
at all five sizes. Additional tests cover double loads, mutation isolation,
semantic/digest distinctions, no constructor/global-RNG effects, malformed types/
enums/IDs/keys/refs/versions, corruption, limits, truncation, module sentinels,
atomic write failures at each stage, and late restore-adapter failure.

Required review order remains: independent schema/codec checkpoint against an
exact published commit, then a distinct fresh final-capable review covering the
complete final diff and multiprocess/ordinary-suite evidence. The coordinator
owns those reviews and workflow transitions. This inventory does not satisfy
either independent gate by itself.
