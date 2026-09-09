"""Run CI against fresh committed sources and installed artifacts, outside checkout."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET


def run_shards(commands, output, cwd, env, steps, timeout=1200, show_failure_logs=True):
    """Run independent shards together, then report in declared order and fail closed."""
    running = []

    def kill(process):
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        with ExitStack() as logs:
            try:
                for name, command in commands:
                    log = logs.enter_context((output / (name + '.log')).open('w'))
                    before = time.monotonic()
                    process = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                                               stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=os.name == 'posix')
                    running.append((process, command, before, {'name': name}))
                pending = list(running)
                while pending:
                    for item in pending[:]:
                        process, command, before, step = item
                        code = process.poll()
                        if code is None and time.monotonic() - before >= timeout:
                            step['timed_out'] = True
                            kill(process)
                            code = process.wait()
                        if code is not None:
                            step.update(seconds=round(time.monotonic() - before, 2), exit_code=code)
                            pending.remove(item)
                    if pending:
                        time.sleep(0.05)
            finally:
                # Also clean up siblings on launch errors, Ctrl-C or SIGTERM.
                # Separate POSIX sessions keep timeout cleanup off other profiles.
                for process, _, _, _ in running:
                    kill(process)
                for process, _, before, step in running:
                    code = process.wait()
                    if 'exit_code' not in step:
                        step.update(seconds=round(time.monotonic() - before, 2), exit_code=code)
                    steps.append(step)
                    print(step['name'], code, flush=True)
    finally:
        signal.signal(signal.SIGTERM, previous)
    failure = None
    for _, command, _, step in running:
        if step.get('timed_out'):
            failure = subprocess.TimeoutExpired(command, timeout)
        elif step['exit_code']:
            if show_failure_logs:
                print((output / (step['name'] + '.log')).read_text()[-12000:], flush=True)
            failure = subprocess.CalledProcessError(step['exit_code'], command)
    if failure:
        raise failure


def run_core_shards(python, helper, plan, output, suite, env, result, timeout=1200):
    from core_selection import PARTS, verify

    common = ['--backend', result['backend'], '--revision', result['source_revision']]
    commands = [(part, [python, helper, 'run', *common, '--plan', plan,
                        '--part', part, '--output', output / (part + '.json')])
                for part in PARTS]
    run_shards(commands, output, suite, env, result['steps'], timeout, show_failure_logs=False)
    coverage = verify(json.loads(plan.read_text()),
                      {part: json.loads((output / (part + '.json')).read_text())
                       for part in PARTS}, result['source_revision'], result['backend'])
    (output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', choices=('suite', 'artifacts', 'extra',
                                            'lab-fast', 'lab-extended', 'lab-adapters'))
    parser.add_argument('--backend', choices=('python', 'native'), default='python')
    parser.add_argument('--extra', choices=('web', 'rl', 'gymnasium', 'multiagent', 'competition', 'dev', 'render'))
    parser.add_argument('--rl', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'DISPLAY'):
        env.pop(key, None)
    constraints = root / 'requirements' / ('rl.txt' if args.rl or args.extra == 'rl' else 'core.txt')
    env['PIP_CONSTRAINT'] = str(constraints)
    env['PIP_BUILD_CONSTRAINT'] = str(constraints)
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    env['MPLBACKEND'] = 'Agg'
    result = {'profile': args.profile, 'backend': args.backend, 'extra': args.extra,
              'rl': args.rl, 'python': sys.version, 'steps': []}
    start = time.monotonic()

    def run(name, command, cwd=output, process_env=env):
        if args.profile.startswith('lab-'):
            run_shards([(name, command)], output, cwd, process_env, result['steps'],
                       show_failure_logs=False)
            return
        before = time.monotonic()
        with (output / (name + '.log')).open('w') as log:
            completed = subprocess.run(list(map(str, command)), cwd=cwd, env=process_env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        result['steps'].append({'name': name, 'seconds': round(time.monotonic() - before, 2),
                                'exit_code': completed.returncode})
        print(name, completed.returncode, flush=True)
        if completed.returncode:
            if args.profile != 'suite':
                print((output / (name + '.log')).read_text()[-12000:], flush=True)
            raise subprocess.CalledProcessError(completed.returncode, command)

    def venv(name):
        target = output / name
        run(name, [sys.executable, '-m', 'venv', target])
        python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        # CPython 3.11 ensurepip can seed old setuptools independently of build
        # isolation. Bring installer tools under the same audited constraints.
        run(name + '-bootstrap', [python, '-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools'])
        return python

    try:
        result['source_revision'] = subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        source = output / 'source'
        source.mkdir()
        archive = output / 'source.tar'
        run('export', ['git', '-C', root, 'archive', 'HEAD', '-o', archive])
        with tarfile.open(archive) as members:
            members.extractall(source, filter='data')
        if args.profile == 'artifacts':
            run('fresh-sdist', [sys.executable, root / 'tests/packaging/verify_sdist.py',
                               '--source', root, '--output', output / 'sdist'])
            # verify_sdist covers native wheel from a fresh Python sdist. Also install
            # the Python wheel and archive independently into minimal environments.
            artifacts = [next((output / 'sdist/python').glob('*.whl')),
                         next((output / 'sdist/python').glob('*.tar.gz'))]
            for index, artifact in enumerate(artifacts):
                python = venv('minimal-' + str(index))
                run('install-' + str(index), [python, '-m', 'pip', 'install', artifact],
                    process_env={**env, 'BOTBOWL_BUILD_NATIVE': '0'})
                run('check-' + str(index), [python, '-m', 'pip', 'check'])
                run('smoke-' + str(index), [python, source / 'tests/packaging/smoke.py', '--minimal'])
                for example in ('public_external', 'public_policy'):
                    run(example + '-' + str(index),
                        [python, source / ('examples/' + example + '.py'), '--max-steps', '100'])
                run('freeze-' + str(index), [python, '-m', 'pip', 'freeze', '--all'])
            return
        run('build', [sys.executable, '-m', 'build', '--wheel', '--outdir', output / 'dist'], source,
            {**env, 'BOTBOWL_BUILD_NATIVE': '1' if args.backend == 'native' else '0'})
        wheel = next((output / 'dist').glob('*.whl'))
        python = venv('installed')
        extras = args.extra if args.profile == 'extra' else 'dev,web,competition' + (',rl' if args.rl else '')
        if args.profile.startswith('lab-'):
            extras = 'dev,multiagent' if args.profile == 'lab-adapters' else 'dev'
        run('install', [python, '-m', 'pip', 'install', str(wheel) + '[' + extras + ']'])
        run('pip-check', [python, '-m', 'pip', 'check'])
        run('freeze', [python, '-m', 'pip', 'freeze', '--all'])
        run('versions', [python, '-c',
            'import importlib.metadata as m, json, sys; from pathlib import Path; '
            'Path(sys.argv[1]).write_text(json.dumps('
            '{d.metadata["Name"]: d.version for d in m.distributions()}, sort_keys=True) + "\\n")',
            output / 'versions.json'])
        if args.profile == 'extra':
            run('smoke', [python, source / 'tests/packaging/smoke.py', '--extra', args.extra])
            return
        # Copy test inputs, never the package, so pytest cannot silently select
        # checkout Python code over the installed native wheel.
        suite = output / 'suite'
        suite.mkdir()
        # Workflow regression tests import their YAML and helper scripts.
        for directory in ('tests', 'examples', '.github'):
            shutil.copytree(source / directory, suite / directory)
        run('identity', [python, '-c',
            'import botbowl; from pathlib import Path; '
            'assert "site-packages" in Path(botbowl.__file__).parts; print(botbowl.__file__)'])
        if args.profile.startswith('lab-'):
            opposite = 'native' if args.backend == 'python' else 'python'
            run('backend-negative', [python, '-c',
                'import subprocess, sys; '
                'r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", '
                '"tests/lab/test_session.py", "--require-pathfinding=' + opposite + '"], '
                'capture_output=True, text=True); '
                'assert r.returncode != 0 and "Required ' + opposite +
                ' pathfinding; loaded" in r.stderr, r.stdout + r.stderr; '
                'print("Opposite backend rejected before collection")'], suite)
            # Reuse process-group cancellation/timeout cleanup, including nested
            # snapshot and installed-consumer subprocesses. Sequential repeats
            # have separate pytest temp trees and independently checked outputs.
            for repeat in range(2 if args.profile == 'lab-fast' else 1):
                name = args.profile + '-' + str(repeat + 1)
                run_shards([(name, [python, source / 'tools/ci/lab_profile.py', args.profile,
                                   '--backend', args.backend, '--output', output / (name + '.json')])],
                           output, suite, env, result['steps'], timeout=1200,
                           show_failure_logs=False)
            return
        if not args.rl:
            helper = source / 'tools/ci/core_selection.py'
            common = ['--backend', args.backend, '--revision', result['source_revision']]
            plan = output / 'collection.json'
            run('collection', [python, helper, 'collect', *common, '--output', plan], suite)
            run_core_shards(python, helper, plan, output, suite, env, result)
            return
        options = ['--require-pathfinding=' + args.backend, '-q', '-ra']
        # Split fast unit/regression tests and integration without dropping files.
        integration = ['tests/ai', 'tests/framework/test_forward_model.py',
                       'tests/framework/test_server.py', 'tests/game/test_full_game.py']
        failure = None
        for name, selection in [('unit', ['tests'] + ['--ignore=' + p for p in integration]),
                                ('integration', integration)]:
            try:
                run(name, [python, '-m', 'pytest', *selection, *options,
                           '--junitxml=' + str(output / (name + '.xml'))], suite)
            except subprocess.CalledProcessError as exc:
                failure = exc
        if failure:
            raise failure
    finally:
        result['seconds'] = round(time.monotonic() - start, 2)
        result['tests'] = {}
        for report in sorted(output.glob('*.xml')):
            document = ET.parse(report).getroot()
            counts = dict(passed=0, failed=0, errors=0, skipped=0, xfailed=0)
            reasons = {}
            for case in document.iter('testcase'):
                skipped = case.find('skipped')
                if skipped is not None:
                    outcome = 'xfailed' if skipped.get('type') == 'pytest.xfail' else 'skipped'
                    reason = skipped.get('message', '')
                    reasons[reason] = reasons.get(reason, 0) + 1
                elif case.find('failure') is not None:
                    outcome = 'failed'
                elif case.find('error') is not None:
                    outcome = 'errors'
                else:
                    outcome = 'passed'
                counts[outcome] += 1
            result['tests'][report.stem] = {
                **counts, 'skip_reasons': reasons,
                'junit_suites': [node.attrib for node in document.iter('testsuite')]}
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.profile.startswith('lab-'):
            # Keep compact receipts only; wheel, datasets and private snapshots
            # must not outlive the job or become uploaded evidence.
            for directory in ('source', 'installed', 'suite', 'dist', 'scratch'):
                shutil.rmtree(output / directory, ignore_errors=True)
            (output / 'source.tar').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
