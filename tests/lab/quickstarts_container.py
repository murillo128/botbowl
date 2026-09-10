"""Optional Linux Docker acceptance for the existing non-root headless image.

python -m tests.lab.quickstarts_container --image botbowl-headless
The HTTP client uses a disposable authenticated host-loopback server. No daemon
access, checkout, display or GPU is mounted inside the container.
"""
import argparse
import os
import subprocess

from botbowl.examples._loopback import ephemeral_tokens, loopback


SCRIPT = '''
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from botbowl.lab.replays import ReplayReader
assert os.getuid() != 0
assert not os.environ.get('DISPLAY')
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    def run(name, budget, *extra):
        subprocess.run([sys.executable, '-I', '-m', 'botbowl.examples.' + name,
                        *extra, '--output', str(root / name), '--seed', '17',
                        '--max-decisions', str(budget), '--max-steps', '1000'],
                       check=True, capture_output=True, timeout=180)
    def read(name):
        return json.loads((root / name).read_text())
    run('dataset', 4)
    assert sum(read('dataset/summary.json')['windows'].values()) == 24
    run('snapshot', 3, 'save')
    run('snapshot', 3, 'resume')
    assert read('snapshot/expected.json') == read('snapshot/actual.json')
    run('branches', 2)
    assert read('branches/comparison.json')['sibling_unchanged']
    for side in ('left', 'right'):
        game = ReplayReader(root / 'branches', side).replay_all()
        game.close()
    run('http_match', 8)
    http = read('http_match/http.json')
    assert {action['actor_id'] for action in http['actions']} == {'home', 'away'}
    assert http['state']['truncated'] and len(http['actions']) == 8
print('Four wheel quickstarts validated as non-root, without display or GPU')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    tokens = ephemeral_tokens()
    with loopback(tokens) as url:
        env = dict(os.environ, BOTBOWL_HTTP_URL=url)
        env.update({'BOTBOWL_HTTP_TOKEN_' + role.upper(): token for role, token in tokens.items()})
        command = ['docker', 'run', '--rm', '-i', '--network', 'host']
        for name in ('BOTBOWL_HTTP_URL', 'BOTBOWL_HTTP_TOKEN_ADMIN',
                     'BOTBOWL_HTTP_TOKEN_HOME', 'BOTBOWL_HTTP_TOKEN_AWAY'):
            command.extend(['--env', name])
        command.extend([args.image, 'python', '-I', '-'])
        result = subprocess.run(command, input=SCRIPT, env=env, capture_output=True,
                                text=True, timeout=600)
        if any(token in result.stdout + result.stderr for token in tokens.values()):
            raise RuntimeError('Credential appeared in container output')
        if result.returncode:
            raise RuntimeError('Container acceptance failed: ' + result.stderr)
        print(result.stdout.strip())


if __name__ == '__main__':
    main()
