# Reproducible CPU pool (OPS-02)

`botbowl.lab.parallel` runs a DATA-01 generation plan with persistent CPU
processes using the `spawn` context. The parent validates and detaches the whole
plan before dispatch: episode/family IDs, recipe, rules/configuration, policies,
purpose-specific seeds and decision/horizon budgets are the existing generator
contract. Every task constructs and closes its own session, Game, policies and
recording buffers. No PID, worker number, clock, completion order or Python
`hash()` participates in seed derivation or episode content.

## API and CLI

```python
from botbowl.lab.generate import JobConfig, build_plan
from botbowl.lab.parallel import PoolConfig, SPOOL_EPISODE_BYTES, execute_plan
from botbowl.lab.storage import DatasetReader

if __name__ == '__main__':  # required by multiprocessing spawn
    plan = build_plan(JobConfig(output='unused', episodes=8, master_seed=17,
                                scenario='pickup', max_decisions=8))
    pool = PoolConfig(workers=2, worker_limit=2, max_in_flight=4, batch_size=2,
                      memory_budget_bytes=5 * 1024**3,
                      spool_budget_bytes=4 * SPOOL_EPISODE_BYTES)
    result = execute_plan(plan, '/tmp/pool-example', pool=pool)
    for episode in result['episodes']:
        print(episode['episode_id'], episode['status'], episode['error'])
    DatasetReader('/tmp/pool-example').verify()
```

The default is one worker, one in-flight episode and one request per dispatch
batch. A larger worker count requires an explicit `worker_limit`, memory budget
and dispatch/spool capacity; no CPU-count auto-expansion occurs. Pool settings
never enter the semantic generation plan. `generate(JobConfig(...), pool=...)`
is the convenience API. `storage_limits=StorageLimits(...)` controls the existing
JSONL writer's shard and buffer sizes. The pool uses the recommended JSONL backend.

```sh
python -m botbowl.lab.parallel --output /tmp/pool-small --episodes 4 \
  --seed 17 --scenario pickup --decisions 4
# Resume the exact plan, with two workers and four admitted episodes:
python -m botbowl.lab.parallel --output /tmp/pool-small \
  --plan /tmp/pool-small/plan.json --workers 2 --worker-limit 2 \
  --max-in-flight 4 --batch-size 2 --memory-budget-bytes 5368709120 \
  --spool-budget-bytes 587202560
```

The CLI prints the operational report and exits 0 only when every episode is
committed, 1 for an incomplete job, and 2 for invalid configuration/input or an
operational exception. Supply a generator `plan.json` for policy configuration,
board size, episode prefix, scenario parameters or fragments beyond the small
CLI defaults. Plans accept registered declarative policies and recipes only;
there is no callable, arbitrary module import or pickle input.

## Ordering, failures and cancellation

Only the parent opens the DATA-07 `DatasetWriter`. Workers publish private
DATA-02 episode spools and compact JSON result notifications; the parent streams
these into the writer. Final episode indexes follow canonical `episode_id`
order. Shard UUIDs, PIDs, memory counters, retry counts and operational logs are
audit/layout information, not semantic content. Compare the original episode
manifest and channel records, or the generator's `semantic_sha256`.

`batch_size` bounds new requests and ready results processed per coordinator
iteration. Each result retains its own ID and error. Each successful episode is
committed separately through DATA-07's atomic manifest replacement; a batch
makes no global atomicity promise. The root `manifest.json` is the sole authority
for committed data. Reopening verifies hashes, versions, limits and the identical
plan, skips the committed prefix and dispatches only unfinished episodes.

`retries` defaults to zero, with a maximum of ten. A worker crash, exception or
wall timeout produces a bounded cause chain in a separate `pool-audit-*.jsonl`
file. A retry uses the identical plan entry and seed streams in a new worker;
it does not replace a failed episode with a different seed. Retried failures
remain in the audit even if the final result succeeds. Exception text is bounded
operational data and must not be used as model input.

DATA-07 commits an ordered prefix. When retries are exhausted, dispatch stops,
already-running tasks are harvested subject to their deadlines, and the available
successful prefix is committed. Later successful results have status
`uncommitted`; untouched requests have status `not_dispatched`. The failing
request is `failed`. A later invocation reruns the uncommitted suffix using its
original identities. This intentionally preserves the storage contract rather
than claiming successful work beyond a permanent gap is committed.

Pass a parent-side Event-like `cancel` with `is_set()`. Cancellation stops
dispatch, harvests already published results, commits their contiguous prefix,
and terminates remaining workers. Uncollected active requests are `cancelled`.
KeyboardInterrupt also closes the job while retaining prior commits. Workers
are terminated and joined against shared deadlines, then killed and joined
against another deadline if necessary. Only this job's process objects and
private temporary directory are closed; no global child-process or queue cleanup
runs. There are no multiprocessing task/result queues or feeder threads.

Writer exceptions stop publication and dispatch. The coordinator rereads the
storage manifest because a failure can occur after its atomic replacement;
an already committed episode is never reported as failed or appended twice.
While holding the writer lock, the coordinator removes only newly created,
uncommitted storage fragments from the failed write; pre-existing and committed
paths remain intact. KeyboardInterrupt during publication follows the same
reconciliation and cleanup: the report marks the job cancelled and the interrupted
episode committed if published, otherwise cancelled. Resume skips all confirmed
episodes. The pool's private spool is removed after workers have exited. Hard termination
of the parent or power loss may leave unadvertised temporary data, as documented
by storage; exactly-once execution after unobservable external failures is not
promised.

## Resource bounds

An admitted episode holds a dispatch-window slot until commit or failure cleanup,
including time spent waiting behind a straggler. Completed spools therefore count
against `max_in_flight`; a fast worker cannot create an unlimited backlog.
`peak_in_flight` and `peak_buffered` count admitted episodes and collected,
uncommitted successful results. The parent retains compact status records, not
whole episode payloads, and streams one bounded storage shard at a time.

Each slot reserves `SPOOL_EPISODE_BYTES`: DATA-02's 128 MiB combined channel cap
plus three 4 MiB metadata allowances for its manifest, request and result. The
entire dispatch window must fit `spool_budget_bytes` before work starts. This is
a logical file-content reservation, excluding filesystem allocation granularity
and the growing final dataset. A retry removes its old spool before reuse.

`workers * worker_memory_bytes + coordinator_memory_bytes` must fit
`memory_budget_bytes`. Defaults reserve 2 GiB virtual memory per worker and
512 MiB for the coordinator within a 4 GiB admission budget. Each worker applies
POSIX `RLIMIT_AS` before constructing episodes, preserving any stricter inherited
limit. Interpreter/import bootstrap precedes that limit. The coordinator
reservation is an admission estimate, not a process-wide hard memory limit;
plan decoding, Python objects and storage codec allocations add overhead within
their existing bounded contracts. Measure real RSS for your corpus and adjust
the reservation. The limit is not a sandbox for hostile code or files.

`episode_timeout` (default 120 seconds) includes time since dispatch, including
worker startup and prior task cleanup; it excludes waiting in the completed
spool buffer. `join_timeout` defaults to two seconds per shared shutdown stage.
The coordinator checks results every 10 ms; local filesystem reads/fsync follow
the same availability assumptions as DATA-07. Native numerical library thread
limits remain caller-owned; `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1` is useful
when measuring CPU process scaling. No GPU, cluster or training dependency is
introduced.

## Validation and benchmark

`tests/lab/test_parallel.py` is included in required lab CI. It compares every
record against DATA-01 sequential generation under 1/2/4 workers, reordered
completion and different batch/shard sizes, and exercises crashes, identical
retries, bounded stragglers, timeouts, cancellation, resume and writer failures.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m botbowl.benchmarks.parallel --output /tmp/parallel-report.json \
  --episodes 8 --decisions 4 --repetitions 3 --seed 17
```

The benchmark reuses PERF-01 environment capture and repetition/dispersion
summaries. Sequential execution and 1/2/4-worker runs generate the same full-match
fragments through DATA-01 and commit each episode through the same JSONL writer.
Fresh subprocess repetitions include generation, durable writes/read-back
verification, worker startup and shutdown. Semantic comparisons are exhaustive
and outside timing. Linux peak RSS includes imports/native allocations; the pool
memory estimate sums the parent peak and worker maxima rather than claiming a
measured simultaneous peak. Raw data stays outside Git. The measured result is
in [parallel benchmark](parallel-benchmark.md); there is no CI speedup threshold.
