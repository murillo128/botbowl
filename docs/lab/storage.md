# Sharded laboratory datasets (DATA-07)

`botbowl.lab.storage` stores DATA-02 episode channels in bounded shards. The
original episode manifest and canonical row bytes define semantic identity;
physical compression and fragment layout do not change it. Decisions remain in
`transitions`, entities retain their ordered stable IDs in `primary` observations,
and events retain their episode/branch/sequence IDs and decision references.
There is no Game reconstruction, pickle, external storage service or credential.

## Write and resume

```python
from botbowl.lab.generate import JobConfig, build_plan, execute_plan
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.storage import DatasetWriter, DatasetReader, StorageLimits

plan = build_plan(JobConfig(output='/tmp/source', episodes=8, master_seed=17))
execute_plan(plan, '/tmp/source')
limits = StorageLimits(shard_rows=256)
with DatasetWriter('/tmp/sharded', plan, limits=limits) as writer:
    start = writer.committed_episodes
    writer.append_batch(EpisodeReader('/tmp/source', entry['episode_id'])
                        for entry in plan['episodes'][start:start + limits.batch_episodes])
```

Repeat bounded batches until the plan is complete. `append_episode(reader)` is a
one-episode batch. Sources implement `manifest` and `iter_channel(name)`; the
existing `EpisodeReader` is the reference implementation. This is an explicit
conversion/append API, not a change to DATA-01 generation or DATA-02 recording.
The generator's own per-episode memory limits remain unchanged. A future pool
coordinator (#36) must feed this single writer rather than opening concurrent
writers on the same destination.

Episodes must be an ordered prefix of the exact DATA-01 plan, including profile,
family, scenario, rule/config descriptor, policy identity and seed provenance.
Committed episodes cannot be appended again. Reopening requires the same plan,
backend, limits and optional frozen `split_manifest`, verifies all committed
shards and semantic channel checksums, and returns the committed count. It never
infers missing fields or accepts another schema/version approximately. Plan
validation also checks the supported generator and installed rule/backend
identity, as DATA-01 does. Move incompatible data only through an explicit
migration, not by bypassing those checks.

## Read channels, columns, batches and windows

```python
reader = DatasetReader('/tmp/sharded')
episode = reader.episode(0)  # or the planned episode_id
for row in episode.iter_channel('transitions', fields=['transition_id', 'action'],
                                start=1, stop=4):
    print(row)
for batch in reader.iter_batches('primary', batch_rows=32, batch_bytes=1024 * 1024):
    consume(batch)
```

Ranges are zero-based half-open channel row ordinals. Shards outside a range are
not opened. Fields are explicit unique top-level row keys; nested entity/action
objects retain their existing DATA-02 shape. JSONL parses the selected channel's
whole rows before projection. Parquet decodes only selected physical columns.
Each top-level field is a binary canonical JSON cell, rather than inferred Arrow
nested types: JSON null, masks, enums, empty containers, IDs and 256-bit seed
integers round-trip exactly. This candidate does **not** provide nested-leaf
projection, predicate pushdown, or direct numeric Arrow training arrays.

Checksums read the compressed bytes of each selected shard before it yields
rows. Thus Parquet projection saves decoding/allocation, but still pays the
whole selected shard's integrity scan. `StorageStats` counts those bytes, opened
files, decoded rows/cells and peak admitted row/byte buffers without retaining
payloads. Selecting primary/transition channels never opens privileged,
evaluation, or control observation files; provenance seeds in metadata are not
model inputs. Closing a generator releases its stream. Full scans also match the
original semantic channel checksum. Partial/projection scans validate selected
physical fragments and fields, not omitted channels or whole-episode causality.
`verify()` checks every channel's rows and checksums; DATA-02 `read_episode()`
remains the whole-episode causal/event audit for reference sources.

Pass a frozen DATA-04 `split_manifest` when creating the writer to retain split
identity. Because original episode manifests are preserved, their source digests
and origin families continue to match the split manifest. Use episode IDs as
split source IDs with the convenience dataset window iterator:

```python
from botbowl.lab.windows import WindowSpecV1, iter_windows
for sample in reader.iter_windows(WindowSpecV1(history_length=3, horizon=2)):
    consume(sample['inputs'])
# For an external split manifest or explicit custom source_id, use DATA-03 directly:
windows = iter_windows(episode, WindowSpecV1(3, 2),
                       split_manifest=frozen_splits, source_id=source_id)
```

The DATA-03 implementation supplies causal joining, masks, history/lookahead and
branch boundaries unchanged; its output is tested against the JSONL reference
across shard boundaries. No alternate window semantics are introduced.

## Atomicity and local ownership

This version requires a local POSIX filesystem with `flock`, same-filesystem
rename and directory `fsync`. A persistent `writer.lock` inode is locked
nonblockingly for the writer lifetime; another writer is rejected. Close or
process death releases the lock. The inode is never unlinked, avoiding races
between old and replacement locks. Do not use this protocol on network/object
storage with different lock or durability semantics.

A new shard is written under a unique `data-<uuid>` directory with a `.partial`
suffix, flushed, closed, read back and verified, renamed, and directory-synced.
Each episode's bounded index contains its original manifest and channel shard
ranges, row counts, logical/physical sizes, version, fields and SHA-256 hashes.
Indexes are flushed and atomically replaced before the root manifest publishes
the batch. The root contains the committed prefix length, index sizes/checksums,
plan/split checksums, backend and limits. Its replacement is the commit point;
all files it names already exist and have been synced.

An interruption before that point leaves the previous committed prefix visible.
After replacement the whole batch is visible. An exception after replacement is
ambiguous to the caller: the writer is poisoned and must be closed/reopened;
consult `committed_episodes` before retrying. A reader snapshots the root manifest
at construction and keeps seeing its original prefix while another batch commits.
Empty batches do not publish anything; empty channels need no data file.

Orphaned temporary files, directories and unpublished indexes are never data.
Recovery ignores them and uses fresh unique shard paths; it can replace only
uncommitted reserved episode indexes. This implementation does not garbage
collect orphans or remove foreign temporary files. Operator cleanup, if needed,
must run with exclusive ownership and identify referenced files first.

## Resource and trust limits

Defaults are 256 rows and 4 MiB canonical JSONL bytes per shard, 4 MiB buffered
canonical bytes, 8 MiB physical bytes, 8 MiB uncompressed Parquet column-page
metadata bytes, 32 episodes per atomic batch and 4 MiB per metadata document.
An oversized individual row is rejected, not split or truncated. Large episodes
span shards while retaining DATA-02's 128 MiB episode and 100,000-row channel
caps. Metadata growth is admitted incrementally; one episode's shard descriptors
are bounded rather than keeping the entire dataset's fragment metadata resident.
The root has at most the generator's 1,000 compact episode references. The plan
retains DATA-01's explicit 128 MiB/1,000-episode cap and is loaded in memory.

These are logical admission bounds, not a byte-exact RSS reservation. Codec
conversion, Python objects, native allocations and one pending source row add a
bounded overhead. Read batches have separate row and canonical-byte caps;
Parquet holds at most one bounded row group, and windows add their declared
history/horizon storage. Dataset iteration does not accumulate rows or windows.
The tests multiply dataset content while measuring memory and counting IO,
decoded work, and admitted buffers independently. Callers that collect generators
into lists take responsibility for that additional memory.

Reader caps default to `StorageLimits()`; opening data recorded with larger
limits requires explicitly passing sufficient reader limits. Hard ceilings still
apply. Shard paths must be literal, non-symlink descendants with the versioned
channel/UUID layout. Readers reject missing/non-regular/oversized files, unknown
versions, mismatched schemas/ranges, wrong hashes and malformed bounded JSON.
Parquet is limited to binary fields, one bounded row group and ZSTD, with bounded
Thrift metadata and declared total uncompressed column sizes checked before
batch decoding. Arrow extension deserialization is disabled.

SHA-256 detects corruption; it does not authenticate a producer or grant access.
Filesystem permissions and trusted immutable publication remain necessary.
Limits are not a sandbox against malicious native-code inputs or a hostile
process rewriting files during reads. Do not accept arbitrary Parquet from an
untrusted producer into a privileged service solely because its checksum matches.

## Backend comparison

Install the optional candidate with `pip install '.[storage]'`. JSONL has no
additional dependency; Parquet uses PyArrow and ZSTD. The benchmark reuses
PERF-01's environment capture and repetition/dispersion summaries:

```sh
python -m botbowl.benchmarks.storage --output /tmp/storage-report.json \
  --episodes 8 --decisions 16 --repetitions 3 --seed 17
```

It generates one DATA-01 corpus, runs each codec in fresh subprocesses, and checks
identical semantic hashes/counts for sequential records, projected decisions and
DATA-03 windows. Durable write/read-back verification, integrity scans and semantic
hashing are included in timings. Separate repetitions with `tracemalloc` measure
Python peaks without contaminating timing samples; per-process maximum RSS also
captures native allocation and import costs. OS caches are warm, and neither
hardware isolation nor cold-cache performance is claimed. Generated datasets
and raw reports stay outside Git. The measured recommendation and compact result
are recorded in [storage benchmark](storage-benchmark.md); CI has no timing threshold.
