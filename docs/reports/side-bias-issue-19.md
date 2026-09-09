# Bounded home/away investigation

This investigates [issue #19](https://github.com/murillo128/botbowl/issues/19)
on base `5a74bf68a4f37aa0f1386b72896962de12840038`. It changes no game rules,
home/away assignment, scatter, production helper, or shipped example policy.
The source hashes in the accompanying JSON identify the measured harness and
scripted policy; the base identifies all unchanged engine/configuration inputs.

## Full-game results

All 96 games completed within the decision limit, with no invalid actions,
exceptions, or excluded pairs. Each row below is 8 pairs / 16 games; cells
are reported separately and are not pooled into a larger sample.

| Policy | Engine paths | Home / away / draw | Home points | 95% pair bound |
|---|---|---:|---:|---:|
| random-legal | off | 0 / 0 / 16 | 0.5000 | [0.020, 0.980] |
| random-legal | on | 0 / 0 / 16 | 0.5000 | [0.020, 0.980] |
| scripted | off | 3 / 8 / 5 | 0.3438 | [0.000, 0.824] |
| scripted | on | 3 / 8 / 5 | 0.3438 | [0.000, 0.824] |
| scripted-mirrored | off | 6 / 4 / 6 | 0.5625 | [0.082, 1.000] |
| scripted-mirrored | on | 6 / 4 / 6 | 0.5625 | [0.082, 1.000] |

First-receiver strata (8 games from 8 pairs per entry):

| Policy | Engine paths | Receiver home: H / A / D | Receiver away: H / A / D |
|---|---|---:|---:|
| random-legal | off | 0 / 0 / 8 | 0 / 0 / 8 |
| random-legal | on | 0 / 0 / 8 | 0 / 0 / 8 |
| scripted | off | 1 / 4 / 3 | 2 / 4 / 2 |
| scripted | on | 1 / 4 / 3 | 2 / 4 / 2 |
| scripted-mirrored | off | 4 / 0 / 4 | 2 / 4 / 2 |
| scripted-mirrored | on | 4 / 0 / 4 | 2 / 4 / 2 |

Exact receiver-stratum estimates and pair bounds, configuration, all episode
seeds/scores/end causes, kickoff events and trace hashes are in
[side-bias-issue-19.json](side-bias-issue-19.json).

The stock scripted sample reproduces the reported direction of an away-heavy
result under controlled reception. The canonical policy changes the observed
counts, consistent with the exact orientation/order confounder. It changes both
orientation and tie handling, and the broad bounds include 0.5 in every cell:
this is not proof that policy exclusively explains the historical gap, nor
proof of engine symmetry. Random-legal scored no touchdowns and supplies no
sensitive scoring-balance evidence. The on/off settings produced identical
scores in each policy's matched episodes; they are not independent replications.
No rules correction is justified by these measurements.

The deterministic policy defect and proposed bounded child below are retained
independently of aggregate win rate. The investigation narrows confounders and
provides reproducible evidence; it does not claim a fully identified cause for
all historical home/away differences.

Validation on the measured implementation: 25 focused controls pass with the
native backend. The isolated Python core profile passes its 2,528 ordinary
selected cases (2,237 passed, 290 capability skips, one pre-existing expected
failure); its coverage receipt separately lists the 41 investigation cases
excluded by the existing core profile. This is not a claim that those 41 cases
or the separate legacy-RL profile ran. Final-head GitHub lint and both core
backends are tracked on the draft PR, outside this technical evidence.

## Method

`examples/side_bias.py` reloads human rosters, rules, configuration, and both
agents for every episode. Stable A/B roster IDs make semantic traces comparable
without UUID noise. Each pair uses one engine seed, with A home in leg 0 and B
home in leg 1. A/B policy seeds are SHA-256-derived independent streams that
travel with the identity, not the physical side. The game-local engine RNG is
separate. A new scripted agent also means new setup/action queues every game.
Only `random-legal` consumes the derived policy RNG; the scripted policies are
deterministic and their recorded policy seeds are unused.

First reception is an experimental control: retain the coin toss, then choose
kick/receive so A receives for even pairs and B for odd pairs, in both legs.
This balances reception by physical side and identity. It does **not** recreate
the original experiment's natural receive-always policy. Subsequent kickoffs,
weather, injuries, and dice follow the ordinary engine. Each episode records the
configuration including formations, policy/engine seeds, first receiver and
physical side, kickoff events with receiver/half, end cause, action/event counts,
score, and semantic trace digest. Full action/event/dice traces stay outside Git.

The three self-play policies are separate strata:

- `random-legal`: uniformly select a legal action family, then a valid target.
  Setup selects a built-in formation and ends as soon as legal. This is not a
  uniform distribution over all concrete actions, nor the shipped random bot.
- `scripted`: the existing `MyScriptedBot`, with only terminal printing silenced.
- `scripted-mirrored`: that same decision logic in a private canonical home
  view. For an away policy, reflect every square as `(width - 1 - x, y)`, swap
  team/agent side references in the copy, reverse board lookup rows, and map
  the returned action back. Canonical action-position or player-target sorting
  applies on both sides, preserving aligned roll/path metadata. Cached planned actions remain
  canonical and players map back by stable ID. The live game is never reflected.
  This is an explicit orientation/tie-order policy intervention, not a promise
  of identical behavior to the original home policy.

Both engine `pathfinding_enabled` settings are tested. The scripted policy
calls pathfinding directly even when this flag is off; **off does not remove
pathfinding from the policy**. Both measurement cells use the compiled native
backend, recorded separately from that flag. Python and native execute the same
deterministic/unit controls; measurements are not pooled across backends.

Run exact block/endzone controls before extending to full games. The fixed
measurement budget is 8 pairs per cell, three policies × two pathfinding flags,
11-player human rosters, two 8-turn halves, kickoff table enabled, seeds
19000–19007, at most 20,000 external decisions per episode: 96 attempted games.
A separate one-pair scripted timing pilot is not added to the sample.

## Estimand, uncertainty, and limitations

Home points are 1 for a home win, 0 for an away win, and 0.5 for a draw.
Estimate the equal-weight mean of pair means. The report includes counts and
separate first-receiver-home/away strata. Invalid actions, exceptions and
decision limits are explicit non-results; exclude the whole incomplete pair
from estimates and show excluded episode/end-cause counts. Never count a
truncated 0–0 game as a draw.

The reported interval is the conservative distribution-free 95% Hoeffding bound
for independent pair means in [0,1]: mean ± sqrt(log(40)/(2 × pairs)), clipped
to [0,1]. Independent seeded pairs are an assumption about the pseudorandom
experiment, not a property proved about NumPy. Eight pairs give a half-width of
approximately 0.480 before clipping: this budget can expose a reproducible
mechanism but cannot estimate a small advantage precisely. These are pointwise
bounds, not simultaneous inference across cells. No p-value or 50% pass gate is
used. Raw win counts and draws remain visible instead of dropping draws.

Shared engine seeds do not match dice to the same football event. The two sides
can encounter different setup/kickoff effects, action enumeration, blocks,
rerolls and injuries, changing subsequent RNG consumption. Neither equal seeds
nor equal initial RNG states establish an event-wise counterfactual. Seeds are
also reused between policy/flag strata: those rows are dependent comparisons,
not additional independent evidence for an aggregate advantage. No confidence
interval on a between-policy causal difference is claimed. Match-by-match
reflection is not expected for stochastic scatter. Runtime clocks are disabled;
CPU timing is not an experimental outcome.

## Deterministic findings

1. Exact reflection properties pass for all squares on sizes 1, 3, 5, 7, 11:
   involution, pitch boundary, team side, adjacency sets and endzone distance.
   Paired block/blitz/dodge helpers give equal values in reflected fixtures.
   Both the existing and mirrored scripted policies plan to the correct endzone;
   a carried ball on that endzone is a touchdown on both sides.
2. With an attacker at (13,8) and equally strong opponents at (12,8)/(14,8),
   `_get_safest_block` keeps the first adjacent opponent on a probability tie.
   Reflected positions exchange which logical opponent is first, while helper
   values remain equal. The canonical decision view retains the same logical
   opponent. This isolates a policy-order confounder, not an incorrect rule.
3. A separate concrete **scripted policy defect** occurs when a stronger defender
   chooses two uphill block dice containing ATTACKER_DOWN and DEFENDER_DOWN.
   `MyScriptedBot.block` selects DEFENDER_DOWN, knocking down its own player,
   even though ATTACKER_DOWN is legal. The fixture executes the real block with
   game-local forced dice and confirms the chooser on both physical sides.
   This is deterministic and role-dependent; by itself it does not establish
   the origin of an aggregate home/away gap.

Proposed bounded additional child of epic #3: **Make scripted block-die selection
respect the choosing team**. Owner: the scripted example policy, not probability
helpers or engine rules. Reuse `test_defender_die_choice_characterizes_scripted_policy_defect`
as the failing acceptance fixture; require chooser-relative die ranking on both
sides, preserving the existing attacker-choice behavior. Cover negative dice,
Block/Dodge/Tackle and reroll availability with small deterministic tests. Do not
change helpers, scatter, team identity, or use win rate as its acceptance oracle.
No correction is included here; the current test deliberately characterizes the
observed defect pending that bounded contract.

## Reproduction

Use Python 3.11 and the pinned `requirements/core.txt` constraints. From a clean
checkout, create a virtual environment and install the repository-native core:

```sh
python -m pip install -c requirements/core.txt setuptools Cython
python setup.py build
python -m pip install -c requirements/core.txt -e '.[dev,web,competition]'
python -m pytest tests/ai/test_side_bias.py --require-pathfinding=python -q
BOTBOWL_BUILD_NATIVE=1 python setup.py build
BOTBOWL_BUILD_NATIVE=1 python -m pip install --no-build-isolation -c requirements/core.txt -e .
python -m pytest tests/ai/test_side_bias.py --require-pathfinding=native -q
python -m examples.side_bias --pairs 8 --seed 19000 --policy scripted \
  --pathfinding off --output /tmp/side-bias-scripted-off
```

Repeat the last command for `random-legal`, `scripted`, `scripted-mirrored` and
`off`/`on`, each with a fresh output directory. The CLI writes a streaming
`episodes.jsonl`, individual traces, `environment.json`, and `summary.json`.
It exits nonzero for an incomplete episode. To recompute a summary without
rerunning games:

```python
import json
from pathlib import Path
from examples.side_bias import dumps, summarize
rows = [json.loads(line) for line in Path('/tmp/side-bias-scripted-off/episodes.jsonl').read_text().splitlines()]
print(dumps(summarize(rows)))
```

Full local evidence is under `/tmp/botbowl-issue19-evidence/`. The compact JSON
keeps all episode seeds, scores, termination, trace digests, kickoff records and
the generated summaries, sufficient to audit every count and recompute them.
Independent methodology/confounder review remains a separate pending gate.
