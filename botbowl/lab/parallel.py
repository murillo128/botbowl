"""Bounded spawn coordinator over DATA-01 and the single DATA-07 writer.

Only validated declarative plans cross the worker boundary. See docs/lab/parallel.md.
"""
import argparse
from dataclasses import asdict, dataclass
import gc
import json
import math
import multiprocessing
import os
from pathlib import Path
import resource
import re
import shutil
import tempfile
import sys
import time

from .generate import (JobConfig, build_plan, _validate_plan, _run, _atomic_json,
                       _read_job_json, _job_hash, _integer)
from .recording import EpisodeReader, _read_file
from .records import MAX_EPISODE_BYTES, MAX_RECORD_BYTES, decode_json, encode_json
from .storage import DatasetReader, DatasetWriter, StorageLimits

MiB = 1024 * 1024
# DATA-02 bounds all channel bytes together, plus its bounded manifest.
SPOOL_EPISODE_BYTES = MAX_EPISODE_BYTES + 3 * MAX_RECORD_BYTES


@dataclass(frozen=True)
class PoolConfig:
    workers: int = 1
    worker_limit: int = 1
    max_in_flight: int = 1
    batch_size: int = 1
    memory_budget_bytes: int = 4096 * MiB
    worker_memory_bytes: int = 2048 * MiB
    coordinator_memory_bytes: int = 512 * MiB
    spool_budget_bytes: int = SPOOL_EPISODE_BYTES
    episode_timeout: float = 120.0
    join_timeout: float = 2.0
    retries: int = 0

    def __post_init__(self):
        for name in ('workers', 'worker_limit', 'max_in_flight', 'batch_size'):
            _integer(getattr(self, name), 1, 1000, name)
        _integer(self.retries, 0, 10, 'retries')
        for name in ('memory_budget_bytes', 'worker_memory_bytes',
                     'coordinator_memory_bytes', 'spool_budget_bytes'):
            _integer(getattr(self, name), 1, 2**50, name)
        if self.workers > self.worker_limit or self.workers > self.max_in_flight:
            raise ValueError('workers must fit worker_limit and max_in_flight')
        if self.batch_size > self.max_in_flight:
            raise ValueError('batch_size must fit max_in_flight')
        if self.workers * self.worker_memory_bytes + self.coordinator_memory_bytes > self.memory_budget_bytes:
            raise ValueError('Worker and coordinator reservations exceed memory budget')
        if self.max_in_flight * SPOOL_EPISODE_BYTES > self.spool_budget_bytes:
            raise ValueError('Dispatch window exceeds spool budget')
        for name in ('episode_timeout', 'join_timeout'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' must be positive and finite')


def _cause(error):
    """Bounded operational cause chain, kept outside semantic channels."""
    causes, seen = [], set()
    while error is not None and id(error) not in seen and len(causes) < 4:
        seen.add(id(error))
        causes.append({'type': type(error).__name__, 'message': str(error)[:512]})
        error = error.__cause__ or error.__context__
    # Keep the complete 1,000-episode operational report within a record bound,
    # including escaped/non-ASCII exception messages.
    while len(encode_json(causes)) > 2048:
        for cause in causes:
            cause['type'] = cause['type'][:64]
            cause['message'] = cause['message'][:len(cause['message']) // 2]
    return causes


def _worker(job, mailbox, spool, memory_bytes):
    """Trusted fixed factory; files contain JSON data, never executable targets."""
    # DATA-07 already requires POSIX. Limit virtual memory during execution,
    # preserving any stricter inherited hard limit. Imports precede this limit.
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = min([memory_bytes] + [limit for limit in (soft, hard) if limit != resource.RLIM_INFINITY])
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    config = JobConfig(output='unused', **job)
    inbox = Path(mailbox) / 'request.json'
    while True:
        if not inbox.exists():
            time.sleep(0.01)
            continue
        request = decode_json(_read_file(inbox, MAX_RECORD_BYTES))
        inbox.unlink()
        if request is None:
            return
        directory = Path(spool) / request['directory']
        try:
            summary = _run(request['entry'], config, directory)
            result = {'summary': {'semantic_sha256': summary['semantic_sha256']}, 'error': None}
        except Exception as error:
            result = {'summary': None, 'error': _cause(error)}
        result['pid'] = os.getpid()
        result['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
        _atomic_json(directory / 'result.json', result)
        gc.collect()


def _stop(processes, timeout):
    """Terminate only this job's workers, with a shared deadline at each stage."""
    for process in processes:
        if process.is_alive():
            process.terminate()
    deadline = time.monotonic() + timeout
    for process in processes:
        process.join(max(0, deadline - time.monotonic()))
    for process in processes:
        if process.is_alive():
            process.kill()
    deadline = time.monotonic() + timeout
    for process in processes:
        process.join(max(0, deadline - time.monotonic()))
        if process.is_alive():
            raise RuntimeError('Worker did not exit after bounded terminate/kill')
        process.close()


def execute_plan(plan, output, *, pool=None, storage_limits=None, cancel=None):
    """Run/resume a DATA-01 plan; return an ID/status/error for every request.

    ``cancel`` is an optional parent-side Event-like object with ``is_set``.
    Completed canonical episodes are committed individually, even in batches.
    A permanent gap stops dispatch; later successes are explicitly uncommitted.
    """
    config = _validate_plan(plan)
    # Rebuild detached validated values before any dispatch or output creation.
    plan = build_plan(config, _legacy=plan['schema_version'] == 1)
    pool = pool or PoolConfig()
    if type(pool) is not PoolConfig:
        raise ValueError('Expected PoolConfig')
    pool.__post_init__()
    limits = storage_limits or StorageLimits()
    limits.__post_init__()
    root = Path(output).resolve()
    package = Path(__file__).resolve().parents[1]
    if root == package or package in root.parents:
        raise ValueError('Datasets cannot be written inside the installed package')
    entries = plan['episodes']
    plan_sha256 = _job_hash(plan)
    if [e['episode_id'] for e in entries] != sorted(e['episode_id'] for e in entries):
        raise ValueError('Plan must be canonical by episode_id')
    outcomes = [{'episode_id': entry['episode_id'], 'status': 'not_dispatched',
                 'attempts': 0, 'error': None} for entry in entries]
    stats = {'peak_in_flight': 0, 'peak_buffered': 0, 'dispatched': 0,
             'worker_peak_rss_bytes': 0}
    processes, slots, active, ready = [], [], {}, {}
    stopped, cancelled, writer_error = False, False, None
    context = multiprocessing.get_context('spawn')
    with DatasetWriter(root, plan, limits=limits) as writer:
        first = writer.committed_episodes
        for outcome in outcomes[:first]:
            outcome['status'] = 'committed'
        next_index = first
        # Own exactly this directory and audit file, not global temp/process state.
        with tempfile.TemporaryDirectory(prefix='.pool-', dir=str(root)) as temporary:
            spool = Path(temporary)
            audit_path = root / ('pool-audit-' + spool.name[6:] + '.jsonl')
            with audit_path.open('x', encoding='utf-8') as audit:
                def log(value):
                    audit.write(encode_json(value).decode('utf-8') + '\n')
                    audit.flush()
                    os.fsync(audit.fileno())

                def launch(slot):
                    process = context.Process(target=_worker, args=(plan['job'], str(slot['mailbox']),
                                                                     str(spool), pool.worker_memory_bytes))
                    process.start()
                    processes.append(process)
                    slot['process'] = process

                def dispatch(index, slot):
                    outcome = outcomes[index]
                    outcome['attempts'] += 1
                    directory = spool / ('task-%06d-%02d' % (index, outcome['attempts']))
                    directory.mkdir()
                    _atomic_json(slot['mailbox'] / 'request.json',
                                 {'entry': entries[index], 'directory': directory.name})
                    active[index] = {'slot': slot, 'directory': directory,
                                     'started': time.monotonic()}
                    outcome['status'] = 'running'
                    stats['dispatched'] += 1
                    stats['peak_in_flight'] = max(stats['peak_in_flight'], len(active) + len(ready))

                try:
                    if first < len(entries) and not (cancel is not None and cancel.is_set()):
                        for number in range(pool.workers):
                            mailbox = spool / ('worker-%04d' % number)
                            mailbox.mkdir()
                            slot = {'mailbox': mailbox, 'process': None}
                            slots.append(slot)
                            launch(slot)
                    while next_index < len(entries) or active or ready:
                        if cancel is not None and cancel.is_set():
                            stopped, cancelled = True, True
                        # Harvest all available results before applying cancellation.
                        for index, task in list(active.items()):
                            slot, directory = task['slot'], task['directory']
                            result_path = directory / 'result.json'
                            result = None
                            if result_path.exists():
                                result = decode_json(_read_file(result_path, MAX_RECORD_BYTES))
                            elif not slot['process'].is_alive():
                                result = {'summary': None, 'error': [{'type': 'WorkerExit',
                                    'message': 'exitcode=%s' % slot['process'].exitcode}]}
                            elif time.monotonic() - task['started'] >= pool.episode_timeout:
                                result = {'summary': None, 'error': [{'type': 'EpisodeTimeout',
                                    'message': 'Episode exceeded %.3f seconds' % pool.episode_timeout}]}
                            if result is None:
                                continue
                            del active[index]
                            outcome = outcomes[index]
                            stats['worker_peak_rss_bytes'] = max(stats['worker_peak_rss_bytes'],
                                                                 result.get('peak_rss_bytes', 0))
                            log({'episode_id': outcome['episode_id'], 'attempt': outcome['attempts'],
                                 'plan_sha256': plan_sha256, **result})
                            if result['error'] is not None:
                                outcome.update(status='failed', error=result['error'])
                                # A timed-out process must be dead before deleting its files/retrying.
                                process = slot['process']
                                _stop([process], pool.join_timeout)
                                processes.remove(process)
                                slot['process'] = None
                                for path in slot['mailbox'].iterdir():
                                    path.unlink()
                                shutil.rmtree(directory)
                                if (not stopped and outcome['attempts'] <= pool.retries
                                        and not (cancel is not None and cancel.is_set())):
                                    launch(slot)
                                    dispatch(index, slot)  # exact same entry and purpose seeds
                                else:
                                    stopped = True
                            else:
                                outcome.update(status='uncommitted', error=None,
                                               semantic_sha256=result['summary']['semantic_sha256'])
                                ready[index] = directory
                        stats['peak_buffered'] = max(stats['peak_buffered'], len(ready))
                        # Batch requests retain per-episode commit/error boundaries.
                        for _ in range(pool.max_in_flight if stopped else pool.batch_size):
                            index = writer.committed_episodes
                            if index not in ready or writer_error is not None:
                                break
                            directory = ready[index]
                            before_write = {path.name for path in root.iterdir()}
                            try:
                                writer.append_episode(EpisodeReader(directory, entries[index]['episode_id']))
                            except Exception as error:
                                writer_error = _cause(error)
                                stopped = True
                                log({'episode_id': entries[index]['episode_id'], 'writer_error': writer_error})
                                # DATA-07 publication can succeed before an exception is observed.
                                committed = len(DatasetReader(root, limits=limits))
                                # Under DATA-07's exclusive lock, these newly created
                                # reserved paths belong to this attempted publication.
                                # Never remove a pre-existing or committed fragment.
                                for path in root.iterdir():
                                    if path.name in before_write:
                                        continue
                                    if re.fullmatch(r'storage-[0-9a-f]{32}\.partial', path.name):
                                        path.unlink()
                                    elif index >= committed:
                                        if re.fullmatch(r'data-[0-9a-f]{32}', path.name):
                                            shutil.rmtree(path)
                                        elif path.name == 'episode-%06d.json' % index:
                                            path.unlink()
                                for outcome in outcomes[:committed]:
                                    outcome.update(status='committed', error=None)
                                if index >= committed:
                                    outcomes[index].update(status='failed', error=writer_error)
                                break
                            outcomes[index].update(status='committed', error=None)
                            del ready[index]
                            shutil.rmtree(directory)
                        if cancelled:
                            break
                        if not stopped:
                            dispatched = 0
                            for slot in slots:
                                if cancel is not None and cancel.is_set():
                                    stopped, cancelled = True, True
                                    break
                                if next_index == len(entries) or dispatched == pool.batch_size:
                                    break
                                if len(active) + len(ready) >= pool.max_in_flight:
                                    break
                                if any(task['slot'] is slot for task in active.values()):
                                    continue
                                # A worker may have exited while idle, after a valid result.
                                if slot['process'] is None or not slot['process'].is_alive():
                                    if slot['process'] is not None:
                                        process = slot['process']
                                        _stop([process], pool.join_timeout)
                                        processes.remove(process)
                                    launch(slot)
                                dispatch(next_index, slot)
                                next_index += 1
                                dispatched += 1
                        if stopped and not active:
                            break
                        if next_index == len(entries) and not active and not ready:
                            break
                        time.sleep(0.01)
                except KeyboardInterrupt:
                    cancelled = True
                finally:
                    _stop(processes, pool.join_timeout)
                for index in active:
                    outcomes[index].update(status='cancelled', error=[{'type': 'Cancelled',
                        'message': 'Job cancelled before a result was collected'}])
                # Summaries are operational; manifest.json alone determines committed data.
                report = {'schema_version': 1, 'plan_sha256': plan_sha256,
                          'cancelled': cancelled, 'writer_error': writer_error,
                          'episodes': outcomes, 'stats': stats, 'pool': asdict(pool)}
                log({'report': report})
                return report


def generate(config, **kwargs):
    return execute_plan(build_plan(config), config.output, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--plan', type=Path, help='Validated DATA-01 plan.json; resumes its committed prefix')
    parser.add_argument('--episodes', type=int, default=1)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--scenario', default='movement')
    parser.add_argument('--decisions', type=int, default=32)
    for name, field in PoolConfig.__dataclass_fields__.items():
        parser.add_argument('--' + name.replace('_', '-'), type=type(field.default), default=field.default)
    args = parser.parse_args()
    try:
        pool = PoolConfig(**{name: getattr(args, name) for name in PoolConfig.__dataclass_fields__})
        plan = (_read_job_json(args.plan) if args.plan else build_plan(JobConfig(
            output=args.output, episodes=args.episodes, master_seed=args.seed,
            scenario=args.scenario, max_decisions=args.decisions)))
        report = execute_plan(plan, args.output, pool=pool)
    except (ValueError, OSError) as error:
        parser.exit(2, '%s: %s\n' % (type(error).__name__, error))
    print(json.dumps(report, sort_keys=True))
    if any(episode['status'] != 'committed' for episode in report['episodes']):
        parser.exit(1)


if __name__ == '__main__':
    main()
