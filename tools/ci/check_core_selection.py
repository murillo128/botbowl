"""Fast negative controls for the core gate's test-identity receipts."""
from copy import deepcopy
import json

from core_selection import INVESTIGATION, PARTS, partition, verify


def main():
    nodes = [f'tests/game/test_control.py::test_case[{i}]' for i in range(20)]
    nodes += [f'tests/ai/test_control.py::test_case[{i}]' for i in range(20)]
    nodes += [INVESTIGATION + '::test_evidence']
    plan = dict(source_revision='a' * 40, backend='python', python='3.11',
                collected=nodes, exit_code=0)
    parts, _ = partition(nodes)
    receipts = {part: dict(plan, part=part, selected=selected, executed=selected[:])
                for part, selected in parts.items()}
    passed = []

    def check(name, mutate):
        p, r = deepcopy(plan), deepcopy(receipts)
        mutate(p, r)
        try:
            verify(p, r, plan['source_revision'], 'python')
        except (ValueError, KeyError):
            passed.append(name)
        else:
            raise AssertionError('Accepted negative control: ' + name)

    coverage = verify(plan, receipts, plan['source_revision'], 'python')
    assert coverage['ordinary'] == 40 and coverage['discovered'] == 41
    # Stable membership despite collection order changes; retain each local order.
    reversed_parts = partition(list(reversed(nodes)))[0]
    assert all(reversed_parts[p] == list(reversed(parts[p])) for p in PARTS)
    check('missing shard', lambda p, r: [r.pop(k) for k in PARTS if k.endswith('-1')])
    check('missing portion', lambda p, r: r.pop('unit-0'))
    check('selection loss', lambda p, r: r['unit-0']['selected'].pop())
    check('overlap', lambda p, r: r['unit-1']['selected'].append(parts['unit-0'][0]))
    check('execution loss', lambda p, r: r['unit-0']['executed'].pop())
    check('duplicate execution', lambda p, r: r['unit-0']['executed'].append(parts['unit-0'][0]))
    check('stale head', lambda p, r: r['unit-0'].update(source_revision='b' * 40))
    check('stale collection', lambda p, r: p.update(source_revision='b' * 40))
    check('wrong backend', lambda p, r: r['unit-0'].update(backend='native'))
    check('wrong interpreter', lambda p, r: r['unit-0'].update(python='3.12'))
    check('failed execution', lambda p, r: r['unit-0'].update(exit_code=1))
    check('unfinished execution', lambda p, r: r['unit-0'].update(exit_code=None))
    check('failed collection', lambda p, r: p.update(exit_code=2))
    check('collection loss', lambda p, r: r['unit-0']['collected'].pop())
    check('duplicate collection', lambda p, r: p['collected'].append(nodes[0]))
    check('wrong portion', lambda p, r: r['unit-0'].update(part='unit-1'))
    check('investigation duplication', lambda p, r: r['unit-0']['selected'].append(nodes[-1]))
    # A missing investigation file must not turn into a silent policy exclusion.
    try:
        partition(nodes[:-1])
    except ValueError:
        passed.append('missing investigation')
    else:
        raise AssertionError('Accepted missing investigation')
    print(json.dumps({'positive_union': coverage, 'negative_controls': passed}, indent=2))


if __name__ == '__main__':
    main()
