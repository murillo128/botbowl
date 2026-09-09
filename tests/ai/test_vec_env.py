"""Exercise the A2C worker lifecycle with spawn, without torch or gym."""
import multiprocessing
from types import SimpleNamespace

import numpy as np
import pytest

from examples.a2c.vec_env import VecEnv, WorkerError, WorkerTimeoutError


class StubEnv:
    def __init__(self, closes, failure=None, terminal=False, wait=None):
        self.closes = closes
        self.failure = failure
        self.terminal = terminal
        self.wait = wait
        self.root_env = self
        self.game = SimpleNamespace(state=SimpleNamespace(
            home_team=SimpleNamespace(state=SimpleNamespace(score=1)),
            away_team=SimpleNamespace(state=SimpleNamespace(score=0))))

    def reset(self):
        if self.failure == 'reset':
            raise ValueError('reset cause')
        return (np.zeros((1,)), np.zeros((1,)), np.ones((1,)))

    def step(self, action):
        if self.wait:
            self.wait[0].set()
            if not self.wait[1].wait(10):
                raise TimeoutError('test release was not signaled')
        if self.failure == 'step':
            raise RuntimeError('step cause')
        if self.failure == 'eof':
            raise EOFError('environment EOF cause')
        return self.reset(), 1.0, self.terminal, {}

    def close(self):
        with self.closes.get_lock():
            self.closes.value += 1
        if self.failure == 'close':
            raise RuntimeError('close cause')


def make_env(**kwargs):
    ctx = multiprocessing.get_context('spawn')
    closes = ctx.Value('i', 0)
    envs = VecEnv([StubEnv(closes, **kwargs)], context=ctx, reset_steps=1, timeout=5)
    return envs, closes


def assert_closed(envs, closes):
    assert all(not p.is_alive() for p in envs.ps)
    assert all(p.exitcode == 0 for p in envs.ps)
    assert all(r.closed for r in envs.remotes)
    assert closes.value == 1


@pytest.mark.parametrize('terminal', [False, True])
def test_spawn_terminal_and_truncation_are_distinct(terminal):
    envs, closes = make_env(terminal=terminal)
    try:
        reset = envs.reset()
        assert not reset[-1][0] and not reset[-2][0]
        *obs, reward, scored, conceded, terminated, truncated = envs.step([0])
        assert terminated[0] == terminal and truncated[0] != terminal
        assert reward[0] == scored[0] == 1 and conceded[0] == 0
    finally:
        envs.close()
    envs.close()
    assert_closed(envs, closes)


@pytest.mark.parametrize('failure', ['reset', 'step', 'eof', 'close'])
def test_spawn_exception_preserves_traceback_and_closes_once(failure):
    envs, closes = make_env(failure=failure)
    try:
        with pytest.raises(WorkerError) as error:
            if failure == 'close':
                envs.close()
            elif failure == 'reset':
                envs.reset()
            else:
                envs.step([0])
        assert 'Traceback' in str(error.value)
        assert ('environment EOF cause' if failure == 'eof' else failure + ' cause') in str(error.value)
        assert error.value in envs.errors
    finally:
        envs.close()
    assert_closed(envs, closes)


def test_spawn_response_and_close_waits_are_bounded_without_killing_worker():
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    envs, closes = make_env(wait=(ready, release))
    try:
        envs.reset()  # Complete spawn/import before testing the small deadline.
        envs.remotes[0].send(('step', [0, 1]))
        assert ready.wait(5)
        envs.timeout = 0.02
        with pytest.raises(WorkerTimeoutError, match='truncated/cancelled'):
            envs._receive(envs.remotes[0])
        with pytest.raises(WorkerTimeoutError, match='still running PIDs'):
            envs.close()
        assert envs.ps[0].is_alive()
        assert envs.remotes[0].closed
    finally:
        release.set()
        for process in envs.ps:
            process.join(timeout=5)
        envs.close()
    assert_closed(envs, closes)


def test_parent_disconnect_still_closes_worker_environment():
    envs, closes = make_env()
    envs.reset()
    envs.remotes[0].close()
    envs.ps[0].join(timeout=5)
    envs.close()
    assert_closed(envs, closes)
