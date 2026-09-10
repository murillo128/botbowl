"""OPS-03 job lifecycle, durable recovery and liveness boundaries."""
from dataclasses import FrozenInstanceError, replace
import io
import json
import multiprocessing
import time

import pytest

from botbowl.lab import generate as generator
from botbowl.lab.generate import JobConfig, build_plan
from botbowl.lab.jobs import (JobContext, JobLimits, IncompatibleRecovery,
                              execute_plan, reproduce, resume, supervise)
from botbowl.lab.policies import ReferencePolicy
from botbowl.lab.storage import DatasetReader, DatasetWriter


def plan(tmp_path, **kwargs):
    return build_plan(JobConfig(str(tmp_path / 'job'), **kwargs))


def test_states_progress_and_semantic_identity(tmp_path):
    snapshots, stream = [], io.StringIO()
    ctx = JobContext(callback=snapshots.append, jsonl=stream)
    assert ctx.snapshot().state == 'pending'
    value = execute_plan(plan(tmp_path, episodes=2), tmp_path / 'job', context=ctx)
    assert value.state == 'succeeded' and value.confirmed_episodes == 2
    assert value.decisions == 2 and value.events > 0 and value.confirmed_bytes > 0
    assert not ctx.cancel() and ctx.snapshot() == value
    with pytest.raises(ValueError, match='single-use'):
        execute_plan(plan(tmp_path), tmp_path / 'reuse', context=ctx)
    assert ctx.snapshot() == value
    with pytest.raises(FrozenInstanceError):
        value.state = 'failed'
    for field in ('confirmed_episodes', 'decisions', 'events', 'confirmed_bytes', 'duration', 'last_progress'):
        values = [getattr(s, field) for s in snapshots]
        assert values == sorted(values)
    assert json.loads(stream.getvalue().splitlines()[-1])['state'] == 'succeeded'
    execute_plan(plan(tmp_path, episodes=2), tmp_path / 'other')
    assert DatasetReader(tmp_path / 'job').episode(0).manifest == DatasetReader(tmp_path / 'other').episode(0).manifest


def test_cancel_before_double_and_resume(tmp_path):
    ctx = JobContext()
    assert ctx.cancel() and not ctx.cancel()
    assert ctx.snapshot().state == 'cancelling'
    value = execute_plan(plan(tmp_path), tmp_path / 'job', context=ctx)
    assert value.state == 'cancelled' and value.confirmed_episodes == value.decisions == 0
    assert not ctx.cancel()
    assert resume(tmp_path / 'job').state == 'succeeded'


def test_cancel_during_safe_decision_and_no_duplicates(tmp_path):
    ctx = JobContext()
    def cancel(value):
        if value.decisions == 2:
            ctx.cancel()
    ctx.callback = cancel
    p = plan(tmp_path, episodes=2, scenario='match', max_decisions=5)
    value = execute_plan(p, tmp_path / 'job', context=ctx)
    assert value.state == 'cancelled' and value.decisions == 2
    reader = DatasetReader(tmp_path / 'job')
    assert len(reader) == 1
    original = reader.episode(0).manifest
    assert original['end'] == {'kind': 'truncated', 'reason': 'job_cancelled'}
    assert resume(tmp_path / 'job').confirmed_episodes == 2
    assert DatasetReader(tmp_path / 'job').episode(0).manifest == original
    assert resume(tmp_path / 'job').confirmed_episodes == 2


@pytest.mark.parametrize('limits,decisions,episodes,reason', [
    (JobLimits(decisions=2), 2, 1, 'job_decision_budget'),
    (JobLimits(episodes=1), 5, 1, 'job_episode_budget'),
])
def test_exact_work_budgets(tmp_path, limits, decisions, episodes, reason):
    value = execute_plan(plan(tmp_path, episodes=3, scenario='match', max_decisions=5),
                         tmp_path / 'job', context=JobContext(limits))
    assert (value.decisions, value.confirmed_episodes, value.reason) == (decisions, episodes, reason)


def test_exact_fake_clock_and_callback_failure(tmp_path):
    now = [0.0]
    def callback(value):
        if value.decisions == 1:
            now[0] = 2.0
        raise RuntimeError('observer failure')
    ctx = JobContext(JobLimits(seconds=2), clock=lambda: now[0], callback=callback)
    value = execute_plan(plan(tmp_path, scenario='match'), tmp_path / 'job', context=ctx)
    assert value.decisions == 1 and value.duration == 2
    assert value.reason == 'job_time_budget' and value.observer_errors > 0
    DatasetReader(tmp_path / 'job').verify()


@pytest.mark.parametrize('limits', [dict(decisions=0), dict(episodes=True), dict(seconds=float('nan')),
                                    dict(worker_timeout=-1), dict(no_progress_decisions=0)])
def test_positive_limits(limits):
    with pytest.raises(ValueError):
        JobLimits(**limits)


def test_policy_failure_recipe_replay_and_sanitization(tmp_path, monkeypatch):
    original = ReferencePolicy.act
    def fail(self, *args, **kwargs):
        if getattr(self, '_job_calls', 0) == 1:
            raise RuntimeError('secret=sk-123 /home/private/me')
        self._job_calls = getattr(self, '_job_calls', 0) + 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ReferencePolicy, 'act', fail)
    ctx = JobContext()
    with pytest.raises(RuntimeError):
        execute_plan(plan(tmp_path, scenario='match'), tmp_path / 'job', context=ctx)
    assert ctx.snapshot().state == 'failed' and ctx.snapshot().failed_episodes == 1
    recovery = (tmp_path / 'job' / 'job-recovery.json').read_text()
    assert 'sk-123' not in recovery and '/home/' not in recovery
    assert json.loads(recovery)['checkpoint']['actions']
    assert reproduce(tmp_path / 'job')['status'] == 'reproduced'
    with pytest.raises(RuntimeError):
        resume(tmp_path / 'job')
    monkeypatch.setattr(ReferencePolicy, 'act', original)
    assert reproduce(tmp_path / 'job')['status'] == 'not_reproduced'
    assert resume(tmp_path / 'job').state == 'succeeded'
    assert len(DatasetReader(tmp_path / 'job')) == 1


@pytest.mark.parametrize('stage,committed', [('before_manifest_replace', 0), ('after_manifest_replace', 1)])
def test_interrupted_publication_reconciles_durable_truth(tmp_path, stage, committed):
    def fault(point):
        if point == stage:
            raise OSError('interrupted')
    p = plan(tmp_path, episodes=2)
    ctx = JobContext()
    with pytest.raises(OSError):
        execute_plan(p, tmp_path / 'job', context=ctx, fault=fault)
    assert ctx.snapshot().confirmed_episodes == committed
    assert len(DatasetReader(tmp_path / 'job')) == committed
    assert reproduce(tmp_path / 'job')['status'] == 'not_reproduced'
    assert resume(tmp_path / 'job').confirmed_episodes == 2
    assert len(DatasetReader(tmp_path / 'job')) == 2
    assert not list((tmp_path / 'job').glob('.job-*'))


@pytest.mark.parametrize('field', ['snapshot', 'versions', 'action', 'boundary'])
def test_incompatible_recovery_is_explicit(tmp_path, monkeypatch, field):
    original = ReferencePolicy.act
    def fail(self, *args, **kwargs):
        if getattr(self, '_job_calls', 0) == 1:
            raise RuntimeError('fail')
        self._job_calls = getattr(self, '_job_calls', 0) + 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ReferencePolicy, 'act', fail)
    with pytest.raises(RuntimeError):
        execute_plan(plan(tmp_path, scenario='match'), tmp_path / 'job')
    path = tmp_path / 'job' / 'job-recovery.json'
    data = json.loads(path.read_text())
    if field == 'snapshot':
        data['checkpoint']['snapshot'] = {'version': 999}
    elif field == 'versions':
        data['versions']['jobs'] = 999
    elif field == 'action':
        data['checkpoint']['actions'][0]['kind'] = 'changed'
    else:
        data['checkpoint']['boundaries'][0]['sha256'] = '0' * 64
    path.write_text(json.dumps(data))
    with pytest.raises((IncompatibleRecovery, ValueError)):
        reproduce(tmp_path / 'job')


def test_engine_no_progress_is_truncation(tmp_path):
    value = execute_plan(plan(tmp_path, scenario='match', max_steps=1), tmp_path / 'job')
    assert value.state == 'succeeded'
    assert DatasetReader(tmp_path / 'job').episode(0).manifest['end'] == {
        'kind': 'truncated', 'reason': 'execution_budget'}


def test_spawn_supervisor_success_and_bounded_timeout(tmp_path):
    before = {p.pid for p in multiprocessing.active_children()}
    value = supervise(plan(tmp_path), tmp_path / 'job')
    assert value.state == 'succeeded' and value.confirmed_episodes == 1
    started = time.monotonic()
    ctx = JobContext(JobLimits(worker_timeout=0.001, join_timeout=0.2))
    value = supervise(plan(tmp_path), tmp_path / 'timeout', context=ctx)
    assert value.state == 'failed' and value.reason == 'worker_timeout'
    assert time.monotonic() - started < 5
    assert {p.pid for p in multiprocessing.active_children()} == before


def _uncooperative_worker(plan, output, limits, cancel):
    # Top-level spawn target: deliberately ignores the cooperative event.
    while True:
        time.sleep(0.05)


def test_noncooperative_worker_does_not_kill_unrelated_process(tmp_path, monkeypatch):
    from botbowl.lab import jobs
    monkeypatch.setattr(jobs, '_supervised_worker', _uncooperative_worker)
    spawn = multiprocessing.get_context('spawn')
    unrelated = spawn.Process(target=time.sleep, args=(20,))
    unrelated.start()
    try:
        value = supervise(plan(tmp_path), tmp_path / 'job', context=JobContext(
            JobLimits(worker_timeout=0.2, join_timeout=0.2)))
        assert value.state == 'failed' and unrelated.is_alive()
        report = json.loads((tmp_path / 'job' / 'job-supervisor.json').read_text())
        assert report['forced'] and not report['restorable_snapshot']
        assert report['versions']['jobs'] == 1
        assert report['limits']['worker_timeout'] == 0.2
        assert report['entry'] == plan(tmp_path)['episodes'][0]
        assert json.loads((tmp_path / 'job' / 'plan.json').read_text()) == plan(tmp_path)
        recovery = json.loads((tmp_path / 'job' / 'job-recovery.json').read_text())
        assert recovery['checkpoint']['snapshot'] is None
        with pytest.raises(IncompatibleRecovery, match='Worker timeout'):
            reproduce(tmp_path / 'job')
        assert resume(tmp_path / 'job').state == 'succeeded'
    finally:
        unrelated.terminate()
        unrelated.join(2)
        unrelated.close()


def test_no_event_progress_driver_guard():
    from types import SimpleNamespace
    from botbowl.lab.jobs import _Observer
    ctx = JobContext(JobLimits(no_progress_decisions=2))
    ctx._start(1)
    observer = _Observer(ctx)
    class Logical:
        def to_json(self):
            return {'event_seq': 0}
    result = SimpleNamespace(to_json=lambda: {'state': 'unchanged'})
    recorder = SimpleNamespace(timeline=SimpleNamespace(context=Logical()))
    for count in range(3):
        observer.boundary(result, count, recorder)
    assert observer.stop_reason() == 'job_no_progress'


def test_resource_cleanup_on_policy_failure(tmp_path, monkeypatch):
    from botbowl.lab.session import SimulationSession
    from botbowl.lab.recording import JsonlEpisodeWriter
    closed = []
    for cls in (ReferencePolicy, SimulationSession, JsonlEpisodeWriter, DatasetWriter):
        original = cls.close
        def close(self, original=original, cls=cls):
            closed.append(cls)
            return original(self)
        monkeypatch.setattr(cls, 'close', close)
    def fail(*args, **kwargs):
        raise RuntimeError('failure')
    monkeypatch.setattr(ReferencePolicy, 'act', fail)
    with pytest.raises(RuntimeError):
        execute_plan(plan(tmp_path), tmp_path / 'job')
    assert set(closed) == {ReferencePolicy, SimulationSession, JsonlEpisodeWriter, DatasetWriter}
    assert len(list((tmp_path / 'job').glob('job-failure-*.json'))) == 1


def test_cli_reproduction_and_incompatibility(tmp_path, monkeypatch, capsys):
    import sys
    from botbowl.lab.jobs import main
    def fail(*args, **kwargs):
        raise RuntimeError('private message')
    monkeypatch.setattr(ReferencePolicy, 'act', fail)
    with pytest.raises(RuntimeError):
        execute_plan(plan(tmp_path), tmp_path / 'job')
    monkeypatch.setattr(sys, 'argv', ['jobs', 'reproduce', str(tmp_path / 'job')])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'reproduced'
    path = tmp_path / 'job' / 'job-recovery.json'
    data = json.loads(path.read_text())
    data['checkpoint']['snapshot'] = {'schema_version': 999}
    path.write_text(json.dumps(data))
    assert main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'incompatible' and 'snapshot' in report['message']


def test_writer_acquisition_failure_is_terminal(tmp_path, monkeypatch):
    from botbowl.lab import jobs
    def fail(*args, **kwargs):
        raise OSError('writer unavailable')
    monkeypatch.setattr(jobs, 'DatasetWriter', fail)
    context = JobContext()
    with pytest.raises(OSError):
        execute_plan(plan(tmp_path), tmp_path / 'job', context=context)
    assert context.snapshot().state == 'failed'


def test_replay_retains_administrative_boundary_before_failed_publication(tmp_path, monkeypatch):
    from botbowl.lab import jobs
    append = DatasetWriter.append_episode
    def fail(self, reader):
        raise OSError('persistent publication failure')
    monkeypatch.setattr(DatasetWriter, 'append_episode', fail)
    context = JobContext(JobLimits(decisions=2))
    with pytest.raises(OSError):
        execute_plan(plan(tmp_path, scenario='match', max_decisions=8), tmp_path / 'job', context=context)
    assert reproduce(tmp_path / 'job')['status'] == 'reproduced'
    failure = next((tmp_path / 'job').glob('job-failure-*.json'))
    monkeypatch.setattr(DatasetWriter, 'append_episode', append)
    assert resume(tmp_path / 'job').confirmed_episodes == 1
    assert reproduce(failure)['status'] == 'not_reproduced'
    assert DatasetReader(tmp_path / 'job').episode(0).manifest['end']['reason'] == 'job_decision_budget'
