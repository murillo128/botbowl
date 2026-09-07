# Inherited baseline, 7 September 2026

This is observed behavior for issue #4, not a claim of complete rule correctness.
The starting revision is `d62fc114de6c8d891d260b1a1f7091c0574247cd` (upstream
reference `3e550bda3666c39efe478fb9465646af24576665`). Only test support and this
report change. No engine rule, formation, runtime dependency or build code changes.
Bootstrap and its eight workflow labels were already verified in the starting
revision by the epic orchestrator; no runner was registered or exercised here.

## Environment and installation

Linux x86_64, kernel 6.8.0-139-generic, glibc 2.39. CPython 3.11.16 was provisioned
with uv 0.12.10 because the host initially had only Python 3.12.3 on PATH. Python's
own build reports Clang 22.1.3; the extension was built with GCC/G++ 13.3.0.
The exact installed package versions and summarized test evidence are in
[baseline-issue-4.json](baseline-issue-4.json).

The initial environment seeded pip 26.2.1, setuptools 84.0.0 and wheel 0.48.0.
`pip install -r requirements.txt`, `python setup.py build`, and `pip install -e .`
all exited zero. The native compiler emitted a signed/unsigned comparison warning
in generated Cython C++, not a build failure. Material installation findings:

- Requirements install selected Requests 2.31.0. Editable install subsequently
  selected 2.30.0, as required by `setup.py`. The two declarations conflict.
- Requirements pin Packaging 23.1. Wheel 0.48.0 requires Packaging >=24.0.
  Both pip's installation warning and `pip check` exposed that tooling conflict.
  Constraining **only Wheel to 0.40.0** restored a clean `pip check`; engine
  dependencies retain their inherited effective versions. Initial suite evidence
  predates this tooling constraint; final suite evidence follows it.
- No installation failure was observed, and no rule was patched to obtain a
  successful run. The available-compiler build was tested; a truly compiler-free
  package build remains #5's work. The Python reference run removed the compiled
  extension from the import path after building it.

Reproduce in a fresh checkout at the starting revision for the inherited runs,
or at this report's revision for the added fixtures and backend assertions:

```bash
uv venv --python 3.11.16 --seed /tmp/botbowl-baseline-venv
source /tmp/botbowl-baseline-venv/bin/activate
python -m pip install pip==26.2.1 setuptools==84.0.0 wheel==0.48.0
python -m pip install -r requirements.txt
python setup.py build
python -m pip install -e .
python -m pip check  # initial Wheel/Packaging conflict is expected
python -m pip install wheel==0.40.0
python -m pip check
python -m pytest tests/game tests/framework tests/ai --require-pathfinding=native
python -m pytest --require-pathfinding=native
```

For the inherited revision omit `--require-pathfinding`, which is introduced by
this child. On Linux CPython 3.11, run the Python-only reference in a fresh process
with the source extension temporarily moved outside the checkout:

```bash
mkdir -p /tmp/botbowl-baseline-native
mv botbowl/core/pathfinding/cython_pathfinding.cpython-311-x86_64-linux-gnu.so /tmp/botbowl-baseline-native/
python -m pytest tests/game tests/framework tests/ai --require-pathfinding=python
mv /tmp/botbowl-baseline-native/cython_pathfinding.cpython-311-x86_64-linux-gnu.so botbowl/core/pathfinding/
```

Both modules were explicitly imported. Their `get_safest_path.__module__` values
were `botbowl.core.pathfinding.python_pathfinding` and
`botbowl.core.pathfinding.cython_pathfinding`; native `__file__` ended in
`cython_pathfinding.cpython-311-x86_64-linux-gnu.so`. The new backend argument
checks the package's selected implementation and verifies the native extension
suffix before collection. Requesting native with the extension absent, and
requesting Python with native selected, both exited 4 with the actual backend in
the error. A missing native job cannot silently become a Python job.

## Results

| Run | Game | Framework | AI | Other sets | Result |
| --- | --- | --- | --- | --- | --- |
| Inherited native | 217 pass | 14 pass | 102 pass | Not requested | 333 pass, 20.52 s |
| Inherited Python | 217 pass | 14 pass | 63 pass, 1 fail | Not requested | 294 pass, 1 fail, 22.97 s |
| Final Python | 280 pass, 1 xfail | 14 pass | 63 pass, 39 skip | Not requested | 357 pass, 39 skip, 1 xfail |
| Final native, complete suite | 280 pass, 1 xfail | 14 pass | 102 pass | kickoff 41 pass; pregame 112 pass | 549 pass, 1 xfail |

Final run durations and exact commands are recorded in the JSON evidence. All
runs had zero collection/runtime errors; the inherited reference's one failure
and final known xfail are classified below. The final complete native command
also covers all tests in the issue's three required directories.

The inherited Python failure was
`tests/ai/test_pathfinding.py::test_compare_cython_python_paths`: “Cython
pathfinding was not imported.” Another 38 parametrized native cases disappeared
entirely from that run. They now remain collected as explicit skips alongside the
native-only comparison (39 in total). There are no native skips in a required
native run. These skips mean unavailable acceleration, not demonstrated parity.
The existing differential test also compares zipped path lists without checking
cardinality; broader semantic parity belongs to #13.

Gym produced 102 warnings in each required baseline run: deprecated APIs, reset
and step return shape, tuple observations against a Box, and observation-space
membership. Passing those tests is not Gym API conformance. In particular,
`test_reward_and_scripted_wrapper` accumulates rewards/scores without checking
numeric expectations. Those are explicit #17 follow-ups, not weakened here.

## Fixtures and limits

`tests/baseline.py` supplies a fresh size-matched game, semantic action/event
records, a deterministic setup/end-turn policy, and explicit player placement.
`tests/game/test_baseline.py` runs movement, block, pass, reroll, touchdown/new
drive, and natural game-end cases for sizes **1/3/5/7/11**, seeds **0/17**, twice
per case: 60 cases, 120 independently constructed scenario runs. Assertions
check expected locations, knockdown, pass/catch, reroll consumption, score and
receiving-team transition, terminal event, and completed turns. Repeat comparison
retains event type, player/team identity by side/jersey number, coordinates,
dice, counts, and skills; generated UUIDs and timestamps are excluded.

Each scenario allows at most 256 explicit actions, including setup; failures
report size, seed, current procedure and the last 12 actions. A deliberate exhausted
budget verifies that diagnostic. A separate configuration test verifies that
one round per half completes at team turns `[1, 1]` after four END_TURN actions;
the helper configures the engine's `Configuration.rounds` field. Global fixed-dice queue objects are saved and
restored on exit, including exceptions; a cleanup test verifies this. This is
serial test isolation, not interleaved per-game RNG isolation (#14).

Movement, block, pass and reroll use synthetic cleared-board micropositions. On
size 1 the pass deliberately places two teammates to exercise pass geometry; it
is not a legal one-player setup. Drive and end-game probes also traverse actual
setup/lifecycle procedures. Kickoff-table randomness and pathfinding are disabled
in these small semantic fixtures; dedicated inherited tests exercise those
systems. These fixtures do not establish every skill interaction or every legal
match trajectory. Legacy cached helpers and formation-dependent expected values
are unchanged.

[Upstream PR #222](https://github.com/njustesen/botbowl/pull/222), head
`a2b441f8c48248fa566c2c083608f46399fd36b6`, was inspected for its size-aware helper
and explicit-position ideas. Its changes from exact tackle-zone counts to `>= 1`
and from fixed rolls 4 to 6 were not copied. Existing test expectations were not
changed to accommodate a different formation.

The legacy suite still includes unseeded random bots, automatic whole-game calls,
and loops without step budgets. Its durations are observations, not stable
performance measurements. New scenarios use explicit step limits; use an outer
process timeout (300 seconds was used for the final complete suite) for legacy
calls. This timeout cannot replace the decision/liveness contracts in #15/#16.

## Failure and coverage routing

| Evidence or test family | Classification / retained regression | Owning child of #3 |
| --- | --- | --- |
| Conflicting Requests pins; initial Wheel/Packaging `pip check` failure; implicit/cwd-dependent build | Dependency/build/tooling, no functional fix in this child | #5 packaging; #6 constraints and CI |
| Missing native comparison and invisible native parametrizations | Test discovery; explicit skips plus required-backend guard delivered here | #4 baseline; #6 native CI; #13 parity corpus |
| `test_human_game_records_end_time` | **Known functional defect**, strict xfail: explicit human actions reach `game_over`, but `_end_game` copies unset `last_action_time` to `end_time` | #16 lifecycle/timestamps; reported in [issue comment](https://github.com/murillo128/botbowl/issues/16#issuecomment-5566267469) |
| Gym return/space warnings; reward wrapper lacks numeric assertions | Observed warnings and test coverage gap | #17 adapters, spaces, rewards |
| New movement/block/pass/reroll micropositions | Passing baseline semantics, not full interaction coverage | #8 block; #13 paths; #25 interaction corpus; #28 pass/reroll design |
| Drive transition and setup across sizes | Passing bounded baseline; synthetic positions distinguished from legal setup | #11 temporary drive state; #18 formation validation |
| End-game events and bounded action driver; inherited unbounded/random callers | Passing natural outcome assertions; known timestamp defect above; liveness coverage gap | #15 decision driver; #16 liveness; #25 corpus |
| Saved/restored process-global dice queues and fixed-seed repeats | Serial reproducibility only | #14 per-game RNG; #25 isolation/interaction corpus |

The human timestamp regression uses size 1, seed 0, one turn per half and a strict
xfail: fixing it produces an XPASS failure until the owner removes the marker.
The broader end-game scenarios assert natural termination independently, so the
known timestamp defect does not hide the other lifecycle checks. Every observed
failure has an existing owner; no extra child or incidental engine correction
was needed. Raw build/install/test logs and JUnit XML remain outside Git; the
compact JSON records commands, counts, failure identity and environment needed
to reproduce the claims.
