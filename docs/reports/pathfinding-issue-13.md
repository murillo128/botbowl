# Handoff and pathfinding queries, 7 September 2026

The source baseline is `036073d69abbbbc7c2bf03434f162ff5ed687036`: accepted
baseline #4 (`26989d87ac95c8354c68dc08e2d39aad4d995e9e`) plus the authorized
CI-only patch. No sibling implementation is included.

## Behavior and origin

Both backends recognize a handoff that has already started, require actual ball
possession for handoff paths, and allow the terminal recipient square after the
last GFI. An opponent or receiver unable to catch remains an invalid handoff
destination. Python also handles an absent ball, matching the native backend.
Existing touchdown, forced-pickup, airborne-ball and foul behavior is retained.

The action-start condition is selectively adapted from
[upstream PR #234](https://github.com/njustesen/botbowl/pull/234), by Mattias
Bermell (`mrbermell`), head `73848b881e969690d1df98007b74e7faf1448282`.
The upstream authorship headers remain intact. The regression extends that PR's
non-None assertion to recipient, steps, rolls and probability. No other upstream
patch is imported.

The three convenience queries share an exception-safe temporary-state helper.
`from_position` and `num_moves_used` work independently; `None` preserves the
current value and explicit zero resets used movement. The default for
`get_safest_path` is now `None`, matching its documented effective behavior and
the other queries. Starting hypothetically on a ground ball assumes its pickup
succeeded, as in the existing bot tutorial. Invalid overrides are rejected before
mutation. Player/ball identities, position, movement, posture and trajectory
are restored without deepcopy or replacing live state objects.

`Path.prob` retains the existing meaning: the probability of the movement/pickup
portion, excluding the terminal handoff catch. `handoff_roll` records that catch
target. The rain/Catch tests check this distinction and the separate catch
probability used by [the bot tutorial](../bots-ii.md). No catch reroll is charged
to movement probability and queries execute no game actions.

## Coverage and observed results

All probability comparisons added here use absolute tolerance **1e-9**, with
relative tolerance zero. The fixed seed-17 corpus crosses both teams, sizes
1/3/5/7/11, nice weather/blizzard, team reroll off/on and Sure Feet off/on:
80 positions, tested against each backend's expected straight paths with
zero/one/two GFIs, plus 80 direct backend comparisons of every returned path.
Those comparisons require equal cardinality, destination sets, exact steps,
rolls, terminal metadata and probabilities. These are synthetic micropositions,
not a claim of exhaustive skill or equal-cost-route equivalence on every board.
The inherited obstacle comparison remains and now checks cardinality and numeric
probability instead of rounded strings.

Additional tests cover available/unavailable/started handoffs, rain, Catch,
opponents, prone receivers, missing/loose/absent balls, boundary destinations,
handoffs at exhausted movement, all three hypothetical query APIs, independent
overrides, explicit zero, unchanged starting square, prone players and simulated
pickups. Whole-game pickle snapshots include RNG, fixtures and trajectory before
and after successful queries and injected exceptions, with the forward model
enabled and disabled. Explicit identity checks cover the live player state,
ball and action-log list. The overwritten forced-pickup test is collected again;
the airborne-ball test has its own name. Existing touchdown tests still run.

| Run | Observed result |
| --- | --- |
| Exact baseline source with final regression tests | **114 failed, 48 passed**, no skips; 4.88 s |
| Final native focused tests, excluding the fixed corpus | **240 passed**; 4.42 s |
| Final full native suite | **949 passed, 1 inherited xfail**, no skips; 250.25 s |
| Final native required acceptance tests | **514 passed**, no skips; 81.05 s |
| Compiler-free Python required acceptance tests | **235 passed, 279 explicit native skips**; 17.07 s |
| Full compiler-free Python suite | **669 passed, 279 native skips, 1 inherited xfail, 1 failure**; 108.19 s |

The required acceptance selection is pathfinding, pass, pickup, player action and
scripted bot. Its Python pathfinding portion is 201 passed and 279 native skips.
The full native suite covers all 480 pathfinding cases, including both imported
implementations and the direct comparisons. A missing native module fails
`--require-pathfinding=native` before collection (exit 4); requesting Python when
native is selected also fails (exit 4). Python skips do not establish parity.
`compileall` and `pip check` pass. The existing CI file is unchanged.

The full Python failure is the unseeded inherited
`tests/framework/test_forward_model.py::test_revert_and_forward`: comparing
different path dictionaries of equal length raises `KeyError` in
`botbowl/core/util.py:131`. A deterministic reduction reproduces on both baseline
and current source, whose helper SHA-256 is identical:
`51c2445ea64bfec5eb026dbaf377536779650cf40e22f3a5aa07a1f24df806af`.

```python
from botbowl.core.model import Square
from botbowl.core.util import compare_iterable
compare_iterable({Square(1, 2): 1}, {Square(10, 8): 1})  # inherited KeyError
```

This is retained as an out-of-scope comparison-helper finding for the epic's
forward-model work; no assertion, helper, skip or xfail was changed to conceal it.
The required Python selection passes independently. The inherited timestamp
xfail and 102 legacy Gym warnings remain in both complete suites.

## Reproduction and evidence

Linux x86_64/glibc 2.39, CPython 3.11.16, GCC/G++ 13.3.0, Cython 3.0.0b2,
NumPy 1.24.3, pytest 7.3.1. Environment:
`/tmp/botbowl-issue13-venv`; raw logs, JUnit XML, package versions, backend module
identities and the comparison reduction: `/tmp/botbowl-issue13-evidence`.
No binary or bulky log is committed. The baseline reproduction lives at
`/tmp/botbowl-issue13-baseline`, and the compiler-free snapshot at
`/tmp/botbowl-issue13-python`. The latter's changed source/test bytes were checked
against the implementation worktree. Native imports end in
`cython_pathfinding.cpython-311-x86_64-linux-gnu.so`.

Bootstrap an isolated environment using the baseline report's versions:

```bash
uv venv --python 3.11.16 --seed /tmp/botbowl-issue13-venv
source /tmp/botbowl-issue13-venv/bin/activate
python -m pip install -r requirements.txt wheel==0.40.0
python setup.py build
python -m pip install -e .
python -m pip check
timeout 300 python -m pytest -q --require-pathfinding=native
```

For baseline regression, archive the exact baseline source, copy only the final
`tests/ai/test_pathfinding.py` into it, build there, then run:

```bash
python -m pytest tests/ai/test_pathfinding.py -q --require-pathfinding=native \
  -k 'handoff_query or hypothetical or forced_pickup or airborne_ball'
```

For reference validation, use a source snapshot without the extension or build
products. Run from that snapshot with `PATH=/tmp/botbowl-issue13-venv/bin`, using
absolute `/usr/bin/timeout` if needed. This run verified that native module
discovery and `shutil.which` for gcc/g++/cc/c++ all return None; it does not invoke
the package build or a compiler. Run the full suite with
`python -m pytest -q --require-pathfinding=python`, and the required selection:

```bash
python -m pytest -q --require-pathfinding=python \
  tests/ai/test_pathfinding.py tests/game/test_pass.py tests/game/test_pickup.py \
  tests/game/test_player_action.py tests/ai/test_scripted_bot.py
```

Final evidence files are `final-build.log`, `focused-native.{log,xml}`,
`full-native.{log,xml}`, `full-python.{log,xml}`, `required-native.{log,xml}`,
`required-python.{log,xml}`,
`baseline-final-regressions.{log,xml}`, the two `*-environment.txt` records,
`required-native-absent.log`, and `compare-{baseline,current}.log` with
`compare-diagnostic.py`. Exploratory logs are retained separately: one early
corpus process was invalidated by rebuilding its loaded extension and crashed;
it is not correctness evidence. Final runs keep the native binary fixed for their
entire lifetime. Early test-fixture corrections are superseded by the final
baseline reproduction using the same final test file.
