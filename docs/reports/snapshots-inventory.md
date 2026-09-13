# SIM-02 procedure and reference inventory

This inventory accompanies the executable allowlists in
[`snapshots.py`](../../botbowl/lab/snapshots.py). It is an implementation inventory
for independent inspection, not an independent review verdict.

All listed procedure types include inherited fields. `Procedure` owns `game`,
`context`, `started` and `done`; Reversible owns `_trajectory` and `_ignored_keys`.
`game` remaps to the clone or existing restore target. `context`, nested
procedures and `rerolled_procs` retain aliases/cycles through one graph memo.
Every declared instance field is copied except derived `MoveAction.paths`.
The separate `steps` field preserves a selected route's continuation. A settled
child decision can suspend an ancestor MoveAction with nonempty `steps`: capture
retains those steps, `orig_action_type`, player aliases and the child reroll/context
cycle. The currently active automatic route cannot itself be a capture boundary;
busy execution and automatic-step exhaustion also remain ineligible.

The field inventory below is class-local; empty rows inherit their base's fields.
The full engine, clocks, RNG, episode, cache and exclusion inventory is in the
[API contract](../lab/snapshots.md#state-and-reference-inventory).

| Procedure type | Class-local continuation fields |
| --- | --- |
| `Procedure` | `context`, `done`, `game`, `started` |
| `Regeneration` | `player`, `regenerates` |
| `Apothecary` | `casualty`, `casualty_first`, `casualty_second`, `decay`, `decay_roll`, `effect`, `effect_first`, `effect_second`, `inflictor`, `outcome`, `player`, `regeneration`, `roll`, `roll_first`, `roll_second`, `waiting_apothecary` |
| `Armor` | `armor_rolled`, `ejected`, `foul`, `inflictor`, `modifiers`, `player`, `skip_armor` |
| `Stab` | `attacker`, `blitz`, `defender`, `foul_appearance`, `gfi`, `reroll`, `roll` |
| `FoulAppearance` | `attacker`, `defender`, `reroll`, `revolted`, `roll` |
| `Block` | `attacker`, `blitz`, `dauntless_roll`, `dauntless_success`, `defender`, `favor`, `foul_appearance`, `frenzy_block`, `frenzy_checked`, `gfi`, `juggernaut_checked`, `reroll`, `roll`, `selected_die`, `waiting_dump_off`, `waiting_foul_appearance`, `waiting_juggernaut`, `waiting_wrestle_attacker`, `waiting_wrestle_defender` |
| `Bounce` | `kick`, `piece` |
| `Casualty` | `always_hungry`, `blood_lust`, `casualty`, `decay`, `decay_roll`, `effect`, `inflictor`, `player`, `regeneration`, `roll`, `waiting_apothecary` |
| `Catch` | `accurate`, `bomb_choice`, `diving`, `handoff`, `kick`, `passer`, `piece`, `player`, `reroll`, `roll` |
| `Intercept` | `ball`, `interceptor`, `passer`, `reroll`, `roll`, `safe_throw_reroll`, `safe_throw_roll`, `waiting_safe_throw` |
| `CoinTossFlip` | Inherited only |
| `CoinTossKickReceive` | `aa` |
| `Ejection` | `awaiting_bribe`, `player` |
| `Foul` | `defender`, `fouler` |
| `ResetHalf` | Inherited only |
| `Half` | `half`, `kicked_off`, `prepared` |
| `Injury` | `blood_lust`, `dirty_player_used`, `ejected`, `foul`, `in_crowd`, `inflictor`, `injury_rolled`, `mighty_blow_used`, `player`, `stab` |
| `Interception` | `ball`, `interceptors`, `passer`, `team` |
| `Touchback` | `ball`, `players_on_pitch_standing` |
| `LandKick` | `ball`, `landed` |
| `Fans` | Inherited only |
| `Kickoff` | Inherited only |
| `GetTheRef` | Inherited only |
| `Riot` | `effect` |
| `HighKick` | `available_players`, `ball`, `receiving_team` |
| `CheeringFans` | Inherited only |
| `BrilliantCoaching` | Inherited only |
| `ThrowARock` | `rolled` |
| `PitchInvasionRoll` | `player`, `team` |
| `KickoffTable` | `ball`, `rolled` |
| `KnockDown` | `armor_roll`, `blood_lust`, `in_crowd`, `inflictor`, `injury_roll`, `modifiers`, `modifiers_opp`, `player`, `stab`, `turnover` |
| `KnockOut` | `inflictor`, `player`, `roll` |
| `Leap` | `player`, `position`, `reroll`, `roll` |
| `Shadowing` | `player`, `position`, `reroll`, `roll`, `shadower`, `shadowers`, `team` |
| `Tentacles` | `move_proc`, `player`, `position`, `reroll`, `roll`, `tentacler` |
| `Move` | `dodge`, `dodge_proc`, `gfi`, `player`, `position`, `tentaclers`, `tentacles_used` |
| `GFI` | `player`, `position`, `reroll`, `roll` |
| `Dodge` | `break_tackle_target`, `diving_tackler`, `diving_tacklers`, `from_position`, `player`, `position`, `reroll`, `roll`, `waiting_break_tackle` |
| `TurnoverIfPossessionLost` | `ball` |
| `Handoff` | `ball`, `catcher`, `eat_thrall`, `player`, `pos_to` |
| `Explode` | `bomb`, `player` |
| `Land` | `player`, `reroll`, `roll` |
| `PassAttempt` | `catcher`, `dump_off`, `eat_thrall`, `fumble`, `interception_tried`, `pass_distance`, `passer`, `piece`, `position`, `reroll`, `roll`, `safe_throw_used`, `ttm`, `turnover` |
| `Pickup` | `ball`, `player`, `reroll`, `roll` |
| `StandUp` | `moves_required`, `player`, `reroll`, `roll`, `roll_required`, `sroll_required` |
| `PlaceBall` | `aa`, `ball` |
| `EndPlayerTurn` | `player` |
| `JumpUpToBlock` | `player`, `reroll`, `roll` |
| `EscapeBeingEaten` | `delicious_player`, `hungry_player`, `reroll`, `roll` |
| `AlwaysHungry` | `delicious_player`, `hungry_player`, `reroll`, `roll` |
| `UndoPlayerAction` | `game`, `player` |
| `MoveAction` | `can_undo`, `orig_action_type`, `paths`, `player`, `player_action_type`, `steps` |
| `HandoffAction` | `can_undo`, `steps` |
| `PassAction` | `can_undo`, `dump_off`, `picked_up_teammate` |
| `ThrowBombAction` | `can_undo`, `player` |
| `FoulAction` | `can_undo`, `steps` |
| `BlockAction` | `can_undo`, `player` |
| `Frenzy` | `attacker`, `blitz`, `defender`, `first_block` |
| `BlitzAction` | `can_undo`, `player`, `steps` |
| `StartGame` | Inherited only |
| `EndGame` | Inherited only |
| `Pregame` | Inherited only |
| `PreKickoff` | `checked`, `team` |
| `FollowUp` | `attacker`, `defender`, `pos_to` |
| `Push` | `blitz`, `chain`, `crowd`, `follow_to`, `knock_down`, `player`, `player_chain`, `push_to`, `pusher`, `selector`, `squares`, `stand_firm_used`, `strip_ball_condition`, `waiting_for_move`, `waiting_stand_firm` |
| `Scatter` | `gentle_gust`, `is_pass`, `kick`, `piece` |
| `ClearBoard` | Inherited only |
| `Setup` | `aa`, `formations`, `reorganize`, `selected_player`, `team` |
| `ThrowIn` | `ball`, `position` |
| `Turnover` | Inherited only |
| `Touchdown` | `eat_thrall`, `handle_bloodlust`, `player` |
| `TurnStunned` | `team` |
| `EndTurn` | `kickoff` |
| `Turn` | `blitz`, `blitz_available`, `foul_available`, `half`, `handoff_available`, `pass_available`, `quick_snap`, `team`, `turn` |
| `WeatherTable` | `kickoff` |
| `Negatrait` | `ends_turn`, `fail_outcome`, `player`, `reroll`, `reroll_used`, `roll`, `roll_type`, `rolled`, `skill`, `success_outcome`, `waiting_reroll` |
| `Bonehead` | `fail_outcome`, `roll_type`, `skill`, `success_outcome` |
| `ReallyStupid` | `fail_outcome`, `roll_type`, `skill`, `success_outcome` |
| `WildAnimal` | `fail_outcome`, `is_block_or_blitz`, `roll_type`, `skill`, `success_outcome` |
| `TakeRoot` | `fail_outcome`, `roll_type`, `skill`, `success_outcome` |
| `BloodLustBlockOrMove` | `player` |
| `BloodLust` | `fail_outcome`, `is_block`, `roll_type`, `skill`, `success_outcome` |
| `Reroll` | `block_action`, `block_actions`, `can_use_pro`, `can_use_team_reroll`, `loner`, `player`, `pro`, `secondary_clock`, `skill`, `use_reroll` |
| `Pro` | `player`, `reroll`, `roll`, `success` |
| `Loner` | `player`, `reroll`, `result`, `roll`, `success` |
| `EatThrall` | `failed`, `player`, `victim`, `victim_pos` |
| `HypnoticGaze` | `player`, `reroll`, `roll`, `target_player` |

## Reference-sensitive acceptance points

- Reroll ↔ triggering Dodge/GFI/Block/Intercept/etc. cycles; popped procedures
  remain reachable through context and rerolled sets.
- Push pusher/player/selector, chain target and follow-up squares preserve their
  relationship to the canonical board/rosters even when the defender decides.
- Interception/Intercept retain passer, ball, eligible interceptors, selected
  interceptor, pending rolls and safe-throw/reroll continuations.
- Apothecary retains both roll/casualty/effect alternatives, inflictor and player,
  remaining resource, regeneration child and waiting/decay flags. Equal first
  and second roll references remain the same object in the copied graph.
- Reports, retained Actions and ActionChoices point at the same copied players,
  teams, balls/squares and roll objects as their procedures.
- Team indexes, player indexes and dugout ownership are checked/rebuilt against
  the canonical copied graph. Observation bindings preserve initial roster
  order rather than deriving new slots from a potentially reordered live roster.
- Every copied Reversible binding refers to the fresh empty trajectory; movement
  steps recorded after capture own the new board and pieces. Old log entries
  and their bound functions are excluded.
- Native/Python Path objects are derived choices, so native C++ nodes and lazy
  source caches never enter the payload. Pathfinding recomputes on the staging
  game before publication of a clone/restore; suspended routes recompute when
  next offering actions.
- Engine Game/RNG/DiceSource references in container-valued Procedure.context
  are remapped. Final live restore traverses dictionary keys/values, lists,
  tuples, sets, frozensets and every object-array element with one shared memo.
  Replaced tuples/frozensets preserve aliases across container edges; arrays
  preserve shape, aliases and mutable cycles, including zero-dimensional arrays.
  Component payloads can preserve a retained supplied RNG alias
  using the same memo, while engine objects are forbidden in codec payloads.

Tests use real legal continuations, complete procedure-field traversal, RNG and
logical trace comparison, reference-identity checks and atomic failure probes.
`test_suspended_multistep_route_reroll_capture_clone_restore_replay` interrupts
a real two-step path to `(3,5)` with a failed dodge and a pending team reroll.
With forward model off/on, it captures, clones, restores and replays the remaining
route, comparing executable state/reports, generated path choices, complete RNG
and forced queues, and timeline counters/records. It checks the suspended steps,
reroll/context cycle, player/Game/trajectory identities and rejection during each
automatic route step. `test_context_containers_rebind_live_game_aliases_and_recapture`
checks object arrays, scalar object arrays, frozensets and nested combinations,
including dictionary-key and tuple aliases and array self-cycles. It checks live
Game identity, independent clones and immediate recapture/restore. The reference
walker also traverses these carriers and rejects any foreign Game it encounters.
These context-carrier tests use forward model off: the legacy property logger
does not support assigning array/frozenset payloads to tracked context fields;
this correction does not expand that logger's mutation API.

The required independent state/reference-inventory checkpoint must inspect this
inventory and the implementation before a distinct final-capable review.
