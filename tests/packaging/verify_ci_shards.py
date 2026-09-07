"""Require successful exact-head receipts for every ordinary cell and investigation."""
import argparse
import json
from pathlib import Path

from run_ci_tests import SHARDS, shard_for


def read(folder, name):
    return json.loads((folder / (name + '.json')).read_text())


def identities(folder, name):
    nodes = read(folder, name)
    assert len(nodes) == len(set(nodes)), 'Duplicate identities: ' + str(folder / name)
    return set(nodes)


def receipt(folder, revision, python, backend, rl, shard, profile):
    result = read(folder, 'result')
    assert result['source_revision'] == revision, 'Wrong source revision: ' + str(folder)
    assert (result['python_version'], result['backend'], result['rl'], result['shard'], result['profile']) == \
        (python, backend, rl, shard, profile), 'Wrong cell identity: ' + str(folder)
    assert result['complete'] and result['steps'], 'Incomplete profile: ' + str(folder)
    assert all(step['exit_code'] == 0 for step in result['steps']), 'Failed step: ' + str(folder)
    return result


def executed(folder, portion, expected, result):
    assert identities(folder, 'selected-' + portion) == expected, 'Selected identity loss or overlap'
    assert identities(folder, 'executed-' + portion) == expected, 'Incomplete execution identities'
    counts = result['tests'][portion]
    assert counts['failed'] == counts['errors'] == 0, 'Failed tests'
    assert counts['passed'] + counts['skipped'] + counts['xfailed'] == len(expected), 'JUnit count mismatch'
    assert any(step['name'] == portion for step in result['steps']), 'Missing test step'


def audit(artifacts, revision):
    dedicated = {}
    for backend in ('python', 'native'):
        folder = artifacts / ('investigation-' + backend)
        result = receipt(folder, revision, '3.11', backend, False, None, 'investigation')
        nodes = identities(folder, 'collection-investigation')
        assert len(nodes) == 41 and all(node.startswith(
            'tests/framework/test_quick_snap_forward_model.py::') for node in nodes), 'Investigation loss'
        executed(folder, 'investigation', nodes, result)
        assert result['tests']['investigation']['passed'] == 41, 'Investigation must pass all cases'
        dedicated[backend] = nodes
    assert dedicated['python'] == dedicated['native'], 'Investigation backend identity mismatch'
    # Independent of matrix expansion: a missing shard or entire runtime cell
    # must fail even if all jobs that happened to be scheduled were green.
    cells = [('core-' + python + '-' + backend, python, backend, False)
             for python in ('3.11', '3.12', '3.13', '3.14') for backend in ('python', 'native')]
    cells += [('rl-' + python, python, 'native', True) for python in ('3.11', '3.12')]
    report = {}
    for cell, python, backend, rl in cells:
        reference = None
        union = set()
        counts = []
        for shard in SHARDS:
            folder = artifacts / (cell + '-shard-' + str(shard))
            result = receipt(folder, revision, python, backend, rl, shard, 'suite')
            collections = {name: identities(folder, 'collection-' + name)
                           for name in ('full', 'unit', 'integration', 'investigation')}
            if reference is None:
                reference = collections
            assert collections == reference, 'Shard reference collections differ: ' + cell
            assert collections['investigation'] == dedicated[backend], 'Dedicated ownership mismatch'
            unit, integration, investigation = (collections[name] for name in
                                                 ('unit', 'integration', 'investigation'))
            assert not (unit & integration or unit & investigation or integration & investigation), 'Route overlap'
            assert unit | integration | investigation == collections['full'], 'Full union mismatch'
            selected = set()
            for portion in ('unit', 'integration'):
                expected = {node for node in collections[portion] if shard_for(node) == shard}
                assert expected, 'Empty shard portion'
                assert identities(folder, 'collection-' + portion + '-shard-' + str(shard)) == expected, \
                    'Shard assignment mismatch'
                executed(folder, portion, expected, result)
                selected |= expected
            assert not union & selected, 'Shard overlap'
            union |= selected
            counts.append(len(selected))
        assert union | dedicated[backend] == reference['full'], 'Missing ordinary coverage: ' + cell
        report[cell] = {'ordinary_shards': counts, 'investigation': len(dedicated[backend]),
                        'full': len(reference['full']), 'complete': True, 'disjoint': True}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.artifacts, args.revision)
    args.output.write_text(json.dumps({'source_revision': args.revision, 'cells': report}, indent=2) + '\n')
    print(args.output.read_text())


if __name__ == '__main__':
    main()
