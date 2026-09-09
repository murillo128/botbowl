"""Small real-process controls for the core profile; run directly from lint."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/ci'))
from core_selection import INVESTIGATION, PARTS, partition
from run_profile import run_core_shards


def probe():
    """Act as a shard helper, including a barrier that requires all four peers."""
    options = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    output = Path(options['--output'])
    part = options['--part']
    plan = json.loads(Path(options['--plan']).read_text())
    (output.parent / (part + '.started')).write_text(str(os.getpid()))
    deadline = time.monotonic() + 5
    while len(list(output.parent.glob('*.started'))) != len(PARTS):
        if time.monotonic() >= deadline:
            sys.exit(90)
        time.sleep(0.01)
    mode = os.environ.get('CONTROL_MODE') if part == os.environ.get('CONTROL_PART') else None
    if mode == 'timeout':
        time.sleep(30)
    time.sleep(0.04 * (len(PARTS) - PARTS.index(part)))
    selected = partition(plan['collected'])[0][part]
    receipt = dict(plan, part=part, selected=selected[:], executed=selected[:])
    if mode == 'omission':
        receipt['executed'].pop()
    elif mode == 'overlap':
        receipt['selected'].append(partition(plan['collected'])[0][PARTS[(PARTS.index(part) + 1) % 4]][0])
    elif mode == 'failed-receipt':
        receipt['exit_code'] = 1
    if mode != 'missing-receipt':
        output.write_text(json.dumps(receipt))
    output.with_suffix('.xml').write_text('<testsuites><testsuite/></testsuites>')
    print(part, 'finished', flush=True)
    (output.parent / (part + '.finished')).write_text(str(time.monotonic()))
    sys.exit(7 if mode == 'failure' else 0)


class ParallelShards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        nodes = [f'tests/game/test_control.py::test_case[{i}]' for i in range(20)]
        nodes += [f'tests/ai/test_control.py::test_case[{i}]' for i in range(20)]
        nodes += [INVESTIGATION + '::test_evidence']
        self.plan = self.output / 'collection.json'
        self.plan.write_text(json.dumps(dict(source_revision='a' * 40, backend='python',
                                             python=sys.version, collected=nodes, exit_code=0)))
        self.result = dict(source_revision='a' * 40, backend='python', steps=[])
        self.processes = []
        self.logs = []
        self.popen = subprocess.Popen

    def launch(self, *args, **kwargs):
        process = self.popen(*args, **kwargs)
        self.processes.append(process)
        self.logs.append(kwargs['stdout'])
        return process

    def invoke(self, mode='', part='', launch=None):
        env = {**os.environ, 'CONTROL_MODE': mode, 'CONTROL_PART': part}
        with patch('run_profile.subprocess.Popen', side_effect=launch or self.launch), redirect_stdout(StringIO()):
            run_core_shards(sys.executable, Path(__file__).resolve(), self.plan, self.output,
                            self.output, env, self.result, timeout=2)

    def assert_reaped(self, count=4):
        self.assertEqual(len(self.processes), count)
        self.assertTrue(all(p.returncode is not None for p in self.processes))
        self.assertTrue(all(log.closed for log in self.logs))
        if os.name == 'posix':
            for process in self.processes:
                with self.assertRaises(ChildProcessError):
                    os.waitpid(process.pid, os.WNOHANG)
        self.assertEqual([s['name'] for s in self.result['steps']], list(PARTS[:count]))

    def test_success_is_concurrent_and_evidence_order_is_stable(self):
        self.invoke()
        self.assert_reaped()
        self.assertEqual(json.loads((self.output / 'coverage.json').read_text())['ordinary'], 40)
        self.assertEqual(len(list(self.output.glob('*.finished'))), 4)
        for part in PARTS:
            self.assertEqual((self.output / (part + '.log')).read_text().strip(), part + ' finished')
            self.assertTrue((self.output / (part + '.xml')).is_file())

    def check_rejection(self, mode, part):
        expected = {'failure': subprocess.CalledProcessError,
                    'timeout': subprocess.TimeoutExpired,
                    'missing-receipt': FileNotFoundError}.get(mode, ValueError)
        with self.assertRaises(expected) as caught:
            self.invoke(mode, part)
        self.assert_reaped()
        self.assertFalse((self.output / 'coverage.json').exists())
        for step in self.result['steps']:
            if step['name'] != part:
                self.assertEqual(step['exit_code'], 0)
                self.assertTrue((self.output / (step['name'] + '.finished')).exists())
        if mode == 'failure':
            self.assertEqual(caught.exception.returncode, 7)
        if mode == 'timeout':
            step = self.result['steps'][PARTS.index(part)]
            self.assertTrue(step['timed_out'])
            self.assertNotEqual(step['exit_code'], 0)

    def test_launch_error_reaps_started_siblings(self):
        def launch(*args, **kwargs):
            if len(self.processes) == 2:
                raise OSError('synthetic launch failure')
            return self.launch(*args, **kwargs)
        with self.assertRaisesRegex(OSError, 'synthetic launch failure'):
            self.invoke(launch=launch)
        self.assert_reaped(2)

    def test_interrupt_reaps_all_siblings(self):
        with patch('run_profile.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke()
        self.assert_reaped()

    def test_sigterm_reaps_all_siblings_and_restores_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with patch('run_profile.time.sleep', side_effect=lambda _: os.kill(os.getpid(), signal.SIGTERM)):
            with self.assertRaises(SystemExit) as caught:
                self.invoke()
        self.assertEqual(caught.exception.code, 128 + signal.SIGTERM)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assert_reaped()


for mode in ('failure', 'timeout', 'omission', 'overlap', 'missing-receipt', 'failed-receipt'):
    for part in PARTS:
        def check(self, mode=mode, part=part):
            self.check_rejection(mode, part)
        setattr(ParallelShards, 'test_' + mode.replace('-', '_') + '_' + part.replace('-', '_'), check)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        probe()
    else:
        unittest.main(verbosity=2)
