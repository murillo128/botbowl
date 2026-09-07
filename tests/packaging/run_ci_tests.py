"""CI-only pytest entry point: stable identity sharding and execution receipts."""
import argparse
import hashlib
import json
from pathlib import Path


SHARDS = (0, 1)


def shard_for(nodeid):
    # Independent of collection order, runtime, backend and Python's hash seed.
    return int(hashlib.sha256(nodeid.encode('utf-8')).hexdigest(), 16) % len(SHARDS)


def main():
    import pytest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', type=int, choices=SHARDS)
    parser.add_argument('--expected', type=Path)
    parser.add_argument('--collected', type=Path, required=True)
    parser.add_argument('--executed', type=Path)
    args, options = parser.parse_known_args()

    class Receipt:
        def __init__(self):
            self.completed = []

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, config, items):
            if args.shard is not None:
                selected = [item for item in items if shard_for(item.nodeid) == args.shard]
                deselected = [item for item in items if shard_for(item.nodeid) != args.shard]
                config.hook.pytest_deselected(items=deselected)
                items[:] = selected

        def pytest_collection_finish(self, session):
            nodes = [item.nodeid for item in session.items]
            args.collected.write_text(json.dumps(nodes, indent=2) + '\n')
            if args.expected is not None and nodes != json.loads(args.expected.read_text()):
                raise pytest.UsageError('Execution collection differs from audited identities')

        def pytest_runtest_logreport(self, report):
            if report.when == 'teardown':
                self.completed.append(report.nodeid)

        def pytest_sessionfinish(self, session, exitstatus):
            if args.executed is not None:
                args.executed.write_text(json.dumps(self.completed, indent=2) + '\n')

    raise SystemExit(pytest.main(options, plugins=[Receipt()]))


if __name__ == '__main__':
    main()
