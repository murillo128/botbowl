"""Reproduce an injected local policy failure without changing any job inputs.

Run: python examples/lab_job_failure.py /tmp/botbowl-job-failure
The injection is trusted Python test code, never a policy loaded from a manifest.
"""
import argparse
from unittest.mock import patch

from botbowl.lab.generate import JobConfig
from botbowl.lab.jobs import generate, reproduce
from botbowl.lab.policies import ReferencePolicy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    args = parser.parse_args()
    original = ReferencePolicy.act

    def fail(policy, *inputs, **kwargs):
        calls = getattr(policy, '_example_calls', 0)
        if calls == 1:
            raise RuntimeError('Injected policy failure')
        policy._example_calls = calls + 1
        return original(policy, *inputs, **kwargs)

    with patch.object(ReferencePolicy, 'act', fail):
        try:
            generate(JobConfig(args.output, scenario='match', max_decisions=8))
        except RuntimeError:
            pass
        report = reproduce(args.output)
        assert report['status'] == 'reproduced', report
        print(report)
    # With the injection removed, disappearance is explicit, never success.
    report = reproduce(args.output)
    assert report['status'] == 'not_reproduced', report
    print(report)


if __name__ == '__main__':
    main()
