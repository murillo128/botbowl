# Local simulation session

`botbowl.lab.SimulationSession` is the stable local façade for controlling both
teams without reading a `Game` or starting the web service. It composes the
public game factory, entity observation, semantic-action, channel, timeline,
reproducibility and executable-snapshot contracts; it does not implement rules.

For bounded synthetic mid-game exercises, use the versioned
[scenario catalogue](scenarios.md), which composes this session and records
exercise completion separately from natural match completion.

```python
from botbowl.lab import SessionConfig, SimulationSession
from botbowl.lab.randomness import SeedSpec

session = SimulationSession(
    SessionConfig(size=1),
    SeedSpec(17, "example", "engine", "local-session-v1"),
)
try:
    legal = session.legal_actions()
    result = session.step(legal.actions[0], legal.state_revision)
finally:
    session.close()
```

`SessionConfig` mirrors the headless factory inputs and adds nonnegative
`max_decisions` plus positive `max_steps`. `reset(config, seed_plan)` validates
and constructs the complete replacement before closing the old game. The seed
plan is the accepted #35 `SeedSpec`, must have `purpose="engine"`, and supplies
all eight derived seed words to the engine. No policy is accepted or called.
Policy scheduling remains an explicit, separately owned driver operation.

`observe(side=None)` returns a `SessionResult` without advancing. Omission uses
the next actor, falling back to home when there is no next actor. `primary`,
`derived` and `control` are independent #48 channel envelopes. Primary contains
the #33 observation, derived is empty until an explicit producer supplies a
separate derived view, and control contains IDs/mask for the flattened #50 legal
actions plus the copied decision context. Neither snapshots nor diagnostics are
inserted into these player-facing channels.

`legal_actions()` returns copied `LegalActionsV1` values, with an empty macro
list because this façade accepts primitive decisions only. Submit one `ActionV1`
or its exact JSON dictionary with the matching `state_revision`. One `step`
accepts one coach decision, resolves automatic consequences to the next decision
and returns all new #52 timeline events. Consecutive decisions by one team and
interruptions owned by the other team remain visible through `next_actor`.

The session's `state_revision` is a transport counter, not logical game time. It
increments after every successful reset, accepted decision, restore, and first
close. It never rewinds. A stale expected value raises `StaleRevision` before
action parsing or engine mutation. Rejected configurations, semantic actions and
snapshots preserve the current game, RNG, timeline and revision.

`snapshot("engine")` returns a privileged process-local `SessionSnapshot` that
wraps the executable engine/timeline graph and session decision-budget state.
`restore(snapshot, expected_revision)` uses the snapshot compatibility and
atomic restore checks, reacquires the restored entity/timeline binding, and then
increments the live session revision. Observation JSON is never a restore input.
The existing explicit #37/#39 graph inventories retain the timeline, event and
decision envelopes, and entity ID-map bindings; no generic copying of unknown
engine components or observation reconstruction is used. Episode scope is not
offered: the session owns no policies, scenario callbacks or auxiliary streams.

For #39 persistence, `SessionSnapshot.engine` is an explicit privileged handle:
pass it to `snapshot_io.write_snapshot`, and use
`dataclasses.replace(saved, engine=snapshot_io.read_snapshot(path))` before
restore. Retain the session envelope's budget fields alongside it; an engine
file alone is not a complete session envelope. The #39 version, integrity and
compatibility gates apply unchanged. Neither object belongs in player channels.

`SimulationSession.from_snapshot(saved)` creates an independent session directly
from a `SessionSnapshot`, preserving its budget and truncation state. It does not
initialize a new match or invoke a policy. For named alternatives with separate
continuation budgets, chance provenance and immutable external predictions, use
the [branch tree API](branches.md).

Natural completion reports `terminated=True`, `truncated=False` and
`end_reason="game_over"`. Decision limits, engine-step exhaustion, no-progress,
execution failure and explicit close never manufacture a sporting result.
Decision exhaustion is returned as `end_reason="decision_budget"`; an engine
execution budget raises typed `NoProgress` and leaves the inspectable session
administratively truncated with `end_reason="execution_budget"`.

Public failures are `InvalidConfiguration`, `InvalidAction`, `StaleRevision`,
`IncompatibleSnapshot`, `SessionClosed`, `NoProgress`, and `ExecutionFailure`.
When an internal failure exists, `error.diagnostic` is a separate detached
`SessionDiagnostic`; it is controller information and never part of a player
result. `close()` is idempotent. After close, reset, step, restore and snapshot
raise `SessionClosed`; `observe()` remains available for the final copied state.

Run the external example from any current directory with
`python /path/to/examples/lab_session.py`.
