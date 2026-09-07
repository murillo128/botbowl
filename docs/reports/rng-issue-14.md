# Game-local dice and RNG replay, issue #14

The starting revision was `036073d69abbbbc7c2bf03434f162ff5ed687036`, containing
accepted baseline #4 and its CI patch. This change isolates test dice and adds
in-memory RNG/checkpoint APIs. It does not change the RNG algorithm, game rules,
packaging, pathfinding implementation, or the known lifecycle timestamp defect.

## Inherited defect

Before editing, two games were created through `get_game_turn()`. For each die,
the lower result was queued for A and the higher for B through the old class
hooks. Rolling B first consumed A's result. Calling `get_game_turn()` again then
cleared B's remaining result, including when that fixture returned a cached copy.

| Die | Intended A | Intended B | Observed first B | Queue after third fixture |
| --- | --- | --- | --- | --- |
| D3 | 1 | 3 | 1 | empty |
| D6 | 1 | 6 | 1 | empty |
| D8 | 1 | 8 | 1 | empty |
| BBDie | ATTACKER_DOWN | DEFENDER_DOWN | ATTACKER_DOWN | empty |

The reproduction script and output are retained outside Git as
`/tmp/botbowl-issue14-evidence/inherited_defect.py` and `inherited-defect.log`.
Its assertions confirm the defect on the starting revision; they are not
acceptance assertions for the new API. The new
`tests/framework/test_rng_isolation.py::test_interleaved_games_and_third_fixture`
checks the corrected behavior for all four dice, including natural continuation.

## Delivered behavior and coverage

`Game.dice` owns queues and the existing NumPy `RandomState`. `Game.rng` exposes
that same stream for non-dice randomness. All 53 direct engine die constructors
and three string-based dice paths use the game source. Block selection constructs
an already chosen result directly, so reporting it cannot consume a future roll.
Test injections now name their game/source. Fixture creation never clears another
game's queues; the baseline fixture scopes only its own rolls.

The 79 new acceptance cases cover interleaving, fixture creation, nested contexts,
exception cleanup, integer/enum domains and atomic rejection, strict exhaustion,
natural fallback, string dice, seeded equivalence to NumPy, cached Gaussian
restoration, independent copied/transferred queues, and independent execution
under both `spawn` and `fork` on this Linux host. A complete B movement/reroll
trace is identical with or without A drawing and creating fixtures between B's
explicit decisions.

The checkpoint replay starts before four explicit movement/reroll decisions,
forces the initial GFI failure, consumes natural randomness on subsequent GFIs,
then restores and repeats the same actions, reports, compared game state,
trajectory position and final RNG/queues. Separate checks retain legacy
`revert`/`forward` state-only behavior, reject foreign/discarded checkpoints and
expired contexts, preserve policy RNG state, and hide test queues in competition
copies. Existing dice, reroll, forward-model and environment-seed tests are
included in focused and complete verification.

An intermediate full run exposed 38 failures from three missed
`DiceRoll.from_string` call sites for scatter/throw-in distances. Routing those
through `game.dice` corrected all 39 focused scatter/throw-in cases. Four new
string-dice cases also check that `ForcedRollExhausted` propagates without being
misreported as a dice-format error. No existing outcome assertions or xfail/skip
markers were weakened to obtain the final results. The intermediate log/XML
remain available as `incomplete-migration-native.*`.

## Reproduction and environment

Linux x86_64; CPython 3.11.16, NumPy 1.24.3, pytest 7.3.1, Cython 3.0.0b2,
GCC/G++ 13.3.0. The isolated runtime is `/tmp/botbowl-issue14-venv`;
logs, JUnit XML, package versions and the reference-run driver are under
`/tmp/botbowl-issue14-evidence`. No other issue's environment or editable install
was modified. Setup followed [the accepted baseline](baseline-issue-4.md):

```bash
/tmp/botbowl-issue4-tools/bin/uv venv --python 3.11.16 --seed /tmp/botbowl-issue14-venv
source /tmp/botbowl-issue14-venv/bin/activate
python -m pip install pip==26.2.1 setuptools==84.0.0 wheel==0.40.0
python -m pip install -r requirements.txt
python setup.py build
python -m pip install -e .
python -m pip check
python -m compileall -q botbowl tests
timeout 300 python -m pytest --require-pathfinding=native
```

Wheel 0.40.0 is the baseline's tooling constraint for Packaging 23.1; editable
installation selects the existing setup.py Requests 2.30.0 pin. `pip check` is
clean. No dependency declaration was changed. The build produced the native
extension in this worktree; generated build artifacts remain untracked/ignored.
The Python reference uses a new test process with that extension moved to
`/tmp/botbowl-issue14-native`, restored in a `finally` block, and runs:

```bash
timeout 300 python -m pytest --require-pathfinding=python
```

Both full runs use the baseline's required-backend guard. `compileall` passed.
Changed Python files also parsed with Python 3.8 grammar; this is a syntax check,
not a Python 3.8 runtime test. Raw logs and dependency details are local evidence,
not hosted CI results. The PR's hosted checks must be assessed separately.

| Local check | Result | Log/XML prefix |
| --- | --- | --- |
| Starting revision, complete native suite | 549 passed, 1 xfailed; 51.55 s | `before-native` |
| Initial dice/reroll/armor/injury/baseline/forward-model/env.seed focus | 95 passed, 1 xfailed; 67.08 s | `initial-focused` |
| Initial isolation tests, before four string-dice cases | 75 passed; 14.25 s | `isolation-first` |
| Corrected scatter/throw-in focus | 39 passed; 1.87 s | `scatter-correction` |
| Final complete native suite | 628 passed, 1 xfailed; 57.21 s | `after-native` |
| Final complete Python-reference suite | 589 passed, 39 skipped, 1 xfailed; 64.89 s | `after-python` |

The final complete suites include all 79 new acceptance cases. They have no
failures or collection/runtime errors. Both full runs retain the same 102
inherited Gym warnings. The 39 Python-only skips are the baseline's explicit
native-unavailable cases; the native run has no such skips. The one strict xfail
is `tests/game/test_baseline.py::test_human_game_records_end_time`, owned by #16.
It is unchanged. Durations are observations under concurrent host load, not
performance benchmarks; legacy full-suite calls still use an outer 300 s timeout.

## Limits

[The API documentation](../forward-model.md) defines the exact scope. Snapshots
are in-memory objects, not a persistent, safe, or cross-version/process snapshot
format. Process tests construct games within workers; they do not establish safe
snapshot transport. RNG restoration inside forced contexts requires the same
active scopes. Checkpoints apply to live ancestors of one enabled trajectory;
wall clocks, persistent replay recording and external policy/I/O state are not
rewound. A caller must supply the same decisions or restore its policy separately.

The removed global `FixedRolls`, die-class `fix`, and `BBDie.clear_fixes` hooks
require migration to `game.dice`. Natural die constructors still accept ordinary
NumPy RNGs. No claims are made about every possible rule interaction or thread
safety for simultaneous mutation of a single game.
