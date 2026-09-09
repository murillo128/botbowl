# CI profiles and dependencies

`Tests` runs on pull requests (including drafts) and pushes to `main` and `codex/**`,
with read-only contents permission. Following the
[local runner amendment](https://github.com/murillo128/botbowl/issues/3#issuecomment-5574335362),
lint uses GitHub-hosted runners and core uses `[self-hosted, codex]` only for trusted
pushes. Pull-request events run hosted lint; core checks attach to the exact pushed
commit. Every action is pinned to a verified release commit; checkout disables
persisted credentials. No workflow calls the separate, unchanged Skillforge
issue-label launcher or executes PR events via `pull_request_target`.

The [September 7 amendment to epic #3](https://github.com/murillo128/botbowl/issues/3#issuecomment-5573357791)
and the current [issue #6 contract](https://github.com/murillo128/botbowl/issues/6)
define exactly three ordinary mandatory jobs: `lint`, `core / 3.11 / python`, and
`core / 3.11 / native`. This policy applies to PRs, intermediate integrations and
the final epic gate. CPython 3.12–3.14, legacy RL, packaging, extras and dependency
audits are outside the ordinary workflow. Run the relevant additional profiles
when a change specifically affects those capabilities.

Core retains the full functional suite with two explicit boundaries:
`tests/ai/test_env.py` belongs to the optional legacy RL profile, and
`tests/framework/test_quick_snap_forward_model.py` is targeted #20 investigation
evidence. The latter remains normally discoverable by `pytest` and can be run
directly on either installed backend; ordinary core does not execute it. Optional
Gym export/mask cases retain their capability skips, native-only comparisons skip
only in Python, and the inherited strict end-time xfail belongs to #16. No other
engine failure is waived; known forward-model findings remain failures.

Each core job first collects all core identities, including the investigation.
It then runs unit/regression and integration (AI, forward-model, server, full-game,
and the generation/recording/validation/replay pipeline in `tests/lab/test_generate.py`)
portions in two deterministic shards: SHA-256 of the UTF-8 pytest node ID modulo
two. The four subprocesses run concurrently in fresh processes without xdist,
preserving order within each portion/shard and collecting all siblings even after
a failed subprocess. Logs, JUnit and receipts have distinct shard paths; step
results retain declared shard order regardless of completion order. Timeout and
interruption cleanup kills each shard's POSIX process group and waits for every
direct child. This bounds accumulated test-process state without helper jobs or
a new runtime matrix; two simultaneous core profiles use up to eight shard processes.
The job retains its 25-minute limit and each execution subprocess its 20-minute
limit. Core clears environment/configuration addopts to prevent global filters
from silently shrinking both reference discovery and execution.

The named core job verifies receipts for all four portions before succeeding:
same source head, installed backend, interpreter and complete collection; exact
deterministic selection; successful pytest exit; and exactly one teardown receipt
per selected identity. The disjoint executed union plus the explicitly recorded
investigation equals full discovery. Missing portions, loss, overlap, duplicates,
stale receipts and failed execution fail the gate. Lint runs synthetic negative
controls for this accounting and real-process controls for parallel execution,
failure, timeout, missing/invalid receipts and child cleanup. These controls are
not functional execution evidence.
Generator selection controls also verify exclusive integration assignment with
unchanged hash placement, collection order and ordinary coverage, and reject old
receipts that assign those tests to unit shards. Other lab modules retain their
existing classification; all generator tests and execution budgets are preserved.

Tests and examples are copied outside the checkout; only the built wheel supplies
`botbowl`. `--require-pathfinding` verifies the actual module and native extension
suffix. JUnit, full collection/selection/execution receipts, package inventories,
step durations, and source revision are retained as 14-day Actions artifacts even
on failure. No pytest discovery configuration or investigation assertions change.

## Historical and targeted additional profiles

The wider matrix recorded in [issue #6's report](reports/ci-issue-6.md) remains
valid historical evidence. Its absence from ordinary CI does not invalidate it
or certify these additional capabilities at a newer head. The profile helpers and
constraints remain available for targeted validation.

Historical packaging smokes ran on Ubuntu 24.04, Windows 2025, and macOS 15, CPython 3.11.
They invoke `tests/packaging/verify_sdist.py`: export committed source, build a
Python wheel/sdist with an unavailable compiler, build a native wheel from that
fresh sdist, then install and smoke-test it in a clean environment. Additional
minimal environments install the Python wheel and sdist independently. All run
outside the checkout, without DISPLAY or optional integrations, with `pip check`.
These are platform packaging smokes, not full platform suite certification.

Historical independent extra installs ran on Linux 3.11 for web, competition, dev, render,
and RL; RL additionally runs on 3.12. Each installs just one extra in a new venv,
runs its installed smoke and `pip check`. This does not certify the Cartesian
product of all extras, platforms, and Python versions. Render uses Agg; competition
smoke does not start Docker containers. Asset exclusions from #5 remain intact.

## Dependency maintenance

`pyproject.toml` owns library compatibility ranges. `requirements/common.txt`
constrains the complete dependency/tool families; `core.txt` and `rl.txt` select
the NumPy profile. Constraints do not install unused extras. Each install resolves
the requested package/extras once under one coherent profile. Build subprocesses
receive the same constraints through `PIP_CONSTRAINT`/`PIP_BUILD_CONSTRAINT`.
Fresh venvs also upgrade their pip/setuptools bootstrap under these constraints;
otherwise Python 3.11 ensurepip can retain old setuptools independently of build
isolation. There is no preceding installation of the old 2023 environment snapshot.

The pins were resolved on 2026-09-07 with uv from `pyproject.toml` (all extras) and
`requirements/ci-tools.in`. To update them, use `uv pip compile pyproject.toml
requirements/ci-tools.in --all-extras --universal --python-version 3.11`, then keep
NumPy in the capability files: latest compatible NumPy 2 for each core interpreter
family, and NumPy 1.26.4 for legacy RL. Review upstream release/advisory sources,
run the actual matrix and platform/extra profiles, and refresh the dated audit.
Pins are reproducible version constraints, not a claim that every version in a
library range has been tested or audited. No package hashes/lockfile are claimed.

The targeted audit helper installs all applicable extras plus build/CI tools, runs `pip check`,
and audits every resolved third-party identity using pip-audit/PyPI in strict mode,
without ignored advisories. Only the unpublished local Bot Bowl project is omitted
from the registry lookup; its source is covered by the other CI jobs. Security
results are dated observations, not a permanent absence-of-vulnerabilities claim.

To reproduce a profile, install `pip` and `build` under the selected constraints,
commit the source to test, then run from a checkout:

```bash
python -m pip install --upgrade -c requirements/core.txt pip setuptools wheel build
python tools/ci/run_profile.py suite --backend native --output /tmp/botbowl-ci-native
python tools/ci/run_profile.py suite --backend python --output /tmp/botbowl-ci-python
python tools/ci/run_profile.py artifacts --output /tmp/botbowl-ci-artifacts
python tools/ci/run_profile.py extra --extra web --output /tmp/botbowl-ci-web
python tools/ci/run_profile.py extra --extra gymnasium --output /tmp/botbowl-ci-gymnasium
python tools/ci/run_profile.py suite --rl --backend native --output /tmp/botbowl-ci-rl
```

Output directories must be new. Each profile exports committed `HEAD`; uncommitted
edits are intentionally absent from its artifact and test input. Hosted jobs
checkout the exact PR head, so reports identify that head rather than a synthetic
merge result. Final integration validation uses the same three ordinary gates,
plus any functional evidence explicitly required by the integrated changes.

After a core profile has installed its wheel, targeted #20 evidence can use its
retained test copy and interpreter (repeat with the Python installation as needed):

```bash
cd /tmp/botbowl-ci-native/suite
BOTBOWL_ISSUE20_EVIDENCE=/tmp/botbowl-issue20-targeted \
  ../installed/bin/python -m pytest tests/framework/test_quick_snap_forward_model.py \
  --require-pathfinding=native -q
```
