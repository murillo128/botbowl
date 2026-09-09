"""Fast, dependency-free regression checks for push/PR ownership and runner safety."""
from pathlib import Path
import re
import unittest


WORKFLOW = (Path(__file__).resolve().parents[2] /
            '.github/workflows/pythonpackage.yml').read_text()
JOBS = dict(re.findall(r'^  (lint|core|lab):\n(.*?)(?=^  \w+:|\Z)',
                       WORKFLOW, flags=re.M | re.S))
REPOSITORY = 'murillo128/botbowl'


def field(job, key):
    match = re.search(r'^    ' + re.escape(key) + r': (.+)$', JOBS[job], re.M)
    if match is None:
        raise AssertionError(f'Missing {job}.{key}')
    return match.group(1)


def evaluate(expression, event, ref, head_repo, head_ref):
    # Only the small boolean/string subset used in this workflow is translated.
    # Replace operators before inserting repr-escaped fixture values, never shell
    # interpolation or repository execution. GitHub startsWith ignores case.
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', 'not ', expression)
    context = {'github.event_name': event, 'github.ref': ref,
               'github.repository': REPOSITORY, 'github.head_ref': head_ref,
               'github.event.pull_request.head.repo.full_name': head_repo}
    expression = re.sub(r'github\.[a-z_.]+', lambda match: repr(context[match.group()]),
                        expression)
    return eval(expression, {'__builtins__': {},
                             'startsWith': lambda value, prefix:
                             value.lower().startswith(prefix.lower())})


# label, event, ref, head repository, head branch, hosted jobs, local core
CASES = [
    ('main push', 'push', 'refs/heads/main', '', '', True, True),
    ('issue push', 'push', 'refs/heads/codex/issue-56', '', '', True, True),
    ('epic push', 'push', 'refs/heads/codex/epic-issue-3', '', '', True, True),
    ('internal issue PR', 'pull_request', 'refs/pull/145/merge', REPOSITORY,
     'codex/issue-56', False, False),
    ('internal epic PR', 'pull_request', 'refs/pull/3/merge', REPOSITORY,
     'codex/epic-issue-3', False, False),
    ('internal main PR', 'pull_request', 'refs/pull/1/merge', REPOSITORY,
     'main', False, False),
    ('fork mimics codex', 'pull_request', 'refs/pull/2/merge', 'contributor/botbowl',
     'codex/issue-56', True, False),
    ('fork main', 'pull_request', 'refs/pull/2/merge', 'contributor/botbowl',
     'main', True, False),
    ('fork feature', 'pull_request', 'refs/pull/2/merge', 'contributor/botbowl',
     'feature/fix', True, False),
    ('internal non-push branch', 'pull_request', 'refs/pull/4/merge', REPOSITORY,
     'feature/fix', True, False),
    ('not a codex prefix', 'pull_request', 'refs/pull/4/merge', REPOSITORY,
     'codex-fix', True, False),
    ('no privileged PR event', 'pull_request_target', 'refs/heads/main',
     'contributor/botbowl', 'codex/issue-56', False, False),
]


class TriggerPolicyTests(unittest.TestCase):
    def test_trigger_scope_and_separate_cancellation(self):
        triggers = WORKFLOW.split('on:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(triggers.strip(),
                         "pull_request:\n  push:\n    branches:\n      - main\n      - 'codex/**'")
        self.assertIn('group: tests-${{ github.event.pull_request.number || github.ref }}', WORKFLOW)
        self.assertIn('cancel-in-progress: true', WORKFLOW)
        self.assertIn('contents: read', WORKFLOW)
        self.assertNotIn('pull_request_target:', triggers)

    def test_event_ownership_and_local_runner_boundary(self):
        for label, event, ref, head_repo, head_ref, hosted, core in CASES:
            for job, expected in (('lint', hosted), ('lab', hosted), ('core', core)):
                with self.subTest(case=label, job=job):
                    self.assertEqual(evaluate(field(job, 'if'), event, ref,
                                              head_repo, head_ref), expected)
        self.assertEqual(field('core', 'runs-on'), '[self-hosted, codex]')
        for job in ('lint', 'lab'):
            self.assertEqual(field(job, 'runs-on'), 'ubuntu-24.04')

    def test_skipped_duplicates_cannot_reuse_authoritative_check_names(self):
        for label, event, ref, head_repo, head_ref, hosted, _ in CASES:
            for job in ('lint', 'lab'):
                with self.subTest(case=label, job=job):
                    expression = re.search(r'\$\{\{ (.*?) \}\}', field(job, 'name')).group(1)
                    prefix = evaluate(expression, event, ref, head_repo, head_ref)
                    duplicate = event == 'pull_request' and not hosted
                    self.assertEqual(prefix, 'duplicate / ' + job if duplicate else job)

    def test_regression_checks_run_in_lint(self):
        self.assertIn('run: python tests/ci/check_trigger_policy.py', JOBS['lint'])


if __name__ == '__main__':
    unittest.main()
