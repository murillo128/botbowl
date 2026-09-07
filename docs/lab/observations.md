# ObservationV1

`botbowl.lab.observations.observe(game, control, observer_team)` exports copied,
public entity data from the current engine boundary. It returns the typed
`ObservationV1` dataclass; `to_json()` returns fresh JSON-compatible dictionaries
and lists. No Game, Player, Team, Square, procedure, agent or RNG reference is in
the observation. The schema is defined by the dataclasses in
[`observations.py`](../../botbowl/lab/observations.py), including nested field
types and literal domains. A change in field meaning, type, presence semantics
or identity convention requires a new schema version.

```python
import json
import botbowl as bb
from botbowl.lab.observations import ObservationControl, observe

config = bb.load_config("gym-3")
rules = bb.load_rule_set(config.ruleset)
home = bb.load_team_by_filename("human", rules, board_size=3)
away = bb.load_team_by_filename("human", rules, board_size=3)
game = bb.Game("example", home, away, bb.Agent("home", human=True),
               bb.Agent("away", human=True), config, seed=17)
control = ObservationControl(game)  # Once, before init or the first action.
game.init()
public_input = observe(game, control, "home").to_json()
encoded = json.dumps(public_input, allow_nan=False)
```

Retain `control` for the whole episode, including drive changes, KO, casualties,
reserves and terminal observations. Its internal-ID map belongs to caller control
code. `control.internal_player_id("home:0")` resolves a local ID for that code;
never include the control object or its map in policy features. A new Game or
clone needs a new binding. Bindings reject a different Game/team instance,
duplicate initial internal IDs and later roster additions. They preserve the
initial roster and slot order when a roster list is reordered or loses a player.
The engine's player/team identities must remain unchanged within an episode.

Home and away observers receive identical public facts; only `observer_team`
differs. Coordinates remain absolute for both sides: `(0, 0)` is the upper-left
arena cell, x increases rightward and y downward. Side normalization is API-06's
responsibility. None of these fields is an estimate of success or tactical value.

## Types, presence and visibility

All fields below are public. No visibility depends on observer side. “Always”
means available at every read, including before init and after game over; the
value is the engine's current raw/default value, without inferring that a
procedure has already established it. For example, pregame `half=1`, `round=0`
and `weather="NICE"` are the engine's initialized values. The `decision.phase`
and `pending` fields give boundary context.

`Presence[T]` always has exactly `value: T | null` and `present: bool`.
`present` is true exactly when `value` is not null. A present zero or false is
data, never absence. A false presence bit means absent **or unavailable under
the field's documented rule**; it does not assert nonexistence in hidden engine
context. Required numeric and boolean fields have no mask because they are
always available. Empty lists represent known empty collections.

`Side` is `"home" | "away"`. `Position` has `x: int` and `y: int` in arena
coordinates. Placed roster players can occupy a crowd-border cell during a
push; airborne/scatter objects may temporarily have exterior coordinates if the
caller reads between decisions. `playable[y][x]`, not mere coordinate presence,
identifies a playable square. The observer does not clamp or move pieces.

Enum strings are the `.name` values of the indicated engine enums, not integer
codes. Strings such as race and role come from the loaded public roster; free
player/team/agent names and internal IDs are deliberately omitted.

| Root field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `schema_version` | literal integer `1` | Always; wire schema identity. |
| `observer_team` | Side | Always; caller's observing side, not a coordinate transform. |
| `geometry` | Geometry | Always; loaded arena, including crowd border. |
| `players` | list of PlayerObservation | Always; every initially bound roster slot, home then away. |
| `teams` | list of two TeamObservation | Always; home then away. |
| `balls` | list of BallObservation | Always; current `Pitch.balls` order, empty before balls exist. No persistent ball IDs are claimed. |
| `match` | MatchObservation | Always; current match facts. |
| `decision` | DecisionContext | Always; selected public boundary facts, explicitly partial. |

| Geometry field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `width`, `height` | positive int | Always; full arena dimensions. |
| `tiles` | height × width list of Tile-name strings | Always; exact loaded arena cells, including HOME/AWAY/endzone/scrimmage/wing/crowd distinctions. |
| `playable` | height × width list of bool | Always; true exactly for non-CROWD arena tiles. |

| PlayerObservation field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `id` | string `home:<slot>` or `away:<slot>` | Always; deterministic episode-local categorical identity. |
| `team` | Side | Always; bound roster side. |
| `slot` | int ≥ 0 | Always; zero-based initial roster-list index, not jersey number or internal ID. |
| `position` | Presence[Position] | Present when `Player.position` exists; includes lifted players' recorded position. |
| `location` | `pitch`, `reserves`, `ko`, `casualties`, `dungeon`, `unplaced` | Always; position takes precedence, then explicit dugout membership. `unplaced` means no position and no listed compartment, including pre-init. |
| `role` | roster role-name string | Always; loaded public role. |
| `attributes` | Attributes | Always; current public MA/ST/AG/AV via Player getters, including injuries and Taken Root, preserving engine behavior. |
| `role_attributes` | Attributes | Always; raw role MA/ST/AG/AV before player adjustments. |
| `extra_attributes` | Attributes | Always; raw player extra_ma/st/ag/av. |
| `skills` | list of Skill-name strings | Always; role skills followed by extra skills, retaining engine order/duplicates. |
| `used_skills` | list of Skill-name strings | Always; already-used skills, sorted by name for deterministic set serialization. |
| `injuries`, `injuries_gained` | lists of CasualtyEffect-name strings | Always; prior injuries and current-game injuries respectively. |
| `mng` | bool | Always; roster miss-next-game flag. |
| `status` | PlayerStatus | Always; explicit raw public state flags/counters below. |
| `squares_moved` | list of Position | Always; copied public movement history retained by PlayerState, in order. |

`Attributes` has `ma`, `st`, `ag`, `av`, all integers: movement allowance,
strength, agility and armour. Current getters normally return 1–10; Taken Root
can return MA 0. Role values are loaded integers; extra values are signed integer
adjustments. The export preserves model getters (including inherited behavior)
and does not recompute tactical features or certify rules correctness.

| PlayerStatus fields | Type/domain | Meaning and availability |
| --- | --- | --- |
| `up`, `in_air`, `used`, `stunned` | bool each | Always; standing, lifted/airborne, activation used, stunned respectively. |
| `bone_headed`, `hypnotized`, `really_stupid`, `wild_animal`, `taken_root`, `blood_lust` | bool each | Always; raw named skill/trait state. |
| `heated`, `knocked_out`, `ejected` | bool each | Always; heat, KO and ejection flags; compartment remains separately observable. |
| `picked_up`, `has_blocked`, `failed_nega_trait_this_turn` | bool each | Always; picked-up, block-used and failed-negatrait state. |
| `moves`, `spp_earned` | int ≥ 0 each | Always; engine movement counter and in-game star-player points earned. |

| TeamObservation field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `id` | Side | Always; deterministic team identity. |
| `race` | roster race-name string | Always; public roster race. |
| `score`, `turn` | int ≥ 0 each | Always; touchdowns and team's current half-turn counter from TeamState. |
| `resources` | TeamResources | Always; current public resources below. |

| TeamResources fields | Type/domain | Meaning and availability |
| --- | --- | --- |
| `rerolls`, `rerolls_start` | int ≥ 0 each | Always; remaining rerolls and starting half allocation. |
| `reroll_used` | bool | Always; team reroll used this turn. |
| `apothecaries`, `bribes`, `babes` | int ≥ 0 each | Always; remaining/current named resources. |
| `wizard_available`, `masterchef` | bool each | Always; raw engine flags, not claims that every related procedure is implemented. |
| `ass_coaches`, `cheerleaders`, `fame` | int ≥ 0 each | Always; assistant coaches, cheerleaders and current fame. |

| BallObservation field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `position` | Presence[Position] | Present when this ball has a recorded position. |
| `carrier` | Presence[player ID string] | Present when is_carried and its in-arena board cell contains a roster player. Otherwise unavailable/absent; no guessed carrier. |
| `on_ground`, `is_carried` | bool each | Always for each ball; exact raw flags, preserved independently without assuming mutual exclusivity. |

| MatchObservation field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `half` | int, normally 1 or 2 | Always; current GameState half. |
| `round` | int ≥ 0 | Always; current GameState round, including initialized zero. |
| `drive` | Presence[int ≥ 0] | Always unavailable (`null`, false): this engine has no authoritative drive counter. Not inferred from scores/reports. |
| `kicking_team`, `receiving_team` | Presence[Side] each | Present once respective `*_this_drive` fields are assigned; retain raw values at terminal. |
| `weather` | WeatherType-name string | Always; current weather/default. |
| `game_over` | bool | Always; exact terminal flag. |

## Decision context and limits

The caller owns driving the engine to a decision. Reading never calls `step`,
`set_available_actions`, procedure `available_actions`, serialization on engine
objects, RNG, clock/time queries, pathfinding or a probability query. It reads the
already-cached choices; an intermediate or stale engine boundary remains such a
boundary. An observation is not a legal-action catalogue or a validation result.

| DecisionContext field | Type/domain | Meaning and availability |
| --- | --- | --- |
| `phase` | Phase literal string | Always; approved semantic label for exact top-procedure class, `unstarted` for empty stack, `terminal` for game_over, `other` for unmapped/custom classes. |
| `pending` | bool | Always; at least one cached non-disabled option, false at terminal. |
| `actor_team` | Presence[Side] | Team of first cached choice, matching Game.active_team/actor semantics; absent with no choices or at terminal. Does not expose an agent. |
| `active_team` | Presence[Side] | Current GameState team, possibly distinct from the choosing defender; present if assigned, including retained terminal values. |
| `active_player` | Presence[player ID] | GameState active player if assigned; raw retained terminal values are possible. |
| `subject` | Presence[player ID] | Approved current procedure participant, e.g. mover, attacker, rerolling player, passer or interceptor; absent for unmapped contexts. |
| `target_player` | Presence[player ID] | Block/follow-up defender, pushed player, pass catcher, or intercept passer; for reroll, selected target of its immediate approved context. |
| `target_position` | Presence[Position] | Announced GFI/dodge/pass destination or follow-up destination, also in an immediate reroll context. It is not a future path step or computed candidate. |
| `player_action` | Presence[PlayerActionType-name string] | Current GameState player action type if assigned. |
| `reroll_of` | Presence[Phase] | Present only for top-level Reroll, naming its immediate context or `other`. |
| `turn` | Presence[TurnContext] | Nearest exact Turn's public flags if present, absent at terminal. No stack representation is exported. |
| `options` | list of PromptOption | Cached prompt types/skills/teams in order, duplicates retained; empty at terminal. |
| `sufficiency` | literal `partial` | Always; explicit declaration that this is not proven sufficient/Markov. |

Phase values are `unstarted`, `terminal`, `other`, `start_game`, `coin_toss`,
`kick_receive`, `setup`, `place_ball`, `high_kick`, `touchback`, `turn`, `move`,
`block_action`, `blitz`, `pass_action`, `handoff_action`, `foul_action`, `block`,
`push`, `follow_up`, `reroll`, `gfi`, `dodge`, `pickup`, `catch`, `pass`,
`interception`, `intercept`, `apothecary`. These are a fixed allowlist; arbitrary
Python class names never become schema values. Pregame/automatic procedures and
unmapped skill prompts can be `other`.

Each `TurnContext` field is boolean: `blitz`/`quick_snap` identify kickoff special
turns; `blitz_available`, `pass_available`, `handoff_available`, `foul_available`
copy the current turn's public unspent action flags. Each `PromptOption` contains
`action_type: ActionType-name string`, `team: Presence[Side]`,
`skill: Presence[Skill-name string]`, and `disabled: bool`. These describe the
already-presented prompt, including rolled block faces expressed as selection
types; they do not export ActionChoice roll thresholds, block-dice predictions,
paths, players/positions candidate lists or success probabilities.

For example, on the same board a move decision can have `phase="move"`, while
a failed GFI reroll has the following additional public context (excerpt):

```json
{
  "phase": "reroll",
  "subject": {"value": "home:0", "present": true},
  "target_position": {"value": {"x": 4, "y": 2}, "present": true},
  "reroll_of": {"value": "gfi", "present": true},
  "sufficiency": "partial"
}
```

A missing position is `{"value": null, "present": false}`; a known position
at the coordinate origin is `{"value": {"x": 0, "y": 0}, "present": true}`.

This context intentionally omits the Python procedure stack, local continuation
flags, nested reroll/skill chains beyond the one immediate reroll context,
candidate action targets, dice-roll objects/history, reports, injury-choice
alternatives, bomb state and clocks/deadlines. Unmapped decisions may share an
observation despite requiring different subsequent handling. Do not claim
Markovianity, complete strategic information, full procedure coverage, or use the
snapshot alone to reconstruct engine execution.

Seeds, RNG state, fixed-dice queues, hidden futures, pending path steps, private
bot policy/weights, tackle-zone layers, roll/block probabilities, valuations and
evaluation artifacts are outside the allowlist. Existing `Game.to_json()` and
RL feature layers are not reused. Tests compare against raw model fields/getters,
not those enriched features. The tests in `tests/lab/test_observations.py` cover
the declared boundary; the issue #4 baseline remains separate evidence of
bounded engine semantics, not proof of observation sufficiency.
