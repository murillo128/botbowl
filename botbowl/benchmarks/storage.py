"""DATA-07 codec comparison using DATA-01 fixtures and PERF-01 reporting.

python -m botbowl.benchmarks.storage --output /tmp/storage-report.json
Each sample runs in a fresh process. Generated data stays outside Git.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import tracemalloc

from .headless import environment, summary
from botbowl.lab.generate import JobConfig, build_plan, execute_plan, _read_job_json
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.records import encode_json
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.storage import DatasetReader, DatasetWriter, StorageLimits, StorageStats
from botbowl.lab.windows import WindowSpecV1, iter_windows


def consume(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(encode_json(row) + b'\n')
        count += 1
    return {'sha256': digest.hexdigest(), 'rows': count}


def channel_rows(readers):
    for reader in readers:
        for name in sorted(reader.manifest['files']):
            yield from reader.iter_channel(name)


def sample(source, destination, backend, memory):
    plan = _read_job_json(source / 'plan.json')
    sources = [EpisodeReader(source, entry['episode_id']) for entry in plan['episodes']]
    split = json.loads((source / 'benchmark-splits.json').read_text())
    limits = StorageLimits(shard_rows=32, shard_bytes=256 * 1024,
                           buffer_bytes=256 * 1024, file_bytes=512 * 1024,
                           decoded_bytes=512 * 1024)
    stats = StorageStats()
    results = {'backend': backend, 'limits': asdict(limits), 'operations': {}}

    def measure(name, function):
        if memory:
            tracemalloc.start()
        start = time.perf_counter()
        value = function()
        elapsed = time.perf_counter() - start
        metric = {'seconds': elapsed, 'result': value}
        if memory:
            metric['python_peak_bytes'] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        results['operations'][name] = metric

    def write():
        with DatasetWriter(destination, plan, backend=backend, limits=limits,
                           split_manifest=split, stats=stats) as writer:
            # Bound input batches explicitly even for >32 episodes.
            for offset in range(0, len(sources), limits.batch_episodes):
                writer.append_batch(sources[offset:offset + limits.batch_episodes])
        return len(sources)

    measure('write', write)
    reader = DatasetReader(destination, stats=stats)
    measure('sequential', lambda: consume(channel_rows(reader.episode(i) for i in range(len(reader)))))
    measure('projection', lambda: consume(row for batch in reader.iter_batches(
        'transitions', fields=['transition_id', 'action']) for row in batch))
    measure('windows', lambda: consume(reader.iter_windows(WindowSpecV1(3, 2))))
    results['bytes'] = sum(path.stat().st_size for path in destination.rglob('*') if path.is_file())
    results['stats'] = asdict(stats)
    results['process_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == 'darwin' else 1024)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--episodes', type=int, default=8)
    parser.add_argument('--decisions', type=int, default=16)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--worker', choices=['jsonl', 'parquet'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--memory', action='store_true')
    args = parser.parse_args()
    if args.worker:
        with tempfile.TemporaryDirectory(prefix='botbowl-storage-worker-') as temp:
            value = sample(args.source, Path(temp), args.worker, args.memory)
        args.output.write_text(json.dumps(value, sort_keys=True) + '\n')
        return
    if args.repetitions < 2:
        parser.error('At least two repetitions are required for dispersion')
    versions = {'pyarrow': importlib.metadata.version('pyarrow')}
    import botbowl.lab.storage as storage_module
    report = {'schema_version': 1, 'environment': environment(), 'codec_versions': versions,
              'implementation_sha256': hashlib.sha256(Path(storage_module.__file__).read_bytes()).hexdigest(),
              'fixture': {'episodes': args.episodes, 'decisions': args.decisions, 'seed': args.seed,
                          'scenario': 'match', 'size': 3},
              'method': 'Fresh subprocess per codec/repetition; warm OS cache; durable writes, '
                        'validation and semantic hashing included. Timing excludes tracemalloc. '
                        'Separate memory samples include tracemalloc; RSS includes imports/native allocator.',
              'results': {}}
    with tempfile.TemporaryDirectory(prefix='botbowl-storage-benchmark-') as temp:
        root = Path(temp)
        source = root / 'source'
        plan = build_plan(JobConfig(output=str(source), episodes=args.episodes,
                                   max_decisions=args.decisions, master_seed=args.seed,
                                   scenario='match', size=3))
        execute_plan(plan, source)
        sources = [EpisodeReader(source, entry['episode_id']) for entry in plan['episodes']]
        split = build_split_manifest([origin_from_episode(r.manifest) for r in sources],
                                     proportions={'train': 0.8, 'test': 0.2}, seed=args.seed,
                                     split_version='storage-benchmark-v1').to_json()
        (source / 'benchmark-splits.json').write_bytes(encode_json(split))
        expected = {'sequential': consume(channel_rows(sources)),
                    'projection': consume({field: row[field] for field in ('transition_id', 'action')}
                                          for r in sources for row in r.iter_channel('transitions')),
                    'windows': consume(w for r in sources for w in iter_windows(
                        r, WindowSpecV1(3, 2), split_manifest=split))}
        report['semantic_reference'] = expected
        for backend in ('jsonl', 'parquet'):
            samples, memories = [], []
            for memory, collection in ((False, samples), (True, memories)):
                for repeat in range(args.repetitions):
                    output = root / 'sample.json'
                    command = [sys.executable, '-m', 'botbowl.benchmarks.storage', '--worker', backend,
                               '--source', str(source), '--output', str(output)]
                    if memory:
                        command.append('--memory')
                    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
                    value = json.loads(output.read_text())
                    for name, reference in expected.items():
                        if value['operations'][name]['result'] != reference:
                            raise RuntimeError('Storage semantic comparison failed: ' + name)
                    collection.append(value)
                    print('%s %s %d/%d complete' % (backend, 'memory' if memory else 'timing',
                                                    repeat + 1, args.repetitions), file=sys.stderr, flush=True)
            report['results'][backend] = {
                'samples': samples, 'memory_samples': memories,
                'seconds': {name: summary([s['operations'][name]['seconds'] for s in samples])
                            for name in samples[0]['operations']},
                'python_peak_bytes': {name: summary([s['operations'][name]['python_peak_bytes'] for s in memories])
                                      for name in memories[0]['operations']},
                'process_peak_rss_bytes': summary([s['process_peak_rss_bytes'] for s in memories]),
                'bytes': summary([s['bytes'] for s in samples])}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
