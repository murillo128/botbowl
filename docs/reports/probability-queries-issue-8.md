# Finite probability queries, issue #8

## Scope and contract

Implementation starts from accepted integration base
`a9a931a4618ea4b3cb45e92ccbffdf23e97bcc6b`, containing completed
#4/#5/#7/#13/#14. Source changes are confined to probability queries and their
position/push helpers in `botbowl/core/game.py`. New acceptance tests live in
`tests/game/test_probability_queries.py` and the durable API contract is
[Probability queries](../probability-queries.md).

The four legacy returns are the attacker/defender knockdown (including defender
crowd removal) and attacker/defender ball-release marginals of one selected
outcome. The documented symmetric chooser policy orders own safety, opponent
knockdown, own possession, then opponent ball release; face and destination ties
are explicit. This policy is a conditional local estimate, not strategic
optimization. There are no new reroll strategies or engine-selected decisions.
The API document explicitly limits unmodelled skills, activation/path rolls,
Frenzy, chain continuations and eventual possession.

Production counts winning ranks of six physical die faces using integer order
statistics, with magnitude 1/2/3 and the sign selecting the chooser. The test
oracle separately enumerates all `6**n` rolls, resolves symbolic faces with sets,
uses successive preference filters, and accumulates rational event counts. It
calls no production face evaluator or probability/ranking helper. The 72 matrix
tests cover 4,608 combinations of signed dice count, carrier, geometry and six
skill flags (396,288 enumerated rolls). Hand counts separately pin `1/216`,
unskilled one-die Both Down, and correlated two-die marginals.

Side Step only uses empty in-bounds neighbours and falls back to ordinary
push/crowd/chain directions when surrounded. The query handles Grab's direct
choice separately from the shared engine fallback so Grab cannot leak into
chain pushes. Stand Firm prevents movement but not knockdown or Strip Ball;
Sure Hands prevents stripping, not knockdown release. Hypothetical origins are
read explicitly, with no live player/ball movement or rollback. Blitz includes
Horns and origin-dependent assists.

The 60 success/induced-exception purity cases inspect the whole pickled game,
RNG/forced queues, live object identities and trajectory entries from *inside*
the calculation and again afterward, with forward model both off/on. They also
cover carried balls on either player, nested strict dice contexts, Gaussian RNG
cache, and independent agent RNG attributes. Six further cases check invalid
origins/targets. No assertion uses the known-defective #20 comparison helper.

## Baseline reproduction and intermediate corrections

`/tmp/botbowl-issue8-base` is a local archive of the exact accepted base. The
probe asserts the imported package is in that export. `base-reproduction.json`
records the original three-dice self-down value `0.027777777777777762`
(approximately `1/36`, rather than `1/216`), missing Both Down contribution to
one-die defender knockdown, empty surrounded Side Step destinations followed by
`IndexError`, and moved player/carried-ball state after induced blitz/dodge
exceptions. The latter adds three trajectory entries when tracing is enabled;
RNG is unchanged in those original reproductions. The first probe's exact
floating-point `== 1/36` assertion failed; the retained `base-reproduction-first`
files record that harness failure. It was corrected to a `1e-15` comparison;
no game behavior was waived.

The initial new test run had 169 passes and two failures: a manually constructed
Grab-cancelled `Push` had no active player in its fixture. Setting the active
player before procedure construction corrected the setup without changing
engine code or assertions. The corrected run passed 171 cases.

A subsequent targeted check exposed direct Grab destinations leaking into the
shared chain-push fallback in the intermediate implementation. The retained
`grab-boundary-before` log/XML has one failure. Restricting Grab choices to the
direct query corrected the defect; the durable regression checks both ordinary
chain directions and direct Grab's in-bounds choice at a sideline. Final query
coverage is 172 passes. These executor validation checks are not the required
independent mathematical/technical review.

## Runtime and validation

All runtime/evidence/source-export writes belong to this issue under
`/tmp/botbowl-issue8-*`; no sibling environment or loaded extension was rebuilt.
The runtime is `/tmp/botbowl-issue8-venv`, CPython 3.11.16 with NumPy 1.26.4.
`packages.txt` records exact installed versions. Setup followed the accepted
package's build contract:

```bash
/tmp/botbowl-issue4-tools/bin/uv venv --python 3.11.16 --seed /tmp/botbowl-issue8-venv
uv pip install --python /tmp/botbowl-issue8-venv/bin/python 'setuptools>=77,<85' 'Cython>=3.2,<4' 'numpy==1.26.4'
BOTBOWL_BUILD_NATIVE=1 python setup.py build
BOTBOWL_BUILD_NATIVE=1 uv pip install --python /tmp/botbowl-issue8-venv/bin/python -e '.[dev,web,rl,competition,render]' 'numpy==1.26.4'
uv pip check --python /tmp/botbowl-issue8-venv/bin/python
python -m compileall -q botbowl tests
```

Here `python` is the issue runtime and `uv` is
`/tmp/botbowl-issue4-tools/bin/uv`. Build/editable installation, dependency check,
compileall, changed-file Python 3.8 grammar parsing, and `git diff --check` pass.
The grammar check is not a Python 3.8 runtime claim; this accepted base's
packaging contract requires Python 3.11+.

The required native selection before the Grab boundary correction passed
**664 tests** in 109.62 s: probability queries, `tests/game/test_block.py`,
`test_strip_ball.py`, `tests/ai/test_pathfinding.py` and `test_scripted_bot.py`.
It is retained as `required-native-before-grab-boundary.*` (also at the original
`required-native.*` paths named by its command/manifest). The final full suites
below supersede that intermediate source and include every named check plus the
new Grab regression.

| Final complete run | Result | pytest duration |
| --- | --- | --- |
| Native, `--require-pathfinding=native` | 1,267 passed, 1 inherited xfail, no skips | 357.86 s |
| Python, `--require-pathfinding=python` | 988 passed, 279 native-only skips, 1 inherited xfail | 160.49 s |

Both runs retain 102 inherited Gym warnings. The sole strict xfail is
`tests/game/test_baseline.py::test_human_game_records_end_time`, owned by #16.
The native suite contains 665 passing tests from the named required selection;
the Python suite contains 386 passing tests from that selection and 279
native-only skips. All 172 new query tests pass in both. No #20 comparison/undo
failure occurred in these runs; success does not establish a fix for that issue.
Durations are observations under shared host load, not performance claims.

The loaded native extension has SHA-256
`61343ab5e0bdd26cb7918f6e7e6187ee0d08982886bf456c4ffcfb1a5d8f7852`.
The two input manifests are identical (1,333 files); the source bytes were
rechecked against the worktree after both runs. `validation-summary.json`
retains the full and named-subset counts derived from JUnit.

`run_check.py` records exact commands, 1200 s timeout, imported source/backend,
extension hash, runtime/packages, timestamps, exit status, JUnit XML and hashes
of source/test/build inputs for each required/full run. Native runs load this
worktree's built extension. Python runs use a separate fresh-process source
copy at `/tmp/botbowl-issue8-python-source` with no extension, the same runtime,
its own cwd/PYTHONPATH, and `--require-pathfinding=python`; the guard and an import
probe verify that selection. Source manifests are checked against the worktree
before each run. This report is added after testing; it is not a changed test
input.

The complete raw packet lives at `/tmp/botbowl-issue8-evidence`; large logs,
exports, packages and binaries are intentionally outside Git. `scope-audit.json`
confirms engine procedures, model/RNG/forward-model implementation, comparison
helpers, pathfinding sources, existing action-validation tests and package/CI
files match the accepted base exactly. No skip, xfail or existing test assertion
was changed. The supplied #20 comparison/undo observations remain separately
owned; these results make no claim to repair them.

Hosted checks must be assessed against the eventual published exact head and
are separate from these local runs. Parent-assigned fresh independent
mathematical/technical review remains mandatory before finalization.
