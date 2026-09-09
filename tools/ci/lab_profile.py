"""Installed M0–M1 contract selections; missing dependencies/tests fail closed."""
import argparse
import importlib
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import sys


FAST = [
    'tests/lab/test_session.py',
    'tests/lab/test_observations.py::test_private_and_future_sentinels_never_appear_at_any_serialized_depth',
    'tests/lab/test_recording.py::test_rejections_are_operational_and_do_not_advance_game',
    'tests/lab/test_recording.py::test_leakage_canaries_selective_read_and_mutation_safety',
    'tests/lab/test_generate.py::test_reproducible_plan_and_supplied_actions',
    'tests/lab/test_generate.py::test_replay_rejects_wrong_length_and_outcome_tampering',
    'tests/lab/test_snapshot_io.py::test_process_a_to_b_five_sizes[False-fresh-3]',
    'tests/lab/test_snapshots.py::test_corrupt_snapshot_rejected_atomically',
    'tests/lab/test_windows.py',
    'tests/lab/test_splits.py',
    'tests/lab/test_external_client.py',
    'tests/lab/test_replays.py',
    'tests/lab/test_commands.py',
]
SELECTIONS = {
    'lab-fast': FAST,
    'lab-extended': ['tests/issue25/broad_corpus.py', 'tests/lab/test_snapshot_io.py',
                     'tests/lab/test_generate.py', 'tests/lab/test_recording.py'],
    'lab-adapters': ['tests/ai/test_gymnasium_env.py', 'tests/lab/test_pettingzoo_aec.py'],
}


def complete(receipt):
    return (bool(receipt['collected']) and not receipt['skips'] and
            receipt['executed'] == receipt['collected'] and
            len(receipt['collected']) == len(set(receipt['collected'])))


def main():
    import pytest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', choices=SELECTIONS)
    parser.add_argument('--backend', choices=('python', 'native'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ.pop('PYTEST_ADDOPTS', None)
    # Required imports precede collection: importorskip in optional owner tests
    # must never turn an incorrectly installed mandatory cell green.
    modules = ['botbowl.lab.dataset_client', 'botbowl.lab.replays', 'botbowl.lab.commands']
    if args.profile == 'lab-adapters':
        modules += ['gymnasium', 'pettingzoo']
    for module in modules:
        importlib.import_module(module)
    import botbowl
    if 'site-packages' not in Path(botbowl.__file__).parts:
        raise RuntimeError('Lab profiles require an installed wheel outside checkout')
    receipt = {
        'profile': args.profile, 'backend': args.backend, 'python': sys.version,
        'versions': {d.metadata['Name']: d.version for d in distributions()},
        'selection': SELECTIONS[args.profile], 'collected': [], 'executed': [],
        'skips': [], 'failed': [], 'exit_code': None,
        'capabilities': {'m0-m1': 'required: lab-fast', 'replays-42': 'required: lab-fast',
                         'adapters-57': 'required: lab-adapters',
                         'local-59': 'required: lab-fast',
                         'remote-60': 'unavailable; not tested',
                         'viewer-51': 'unavailable; not tested'},
        'fixture_plan': {'m1_seed': 17, 'm1_episodes': 100, 'm1_size': 3,
                         'm1_max_decisions': 8, 'm1_max_steps': 1000,
                         'broad_seeds': [0, 3, 17], 'sizes': [1, 3, 5, 7, 11]},
    }

    class Receipt:
        def pytest_collection_modifyitems(self, items):
            receipt['collected'] = [item.nodeid for item in items]

        def pytest_collectreport(self, report):
            if report.failed:
                receipt['failed'].append({'nodeid': report.nodeid, 'phase': 'collection'})
            if report.skipped:
                receipt['skips'].append(str(report.longrepr))

        def pytest_runtest_logreport(self, report):
            if report.failed:
                receipt['failed'].append({'nodeid': report.nodeid, 'phase': report.when})
            if report.skipped:
                receipt['skips'].append(str(report.longrepr))
            if report.when == 'teardown':
                receipt['executed'].append(report.nodeid)

    try:
        code = int(pytest.main([
            *SELECTIONS[args.profile], '--require-pathfinding=' + args.backend,
            '--basetemp=' + str(args.output.parent / 'scratch'),
            '-o', 'addopts=', '-q', '-ra', '--junitxml=' + str(args.output.with_suffix('.xml')),
        ], plugins=[Receipt()]))
        if not complete(receipt):
            code = code or 1
        receipt['exit_code'] = code
    finally:
        args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    return code


if __name__ == '__main__':
    sys.exit(main())
