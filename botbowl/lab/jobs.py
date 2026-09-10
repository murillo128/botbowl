"""Sequential generation jobs with cooperative cancellation and exact-input recovery.

Operational JSON stays outside DATA-07/model channels. See docs/lab/jobs.md.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
import multiprocessing
from pathlib import Path
import signal
import tempfile
import threading
import time
import uuid

from botbowl._version import __version__
from .actions import ActionV1
from .generate import (JobConfig, build_plan, _validate_plan, _run, _job_hash,
                       _hash, _read_job_json)
from .recording import EpisodeReader, _read_file
from .records import MAX_RECORD_BYTES, decode_json, encode_json, require, keys
from .storage import DatasetReader, DatasetWriter, _atomic


class IncompatibleRecovery(ValueError):
    """Recovery inputs or an observed replay prefix differ from the checkpoint."""


@dataclass(frozen=True)
class JobLimits:
    decisions: int = None
    episodes: int = None
    seconds: float = None
    no_progress_decisions: int = 100
    worker_timeout: float = 120.0
    join_timeout: float = 2.0

    def __post_init__(self):
        for name in ('decisions', 'episodes', 'no_progress_decisions'):
            value = getattr(self, name)
            if value is None and name != 'no_progress_decisions':
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(name + ' must be a positive integer')
        for name in ('seconds', 'worker_timeout', 'join_timeout'):
            value = getattr(self, name)
            if value is None and name == 'seconds':
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' must be positive and finite')


@dataclass(frozen=True)
class Progress:
    state: str
    planned_episodes: int
    confirmed_episodes: int
    failed_episodes: int
    decisions: int
    events: int
    confirmed_bytes: int
    duration: float
    last_progress: float
    end_causes: tuple
    observer_errors: int
    reason: str = None


class JobContext:
    """One execution attempt. Call cancel() from a callback, signal or thread.

    Limits count work in this attempt (including replay); confirmed counts also
    include a resumed prefix. Callbacks and JSONL streams are caller-owned.
    """

    def __init__(self, limits=None, *, callback=None, jsonl=None, clock=time.monotonic):
        self.limits = limits or JobLimits()
        self.limits.__post_init__()
        self.callback, self.jsonl, self.clock = callback, jsonl, clock
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self.state, self.reason = 'pending', None
        self.planned = self.confirmed = self.failed = 0
        self.decisions = self.events = self.bytes = self.observer_errors = 0
        self.causes = {}
        self._started = None
        self._duration = self._last = 0.0
        self._attempt_episodes = 0

    def snapshot(self):
        with self._lock:
            if self._started is not None and self.state not in ('cancelled', 'succeeded', 'failed'):
                self._duration = max(self._duration, self.clock() - self._started)
            return Progress(self.state, self.planned, self.confirmed, self.failed,
                            self.decisions, self.events, self.bytes, self._duration,
                            self._last, tuple(sorted(self.causes.items())),
                            self.observer_errors, self.reason)

    def emit(self):
        value = self.snapshot()
        for sink in (self.callback, None if self.jsonl is None else self._write_jsonl):
            if sink is not None:
                try:
                    sink(value)
                except Exception:
                    self.observer_errors += 1
        return value

    def _write_jsonl(self, value):
        self.jsonl.write(json.dumps(asdict(value), sort_keys=True) + '\n')
        self.jsonl.flush()

    def cancel(self):
        with self._lock:
            if self.state in ('cancelled', 'succeeded', 'failed'):
                return False
            first = not self._cancel.is_set()
            self._cancel.set()
            self.state, self.reason = 'cancelling', 'job_cancelled'
            return first

    def _start(self, planned):
        if self._started is not None or self.state not in ('pending', 'cancelling'):
            raise ValueError('JobContext is single-use')
        self._started = self.clock()
        self.planned = planned
        if self.state == 'pending':
            self.state = 'running'
        self.emit()

    def stop_reason(self):
        if self._cancel.is_set():
            return 'job_cancelled'
        limits = self.limits
        if limits.seconds is not None and self.snapshot().duration >= limits.seconds:
            return 'job_time_budget'
        if limits.decisions is not None and self.decisions >= limits.decisions:
            return 'job_decision_budget'
        if limits.episodes is not None and self._attempt_episodes >= limits.episodes:
            return 'job_episode_budget'
        return None

    def _finish(self, state, reason=None):
        self.snapshot()  # Freeze duration before exposing terminal state.
        self.state, self.reason = state, reason
        self.emit()


def _error(error):
    # Arbitrary exception messages can contain tokens, personal paths or payloads.
    # The stable redaction is deliberate, rather than a fallible secret regex.
    name = type(error).__name__
    if not name.isidentifier() or len(name) > 80:
        name = 'Exception'
    return {'class': name, 'message': 'Exception details redacted'}


def _versions():
    return {'jobs': 1, 'package': __version__, 'recovery': 'recipe-actions-v1'}


class _Observer:
    def __init__(self, context, expected=None, persist=None):
        self.context = context
        self.expected = expected
        self.persist = persist
        self.actions, self.boundaries = [], []
        self.attempted_action = None
        self.reason = None
        self._events = self._count = self._stalled = 0

    def boundary(self, result, count, recorder):
        logical = recorder.timeline.context.to_json()
        point = {'decisions': count, 'context': logical, 'sha256': _hash(result.to_json())}
        if self.expected is not None and len(self.boundaries) < len(self.expected['boundaries']):
            if point != self.expected['boundaries'][len(self.boundaries)]:
                raise IncompatibleRecovery('Replay boundary differs at decision %d' % count)
        if count > self._count:
            self.actions.append(self.attempted_action)
            self.attempted_action = None
            self._stalled = self._stalled + 1 if logical['event_seq'] == self._events else 0
        self.boundaries.append(point)
        ctx = self.context
        ctx.decisions += count - self._count
        ctx.events += logical['event_seq'] - self._events
        self._count, self._events = count, logical['event_seq']
        ctx._last = ctx.snapshot().duration
        if (self.persist is not None and (self.expected is None or
                len(self.boundaries) >= len(self.expected['boundaries']))):
            self.persist()
        ctx.emit()

    def action(self, action):
        value = action.to_json() if isinstance(action, ActionV1) else deepcopy(action)
        # Closed action validation; never serialize arbitrary policy objects.
        value = ActionV1.from_json(value).to_json()
        position = len(self.actions)
        if self.expected is not None:
            actions = self.expected['actions']
            wanted = (actions[position] if position < len(actions) else
                      self.expected['attempted_action'] if position == len(actions) else None)
            if wanted is not None and value != wanted:
                raise IncompatibleRecovery('Replay action differs at decision %d' % position)
        self.attempted_action = value

    def stop_reason(self):
        if (self.expected is not None and self.expected['stop_reason'] is not None and
                len(self.boundaries) >= len(self.expected['boundaries'])):
            self.reason = self.expected['stop_reason']
        elif self._stalled >= self.context.limits.no_progress_decisions:
            self.reason = 'job_no_progress'
        else:
            self.reason = self.context.stop_reason()
        return self.reason

    def checkpoint(self):
        return {'snapshot': None, 'actions': self.actions, 'boundaries': self.boundaries,
                'attempted_action': self.attempted_action, 'stop_reason': self.reason}

    def checked_prefix(self):
        if self.expected is not None and len(self.boundaries) < len(self.expected['boundaries']):
            raise IncompatibleRecovery('Replay ended before the checked boundary')


def _read_recovery(root, plan, filename='job-recovery.json'):
    try:
        _validate_plan(plan)
        data = decode_json(_read_file(root / filename, MAX_RECORD_BYTES))
        keys(data, ('schema_version', 'versions', 'plan_sha256', 'limits', 'storage_limits',
                    'entry', 'last_confirmed_index', 'checkpoint', 'error', 'stage', 'state', 'reason'))
        require(type(data['schema_version']) is int and data['schema_version'] == 1
                and encode_json(data['versions']) == encode_json(_versions()),
                'Recovery version mismatch')
        require(data['plan_sha256'] == _job_hash(plan), 'Recovery plan mismatch')
        JobLimits(**data['limits'])
        from .storage import StorageLimits
        StorageLimits(**data['storage_limits'])
        index = data['last_confirmed_index']
        require(type(index) is int and -1 <= index < len(plan['episodes']), 'Recovery index mismatch')
        entry = data['entry']
        require(entry is None or entry in plan['episodes'], 'Recovery entry mismatch')
        require(data['stage'] in ('generation', 'publication', 'idle'), 'Recovery stage mismatch')
        require(data['state'] in ('running', 'cancelling', 'failed', 'cancelled', 'succeeded'), 'Recovery state mismatch')
        require(data['reason'] in (None, 'job_cancelled', 'job_time_budget', 'job_decision_budget',
                'job_episode_budget', 'job_no_progress', 'job_error'), 'Invalid job stop reason')
        if data['error'] is not None:
            keys(data['error'], ('class', 'message'))
            require(type(data['error']['class']) is str and data['error']['class'].isidentifier()
                    and data['error']['message'] == 'Exception details redacted', 'Invalid recovery error')
        checkpoint = data['checkpoint']
        keys(checkpoint, ('snapshot', 'actions', 'boundaries', 'attempted_action', 'stop_reason'))
        require(checkpoint['stop_reason'] in (None, 'job_cancelled', 'job_time_budget',
                'job_decision_budget', 'job_episode_budget', 'job_no_progress'),
                'Invalid checkpoint stop reason')
        require(checkpoint['snapshot'] is None, 'Incompatible snapshot: recipe-actions-v1 requires an initial recipe')
        actions, boundaries = checkpoint['actions'], checkpoint['boundaries']
        require(type(actions) is list and type(boundaries) is list and len(actions) <= 10000
                and (len(boundaries) == len(actions) + 1 or not boundaries and not actions), 'Invalid replay prefix')
        for action in actions:
            ActionV1.from_json(action)
        if checkpoint['attempted_action'] is not None:
            ActionV1.from_json(checkpoint['attempted_action'])
        for count, point in enumerate(boundaries):
            keys(point, ('decisions', 'context', 'sha256'))
            require(point['decisions'] == count and type(point['sha256']) is str
                    and len(point['sha256']) == 64, 'Invalid replay boundary')
        return data
    except OSError as error:
        raise IncompatibleRecovery('Recovery metadata unavailable: ' + type(error).__name__) from error
    except (ValueError, TypeError, KeyError) as error:
        raise IncompatibleRecovery('Incompatible recovery: %s' % str(error)) from error


def _confirmed(context, root, limits, *, verify=True):
    reader = DatasetReader(root, limits=limits)
    if verify:
        reader.verify()
    causes, size = dict(context.causes), context.bytes
    for index in range(context.confirmed, len(reader)):
        episode = reader.episode(index)
        manifest = episode.manifest
        size += sum(info['bytes'] for info in manifest['files'].values())
        end = manifest['end']
        reason = end.get('reason', end['kind'])
        causes[reason] = causes.get(reason, 0) + 1
    context.confirmed, context.bytes, context.causes = len(reader), size, causes


def _execute_plan(plan, output, *, context=None, resume=False, storage_limits=None, fault=None):
    """Run a plan into DATA-07 storage. Return terminal immutable Progress.

    Failed attempts raise their original exception after writing recovery data.
    Resume revalidates committed storage under its writer lock before skipping it.
    ``fault`` is DATA-07's trusted local fault-injection callback, never file data.
    """
    config = _validate_plan(plan)
    plan = build_plan(config, _legacy=plan['schema_version'] == 1)
    root = Path(output).resolve()
    package = Path(__file__).resolve().parents[1]
    if root == package or package in root.parents:
        raise ValueError('Datasets cannot be written inside the installed package')
    ctx = context or JobContext()
    recovery = _read_recovery(root, plan) if resume else None
    if recovery is not None:
        from .storage import StorageLimits
        if storage_limits is None:
            storage_limits = StorageLimits(**recovery['storage_limits'])
        require(asdict(ctx.limits) == recovery['limits'], 'Resume requires identical job limits')
    elif root.exists():
        raise FileExistsError('Job output must be new; use resume explicitly')
    plan_digest = _job_hash(plan)
    ctx._start(len(plan['episodes']))
    observer, entry, stage = _Observer(ctx), None, 'idle'
    with DatasetWriter(root, plan, limits=storage_limits, fault=fault) as writer:
        _confirmed(ctx, root, writer.limits)
        if recovery is not None:
            require(ctx.confirmed >= recovery['last_confirmed_index'] + 1,
                    'Confirmed prefix was lost')
        def save(error=None):
            data = {'schema_version': 1, 'versions': _versions(), 'plan_sha256': plan_digest,
                    'limits': asdict(ctx.limits), 'storage_limits': asdict(writer.limits),
                    'entry': entry, 'last_confirmed_index': ctx.confirmed - 1,
                    'checkpoint': observer.checkpoint(), 'error': error, 'stage': stage,
                    'state': ctx.state, 'reason': ctx.reason}
            payload = encode_json(data)
            _atomic(root, 'job-recovery.json', payload)
            if error is not None:
                _atomic(root, 'job-failure-' + uuid.uuid4().hex + '.json', payload)

        try:
            if recovery is None:
                save()
            ctx.emit()
            for index in range(writer.committed_episodes, len(plan['episodes'])):
                reason = ctx.stop_reason()
                if reason is not None:
                    break
                entry, stage = plan['episodes'][index], 'generation'
                expected = (recovery['checkpoint'] if recovery is not None and
                            recovery['entry'] == entry else None)
                observer = _Observer(ctx, expected, persist=save)
                if expected is None:
                    save()
                with tempfile.TemporaryDirectory(prefix='.job-', dir=str(root)) as spool:
                    summary = _run(entry, config, Path(spool), observer=observer)
                    observer.checked_prefix()
                    stage = 'publication'
                    save()
                    writer.append_episode(EpisodeReader(spool, entry['episode_id']))
                ctx._attempt_episodes += 1
                _confirmed(ctx, root, writer.limits, verify=False)
                ctx.emit()
                reason = summary['end']['reason']
                if reason.startswith('job_'):
                    break
            else:
                reason = None
            ctx.snapshot()
            if reason is not None and (reason.startswith('job_') or ctx.confirmed < ctx.planned):
                ctx.state, ctx.reason = 'cancelled', reason
            else:
                ctx.state, ctx.reason = 'succeeded', None
            save()
            ctx.emit()
        except BaseException as error:
            # Publication may have happened before an interrupted fsync/return.
            # Read durable truth rather than retrying on a poisoned writer.
            try:
                _confirmed(ctx, root, writer.limits)
            except (OSError, ValueError):
                pass
            ctx.failed += 1
            ctx._finish('cancelled' if isinstance(error, KeyboardInterrupt) else 'failed',
                        'job_cancelled' if isinstance(error, KeyboardInterrupt) else 'job_error')
            try:
                save(_error(error))
            except (OSError, ValueError):
                pass  # Preserve the original exception when diagnostics also fail.
            raise
    return ctx.snapshot()



def execute_plan(plan, output, *, context=None, resume=False, storage_limits=None, fault=None):
    """Execute one sequential attempt; see :func:`_execute_plan` for recovery semantics."""
    ctx = context or JobContext()
    if ctx._started is not None:
        raise ValueError('JobContext is single-use')
    try:
        return _execute_plan(plan, output, context=ctx, resume=resume,
                             storage_limits=storage_limits, fault=fault)
    except BaseException as error:
        # Include writer acquisition/close failures in lifecycle reporting.
        if ctx._started is not None and ctx.state not in ('failed', 'cancelled'):
            ctx.failed += 1
            ctx._finish('cancelled' if isinstance(error, KeyboardInterrupt) else 'failed',
                        'job_cancelled' if isinstance(error, KeyboardInterrupt) else 'job_error')
        raise


def generate(config, **kwargs):
    return execute_plan(build_plan(config), config.output, **kwargs)


def resume(output, *, context=None, **kwargs):
    root = Path(output)
    plan = _read_job_json(root / 'plan.json')
    recovery = _read_recovery(root, plan)
    if context is None:
        context = JobContext(JobLimits(**recovery['limits']))
    return execute_plan(plan, root, context=context, resume=True, **kwargs)


def reproduce(output):
    """Re-run the same policy/recipe/seeds and compare the checked failure point.

    Never report disappearance of a failure as success. Publication failures need
    the original storage fault/environment; a clean run returns not_reproduced.
    """
    source = Path(output)
    root = source if source.is_dir() else source.parent
    plan = _read_job_json(root / 'plan.json')
    data = _read_recovery(root, plan, 'job-recovery.json' if source.is_dir() else source.name)
    config = _validate_plan(plan)
    if data['error'] is None or data['entry'] is None:
        raise IncompatibleRecovery('No reproducible episode failure was recorded')
    context = JobContext(JobLimits(**data['limits']))
    context._start(len(plan['episodes']))
    observer = _Observer(context, data['checkpoint'])
    stage, actual = 'generation', None
    with tempfile.TemporaryDirectory(prefix='botbowl-reproduce-') as temporary:
        spool = Path(temporary) / 'spool'
        try:
            _run(data['entry'], config, spool, observer=observer)
            observer.checked_prefix()
            stage = 'publication'
            from .storage import StorageLimits
            with DatasetWriter(Path(temporary) / 'store', plan,
                               limits=StorageLimits(**data['storage_limits'])) as writer:
                # DATA-07 accepts only the next canonical index. Reproduce prior
                # entries too, preserving inputs; no dummy prefix or reseeding.
                for entry in plan['episodes']:
                    if entry == data['entry']:
                        writer.append_episode(EpisodeReader(spool, entry['episode_id']))
                        break
                    _run(entry, config, spool)
                    writer.append_episode(EpisodeReader(spool, entry['episode_id']))
        except IncompatibleRecovery:
            raise
        except Exception as error:
            actual = _error(error)
    observer.checked_prefix()
    same = (actual == data['error'] and stage == data['stage'] and
            observer.checkpoint() == data['checkpoint'])
    return {'status': 'reproduced' if same else 'not_reproduced',
            'expected': data['error'], 'actual': actual, 'stage': stage}


class WorkerTimeout(RuntimeError):
    pass


def _supervised_worker(plan, output, limits, cancel):
    context = JobContext(JobLimits(**limits))
    context._cancel = cancel
    try:
        execute_plan(plan, output, context=context)
    except BaseException:
        raise SystemExit(1) from None  # Do not print arbitrary worker exception payloads.


def supervise(plan, output, *, context=None):
    """Optional single spawned worker with bounded joins; never targets other PIDs.

    Cooperative progress is available through sequential execute_plan. This
    supervisor exposes lifecycle progress and retains DATA-07's durable prefix.
    A hard stop is explicitly non-restorable, never an engine snapshot.
    """
    _validate_plan(plan)
    root = Path(output).resolve()
    package = Path(__file__).resolve().parents[1]
    if root == package or package in root.parents:
        raise ValueError('Datasets cannot be written inside the installed package')
    if root.exists():
        raise FileExistsError('Supervised output must be new')
    ctx = context or JobContext()
    ctx._start(len(plan['episodes']))
    if ctx.stop_reason() is not None:
        ctx._finish('cancelled', ctx.stop_reason())
        return ctx.snapshot()
    spawn = multiprocessing.get_context('spawn')
    cancel = spawn.Event()
    process = spawn.Process(target=_supervised_worker,
                            args=(plan, str(output), asdict(ctx.limits), cancel))
    process.start()
    stopped_at = None
    forced = False
    try:
        while process.is_alive():
            if ctx.stop_reason() is not None:
                cancel.set()
                if stopped_at is None:
                    stopped_at = time.monotonic()
            duration = time.monotonic() - stopped_at if stopped_at is not None else ctx.snapshot().duration
            if duration >= ctx.limits.worker_timeout:
                forced = True
                break
            process.join(min(0.05, ctx.limits.join_timeout))
        if forced:
            process.terminate()
            process.join(ctx.limits.join_timeout)
            if process.is_alive():
                process.kill()
                process.join(ctx.limits.join_timeout)
            if process.is_alive():
                raise WorkerTimeout('Owned worker did not exit after terminate/kill')
        code = process.exitcode
    finally:
        if process.is_alive():
            process.terminate()
            process.join(ctx.limits.join_timeout)
            if process.is_alive():
                process.kill()
                process.join(ctx.limits.join_timeout)
        if not process.is_alive():
            process.close()
    root.mkdir(parents=True, exist_ok=True)
    if root.exists():
        _atomic(root, 'job-supervisor.json', encode_json({
            'schema_version': 1, 'forced': forced, 'restorable_snapshot': False,
            'exitcode': code, 'reason': 'worker_timeout' if forced else 'worker_exit'}))
    if (root / 'manifest.json').exists():
        _confirmed(ctx, root, None)
    if forced or code != 0:
        ctx.failed += 1
        ctx._finish('cancelled' if ctx._cancel.is_set() else 'failed',
                    'worker_timeout' if forced else 'worker_exit')
    else:
        data = _read_recovery(root, plan)
        from .storage import StorageLimits
        _confirmed(ctx, root, StorageLimits(**data['storage_limits']))
        ctx._finish(data['state'], data['reason'])
    return ctx.snapshot()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--plan', required=True, type=Path)
    run.add_argument('--output', required=True)
    for name in ('decisions', 'episodes'):
        run.add_argument('--' + name, type=int)
    run.add_argument('--seconds', type=float)
    run.add_argument('--no-progress-decisions', type=int, default=100)
    run.add_argument('--progress', type=Path)
    for command in ('resume', 'reproduce'):
        sub.add_parser(command).add_argument('output', type=Path)
    args = parser.parse_args()
    stream, previous = None, None
    try:
        if args.command == 'reproduce':
            report = reproduce(args.output)
            print(json.dumps(report, sort_keys=True))
            return 0 if report['status'] == 'reproduced' else 1
        if args.command == 'run':
            if args.progress:
                stream = args.progress.open('x', encoding='utf-8')
            context = JobContext(JobLimits(args.decisions, args.episodes, args.seconds,
                                           args.no_progress_decisions), jsonl=stream)
            plan = _read_job_json(args.plan)
        else:
            plan = _read_job_json(args.output / 'plan.json')
            data = _read_recovery(args.output, plan)
            context = JobContext(JobLimits(**data['limits']))
        previous = signal.signal(signal.SIGINT, lambda *unused: context.cancel())
        result = execute_plan(plan, args.output, context=context, resume=args.command == 'resume')
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.state == 'succeeded' else 1
    except IncompatibleRecovery as error:
        print(json.dumps({'status': 'incompatible', 'message': str(error)}))
        return 2
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error': _error(error)}))
        return 2
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
        if stream is not None:
            stream.close()


if __name__ == '__main__':
    raise SystemExit(main())
