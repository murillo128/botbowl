# Installed laboratory quickstarts

These four independent examples ship in the wheel under `botbowl.examples`.
They reuse the public lab APIs; their `summary.json`, `http.json`, comparison and
trace files are example output, not additional interchange schemas. Use CPython
3.11+ and the artifact built by [the existing release procedure](../releasing.md).
The package version comes from `botbowl._version`, also reported by
`python -m botbowl --version`. No registry publication is implied.

```bash
python -m venv /tmp/botbowl-lab-venv
. /tmp/botbowl-lab-venv/bin/activate
# Set WHEEL to the absolute path of the verified wheel from release preparation.
python -m pip install "${WHEEL}[web]"
LAB_OUT=$(mktemp -d)
cd /tmp
python -m botbowl --version
```

The `web` extra is needed only to start the disposable loopback server. Dataset,
snapshot, branches and a remote HTTP client need only the base wheel. No notebook,
GPU, trained model, NFL code or graphical assets are used. Every command accepts
an explicit output directory, seed, accepted-decision budget and automatic-step
limit. Use fresh output subdirectories, especially for datasets and replay
exports. All generated files stay under `LAB_OUT`; examples never write into the
installed package. `--help` describes arguments; nonpositive budgets are rejected
before generating output. A step limit applies **per decision**, not to the
entire command; dataset count and branch count multiply the total work bound.

## 1. Generate, validate and read a dataset

```bash
python -m botbowl.examples.dataset --output "$LAB_OUT/dataset" --seed 17 --episodes 6 --max-decisions 4 --max-steps 1000
```

The producer calls `build_plan`, `execute_plan` and `validate_dataset`, then
freezes a `DatasetManifestV1` in `consumer.json`. Six independent 3v3 match origins
are allocated as whole families into train/validation/test (requested .6/.2/.2;
actual counts are reported). `DatasetReader(..., split=...)` validates and reads
every partition in batches. Four accepted decisions per episode produce 24
windows with the demonstrated seed: these are truncated fragments, not completed
match results. `summary.json` records counts, end conditions and semantic hashes;
`plan.json`, `dataset.json` and episode manifests retain the producer evidence.

Each passive window has three history slots and two future target slots with
presence masks. The example predictor may copy only the latest authorized
history; it has no trained-model or accuracy claim. Targets, privileged RNG and
evaluation channels must never become predictor inputs. Splits are frozen by
origin family before extracting overlapping windows: a random window split would
leak related histories. The installed acceptance test repeats generation, compares
semantic hashes, verifies disjoint families and reads with privileged files
removed. See [generation](generate.md), [offline consumer](external-client.md),
[windows](windows.md), [splits](splits.md), and [channel permissions](channels.md).

## 2. Control both teams through HTTP

```bash
python -m botbowl.examples.http_match --loopback --output "$LAB_OUT/http" --seed 17 --max-decisions 8 --max-steps 1000
```

`--loopback` starts one temporary server on an OS-assigned loopback port, creates
three ephemeral bearer credentials in memory and shuts down on exit. Credentials
are not printed or saved. The administrator can create/close its sessions; the
home and away principals can play only their own team. The SDK queries legal
commands at every revision and selects the indexed legal command for the actual
actor. Teams need not alternate. `http.json` contains actions and the final public
session state; the acceptance test compares it with the same decisions through
`SimulationSession`, including end reason and both actors.

For an operator-configured remote service, omit `--loopback` and provide
`BOTBOWL_HTTP_URL`, `BOTBOWL_HTTP_TOKEN_ADMIN`, `BOTBOWL_HTTP_TOKEN_HOME` and
`BOTBOWL_HTTP_TOKEN_AWAY` through your process environment/secret manager. The
same command and SDK run against that URL. Never put tokens in URLs, source,
output files or shell commands stored in history. This example does not provision
a remote service. The SDK rejects plaintext non-loopback URLs, redirects and
proxy discovery. Real remote deployments require TLS (or the server's exact
trusted-proxy configuration), transport timeouts and one registry-owning process.

The [HTTP contract](http.md) owns authentication, resource/rate bounds and errors;
the [command contract](commands.md) owns role grants, revision guards and bounded
idempotency. Bearer theft, unauthorized grants, stale writes, replayed commands,
resource exhaustion and malformed snapshot data are distinct threats. Keep debug
and CORS disabled, assign minimal grants and bound ingress bytes and timeouts.
Remote data is JSON with closed validators, never pickle. A timeout can leave a
write uncertain: recover the same pending request identity through the SDK,
never resubmit a potentially committed action under a new ID. Registry restart
invalidates generations/receipts; there is no persistent exactly-once guarantee.
Loopback acceptance establishes local/HTTP equivalence, not external TLS/proxy
operations or production availability.

## 3. Persist and restore an episode in another process

```bash
python -m botbowl.examples.snapshot save --output "$LAB_OUT/snapshot" --seed 17 --max-decisions 3 --max-steps 1000
python -m botbowl.examples.snapshot resume --output "$LAB_OUT/snapshot" --seed 17 --max-decisions 3 --max-steps 1000
```

The first command saves an episode-scoped `SnapshotFileV1` after `START_GAME`,
then records an uninterrupted continuation. The second interpreter reads only
the snapshot to reconstruct the episode and continues with `HEADS`, then
`RECEIVE`. `expected.json` and `actual.json` must match: events, terminal/truncated
status, decision count, seed/resource manifest and final semantic state hash.
The PID files establish separate processes. This bounded natural pregame example
accepts budgets of 2 or 3 decisions; it does not claim full-match coverage.

The snapshot owns the episode budget and all five RNG streams. Resume arguments
must agree with the saved seed and budgets; they do not reseed or reset it.
Logical clocks replace wall-time deadlines. The [snapshot file contract](snapshot-files.md)
requires compatible exact engine, NumPy, backend, schema and adapter identities;
“portable” means another compatible process, not arbitrary future versions.
External policy I/O and arbitrary Python callbacks are not serialized. Unknown
schemas/components fail closed. Hashes detect changes, not producer authenticity.

Legacy visual `Replay`/`.rep`, UI `Game.to_json()` and in-memory checkpoints are
not substitutes for this file. Executable [ReplayV1](replays.md) additionally
records snapshots and validated decision/event history for seek and continuation.

## 4. Fork two alternatives and compare events/hashes

```bash
python -m botbowl.examples.branches --output "$LAB_OUT/branches" --seed 17 --max-decisions 2 --max-steps 1000
```

After `START_GAME`, the example normalizes a saved source to logical clocks and
forks the next two legal initial actions into `left` and `right`. Each branch has
a two-decision horizon **including** its initial action and a declared
`first-legal/v1` continuation policy. `ChancePolicy(mode='independent')` derives
different branch streams from the explicit seed and component IDs. This is one
sample per alternative, not a paired estimator or a causal-effect claim.

`tree.json` is the existing `BranchTreeV1` export: inspect shared
`origin_family_id`, `parent_snapshot_id`, policies, chance provenance and result
hashes. `comparison.json` reports the source hash, sibling/source isolation and
continuation events. The `left`/`right` executable replay directories include
**all** events and pre/post hashes, including the initial actions; `ReplayReader`
verifies these during replay. The acceptance test replays both complete branches.
Different branch identities themselves affect hashes; a different hash alone
is not proof of a different sporting outcome. See [branches](branches.md) and
[chance modes](chance.md) for independent, replay, forced and bounded matched
sampling. Imported forecasts remain predictions, separate from executed branches.

## Contracts across the four paths

| Boundary | Meaning and owning contract |
| --- | --- |
| ObservationV1 | Public facts in stable home/away coordinates; observer side is metadata, not an automatic coordinate transform. Not a sufficient executable state. [Observation](observations.md) |
| Channels/input profile | Primary public state, derived public features, control legality; privileged snapshots/RNG and evaluation labels stay separate. Leaf allowlists define model inputs. [Channels](channels.md) |
| ActionV1 | Semantic command with actor/entity IDs and current decision context; reject stale revisions and illegal commands before mutation. [Actions](actions.md) |
| EventV1 / HTTP notifications | Engine events carry logical context; HTTP notifications invalidate cached views and have bounded cursors, not full replay semantics. [Timeline](timeline.md), [HTTP](http.md) |
| Time/budgets | Decision sequence is an accepted action boundary; event sequence can advance several times within it. Turn/drive/half are contextual, not seconds. Truncation is administrative, termination is sporting. [Session](session.md) |
| Rules/resources | Partial shipped BB2016 behavior; resource and roster digests identify effective inputs. Sizes 1/3/5/7/11 are supported profiles, not certification of all official rules. [Rules](rules.md) |
| RNG | Versioned seed derivation and owned streams; same seed without the same policies, actions, resources, backend and runtime is insufficient. [Randomness](randomness.md) |
| Adapter/format versions | Package version is distinct from schema and environment versions. Explicit compatibility boundaries and non-migrations appear in the [release proposal](release-proposal.md). |

## Headless container and validation

Build the verified wheel and the existing `headless` Docker target using
[release preparation](../releasing.md) and [container instructions](../docker.md).
The image already contains these same modules and runs as UID 10001, without a
display or GPU. For example (each command executes the wheel, with no checkout):

```bash
docker run --rm botbowl-headless python -m botbowl.examples.dataset --output /tmp/dataset --seed 17 --episodes 6 --max-decisions 4 --max-steps 1000
docker run --rm botbowl-headless sh -c 'python -m botbowl.examples.snapshot save --output /tmp/snapshot --seed 17 --max-decisions 3 --max-steps 1000 && python -m botbowl.examples.snapshot resume --output /tmp/snapshot --seed 17 --max-decisions 3 --max-steps 1000'
docker run --rm botbowl-headless python -m botbowl.examples.branches --output /tmp/branches --seed 17 --max-decisions 2 --max-steps 1000
```

The base headless image can also run `http_match` against your HTTPS service,
passing the four environment variables with `docker run --env NAME` (names only,
not literal credentials). Its optional loopback server needs Flask, so use the
existing `web` target for that variant and override its command with the same
`python -m botbowl.examples.http_match --loopback ...` invocation. Persist desired
outputs through an operator-owned writable mount; ephemeral `/tmp` is discarded
when the container exits.

`pytest tests/lab/test_quickstarts.py` builds a wheel from a temporary source copy,
installs it in a new venv, runs isolated interpreters outside the checkout and
validates artifact contents. The hosted `lab-http` CI profile includes this test;
no new access to the Codex runner is added. Run
`python tools/release/check_docs.py` for repository-local link checks. On Linux with Docker available, run
`python -m tests.lab.quickstarts_container --image botbowl-headless` from the
checkout to validate all four paths as non-root in the same headless image. Its
temporary authenticated server listens only on host loopback; the client uses
Linux host networking, with no checkout or Docker socket mounted. Container
execution is conditional on an available daemon; unrun checks must be recorded
in the PR evidence. Final audit covers documentation, security, migration and
reproducibility claims on the exact published head.
