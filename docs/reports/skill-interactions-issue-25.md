# Issue #25: bounded skill interaction corpus

## Resumption on the reviewed composition

Integration `8ad8a3cde1a3b5fd391d9527047a8ccb4bb5d16f` was merged additively
into the existing issue branch. It includes #9's independently reviewed Push
correction. The unchanged reproduction below now passes all six cases in each
fresh installed backend. The historical report is retained below without
relabeling its original failures or exact-base suite as new evidence.

The [support inventory](skill-support-issue-25.md) covers every current `Skill`
entry and separates implementation from test evidence: 62 implemented paths,
3 partial, 14 unsupported. This corpus adds no engine changes, unsupported skill
implementation, property-testing dependency, seed exclusion or xfail. Explicit
action replay and the existing reduced Push regression provide useful reduction
without adding a property-testing library for these bounded cases.

## Corpus and invariant boundary

`tests/issue25/test_interactions.py` is ordinary pytest discovery. Its 70 tests
include 24 both-side mechanism variants, four naturally reached complete games
and their action replays, changed-order recipe replay, interleaved games with
all four foreign forced-die queues pending, and negative/limit controls.

| Mechanism | New bounded exercise | Remaining limits |
| --- | --- | --- |
| Rerolls / movement / Shadowing | GFI failure → team reroll or Sure Feet → dodge → opposing Shadowing accept/decline → pickup; both sides, pathfinding off/on | Not every agility/mutation combination |
| Push / Stand Firm | Strip Ball versus Sure Hands plus Stand Firm accept/decline; carried ball follows defender | Existing #9 tests own chain pushes, crowd, Frenzy and Side Step branches |
| Injury / Apothecary | Mighty Blow versus Thick Skull, opposing Apothecary use/decline, resource and dugout results | Existing #12 tests own casualty/Decay/Regeneration branches; unchanged six-case Push regression retained explicitly |
| Negatraits / drive | Actual BB2016 Human Ogre role (Bone Head/Loner), fail → successful/failed Loner team reroll → real MOVE touchdown → cleared drive state | Scorer and Ogre placement are synthetic; other negatraits remain in existing dedicated tests |
| Natural sequences | Shipped Human rosters, legal setup formations, independently seeded choice/target policy, both teams, terminal games | Bounded turns and seeds; random games do not prove skill coverage |

Natural cases start from loaded `gym-N`/BB2016 inputs with kickoff events enabled;
no synthetic board changes or forced dice. Synthetic cases record the legal
setup prefix, named fixture boundaries with complete state snapshots, action
journal and exact forced-roll scopes. Human linemen use compatible General or
doubles advancements. The Ogre roster variant replaces one sample lineman with
the actual Human Ogre role before Game construction; it does not invent a
Bone Head/Loner lineman. The scoring MOVE enters the real touchdown/drive stack.

At each decision the checker asserts board ↔ player occupancy, canonical roster
and team indexes, disjoint dugouts, ball bounds/unique carrier, nonnegative
integer resources in their actual domains, valid actors and all enabled
advertised action targets. Pregame `StartGame` has not populated reserves yet;
airborne players are not board occupants; allocated crowd cells can be occupied;
`ball.on_ground` is not assumed to be the inverse of `is_carried`. Signed stat
modifiers are not incorrectly treated as resources. Bomb interactions are outside
these fixtures. Checking invariants and selecting policy actions must preserve
the game's RNG/queue snapshot.

Progress fingerprints exclude reports, time and RNG churn, and include the
semantic state, procedure names and used skills. All cases use 1,500 decisions,
2,000 engine steps per decision, 16 visits per semantic state and 90 seconds per
probe. Four visits proved too strict for legitimate randomly selected START/UNDO
cycles; sixteen is applied uniformly, including the originally failing size-7
seed-0 case. Limits are administrative failures, never manufactured game results.
Controls cover a stalled `advance`, a real repeated START_MOVE/UNDO cycle,
occupancy/roster/ball/resource/actor corruption, invalid limit values, exhausted
decision/engine/time limits and a stuck engine interrupted by the POSIX timer.
Other platforms retain cooperative wall checks and the engine/decision bounds;
the separate job timeout remains necessary for a hard process limit.

Natural journals replay serialized actions without consulting the generating
policy. Synthetic replay calls the named fixture recipe and requires exact
equality of actions, forced scopes and fixture states. It does not pretend
synthetic placements were legal decisions. Failures retain seed/configuration,
attempted actions, fixture states, forced queues, limits and current options/stack
as JSON in `CorpusFailure`; pytest logs preserve that reproduction. For a natural
record use `tests.interaction_corpus.replay(record)`; for a synthetic failure rerun
its named scenario/side/seed/variant from `tests.interaction_scenarios`. Successful
synthetic records also pass through `replay` with journal equality checks.

The serialized pathfinding replay correction normalizes reproduction records to
the JSON representation before comparison, including tuple-valued path rolls.
Six regression cases cover both sides and all three movement variants with
pathfinding enabled, checking saved and in-memory replay. Five negative controls
first accept an unchanged saved journal, then require rejection of altered
actions, forced-scope boundaries, forced rolls, fixture state or pathfinding
rolls. The full journal comparison and engine behavior remain unchanged.
Correction validation logs and exact-head receipts are retained separately under
`/tmp/botbowl-issue25-correction-evidence/`.

## Separate broad invocation and evidence

The explicitly targeted `broad_corpus.py` is not selected by ordinary filename
discovery. Run it as a separate job in each installed backend, from the copied
test suite (never from a path that can import checkout code):

```sh
timeout 600 /path/to/installed/bin/python -m pytest tests/issue25/broad_corpus.py \
  --require-pathfinding=python -q -s --junitxml=/tmp/issue25-broad.xml
# Use --require-pathfinding=native with the separately installed native wheel.
```

Its 66 cases comprise 30 generated games (sizes 1/3/5/7/11, seeds 0/3/17,
pathfinding off/on, two turns per half) with full action replays, plus 36 both-side
mechanism cases at seeds 3/17. Paths are actually exposed/consumed by movement
choices with `pathfinding_directly_to_adjacent=False`; installed backend checks
require the real native extension when native is requested. No universal Python/
native path ordering or all-skill parity is inferred from backend identity.

Resumption logs, installed artifacts, JUnit and deterministic four-shard receipts
belong under `/tmp/botbowl-issue25-resume-evidence/`. Final-head counts, source SHA
and CI run IDs are recorded in the PR/issue handoff, after execution. Required
gates remain lint and core CPython 3.11 Python/native. Selection verification and
four-process overlap must pass before relying on each suite. The accepted
41-node Quick Snap/forward-model investigation and legacy RL capability boundary
are unchanged; no new failures are hidden by the harness. Fresh independent
review must inspect invariants, generation, limits, recipe/action replay and
the exact final published head before readiness.

---

# Historical report: stopped at the Push/injury boundary

Execution base: `ad63891a745e908313596c24a3a11fe8c2a5bda6`.
This is a product-defect reproduction and incomplete-work report, not acceptance
of the transversal corpus. The execution instruction requires stopping when a
genuine product defect belongs to another issue. No engine fix is included.

## Reproduction

Run from the repository root in the normal development environment:

```sh
python -m pytest tests/issue25/reproduce_push_injury.py -q -s
```

This explicitly targeted file is outside ordinary pytest discovery by filename.
It remains a failing regression, without xfail, retries, or a passing assertion
of the defective result. It is not an expensive investigation added to every
ordinary profile. The short ordinary #25 corpus remains unfinished.

The seed is 0, configuration `gym-11` / BB2016, two rounds per half, kickoff table
enabled and pathfinding disabled. Legal decisions reach a normal turn. A clearly
synthetic fixture then clears the pitch and puts two Human linemen at `(5,5)` and
`(6,5)`, removing them from reserves. The attacker has Block and Mighty Blow; the
defender has Thick Skull and, in one variant, Stand Firm. These skills are legal
General/doubles advancements, not mutually exclusive traits. The defender owns
one Apothecary and neither team has a reroll.

The reduced suffix is:

1. `START_BLOCK(attacker)`.
2. `BLOCK(6,5)` with a forced `DEFENDER_DOWN`.
3. `SELECT_DEFENDER_DOWN`.
4. `PUSH(7,5)`, then `FOLLOW_UP(5,5)`; alternatively, `USE_SKILL(defender)` for
   Stand Firm instead of those two decisions.

Both routes consume D6 values `[6,6,4,4]`. Armour 12 leaves Mighty Blow available.
Injury 8 + Mighty Blow 1 - Thick Skull 1 should be a KO and offer Apothecary.
Observed: `STUNNED`, no Apothecary decision, and no attacker on the armour/injury
reports. Both teams reproduce this. A three-decision `BOTH_DOWN` control uses the
attacker's Block and reaches Apothecary with the same D6 values and skill pair.
The test prints the entire setup/action prefix, seed/configuration, forced
queues, semantic events, procedure stack and options. The fixed-roll helper
also verifies exact consumption and cleanup without advancing the game RNG.

`Push.step` creates `KnockDown` without `inflictor=self.pusher`; so does
`Push.finish_without_push`. The Both Down branch of `Block` supplies the
attacker. `KnockDown` passes its inflictor to `Armor`, which passes it to `Injury`;
losing it suppresses Mighty Blow before the #12 Apothecary boundary. Route the
finding primarily to #9's Push ownership, with #12 copied for injury semantics.
Crowd injury is a separate rule and must not automatically inherit the attacker.
The owner should decide the bounded correction and any broader regression cases.

## Evidence

Linux, CPython 3.11.16; full raw logs, JUnit, receipts, frozen dependencies,
installed wheels and unfinished source are under `/tmp/botbowl-issue25-evidence/`.

| Check against the exact base | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Full ordinary core, installed Python wheel | 2,407 | 0 | 290 |
| Full ordinary core, installed native wheel | 2,688 | 0 | 9 |
| Explicit reproduction, installed Python | 2 | 4 | 0 |
| Explicit reproduction, installed native | 2 | 4 | 0 |

Both ordinary profiles used `tools/ci/run_profile.py suite --backend BACKEND
--output DIRECTORY`, exported the exact base and ran all four `core_selection.PARTS`
concurrently. Each discovered 2,738 nodes and executed 2,697 ordinary nodes, with
the 41 existing Quick Snap investigation nodes retained separately by the
amended policy. Neither legacy RL nor that investigation was claimed as ordinary
core execution. Python skips are optional native/RL capabilities; native skips
are optional RL capabilities. There were no errors or xfails.

Evidence names: `base-{python,native}/{result,coverage,collection}.json`, per-shard
logs/XML/receipts, `reproduction-{python,native}-installed.log`,
`reproduction-python-installed.xml`, and `reproduction-native.xml`.
An initial native invocation from the source test path correctly failed its
backend assertion because pytest selected checkout Python; it is retained as
`reproduction-native.log`. The corrected installed invocation copied only the
reproduction test into the native profile's suite and required native before
collection. The scenario itself does not use pathfinding; no path parity claim
is made from this reproduction.

The parallel process controls passed all 28 cases, including the barrier that
requires all four shards to start before any completes. Core selection positive
and 18 negative controls passed. Error lint passed for the reproduction. The
workflow and CI runner are unchanged: lint plus Python 3.11 python/native remain
the gates. Logs are `parallel-controls.log`, `selection-controls.log` and
`reproduction-lint.log`.

## Mechanisms and pending work

The initial uncommitted generator smoke reached terminal games at `(size,seed)`
`(1,0)`, `(3,17)`, `(11,3)`. Synthetic GFI/team reroll/Shadowing/pickup and
Strip Ball/Sure Hands/Stand Firm probes passed for home, seed 0. The next probe,
Mighty Blow/Thick Skull/Apothecary, exposed the defect above. These are development
observations, not a completed fixed corpus or proof of full skill coverage.
Sources and patches are preserved in `unfinished-corpus/`; initial outputs are
`generated-smoke.log` and `mechanism-smoke.log`.

| Mechanism | Existing source/tests inspected | Remaining #25 acceptance |
| --- | --- | --- |
| Rerolls and movement/Shadowing | `test_reroll.py`, `test_shadowing.py`, `Reroll`, `Shadowing` | Both-side fixed cases, variants and pathfinder execution |
| Push and Stand Firm | `test_push.py`, `Push`, `FollowUp` | Owner correction above, then compatible pair corpus |
| Injury and Apothecary | `test_injury.py`, `test_apothecary.py`, `Armor`, `Injury` | Failing pair above, resource/event checks and decline variants |
| Negatraits and drive | `test_negatraits.py`, `test_drive_states.py` | Roster-compatible paired scenarios and actual drive transitions |
| RNG and queue isolation | `test_rng_isolation.py`, `only_fixed_rolls` | Corpus interleaving and changed-order equality |
| State invariants and progress | Initial checker/generator preserved locally | Controlled occupancy corruption/nonprogress controls, complete replay diagnostics, step/time-limit validation |
| Skill support inventory | `Skill`, `docs/features.md`, rules capability catalogue | Full implemented/partial/unsupported inventory with current test evidence |

The feature table is historical, not a substitute for that inventory. Enum
presence is not implementation. The already accepted #30 limitation that
`kick_off_table=False` still schedules inherited kickoff events was encountered
in the first smoke and recorded; subsequent fixtures use the shipped enabled
configuration. It is not a new #25 defect or a filtered event.

The broader explicitly invoked corpus, final short ordinary corpus, complete
inventory, negative controls, execution-order checks and fresh independent
review are pending. No property-testing dependency was added. Resume only after
the product boundary is resolved by its owner and execution is authorized on the
appropriate composed base.
