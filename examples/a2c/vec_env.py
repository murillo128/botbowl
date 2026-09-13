"""A2C subprocess environments with bounded waits and explicit truncation.

Workers close cooperatively; a timeout reports the worker PID and never kills it.
No torch dependency is needed to exercise the process/pipe lifecycle.
"""
import multiprocessing
import time
import traceback

import numpy as np
import botbowl


class WorkerError(RuntimeError):
    """A worker failed; the message retains its remote exception traceback."""


class WorkerTimeoutError(TimeoutError):
    """An administrative wait expired; no match result is implied."""


def worker(remote, parent_remote, env, reset_steps):
    parent_remote.close()
    failure = None
    try:
        steps = tds = tds_opp = 0
        next_opp = None
        root = getattr(env, 'unwrapped', None) or env.root_env

        def reset_env():
            if next_opp is not None:
                controller = env
                while hasattr(controller, 'env') and not hasattr(controller, 'opponent'):
                    controller = controller.env
                if hasattr(controller, 'opponent'):
                    if not callable(next_opp):
                        raise ValueError('Gymnasium swap requires an explicit callable opponent')
                    controller.opponent = next_opp
                else:
                    root.away_agent = next_opp
            value = env.reset()
            if len(value) == 2 and isinstance(value[0], dict):
                obs, info = value
                if info.get('terminated') or info.get('truncated'):
                    raise ValueError('Episode ended during reset; no learner decision is available')
                return tuple(obs[k] for k in ('spatial', 'non_spatial', 'action_mask')), info
            return value, {}
        ppcg_wrapper = None
        if hasattr(env, 'get_wrapper_with_type'):
            from botbowl.ai.env import PPCGWrapper
            ppcg_wrapper = env.get_wrapper_with_type(PPCGWrapper)
        while True:
            try:
                command, data = remote.recv()
            except EOFError:
                break  # Parent disconnected; release the environment.
            if command == 'close':
                break
            if command == 'swap':
                next_opp = data
                continue
            if command == 'reset':
                steps = tds = tds_opp = 0
                obs, info = reset_env()
                result = (*obs, 0.0, 0, 0, False, False)
            elif command == 'step':
                steps += 1
                if ppcg_wrapper is not None:
                    ppcg_wrapper.difficulty = data[1]
                value = env.step(data[0])
                if len(value) == 5:
                    obs, reward, terminated, truncated, info = value
                    obs = tuple(obs[k] for k in ('spatial', 'non_spatial', 'action_mask'))
                else:
                    obs, reward, terminated, info = value
                    truncated = False
                game = root.game
                scored = game.state.home_team.state.score - tds
                conceded = game.state.away_team.state.score - tds_opp
                tds = game.state.home_team.state.score
                tds_opp = game.state.away_team.state.score
                truncated = truncated or (steps >= reset_steps and not terminated)
                if terminated or truncated:
                    final_observation, final_info = obs, info
                    obs, reset_info = reset_env()
                    info = {'final_observation': final_observation, 'final_info': final_info,
                            'reset_info': reset_info}
                    steps = tds = tds_opp = 0
                result = (*obs, reward, scored, conceded, terminated, truncated)
            else:
                raise ValueError(f"Unknown worker command: {command!r}")
            remote.send(('ok', (result, info)))
    except Exception:
        failure = traceback.format_exc()
    finally:
        try:
            env.close()
        except Exception:
            failure = (failure or '') + traceback.format_exc()
        try:
            if failure is not None:
                remote.send(('error', failure))
        except (BrokenPipeError, EOFError, OSError):
            # A disconnected parent cannot receive the cause; retain it in logs.
            if failure is not None:
                import sys
                print(failure, file=sys.stderr)
        finally:
            remote.close()


class VecEnv:
    def __init__(self, envs, *, reset_steps=5000, timeout=30.0, context=None):
        if type(reset_steps) is not int or reset_steps < 1:
            raise ValueError('reset_steps must be a positive integer')
        if not np.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        self.closed = False
        self.timeout = timeout
        self.errors = []
        self.last_infos = []
        self.remotes = []
        self.ps = []
        ctx = context or multiprocessing.get_context('spawn')
        try:
            for env in envs:
                remote, work_remote = ctx.Pipe()
                process = ctx.Process(target=worker, args=(work_remote, remote, env, reset_steps))
                try:
                    process.start()
                except BaseException:
                    remote.close()
                    raise
                finally:
                    work_remote.close()
                self.remotes.append(remote)
                self.ps.append(process)
        except BaseException:
            self.close()
            raise

    def _receive(self, remote):
        if not remote.poll(self.timeout):
            raise WorkerTimeoutError('Worker response wait expired; environment truncated/cancelled')
        try:
            status, result = remote.recv()
        except EOFError as error:
            raise WorkerError('Worker exited without a response') from error
        if status == 'error':
            error = WorkerError(result)
            self.errors.append(error)
            raise error
        return result

    def _request(self, command, values):
        if self.closed:
            raise RuntimeError('VecEnv is closed')
        values = list(values)
        if len(values) != self.num_envs:
            raise ValueError('Expected one value per environment')
        for remote, data in zip(self.remotes, values):
            remote.send((command, data))
        replies = [self._receive(r) for r in self.remotes]
        self.last_infos = [info for _, info in replies]
        return tuple(map(np.stack, zip(*(result for result, _ in replies))))

    def step(self, actions, difficulty=1.0):
        """Return observations, reward, TDs, terminated and truncated arrays."""
        return self._request('step', ([action, difficulty] for action in actions))

    def reset(self, difficulty=1.0):
        return self._request('reset', [difficulty] * self.num_envs)

    def swap(self, agent):
        if self.closed:
            raise RuntimeError('VecEnv is closed')
        for remote in self.remotes:
            remote.send(('swap', agent))

    def close(self):
        if self.closed:
            return
        self.closed = True
        deadline = time.monotonic() + self.timeout
        new_errors = []
        try:
            for remote in self.remotes:
                try:
                    remote.send(('close', None))
                except (BrokenPipeError, EOFError, OSError):
                    pass
            for process in self.ps:
                process.join(timeout=max(0, deadline - time.monotonic()))
            for remote in self.remotes:
                while not remote.closed and remote.poll():
                    try:
                        status, result = remote.recv()
                    except (EOFError, OSError):
                        break
                    if status == 'error':
                        new_errors.append(WorkerError(result))
        finally:
            for remote in self.remotes:
                remote.close()
        self.errors.extend(new_errors)
        alive = [process.pid for process in self.ps if process.is_alive()]
        if alive:
            error = WorkerTimeoutError(f'Worker close wait expired; still running PIDs: {alive}')
            self.errors.append(error)
            raise error
        if new_errors:
            raise new_errors[0]

    @property
    def num_envs(self):
        return len(self.remotes)
