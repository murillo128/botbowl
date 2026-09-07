"""Audit real pytest collection: ordinary + dedicated must partition the full suite.

Invoked by both installed CI test profiles, without running test bodies. A missing
file, empty selection, lost case or overlapping route fails before test execution.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    if sys.argv[1:2] == ['--collect']:
        import pytest

        class Capture:
            def pytest_collection_finish(self, session):
                Path(sys.argv[2]).write_text(json.dumps([item.nodeid for item in session.items]))

        raise SystemExit(pytest.main(sys.argv[3:], plugins=[Capture()]))

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
    selections = [('full', ['tests'] + ([] if args.rl else ['--ignore=tests/ai/test_env.py']))]
    selections += test_selections('suite', args.rl) + test_selections('investigation', args.rl)
    collected = {}
    for name, selection in selections:
        target = output / ('collection-' + name + '.json')
        command = [sys.executable, str(Path(__file__).resolve()), '--collect', str(target),
                   *selection, '--collect-only', '-q', '--require-pathfinding=' + args.backend]
        with (output / ('collection-' + name + '.log')).open('w') as log:
            subprocess.run(command, cwd=args.suite, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=120)
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
    report = {'backend': args.backend, 'rl': args.rl, 'selections': dict(selections),
              'counts': {name: len(nodes) for name, nodes in collected.items()},
              'disjoint': True, 'complete': True}
    (output / 'selection.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
