# Long generation jobs (OPS-03)

`botbowl.lab.jobs` adds a sequential job driver over the existing generation
plan, session decision boundary and DATA-07 atomic writer. It requires core
runtime dependencies. The original `botbowl.lab.generate` API is unchanged.

```python
from botbowl.lab.generate import JobConfig, build_plan
from botbowl.lab.jobs import JobContext, JobLimits, execute_plan, resume

plan = build_plan(JobConfig('/tmp/job', episodes=10, master_seed=17))
context = JobContext(JobLimits(decisions=100, episodes=5, seconds=300),
                     callback=lambda progress: print(progress))
progress = execute_plan(plan, '/tmp/job', context=context)
# Later, using the same plan, policies, seeds and limits:
progress = resume('/tmp/job')
```

A context is single-use. Its states are `pending`, `running`, `cancelling`,
`cancelled`, `succeeded` and `failed`. `cancel()` is idempotent, thread-safe and
has no effect after a terminal state. Cancellation before dispatch creates no
session. During execution it takes effect at the next safe decision boundary.
A policy call must return for cooperative cancellation to proceed.

Callbacks receive frozen `Progress` values with planned/confirmed/failed episode
counts, decisions, logical timeline events, confirmed logical channel bytes,
monotonic duration, elapsed time of last progress, and an immutable tuple of
end-cause counts. Callback and optional JSONL stream failures increment
`observer_errors` without entering the engine or failing generation. The caller
owns these sinks and should keep them nonblocking. JSONL is incidental telemetry;
it is excluded from semantic hashes and model inputs.

All specified job limits are strictly positive and finite. Decision, event and
elapsed counters measure this attempt, including any replay work. Confirmed
counts/bytes include the preserved prefix on resume. Episode limits count new
confirmations this attempt. Resume uses the same limits with a new time/work
allowance; it never changes episode seeds or budgets. Per-decision automatic
step bounds remain the session/engine bounds from the plan (OPS-02). Consecutive
decisions without a new logical event have an additional driver limit.

Cancellation and job budget stops confirm the active episode as an explicit
fragment with `truncated=true` and `job_cancelled`, `job_time_budget`,
`job_decision_budget`, `job_episode_budget` or `job_no_progress`. A stop before
an episode creates no artificial episode. A natural end already reached takes
precedence in that episode. Job stops never change scores, select a winner or
convert an administrative error into a sporting defeat. A completed truncated
episode is preserved on resume; resume then starts the next planned episode.
Unexpected policy/engine/writer errors fail the job and propagate to Python callers.

## Recovery and failure evidence

DATA-07 `manifest.json` alone owns confirmation. Under its exclusive writer lock,
resume validates the plan, versions, loaded rules/backend, purpose-specific
seeds, policy specifications, storage limits and all committed channel checksums.
It keeps the confirmed canonical prefix without duplicates. If publication
succeeded immediately before an exception, the durable manifest wins over the
writer's stale in-memory count. Unreferenced storage shards are not confirmed.

`job-recovery.json` is atomically updated at safe decision boundaries and before
publication. Failure also creates a retained `job-failure-<id>.json`; later resume
does not erase that file. Metadata records package/schema versions, the plan
hash, job/storage limits, the complete current plan entry (scenario, policies,
episode/origin IDs, purpose seed recipes), last confirmed index, checked boundary
hashes, replay actions, the attempted action, stage and sanitized error class and
message. Messages are deliberately replaced with `Exception details redacted`:
arbitrary exception strings, credentials, personal paths and object dumps are
not diagnostic inputs. Reads/writes retain DATA-02's bounded JSON record limit.
If even the recovery write fails, the original exception remains authoritative;
no durable checkpoint is claimed.

This format uses the supported **initial recipe plus actions** recovery route,
with `snapshot=null`. DATA-02 recording requires its original reset observation
and rejects attaching to a restored mid-episode snapshot, so an engine snapshot
alone cannot restore a generation writer. Reconstructing the exact recipe and
running the same policies restores their private RNG/state too. Every saved
action and observation/timeline boundary is compared before proceeding past the
checked prefix. An unsupported snapshot, version/input mismatch or replay drift
raises `IncompatibleRecovery`; it is never silently ignored or repaired by a new
seed. Job metadata and all replay material stay outside model-permitted channels.

```sh
python -m botbowl.lab.jobs run --plan /tmp/recipe/plan.json --output /tmp/job \
  --decisions 100 --episodes 5 --seconds 300 --progress /tmp/job-progress.jsonl
python -m botbowl.lab.jobs resume /tmp/job
python -m botbowl.lab.jobs reproduce /tmp/job
python -m botbowl.lab.jobs reproduce /tmp/job/job-failure-IDENTIFIER.json
```

The CLI handles Ctrl-C cooperatively. `run` requires a new output directory;
`resume` requires recovery metadata. `reproduce` reuses the original recipe,
policies, seeds and checked prefix in disposable storage, then compares the
failure stage, sanitized error class and exact failure checkpoint. It returns
`reproduced` (exit 0), `not_reproduced` (exit 1), or an explicit incompatibility
(exit 2). Disappearance of a transient storage fault or changed external policy
implementation is not reported as successful reproduction. Exception prose is
redacted, so reproduction does not claim equivalence of hidden message text.
See `examples/lab_job_failure.py` for a reproducible local injected policy fault.

## Noncooperative workers

`supervise(plan, output, context=...)` is an optional **single spawned worker**,
independent of the parallel pool. It exposes parent lifecycle state; detailed
live decision callbacks use the sequential API. It requests cooperative stop
and waits the configured `worker_timeout`, then terminates/kills only the process
it created, with bounded `join_timeout` joins. Worker lifetime is also bounded by
`worker_timeout`. The supervisor never scans or signals other processes.
`job-supervisor.json` retains versions, limits, the plan hash and current entry;
`plan.json` is retained even when the worker never initialized. It records forced
termination as a failure/cancellation with
`restorable_snapshot=false`; it is never a restorable engine snapshot. A safe
recipe checkpoint can still be replayed through sequential resume. If the worker
never wrote a checkpoint, the supervisor records the initial recipe as the safe
restart point. For forced stops, the supervisor archives the last checked recipe
and actions with the sanitized timeout/cancellation cause before updating recovery
metadata. Abnormal exits without worker-written failure evidence receive the same
retention; an existing worker failure report keeps its original cause. These
archives remain available after resume. Hard timeouts and abnormal exits without
an in-process exception are explicitly incompatible with in-process failure
reproduction: repeat `supervise` with the recorded plan and limits instead.
