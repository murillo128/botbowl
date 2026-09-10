"""OPS-05: build and install the four examples outside the checkout."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import venv

import pytest


TEST_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = (TEST_ROOT.parent / 'source'
               if (TEST_ROOT.parent / 'source/pyproject.toml').is_file() else TEST_ROOT)
MODULES = ('dataset', 'http_match', 'snapshot', 'branches')


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp('quickstarts-installed')
    source = root / 'source'
    source.mkdir()
    for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(SOURCE_ROOT / name, source / name)
    shutil.copytree(SOURCE_ROOT / 'botbowl', source / 'botbowl',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.so', '*.pyd'))
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONPATH', 'PYTHONHOME', 'DISPLAY', 'PYTEST_ADDOPTS')
           and not key.startswith('BOTBOWL_HTTP_')}
    env.update(BOTBOWL_BUILD_NATIVE='0', PIP_CACHE_DIR=str(root / 'pip-cache'))

    def command(args, expected=0, extra_env=None):
        result = subprocess.run(list(map(str, args)), cwd=root,
                                env=dict(env, **(extra_env or {})), capture_output=True,
                                text=True, timeout=300)
        # Credentials are test-generated and must never enter pytest diagnostics.
        output = result.stdout + result.stderr
        for key, value in (extra_env or {}).items():
            if key.startswith('BOTBOWL_HTTP_TOKEN_'):
                if value in output:
                    raise AssertionError('credential appeared in example output')
        assert result.returncode == expected, output
        return result.stdout

    command([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-w', root / 'dist', source])
    venv.EnvBuilder(with_pip=True).create(root / 'venv')
    python = root / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    wheel, = (root / 'dist').glob('botbowl-*.whl')
    command([python, '-m', 'pip', 'install', str(wheel) + '[web]'])
    command([python, '-I', '-c', '''
from importlib.metadata import version
from pathlib import Path
import botbowl, sys
assert Path(sys.prefix) in Path(botbowl.__file__).parents
assert botbowl.__version__ == version('botbowl')
'''])

    def run(module, *args, expected=0, extra_env=None):
        return command([python, '-I', '-m', 'botbowl.examples.' + module, *args],
                       expected, extra_env)

    def code(script):
        return command([python, '-I', '-c', script])

    return root, run, code


def read(path):
    return json.loads(path.read_text())


def test_dataset_installed_reproducibility_and_no_contamination(installed):
    root, run, code = installed
    for name in ('dataset', 'repeat'):
        run('dataset', '--output', root / name, '--seed', '17', '--max-decisions', '4')
    first = read(root / 'dataset/summary.json')
    assert first == read(root / 'repeat/summary.json')
    assert sum(first['windows'].values()) == 24
    assert all(n > 0 for n in first['windows'].values())
    assert all(end['truncated'] and not end['terminated'] for end in first['ends'])
    code('''
from botbowl.lab.dataset_client import DatasetReader
import json
from pathlib import Path
path = Path('dataset/consumer.json')
manifest = json.loads(path.read_text())
assert manifest['schema_version'] == 1
families = {}
for split in ('train', 'validation', 'test'):
    reader = DatasetReader(path, split=split)
    windows = list(reader.iter_windows())
    assert len(windows) == reader.validate() > 0
    sources = {entry['source']['source_id']: entry['source']['origin_family_id']
               for entry in manifest['split_manifest']['sources']
               if manifest['split_manifest']['assignments'][entry['family_id']] == split}
    families[split] = set(sources.values())
    for sample in windows:
        assert sample['origin']['source_id'] in sources
        assert 'action' not in sample['inputs']
        for row in sample['inputs']['observations']:
            if row is not None:
                assert all(key.startswith('primary.') for key in row)
assert not (families['train'] & families['test'] or families['train'] & families['validation']
            or families['test'] & families['validation'])
# Offline input consumption does not construct a simulator or open audit artifacts.
import botbowl
def forbidden(*args, **kwargs):
    raise AssertionError('offline reader constructed a Game')
botbowl.Game.__init__ = forbidden
removed = 0
for episode in Path('dataset').glob('episode-*'):
    for directory in ('privileged', 'evaluation'):
        import shutil
        if (episode / directory).exists():
            removed += 1
            shutil.rmtree(episode / directory)
assert removed >= 6
for split in families:
    assert DatasetReader(path, split=split).validate() > 0
''')


def test_http_installed_matches_local_actual_actors(installed):
    root, run, code = installed
    credentials = {'BOTBOWL_HTTP_TOKEN_' + role: secrets.token_urlsafe(32)
                   for role in ('ADMIN', 'HOME', 'AWAY')}
    run('http_match', '--loopback', '--output', root / 'http', '--seed', '17',
        '--max-decisions', '8', extra_env=credentials)
    code('''
import json
from pathlib import Path
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig, SimulationSession
remote = json.loads(Path('http/http.json').read_text())
local = SimulationSession(SessionConfig(size=1, max_decisions=8, max_steps=1000),
                          SeedSpec(17, 'quickstart-http'))
try:
    actions = []
    while local.legal_actions().actions:
        legal = local.legal_actions()
        action = legal.actions[len(actions) % len(legal.actions)]
        actions.append(action.to_json())
        local.step(action, legal.state_revision)
    assert actions == remote['actions']
    assert local.observe().to_json() == remote['state']
finally:
    local.close()
''')
    result = read(root / 'http/http.json')
    assert {a['actor_id'] for a in result['actions']} == {'home', 'away'}
    assert result['state']['truncated'] and not result['state']['terminated']
    assert result['state']['end_reason'] == 'decision_budget'


def test_snapshot_installed_fresh_process_restores_episode(installed):
    root, run, _ = installed
    for operation in ('save', 'resume'):
        run('snapshot', operation, '--output', root / 'snapshot', '--seed', '17',
            '--max-decisions', '3')
    assert read(root / 'snapshot/save-process.json') != read(root / 'snapshot/resume-process.json')
    expected = read(root / 'snapshot/expected.json')
    assert expected == read(root / 'snapshot/actual.json')
    assert expected['decisions'] == 3 and len(expected['results']) == 2
    assert expected['results'][-1]['truncated'] and not expected['results'][-1]['terminal']
    envelope = read(root / 'snapshot/episode.snapshot.json')
    assert envelope['format'] == 'SnapshotFileV1' and envelope['scope'] == 'episode'
    assert envelope['component_versions']['engine'] == expected['manifest']['rules']['engine_version']


def test_branches_installed_replay_hashes_and_isolation(installed):
    root, run, code = installed
    run('branches', '--output', root / 'branches', '--seed', '17', '--max-decisions', '2')
    tree = read(root / 'branches/tree.json')
    assert tree['format'] == 'BranchTreeV1' and tree['schema_version'] == 1
    assert tree['attempted_decisions'] == 4
    alternatives = [n for n in tree['nodes'] if n['parent'] is not None]
    assert {n['branch_id'] for n in alternatives} == {'left', 'right'}
    assert all(n['parent_snapshot_id'] == 'origin' and n['origin_family_id'] == 'quickstart-origin'
               and n['kind'] == 'simulated_alternative' and n['horizon'] == 2
               and n['policy'] == {'policy_id': 'first-legal', 'version': 'v1'}
               for n in alternatives)
    assert alternatives[0]['initial_action'] != alternatives[1]['initial_action']
    comparison = read(root / 'branches/comparison.json')
    assert comparison['source_unchanged'] and comparison['sibling_unchanged']
    assert comparison['origin_hash'] == tree['snapshots'][0]['state_hash']
    code('''
from botbowl.lab.replays import ReplayReader
for side in ('left', 'right'):
    reader = ReplayReader('branches', side)
    result = reader.replay_all()
    result.close()
''')


@pytest.mark.parametrize('module', MODULES)
def test_installed_help_and_argument_errors(installed, module):
    root, run, _ = installed
    assert '--output' in run(module, '--help')
    assert '--seed' in run(module, '--help')
    args = ['save'] if module == 'snapshot' else []
    run(module, *args, '--output', root / 'invalid', '--seed', '17',
        '--max-steps', '0', expected=2)
    assert not (root / 'invalid').exists()


def test_installed_credential_failure_diagnostic_is_redacted(installed):
    _, run, _ = installed
    # A public canary present in help deliberately exercises the failure path.
    # Pytest assertion introspection must never echo the credential or output.
    with pytest.raises(AssertionError) as failure:
        run('dataset', '--help', extra_env={'BOTBOWL_HTTP_TOKEN_ADMIN': 'usage:'})
    assert str(failure.value) == 'credential appeared in example output'


def test_quickstart_docs_links_and_runtime_contract():
    import botbowl
    text = (SOURCE_ROOT / 'docs/lab/quickstarts.md').read_text()
    proposal = (SOURCE_ROOT / 'docs/lab/release-proposal.md').read_text()
    assert botbowl.__version__ in proposal
    for module in MODULES:
        assert 'python -m botbowl.examples.' + module in text
    assert 'SnapshotFileV1' in proposal and 'DatasetManifestV1' in proposal
    subprocess.run([sys.executable, SOURCE_ROOT / 'tools/release/check_docs.py',
                    '--root', SOURCE_ROOT], check=True, capture_output=True, text=True)
