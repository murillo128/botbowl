# Laboratory CI contracts

The `Tests` workflow owns these installed-wheel profiles on CPython 3.11.
Every lab cell runs on GitHub-hosted Ubuntu, including external pull requests.
The existing lint and trusted-push Python/native core gates remain unchanged
(the runner amendment on issue #6 applies to core only).

| Profile | Installation | Required contracts |
| --- | --- | --- |
| `lab-fast` (Python and native) | Core + test tools | Public control, atomic rejection, observation privacy, cross-process snapshot, recorded-action replay, causal windows, origin splits, external M1 client, ReplayV1 (#42), session command permissions/idempotency (#59) |
| `lab-extended` (Python and native) | Core + test tools | #25 broad sequences with seeds 0/3/17 and sizes 1/3/5/7/11; full snapshot subprocess, generation and recording contracts |
| `lab-adapters` (Python) | Core + test tools + multiagent (includes Gymnasium) | Gymnasium and PettingZoo AEC contracts (#57), including invalid calls and interruption/mask behavior |

Run a profile from a clean committed tree:

```sh
python tools/ci/run_profile.py lab-fast --backend python --output /tmp/lab-fast
```

The runner archives HEAD, builds a wheel, installs into a fresh venv and copies
only tests/examples into the execution directory. Neither the checkout nor
PYTHONPATH supplies the package. The external-client owner tests additionally
build a minimal wheel installation without test tools or optional runtime extras
and run both examples with isolated Python from an unrelated working directory.

`lab-fast` runs twice with independent temporary fixtures. Its M1 owner test
compares two generated 100-episode, seed-17, 3v3 plans (eight decisions and 1,000
engine steps per episode), validates all three origin partitions and reads 800
windows in other processes. It checks semantic hashes, masks and shapes, rather
than merely accepting an example exit code. All 100 episodes are intentionally
truncated; this is neither 100 complete matches nor model-quality evidence.

The selections in `tools/ci/lab_profile.py` reuse capability-owner tests, including
unknown/incompatible versions, corruption, empty/terminal data and compatibility
boundaries. The leakage sentinel, contaminated-origin, corrupt-snapshot and replay
hash-divergence fixtures must be rejected. Each cell also requests the opposite
backend in a subprocess and requires a pre-collection failure; the real run then
requires its actual backend (including a compiled extension for native).

All selected tests must execute. Skips, missing tests, collection failures and
missing required imports fail the cell; optional-owner `importorskip` cannot hide
a missing adapter dependency. The wheel currently supplies replays and local
commands and advertises the multiagent extra, so their profiles are mandatory.
HTTP/SDK #60 and the research viewer #51 are not supplied yet: receipts explicitly
say **unavailable; not tested**, rather than passed. Their owners must add their
installed dependency, test selection and mandatory workflow cell when adding the
capability to the distribution. The release manifest currently inventories wheel
members, not a separate feature/version schema; this CI does not invent one.

Hosted jobs have a 45-minute ceiling, a 20-minute process-group deadline per
command/profile pass and a 6 GiB address-space ceiling per process. The broad
corpus retains its owner-defined decision/step/no-progress limits. On timeout or
SIGTERM the runner kills and reaps the entire active process group, including
nested consumers. A final cleanup step runs even after cancellation. Deterministic
fixtures, wheels and venvs are removed; no background service is started.

Uploaded JSON contains counts, skip reasons, selected/executed test identities,
failure identities and phases (without private assertion values),
versions, fixture seeds and observed status. Core also retains its collection and
shard receipts and a JSON package inventory. Raw pytest logs/XML, snapshots,
private RNG state and datasets are excluded from all Tests workflow uploads;
core and lab failures do not echo raw tracebacks into public job logs. Detailed
logs remain locally available for diagnosis. No benchmark threshold substitutes
for semantic tests.
