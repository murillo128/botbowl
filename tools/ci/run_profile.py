"""Run CI against fresh committed sources and installed artifacts, outside checkout."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', choices=('suite', 'artifacts', 'extra'))
    parser.add_argument('--backend', choices=('python', 'native'), default='python')
    parser.add_argument('--extra', choices=('web', 'rl', 'competition', 'dev', 'render'))
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
        before = time.monotonic()
        with (output / (name + '.log')).open('w') as log:
            completed = subprocess.run(list(map(str, command)), cwd=cwd, env=process_env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        result['steps'].append({'name': name, 'seconds': round(time.monotonic() - before, 2),
                                'exit_code': completed.returncode})
        print(name, completed.returncode, flush=True)
        if completed.returncode:
            print((output / (name + '.log')).read_text()[-12000:], flush=True)
            raise subprocess.CalledProcessError(completed.returncode, command)

    def venv(name):
        target = output / name
        run(name, [sys.executable, '-m', 'venv', target])
        python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run(name + '-pip', [python, '-m', 'pip', 'install', 'pip==26.2.1'])
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
                run('freeze-' + str(index), [python, '-m', 'pip', 'freeze', '--all'])
            return
        run('build', [sys.executable, '-m', 'build', '--wheel', '--outdir', output / 'dist'], source,
            {**env, 'BOTBOWL_BUILD_NATIVE': '1' if args.backend == 'native' else '0'})
        wheel = next((output / 'dist').glob('*.whl'))
        python = venv('installed')
        extras = args.extra if args.profile == 'extra' else 'dev,web,competition' + (',rl' if args.rl else '')
        run('install', [python, '-m', 'pip', 'install', str(wheel) + '[' + extras + ']'])
        run('pip-check', [python, '-m', 'pip', 'check'])
        run('freeze', [python, '-m', 'pip', 'freeze', '--all'])
        if args.profile == 'extra':
            run('smoke', [python, source / 'tests/packaging/smoke.py', '--extra', args.extra])
            return
        # Copy test inputs, never the package, so pytest cannot silently select
        # checkout Python code over the installed native wheel.
        suite = output / 'suite'
        suite.mkdir()
        for directory in ('tests', 'examples'):
            shutil.copytree(source / directory, suite / directory)
        run('identity', [python, '-c',
            'import botbowl; from pathlib import Path; '
            'assert "site-packages" in Path(botbowl.__file__).parts; print(botbowl.__file__)'])
        options = ['--require-pathfinding=' + args.backend, '-q', '-ra']
        if not args.rl:
            # Only the legacy RL module requires unsupported Gym on 3.13/3.14.
            # Its complete suite runs in BOTH RL jobs, including the checker.
            options += ['--ignore=tests/ai/test_env.py']
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
        for report in output.glob('*.xml'):
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


if __name__ == '__main__':
    main()
