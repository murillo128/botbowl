# Probability queries

## Typed conditional launch and block APIs

The public `botbowl` imports include `PassRerollPolicy`, `BlockRerollPolicy`,
`RerollProbabilityInfo`, `PassOutcomeProbabilities` and `BlockOutcomeProbabilities`.
The result types are immutable typed tuples containing copied data only.

```python
Game.get_pass_outcome_probs(player: Player, piece: Ball, position: Square, *,
    reroll_policy: PassRerollPolicy = "pass_skill",
    already_rerolled: bool = False) -> PassOutcomeProbabilities
Game.get_block_outcome_probs(attacker: Player, defender: Player, *,
    reroll_policy: BlockRerollPolicy = "never",
    already_rerolled: bool = False) -> BlockOutcomeProbabilities
Game.get_blitz_outcome_probs(attacker: Player, attack_position: Square, defender: Player, *,
    reroll_policy: BlockRerollPolicy = "never",
    already_rerolled: bool = False) -> BlockOutcomeProbabilities
```

These queries describe natural dice even inside a strict forced-dice context.
Forced queue contents do not supply a distribution or indicate reroll history.
Policy strings are case-sensitive and validated at runtime. Both policy and
history arguments are keyword-only. No arbitrary modifiers or callbacks are
accepted. The historical methods described below retain their own defaults and
domains.

### Ordinary ball launch

`PassOutcomeProbabilities.accurate`, `.inaccurate` and `.fumble` partition a
normal ball launch **conditional on reaching its D6 roll**. In this engine,
interception is attempted before that roll: the experiment conditions on no
successful interception preventing the launch. It does not predict or multiply
interception survival, movement, activation or blood-lust survival. Catch,
scatter, bounce, eventual possession and turnover are outside the boundary.
Accuracy has the same meaning for empty, friendly-occupied and opposing-occupied
targets. A prospective passer need not currently hold the game's ball.

For raw D6 face `r`, agility target `T = Rules.agility_table[player.get_ag()]`
and actual pass modifier `M`, classification preserves `PassAttempt` order:

1. Accurate if `r == 6` or (`r != 1` and `r + M >= T`).
2. Otherwise fumble if `r == 1` or `r + M <= 1`.
3. Otherwise inaccurate.

Natural one always fumbles; natural six is accurate even under extreme
penalties. Accuracy is checked before a modified-one fumble. For example,
AG6/long has `T=1, M=-1`, so raw two is accurate, while AG3/long has `T=4, M=-1`
and raw two fumbles. Effective AG retains the engine's injury behavior and
1–10 clamp; the agility **target** is not clamped to two for this classifier.
This is the implemented BB2016-style agility engine, including its high-AG
precedence, rather than a claim of complete official-edition conformance.

Modifiers are read from the existing game inputs:

- Quick/short/long/long bomb contribute `+1/0/-1/-2`.
- Each active opposing tackle zone contributes `-1` unless Nerves of Steel.
- Very Sunny contributes `-1`; Nice, Sweltering Heat, Pouring Rain and Blizzard
  contribute zero at this boundary. Blizzard allows only quick/short.
- Accurate contributes `+1`, Strong Arm `+1` except at quick range, Stunty `-1`.
- Each opposing on-pitch player with Disturbing Presence at existing square
  distance at most three contributes `-1`, including prone/non-TZ players.
  Nerves of Steel does not cancel presence, and duplicate skill entries on one
  player do not multiply its penalty.

Safe Throw is rejected, even when the selected range/policy happens to mask its
effect: a failed modified roll may leave the ball held, a fourth physical state.
Non-Ball pieces, TTM, bombs and Hail Mary range are also rejected. Possessing
Hail Mary Pass at an ordinary distance is allowed. There is no Dump-Off mode,
PA characteristic, wildly inaccurate category or catch/interception integration.

| Pass policy | Action after initial inaccurate or fumble |
| --- | --- |
| `never` | Retain it, suppressing even automatic Pass. This can be an analytical counterfactual; it does not promise the engine offers “decline Pass.” |
| `pass_skill` (default) | Replace once using usable Pass; no team fallback. |
| `pass_skill_then_team` | Prefer usable Pass; use an eligible team reroll only when Pass was unavailable before the original roll. |

An initial accurate launch is retained. A replacement is final even if it fails;
there is no team fallback after failed Pass, or third attempt. Pro is declined
throughout, including on a failed Loner check.

The pass record also exposes `ruleset_id="BB2016"`,
`conditions="normal_ball_launch_reached_v1"`, integer `agility_target` and
`modifier`, enum `pass_distance` and `weather`, immutable integer coordinate
tuples `from_position` and `target_position`, and the `reroll` record below.
The condition identifier names this bounded experiment, not an official rules
certification or full state snapshot.

### Typed blocks and reroll resources

The typed block and blitz APIs share the historical block evaluator's exact
selection, correlated direct effects and hypothetical occupancy described below.
`never` is the default. `avoid_attacker_down` first selects **one face for the
complete roll** under `issue8_local_v1`. Only a selected face that directly downs
the attacker triggers an eligible attacker-team reroll. It replaces **all** block
dice, preserving chooser, effects and selection policy, and accepts the new
selection even if worse. A bad face merely present in the original tuple does
not trigger. Negative dice still spend the attacker's resources; the defender's
team never funds this policy, irrespective of home/away roles or `game.actor`.

The trigger is skull or both down without attacker Block. It is not a general
turnover estimator or tactical optimum. Defender-down probability can decrease
while attacker survival improves. Pro and both players' Wrestle are declined;
Block changes effects rather than supplying a reroll. Dump-Off is declined,
Foul Appearance/GFI/activation are conditioned on reaching the block, Dauntless
is conditioned on no strength increase, and Frenzy is the first/single block
only. Follow-up (including Fend), armour/injury, ball continuation, touchdowns
and effects on others in a chain push are excluded. A Juggernaut attacker is
rejected by the typed **blitz** API, regardless of defender skills, because its
automatic Stand Firm cancellation changes geometry. Non-blitz Juggernaut has
no modeled effect. Stand Firm is used; taken-root state already applies.

`BlockOutcomeProbabilities` exposes four overlapping float marginals:
`attacker_down`, `defender_down`, `attacker_ball_loss`, `defender_ball_loss`.
Its `selected_face_probabilities` is a normalized immutable five-float tuple
in `(ATTACKER_DOWN, BOTH_DOWN, PUSH, DEFENDER_STUMBLES, DEFENDER_DOWN)` order.
The two physical push faces are combined only after counting. The four event
marginals must **not** be summed or renormalized.

Its metadata is `ruleset_id="BB2016"`,
`conditions="single_block_direct_effects_v1"`,
`selection_policy="issue8_local_v1"`, integer `signed_dice`,
`chooser="attacker" | "defender"`, `reroll_team="attacker"`, boolean `blitz`,
immutable integer `(x,y)` `attack_position`, and `reroll`.

Every typed result's `RerollProbabilityInfo` contains:

| Field | Meaning |
| --- | --- |
| `policy` | Requested pass/block policy, retained even if no resource can be used. |
| `already_rerolled` | Caller-supplied boolean roll history. |
| `pass_skill_available` | Live `player.can_use_skill(PASS)` before policy/history suppression; always false for blocks. |
| `team_reroll_available` | Live `game.can_use_reroll(actor.team)` before suppression. Actor means passer or block attacker. |
| `source` | Effective `none`, `pass` or `team` after policy/history suppression. |
| `loner_success_probability` | `1/2` only for an effective team source on a Loner; otherwise `1`. |
| `replacement_probability` | Probability a complete replacement roll is actually made. |
| `pass_use_probability` | Probability/expected units of Pass opportunities used on this roll. |
| `team_use_probability` | Probability/expected team rerolls spent, including failed Loner checks. |

Team eligibility requires positive remaining rerolls, no reroll already spent
this turn, the actor's team as `state.current_team`, a current `Turn`, and no
Quick Snap. More than one available reroll still permits at most one modeled
use. Pass availability checks `used_skills`; using Pass here models a roll
opportunity, not an invented once-per-turn debit. The engine records the
rerolled procedure rather than marking Pass used.

A team request spends its resource before an unmodified Loner D6 4+ check.
Failed Loner retains the original outcome and the cost, with Pro declined and
no further draw. Pass is not Loner-gated. `already_rerolled=True` describes the
natural result of a replacement roll with no further reroll: both sources are
suppressed, while live availability and requested policy remain visible. These
pre-roll helpers do not inspect a live procedure or guess history from
`rerolled_procs`; callers supply history explicitly. Missing resources and
suppressed sources are successful base-distribution queries with zero costs.

For example, AG3 short/Nice with no tackle zones starts at `(1/2,1/3,1/6)`.
Usable Pass or non-Loner team replacement gives `(3/4,1/6,1/12)`, replacement
probability `1/2`, and source cost `1/2`. A Loner team source gives
`(5/8,1/4,1/8)`, replacement probability `1/4`, and team cost still `1/2`.
One unskilled block die with Loner/team gives attacker down `2/9`, defender
down `1/2`, replacement `1/6`, and team cost `1/3`.

### Validation and arithmetic

The typed methods raise `ValueError` with explanatory categories (`policy`,
`already_rerolled`, `player`/`players`, `origin`/`target`, `ball`, `ruleset`,
`weather`, `distance`, `Safe Throw`, `Blizzard`, `Juggernaut`, `dice`,
`agility target`, `modifier`) for invalid or unsupported inputs:

- Non-string, unknown or wrong-domain policy; non-bool history (including 0/1).
- Wrong object kinds, foreign/unregistered players or teams, missing/off-pitch
  origin or target, non-integer coordinates (including bool), or out-of-bounds
  coordinates. A player's live board position must belong to that player.
- Same-player/same-team blocks, nonadjacent defenders, occupied hypothetical
  origins; non-integer dice counts (including bool), zero or magnitude over three.
- Pass target equal to origin; non-Ball/unowned Ball or missing/multiple pitch
  balls. Blocks support zero/one pitch Ball; multiple balls are unsupported.
- The pass/skill/range exclusions above; inconsistent configuration/ruleset
  references to `BB2016`, unrecognized weather or malformed non-integer/non-finite
  agility-target/modifier values.

Unchanged shipped `Rules` tables are a precondition. Arbitrary well-typed runtime
table monkeypatches and custom rules are unsupported; no generic table hashing
is performed. Unexpected internal helper exceptions propagate unchanged. The
queries do not refresh actions: standing, activation, action legality and path
feasibility remain caller responsibilities.

All success and failure paths read state and compute locally. No temporary
movement, cache writes, trajectory suspension, resource/skill consumption, RNG
advance, forced-queue consumption, procedure/agent calls, reports or clocks are
allowed. Results retain no mutable game references and repeated calls compare
equal. The historical pass helper also retains these purity guarantees.

Production counts six pass faces or uses the existing block order-statistic
counts. Let `q(s)` be the base pass-event or selected-face probability, `B(s)`
its trigger, `b=sum(q(s) for triggered s)`, and `L` replacement eligibility
(`1` or Loner `1/2`). For an effective source:

`q_final(s) = q(s) * (1 - L*B(s)) + L*b*q(s)`.

Replacement probability is `L*b`, source cost is `b`; without a source the base
distribution is unchanged. Integer numerators/denominators are retained until
final float conversion. There is no sampling, pair enumeration in queries,
probability clipping or separate marginal optimization.

`tests/game/probability_oracles.py` contains the pre-implementation rational
tables and independent symbolic physical-face enumerators. The focused tests
cover ten pass rows/resources/modifiers, twelve signed block tables, bounded
skill/ball/geometry pairs, actual forced procedure boundaries, metadata,
invalid domains, legacy limits, performance and in-query/after-query purity.
The existing 4,608-configuration block matrix also requires exact typed-`never`
equivalence. Arithmetic comparisons use absolute tolerance at most `1e-12`
and zero relative tolerance. Full-suite accounting and backend guards follow
[the ordinary three-job CI gate](ci.md).

### Historical passing compatibility

`get_pass_prob(player, piece, position, allow_pass_reroll=True,
allow_team_reroll=False)` remains a float accuracy-threshold estimate. It is
not catch or whole-pass success. It preserves its positional flags, defaults,
quick/short TTM approximation and previous unsupported-range behavior. It
assumes Pass availability from possession of the skill and does not model
used Pass, Loner or already-rerolled history. Thus used Pass at the P2 boundary
still gives old default `3/4` versus new default `1/2`; Loner/team still gives
old team-enabled `3/4` versus new `pass_skill_then_team` `5/8`. Safe Throw/TTM
are not newly rejected by this historical interface. Dodge, catch, pickup and
pathfinding policies/defaults are unchanged.

## Historical block and blitz APIs

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
