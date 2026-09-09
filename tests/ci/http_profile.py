"""Installed-only HTTP acceptance, selected explicitly by the lab CI profile."""
import os
from pathlib import Path
import subprocess
import sys


def test_installed_http_example_controls_and_closes_both_teams(tmp_path):
    helper = Path(__file__).resolve().parents[1] / 'lab/http_wheel_process.py'
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, helper], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'decisions=24 end_reason=decision_budget' in result.stdout
    assert 'both teams controlled; owned session released' in result.stdout
