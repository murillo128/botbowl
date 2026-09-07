"""Audit real pytest collection: ordinary + dedicated must partition the full suite.

Invoked by both installed CI test profiles, without running test bodies. A missing
file, empty selection, lost case or overlapping route fails before test execution.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from run_ci_tests import SHARDS, shard_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--backend', choices=('python', 'native'), required=True)
    parser.add_argument('--rl', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Import routing from the same committed archive that supplies the test inputs.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.ci.run_profile import test_selections

    output = args.output.resolve()
    ordinary = test_selections('suite', args.rl)
    selections = [('full', ['tests'] + ([] if args.rl else ['--ignore=tests/ai/test_env.py']))]
    selections += ordinary + test_selections('investigation', args.rl)
    selections += [(name + '-shard-' + str(shard), selection + ['--shard', str(shard)])
                   for shard in SHARDS for name, selection in ordinary]
    collected = {}
    for name, selection in selections:
        target = output / ('collection-' + name + '.json')
        command = [sys.executable, str(Path(__file__).with_name('run_ci_tests.py').resolve()),
                   '--collected', str(target),
                   *selection, '--collect-only', '-q', '--require-pathfinding=' + args.backend]
        env = dict(os.environ)
        if name == 'full':
            # The reference must not share a global filter with every route.
            env.pop('PYTEST_ADDOPTS', None)
            command += ['-o', 'addopts=']
        with (output / ('collection-' + name + '.log')).open('w') as log:
            subprocess.run(command, cwd=args.suite, stdout=log, stderr=subprocess.STDOUT,
                           env=env, check=True, timeout=120)
        nodes = json.loads(target.read_text())
        assert len(nodes) == len(set(nodes)), 'Duplicate collection in ' + name
        collected[name] = set(nodes)

    full, unit, integration, investigation = (collected[name] for name in
                                             ('full', 'unit', 'integration', 'investigation'))
    # This explicit contract also catches a globally filtered collection and a
    # changed route constant. Plain pytest must continue to discover all 41 cases.
    required = {node for node in full if node.split('::')[0] ==
                'tests/framework/test_quick_snap_forward_model.py'}
    assert len(required) == 41, 'Full collection must retain all 41 investigation cases'
    assert len([node for node in required if '::test_quick_snap_advance_revert_forward_clean_equivalence['
                in node]) == 24, 'The complete 24-configuration matrix is required'
    assert investigation == required, 'Dedicated selection must cover exactly the investigation file'
    assert not (unit & integration or unit & investigation or integration & investigation), 'Overlapping routes'
    assert unit | integration | investigation == full, 'Routes omit or add tests versus full collection'
    for name, _ in ordinary:
        shards = [collected[name + '-shard-' + str(shard)] for shard in SHARDS]
        assert all(shards), 'Empty ordinary shard'
        assert not shards[0] & shards[1], 'Overlapping ordinary shards'
        assert shards[0] | shards[1] == collected[name], 'Ordinary shards lose or add identities'
        for shard, nodes in zip(SHARDS, shards):
            assert nodes == {node for node in collected[name] if shard_for(node) == shard}, \
                'Unstable shard assignment'
    report = {'backend': args.backend, 'rl': args.rl, 'selections': dict(selections),
              'counts': {name: len(nodes) for name, nodes in collected.items()},
              'disjoint': True, 'complete': True}
    (output / 'selection.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
