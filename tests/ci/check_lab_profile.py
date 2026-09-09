"""Negative controls for mandatory lab receipts and safe failure reporting."""
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/ci'))
from lab_profile import complete
from run_profile import run_shards


class LabControls(unittest.TestCase):
    def test_missing_skipped_duplicate_and_partial_execution_fail(self):
        self.assertTrue(complete(dict(collected=['a', 'b'], executed=['a', 'b'], skips=[])))
        for receipt in (
            dict(collected=[], executed=[], skips=[]),
            dict(collected=['a'], executed=['a'], skips=['missing dependency']),
            dict(collected=['a', 'b'], executed=['a'], skips=[]),
            dict(collected=['a', 'a'], executed=['a', 'a'], skips=[]),
            dict(collected=['a'], executed=['foreign'], skips=[]),
        ):
            with self.subTest(receipt=receipt):
                self.assertFalse(complete(receipt))

    def test_failure_is_observable_without_publishing_private_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            steps = []
            public_log = StringIO()
            with redirect_stdout(public_log), self.assertRaises(subprocess.CalledProcessError):
                run_shards([('control', [sys.executable, '-c',
                            'print("PRIVATE_RNG_SENTINEL"); raise RuntimeError("control")'])],
                           output, output, dict(os.environ), steps, show_failure_logs=False)
            self.assertNotIn('PRIVATE_RNG_SENTINEL', public_log.getvalue())
            self.assertIn('PRIVATE_RNG_SENTINEL', (output / 'control.log').read_text())
            self.assertNotEqual(steps[0]['exit_code'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
