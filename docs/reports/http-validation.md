# HTTP/SDK validation

Validation used CPython 3.11.16, the Python pathfinding backend, Flask 3.1.3 and
NumPy 2.4.6. No external service or production credential was provisioned.

* `python -m pytest tests/lab/test_http_api.py tests/lab/test_http_client.py
  tests/lab/test_commands.py -q`: **52 passed**. Includes two remote and two
  local controllers with identical standard/pickup/block configurations, seeds,
  observations, public notifications and episode outcomes; authenticated random
  continuation after snapshot restore; pause/clock purity; schema/role failures;
  duplicate, expired and lost receipts; post-commit timeout; disconnection;
  pagination/cursor loss; bounded waits, rate and concurrent admission; socket
  and session teardown.
* Existing session/scenario/gateway regression baseline:
  `python -m pytest tests/lab/test_commands.py tests/lab/test_session.py
  tests/lab/test_scenarios.py -q`: **228 passed** before the additional typed
  internal-error mapping, subsequently covered by the focused gateway run above.
* Repository error-level Ruff checks and `git diff --check`: passed.
* `tools/ci/check_public_types.py` and `tools/ci/check_lab_types.py`: positive
  examples accepted; each check rejected its eight intentional invalid calls.
* `python setup.py build` and `python -m build --wheel`: passed. The latter uses
  the declared isolated Cython/setuptools build environment; no native extension
  was requested.
* Installed the wheel with its `web` extra in a fresh environment, then ran
  `tests/lab/http_wheel_process.py` from `/tmp`. Both the server and child SDK
  imported the installed package. `examples/lab/http_match.py` controlled both
  teams without a GUI or engine internals and printed
  `decisions=24 end_reason=decision_budget`. The harness verified session cleanup
  and closed the disposable loopback listener.

Reproduce the wheel check with an absolute harness path while outside the repo:

```sh
python -m build --wheel
python -m venv /tmp/botbowl-http-check
/tmp/botbowl-http-check/bin/python -m pip install '/absolute/dist/botbowl-2.0.0a1-py3-none-any.whl[web]'
cd /tmp
/tmp/botbowl-http-check/bin/python /absolute/repo/tests/lab/http_wheel_process.py
```

The 24-decision example demonstrates bounded external control, not natural match
completion. Transport tests use public fixture identities exclusively. Proxy/TLS
configuration is exercised with simulated request environments; no remote TLS
service or deployment is claimed. Broad-suite and exact-head CI results belong
in the PR evidence, including optional dependency exclusions.
