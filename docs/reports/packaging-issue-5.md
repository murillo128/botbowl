# Packaging verification, 7 September 2026

The packaging change preserves the accepted issue #4 engine baseline. It delivers
setuptools/PEP 517 metadata, a default headless Python build, required native
build selection, installed resources, and deferred optional exports. The original
engine and forward-model algorithms, rules, formations, and baseline expectations
are unchanged. This report records observed compatibility, including failed
attempts; it does not claim complete Blood Bowl correctness.

## Environment and capability matrix

Linux x86_64, glibc 2.39, GCC/G++ 13.3.0. Isolated builds resolved setuptools
84.0.0, Cython 3.3.0, and build 1.6.0. Runtime untangle was 1.2.1 with defusedxml
0.7.1. Each cell below used its own fresh environment and an installed wheel,
with `DISPLAY` and `PYTHONPATH` absent. Before adding test dependencies it loaded
rules/teams/configurations and executed START_GAME, HEADS, and KICK for every
pitch size 1/3/5/7/11. No Flask, Gym, Docker, matplotlib, or pytest distribution
was installed during those minimal checks.

| CPython | NumPy for core | Python core suite | Native core suite |
| --- | --- | --- | --- |
| 3.11.16 | 2.4.6 | 489 pass, 53 skip, 1 xfail | 528 pass, 14 skip, 1 xfail |
| 3.12.14 | 2.5.3 | 489 pass, 53 skip, 1 xfail | 528 pass, 14 skip, 1 xfail |
| 3.13.15 | 2.5.3 | 489 pass, 53 skip, 1 xfail | 528 pass, 14 skip, 1 xfail |
| 3.14.7 | 2.5.3 | 489 pass, 53 skip, 1 xfail on recheck; initial failure below | 528 pass, 14 skip, 1 xfail |

The core suite is the repository suite with the optional Gym, competition, and
HTTP-server test modules omitted. Fourteen optional public-export assertions
remain explicitly skipped in these dev-only environments. Python builds also
retain the baseline's 39 explicit native-only skips. Required native runs have
no missing-native skips; the loaded function module and extension suffix are
checked by `--require-pathfinding`. The inherited strict timestamp xfail remains
owned by #16. The 63 passing bounded baseline tests and the pathfinding corpus
are included in every cell.

The **complete suite**, after adding all extras on each of CPython 3.11.16 and
3.12.14, passed with NumPy 1.26.4: Python **525 pass, 39 skip, 1 xfail**; native
**564 pass, 1 xfail**. There were 102 inherited Gym warnings per complete run.
Those warnings and the existing wrapper semantics are not Gymnasium conformance.

The approved capability decision in issue #5 keeps legacy RL on **3.11/3.12
only**, using Gym 0.26.2 and NumPy <2. Independent RL-only environments on both
interpreters passed `pip check`, installed Gym plugin discovery, registered
`gym.make('botbowl-1-v4')`, reset, and a legal step with the checker enabled.
On CPython 3.13.15, a separate Gym 0.26.2/NumPy 2.5.3 control failed in the checker
at `np.bool8`; a binary-only download of `numpy<2` found no compatible wheel.
This is the observed incompatibility, not a claim that every possible source
build is impossible. RL on 3.13/3.14 remains unsupported until #17 is delivered.
The core's NumPy range does not certify every NumPy/Python combination.

Each other extra was installed independently on CPython 3.11.16 and passed
`pip check` plus its smoke. This does not certify an untested cross-product:

| Extra | Direct versions observed | Smoke |
| --- | --- | --- |
| web | Flask 3.1.3 | Root page, required JS assets, game-mode and bot routes via Flask test client |
| competition | Docker 7.2.0, tabulate 0.10.0 | Public competition classes and socket request construction; no daemon/service started |
| dev | pytest 9.1.1, build 1.6.0, more-itertools 10.8.0 | Import and callable tool checks |
| render | matplotlib 3.11.1 | Agg figure draw and deferred EnvRenderer import, without Tk/display |

The Tk window itself was not exercised without a display. Tk remains an
interpreter/system component needed when constructing the legacy EnvRenderer.

The [compact JSON evidence](packaging-issue-5.json) records artifact hashes and
per-cell summaries. The core and complete suites above exercised package source
revision `3c1441a46f38ae5092729a688ed2d4816ba445ee`. Independent review then
identified a fresh-sdist defect in the original build evidence, described below.
The corrected artifacts come from committed revision
`9cbfdbaeba0beb882ce34755fc501301d5d4fd9a`; its only package change explicitly
includes the two existing native C++ source files in the sdist manifest. Engine
code, native source contents, and runtime dependencies are unchanged.

All eight refreshed artifacts passed installed baseline, pathfinding, and focused
import/export checks: on 3.11/3.12, Python **119 pass, 39 skip, 1 xfail** and
native **158 pass, 1 xfail**; on 3.13/3.14, Python **105 pass, 53 skip, 1 xfail**
and native **144 pass, 14 skip, 1 xfail**. The additional fourteen skips are the
optional exports in environments without those extras. Core resource/decision
smokes and `pip check` passed in every cell, with registered RL smokes again on
3.11/3.12. The refreshed wheel and sdist also passed minimal installation smokes
outside the checkout. All 99 installed Python package files are byte-identical
to the previously reviewed wheel; only archive construction changed. Editable
and VCS checks below were retained from the preceding runtime-code validation.

## Artifacts, imports, and installation forms

- A fresh Git export on each supported interpreter ran `python -m build` with
  `BOTBOWL_BUILD_NATIVE=0`, `CC=/nonexistent`, and `CXX=/nonexistent`.
  The regression then extracted that run's default sdist into another new
  directory, built a required-native wheel with a working compiler, installed it
  in a new environment, and passed `pip check` plus the minimal resource/decision
  smoke outside the checkout. This passed on 3.11.16, 3.12.14, 3.13.15, and 3.14.7.
- Native wheels built from these fresh sdists loaded
  `botbowl.core.pathfinding.cython_pathfinding` as an extension.
  Python wheels loaded `botbowl.core.pathfinding.python_pathfinding`.
- A fresh native source build with both compiler commands set to `/nonexistent`
  failed explicitly. `BOTBOWL_BUILD_NATIVE=auto` also failed, explaining that
  only `0` and `1` are accepted. No requested native build silently fell back.
- Python editable installation passed the complete suite; an independent native
  editable installation from an extracted sdist passed the minimal smoke and
  `pip check`. Setuptools manages its editable extension; no project post-install
  copy hook remains.
- An exact published-commit VCS install over the repository's established SSH
  transport passed outside the checkout. Its recorded `direct_url.json` matched
  the requested commit, and the minimal smoke and `pip check` passed.
- Fifteen focused import/export checks protect the headless boundary and optional
  named exports. An additional audit resolved all 229 audited baseline exports, including
  the legacy rule set and Gym registration names. Explicit RL module import registers IDs idempotently for
  source checkouts; installed clients also use Gym's plugin entry point.
- Archive inspection found the required rules/teams/arenas/formations/configs,
  web HTML and required JS/CSS source, and preserved source/license notices. It
  found no tests, caches, saves, replays, credentials, graphics, fonts, or accidental
  build output. The native wheel contains its intended extension; Python wheels
  contain none. The sdist contains `.pyx`/`.pxd` and the required
  `pathing_node.cpp`/`pathing_node.h` sources needed to build natively.
  Builds left versioned source files unchanged.

The installed browser source lacks the excluded artwork/fonts. The complete
legacy artwork remains in the checkout under its existing permissions; the
project license does not become a universal asset license. Existing source
notices for AngularJS, jQuery, Bootstrap, and wysihtml5 remain with those files.

## Failures retained and routed

Independent final review **failed** revision
`52dd3caf0899fe0dfdf5729b1a6fc5a2870f2f0f`: a default Python sdist built from a
fresh checkout omitted `pathing_node.cpp` and `pathing_node.h`. A native build
from that archive failed even with a working compiler. The previously recorded
archive contained these files, but reused the source-manifest cache after an
earlier native build; that result did not establish a clean-build guarantee.
Both files now have explicit manifest entries. The new
`tests/packaging/verify_sdist.py` regression exports only committed source,
excluding local build and `SOURCES.txt` caches. It reproduced the missing-file
failure against `52dd3ca` and passed the complete Python-sdist-to-native sequence
on all four supported interpreters at `9cbfdba`. The earlier failed review
remains recorded in PR #78. Hosted CI passed on the correction commit in
[run 34099205271](https://github.com/murillo128/botbowl/actions/runs/34099205271).

An initial broad exclusion of web resources produced a 500 response at `/` in
an installed full-suite run. Required HTML/JS/CSS source was restored, the smoke
was strengthened to exercise it, and complete installed suites passed afterward.
No graphics were added to the distribution as a workaround.

The first hosted run against the packaging change failed 14 source-checkout
checks because it installed legacy requirements without installing the package.
Explicit adapter import now registers Gym IDs, and the import-boundary unit
subprocess selects the package under test. The separate installed-artifact smoke
continues to use no path injection. The corrected source-only local suite passed
525/39 skip/1 xfail, and hosted CI passed. The inherited requirements snapshot
still has the previously recorded Wheel/Packaging constraint conflict; its
replacement remains #6's scope.

The first Python 3.14 core run failed
`test_forward_model_revert_every_step` at its player-position comparison:
488 pass, 53 skip, 1 xfail, **1 fail**. A fresh single-test run and full core
recheck passed. These passing attempts do not erase the initial failure.

A bounded seeded diagnostic established an inherited setup undo defect:
**seed 3**, zero-based step **167**, phase **Setup**, action
**SETUP_FORMATION_ZONE**, with pathfinding disabled. After revert, the positions
associated with away jerseys 6–12 rotate incorrectly. The accepted baseline on
CPython 3.11.16, installed issue #5 on 3.11.16, and installed issue #5 on 3.14.7
produced the same 167-pair action trace and identical before/after coordinates.
The retained [standalone reproduction](packaging-forward-model-reproduction.py)
uses a 256-step limit and emits the identical divergence JSON on the baseline
and Python 3.14; its expected exit status is 1. This distinguishes the observed
failure from a new Python 3.14 or packaging incompatibility. Investigation and
correction belong to #20. No assertion was weakened, no new xfail was introduced,
and no engine fix is claimed. The existing unseeded full-game test remains
stochastic, so successful suite runs do not promise that every future run passes.

## Reproduction

Use fresh environments and source/build directories for each mode. The checked-in
smoke has no pytest dependency:

```bash
python -m pip install build
python tests/packaging/verify_sdist.py --revision 9cbfdbaeba0beb882ce34755fc501301d5d4fd9a --output /tmp/botbowl-sdist-check
CC=/nonexistent CXX=/nonexistent BOTBOWL_BUILD_NATIVE=0 python -m build --outdir /tmp/botbowl-python
BOTBOWL_BUILD_NATIVE=1 python -m build --outdir /tmp/botbowl-native
python -m venv /tmp/botbowl-check
/tmp/botbowl-check/bin/python -m pip install /tmp/botbowl-python/botbowl-1.1.0-py3-none-any.whl
cd /tmp
env -u DISPLAY /tmp/botbowl-check/bin/python /path/to/checkout/tests/packaging/smoke.py --minimal
```

The explicit fresh-sdist regression requires a working C++ compiler for its
second build and a new output directory. Repeat it with each supported
interpreter. Its default `--revision HEAD` checks committed files, so commit
packaging changes before using it as a final gate. Artifacts, per-step logs,
source revision, and SHA-256 hashes are retained in the output directory.

For independent extras, install `artifact.whl[extra]` into a new environment and
run `smoke.py --extra extra`. Repeat RL on both 3.11 and 3.12. For native, use the
matching interpreter/wheel and add `--backend native`. To run the installed
regression corpus, copy only `tests/` and `examples/` to a separate directory,
install `artifact.whl[dev]`, change to that directory, then run:

```bash
python -m pytest tests --ignore=tests/ai/test_env.py --ignore=tests/ai/test_competition.py --ignore=tests/framework/test_server.py --require-pathfinding=python
```

Use `--require-pathfinding=native` for native. After installing
`artifact.whl[web,rl,competition,render]` on 3.11/3.12, run the full `python -m
pytest --require-pathfinding=...`. Outer 300-second process limits were used for
inherited unbounded callers. Raw logs and wheels remain outside Git; only this
compact report and the bounded defect reproduction are retained.
