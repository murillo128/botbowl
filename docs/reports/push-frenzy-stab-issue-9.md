# Chain pushes and Frenzy/Stab (#9)

## Rule interpretation recorded before implementation

The execution base is `1153797c2346fd2d6360e60e0af4c85bcc218a1b`.
`botbowl/data/config/gym-11.json` selects `BB2016`; the repository's
`docs/bot-bowl-iii.md` also identifies that ruleset. The relevant legacy skill
wording is in Games Workshop's [Competition Rules Pack](https://cdn.steamstatic.com/steam/apps/58520/manuals/Blood_Bowl_Competition_Rules.pdf?t=1678959819),
Frenzy (printed p. 65 / PDF p. 39), Stab and Stand Firm (printed p. 67 /
PDF p. 41), and the block-declaration FAQ (printed p. 77 / PDF p. 51).
This is the pre-2020 rule text underlying these implemented skills; the later
2020/Second Season FAQ is not an input to this change.

Interpretation of those skill texts together: Stand Firm stops the selected
chain, including predecessors; retain knockdown at the original square. No
follow-up enters an occupied square. Declining permits normal push resolution.
A qualifying first block permits one Frenzy attack against the same standing,
adjacent opponent. Stab may replace that attack, chosen before block dice or
Dauntless; an initial Stab never triggers Frenzy. Each Blitz attack costs one
movement, including GFI when necessary; no budget means no second attack. A
failed GFI prevents the attack. Stab ends the activation, including a Blitz,
and beats (not equals) armour; its injury ignores modifiers. A second ordinary
block permits remaining Blitz movement. These are implementation expectations
inferred from the cited rules, not a claim that PR #151 is rules authority.

## Original failures

Reproduced on the exact base with an isolated CPython 3.11.16 environment and
strict game-owned dice (`tests.util.only_fixed_rolls`, accepted #14):

- [Upstream #244](https://github.com/njustesen/botbowl/issues/244): a selected
  two-player chain ending in Stand Firm reaches `Game.move` with the destination
  still occupied and raises `AssertionError`.
- [Upstream PR #151](https://github.com/njustesen/botbowl/pull/151): after a first
  Frenzy push, `Block` immediately consumes second-block dice instead of offering
  Stab. A strict queue containing only the first block raises
  `ForcedRollExhausted`. The author's [material warning](https://github.com/njustesen/botbowl/pull/151#issuecomment-860107868)
  says that draft permits Stab against anyone; its body also says Blitz is
  missing and Throw Team Mate changes are accidentally included. None of that
  patch is imported.

Full baseline traces and the standalone reproducer are retained under
`/tmp/botbowl-issue9-evidence/` (`baseline-chain.log`, `baseline-frenzy.log`,
`reproduce.py`). Deterministic regression tests accompany the fixes.

## Implementation and regression coverage

`Push` checks whether the selected destination was vacated after the nested push
finishes. If it remains occupied, displacement stops through the preceding
chain; the original block's knockdown or Strip Ball still resolves in place.
Displacement reports now describe completed movement. The accepted #8 push
geometry and Side Step selection code are unchanged.

`Frenzy` continues the existing Block/Blitz activation, retaining the first
`Block` and its exact defender. Eligibility is evaluated after the first block's
push, follow-up and damage. Only push/stumbles results with both players standing
and adjacent qualify. The continuation offers Block/Stab only to a stabber,
charges the second Blitz movement at selection, and delegates GFI and attack
resolution to the existing procedures. It subclasses `BlockAction` so ProcBot
uses its existing player-action dispatch. No extra activation is started.

Stab's damage path uses armour strictly greater than AV (Stakes excepted),
unmodified injury, and Foul Appearance (also specified on printed p. 65 of the
cited rule pack). These existing-path defects were exposed by the required
initial/second-attack damage tests. The new injury flag defaults off for every
other caller. No Throw Team Mate implementation is changed.

The three focused files contain 108 tests (103 added):

- `test_push.py`: short/long chains in both directions; Stand Firm use/decline;
  Side Step with free squares/full occupancy; both sidelines; knockdown and
  Strip Ball on a stopped chain; Take Root as another immovable tail. Assertions
  cover board/player identity, carried/loose ball position, follow-up legality,
  remaining stack, subsequent actions and one activation end.
- `test_frenzy.py`: both attack choices and activation types; unrelated targets
  and late Stab rejected; normal/first/second GFI costs and exhausted movement;
  first/second failed GFI; Fend, knockdown, Both Down, Foul Appearance and Stand
  Firm eligibility; stopped long chains; ProcBot dispatch and reversible replay.
- `test_stab.py`: initial and second attacks; armour below/equal/above AV;
  Mighty Blow, Thick Skull, Stunty, niggling injuries, Stakes and Foul Appearance;
  initial GFI with both pathfinding settings; casualties, ball release and
  rejection of another attack/activation.

All new attack and push sequences use strict game-owned controlled dice. A fresh
export of the execution base, overlaid only with these final three test files,
produces **78 failed, 30 passed**, without missing-name or fixture-type failures
(`regressions-on-base-final.log`). This complements the two standalone baseline
reproductions above. Early development logs are retained, including corrected
fixture mistakes (same-square placement, running-clock comparisons and D68
casualty dice); they are not counted as engine regressions.

## Validation

Isolated CPython **3.11.16**. Full profiles exported committed implementation
`d3521bee6a33a13ff3e4e1692130af0a47f3b390`, built fresh wheels under `/tmp`,
installed into separate environments, and checked installed-package/backend
identity and dependencies. No shared native-extension mutation was required.
The only later test change names the existing `BlockAction` base class in the
failed-first-GFI stack assertion, allowing that test to run on the old source
as well. All 108 final focused tests were then run against both installed wheels.

| Check | Passed | Skipped | Expected failures | Failed/errors |
| --- | ---: | ---: | ---: | ---: |
| Affected skills, block, Throw Team Mate, probability and damage tests | 350 | 0 | 0 | 0 |
| Final push/Frenzy/Stab tests, installed Python wheel | 108 | 0 | 0 | 0 |
| Final push/Frenzy/Stab tests, installed native wheel | 108 | 0 | 0 | 0 |
| Python profile: unit | 853 | 9 | 1 | 0 |
| Python profile: integration | 443 | 279 | 0 | 0 |
| Native + RL profile: unit | 862 | 0 | 1 | 0 |
| Native + RL profile: integration | 740 | 0 | 0 | 0 |

The earlier 350-test affected-skill run preceded two added reversible-replay
cases; those are included in the full profiles and final 108-test runs. The
expected failure is the base's issue #16 human-game `end_time` test. Python
skips comprise 279 optional-native integration cases, seven Gym import cases,
and two tests identifying Gym as a separate capability. The native/RL profile
runs all of those capabilities. Its integration log retains **102 warnings**,
including Gym observation-space checker warnings; a passing exit code does not
claim that these warnings have been resolved.

Commands (run from the issue worktree; outputs must not already exist):

```sh
/tmp/botbowl-epic3-wave6-21-tools/bin/python tools/ci/run_profile.py suite \
  --backend python --output /tmp/botbowl-issue9-evidence/suite-python
/tmp/botbowl-epic3-wave6-21-tools/bin/python tools/ci/run_profile.py suite \
  --rl --backend native --output /tmp/botbowl-issue9-evidence/suite-native-rl
```

Each output contains `result.json`, unit/integration JUnit XML, full build/test
logs, dependency freeze and installed identity. `final-focused-python.log` and
`final-focused-native.log` (and matching XML) capture the final focused runs
outside the checkout. `affected-skills-final.log` records the broader focused
selection. Repository CI error lint (`ruff check --select E9,F63,F7,F82 botbowl
tests examples tools setup.py`) and `git diff --check` also pass.

No local claim is made for other Python versions, platforms, or the separate
`artifacts`/`extra` profiles. Independent technical review must still assess the
rule interpretation and complete transition at the published final head.
