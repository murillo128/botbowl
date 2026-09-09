# DATA-04 validation

Validation used CPython 3.12.3, the Python pathfinder, and an isolated environment
at `/tmp/botbowl-issue70-venv`. Build succeeded with `python setup.py build`;
editable installation used `pip install -e '.[dev]'`. The aggregate
`requirements.txt` subsequently supplied optional full-suite dependencies,
including NumPy 1.26.4 and Gym 0.26.2. No dependency files changed.

The implementation before the final focused corrections completed root `pytest
-q`: **4,142 passed, 300 skipped, 41 failed** in 535 seconds. All failures were
reproduced from an unmodified archive of the pinned base
`688af13f6548a48e798baccc45b0b7944cef88cc` with the same environment:

```
python -m pytest tests/framework/test_quick_snap_forward_model.py \
  tests/lab/test_reproducibility.py -q --tb=short
```

Baseline result: **109 passed, 41 failed**. Thirty-nine failures come from
`rules_blueprint` substituting `empty_ruleset` without the `races` keyword now
passed by the loader. The other two are existing global-RNG assertions in
`test_omitted_seed_is_returned_and_does_not_seed_globals` and
`test_engine_consumption_and_observer_exception_do_not_move_other_sources_or_globals`.
No correction to those unrelated tests or engine behavior is included.

The recorder, records, snapshot persistence and earlier split suites completed
with **388 passed**. After corrections to holdout feasibility, connected family
labels, copy identity, canonical relation ordering, appended bridge diagnostics,
and policy group keys, `pytest tests/lab/test_splits.py -q` reports **42 passed**.
That suite includes an actual SnapshotFileV1 semantic hash integration, actual
EpisodeManifestV1 adaptation, subprocesses with different `PYTHONHASHSEED`, and
adversarial frozen-manifest/membership checks. The executable example and Python
compilation also passed before these final focused corrections; both are rerun
for publication. No native backend or learning framework is required by DATA-04.

The full suite is not claimed green. Its baseline failures are separated from
the focused acceptance result, and the full suite was not repeated after the
final split-only corrections.
