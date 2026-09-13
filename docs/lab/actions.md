# Semantic actions v1

`botbowl.lab.actions` is the public data boundary between a controller and the
legacy `Action`/`ActionChoice` objects. `ActionControl` is episode-local trusted
state: create it once for a `Game`, retain it for the episode, and send only the
result of `to_json()` to an external policy.

```python
from botbowl.lab.actions import ActionControl

control = ActionControl(game)
decision = control.legal_actions()
selected = decision.actions[0]
request = control.request(selected)
engine_action = control.decode(request)
result = game.advance(engine_action)
```

The executable version is [`examples/lab/actions.py`](../../examples/lab/actions.py).
All wire enum values are stable `.name` strings. They never use Python enum
ordinals, Gym indices, internal player UUIDs, agent IDs, or a session ID.
Coordinates are absolute arena coordinates, including the allocated crowd
border: `(0, 0)` is the upper-left cell, x increases rightward, and y downward.

## Schemas and option catalogue

`ActionV1` has exactly these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | literal integer `1` | Action wire version. |
| `type` | `ActionType.name` string | Stable engine action name. |
| `actor_id` | `home` or `away` | Team that owns the current choice. |
| `player_id` | local player ID or null | Selected member of an offered player list. |
| `target_id` | local player ID or null | Occupied target for block, stab, handoff, foul, or hypnotic gaze. |
| `position` | `{x, y}` or null | Selected square for spatial actions; target actions use `target_id`. |
| `options` | one object from the table below | Choice metadata needed to retain identity. |

Options are an explicit tagged-by-shape union; unknown or mixed fields are
rejected rather than ignored.

| Option object | Permitted family | Meaning |
| --- | --- | --- |
| `{}` | Every family without special metadata | No additional option. |
| `{"skill": "<Skill.name>"}` | `USE_SKILL`, `DONT_USE_SKILL` | The skill named by the cached choice. |
| `{"skill": "HYPNOTIC_GAZE"}` | `HYPNOTIC_GAZE` | The skill metadata emitted by the engine's occupied-target choice. |
| `{"path": [{"x": int, "y": int}, ...]}` | `MOVE`, `BLOCK`, `STAB`, `HANDOFF`, `FOUL` | Copied path selected by an engine pathfinding choice. No rolls, probabilities, or future state are exposed. |

The explicit payload families are:

- player selection: `START_MOVE`, `START_BLOCK`, `START_BLITZ`, `START_PASS`,
  `START_FOUL`, `START_HANDOFF`, and `START_THROW_BOMB`, represented by `player_id`;
- occupied targets: `BLOCK`, `STAB`, `HANDOFF`, `FOUL`, and
  `HYPNOTIC_GAZE`, represented by `target_id` and resolved to the target's
  current square;
- square selection: `PLACE_BALL`, `MOVE`, `PASS`, `PUSH`, `FOLLOW_UP`, `LEAP`,
  `THROW_BOMB`, `PICKUP_TEAM_MATE`, and `THROW_TEAM_MATE`, represented by `position`;
- `SELECT_PLAYER`: either `player_id` or `position`, according to its choice;
- `USE_SKILL` / `DONT_USE_SKILL`: an optional `player_id` and the required skill option;
- player plus square: only `PLACE_PLAYER`, whose engine contract authorizes
  the product of the offered players and positions;
- all other type-only decisions: no player, target, or position. Enum aliases
  retain the engine's canonical `.name`; this codec does not distinguish values
  the legacy enum aliases together. `CONTINUE` advances automatic work and is
  never enumerated as a coach decision.

No other player/position product is inferred. Disabled choices are absent.
Candidates are checked by `Game.is_action_allowed`, so the codec does not
replace engine legality. A legal listing, encode, decode, or rejection does not
advance the game, consume randomness, update trajectory/replay/clocks, or alter
the caller's `ActionV1`/legacy `Action`.

## Decision control and errors

`LegalActionsV1` contains `decision_id`, monotonic `state_revision`, current
`actor_id`, `actions`, and `macros`. A submitted `ActionRequestV1` repeats the
decision ID and revision. `ActionControl.decode` rejects the request after the
cached engine decision changes, even if a similarly shaped action is offered
later. These control values prevent stale commands; they are not model features
or globally persistent episode/session identities.

Strict parsers reject missing/extra fields, unknown action names, prohibited
option shapes, non-integer coordinates (including booleans), and unsupported
schema versions. Runtime resolution uses typed `SemanticActionError` subclasses:
`ActionSchemaError`, `StaleDecisionError`, `WrongActorError`,
`UnknownEntityError`, `InvalidPositionError`, `AmbiguousActionError`, and
`ActionNotOfferedError`. Do not retry a stale request with a new revision;
request the new legal-action envelope and choose again.
Directly constructed requests and macros pass the same schema checks as wire
data. Decode also rechecks current engine legality, including after game closure.

`encode_action`/`decode_action` preserve historical engine `Action` objects.
When the Gymnasium extra is installed, `gym_to_semantic` and
`semantic_to_gym` translate through `GymnasiumEnv.decode_action` and
`encode_action`. Flat indices remain adapter-local and board-size-dependent;
they are not stored in `ActionV1`.

## Formation and route macros

`LegalActionsV1.macros` describes current controller macros. Formation macros
name one formation already offered by `Setup`. Route macros copy one path and
its final action type. Their deterministic `macro_id` is scoped to the current
decision.

Call `ActionControl.execute_macro`; do not submit the corresponding legacy
formation/path shortcut directly when an observable expansion is required.
The controller replans no game rules. It expands the formation's pure `actions`
plan or the declared route into ordinary actions, re-encodes and validates each
one at the then-current decision, and sends it through `Game.advance`.
A route's initial current-square entry for a prone player expands to `STAND_UP`.
Every subsequent path choice must still describe exactly the anticipated single
step; a newly offered detour to the same endpoint interrupts with
`route_invalidated` before it is submitted.

`MacroResultV1.steps` records `macro_id`, zero-based order, the exact
decision/revision, copied semantic primitive action, accepted result, and every
event returned by that advance. Dice outcomes and automatic consequences remain
events, not invented coach decisions. The prefix is copied, so later mutation
of a proposed remaining route cannot rewrite an action already recorded.

With [timeline recording](timeline.md), each step also exposes `decision`, the
API-02 envelope with logical contexts and the parent/primitive relation. The
timeline retains a copied macro parent and its interruption boundary.

Expansion stops before sending the remainder when the actor changes, the game
terminates, the planned next action is no longer offered, or an unplanned
optional prompt such as a reroll appears. The controller never answers a
reroll or rival decision. Resume by obtaining a new legal-action envelope and
constructing a new macro after validating the remaining intent against the new
state. `status="interrupted"` and `interruption` identify the boundary; already
accepted steps and their events remain available.

Directly decoding a path or legacy formation `ActionV1` is supported for
compatibility and Gym round-trips, but calling `Game.advance` with that shortcut
does not create the observable per-decision macro record. Controllers that need
the API-05 trace must use `execute_macro`.
