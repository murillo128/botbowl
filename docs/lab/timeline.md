# Logical episode time (API-02)

Attach `botbowl.lab.timeline.Timeline(game, episode_id="episode-17")` before the
first action of an externally controlled game. Attachment may precede or follow
`init()` at `START_GAME`. One timeline belongs to one game; attachment to an
already played/loaded game or legacy bot scheduling is rejected. Existing games
have `game.timeline is None`. `GymnasiumEnv(record_timeline=True)` attaches a
fresh timeline on each reset.

The [executable example](../../examples/lab/timeline.py) runs a complete small
episode, expands setup macros, captures/restores a checkpoint, forks a branch,
and prints the JSON trace. The controller owns the engine-bound `Timeline`;
send records or `to_json()` to consumers. This is logical time, without a
physical frequency in Hz.

## Index contract

Sequences are episode-wide, one-based identities. Event/decision zero means
the empty prefix. Other sequence counters are null until their first start,
then retain their latest value even after that scope ends. End events distinguish
closed scopes. No counter derives from `get_step()`, `Replay.idx`, HTTP calls,
render frames, new action lists or actor changes.

| Context field | Initial value | Change |
| --- | --- | --- |
| `episode_id` | Supplied nonempty string, default `game.game_id` | Fixed across the episode's branches. The controller owns uniqueness. |
| `branch_id` | Supplied nonempty string, default `root` | Explicit `fork(new_id)` only. |
| `event_seq` | `0` | Once per emitted report or phase event, in emission order. |
| `decision_seq` | `0` | Once after engine/ActionV1 validation and admission of the action's first engine step by the execution budget. |
| `activation_seq` | null | `Turn.step` accepts a player's `START_*` action and assigns the active player, before traits or optional prompts. |
| `team_turn_seq` | null | `Turn.start`, including actual Blitz and Quick Snap kickoff turns. |
| `drive_seq` | null | The engine schedules kickoff and both setups at half start or an eligible post-touchdown restart. |
| `half` | null | `Half.step` assigns engine half `1` or `2`. |
| `round` | null | Becomes engine round `0` at half start, then follows each `Half.step` round increment. |

The engine initializes its own half to `1` before play; timeline half/round stay
null until the half actually starts. Recovery reports before second-half
assignment still carry the previous engine half/round. Special kickoff turns
increment `team_turn_seq` without advancing `round` or the engine's team turn
number. Both teams have distinct turn identities within the same round.

Failed traits still belong to the newly started activation. Undo ends it; a new
selection starts another. Movement, block-die/defender choices, Pro, team rerolls
and automatic skill rerolls keep the activation identity. `EndPlayerTurn`,
`UndoPlayerAction`, or an enclosing `EndTurn`/terminal boundary closes it.
`EndTurn` closes team turns, including turnovers/touchdowns with no
`END_OF_TURN` report. Drives close at a touchdown's `EndTurn` or after an
end-of-half report. A last-turn touchdown closes the drive without inventing
another kickoff. Terminal cleanup closes any remaining open scopes.

## Events and accepted decision envelopes

`TimelineEvent` has `context` (after emission), `decision_seq` (causing accepted
decision, or null), `kind`, and copied `data`. `kind="report"` is exactly one
event per `Game.report`, preserving Outcome order, rolls, enum names and casualty
multiplicity. Team/player IDs use the initial API-04/API-05 roster binding:
`home`, `away`, `home:0`, `away:0`, etc. Exported records contain no engine
objects, functions, policies, exception objects or RNG states.

Additional phase kinds are `half_started`, `round_started`, `drive_started`,
`drive_ended`, `team_turn_started`, `team_turn_ended`, `activation_started` and
`activation_ended`. They never enter `game.state.reports`. Closing an already
closed/unstarted scope emits nothing. Turn-start data includes `team_id` and
`turn_kind` (`regular`, `blitz`, `quick_snap`); activation-start data includes
`player_id` and `action_type`. Original end-half/terminal reports supply those
final boundaries.

With recording enabled, `Game.advance(action).decisions` normally contains one
`DecisionEnvelope`. Existing `actor`, Outcome-valued `events` and `terminal`
result fields retain their meanings. The envelope contains:

- `before`/`after`: contexts surrounding the action and automatic consequences;
- `actor_id`, `team_id`: choosing seat/team, both `home` or `away` in this
  two-seat engine, from the offered choice rather than `current_team`;
- `action`: accepted, copied API-05 `ActionV1`;
- `event_start`, `event_stop`, `events`: half-open sequence interval
  `[before.event_seq + 1, after.event_seq + 1)` and ordered copied events;
- `next_actor_id`, `terminal`, `status` (`resolved` or operationally `pending`);
- `macro_id`, `primitive_order`: null for direct actions, otherwise parent ID
  and zero-based order.

An empty interval has equal start/stop, for example Pro decline followed by a
team reroll prompt. Consecutive same-team decisions increment twice. Defender
decisions name the defender inside the attacker's turn/activation. Automatic
consequences until the next prompt all carry the causing decision's sequence;
dice and automatic skill use never create coach decisions.

Initialization, queries, serialization/rendering and rejected input add no
accepted action/event. API-05 request tokens reject repeated submissions after
their decision changes. Independently submitting a legal legacy `Action` again
is a new decision; this API adds no transport retry protocol. `None`/`CONTINUE`
only advances automatic work and creates no decision. Repeated terminal queries
return no decisions. Reports emitted by a trusted scenario outside an advance
have null causation, never retroactive attribution to the last coach action.

## Macros and aggregation

`ActionControl.execute_macro` retains API-05 legality and interruption behavior.
Each `MacroStepV1.decision` contains its accepted timeline envelope (null when
disabled). Children carry parent `macro_id` and `primitive_order`.
`Timeline.to_json()["macros"]` retains the copied proposal, before/after context,
accepted child sequence IDs, status and interruption reason/context/next actor/
next primitive order. The macro itself increments no game counter. Resume by
validating a new macro at the new API-05 decision token; prior records stay fixed.

`PolicyDriver.run(...).decisions` returns every intermediate envelope; existing
driver trace and last-result Outcome fields remain compatible. Gymnasium step
info adds `decisions`. Single-player/scripted wrappers concatenate those records
in transition order, including opponent decisions during reset. Actors survive
aggregation. These are control/audit data, outside observation features. Legacy
Gym/bot scheduling remains outside this external-control trace.

## Failures and checkpoint ownership

Rejected input has no entry. Operational failures are separate
`operational_errors` containing context, exception type name and code; failures
do not become coach actions or sporting results. Zero budget before the first
step leaves counters unchanged. Work already started retains a `pending`
accepted envelope and partial interval. Resuming automatic work completes that
same envelope/sequence. Consumers receiving pending envelopes replace by
identity rather than append duplicates. Existing exceptions propagate; arbitrary
procedure-error recovery is not promised. Macro operational failures retain an
interrupted parent and accepted children before propagating. Finalizer failures
retain the terminal decision and a separate operational record.

`Timeline.capture()` returns a detached, trusted `TimelineCheckpoint`: full
context, event/decision/macro prefixes, operational records, pending decision
index and open activation/turn/drive flags. `restore()` restores those fields
together. The owner must first validate/restore matching engine state and RNG.
The existing in-memory `Game.capture_checkpoint()`/`restore_checkpoint()` now
carry this payload and reject mismatched instrumentation before rewinding.
State-only `revert()`/`forward()` retain their legacy semantics: they do not
rewind logical time, RNG or external records. Recorded continuations use the
complete checkpoint boundary.

To branch, restore the ancestor and call `timeline.fork("new-branch")` at a
resolved decision boundary. The retained prefix keeps its original branch IDs;
new contexts/events/decisions use the new branch. Sequences continue without
gaps. Event identity is `(episode_id, branch_id, event_seq)`; accepted-decision
identity is `(episode_id, branch_id, after.decision_seq)`. The controller must
allocate distinct IDs across sibling histories; this binding can reject only
IDs visible in its own retained prefix. Fork emits no gameplay event or RNG
change. Macro IDs and API-05 request tokens stay controller-local.

SIM-02 codecs must preserve these fields, the roster-ID binding and referenced
ancestor prefixes with their matching engine snapshot. This change supplies the
trusted data contract and checkpoint-double tests, not a portable codec,
cross-process restore, policy-state serialization or persistent recorder.
API-05 request caches, wall-clock lifecycle side effects and previously returned
records remain caller-owned and are not rolled back by timeline restore.
