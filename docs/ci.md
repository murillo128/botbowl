# CI profiles and dependencies

`Tests` runs on pull requests (including drafts) and pushes to `main`, using only
GitHub-hosted runners with read-only contents permission. Every action is pinned
to a verified release commit; checkout disables persisted credentials. No workflow
calls the separate, unchanged Skillforge issue-label launcher. PR code never runs
via `pull_request_target` or on the self-hosted Codex runner.

The Linux core matrix is CPython 3.11–3.14 × Python/native pathfinding. Separate
Linux legacy RL jobs run the complete suite on 3.11/3.12 with Gym 0.26.2 and NumPy
1.26.4, including real registered environments with the checker enabled. Core
jobs omit only `tests/ai/test_env.py`; that module runs in both RL jobs. Optional
Gym export/mask cases explain their absent capability through explicit skips.
Native-only pathfinding comparisons skip in Python jobs and execute in all four
native jobs. The strict inherited end-time xfail belongs to #16. No other engine
failure is waived; known forward-model findings belong to #20 and remain failures.

Each suite is split into fast unit/regression tests and integration (AI,
forward-model, server, full-game). Both portions run even if the first fails. The
suite and examples are copied outside the checkout; only the built wheel supplies
`botbowl`. `--require-pathfinding` verifies the actual module and native extension
suffix. Unit/integration JUnit, logs, resolved packages, step durations, and source
revision are retained as 14-day Actions artifacts even on failure.

Packaging smokes run on Ubuntu 24.04, Windows 2025, and macOS 15, CPython 3.11.
They invoke `tests/packaging/verify_sdist.py`: export committed source, build a
Python wheel/sdist with an unavailable compiler, build a native wheel from that
fresh sdist, then install and smoke-test it in a clean environment. Additional
minimal environments install the Python wheel and sdist independently. All run
outside the checkout, without DISPLAY or optional integrations, with `pip check`.
These are platform packaging smokes, not full platform suite certification.

Independent extra installs run on Linux 3.11 for web, competition, dev, render,
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

Audit jobs install all applicable extras plus build/CI tools, run `pip check`,
and audit every resolved third-party identity using pip-audit/PyPI in strict mode,
without ignored advisories. Only the unpublished local Bot Bowl project is omitted
from the registry lookup; its source is covered by the other CI jobs. Security
results are dated observations, not a permanent absence-of-vulnerabilities claim.

To reproduce a profile, install `pip` and `build` under the selected constraints,
commit the source to test, then run from a checkout:

```bash
python -m pip install --upgrade -c requirements/core.txt pip setuptools wheel build
python tools/ci/run_profile.py suite --backend native --output /tmp/botbowl-ci-native
python tools/ci/run_profile.py artifacts --output /tmp/botbowl-ci-artifacts
python tools/ci/run_profile.py extra --extra web --output /tmp/botbowl-ci-web
python tools/ci/run_profile.py suite --rl --backend native --output /tmp/botbowl-ci-rl
```

Output directories must be new. Each profile exports committed `HEAD`; uncommitted
edits are intentionally absent from its artifact and test input. Hosted jobs
checkout the exact PR head, so reports identify that head rather than a synthetic
merge result. Final integration validation remains a separate epic gate.
