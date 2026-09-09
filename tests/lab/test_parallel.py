"""Spawn, ordering, bounded dispatch and atomic per-episode failure contracts."""
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import random
import subprocess
import sys
import threading
import time

import pytest

from botbowl.lab import parallel
from botbowl.lab.generate import JobConfig, build_plan, execute_plan as sequential
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.storage import DatasetReader, DatasetWriter, StorageLimits


def options(workers=2, window=4, batch=2, **kwargs):
    return parallel.PoolConfig(workers=workers, worker_limit=workers, max_in_flight=window,
        batch_size=batch, memory_budget_bytes=(workers * 2048 + 512) * parallel.MiB,
        spool_budget_bytes=window * parallel.SPOOL_EPISODE_BYTES, **kwargs)


def fault_worker(job, mailbox, spool, memory_bytes):
    """Trusted test injection; no callable or fault instructions enter a data plan."""
    original = parallel._run
    if job['master_seed'] == 106:
        from botbowl.lab import generate as generator
        open_session = generator._open_session
        previous = []

        def isolated(entry, config):
            session = open_session(entry, config)
            if previous:
                old = previous[-1]
                assert old.closed
                assert old._game is not session._game
                assert old._game.config is not session._game.config
                assert old._game.rng is not session._game.rng
                assert old._game.state.home_team is not session._game.state.home_team
                old._game.state.home_team.name = 'foreign canary'
                assert session._game.state.home_team.name != 'foreign canary'
            previous[:] = [session]
            return session

        generator._open_session = isolated

    def run(entry, config, destination):
        index = int(entry['episode_id'].rsplit('-', 1)[1])
        first_attempt = Path(destination).name.endswith('-01')
        if config.master_seed == 101 and index == 0 and first_attempt:
            os._exit(23)
        if config.master_seed == 102 and index == 0:
            try:
                raise ValueError('injected policy cause')
            except ValueError as cause:
                raise RuntimeError('injected policy failure') from cause
        if config.master_seed == 103 and index == 0:
            time.sleep(30)
        if config.master_seed == 104 and index == 0:
            # Wait until other workers fill the entire admission window.
            marker = Path(spool).parent / 'release-straggler'
            while not marker.exists():
                time.sleep(0.01)
        if config.master_seed == 105:
            # Deterministic pseudo-random delay, unrelated to simulation RNG.
            time.sleep(random.Random(index + 4).random() * 0.15)
        return original(entry, config, destination)

    parallel._run = run
    parallel._worker(job, mailbox, spool, memory_bytes)


def read_stored(root):
    dataset = DatasetReader(root)
    return [{'manifest': dataset.episode(i).manifest,
             'channels': {name: list(dataset.episode(i).iter_channel(name))
                          for name in dataset.episode(i).manifest['files']}}
            for i in range(len(dataset))]


def audits(root):
    return [json.loads(line) for path in Path(root).glob('pool-audit-*.jsonl')
            for line in path.read_text().splitlines()]


def assert_clean(root, before):
    assert {p.pid for p in multiprocessing.active_children()} == before
    assert not list(Path(root).glob('.pool-*'))
    assert not list(Path(root).rglob('*.partial'))


@pytest.mark.parametrize('workers,batch', [(1, 1), (2, 1), (4, 3)])
def test_exact_records_across_scheduling_and_batches(tmp_path, monkeypatch, workers, batch):
    monkeypatch.setattr(parallel, '_worker', fault_worker)
    plan = build_plan(JobConfig('unused', episodes=6, master_seed=105,
                                scenario='pickup', home_policy='random', max_decisions=4))
    reference = sequential(plan, tmp_path / 'sequential')
    before = {p.pid for p in multiprocessing.active_children()}
    report = parallel.execute_plan(plan, tmp_path / 'pool', pool=options(workers, 4, batch),
                                  storage_limits=StorageLimits(shard_rows=batch))
    assert all(r['status'] == 'committed' and r['error'] is None for r in report['episodes'])
    assert [r['semantic_sha256'] for r in report['episodes']] == [
        r['semantic_sha256'] for r in reference['episodes']]
    expected = [EpisodeReader(tmp_path / 'sequential', e['episode_id']).read_episode()
                for e in plan['episodes']]
    assert read_stored(tmp_path / 'pool') == expected
    pids = {a['pid'] for a in audits(tmp_path / 'pool') if 'pid' in a}
    assert os.getpid() not in pids
    assert len(pids) == workers
    sources = [e['seed_plan']['sources'] for e in plan['episodes']]
    assert len({json.dumps(s, sort_keys=True) for s in sources}) == len(sources)
    assert report['stats']['peak_in_flight'] <= 4
    assert_clean(tmp_path / 'pool', before)


def test_crash_retry_keeps_identity_and_resume_does_not_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, '_worker', fault_worker)
    root = tmp_path / 'pool'
    plan = build_plan(JobConfig('unused', episodes=3, master_seed=101, max_decisions=1))
    report = parallel.execute_plan(plan, root, pool=options(retries=1))
    assert all(r['status'] == 'committed' for r in report['episodes'])
    assert report['episodes'][0]['attempts'] == 2
    attempts = [a for a in audits(root) if a.get('episode_id') == 'episode-000000']
    assert attempts[0]['error'] == [{'type': 'WorkerExit', 'message': 'exitcode=23'}]
    assert attempts[1]['error'] is None
    assert attempts[0]['plan_sha256'] == attempts[1]['plan_sha256']
    reference = sequential(plan, tmp_path / 'reference')
    assert report['episodes'][0]['semantic_sha256'] == reference['episodes'][0]['semantic_sha256']
    contents = read_stored(root)
    monkeypatch.setattr(parallel, '_worker', lambda *a: pytest.fail('Resume spawned a worker'))
    resumed = parallel.execute_plan(plan, root, pool=options(retries=1))
    assert resumed['stats']['dispatched'] == 0
    assert read_stored(root) == contents
    assert_clean(root, set())


def test_error_is_per_episode_and_permanent_gap_can_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, '_worker', fault_worker)
    root = tmp_path / 'pool'
    plan = build_plan(JobConfig('unused', episodes=8, master_seed=102, max_decisions=1))
    report = parallel.execute_plan(plan, root, pool=options(retries=1))
    assert report['episodes'][0]['status'] == 'failed'
    assert [c['type'] for c in report['episodes'][0]['error']] == ['RuntimeError', 'ValueError']
    assert report['episodes'][0]['attempts'] == 2
    assert len(DatasetReader(root)) == 0
    assert any(r['status'] == 'uncommitted' for r in report['episodes'][1:])
    assert any(r['status'] == 'not_dispatched' for r in report['episodes'][1:])
    assert_clean(root, set())
    monkeypatch.undo()
    resumed = parallel.execute_plan(plan, root, pool=options())
    assert all(r['status'] == 'committed' for r in resumed['episodes'])
    assert len(DatasetReader(root)) == 8


def test_timeout_is_bounded_and_does_not_touch_foreign_process(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, '_worker', fault_worker)
    foreign = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        start = time.monotonic()
        report = parallel.generate(JobConfig(str(tmp_path / 'pool'), master_seed=103),
                                   pool=options(1, 1, 1, episode_timeout=2, join_timeout=0.2))
        assert time.monotonic() - start < 10
        assert report['episodes'][0]['error'][0]['type'] == 'EpisodeTimeout'
        assert foreign.poll() is None
        assert_clean(tmp_path / 'pool', set())
    finally:
        foreign.terminate()
        foreign.wait(timeout=5)


def test_straggler_applies_backpressure_and_cancel_collects_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, '_worker', fault_worker)
    root = tmp_path / 'pool'
    observations = []
    cancellation = threading.Event()

    def monitor():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            results = list(root.glob('.pool-*/task-*/result.json'))
            if len(results) == 3:
                time.sleep(0.2)  # give fast workers time to try dispatching again
                tasks = list(root.glob('.pool-*/task-*'))
                observations.append(len(tasks))
                (root / 'release-straggler').touch()
                while time.monotonic() < deadline:
                    try:
                        if len(DatasetReader(root)) >= 1:
                            cancellation.set()
                            return
                    except (ValueError, FileNotFoundError):
                        pass
                    time.sleep(0.01)
            time.sleep(0.01)
        cancellation.set()

    monitor_thread = threading.Thread(target=monitor)
    monitor_thread.start()
    report = parallel.generate(JobConfig(str(root), episodes=20, master_seed=104, max_decisions=1),
                               pool=options(2, 4, 4), cancel=cancellation)
    monitor_thread.join(timeout=20)
    assert not monitor_thread.is_alive()
    assert observations == [4]
    assert report['stats']['peak_in_flight'] == 4
    assert report['stats']['peak_buffered'] >= 3
    assert report['cancelled']
    assert 1 <= len(DatasetReader(root)) < 20
    assert all(r['status'] == 'committed' for r in report['episodes'][:len(DatasetReader(root))])
    assert_clean(root, set())


def test_precancel_and_invalid_parameters_have_no_workers(tmp_path):
    cancellation = threading.Event()
    cancellation.set()
    report = parallel.generate(JobConfig(str(tmp_path / 'pool')), cancel=cancellation)
    assert report['cancelled'] and report['stats']['dispatched'] == 0
    assert len(DatasetReader(tmp_path / 'pool')) == 0
    assert_clean(tmp_path / 'pool', set())
    for change in ({'workers': 0}, {'workers': 2}, {'max_in_flight': 2},
                   {'batch_size': 2}, {'retries': -1}, {'join_timeout': float('nan')},
                   {'episode_timeout': False}, {'memory_budget_bytes': 1}):
        with pytest.raises(ValueError):
            replace(parallel.PoolConfig(), **change)
    plan = build_plan(JobConfig('unused'))
    plan['episodes'][0]['policies']['home']['id'] = 'os.system'
    with pytest.raises(ValueError):
        parallel.execute_plan(plan, tmp_path / 'invalid')
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('after_commit', [False, True])
def test_writer_failure_observes_actual_commit_and_releases_lock(tmp_path, monkeypatch, after_commit):
    original = DatasetWriter.append_episode

    def fail(self, reader):
        if after_commit:
            original(self, reader)
        raise OSError('injected storage failure')

    monkeypatch.setattr(DatasetWriter, 'append_episode', fail)
    root = tmp_path / 'pool'
    plan = build_plan(JobConfig('unused', episodes=2, max_decisions=1))
    report = parallel.execute_plan(plan, root, pool=options())
    assert report['writer_error'][0]['type'] == 'OSError'
    assert len(DatasetReader(root)) == int(after_commit)
    assert report['episodes'][0]['status'] == ('committed' if after_commit else 'failed')
    assert_clean(root, set())
    monkeypatch.undo()
    with DatasetWriter(root, plan) as writer:
        assert writer.committed_episodes == int(after_commit)
    report = parallel.execute_plan(plan, root, pool=options())
    assert len(DatasetReader(root)) == 2
    assert report['episodes'][0]['attempts'] == int(not after_commit)


def test_cli_smoke(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'botbowl.lab.parallel', '--output',
                             str(tmp_path / 'pool'), '--decisions', '1'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['episodes'][0]['status'] == 'committed'
    DatasetReader(tmp_path / 'pool').verify()


@pytest.mark.parametrize('stage', ['before_shard_rename', 'after_shard_rename',
                                   'before_manifest_replace', 'after_manifest_replace'])
def test_storage_fault_cleanup_preserves_commits_and_foreign_files(tmp_path, monkeypatch, stage):
    root = tmp_path / 'pool'
    root.mkdir()
    foreign = root / ('data-' + 'f' * 32)
    foreign.mkdir()
    (foreign / 'foreign.partial').write_text('untouched')

    def fault(at):
        if at == stage:
            raise OSError('injected ' + stage)

    monkeypatch.setattr(parallel, 'DatasetWriter', lambda *a, **k: DatasetWriter(*a, fault=fault, **k))
    report = parallel.generate(JobConfig(str(root), max_decisions=1))
    count = int(stage == 'after_manifest_replace')
    assert len(DatasetReader(root)) == count
    assert report['writer_error']
    assert (foreign / 'foreign.partial').read_text() == 'untouched'
    assert len(list(root.glob('data-*'))) == 1 + count
    assert not list(root.glob('storage-*.partial'))
    assert not list(root.glob('.pool-*'))
    assert not multiprocessing.active_children()


def test_reused_worker_owns_fresh_objects_and_empty_episodes(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, '_worker', fault_worker)
    plan = build_plan(JobConfig('unused', episodes=3, master_seed=106,
                                scenario='match', horizon=0))
    report = parallel.execute_plan(plan, tmp_path / 'pool')
    assert all(e['status'] == 'committed' for e in report['episodes'])
    assert len({a['pid'] for a in audits(tmp_path / 'pool') if 'pid' in a}) == 1
    assert all(not e['channels']['transitions'] for e in read_stored(tmp_path / 'pool'))
    assert_clean(tmp_path / 'pool', set())


def test_error_diagnostics_are_bounded_even_with_escaped_unicode():
    error = ValueError('\x00😀' * 1000)
    for _ in range(3):
        outer = RuntimeError('\x00😀' * 1000)
        outer.__cause__ = error
        error = outer
    causes = parallel._cause(error)
    assert len(causes) == 4
    assert len(parallel.encode_json(causes)) <= 2048
