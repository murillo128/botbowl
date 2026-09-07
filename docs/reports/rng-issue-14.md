# Game-local dice and RNG replay, issue #14

## Accepted integration refresh, 7 September 2026

The integration base is `23d8c09a5822157a510174c9f0c6bfd3501f74f0`
(`codex/epic-3/integration/base-accepted-23d8c09`). It is adopted by an additive
merge into the existing issue branch, preserving the original unreviewed
`744e91a03165474ee253df33b92f2ef7c112e55a` and its predecessors. The base contains
accepted #4/#7/#5/#13. This is the current-integration base policy authorized by
the parent; the RNG contract and DAG dependencies are unchanged.

The sole textual merge conflict was the `dataclasses` import in `game.py`:
retaining `field` supports `GameCheckpoint` alongside `ActionValidationResult`.
The only new compatibility edit changes two `bb.BBDie.fix(result)` calls in
`test_disabled_block_choice_and_gym_mask_wait_for_reroll` to
`game.dice.fix(bb.BBDie, result)`. A byte comparison against the accepted test
confirms every other byte is preserved: all three block results, the one team
reroll, both decisions, Gym mask checks and all assertions. Frenzy and Strip Ball
retain the accepted explicit reroll decisions through the automatic merge.
An AST audit finds no remaining class-global forced-dice hooks in engine, tests
or examples. Pathfinding source/tests, `core/util.py`, the forward-model test,
packaging declarations and CI workflow match the accepted base exactly.

### New local evidence

All new raw evidence is under `/tmp/botbowl-issue14-integrated-evidence`.
The old directory `/tmp/botbowl-issue14-evidence` is preserved; its results below
belong to the original revisions and are not integration results. The parent's
accepted-base native result, **1016 passed / 1 known xfail**, is supplied parent
evidence, not a baseline suite rerun by this executor.

A fresh local export of exact accepted base `23d8c09` reproduces shared-roll
theft and third-fixture clearing for D3/D6/D8/BBDie, using the original reproduction
script and the new runtime with Python pathfinding (`accepted-base-defect.log`).
Before the two compatibility replacements, both parameterizations fail with
`AttributeError: BBDie.fix`; the required native selection subsequently passes both.
No rolls, resources, actions, assertions, skips or xfails were weakened.

| Integration check | Observed result | Log/XML prefix |
| --- | --- | --- |
| Before compatibility migration, block-choice regression | 2 failed, 50 deselected; 0.11 s | `before-migration-native` |
| Required native selection | 674 passed, no skips; 551.79 s | `required-native` |
| Required Python selection | 395 passed, 279 native-only skips; 286.03 s | `required-python` |
| Complete native suite | 1095 passed, 1 known xfail, no skips; 327.67 s | `full-native` |
| Complete Python suite | 816 passed, 279 native-only skips, 1 known xfail; 187.46 s | `full-python` |

Both required selections include all 79 RNG acceptance cases, dice, reroll,
forward model, `env.seed`, action validation, pathfinding, pass, pickup, player
action, scripted bot, Frenzy and Strip Ball. Each required run retains 12 Gym
warnings. Both full runs retain 102 Gym warnings and the unchanged strict
timestamp xfail `test_human_game_records_end_time` (#16).

The separate known unequal-key `compare_iterable` defect is still reproducible
(`known-comparison-defect.log`) and remains assigned to
[#20, comment 5567725149](https://github.com/murillo128/botbowl/issues/20#issuecomment-5567725149).
This full Python run did not encounter it; its success does not establish a fix.
The integrated helper file has SHA-256
`3210d61aa823bfbb8e97789748533463131c25e890b8314521b1a2c43c6e3fbd`.
It differs from the earlier #13 report's whole-file hash because accepted #5
changed package resource lookup; its comparison implementation is preserved.

### Runtime, build and exact source attribution

The new isolated runtime is `/tmp/botbowl-issue14-integrated-venv`: CPython
3.11.16, NumPy 1.26.4, pytest 9.1.1, Gym 0.26.2, Linux x86_64/glibc 2.39.
The direct build uses setuptools 79.0.1, Cython 3.3.0 and GCC/G++ 13.3.0.
`packages.txt` records the installed versions; `native-editable.log` records the
separate isolated PEP 660 build. The accepted packaging line supplies Python
3.11+ and explicit native build selection; the legacy requirements snapshot
is not used for this local refresh.

```bash
/tmp/botbowl-issue14-venv/bin/python -m venv /tmp/botbowl-issue14-integrated-venv
source /tmp/botbowl-issue14-integrated-venv/bin/activate
python -m pip install pip==26.2.1 'setuptools>=77,<85' 'Cython>=3.2,<4'
BOTBOWL_BUILD_NATIVE=1 python setup.py build
BOTBOWL_BUILD_NATIVE=1 python -m pip install -e '.[dev,web,rl,competition,render]'
python -m pip check
python -m compileall -q botbowl tests
timeout 900 python -m pytest --require-pathfinding=native
```

Before building, a `/proc/*/maps` check found no process loading this worktree's
extension. Old build output, metadata and extension were moved to
`previous-build-artifacts/` in the new evidence directory. The extension was
actually rebuilt here, then loaded from this worktree by native checks.
Build, editable installation, `pip check` and `compileall` pass. No parent or
sibling worktree, runtime or artifact was modified.

| Loaded artifact or build source | SHA-256 |
| --- | --- |
| `cython_pathfinding.cpython-311-x86_64-linux-gnu.so` | `2b1273106f25eca9926ffee001f6d80fb42f47c16a58f8571ca29cf60473ce05` |
| `cython_pathfinding.pyx` | `319271103f09c13434598362d315cca5028f701d2acaccdc65e652bef95787c8` |
| `python_pathfinding.py` | `fa464899dad38a2bc215b799355f2b44c62c66f3d2e2a1190b6448a138a8e615` |

Paths in the table are under `botbowl/core/pathfinding/`. The Python reference
is a copy of tracked source at `python-source/` in the new evidence directory,
containing no extension. It runs in a fresh process with that directory as cwd
and `PYTHONPATH`, using the same issue-owned runtime and
`timeout 600 python -m pytest --require-pathfinding=python`. The loaded Python
module is verified to belong to that copy. Each required/full run has a JSON
command/runtime/backend/artifact record and a SHA-256 manifest of source bytes;
the native and Python source/test/build inputs match. The report is updated
after runs, without changing those tested inputs. `run_check.py` retains exact
selections, timestamps, exits and time bounds. Durations are observations, not
performance claims; the full native limit is 900 s after the required selection
took 552 s. Hosted CI is separate and must be attributed to the published head.

## Original evidence at `744e91a`

The remainder records the original implementation and checks before integration.
The starting revision was `036073d69abbbbc7c2bf03434f162ff5ed687036`, containing
accepted baseline #4 and its CI patch. That change isolates test dice and adds
in-memory RNG/checkpoint APIs. It does not change the RNG algorithm, game rules,
packaging, pathfinding implementation, or the known lifecycle timestamp defect.

### Inherited defect

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

### Delivered behavior and coverage

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

### Reproduction and environment

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

### Limits

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
