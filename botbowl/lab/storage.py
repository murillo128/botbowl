"""DATA-07 bounded channel shards and local, single-writer atomic publication.

DATA-02 rows and episode manifests remain the semantic contract. See
``docs/lab/storage.md`` for limits, projection, recovery and trust boundaries.
"""
from contextlib import contextmanager, closing
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from .generate import (_encode_job_json, _decode_job_json, _validate_plan,
                       MAX_JOB_BYTES)
from .recording import _path, _read_file, _sync_directory
from .records import (MAX_RECORD_BYTES, MAX_EPISODE_BYTES, MAX_ROWS,
                      EpisodeManifestV1, RecordError, decode_json, encode_json,
                      integer, keys, require, validate_row)
from .splits import validate_split_manifest


@dataclass(frozen=True)
class StorageLimits:
    """Hard admission caps, not promises about allocator/RSS overhead."""
    shard_rows: int = 256
    shard_bytes: int = 4 * 1024 * 1024
    buffer_bytes: int = 4 * 1024 * 1024
    file_bytes: int = 8 * 1024 * 1024
    decoded_bytes: int = 8 * 1024 * 1024
    batch_episodes: int = 32
    metadata_bytes: int = MAX_RECORD_BYTES

    def __post_init__(self):
        for name, value in asdict(self).items():
            integer(value, 1)
            maximum = MAX_ROWS if name == 'shard_rows' else MAX_EPISODE_BYTES
            require(value <= maximum, 'Storage limit exceeds hard ceiling')
        require(self.batch_episodes <= 1000 and self.metadata_bytes <= MAX_RECORD_BYTES,
                'Metadata/batch limit exceeds hard ceiling')
        require(self.shard_bytes <= self.buffer_bytes <= self.decoded_bytes,
                'Shard/buffer/decode limits are inconsistent')


@dataclass
class StorageStats:
    """Deterministic work counters; no rows, paths or privileged values retained."""
    files_opened: int = 0
    verified_bytes: int = 0
    rows_decoded: int = 0
    cells_decoded: int = 0
    peak_buffer_rows: int = 0
    peak_buffer_bytes: int = 0
    peak_batch_rows: int = 0
    peak_batch_bytes: int = 0


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _hash_valid(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'Invalid checksum')


def _arrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError('Parquet requires the optional botbowl[storage] extra') from error
    return pa, pq


def _atomic(root, relative, payload):
    final = _path(root, relative)
    temporary = _path(root, 'storage-' + uuid.uuid4().hex + '.partial')
    with temporary.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, final)
    _sync_directory(root)


def _json(root, relative, limit):
    return decode_json(_read_file(_path(root, relative), limit))


def _index_name(index):
    return 'episode-%06d.json' % index


@contextmanager
def _verified(root, info, limits, stats):
    """Check size and hash through one regular-file descriptor before parsing."""
    path = _path(root, info['path'])
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                     getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            stats.files_opened += 1
            actual = os.fstat(stream.fileno())
            require(stat.S_ISREG(actual.st_mode) and actual.st_size == info['bytes'] and
                    actual.st_size <= limits.file_bytes, 'Shard size/type mismatch')
            digest, size = hashlib.sha256(), 0
            while True:
                chunk = stream.read(min(65536, limits.file_bytes + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                require(size <= info['bytes'], 'Shard grew during verification')
                digest.update(chunk)
            stats.verified_bytes += size
            require(size == info['bytes'] and digest.hexdigest() == info['sha256'],
                    'Shard checksum mismatch')
            stream.seek(0)
            yield stream
    except OSError as error:
        raise RecordError('Cannot read storage shard') from error


def _parquet_file(stream, info, limits):
    pa, pq = _arrow()
    parquet = pq.ParquetFile(stream, pre_buffer=False, memory_map=False,
                             thrift_string_size_limit=limits.metadata_bytes,
                             thrift_container_size_limit=MAX_ROWS,
                             arrow_extensions_enabled=False)
    metadata = parquet.metadata
    require(metadata.num_row_groups == 1 and metadata.num_rows == info['rows'],
            'Unexpected Parquet row groups/count')
    schema = parquet.schema_arrow
    require(schema.names == info['fields'] and
            all(field.type == pa.binary() for field in schema) and
            schema.metadata is None, 'Unsupported Parquet schema')
    group = metadata.row_group(0)
    size = 0
    for index in range(group.num_columns):
        column = group.column(index)
        require(column.compression == 'ZSTD' and column.file_path in ('', None),
                'Unsupported Parquet compression/external column')
        require(0 <= column.total_uncompressed_size <= limits.decoded_bytes,
                'Parquet decompression limit')
        size += column.total_uncompressed_size
    require(size <= limits.decoded_bytes, 'Parquet decompression limit')
    return parquet


def _rows(root, name, info, backend, limits, stats, fields=None):
    with _verified(root, info, limits, stats) as stream:
        if backend == 'jsonl':
            size = 0
            for _ in range(info['rows']):
                line = stream.readline(min(MAX_RECORD_BYTES + 2, limits.shard_bytes + 1))
                require(line.endswith(b'\n'), 'Partial/oversized JSONL row')
                size += len(line)
                require(size <= info['logical_bytes'], 'Shard logical byte limit')
                row = decode_json(line[:-1])
                validate_row(name, row)
                require(sorted(row) == info['fields'], 'Shard field mismatch')
                stats.rows_decoded += 1
                stats.cells_decoded += len(row)
                yield row if fields is None else {field: row[field] for field in fields}
            require(not stream.read(1) and size == info['logical_bytes'], 'Shard row/byte mismatch')
        else:
            parquet = _parquet_file(stream, info, limits)
            selected = info['fields'] if fields is None else list(fields)
            count, size = 0, 0
            try:
                for batch in parquet.iter_batches(batch_size=limits.shard_rows,
                                                   columns=selected, use_threads=False):
                    require(batch.nbytes <= limits.decoded_bytes, 'Arrow batch byte limit')
                    # Binary canonical JSON cells preserve huge seed integers, enums,
                    # empty containers and explicit nulls without Arrow inference.
                    for index in range(batch.num_rows):
                        row = {}
                        for field in selected:
                            value = batch.column(field)[index].as_py()
                            require(type(value) is bytes, 'Missing canonical JSON cell')
                            row[field] = decode_json(value)
                        if fields is None:
                            validate_row(name, row)
                            size += len(encode_json(row)) + 1
                            require(size <= info['logical_bytes'], 'Shard logical byte limit')
                        stats.rows_decoded += 1
                        stats.cells_decoded += len(selected)
                        count += 1
                        yield row
                require(count == info['rows'] and (fields is not None or size == info['logical_bytes']),
                        'Parquet logical count/size mismatch')
            finally:
                parquet.close()


def _plan_match(manifest, plan, index):
    require(index < len(plan['episodes']), 'Episode exceeds generation plan')
    entry = plan['episodes'][index]
    rules = entry['recipe'].get('rules') if entry['recipe']['kind'] == 'match' else entry['recipe']['spec']['rules']
    require(manifest['episode_id'] == entry['episode_id'] and
            manifest['source_family'] == entry['origin_family_id'] and
            manifest['profile'] == plan['profile'] and
            manifest['scenario_id'] == plan['job']['scenario'] and
            manifest['provenance'] == {'rules': rules, 'policies': entry['policies'],
                                       'seed_plan': entry['seed_plan']},
            'Episode does not match generation plan/order')


class DatasetReader:
    """Immutable committed-prefix snapshot; episode metadata is loaded on demand."""

    def __init__(self, destination, *, limits=None, stats=None):
        self.root = Path(destination).resolve()
        self.stats = stats if stats is not None else StorageStats()
        self.limits = limits or StorageLimits()
        self.limits.__post_init__()
        self._manifest = _json(self.root, 'manifest.json', self.limits.metadata_bytes)
        data = self._manifest
        keys(data, ('schema_version', 'backend', 'limits', 'plan_sha256', 'split_sha256', 'episodes', 'indexes'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'Unknown storage version; explicit migration required')
        require(data['backend'] in ('jsonl', 'parquet'), 'Unsupported storage backend')
        keys(data['limits'], StorageLimits.__dataclass_fields__)
        recorded = StorageLimits(**data['limits'])
        require(all(value <= getattr(self.limits, name) for name, value in asdict(recorded).items()),
                'Dataset exceeds reader limits; explicit larger limits required')
        self.limits = recorded
        _hash_valid(data['plan_sha256'])
        payload = _read_file(_path(self.root, 'plan.json'), MAX_JOB_BYTES)
        require(_digest(payload) == data['plan_sha256'], 'Generation plan checksum mismatch')
        self._plan = _decode_job_json(payload)
        # Plan shape/version/recipe validation does not construct Game objects.
        _validate_plan(self._plan)
        integer(data['episodes'])
        require(data['episodes'] <= len(self._plan['episodes']), 'Invalid committed episode count')
        require(type(data['indexes']) is list and len(data['indexes']) == data['episodes'],
                'Invalid episode index count')
        for info in data['indexes']:
            keys(info, ('bytes', 'sha256'))
            integer(info['bytes'], 1)
            require(info['bytes'] <= self.limits.metadata_bytes, 'Episode metadata byte limit')
            _hash_valid(info['sha256'])
        self._split = None
        if data['split_sha256'] is not None:
            _hash_valid(data['split_sha256'])
            payload = _read_file(_path(self.root, 'splits.json'), self.limits.metadata_bytes)
            require(_digest(payload) == data['split_sha256'], 'Split checksum mismatch')
            self._split = validate_split_manifest(decode_json(payload)).to_json()

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    @property
    def split_manifest(self):
        return deepcopy(self._split)

    def __len__(self):
        return self._manifest['episodes']

    def episode(self, index):
        if type(index) is str:
            index = next((i for i, entry in enumerate(self._plan['episodes'])
                          if entry['episode_id'] == index), -1)
        integer(index)
        require(index < len(self), 'Episode is not committed')
        return StoredEpisodeReader(self, index)

    def iter_batches(self, channel, *, fields=None, batch_rows=32, batch_bytes=None):
        """Yield row batches across episodes without discarding existing row IDs."""
        integer(batch_rows, 1)
        require(batch_rows <= self.limits.shard_rows, 'Batch row limit')
        cap = self.limits.buffer_bytes if batch_bytes is None else batch_bytes
        integer(cap, 1)
        require(cap <= self.limits.buffer_bytes, 'Batch byte limit')
        batch, size = [], 0
        for index in range(len(self)):
            with closing(self.episode(index).iter_channel(channel, fields=fields)) as stream:
                for row in stream:
                    cost = len(encode_json(row)) + 1
                    require(cost <= cap, 'Row exceeds batch byte limit')
                    if batch and (len(batch) == batch_rows or size + cost > cap):
                        yield batch
                        batch, size = [], 0
                    batch.append(row)
                    size += cost
                    self.stats.peak_batch_rows = max(self.stats.peak_batch_rows, len(batch))
                    self.stats.peak_batch_bytes = max(self.stats.peak_batch_bytes, size)
        if batch:
            yield batch

    def iter_windows(self, spec):
        from .windows import iter_windows
        require(self._split is not None, 'Windows require a frozen split manifest')
        for index in range(len(self)):
            with closing(iter_windows(self.episode(index), spec, split_manifest=self._split)) as stream:
                yield from stream

    def verify(self):
        """Exhaust physical checks and DATA-02 row schemas, including privileged channels.

        Whole-episode causal/event validation remains DATA-02's separate audit;
        window iteration additionally checks its selected causal references.
        """
        for index in range(len(self)):
            episode = self.episode(index)
            for channel in episode.manifest['files']:
                for _ in episode.iter_channel(channel):
                    pass


class StoredEpisodeReader:
    """DATA-03 reader protocol over DATA-07 shards; original manifest is intact."""

    def __init__(self, dataset, index):
        self.dataset = dataset
        limits = dataset.limits
        reference = dataset._manifest['indexes'][index]
        payload = _read_file(_path(dataset.root, _index_name(index)), limits.metadata_bytes)
        require(len(payload) == reference['bytes'] and _digest(payload) == reference['sha256'],
                'Episode metadata checksum mismatch')
        info = decode_json(payload)
        keys(info, ('schema_version', 'episode', 'shards'))
        require(type(info['schema_version']) is int and info['schema_version'] == 1,
                'Unknown episode storage version')
        self._manifest = EpisodeManifestV1(info['episode']).to_json()
        _plan_match(self._manifest, dataset._plan, index)
        self._shards = info['shards']
        keys(self._shards, self._manifest['files'])
        seen = set()
        for channel, shards in self._shards.items():
            require(type(shards) is list and len(shards) <= MAX_ROWS, 'Shard count limit')
            rows, size = 0, 0
            for shard in shards:
                keys(shard, ('path', 'schema_version', 'start', 'rows', 'logical_bytes',
                             'bytes', 'sha256', 'fields'))
                require(type(shard['schema_version']) is int and shard['schema_version'] == 1,
                        'Unknown shard schema')
                for name in ('start', 'rows', 'logical_bytes', 'bytes'):
                    integer(shard[name])
                require(shard['start'] == rows and 1 <= shard['rows'] <= limits.shard_rows and
                        1 <= shard['logical_bytes'] <= limits.shard_bytes and
                        1 <= shard['bytes'] <= limits.file_bytes, 'Shard range/size limit')
                _path(dataset.root, shard['path'])
                suffix = 'jsonl' if dataset._manifest['backend'] == 'jsonl' else 'parquet'
                require(re.fullmatch(r'data-[0-9a-f]{32}/' + channel + r'-[0-9]{6}\.' + suffix,
                                     shard['path']) and shard['path'] not in seen,
                        'Invalid or aliased shard path')
                seen.add(shard['path'])
                _hash_valid(shard['sha256'])
                fields = shard['fields']
                require(type(fields) is list and 1 <= len(fields) <= 64 and
                        all(type(f) is str and re.fullmatch('[a-z][a-z0-9_]*', f) for f in fields) and
                        fields == sorted(set(fields)), 'Invalid shard fields')
                rows += shard['rows']
                size += shard['logical_bytes']
            source = self._manifest['files'][channel]
            require(rows == source['rows'] and size == source['bytes'], 'Channel shard coverage mismatch')

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    def iter_channel(self, name, *, fields=None, start=0, stop=None):
        require(name in self._shards, 'Missing requested channel')
        integer(start)
        total = self._manifest['files'][name]['rows']
        stop = total if stop is None else stop
        integer(stop)
        require(start <= stop <= total, 'Invalid channel range')
        if fields is not None:
            require(type(fields) in (list, tuple) and bool(fields) and
                    all(type(field) is str for field in fields) and
                    len(set(fields)) == len(fields), 'Select explicit unique fields')
        dataset = self.dataset
        digest = hashlib.sha256()
        for shard in self._shards[name]:
            if fields is not None:
                require(set(fields) <= set(shard['fields']), 'Unknown selected field')
            if start >= shard['start'] + shard['rows'] or stop <= shard['start']:
                continue
            with closing(_rows(dataset.root, name, shard, dataset._manifest['backend'],
                               dataset.limits, dataset.stats, fields)) as stream:
                for offset, row in enumerate(stream, shard['start']):
                    if fields is None and start == 0 and stop == total:
                        digest.update(encode_json(row) + b'\n')
                    if start <= offset < stop:
                        yield row
        if fields is None and start == 0 and stop == total:
            require(digest.hexdigest() == self._manifest['files'][name]['sha256'],
                    'Source semantic channel checksum mismatch')


class DatasetWriter:
    """Append generation-plan episodes in order, atomically per bounded batch.

    Failure poisons the writer: close and reopen to discover whether publication
    happened. Never retry on the same instance after an ambiguous interruption.
    """

    def __init__(self, destination, plan, *, backend='jsonl', limits=None,
                 split_manifest=None, stats=None, fault=None):
        self.root = Path(destination).resolve()
        self.limits = limits or StorageLimits()
        self.limits.__post_init__()
        require(backend in ('jsonl', 'parquet'), 'Unsupported storage backend')
        if backend == 'parquet':
            _arrow()
        _validate_plan(plan)
        payload = _encode_job_json(plan)
        self.plan = _decode_job_json(payload)
        split = None if split_manifest is None else validate_split_manifest(split_manifest).to_json()
        split_payload = None if split is None else encode_json(split)
        require(split_payload is None or len(split_payload) <= self.limits.metadata_bytes, 'Split byte limit')
        self.backend = backend
        self.stats = stats if stats is not None else StorageStats()
        self.fault = fault or (lambda stage: None)
        self.closed, self.failed = False, False
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = None
        try:
            import fcntl
            lock = _path(self.root, 'writer.lock')
            fd = os.open(str(lock), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NONBLOCK', 0), 0o600)
            self._lock = os.fdopen(fd, 'r+b')
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'Invalid writer lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RecordError('Another writer owns this dataset') from error
            expected = {'schema_version': 1, 'backend': backend, 'limits': asdict(self.limits),
                        'plan_sha256': _digest(payload), 'split_sha256': None if split_payload is None
                        else _digest(split_payload), 'episodes': 0, 'indexes': []}
            manifest_path = _path(self.root, 'manifest.json')
            if manifest_path.exists():
                existing = DatasetReader(self.root, limits=self.limits)
                require({**existing.manifest, 'episodes': 0, 'indexes': []} == expected,
                        'Resume requires identical versions, limits, backend, plan and splits')
                existing.verify()
                self._manifest = existing.manifest
            else:
                # Initialization may be retried only with exactly the same plan.
                for name, content in (('plan.json', payload), ('splits.json', split_payload)):
                    path = _path(self.root, name)
                    if path.exists():
                        require(content is not None and _read_file(path, MAX_JOB_BYTES) == content,
                                'Uncommitted initialization identity mismatch')
                    elif content is not None:
                        _atomic(self.root, name, content)
                encoded = encode_json(expected)
                require(len(encoded) <= self.limits.metadata_bytes, 'Root metadata byte limit')
                _atomic(self.root, 'manifest.json', encoded)
                self._manifest = expected
        except BaseException:
            self.close()
            raise

    @property
    def committed_episodes(self):
        return self._manifest['episodes']

    def _shard(self, directory, name, ordinal, start, lines):
        extension = 'jsonl' if self.backend == 'jsonl' else 'parquet'
        relative = '%s/%s-%06d.%s' % (directory.name, name, ordinal, extension)
        final = _path(self.root, relative)
        temporary = final.with_name(final.name + '.partial')
        fields = sorted(decode_json(lines[0][:-1]))
        if self.backend == 'jsonl':
            with temporary.open('xb') as stream:
                for line in lines:
                    stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            pa, pq = _arrow()
            # Convert one bounded shard; never a whole channel/episode/dataset.
            columns = {field: [] for field in fields}
            for line in lines:
                row = decode_json(line[:-1])
                require(sorted(row) == fields, 'Channel field shape changed')
                for field in fields:
                    columns[field].append(encode_json(row[field]))
            table = pa.table(columns, schema=pa.schema([(field, pa.binary()) for field in fields]))
            require(table.nbytes <= self.limits.decoded_bytes, 'Arrow conversion byte limit')
            with temporary.open('xb') as stream:
                pq.write_table(table, stream, row_group_size=self.limits.shard_rows,
                               compression='zstd', use_dictionary=False, write_statistics=False,
                               store_schema=False, data_page_size=min(65536, self.limits.shard_bytes),
                               write_batch_size=1)
                stream.flush()
                os.fsync(stream.fileno())
        info = {'path': relative, 'schema_version': 1, 'start': start, 'rows': len(lines),
                'logical_bytes': sum(map(len, lines)), 'bytes': temporary.stat().st_size,
                'sha256': '', 'fields': fields}
        require(info['bytes'] <= self.limits.file_bytes, 'Encoded shard file limit')
        digest = hashlib.sha256()
        with temporary.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        info['sha256'] = digest.hexdigest()
        # Read-back verification before rename, using the same codec/limits.
        temporary_info = {**info, 'path': str(temporary.relative_to(self.root))}
        for _ in _rows(self.root, name, temporary_info, self.backend, self.limits, self.stats):
            pass
        self.fault('before_shard_rename')
        os.rename(temporary, final)
        _sync_directory(directory)
        self.fault('after_shard_rename')
        return info

    def _episode(self, reader, index):
        manifest = EpisodeManifestV1(reader.manifest).to_json()
        _plan_match(manifest, self.plan, index)
        directory = _path(self.root, 'data-' + uuid.uuid4().hex)
        directory.mkdir()
        _sync_directory(self.root)
        shards = {}
        metadata_size = len(encode_json(manifest)) + 128
        def add_shard(name, info):
            nonlocal metadata_size
            metadata_size += len(encode_json(info)) + 1
            require(metadata_size <= self.limits.metadata_bytes, 'Episode metadata byte limit')
            shards[name].append(info)

        for name, source in manifest['files'].items():
            shards[name] = []
            lines, size, count, total_bytes = [], 0, 0, 0
            metadata_size += len(name) + 8
            digest = hashlib.sha256()
            with closing(reader.iter_channel(name)) as stream:
                for row in stream:
                    validate_row(name, row)
                    line = encode_json(row) + b'\n'
                    require(len(line) <= self.limits.shard_bytes, 'Row exceeds shard byte limit')
                    if lines and (len(lines) == self.limits.shard_rows or size + len(line) > self.limits.shard_bytes):
                        add_shard(name, self._shard(directory, name, len(shards[name]), count - len(lines), lines))
                        lines, size = [], 0
                    lines.append(line)
                    size += len(line)
                    count += 1
                    total_bytes += len(line)
                    require(total_bytes <= source['bytes'], 'Extra source bytes')
                    require(count <= source['rows'], 'Extra source rows')
                    digest.update(line)
                    self.stats.peak_buffer_rows = max(self.stats.peak_buffer_rows, len(lines))
                    self.stats.peak_buffer_bytes = max(self.stats.peak_buffer_bytes, size)
            if lines:
                add_shard(name, self._shard(directory, name, len(shards[name]), count - len(lines), lines))
            require(count == source['rows'] and total_bytes == source['bytes'] and digest.hexdigest() == source['sha256'],
                    'Source channel count/content mismatch')
        payload = encode_json({'schema_version': 1, 'episode': manifest, 'shards': shards})
        require(len(payload) <= self.limits.metadata_bytes, 'Episode metadata byte limit')
        _atomic(self.root, _index_name(index), payload)
        return {'bytes': len(payload), 'sha256': _digest(payload)}

    def append_batch(self, readers):
        require(not self.closed and not self.failed, 'Writer is not open')
        count, indexes = 0, []
        try:
            for reader in readers:
                require(count < self.limits.batch_episodes, 'Episode batch limit')
                indexes.append(self._episode(reader, self.committed_episodes + count))
                count += 1
            if count:
                candidate = {**self._manifest, 'episodes': self.committed_episodes + count,
                             'indexes': self._manifest['indexes'] + indexes}
                encoded = encode_json(candidate)
                require(len(encoded) <= self.limits.metadata_bytes, 'Root metadata byte limit')
                self.fault('before_manifest_replace')
                _atomic(self.root, 'manifest.json', encoded)
                self.fault('after_manifest_replace')
                self._manifest = candidate
            return count
        except BaseException:
            self.failed = True
            raise

    def append_episode(self, reader):
        return self.append_batch([reader])

    def close(self):
        if self._lock is not None:
            self._lock.close()  # flock releases on close/crash; never unlink the lock inode.
            self._lock = None
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
