"""DATA-07 fidelity, transactional interruption and deterministic bounded IO."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tracemalloc

import pytest

from botbowl.lab.generate import JobConfig, build_plan, execute_plan
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.records import RecordError, encode_json
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.storage import DatasetReader, DatasetWriter, StorageLimits, StorageStats
from botbowl.lab.windows import WindowSpecV1, iter_windows
import botbowl.lab.storage as storage


@pytest.fixture(params=['jsonl', 'parquet'])
def backend(request):
    if request.param == 'parquet':
        pytest.importorskip('pyarrow')
    return request.param


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp('storage-source') / 'generated'
    plan = build_plan(JobConfig(output=str(root), episodes=3, master_seed=17,
                               size=3, max_decisions=8))
    execute_plan(plan, root)
    readers = [EpisodeReader(root, entry['episode_id']) for entry in plan['episodes']]
    splits = build_split_manifest([origin_from_episode(r.manifest) for r in readers],
                                  proportions={'train': 0.7, 'test': 0.3}, seed=17,
                                  split_version='storage-fixture-v1').to_json()
    return plan, readers, splits


LIMITS = StorageLimits(shard_rows=2, shard_bytes=65536, buffer_bytes=65536,
                       file_bytes=131072, decoded_bytes=131072)


def store(root, corpus, backend='jsonl', limits=LIMITS):
    plan, readers, splits = corpus
    with DatasetWriter(root, plan, backend=backend, limits=limits, split_manifest=splits) as writer:
        writer.append_batch(readers)
    return DatasetReader(root)


def rewrite(path, value):
    path.write_bytes(encode_json(value))


def test_roundtrip_batches_windows_and_resume(tmp_path, corpus, backend):
    plan, sources, splits = corpus
    root = tmp_path / backend
    with DatasetWriter(root, plan, backend=backend, limits=LIMITS, split_manifest=splits) as writer:
        writer.append_episode(sources[0])
    old = DatasetReader(root)
    with DatasetWriter(root, plan, backend=backend, limits=LIMITS, split_manifest=splits) as writer:
        assert writer.committed_episodes == 1
        writer.append_batch(sources[1:])
    reader = DatasetReader(root)
    assert len(old) == 1 and len(reader) == 3
    assert reader.split_manifest == splits
    for index, source in enumerate(sources):
        target = reader.episode(source.manifest['episode_id'])
        assert target.manifest == source.manifest
        for channel in source.manifest['files']:
            expected = list(source.iter_channel(channel))
            assert list(target.iter_channel(channel)) == expected
            if len(expected) > 2:
                assert list(target.iter_channel(channel, start=1, stop=len(expected) - 1)) == expected[1:-1]
        spec = WindowSpecV1(3, 2)
        assert list(iter_windows(target, spec, split_manifest=splits)) == list(
            iter_windows(source, spec, split_manifest=splits))
    expected = [row for source in sources for row in source.iter_channel('transitions')]
    batches = list(reader.iter_batches('transitions', batch_rows=2))
    assert [row for batch in batches for row in batch] == expected
    assert all(len(batch) <= 2 for batch in batches)
    assert list(reader.iter_windows(WindowSpecV1(3, 2))) == [
        window for source in sources for window in iter_windows(source, WindowSpecV1(3, 2), split_manifest=splits)]
    assert any(row['macro_id'] is None for row in expected)
    assert all(type(row['action']['type']) is str for row in expected)
    reader.verify()
    with DatasetWriter(root, plan, backend=backend, limits=LIMITS, split_manifest=splits) as writer:
        with pytest.raises(RecordError, match='exceeds generation plan'):
            writer.append_episode(sources[0])
    assert len(DatasetReader(root)) == 3


def test_projection_and_range_do_not_open_privileged_or_unrelated_shards(tmp_path, corpus, backend, monkeypatch):
    reader = store(tmp_path / backend, corpus, backend)
    episode = reader.episode(0)
    opened = []
    original = storage._verified

    @contextmanager
    def guarded(root, info, limits, stats):
        assert '/primary-' in info['path']
        opened.append(info['path'])
        with original(root, info, limits, stats) as stream:
            yield stream

    monkeypatch.setattr(storage, '_verified', guarded)
    stats = reader.stats
    rows = list(episode.iter_channel('primary', fields=['observation_id'], start=1, stop=3))
    assert rows == [{'observation_id': 2}, {'observation_id': 3}]
    assert len(opened) == 2
    if backend == 'parquet':
        assert stats.cells_decoded == stats.rows_decoded  # no channel/context cell decoding
    assert stats.rows_decoded <= 4
    with pytest.raises(RecordError, match='Unknown selected field'):
        list(episode.iter_channel('primary', fields=['rng']))


@pytest.mark.parametrize('stage', ['before_shard_rename', 'after_shard_rename',
                                   'before_manifest_replace', 'after_manifest_replace'])
def test_fault_recovery_exposes_only_complete_batch(tmp_path, corpus, backend, stage):
    plan, sources, splits = corpus
    root = tmp_path / 'dataset'
    with DatasetWriter(root, plan, backend=backend, limits=LIMITS, split_manifest=splits) as writer:
        writer.append_episode(sources[0])
    foreign = root / 'someone-else.partial'
    foreign.write_text('not ours')

    def interrupt(point):
        if point == stage:
            raise InterruptedError(point)

    with DatasetWriter(root, plan, backend=backend, limits=LIMITS,
                       split_manifest=splits, fault=interrupt) as writer:
        with pytest.raises(InterruptedError):
            writer.append_batch(sources[1:])
        with pytest.raises(RecordError, match='not open'):
            writer.append_batch([])
    expected = 3 if stage == 'after_manifest_replace' else 1
    recovered = DatasetReader(root)
    assert len(recovered) == expected
    recovered.verify()
    with DatasetWriter(root, plan, backend=backend, limits=LIMITS, split_manifest=splits) as writer:
        writer.append_batch(sources[writer.committed_episodes:])
    assert len(DatasetReader(root)) == 3
    assert foreign.read_text() == 'not ours'
    assert len(list(DatasetReader(root).iter_batches('transitions', batch_rows=2))) > 0


def test_process_death_releases_lock_and_resume(tmp_path, corpus):
    plan, sources, splits = corpus
    root = tmp_path / 'dataset'
    with DatasetWriter(root, plan, limits=LIMITS, split_manifest=splits) as writer:
        writer.append_episode(sources[0])
        with pytest.raises(RecordError, match='Another writer'):
            DatasetWriter(root, plan, limits=LIMITS, split_manifest=splits)
    script = '''
import json, os, sys
from botbowl.lab.storage import DatasetWriter, StorageLimits
from botbowl.lab.generate import _read_job_json
root = sys.argv[1]
writer = DatasetWriter(root, _read_job_json(__import__('pathlib').Path(root) / 'plan.json'),
    limits=StorageLimits(**json.loads(sys.argv[2])),
    split_manifest=json.load(open(root + '/splits.json')))
os._exit(19)
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(asdict(LIMITS))])
    assert result.returncode == 19
    with DatasetWriter(root, plan, limits=LIMITS, split_manifest=splits) as writer:
        assert writer.committed_episodes == 1


@pytest.mark.parametrize('damage', ['hash', 'missing', 'schema', 'traversal', 'symlink', 'oversized', 'range'])
def test_corrupt_sources_rejected_before_resume(tmp_path, corpus, backend, damage):
    root = tmp_path / 'dataset'
    store(root, corpus, backend)
    index_path = root / 'episode-000000.json'
    index = json.loads(index_path.read_bytes())
    shard = index['shards']['primary'][0]
    path = root / shard['path']
    if damage == 'hash':
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 1
        path.write_bytes(raw)
    elif damage == 'missing':
        path.unlink()
    elif damage == 'schema':
        shard['schema_version'] = 99
    elif damage == 'traversal':
        shard['path'] = '../outside.jsonl'
    elif damage == 'symlink':
        outside = tmp_path / 'outside'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif damage == 'oversized':
        shard['logical_bytes'] = LIMITS.shard_bytes + 1
    else:
        shard['start'] = 1
    rewrite(index_path, index)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['indexes'][0] = {'bytes': index_path.stat().st_size,
                              'sha256': hashlib.sha256(index_path.read_bytes()).hexdigest()}
    rewrite(root / 'manifest.json', manifest)
    with pytest.raises(RecordError):
        DatasetReader(root).verify()
    plan, _, splits = corpus
    with pytest.raises(RecordError):
        DatasetWriter(root, plan, backend=backend, limits=LIMITS, split_manifest=splits)


def test_resume_rejects_different_plan_splits_limits_or_backend(tmp_path, corpus):
    root = tmp_path / 'dataset'
    plan, sources, splits = corpus
    store(root, corpus)
    altered = build_plan(JobConfig(output='unused', episodes=3, master_seed=18, size=3, max_decisions=8))
    for options in ({'plan': altered}, {'limits': replace(LIMITS, shard_rows=3)}, {'split_manifest': None}):
        kwargs = dict(plan=plan, limits=LIMITS, split_manifest=splits)
        kwargs.update(options)
        with pytest.raises(RecordError, match='Resume requires'):
            DatasetWriter(root, **kwargs)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['schema_version'] = 2
    rewrite(root / 'manifest.json', manifest)
    with pytest.raises(RecordError, match='migration'):
        DatasetReader(root)


def test_empty_batch_empty_channels_and_byte_caps(tmp_path, corpus, backend):
    plan, sources, splits = corpus
    root = tmp_path / 'empty'
    with DatasetWriter(root, plan, backend=backend, limits=LIMITS) as writer:
        assert writer.append_batch([]) == 0
    reader = DatasetReader(root)
    assert len(reader) == 0 and list(reader.iter_batches('primary', batch_rows=2)) == []
    assert not list(root.glob('data-*'))
    full = store(tmp_path / 'full', corpus, backend)
    assert list(full.episode(0).iter_channel('macros')) == []
    metadata = json.loads((full.root / 'episode-000000.json').read_bytes())
    assert metadata['shards']['macros'] == []
    tiny = replace(LIMITS, shard_bytes=16)
    with DatasetWriter(tmp_path / 'tiny', plan, backend=backend, limits=tiny) as writer:
        with pytest.raises(RecordError, match='Row exceeds'):
            writer.append_episode(sources[0])
    assert len(DatasetReader(tmp_path / 'tiny')) == 0
    with pytest.raises(RecordError, match='Row exceeds batch'):
        list(full.iter_batches('primary', batch_rows=2, batch_bytes=16))


def test_large_episode_and_scaled_dataset_buffers(tmp_path, backend):
    # Source generation is outside measurements; source-reader counters prove
    # work scales with content while buffering is independent of episode count.
    root = tmp_path / 'source'
    plan = build_plan(JobConfig(output=str(root), episodes=6, max_decisions=24,
                               master_seed=17, scenario='match', size=3))
    execute_plan(plan, root)
    sources = [EpisodeReader(root, e['episode_id']) for e in plan['episodes']]
    peaks = []
    work = []
    for number in (1, 6):
        stats = StorageStats()
        destination = tmp_path / str(number)
        with DatasetWriter(destination, plan, backend=backend, limits=LIMITS, stats=stats) as writer:
            writer.append_batch(sources[:number])
        assert stats.peak_buffer_rows <= 2 and stats.peak_buffer_bytes <= LIMITS.buffer_bytes
        reader = DatasetReader(destination, stats=stats)
        assert sources[0].manifest['files']['transitions']['rows'] == 24
        tracemalloc.start()
        count = sum(len(batch) for batch in reader.iter_batches('primary', batch_rows=2))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        work.append((count, stats.files_opened))
        assert stats.peak_batch_rows <= 2 and stats.peak_batch_bytes <= LIMITS.buffer_bytes
    assert work[1][0] > work[0][0] * 4 and work[1][1] > work[0][1] * 4
    assert peaks[1] < peaks[0] * 2 + 500000


def test_parquet_decompression_and_schema_limits_before_decode(tmp_path, corpus, monkeypatch):
    pytest.importorskip('pyarrow')
    reader = store(tmp_path / 'dataset', corpus, 'parquet')
    episode = reader.episode(0)
    shard = episode._shards['primary'][0]
    with storage._verified(reader.root, shard, reader.limits, reader.stats) as stream:
        with pytest.raises(RecordError, match='decompression limit'):
            storage._parquet_file(stream, shard, replace(reader.limits, shard_bytes=1, buffer_bytes=1, decoded_bytes=1))
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = reader.root / shard['path']
    pq.write_table(pa.table({'observation_id': [1, 2]}), path)
    raw = path.read_bytes()
    shard.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(RecordError, match='schema'):
        list(episode.iter_channel('primary', fields=['observation_id']))


def test_zero_decision_episode_and_batch_failure(tmp_path):
    source = tmp_path / 'source'
    plan = build_plan(JobConfig(output=str(source), episodes=2, max_decisions=0))
    execute_plan(plan, source)
    readers = [EpisodeReader(source, e['episode_id']) for e in plan['episodes']]
    limited = replace(LIMITS, batch_episodes=1)
    root = tmp_path / 'stored'
    with DatasetWriter(root, plan, limits=limited) as writer:
        with pytest.raises(RecordError, match='Episode batch limit'):
            writer.append_batch(readers)
    assert len(DatasetReader(root)) == 0
    with DatasetWriter(root, plan, limits=limited) as writer:
        for reader in readers:
            writer.append_episode(reader)
    result = DatasetReader(root)
    assert len(result) == 2
    assert list(result.iter_batches('transitions', batch_rows=2)) == []
    result.verify()


@pytest.mark.parametrize('stage', ['before_shard_rename', 'after_shard_rename',
                                   'before_manifest_replace', 'after_manifest_replace'])
def test_abrupt_process_exit_at_publication_boundaries(tmp_path, corpus, stage):
    plan, sources, splits = corpus
    root = tmp_path / 'stored'
    with DatasetWriter(root, plan, limits=LIMITS, split_manifest=splits) as writer:
        writer.append_episode(sources[0])
    script = '''
import json, os, sys
from pathlib import Path
from botbowl.lab.generate import _read_job_json
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.storage import DatasetWriter, StorageLimits
root, source, stage = sys.argv[1:4]
plan = _read_job_json(Path(root) / 'plan.json')
def fault(point):
    if point == stage:
        os._exit(23)
with DatasetWriter(root, plan, limits=StorageLimits(**json.loads(sys.argv[4])),
        split_manifest=json.load(open(root + '/splits.json')), fault=fault) as writer:
    writer.append_batch(EpisodeReader(source, entry['episode_id']) for entry in plan['episodes'][1:])
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), str(sources[0].root),
                             stage, json.dumps(asdict(LIMITS))])
    assert result.returncode == 23
    reader = DatasetReader(root)
    assert len(reader) == (3 if stage == 'after_manifest_replace' else 1)
    reader.verify()
    with DatasetWriter(root, plan, limits=LIMITS, split_manifest=splits) as writer:
        writer.append_batch(sources[writer.committed_episodes:])
    assert len(DatasetReader(root)) == 3


def test_windows_never_construct_game_or_open_oracle(tmp_path, corpus, backend, monkeypatch):
    root = tmp_path / 'dataset'
    store(root, corpus, backend)
    from botbowl.core.game import Game
    def forbidden(*args, **kwargs):
        raise AssertionError('Game construction or privileged access')
    monkeypatch.setattr(Game, '__init__', forbidden)
    original = storage._verified
    @contextmanager
    def selected(root, info, limits, stats):
        assert '/primary-' in info['path'] or '/transitions-' in info['path']
        with original(root, info, limits, stats) as stream:
            yield stream
    monkeypatch.setattr(storage, '_verified', selected)
    assert list(DatasetReader(root).iter_windows(WindowSpecV1(2, 2)))


def test_episode_metadata_checksum_and_metadata_budget(tmp_path, corpus):
    root = tmp_path / 'dataset'
    store(root, corpus)
    index_path = root / 'episode-000000.json'
    index = json.loads(index_path.read_bytes())
    index['episode']['end']['reason'] = 'tampered'
    rewrite(index_path, index)
    with pytest.raises(RecordError, match='metadata checksum'):
        DatasetReader(root).episode(0)
    plan, sources, _ = corpus
    limits = replace(LIMITS, metadata_bytes=1024)
    with DatasetWriter(tmp_path / 'bounded', plan, limits=limits) as writer:
        with pytest.raises(RecordError, match='metadata byte limit'):
            writer.append_episode(sources[0])
    assert len(DatasetReader(tmp_path / 'bounded')) == 0
