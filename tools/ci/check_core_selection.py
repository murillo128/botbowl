"""Fast negative controls for the core gate's test-identity receipts."""
from copy import deepcopy
import hashlib
import json

from core_selection import INVESTIGATION, PARTS, partition, verify


def main():
    nodes = [f'tests/game/test_control.py::test_case[{i}]' for i in range(20)]
    nodes += [f'tests/ai/test_control.py::test_case[{i}]' for i in range(20)]
    other_modules = {'tests/lab/test_policies.py': 'unit',
                     'tests/lab/test_recording.py': 'unit',
                     'tests/framework/test_forward_model.py': 'integration',
                     'tests/framework/test_server.py': 'integration',
                     'tests/game/test_full_game.py': 'integration'}
    nodes += [path + '::test_control' for path in other_modules]
    nodes += [INVESTIGATION + '::test_evidence']
    unchanged_parts, investigation = partition(nodes)
    generator_nodes = ['tests/lab/test_generate.py::' + name for name in (
        'test_maximum_batch_plan_roundtrip_and_hash[False]',
        'test_maximum_batch_plan_roundtrip_and_hash[True]',
        'test_large_batch_generates_validates_and_replays',
        'test_large_valid_episode_hashes_validates_and_replays',
        'test_job_codec_keeps_small_file_bytes_and_per_entry_limits')]
    # Interleave identities to check order relative to other integration tests.
    nodes[3:3] = generator_nodes[:2]
    nodes[25:25] = generator_nodes[2:]
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
    ordinary = set(nodes) - set(investigation)
    assert coverage['ordinary'] == len(ordinary) and coverage['discovered'] == len(nodes)
    assert PARTS == ('unit-0', 'integration-0', 'unit-1', 'integration-1')
    generator_parts = {node: 'integration-' + str(
        int(hashlib.sha256(node.encode('utf-8')).hexdigest(), 16) % 2)
        for node in generator_nodes}
    assert set(generator_parts.values()) == {'integration-0', 'integration-1'}
    for node, expected in generator_parts.items():
        assert [part for part in PARTS if node in parts[part]] == [expected]
    for path, portion in other_modules.items():
        node = path + '::test_control'
        shard = int(hashlib.sha256(node.encode('utf-8')).hexdigest(), 16) % 2
        assert [part for part in PARTS if node in parts[part]] == [f'{portion}-{shard}']
    for part in PARTS:
        assert [node for node in parts[part] if node not in generator_parts] == unchanged_parts[part]
        assert parts[part] == [node for node in nodes
                               if node in unchanged_parts[part] or generator_parts.get(node) == part]
    assigned = [node for part in PARTS for node in parts[part]]
    assert len(assigned) == len(set(assigned)) and set(assigned) == ordinary

    # Former receipts contain the same complete union but put generator nodes
    # in unit-N. They must fail verification under the new classification.
    old_parts = {part: [node for node in nodes if node in unchanged_parts[part]
                       or (node in generator_parts
                           and generator_parts[node].replace('integration-', 'unit-') == part)]
                 for part in PARTS}
    old_assigned = [node for part in PARTS for node in old_parts[part]]
    assert len(old_assigned) == len(set(old_assigned)) and set(old_assigned) == ordinary

    def old_assignments(p, r):
        for part in PARTS:
            r[part].update(selected=old_parts[part][:], executed=old_parts[part][:])

    check('old generator unit assignments', old_assignments)
    generator = generator_nodes[0]
    assigned_part = generator_parts[generator]
    check('generator selection loss', lambda p, r: r[assigned_part]['selected'].remove(generator))
    check('generator execution loss', lambda p, r: r[assigned_part]['executed'].remove(generator))
    check('generator duplicate execution', lambda p, r: r[assigned_part]['executed'].append(generator))

    def wrong_generator_hash(p, r):
        other = 'integration-' + str(1 - int(assigned_part[-1]))
        for field in ('selected', 'executed'):
            r[assigned_part][field].remove(generator)
            r[other][field].append(generator)

    check('wrong generator hash shard', wrong_generator_hash)
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
