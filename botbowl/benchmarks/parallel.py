"""OPS-02 end-to-end pool comparison, using PERF-01 hardware/dispersion capture.

python -m botbowl.benchmarks.parallel --output /tmp/parallel-report.json
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

from .headless import environment, summary
from .storage import channel_rows, consume
from botbowl.lab.generate import JobConfig, build_plan, _run
from botbowl.lab.parallel import PoolConfig, SPOOL_EPISODE_BYTES, MiB, execute_plan
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.storage import DatasetReader, DatasetWriter


def sample(root, episodes, decisions, seed, workers):
    plan = build_plan(JobConfig('unused', episodes=episodes, max_decisions=decisions,
                                master_seed=seed, scenario='match', size=3))
    destination = root / 'data'
    started = time.perf_counter()
    if workers == 0:
        # Same generator, same atomic writer, no process/coordination overhead.
        with DatasetWriter(destination, plan) as writer:
            for entry in plan['episodes']:
                with tempfile.TemporaryDirectory(dir=root) as temporary:
                    _run(entry, JobConfig(output='unused', **plan['job']), temporary)
                    writer.append_episode(EpisodeReader(temporary, entry['episode_id']))
        counters = None
    else:
        window = 2 * workers
        config = PoolConfig(workers=workers, worker_limit=workers, max_in_flight=window,
            batch_size=workers, memory_budget_bytes=(workers * 2048 + 512) * MiB,
            spool_budget_bytes=window * SPOOL_EPISODE_BYTES)
        result = execute_plan(plan, destination, pool=config)
        if any(e['status'] != 'committed' for e in result['episodes']):
            raise RuntimeError('Benchmark generation did not finish')
        counters = result['stats']
    seconds = time.perf_counter() - started
    dataset = DatasetReader(destination)
    semantic = consume(channel_rows(dataset.episode(i) for i in range(len(dataset))))
    parent_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    worker_rss = 0 if counters is None else counters['worker_peak_rss_bytes']
    return {'seconds': seconds, 'semantic': semantic, 'stats': counters,
            'parent_peak_rss_bytes': parent_rss, 'worker_peak_rss_bytes': worker_rss,
            # Sum of maxima is conservative, not a measured simultaneous RSS peak.
            'rss_peak_upper_estimate_bytes': parent_rss + workers * worker_rss}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--episodes', type=int, default=8)
    parser.add_argument('--decisions', type=int, default=8)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--sample-workers', type=int, choices=(0, 1, 2, 4))
    args = parser.parse_args()
    if args.sample_workers is not None:
        with tempfile.TemporaryDirectory(prefix='botbowl-parallel-sample-') as temporary:
            result = sample(Path(temporary), args.episodes, args.decisions, args.seed, args.sample_workers)
        args.output.write_text(json.dumps(result, sort_keys=True) + '\n')
        return
    if args.repetitions < 2:
        parser.error('At least two repetitions are required for dispersion')
    import botbowl.lab.parallel as implementation
    captured_environment = environment()
    captured_environment.update(memory='Linux ru_maxrss; parent and worker maxima, including validation',
                                gc='enabled; pool collects after each episode',
                                write='DATA-07 verified JSONL shards and atomic manifest; fsync enabled')
    report = {'schema_version': 1, 'environment': captured_environment,
              'implementation_sha256': hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest(),
              'fixture': {'episodes': args.episodes, 'decisions': args.decisions,
                          'seed': args.seed, 'scenario': 'match', 'size': 3},
              'thread_limits': {key: os.environ.get(key) for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')},
              'method': 'Fresh subprocess per repetition; sequential and pool use the same generator '
                        'and per-episode JSONL atomic writer. Timing includes worker startup, generation, '
                        'spooling, writer verification/fsync and shutdown; final comparison excluded. '
                        'Linux ru_maxrss includes imports/native allocations. Pool RSS estimate adds '
                        'parent maximum to workers times maximum observed worker RSS, not a simultaneous '
                        'measurement. Warm OS caches, shared host, no timing CI threshold.',
              'results': {}}
    expected = None
    with tempfile.TemporaryDirectory(prefix='botbowl-parallel-benchmark-') as temporary:
        for workers in (0, 1, 2, 4):
            samples = []
            for repeat in range(args.repetitions):
                output = Path(temporary) / 'sample.json'
                subprocess.run([sys.executable, '-m', __spec__.name,
                    '--sample-workers', str(workers), '--output', str(output),
                    '--episodes', str(args.episodes), '--decisions', str(args.decisions),
                    '--seed', str(args.seed)], check=True, timeout=3600)
                value = json.loads(output.read_text())
                expected = value['semantic'] if expected is None else expected
                if value['semantic'] != expected:
                    raise RuntimeError('Scheduling changed semantic content')
                samples.append(value)
                print('workers=%d repetition=%d/%d complete' % (workers, repeat + 1, args.repetitions),
                      file=sys.stderr, flush=True)
            report['results'][str(workers)] = {'samples': samples, **{
                name: summary([s[name] for s in samples]) for name in
                ('seconds', 'parent_peak_rss_bytes', 'worker_peak_rss_bytes', 'rss_peak_upper_estimate_bytes')}}
    report['semantic_reference'] = expected
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
