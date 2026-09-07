# Temporary player state at drive and turn boundaries

This table describes the existing rules implemented by `PlayerState`,
`ClearBoard`, and the turn procedures. It does not introduce a new rules edition.
At each drive boundary, including the first setup and halftime, `ClearBoard`
resets temporary state for **both complete rosters**, including reserves, KO,
casualties and ejected players. Only players leaving the pitch roll for
Sweltering Heat; on a 1 they sit out the upcoming drive in reserves. Players
who sat out recover automatically at the next drive boundary, even if the
weather remains hot. A kickoff weather change does not shorten that exclusion.

| State | Owner | Reset boundary and retained behavior |
| --- | --- | --- |
| `heated` | `ClearBoard` / `PlayerState.reset` | Clear old heat for every player before rolling for players leaving the pitch. Set only on a failed heat roll; retain throughout the upcoming drive. |
| `used`, `has_blocked`, `moves` | `EndPlayerTurn`, `EndTurn`, `PlayerState` | Ending an activation marks `used` and clears `has_blocked`/`moves`; a second block remains unavailable during the same Blitz. `EndTurn` clears all three for the ending team's entire roster. `ClearBoard` clears them for both rosters. |
| `used_skills`, `squares_moved`, `failed_nega_trait_this_turn` | `PlayerState.reset_turn` / `reset` | Clear at the ending team's turn boundary and for both rosters at a drive boundary. `EndPlayerTurn` also clears movement history. |
| Game `active_player`, `player_action_type` | `EndPlayerTurn` / `EndTurn` | Clear when the activation or team turn ends, including a touchdown that goes directly into setup. |
| `up`, `in_air`, `stunned`, `bone_headed`, `wild_animal`, `taken_root`, `hypnotized`, `really_stupid`, `blood_lust`, `picked_up` | Individual rule procedures; `PlayerState.reset` at drive boundaries | At a new drive, restore `up=True` and clear the other flags for both rosters. Ordinary turn resets keep their existing rule-specific lifetimes; they do not perform this drive reset. |
| `knocked_out`, `ejected`, `injuries_gained`, `spp_earned`; dugout compartments | KO/recovery, injury, ejection and scoring procedures | Neither temporary reset clears these. `PreKickoff` alone handles KO recovery (4+); casualties and ejected players remain outside the reserves/pitch. |

Setup choices, automatic formations and setup minimum counts exclude heated
reserves. `Game.get_reserves(team)` continues to return the actual mutable
dugout list for transfers; `include_heated=False` returns a filtered copy for
setup. Fewer than three eligible players may therefore still form a legal setup.

`tests/game/test_drive_states.py` covers consecutive drive boundaries for both
teams, heat and weather rolls, setup eligibility and positions, KO recovery,
persistent injury/ejection state and reports. Its trajectory undo/redo assertions
cover game state only, not RNG restoration. `test_player_action.py` covers
Blitz → new turn, including a failed dodge turnover, without enabling an extra
activation or block early. `test_full_game.py` includes a seeded, bounded game
that checks that no heated player enters the pitch.
