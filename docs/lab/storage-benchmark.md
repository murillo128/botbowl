# DATA-07 storage benchmark

## Recommendation

Keep **JSONL as the general-purpose default** for the measured workload. It has
lower process peak memory, faster full sequential reads, and no optional codec
dependency. Parquet is a supported alternative when disk footprint is the
priority: it reduced total stored bytes by 82.2% on the identical corpus.
Its projected decision reads were faster, but it did not improve window throughput.
Write timings overlap substantially; no reliable write speedup is claimed.

This is an eight-episode corpus, not evidence of universal performance at large
scale. Repeat the included CLI with representative episode lengths and counts
before changing a production workload’s default. Bounded buffering is separately
covered by deterministic work/IO counters and dataset-multiplication tests.

## Reproduction and environment

Run from this implementation with the storage extra installed:

```sh
python -c 'import os; os.sched_setaffinity(0, {max(os.sched_getaffinity(0))}); from botbowl.benchmarks.storage import main; main()' \
  --output /tmp/storage-report.json --episodes 8 --decisions 16 --repetitions 3 --seed 17
```

- Date: 2026-09-09; CPU: 11th Gen Intel(R) Core(TM) i7-11700K @ 3.60GHz.
- Linux-6.8.0-139-generic-x86_64-with-glibc2.39; Python 3.12.3; CPU affinity `[15]`.
- Botbowl 2.0.0a1, NumPy 1.26.4, PyArrow 25.0.1, pytest 9.1.1; Python pathfinding.
- Shared 16-logical-CPU host; initial load averages 10.84 / 12.12 / 10.99. No exclusive hardware reservation.
- DATA-01 match scenario, size 3, seed 17, eight episodes × 16 decisions. All 473 channel rows and 128 windows match the JSONL source.
- Same canonical content and shard boundaries: 32 rows/256 KiB per shard, 256 KiB logical buffer, 512 KiB physical/decode caps; Parquet uses ZSTD and one row group per shard.
- Three fresh-worker timing repetitions and three separate memory repetitions per backend. Warm OS caches; durable writes, read-back verification, integrity scans, row validation and semantic hashing included.
- Timing has no tracemalloc overhead. Python memory peaks are from the separate traced runs; process maximum RSS includes interpreter, imports, source metadata and native allocations, and is not a per-operation incremental allocation.

## Results

Time cells are **median seconds (sample standard deviation)**. Samples are retained below so dispersion is inspectable without the raw report.

| Operation | JSONL | Parquet |
| --- | ---: | ---: |
| write | 1.9080 (0.3653) | 1.7782 (0.0517) |
| sequential | 0.4431 (0.1378) | 0.6442 (0.0295) |
| projection | 0.0393 (0.0007) | 0.0289 (0.0019) |
| windows | 3.5392 (0.3499) | 3.6083 (0.2224) |

| Footprint | JSONL | Parquet |
| --- | ---: | ---: |
| Total dataset bytes, median | 1800251 | 319736 |
| Process peak RSS, median MiB (SD) | 53.58 (0.00) | 101.94 (0.06) |
| write Python peak, median MiB (SD) | 1.81 (0.00) | 4.83 (0.00) |
| sequential Python peak, median MiB (SD) | 4.11 (0.00) | 4.12 (0.01) |
| projection Python peak, median MiB (SD) | 4.12 (0.00) | 4.12 (0.00) |
| windows Python peak, median MiB (SD) | 4.28 (0.00) | 4.30 (0.01) |

| Operation | JSONL timing samples (s) | Parquet timing samples (s) |
| --- | --- | --- |
| write | 1.9080, 2.2732, 1.5426 | 1.7782, 1.6929, 1.7861 |
| sequential | 0.4431, 0.6801, 0.4398 | 0.6442, 0.6049, 0.6627 |
| projection | 0.0393, 0.0398, 0.0384 | 0.0312, 0.0274, 0.0289 |
| windows | 3.5392, 3.0569, 3.7371 | 3.3444, 3.7865, 3.6083 |

The output `StorageStats` counters include verification IO; projected Parquet
columns still require an integrity scan of the selected compressed fragment.
Both backends therefore perform the same semantic checks, but Parquet avoids
decoding unselected cells. The observed space reduction is useful without
implying a general throughput or memory improvement.

## Semantic evidence

Every timed and memory sample matched the reference count/hash for each stream:

| Stream | Rows | Canonical SHA-256 |
| --- | ---: | --- |
| projection | 128 | `e0fd889d306c020cc4bf468ec1aa0dc536e06728cccd9f7740754d16d95376c8` |
| sequential | 473 | `e0b2f81eb0deecc10dad212aa578e1805d896bbc08d7183c4d3b2b350e27533b` |
| windows | 128 | `2843f325fd05250f65fe1ec56393b8960a9a60e309db75ba3bcf341372e54afd` |

Measured `botbowl/lab/storage.py` SHA-256:
`d1cb780854578cdd4c8efb273543ef61b0dc4cf85ae4f84020d25908ef10c1fb`.

Raw JSON reports and generated datasets remain outside Git. The CLI records
hardware, package versions, source/diff identities, repeated samples, counters
and semantic identities. CI exercises correctness and memory admission, with no
wall-clock performance threshold.
