# BB2016 skill support and test inventory for issue #25

Inventory of all 79 `Skill` enum entries on composed integration
`8ad8a3cde1a3b5fd391d9527047a8ccb4bb5d16f`. This is an execution-path/test
inventory, not certification of complete BB2016 conformance. XML/enum presence
alone is not support. No engine behavior is changed by this inventory.

- **implemented**: an active rule execution path exists; test evidence is listed
  separately and can be absent or bounded to one interaction.
- **partial**: only a portion is established, or the historical partial status
  has no new evidence sufficient to upgrade it.
- **unsupported**: no active execution branch in the current core; an enum or
  roster declaration does not supply one.

Source names below are under `botbowl/core/` (the Python pathfinder is under
`pathfinding/`). Test short names resolve to `tests/game/test_NAME.py`, except
`pathfinding` under `tests/ai/`, and `external_control`, `public_api`,
`action_validation` under `tests/framework/`. `rules_descriptor` is under
`tests/lab/`; `kickoff_table` under `tests/kickoff/`. Listed tests are representative
current skill references, not exhaustive test counts or claims that every
branch is tested. `#25 pairs` is `tests/issue25/test_interactions.py` calling
`tests/interaction_scenarios.py`.

Counts: 62 implemented, 3 partial, 14 unsupported.

| Skill | Support | Active source anchors | Test evidence | Limit / note |
| --- | --- | --- | --- | --- |
| `THICK_SKULL` | implemented | `procedure.py:Injury.step` | `push`, `injury`, `stab`, `#25 pairs` |  |
| `STUNTY` | implemented | `game.py:Game.get_pass_modifiers`, `game.py:Game.get_dodge_modifiers` | `injury`, `stab` |  |
| `MIGHTY_BLOW` | implemented | `procedure.py:Armor.step`, `procedure.py:Injury.step` | `armor`, `push`, `injury`, `#25 pairs` |  |
| `CLAWS` | implemented | `procedure.py:Armor.step` | None located |  |
| `SPRINT` | implemented | `game.py:Game.get_adjacent_move_actions`, `game.py:Game.get_leap_actions` | None located |  |
| `SURE_FEET` | implemented | `procedure.py:Reroll.start`, `python_pathfinding.py:Pathfinder.get_paths` | `external_control`, `shadowing`, `reroll`, `#25 pairs` |  |
| `NO_HANDS` | implemented | `game.py:Game.get_interceptors`, `procedure.py:Intercept.step` | None located |  |
| `BALL_AND_CHAIN` | partial | `procedure.py:Injury.step`, `procedure.py:PitchInvasionRoll.step` | `kickoff_table` | Injury/push/move exceptions; dedicated random movement remains partial. |
| `DODGE` | implemented | `game.py:Game._get_block_probs_at`, `game.py:Game._get_dodge_prob_at` | `public_api`, `external_control`, `drive_states` |  |
| `PREHENSILE_TAIL` | implemented | `game.py:Game.get_dodge_modifiers` | `dodge`, `probability_queries` |  |
| `TACKLE` | implemented | `game.py:Game._get_block_probs_at`, `game.py:Game._get_dodge_prob_at` | `probability_queries` |  |
| `BREAK_TACKLE` | implemented | `procedure.py:Dodge.step`, `procedure.py:Dodge.available_actions` | `action_validation`, `dodge` |  |
| `TITCHY` | partial | `game.py:Game.get_dodge_modifiers`, `model.py:Player.has_tackle_zone` | None located | Dodge/tackle-zone paths exist; historical partial status retained, no dedicated test. |
| `DIVING_TACKLE` | implemented | `game.py:Game.get_dodge_modifiers`, `procedure.py:Dodge.__init__` | None located |  |
| `SHADOWING` | implemented | `procedure.py:Shadowing.step`, `procedure.py:Shadowing.available_actions` | `shadowing`, `#25 pairs` |  |
| `TENTACLES` | implemented | `procedure.py:Move.__init__` | None located |  |
| `TWO_HEADS` | implemented | `game.py:Game.get_dodge_modifiers`, `python_pathfinding.py:Pathfinder._get_dodge_target` | None located |  |
| `BLOCK` | implemented | `game.py:Game._get_block_probs_at`, `procedure.py:Block.handle_block_die` | `negatraits`, `block`, `frenzy` |  |
| `WRESTLE` | implemented | `procedure.py:Block.handle_block_die`, `procedure.py:Block.available_actions` | None located |  |
| `STAND_FIRM` | implemented | `game.py:Game._get_block_probs_at`, `procedure.py:Push.step` | `strip_ball`, `block`, `frenzy`, `#25 pairs` |  |
| `GUARD` | implemented | `game.py:Game.get_assisting_players`, `game.py:Game.can_assist` | None located |  |
| `HORNS` | implemented | `game.py:Game.get_block_strengths`, `game.py:Game.num_block_dice_at` | `probability_queries` |  |
| `SIDE_STEP` | implemented | `game.py:Game._get_push_squares_at`, `game.py:Game._get_block_probs_at` | `push`, `probability_queries` |  |
| `FRENZY` | implemented | `procedure.py:Block.step`, `procedure.py:BlockAction.step` | `frenzy`, `stab` |  |
| `CATCH` | implemented | `game.py:Game.get_catch_prob`, `procedure.py:Reroll.start` | `catch`, `pathfinding` |  |
| `SURE_HANDS` | implemented | `game.py:Game._get_block_probs_at`, `game.py:Game.get_pickup_prob` | `external_control`, `strip_ball`, `pickup`, `#25 pairs` |  |
| `BIG_HAND` | implemented | `game.py:Game.get_pickup_modifiers`, `python_pathfinding.py:Pathfinder._get_pickup_target` | `pickup` |  |
| `EXTRA_ARMS` | implemented | `game.py:Game.get_catch_modifiers`, `game.py:Game.get_pickup_modifiers` | `catch`, `pickup`, `interception` |  |
| `DIRTY_PLAYER` | implemented | `procedure.py:Armor.step`, `procedure.py:Injury.step` | None located |  |
| `SNEAKY_GIT` | implemented | `procedure.py:Armor.step`, `procedure.py:Injury.step` | None located |  |
| `STRONG_ARM` | implemented | `game.py:Game.get_pass_modifiers` | `pass` |  |
| `LONG_LEGS` | unsupported | No active core branch | None located | Unused enum; VERY_LONG_LEGS is the implemented spelling. |
| `PASS` | implemented | `game.py:Game.get_pass_prob`, `procedure.py:Reroll.start` | `pass` |  |
| `LONER` | implemented | `procedure.py:Reroll.step`, `procedure.py:Loner.step` | `external_control`, `reroll`, `#25 pairs` |  |
| `WILD_ANIMAL` | implemented | `procedure.py:Turn.step`, `procedure.py:WildAnimal.__init__` | `negatraits` |  |
| `RIGHT_STUFF` | implemented | `game.py:Game.get_pickup_teammate_actions` | `always_hungry`, `throw_team_mate` |  |
| `ALWAYS_HUNGRY` | implemented | `game.py:Game.get_pickup_teammate_actions`, `procedure.py:PassAction.step` | `always_hungry` |  |
| `THROW_TEAM_MATE` | implemented | `procedure.py:PassAction.available_actions` | `always_hungry`, `throw_team_mate` | PassAction → AlwaysHungry/PassAttempt/Land; historical NO is stale. |
| `BONE_HEAD` | implemented | `procedure.py:Turn.step`, `procedure.py:Bonehead.__init__` | `negatraits`, `player_action`, `reroll`, `#25 pairs` |  |
| `DUMP_OFF` | implemented | `game.py:Game.get_pass_actions`, `procedure.py:Block.start` | None located |  |
| `STAB` | implemented | `game.py:Game.get_block_actions`, `procedure.py:Stab.step` | `shadowing`, `frenzy`, `stab` |  |
| `JUMP_UP` | implemented | `game.py:Game.get_stand_up_actions`, `game.py:Game.get_block_actions` | None located |  |
| `DAUNTLESS` | implemented | `procedure.py:Block.step` | `frenzy` |  |
| `JUGGERNAUT` | implemented | `procedure.py:Block.step`, `procedure.py:Block.handle_block_die` | None located |  |
| `SECRET_WEAPON` | unsupported | No active core branch | None located |  |
| `NERVES_OF_STEEL` | implemented | `game.py:Game.get_catch_modifiers`, `game.py:Game.get_pass_modifiers` | `catch`, `pass` |  |
| `BOMBARDIER` | implemented | `procedure.py:Turn.available_actions` | None located | Turn → ThrowBombAction/PassAttempt/Explode; no dedicated test. |
| `LEAP` | implemented | `game.py:Game.get_leap_actions`, `procedure.py:Leap.start` | `leap` |  |
| `VERY_LONG_LEGS` | implemented | `game.py:Game.get_catch_modifiers`, `game.py:Game.get_leap_modifiers` | `leap`, `interception` |  |
| `CHAINSAW` | unsupported | No active core branch | None located |  |
| `TAKE_ROOT` | implemented | `procedure.py:Push.step`, `procedure.py:Turn.step` | `strip_ball`, `negatraits` |  |
| `SAFE_THROW` | implemented | `procedure.py:Intercept.step`, `procedure.py:PassAttempt.step` | `pass`, `interception` |  |
| `DECAY` | implemented | `procedure.py:Injury.step` | `apothecary`, `casualty` |  |
| `DISTURBING_PRESENCE` | implemented | `game.py:Game.get_catch_modifiers`, `game.py:Game.get_pass_modifiers` | None located |  |
| `NURGLES_ROT` | unsupported | No active core branch | None located |  |
| `FOUL_APPEARANCE` | implemented | `procedure.py:Stab.step`, `procedure.py:FoulAppearance.step` | `frenzy`, `stab` |  |
| `DIVING_CATCH` | implemented | `game.py:Game.get_catch_modifiers`, `game.py:Game.get_catcher` | `catch` |  |
| `BLOOD_LUST` | implemented | `game.py:Game.get_adjacent_blood_lust_victims`, `procedure.py:Turn.step` | `blood_lust` |  |
| `HYPNOTIC_GAZE` | implemented | `game.py:Game.get_hypno_targets`, `game.py:Game.get_hypnotic_gaze_actions` | `hypnotic_gaze` |  |
| `HAIL_MARY_PASS` | partial | `game.py:Game.get_pass_distances_at` | None located | Target-range branch exists; special resolution is not established by dedicated evidence. |
| `ACCURATE` | implemented | `game.py:Game.get_pass_modifiers` | `pass` |  |
| `KICK` | unsupported | No active core branch | None located |  |
| `KICK_OFF_RETURN` | unsupported | No active core branch | None located |  |
| `PASS_BLOCK` | unsupported | No active core branch | None located |  |
| `FEND` | implemented | `procedure.py:FollowUp.step`, `procedure.py:FollowUp.can_follow_up` | `frenzy` |  |
| `MULTIPLE_BLOCK` | unsupported | No active core branch | None located |  |
| `STRIP_BALL` | implemented | `game.py:Game._get_block_probs_at`, `procedure.py:Push.__init__` | `strip_ball`, `push`, `probability_queries`, `#25 pairs` |  |
| `GRAB` | implemented | `game.py:Game._get_push_squares_at`, `game.py:Game._get_block_probs_at` | `probability_queries` |  |
| `STAKES` | implemented | `game.py:Game.get_block_actions`, `procedure.py:Stab.step` | `stab` |  |
| `ANIMOSITY` | unsupported | No active core branch | None located |  |
| `PILING_ON` | unsupported | No active core branch | None located |  |
| `REALLY_STUPID` | implemented | `procedure.py:Turn.step`, `procedure.py:ReallyStupid.__init__` | `negatraits` |  |
| `REGENERATION` | implemented | `procedure.py:Regeneration.step`, `procedure.py:Apothecary.step` | `apothecary`, `casualty` |  |
| `MONSTROUS_MOUTH` | unsupported | No active core branch | None located |  |
| `SWOOP` | unsupported | No active core branch | None located |  |
| `FAN_FAVOURITE` | unsupported | No active core branch | None located |  |
| `SWIFT_REACTION` | unsupported | No active core branch | None located |  |
| `PRO` | implemented | `procedure.py:Reroll.start`, `procedure.py:Reroll.step` | `external_control` |  |
| `TIMMMBER` | implemented | `game.py:Game.get_stand_up_modifier` | `timber` |  |

`docs/features.md` is historical: for example Throw Team-Mate now has a real
procedure chain and regression tests, and Push/injury, rerolls and Shadowing have
substantial tests despite older NO cells. Its partial Ball & Chain/Titchy claims
are retained conservatively. Hail Mary has a range branch but no dedicated
special-resolution evidence. No support claim is inferred from an untargeted
random game. Leader, Filthy Rich and Weeping Dagger appear in the historical
feature table but have no current `Skill` entry or core execution support.

Pending mechanism coverage includes untested branches of Claws, Sprint,
No Hands, Diving Tackle, Tentacles, Two Heads, Wrestle, Guard, Dirty Player,
Sneaky Git, Dump-Off, Jump Up, Juggernaut, Disturbing Presence and Bombardier.
Existing probability tests for Tackle/Horns/Grab are not a substitute for all
real procedure outcomes. Postgame Nurgle's Rot and unsupported inducement/star
player features remain outside the corpus. This bounded issue does not implement
those missing mechanics or demand the Cartesian product of skills.
