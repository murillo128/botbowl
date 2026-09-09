"""CI-only core partitioning and execution receipts; normal pytest is unchanged."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


INVESTIGATION = 'tests/framework/test_quick_snap_forward_model.py'
LEGACY_RL = 'tests/ai/test_env.py'
INTEGRATION = ('tests/ai/', 'tests/framework/test_forward_model.py',
               'tests/framework/test_server.py', 'tests/game/test_full_game.py')
PARTS = tuple(f'{portion}-{shard}' for shard in range(2)
              for portion in ('unit', 'integration'))


def unique(nodes):
    if len(nodes) != len(set(nodes)):
        raise ValueError('Duplicate test identities')
    return set(nodes)


def partition(nodes):
    """Preserve collection order within two stable SHA-256 shards per portion."""
    unique(nodes)
    parts = {part: [] for part in PARTS}
    investigation = []
    for node in nodes:
        path = node.split('::', 1)[0]
        if path == LEGACY_RL:
            raise ValueError('Legacy RL is outside the core profile')
        if path == INVESTIGATION:
            investigation.append(node)
            continue
        portion = 'integration' if path.startswith(INTEGRATION) else 'unit'
        shard = int(hashlib.sha256(node.encode('utf-8')).hexdigest(), 16) % 2
        parts[f'{portion}-{shard}'].append(node)
    if not investigation or not all(parts.values()):
        raise ValueError('Missing investigation or ordinary test portion')
    return parts, investigation


def verify(plan, receipts, revision, backend):
    """Fail closed on missing, stale, overlapping, partial or failed execution."""
    if plan['source_revision'] != revision or plan['backend'] != backend:
        raise ValueError('Stale head or wrong backend in collection')
    if plan['exit_code'] != 0 or set(receipts) != set(PARTS):
        raise ValueError('Failed collection or missing shard/portion')
    parts, investigation = partition(plan['collected'])
    executed = []
    for part in PARTS:
        receipt = receipts[part]
        for key in ('source_revision', 'backend', 'python', 'collected'):
            if receipt[key] != plan[key]:
                raise ValueError(f'{part}: mismatched {key}')
        if receipt['part'] != part or receipt['exit_code'] != 0:
            raise ValueError(f'{part}: wrong portion or failed execution')
        if receipt['selected'] != parts[part]:
            raise ValueError(f'{part}: selection loss, overlap or wrong assignment')
        if unique(receipt['executed']) != unique(parts[part]):
            raise ValueError(f'{part}: incomplete execution')
        executed.extend(receipt['executed'])
    if unique(executed).intersection(investigation):
        raise ValueError('Investigation duplicated in ordinary core')
    if unique(executed + investigation) != unique(plan['collected']):
        raise ValueError('Incomplete discovery coverage')
    return {'source_revision': revision, 'backend': backend, 'python': plan['python'],
            'discovered': len(plan['collected']), 'ordinary': len(executed),
            'investigation': investigation,
            'parts': {part: len(nodes) for part, nodes in parts.items()}}


def main():
    import pytest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('collect', 'run'))
    parser.add_argument('--backend', choices=('python', 'native'), required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--part', choices=PARTS)
    args = parser.parse_args()
    if args.mode == 'run' and (args.plan is None or args.part is None):
        parser.error('run requires --plan and --part')
    plan = json.loads(args.plan.read_text()) if args.mode == 'run' else None
    receipt = dict(source_revision=args.revision, backend=args.backend,
                   python=sys.version, part=args.part, collected=[], selected=[], executed=[],
                   failed=[], exit_code=None)

    class Receipt:
        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, session, config, items):
            nodes = [item.nodeid for item in items]
            unique(nodes)
            receipt['collected'] = nodes
            if plan is None:
                partition(nodes)
                return
            for key in ('source_revision', 'backend', 'python', 'collected'):
                if receipt[key] != plan[key]:
                    raise pytest.UsageError(f'Collection identity mismatch: {key}')
            selected = partition(nodes)[0][args.part]
            selected_set = set(selected)
            config.hook.pytest_deselected(items=[i for i in items if i.nodeid not in selected_set])
            items[:] = [i for i in items if i.nodeid in selected_set]
            receipt['selected'] = selected

        def pytest_collectreport(self, report):
            if report.failed:
                receipt['failed'].append({'nodeid': report.nodeid, 'phase': 'collection'})

        def pytest_runtest_logreport(self, report):
            if report.failed:
                receipt['failed'].append({'nodeid': report.nodeid, 'phase': report.when})
            # Teardown receipts include legitimate capability skips/xfails. The
            # pytest exit code still rejects failed setup, call and teardown.
            if report.when == 'teardown':
                receipt['executed'].append(report.nodeid)

    # A shared user/global filter must never shrink both the reference and shards.
    os.environ.pop('PYTEST_ADDOPTS', None)
    options = ['tests', '--ignore=' + LEGACY_RL, '--require-pathfinding=' + args.backend,
               '-o', 'addopts=', '-q', '-ra']
    if args.mode == 'collect':
        options.append('--collect-only')
    else:
        options.append('--junitxml=' + str(args.output.with_suffix('.xml')))
    try:
        receipt['exit_code'] = int(pytest.main(options, plugins=[Receipt()]))
    finally:
        args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt['exit_code']


if __name__ == '__main__':
    sys.exit(main())
