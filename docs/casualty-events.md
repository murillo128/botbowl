# Casualty reports and Apothecary decisions

`Casualty` owns the single `OutcomeType.CASUALTY` report for an injury. It
represents the casualty suffered, before recovery, and remains present when
Apothecary or Regeneration saves the player. Decay adds an effect to that injury,
not a second casualty credit. Direct casualty procedures (including Always
Hungry) use the same owner without requiring an `Injury` procedure.

| Report | Meaning |
| --- | --- |
| `INJURY_CASUALTY` | Diagnostic injury-table roll that reached casualty; contains the injury dice and modifiers. No reward credit. |
| `CASUALTY` | One casualty credit. Contains the victim, inflictor, victim's team, initial table roll and candidate effect in `n`. The candidate is not necessarily the final injury. Blood Lust and Always Hungry retain their fixed-result semantics and omit the table dice here. |
| `CASUALTY_APOTHECARY` | Apothecary consumed and a second D68 result rolled; contains both table rolls. A selection is still required. |
| `APOTHECARY_USED_CASUALTY` | Accepted selection after using Apothecary; contains only the selected roll and its effect in `n`. No extra credit. |
| `SUCCESSFUL_REGENERATION` / `FAILED_REGENERATION` | One recovery attempt, after the Apothecary decision when offered. Successful recovery prevents both effects of Decay. |
| `DECAYING` | An additional table result for the same injury, after failed or unavailable recovery. Contains its table dice and candidate effect in `n`. No additional regeneration attempt or casualty credit. |
| `BADLY_HURT` / `MISS_NEXT_GAME` / `DEAD` | Effect actually applied by `Game.apply_casualty`, with the selected table dice. Decay can produce two effect reports. |
| `APOTHECARY_USED_KO` | Apothecary consumed to leave the player prone and stunned on the pitch. |
| `KNOCKED_OUT` | Player actually sent to KO, including when Apothecary was declined. |

Declining Apothecary preserves the original result and the remaining resource;
it emits neither use nor selection reports. Accepting consumes exactly one
Apothecary, even when the first roll is selected or Regeneration later succeeds.
A treated Badly Hurt result returns to reserves; an untreated one stays in the
casualty box. Treating one Decay result does not also treat its other result.
The engine retains the ability to offer an unused Apothecary on that later
result for custom rosters combining these capabilities.

These recovery rules follow the implemented D68 edition: see Apothecary
(printed page 17), Decay and Regeneration in the
[Competition Rules Pack](https://www.thenaf.net/wp-content/uploads/2013/06/CRP1.pdf).
This change does not migrate to the 2020 injury table.

`examples/a2c/a2c_env.py::A2C_Reward` retains its existing weights: a casualty
contributes -0.5 for the victim's team and +0.5 for the opponent, including a
recovered casualty. It reads only new reports since its previous call; reading
again without new reports gives zero event reward. Consumers that want the
final damage must inspect the effect/recovery reports, not the initial `n`.
Existing outcome numbers are preserved; `INJURY_CASUALTY` adds value 187.
