# Block, blitz and dodge probability queries

`Game.get_block_probs(attacker, defender)` and
`Game.get_blitz_probs(attacker, attack_position, defender)` return four floats
in the existing order:

| Index | Event |
| --- | --- |
| 0 | The attacker is knocked down by this block. |
| 1 | The defender is knocked down or removed into the crowd by this block. |
| 2 | The attacker, if currently carrying a ball, releases it through event 0. |
| 3 | The defender, if currently carrying a ball, releases it through event 1 or Strip Ball. |

Crowd removal is included even if a crowd injury would leave the player
standing in the reserves. Ball loss means release of possession at the block
resolution, **before** a bounce, catch or throw-in; it does not mean the opponent
eventually possesses the ball. A loose ball under a player is not possession.
The normal single-ball game is the supported domain. These are overlapping
marginals of one selected outcome; they are not four disjoint alternatives and
must not be added to obtain a total probability. Each value is finite and in
`[0, 1]`, obtained from finite outcome counts without clipping.

## Fixed selection policy

There is no single strategic block probability independent of choices. These
queries use this deterministic local policy for a single, unre-rolled block:

1. The sign of the strength/assists dice result identifies the chooser: positive
   for the attacker, negative for the defender; magnitude is one, two or three.
   The engine uses positive one for equal strength. A single die offers no choice
   and has the same distribution under either sign.
2. For each possible die face, resolve the direct knockdown, push/crowd and
   ball-release effects below.
3. The chooser first avoids their own knockdown/removal. Among remaining faces,
   they prefer knocking down/removing the opponent; then avoiding their own
   ball release; then making the opponent release the ball.
4. Any remaining face tie prefers defender down (POW), defender stumbles, push,
   both down, then attacker down (skull), in that order for either chooser.
   The two physical push faces have identical effects and retain both of their
   six-sided-die outcomes.

All four probabilities use that **same** selected face on each roll. For example,
with two unskilled players and an attacker-selected two-dice block, attacker
knockdown is `4/36` while defender knockdown is `23/36`: if all dice are skull or
both down, both down is preferred whenever available. With the defender choosing,
those values are `16/36` and `9/36`. Treating each marginal as independently
optimized would describe different decisions and give different answers.
An attacker with Block choosing three dice falls only on three skulls, `1/216`.

## Direct effects and push choices

- Skull knocks down the attacker. Both down independently knocks down each
  player without Block. Defender down knocks down the defender. Defender
  stumbles does so unless the defender has Dodge and the attacker lacks Tackle.
- Push, defender stumbles and defender down all use push geometry. Stand Firm
  is always used when available, so prevents crowd removal but not a knockdown
  from the selected face. An already taken-root defender also stays in place.
- Side Step chooses an empty adjacent **in-bounds** square. If there is none,
  ordinary push directions apply, including occupied squares for chain pushes
  or a mandatory crowd destination. Being surrounded is not a zero-risk result.
- Grab cancels Side Step. Otherwise the block query uses Grab's empty adjacent
  destinations, matching the existing engine's direct push choices. The shared
  engine fallback does not apply Grab to chain pushes. When neither
  skill supplies a destination, ordinary push rules prioritize empty in-bounds
  squares, then crowd squares, then occupied chain-push squares.
- The attacker prefers a crowd destination on an ordinary/Grab push. A defender
  choosing with Side Step prefers an in-bounds destination. Equal destinations
  are ordered by `(y, x)`. These choices apply regardless of who chose the die;
  a defender-selected block die does not transfer the attacker's normal push
  choice. The geometry usually eliminates mixed in-bounds/crowd alternatives
  before this preference is applied.
- Strip Ball causes a carrying defender to release the ball on a push face,
  including a Dodge-protected stumble, unless they have Sure Hands. Consistent
  with the engine, using Stand Firm or being taken root does not prevent that
  release. Knockdown/crowd release and Strip Ball are a union of events, never
  an addition of already-aggregated probabilities.

The query does not execute agent or procedure decisions. The shared push helper
corrects the empty Side Step fallback and excludes illegal Side Step crowd
choices; `Block` and `Push` procedure selection, reroll and skill prompts remain
under engine/agent control.

## Conditions and limits

The block evaluation concerns adjacent on-pitch players, and conditions on a
single block being rolled. It includes strengths, assists, Block, Dodge, Tackle,
Strip Ball, Sure Hands, Stand Firm and Side Step, plus the existing push helper's
Grab handling and already-taken-root state. Blitz includes Horns and assists
from `attack_position`, with the attacker's old square vacated and the new
square occupied for push geometry. A carried ball remains attributed to its
original carrier throughout a hypothetical query.

It does not integrate movement to the block, GFIs, Foul Appearance or other
activation rolls, Dauntless success, block rerolls, Pro, Wrestle, Juggernaut,
Dump-Off, Frenzy's second block, follow-up, armour/injury, bounce/catch/throw-in,
or later chain-push decisions/effects on other players. In particular,
Juggernaut's cancellation of Stand Firm is outside this query model; use the
result only for the declared skill domain. Occupied fallback squares describe
an ordinary chain push, without predicting a whole chain, voluntary skill
choices or a resulting touchdown. These helpers are local conditional
estimates, not full-action success, turnover, possession or value estimators.

An off-pitch or occupied hypothetical origin, nonadjacent block target, or
unsupported number of block dice raises `ValueError`. Callers still establish
action legality, player ownership, activation and path feasibility through the
engine's action API; these queries are not action validators.

## Dodge and purity

`get_dodge_prob_from(player, from_position, to_position, ...)` reads tackle zones,
Prehensile Tail and Tackle at the hypothetical origin and destination modifiers
at the target. It returns one when no dodge roll is needed at the origin.
Otherwise it counts successful D6 faces with the existing natural-one failure
and natural-six success thresholds. Clamping the **roll threshold** to 2–6
implements those die rules; it is not clipping a computed probability.

The existing reroll flags and defaults are preserved: `get_dodge_prob` allows
the Dodge reroll by default; `get_dodge_prob_from` does not; neither allows a team
reroll by default. When requested, an available Dodge reroll is preferred,
subject to adjacent Tackle; otherwise the existing `can_use_reroll` team check
applies. At most one reroll is counted. These flags retain the existing
conditional roll model, including its skill-availability assumptions; they do
not optimize future reroll resources or model optional Diving Tackle, Break
Tackle or other action sequences. No reroll resource or used-skill flag is
consumed.

Block, blitz, dodge and push queries perform reads and local arithmetic only.
They never move a live player or ball, temporarily replace live state, suspend
trajectory recording, roll dice or call a policy. Successes and exceptions
leave the board, positions, reports, procedure stack, team/player state,
trajectory (including its entries and identity), game RNG/forced-dice queues,
and external agent RNG state unchanged. No rollback mechanism is needed.

## Finite-count implementation and oracle

The implementation ranks the six physical face outcomes from best to worst
under the declared policy. If a face has rank `i` (zero-based) and `n` dice are
rolled, the number of rolls on which it wins is
`(6 - i)**n - (5 - i)**n`: all dice have rank at least `i`, with at least one of
rank `i`. Those disjoint counts sum to `6**n`; summing the counts whose selected
face contains an event and dividing once gives its marginal.

`tests/game/test_probability_queries.py` independently enumerates every one of
`6**n` rolls using its own symbolic faces, set-based face effects, successive
preference filters and rational counts. It imports no production face evaluator
or ranking formula. Its main matrix checks 4,608 combinations: six signed dice
counts, three carrier states, four geometries and every combination of six
Block/Dodge/Tackle/Strip Ball/Sure Hands flags. Separate cases cover legal push
fallback, alternative destinations, Grab cancellation, Horns/assists, vacated
origins, loose balls, taken root, dodge rerolls, and in-query/after-query purity
under both forward-model settings, including induced failures.
