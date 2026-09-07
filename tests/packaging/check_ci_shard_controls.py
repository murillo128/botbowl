"""Fast adversarial controls for the artifact gate; synthetic receipts only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from run_ci_tests import SHARDS, shard_for
from verify_ci_shards import audit, read


def write(folder, name, value):
    (folder / (name + '.json')).write_text(json.dumps(value))


class CoverageControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        investigation = ['tests/framework/test_quick_snap_forward_model.py::case-' + str(i)
                         for i in range(41)]
        for backend in ('python', 'native'):
            self.make('investigation-' + backend, '3.11', backend, False, None,
                      'investigation', {'investigation': investigation})
        for python in ('3.11', '3.12', '3.13', '3.14'):
            for backend in ('python', 'native'):
                self.cell('core-' + python + '-' + backend, python, backend, False, investigation)
        for python in ('3.11', '3.12'):
            self.cell('rl-' + python, python, 'native', True, investigation)
        self.target = self.root / 'core-3.11-python-shard-0'

    def make(self, name, python, backend, rl, shard, profile, portions, collections=None):
        folder = self.root / name
        folder.mkdir()
        tests = {}
        for portion, nodes in portions.items():
            write(folder, 'selected-' + portion, nodes)
            write(folder, 'executed-' + portion, nodes)
            suffix = '-shard-' + str(shard) if shard is not None else ''
            write(folder, 'collection-' + portion + suffix, nodes)
            tests[portion] = dict(passed=len(nodes), failed=0, errors=0, skipped=0, xfailed=0)
        for portion, nodes in (collections or {}).items():
            write(folder, 'collection-' + portion, nodes)
        write(folder, 'result', dict(source_revision='target', python_version=python,
              backend=backend, rl=rl, shard=shard, profile=profile, complete=True,
              tests=tests, steps=[dict(name=name, exit_code=0) for name in portions]))

    def cell(self, name, python, backend, rl, investigation):
        ordinary = {name: [name + '::case-' + str(i) for i in range(20)]
                    for name in ('unit', 'integration')}
        collections = dict(ordinary, investigation=investigation,
                           full=ordinary['unit'] + ordinary['integration'] + investigation)
        for shard in SHARDS:
            portions = {name: [node for node in nodes if shard_for(node) == shard]
                        for name, nodes in ordinary.items()}
            self.make(name + '-shard-' + str(shard), python, backend, rl, shard,
                      'suite', portions, collections)

    def test_complete_union(self):
        self.assertEqual(len(audit(self.root, 'target')), 10)

    def test_missing_shard_and_cell_fail(self):
        shutil.rmtree(self.target)
        with self.assertRaises(FileNotFoundError):
            audit(self.root, 'target')
        shutil.rmtree(self.root / 'core-3.11-python-shard-1')
        with self.assertRaises(FileNotFoundError):
            audit(self.root, 'target')

    def test_missing_dedicated_backend_fails(self):
        shutil.rmtree(self.root / 'investigation-native')
        with self.assertRaises(FileNotFoundError):
            audit(self.root, 'target')

    def test_loss_overlap_and_duplicate_fail(self):
        original = read(self.target, 'executed-unit')
        for mutated in (original[:-1], original + ['foreign::case'], original + original[:1]):
            with self.subTest(mutated=mutated):
                write(self.target, 'executed-unit', mutated)
                with self.assertRaises(AssertionError):
                    audit(self.root, 'target')

    def test_wrong_assignment_fails(self):
        write(self.target, 'collection-unit-shard-0', read(self.target, 'collection-unit'))
        with self.assertRaises(AssertionError):
            audit(self.root, 'target')

    def test_stale_partial_failed_and_wrong_cell_fail(self):
        original = read(self.target, 'result')
        for mutation in (dict(source_revision='stale'), dict(complete=False), dict(shard=1),
                         dict(steps=[dict(name='unit', exit_code=1)])):
            with self.subTest(mutation=mutation):
                write(self.target, 'result', dict(original, **mutation))
                with self.assertRaises(AssertionError):
                    audit(self.root, 'target')

    def test_global_collection_loss_fails(self):
        # Losing an ordinary case while retaining all 41 investigation cases
        # still fails against the unfiltered full reference.
        for shard in SHARDS:
            folder = self.root / ('core-3.11-python-shard-' + str(shard))
            write(folder, 'collection-unit', read(folder, 'collection-unit')[:-1])
        with self.assertRaises(AssertionError):
            audit(self.root, 'target')

    def test_assignment_stable_across_order_and_hash_seed(self):
        nodes = ['tests/a.py::case[' + str(i) + ']' for i in range(100)]
        expected = {node: shard_for(node) for node in nodes}
        self.assertEqual(expected, {node: shard_for(node) for node in reversed(nodes)})
        for seed in ('1', '917'):
            code = ('import json; from run_ci_tests import shard_for; '
                    'print(json.dumps({node: shard_for(node) for node in ' + repr(nodes) + '}))')
            actual = subprocess.check_output([sys.executable, '-c', code],
                cwd=Path(__file__).resolve().parent, env=dict(os.environ, PYTHONHASHSEED=seed), text=True)
            self.assertEqual(expected, json.loads(actual))


if __name__ == '__main__':
    unittest.main(verbosity=2)
