# Quick Snap / forward-model investigation

Source: accepted `a9a931a4618ea4b3cb45e92ccbffdf23e97bcc6b`. The historical
setup control also runs on accepted baseline #4,
`26989d87ac95c8354c68dc08e2d39aad4d995e9e`. No production file, inherited test,
expectation, or xfail is changed by this investigation.

The reported Quick Snap `player.position is None` crash **was not reproduced**
in the configurations below. Its risk remains unresolved. Three other findings
are distinguishable: an inherited setup undo defect, an inherited comparison
helper defect, and a controlled example-search hash collision. None establishes
the historical crash's cause.

## Bounded Quick Snap experiment

`tests/framework/test_quick_snap_forward_model.py` constructs independent games
through START_GAME, HEADS, the required KICK/RECEIVE decision, stock defensive
Spread and offensive Wedge formations, and END_SETUP. Both setups are checked
for legality and exact on-pitch count. Each game loads its own teams/configuration;
stable roster IDs permit comparison. Following the isolation correction below,
each game receives a deep copy of a fixed, detached rules blueprint explicitly
through `Game(ruleset=...)`. The stock XML loader runs once per ruleset name in
a private function namespace with an explicit empty-container RuleSet constructor.
The complete result is detached, including nested model defaults. Ordinary loader
history cannot contribute definitions; the production loader and its defaults
are unchanged.

The matrix is the Cartesian product of:

- Stock `gym-1` and `gym-3`, with human rosters: 1 or 3 players on each side.
- Home and away receiving Quick Snap.
- `pathfinding_enabled=False` and `True`.
- Game seeds 0, 3, 17; a separate `random.Random(seed + 1000)` selects committed
  decisions. The original seed-3 setup control intentionally retains its original
  shared game/policy sampling order instead.

This is **24 configurations per backend**, run against Python and compiled native
pathfinding. The test starts before PLACE_BALL and forces the kickoff event,
not a synthetic Turn stack. Forced dice are D8=2 for scatter direction; gym-1
uses D6=[1,4,5], and gym-3 uses D3=[1], D6=[4,5]. Natural randomness resumes
after those queues are consumed. A central unoccupied receiving-side target is
chosen deterministically. Actual initial states and every attempted decision
are retained in the optional evidence files.

At each visited Quick Snap decision, all available player activations, square
targets, END_PLAYER_TURN, UNDO, and END_TURN alternatives are explored separately.
Each alternative reconstructs a fresh clean game and replays the committed
prefix plus that alternative. It is then executed three times, checking each
advance, legacy revert, forward, and explicit checkpoint restore. Choices are
regenerated twice at each visited decision. The committed policy prefers movement
and has a hard 24-decision Quick Snap budget. This is a bounded set of all
one-action alternatives along each selected prefix, **not exhaustive search of
all multi-action sequences**.

The sequence covers entry into Quick Snap, movement/activation completion, exit,
any pending kickoff touchback selection, and the first ordinary START_MOVE.
Quick Snap itself uses adjacent-move actions with either pathfinding setting;
the ordinary activation asserts that paths actually exist when pathfinding is
enabled. Finally the complete multi-decision sequence is reverted, forwarded,
checkpoint-restored, and replayed against another independent clean execution
three times, checking each transition.

## Observability and equivalence

The observer compares complete public state fields, full rules/player/team definitions,
procedure type/order and public fields (including context, flags, paths and
steps), action choices, reports, board, dugouts, ball state and activation state.
It normalizes roster references to stable IDs, immutable squares to coordinates,
NumPy values and reversible/plain container representations. Public class
defaults are included whether trajectory has copied them onto the instance or
they still live on the class. Paths are compared through all public result data
(steps, rolls, probability and terminal metadata), not internal search caches.
Dictionary keys are compared as well as values, avoiding the inherited helper.

Independent consistency checks require canonical player/team references through
the object graph; unique board occupants; both directions of the board/position
mapping; canonical square references for on-pitch players; game references on
procedures; and a single shared ball object across pitch and pending procedures.
In Quick Snap they also check the receiving/current/active team, available
START_MOVE players, active-player/procedure identity, non-None active position,
and exact adjacent MOVE target set with zero movement used and no roll/path data.
Undo also checks original player, stack and available-action-list identities.

RNG is checked separately under #14's explicit contract. Legacy `revert/forward`
must retain the advanced RNG/forced queues. `restore_checkpoint` must restore
the captured RNG/queues. Clean controls receive the same decisions and their
final RNG must match. Clocks, Game wall-time fields, policy state, persistent
replays and trajectory bookkeeping are outside semantic equality, as documented
in `docs/forward-model.md`. No legacy-RNG restoration assumption is made.

Five extra tests cover four side/pathfinding combinations of stale START_MOVE
rejection and detached-player canonicalization, plus an observer control that
deliberately introduces a missing position, a foreign board player reference,
and equal-length dictionaries with different Square keys. The corruption checks
must fail; the key change must produce a semantic difference. There is no
None-position skip and no new expected failure in the default suite.

## Fixture-isolation correction after failed review

The original head `47593b6af2afc8ae3a014faf79c8401a55443efc` failed the
issue's explicit criterion, “Repetir consultas y ramificaciones sin compartir
objetos mutables.” The published [issue FAIL](https://github.com/murillo128/botbowl/issues/20#issuecomment-5569428623)
and [PR FAIL](https://github.com/murillo128/botbowl/pull/86#issuecomment-5569429606)
remain authoritative evidence of that head's acceptance gap. Full review and
original reproduction are preserved at `/tmp/botbowl-review20-tvGvPV/review.md`
and `/tmp/botbowl-review20-tvGvPV/shared_ruleset_repro.py`.

All six RuleSet containers (`races`, `star_players`, `inducements`, `spp_actions`,
`spp_levels`, `improvements`) were shared. Constructing a second fixture grew the
first game's race/star/inducement counts from 48/142/16 to 96/284/32 while the
original semantic observer remained unchanged. This was independently reproduced
on both backends, and reproduced again before this correction in isolated runtimes.

The correction is confined to the test harness. Every fixture copies the detached
blueprint, including nested rule definitions, and supplies it to Game explicitly.
Rules now participate in semantic equality. A separate identity traversal covers
instance attributes, containers, arrays, rules and trajectory history, and checks
the primary, committed-control and alternative-control games before and after
branch activity. Reconstructed whole-sequence controls receive the same checks.
The traversal does not exclude rules or other Game fields. Scalars, enum values
and callable code are leaves; tuple/frozenset contents are traversed. Native
objects without an instance dictionary are checked by identity as opaque leaves.

Six new regression cases check each RuleSet container: constructing and advancing
a second game leaves the first game's full semantics and RNG unchanged; clearing
the second container is observable only there; a third game remains equivalent
to the first. A seventh control deliberately shares a nested roles list while
semantic values remain equal and requires the identity observer to reject it.
The six isolation cases **fail before** the fixture correction and pass afterward
on both backends. Fresh-process rule counts now stay at 24/71/8 (with 5/7/6 entries
in the three dictionaries), with no shared containers. The original semantic,
reference, legal-setup, empty-square and stale-action controls remain in place.

Correction evidence is isolated under
`/tmp/botbowl-issue20-correction-9kfhevsv/`: `evidence/*-original-sharing.json`,
`evidence/*-regression-before.log`, `evidence/test_with_regression_before_fix.py`,
`evidence/*-regression-after.log`, and `evidence/*-isolation-proof.json` retain the
failure and correction proof. `evidence/*-matrix/` retains refreshed initial
snapshots and transition hashes, which now include rules. The accompanying JSON's
`correction` section attributes that correction's evidence and source hashes separately from
its unchanged original-head records. Original files under
`/tmp/botbowl-issue20-evidence/` and reviewer environments are preserved.

This fixture correction does not resolve the original Quick Snap crash or apply
any of the distinct production/helper/search proposals below. Fresh independent
re-review remains required before parent acceptance and integration.

## Ordinary-order fixture correction

The second independent review of `cd248094aae55f0ebbaf2c5083f4da29c0e4e565`
confirmed that the sharing finding above was resolved, but found a distinct
ordinary-order failure. After 288 passing tests and 279 capability skips, the
first fixture copied 7,488 races, 20,418 stars and 2,306 inducements accumulated
by earlier loader calls. Complete semantic observation rose from 0.00653 seconds
in a fresh process to 2.714 seconds. Hosted CI run 34114477569 exceeded its
unchanged ten-minute limit. The [second FAIL](https://github.com/murillo128/botbowl/issues/20#issuecomment-5569881534),
`/tmp/botbowl-review20-corrected-9cg3_tep/review.md`, `order_probe.py` and its
original prefix evidence remain preserved; standalone timings did not establish
normal-order viability at that head.

The fixture now uses the existing parser's code and dependencies in a private
function namespace, supplying a constructor with six fresh containers. It does
not replace module globals, modify constructor defaults, duplicate the XML
parser, or copy previously loaded records. A final deepcopy detaches nested
model defaults before the blueprint is cached. Each game still deep-copies that
blueprint and all rules remain included in every complete semantic observation.
Consistency checks return that observation for immediate comparison, avoiding
duplicate traversal at the same transition. No observation is cached across a
transition; public class defaults and dynamically added fields remain observable.
Dictionary entries are still fully encoded. Ordering uses the encoded key's
representation and its delimiter, retaining the original full-entry ordering
when distinct Python keys normalize to the same key. This avoids stringifying
entire nested values merely to sort entries with distinct keys. A mixed-key
control checks the original ordering, reversed insertion order, and changed
values, including a Square/tuple normalization tie. Exact Python scalars take
a fast path while NumPy values retain their existing conversion.

The new regression runs before the matrix in default order. It clears a warm
blueprint cache, performs three ordinary loader calls, adds distinct dictionary
sentinels, and checks the canonical counts 24/71/8 and 5/7/6. It then compares
cached and cold reconstruction after another loader call, including full semantic
equality, unchanged RNG, and disjoint graphs against the inherited defaults and
both blueprints. It restores the inherited containers afterward. Before this
correction it fails with counts 96/284/32 and 6/8/7; afterward it passes alongside
the seven original rule isolation/alias controls and the corruption observer.

Current-source evidence is recorded separately under `order_correction` in the
accompanying JSON and `/tmp/botbowl-issue20-order-correction/`. Earlier evidence
sections retain their original source attribution. This correction changes only
the fixture/instrument and its regression; the matrix, production source,
inherited tests, skip/xfail policy, and hosted CI configuration are unchanged.

Final validation uses `final-python-src` and `final-native-src` in that evidence
directory, with the same tracked execution source and a native extension freshly
built from unchanged production sources earlier in this correction:

| Final-source validation | Python | Native |
| --- | --- | --- |
| Focused investigation plus existing forward-model tests | 38 + 6 passed | 38 + 6 passed |
| Normal-order full suite | 854 passed, 279 capability skips, 1 inherited xfail | 1,133 passed, no skips, 1 inherited xfail |
| First-matrix prefix passing tests | 289 (plus 279 capability skips) | 568 |
| Complete observation after prefix | 0.00548 s | 0.00608 s |

Both prefixes retain canonical counts and the fresh fixture's snapshot hash.
Each final matrix covers 24 configurations and 10,208 checked transitions; all
48 focused files match the previous correction byte-for-byte, as do their final
normal-order full-suite counterparts. Executor replays of the unchanged prior
reviewer challenge script pass 244 graph comparisons, 45 first-game stability
checks, 13 nested semantic faults and 12 deliberate aliases per backend. These
replays are validation, not a fresh independent review verdict. All ten refreshed
setup/helper/consumer controls retain their original finding JSON.

Final focused runs took 330.64 s Python and 330.22 s native; full runs took
618.99 s and 808.75 s respectively. Native spent 384.71 s in preceding inherited
tests, versus 176.68 s for Python, before the matrix. These local timings do not
substitute for the required successful exact-head hosted CI run. Logs/XML,
commands, source/evidence manifests and initial dependency failures are retained.
The successful pre-optimization full suites (853 Python passes and 1,132 native
passes, each with the inherited xfail and Python's 279 capability skips) retain
their separate observer-source hash. The original Quick Snap risk and separate
follow-up proposals remain unresolved pending parent acceptance and ownership.

## Reproduced setup defect

The unchanged `packaging-forward-model-reproduction.py` exits 1 at seed 3,
zero-based step 167, SETUP_FORMATION_ZONE. Its JSON is byte-identical on the
accepted investigation base and baseline #4 in isolated CPython 3.11.16 runtimes.
The earlier #5 controls on CPython 3.14 remain documented in
`packaging-issue-5.md`; this investigation does not claim a new 3.14 run.

An observer around the unchanged script retains all **335 external decisions**
(167 advance/revert/advance pairs and the final failing probe). The final Setup
has `reorganize=True`: Perfect Defence. Its formation performs swaps
`(6,12),(7,12),(8,12),(1,1),(2,2),(3,3),(4,4),(9,12),(10,12),(11,12),(12,12)`
and creates **zero MovementSteps**. Away jerseys 6–12 retain the reported rotated
positions after undo.

The minimal reduction uses legal gym-3 formations and forces Perfect Defence
(D6=2+2), then issues **one** legal PLACE_PLAYER onto another player's occupied
square. Both sides reproduce at seed 0:

| Reorganizing side | Placement | Original occupants | After checkpoint restore |
| --- | --- | --- | --- |
| Home | jersey 1 to (9,4) | jersey 1=(9,2), jersey 3=(9,4) | jersey 1=(9,4), jersey 3=(9,2) |
| Away | jersey 1 to (4,4) | jersey 1=(4,2), jersey 3=(4,4) | jersey 1=(4,4), jersey 3=(4,2) |

Each action records two trajectory entries but zero MovementSteps. RNG restores
exactly, and the board and player positions remain mutually consistent in the
wrong swapped state. This demonstrates why position/reference consistency alone
is insufficient: equivalence with the original state is also required. A legal
placement onto an empty square round-trips on both sides as a negative control.

Cause: `Setup.step(PLACE_PLAYER)` calls `Game.swap` for an occupied square.
`Game.swap` directly assigns player positions and board cells. `Player.position`
and `Pitch.board` are explicitly ignored by generic reversibility; the swap does
not supply the MovementSteps used by `Game.move/remove/put`. The formation
reduction and the one-action reduction reach this same untracked swap path.

Bounded correction proposal, requiring an explicit ownership/implementation
decision: make player/player swaps record reversible changes for both original
occupants and positions, including self-swap behavior. Preserve existing clean
ball/carrier behavior and validate legacy undo/redo plus checkpoint replay and
both-side setup formation cases. Affected implementation would be
`botbowl/core/game.py::Game.swap`, possibly a dedicated step in
`botbowl/core/forward_model.py`, and focused setup/forward-model tests. The
probability-query owner also works in game.py, so this proposal is not applied
incidentally here. It does not propose making position-less players disappear
from Quick Snap action generation.

## Comparison helper and search-consumer controls

`compare_iterable({Square(1,2): 1}, {Square(10,8): 1})` raises KeyError on the
accepted source, confirming #13's comment. Equal dictionary lengths do not imply
equal keys; the helper indexes the second dictionary without checking. This is
an observability defect and can occur before undo is tested. A separate bounded
proposal would compare dictionary key sets in `botbowl/core/util.py` and add a
focused utility regression. No helper correction is included.

The accepted example MCTS caches decisions by `examples/hash_example.py`'s
approximate `gamestate_hash`. In gym-3 Quick Snap, activating jersey 1, reverting
the root checkpoint, then activating jersey 3 produces the same example hash
but different semantic state and legal MOVE targets, on either receiving side.
The hash includes action types and report count but omits the active player,
available targets, report content and full procedure state.

A MOVE cached from the first branch to (8,3) for home or (7,2) for away is invalid
in the second branch. Public `Game.step` rejects it with `invalid_target` while
state and RNG remain unchanged. Separately, the tests show that a detached player
with matching IDs is normalized to this game's canonical player; a repeated
START_MOVE is rejected while an activation is already in progress and becomes
legal again after root restoration. Therefore action reuse must be evaluated
against the current decision state, not object age alone.

The hash collision is a concrete consumer-cache risk consistent with the example
search architecture. It is **not a reproduction of the upstream MCTS crash**:
that historical run's seed, decisions and exact version are unavailable. No MCTS
rollout or large game corpus is substituted for them. If adopted separately, a
consumer correction would concern `examples/hash_example.py`,
`examples/mcts_example.py` and focused cache-equivalence tests. Neither an engine
fix nor a new epic dependency is inferred from this finding.

## Reproduction and results

From the repository root with an installed development environment:

```sh
BOTBOWL_ISSUE20_EVIDENCE=/tmp/botbowl-issue20-correction-replay/python \
  python -m pytest tests/framework/test_quick_snap_forward_model.py -q --require-pathfinding python
PYTHONPATH=. python docs/reports/quick-snap-diagnosis.py setup --side 0
PYTHONPATH=. python docs/reports/quick-snap-diagnosis.py setup --side 1
PYTHONPATH=. python docs/reports/quick-snap-diagnosis.py helper
PYTHONPATH=. python docs/reports/quick-snap-diagnosis.py consumer --side 0
PYTHONPATH=. python docs/reports/quick-snap-diagnosis.py consumer --side 1
PYTHONPATH=. python docs/reports/quick-snap-diagnosis.py original \
  --trace /tmp/botbowl-issue20-correction-replay/original-full-trace.json
```

`setup`, `helper`, and `original` intentionally exit **1** on the observed
defects. The consumer controls exit **0** after checking the collision and safe
rejection. For native testing, install a native build and require `native`;
the backend assertion fails before collection if the expected implementation
was not loaded. The native runtime/source and historical baseline runtime/source
are isolated under `/tmp/botbowl-issue20-*`.

Validation totals and configuration counts are recorded in the accompanying
`quick-snap-issue-20.json`:

| Validation | Result |
| --- | --- |
| Prior isolation-correction focused Python target | 36 passed |
| Prior isolation-correction focused native target | 36 passed |
| Prior isolation-correction existing forward-model target | 6 passed on each backend |
| Original focused Python target (before isolation correction) | 29 passed |
| Original focused native target (before isolation correction) | 29 passed |
| Existing Python suite (focused file excluded) | 816 passed, 279 native-only skips, 1 inherited timestamp xfail |
| Native full run | 1,124 passed, 1 inherited timestamp xfail |

Each backend's final matrix checks 10,208 game transitions, 240 repeated queries
and 24 initial fixtures. It executes 3,026 advances, including 774 one-action
round-trip probes repeated three times and 72 whole-sequence round trips.
There are no observed semantic/RNG divergences in those configurations.
The corrected matrices retain every original committed/attempted decision,
transition label and trajectory step, and all previously observed initial gameplay
fields. All 48 evidence files were refreshed because snapshots and transition
hashes now include rules. The corrected setup/consumer/helper outputs match the
original finding JSON on both backends, including legal empty-square/RNG controls
and mutation-free stale-target rejection. For that prior isolation correction,
each focused run took about eight minutes locally. Its native extension was
reused byte-for-byte from the original isolated build; all 1,333 tracked execution
source/data paths matched each correction runtime. That evidence did not include
a new native build or an exact-source full suite. Current ordinary-order results
are attributed separately above and in the JSON's `order_correction` section.

The historical native full run used the 29-test observer revision preceding the final
ball-reference/branch-state identity assertions and expanded evidence capture.
Its existing engine and tests are unchanged from the accepted base. The final
29-test file at the original head was separately rerun against both backends;
both source hashes are retained in the JSON. These full-suite results are reused
historical evidence, not an exact-source full-suite run of the isolation correction.
The passing unseeded inherited suite does not negate the preserved seeded setup
failure. All runs use CPython 3.11.16; focused runtime versions include NumPy
1.26.4, pytest 9.1.1 and untangle 1.2.1.

Full logs, per-transition hashes, initial states,
decisions and first-divergence snapshots remain outside Git under
`/tmp/botbowl-issue20-evidence/`. The two initial observer-development failures
(NumPy formation arrays and class-default normalization) and the first historical
control's mixed-version Gym entry-point failure are preserved there. They were
instrument/runtime failures; their corrections did not change engine behavior or
weaken equality. The isolated historical rerun preserves the original engine
failure exactly.

The investigation supports a bounded evidence deliverable with the original
Quick Snap risk unresolved. Accepting or implementing the separate correction
proposals requires an explicit subsequent scope/ownership decision; a passing
matrix is not proof that the reported crash was fixed or cannot occur.
