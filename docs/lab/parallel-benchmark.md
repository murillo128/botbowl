# CPU pool benchmark (OPS-02)

Measured on 2026-09-09 using the [reproduction command](parallel.md#validation-and-benchmark).
The corpus has eight size-3 full-match fragments, four decisions each, master
seed 17, scripted home and random away policies. All 12 runs (three fresh
subprocess repetitions per configuration) produced the same 180 channel rows
and SHA-256 `68a9c1c0c051a3a5ec6257fb29826456565a391263421a083e38d4849373bee5`.
The tests additionally compare complete original manifests and records.

Hardware: Intel Core i7-11700K @ 3.60 GHz, 16 logical CPUs, affinity 0–15,
65,739,692 KiB total RAM; Linux 6.8.0-139, glibc 2.39; CPython 3.11.16,
NumPy 1.26.4, Python pathfinding backend. OPENBLAS_NUM_THREADS and OMP_NUM_THREADS
were both 1. This was a shared host with other regression work running, warm OS
caches, no hardware isolation and no discarded warmup; the figures do not
establish a universal speedup. Versions, load and individual samples are in the
[compact evidence](parallel-benchmark.json). Its implementation hash identifies
the measured coordinator snapshot; subsequent cancellation checks do not change
the uncancelled benchmark workload.

| Mode | Median seconds | Min–max seconds | Sample stdev seconds | Median RSS estimate MiB |
| --- | ---: | ---: | ---: | ---: |
| Sequential | 4.885 | 4.651–5.339 | 0.350 | 56.17 |
| 1 worker | 6.223 | 5.978–6.287 | 0.163 | 108.46 |
| 2 workers | 4.580 | 4.046–4.600 | 0.314 | 162.87 |
| 4 workers | 3.395 | 3.059–3.610 | 0.278 | 271.23 |

Timing includes episode generation, durable per-episode DATA-07 publication and
verification, plus pool startup/spooling/shutdown where applicable. Final
semantic comparison is outside timing. Sequential uses the same generator and
writer without processes. Each pool uses a dispatch window twice its worker
count and a batch size equal to its worker count. No generated datasets are
committed to Git.

Memory uses Linux ru_maxrss, including imports, validation and native allocations.
For a pool, the estimate is parent maximum plus worker count times the largest
observed worker maximum. These maxima need not coincide, so this is a conservative
sum, not a measured simultaneous process-tree peak. Per-parent and per-worker
samples are retained separately. Deterministic admission/backpressure tests,
including a straggler held until its window fills, establish the spool bound
independently of these small-corpus RSS measurements.

Four workers took about 0.69 times the sequential median in this sample, at about
4.8 times its estimated memory. One worker was slower than direct sequential
execution. The CPU pool is useful when the workload amortizes its process and
storage costs; the default stays one process and callers explicitly choose a
worker ceiling and resource budget. CI asserts content and resource contracts,
with no timing threshold or promised speedup.
