"""Small local consumer of frozen DATA-02/03/04 contracts, without a Game.

Only ``sample['inputs']`` is encoder input. Future public observations and audit
metadata remain separate. This reader grants no oracle or snapshot capability.
"""
from copy import deepcopy
from pathlib import Path

from .channels import InputProfile
from .recording import EpisodeReader
from .records import (MAX_RECORD_BYTES, decode_json, encode_json, integer, keys,
                      require, safe_relative)
from .splits import SplitManifestV1, origin_from_episode, validate_split_manifest
from .windows import WindowSpecV1, iter_windows


_OBSERVATION = {'schema': 'ObservationV1', 'schema_version': 1}
_VIEW = {'observer_team': 'home', 'coordinates': 'absolute'}


class DatasetManifestV1:
    """Closed local dataset manifest; window policy includes time and channels.

    Paths are relative to this manifest's directory. The frozen split inventory
    must describe exactly these episodes, including their content digests.
    """

    def __init__(self, data):
        data = decode_json(encode_json(data))
        keys(data, ('schema_version', 'observation', 'view', 'window',
                    'split_manifest', 'episodes'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'Unknown dataset client version')
        require(data['observation'] == _OBSERVATION and
                type(data['observation']['schema_version']) is int,
                'Unknown observation schema/version')
        require(data['view'] == _VIEW, 'Unsupported recorded view policy')
        WindowSpecV1.from_json(data['window'])
        splits = SplitManifestV1(data['split_manifest']).to_json()
        require(type(data['episodes']) is list and bool(data['episodes']), 'Missing episode inventory')
        sources, paths = set(), set()
        for entry in data['episodes']:
            keys(entry, ('source_id', 'path'))
            require(type(entry['source_id']) is str, 'Expected source ID')
            safe_relative(entry['path'])
            require(entry['source_id'] not in sources and entry['path'] not in paths,
                    'Duplicate source or episode path')
            sources.add(entry['source_id'])
            paths.add(entry['path'])
        require(sources == {entry['source']['source_id'] for entry in splits['sources']},
                'Episode inventory differs from frozen split sources')
        self._data = data

    @classmethod
    def from_sources(cls, episode_paths, *, window, split_manifest):
        """Declare V1's home/absolute recorded view, with explicit policy/splits."""
        require(type(episode_paths) is dict, 'Expected source-ID to relative-path mapping')
        require(type(window) is WindowSpecV1, 'Expected WindowSpecV1')
        return cls({'schema_version': 1, 'observation': _OBSERVATION, 'view': _VIEW,
                    'window': window.to_json(),
                    'split_manifest': validate_split_manifest(split_manifest).to_json(),
                    'episodes': [{'source_id': source, 'path': path}
                                 for source, path in sorted(episode_paths.items())]})

    def to_json(self):
        return deepcopy(self._data)


class DatasetReader:
    """Open a local manifest with an explicit frozen split, never infer defaults.

    Opening binds every source manifest to its frozen origin. Iteration streams
    only the requested split's transitions and authorized observation channels.
    Exhaust ``validate()`` before consuming an unverified source; partial
    iteration validates only the consumed rows. Close partially used generators.
    """

    def __init__(self, manifest_path, *, split):
        path = Path(manifest_path)
        require(path.is_file() and not path.is_symlink(), 'Expected regular dataset manifest')
        with path.open('rb') as stream:
            payload = stream.read(MAX_RECORD_BYTES + 1)
        self._manifest = DatasetManifestV1(decode_json(payload))
        data = self._manifest.to_json()
        self._root = path.resolve().parent
        self._spec = WindowSpecV1.from_json(data['window'])
        self._splits = SplitManifestV1(data['split_manifest'])
        frozen = self._splits.to_json()
        require(type(split) is str and split in frozen['proportions'], 'Unknown requested split')
        self._split = split
        self._entries = data['episodes']
        self._membership = {entry['source']['source_id']: frozen['assignments'][entry['family_id']]
                            for entry in frozen['sources']}
        sources = []
        for entry in self._entries:
            reader = EpisodeReader(self._root, entry['path'])
            manifest = reader.manifest
            require(manifest['schemas']['ObservationV1'] == data['observation']['schema_version'],
                    'Incompatible observation version')
            recorded = InputProfile.from_json(manifest['profile'])
            require(set(self._spec.profile.fields) <= set(recorded.fields),
                    'Profile exceeds recorded input authority')
            sources.append(origin_from_episode(manifest, source_id=entry['source_id']))
        validate_split_manifest(self._splits, sources)

    @property
    def manifest(self):
        """Detached audit/policy data; never an encoder feature collection."""
        return self._manifest.to_json()

    def iter_windows(self):
        for entry in self._entries:
            if self._membership[entry['source_id']] != self._split:
                continue
            reader = EpisodeReader(self._root, entry['path'])
            stream = iter_windows(reader, self._spec, split_manifest=self._splits,
                                  source_id=entry['source_id'])
            try:
                yield from stream
            finally:
                stream.close()

    def iter_batches(self, batch_size=32):
        """Bounded batches; no padding, vocabulary fitting or normalization."""
        integer(batch_size, 1)
        stream = self.iter_windows()
        try:
            batch = []
            for sample in stream:
                batch.append(sample)
                if len(batch) == batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch
        finally:
            stream.close()

    def validate(self):
        """Exhaust selected channels, causal joins and membership; count windows.

        Full producer auditing (including oracle/privileged channels) belongs to
        the separate ``generate.validate_dataset`` or ``EpisodeReader`` APIs.
        """
        return sum(1 for _ in self.iter_windows())
