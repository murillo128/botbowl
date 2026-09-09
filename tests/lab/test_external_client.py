"""Frozen-reader adversarial tests and genuinely separately installed consumers."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

from botbowl.lab.dataset_client import DatasetManifestV1, DatasetReader
from botbowl.lab.channels import InputProfile, make_channel
from botbowl.lab.records import RecordError, encode_json
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1
from tests.lab.test_windows import PROFILE, enumerable, template, write_episode  # noqa: F401


TEST_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = (TEST_ROOT.parent / 'source'
               if (TEST_ROOT.parent / 'source/pyproject.toml').is_file() else TEST_ROOT)


def publish(root, readers, spec=None, proportions=None):
    splits = build_split_manifest([origin_from_episode(reader.manifest) for reader in readers],
                                  proportions=proportions or {'train': 1}, seed=17,
                                  split_version='external-test-v1')
    manifest = DatasetManifestV1.from_sources(
        {reader.manifest['episode_id']: reader.directory.name for reader in readers},
        window=spec or WindowSpecV1(3, 2, profile=PROFILE), split_manifest=splits)
    path = root / 'consumer.json'
    path.write_bytes(encode_json(manifest.to_json()))
    return path


def test_terminal_empty_masks_modes_and_detached_data(tmp_path, template):
    reader, _ = enumerable(tmp_path, template)
    path = publish(tmp_path, [reader])
    client = DatasetReader(path, split='train')
    samples = list(client.iter_windows())
    assert client.validate() == len(samples) == 6
    assert samples[0]['inputs']['presence'] == [False, False, True]
    assert samples[-1]['targets']['presence'] == [True, False]
    assert samples[-1]['targets']['observations'][0]['primary.match.game_over'] is True
    assert samples[-1]['targets']['observations'][1] is None
    assert 'action' not in samples[0]['inputs']
    assert [len(batch) for batch in client.iter_batches(4)] == [4, 2]
    saved = deepcopy(samples[1])
    samples[0]['inputs']['observations'][-1]['primary.teams[].score'][0] = 9999
    assert samples[1] == saved
    detached = client.manifest
    detached['window']['mode'] = 'action_conditioned'
    assert client.manifest['window']['mode'] == 'passive'
    path = publish(tmp_path, [reader], WindowSpecV1(3, 2, profile=PROFILE, mode='action_conditioned'))
    assert 'action' in next(DatasetReader(path, split='train').iter_windows())['inputs']
    empty, _ = enumerable(tmp_path, template, count=0, end='truncated', name='empty')
    path = publish(tmp_path, [empty])
    assert DatasetReader(path, split='train').validate() == 0
    assert list(DatasetReader(path, split='train').iter_batches()) == []


@pytest.mark.parametrize('edit', [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(future_labels={'sentinel': 991122}),
    lambda d: d['observation'].update(schema_version=2),
    lambda d: d['observation'].update(schema_version=True),
    lambda d: d['view'].update(observer_team='away'),
    lambda d: d['window'].update(mode='oracle'),
    lambda d: d['window'].update(unit='seconds'),
    lambda d: d['window'].update(data_version=2),
    lambda d: d['window'].pop('mode'),
    lambda d: d['window']['profile'].update(schema_version=2),
    lambda d: d['episodes'][0].update(path='../escape'),
    lambda d: d['episodes'].append(deepcopy(d['episodes'][0])),
    lambda d: d['split_manifest']['assignments'].update(
        {next(iter(d['split_manifest']['assignments'])): 'test'}),
])
def test_unknown_or_tampered_manifest_fails(tmp_path, template, edit):
    reader, _ = enumerable(tmp_path, template)
    path = publish(tmp_path, [reader])
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        DatasetReader(path, split='train')


def test_split_is_required_and_inventory_digest_is_bound(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    path = publish(tmp_path, [reader])
    with pytest.raises(TypeError):
        DatasetReader(path)
    with pytest.raises(RecordError, match='split'):
        DatasetReader(path, split='test')
    rows['primary'][0]['channel']['data']['teams'][0]['score'] = 91234
    write_episode(reader.directory, reader.manifest, rows)
    with pytest.raises(ValueError):
        DatasetReader(path, split='train')



def test_partition_reads_only_selected_origins_and_rejects_changed_bytes(tmp_path, template, monkeypatch):
    readers = []
    for index in range(3):
        reader, rows = enumerable(tmp_path, template, name='origin-%d' % index)
        manifest = reader.manifest
        manifest['source_family'] = 'family-%d' % index
        for row in rows['transitions']:
            row['source_family'] = manifest['source_family']
        readers.append(write_episode(reader.directory, manifest, rows))
    path = publish(tmp_path, readers, proportions={'train': .8, 'validation': .1, 'test': .1})
    client = DatasetReader(path, split='train')
    frozen = client.manifest['split_manifest']
    selected = {entry['source']['source_id'] for entry in frozen['sources']
                if frozen['assignments'][entry['family_id']] == 'train'}
    assert len(selected) == 1
    from botbowl.lab.recording import EpisodeReader
    original = EpisodeReader.iter_channel
    opened = set()

    def guarded(reader, name):
        assert reader.manifest['episode_id'] in selected
        opened.add(name)
        return original(reader, name)

    monkeypatch.setattr(EpisodeReader, 'iter_channel', guarded)
    samples = list(client.iter_windows())
    assert len(samples) == 6 and {s['origin']['source_id'] for s in samples} == selected
    assert opened == {'primary', 'transitions'}
    selected_path = next(reader.directory for reader in readers if reader.directory.name in selected)
    with (selected_path / 'inputs/primary.jsonl').open('ab') as stream:
        stream.write(b'{}\n')
    with pytest.raises(ValueError, match='byte count'):
        client.validate()


def test_future_reference_and_duplicate_manifest_keys_fail(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    rows['transitions'][0]['pre_observation'] = rows['transitions'][0]['post_observation']
    reader = write_episode(reader.directory, reader.manifest, rows)
    path = publish(tmp_path, [reader])
    with pytest.raises(ValueError):
        DatasetReader(path, split='train').validate()
    payload = path.read_text()
    path.write_text('{"schema_version":1,' + payload[1:])
    with pytest.raises(ValueError):
        DatasetReader(path, split='train')

def test_rejects_channel_injection_and_disallowed_profile(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    rows['primary'][0]['channel']['data']['teams'][0]['oracle'] = 991122
    reader = write_episode(reader.directory, reader.manifest, rows)
    path = publish(tmp_path, [reader])
    with pytest.raises(ValueError):
        DatasetReader(path, split='train').validate()
    with pytest.raises(ValueError):
        InputProfile('custom', (('evaluation.labels.winner', 'integer'),))
    with pytest.raises(ValueError):
        InputProfile('custom', (('primary.teams[]', 'integer'),))


def test_oracle_and_future_sentinels_stay_out_of_inputs(tmp_path, template, monkeypatch):
    reader, rows = enumerable(tmp_path, template)
    for name, data in [('evaluation', {'labels': {'oracle': 991122}, 'estimates': {}, 'provenance': {}}),
                       ('privileged', {'snapshot': 991122})]:
        rows[name] = [{'schema_version': 1, 'observation_id': 1,
                       'context': rows['primary'][0]['context'], 'channel': make_channel(name, data)}]
    reader = write_episode(reader.directory, reader.manifest, rows)
    path = publish(tmp_path, [reader])
    # Physically unavailable target/audit files must not prevent standard reads.
    for name in ('evaluation', 'privileged'):
        (reader.directory / reader.manifest['files'][name]['path']).unlink()
    import botbowl
    monkeypatch.setattr(botbowl.Game, '__init__', lambda *a, **k: pytest.fail('constructed Game'))
    before = list(DatasetReader(path, split='train').iter_windows())
    assert '991122' not in json.dumps(before)
    assert before[0]['inputs']['observations'][-1]['primary.teams[].score'] == [0, 0]
    # Change only the final public future; reseal the producer's manifest/split.
    rows['primary'][-1]['channel']['data']['teams'][0]['score'] = 991122
    reader = write_episode(reader.directory, reader.manifest, rows)
    path = publish(tmp_path, [reader])
    after = list(DatasetReader(path, split='train').iter_windows())
    assert [s['inputs'] for s in before] == [s['inputs'] for s in after]
    assert '991122' in json.dumps(after[-1]['targets'])


def test_view_and_batch_policy_fail_visibly(tmp_path, template):
    reader, rows = enumerable(tmp_path, template)
    rows['primary'][0]['channel']['data']['observer_team'] = 'away'
    reader = write_episode(reader.directory, reader.manifest, rows)
    path = publish(tmp_path, [reader])
    client = DatasetReader(path, split='train')
    with pytest.raises(ValueError, match='observer'):
        client.validate()
    for size in (0, -1, True):
        with pytest.raises(ValueError):
            list(client.iter_batches(size))


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    work = tmp_path_factory.mktemp('external-installed')
    source = work / 'source'
    # Build a separate source tree to avoid polluting the checkout or picking
    # up a previous compiled extension. Include only distribution build inputs.
    source.mkdir()
    for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(SOURCE_ROOT / name, source / name)
    shutil.copytree(SOURCE_ROOT / 'botbowl', source / 'botbowl',
                    ignore=shutil.ignore_patterns('__pycache__', '*.so', '*.pyd', '*.pyc'))
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONPATH', 'PYTHONHOME')}
    env.update(BOTBOWL_BUILD_NATIVE='0', PIP_CACHE_DIR=str(work / 'pip-cache'))

    def command(args, cwd=work, timeout=240):
        result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    command([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', work / 'dist', source])
    venv.EnvBuilder(with_pip=True).create(work / 'venv')
    python = work / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    wheel, = (work / 'dist').glob('botbowl-*.whl')
    command([python, '-m', 'pip', 'install', wheel])
    for name in ('external_sequences.py', 'external_control.py'):
        shutil.copy2(SOURCE_ROOT / 'examples/lab' / name, work / name)
    # Check direct consumer imports structurally, and forbid optional imports at
    # runtime. Engine implementation imports inside public Bot Bowl APIs remain
    # necessary for simulation; the consumer never imports those private APIs.
    for relative in ('examples/lab/external_sequences.py', 'examples/lab/external_control.py',
                     'botbowl/lab/dataset_client.py'):
        tree = ast.parse((SOURCE_ROOT / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                assert not module.startswith('botbowl.core')
                assert not any(part.startswith('_') for part in module.split('.'))
                assert all(not name.name.startswith('_') for name in node.names)
            if isinstance(node, ast.Attribute):
                assert not node.attr.startswith('_') or relative.startswith('botbowl/')
    driver = work / 'driver.py'
    driver.write_text('''
import importlib.abc
import json
from pathlib import Path
import runpy
import sys
class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0].lower() in {'torch', 'tensorflow', 'jax', 'nfl', 'nfl_world_model',
                                             'flask', 'gym', 'gymnasium', 'matplotlib'}:
            raise AssertionError('Unexpected consumer dependency: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
import botbowl
assert Path(sys.prefix) in Path(botbowl.__file__).parents, botbowl.__file__
if sys.argv[2:3] == ['read']:
    def no_game(*args, **kwargs):
        raise AssertionError('Reader constructed a Game')
    botbowl.Game.__init__ = no_game
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
''')

    def run(name, *args, timeout=600):
        return json.loads(command([python, '-I', driver, work / name, *args], timeout=timeout))
    return work, run


def test_installed_external_control_is_deterministic_and_schedules_actual_actors(installed):
    _, run = installed
    first = run('external_control.py')
    assert first == run('external_control.py')
    actors = [row['actor'] for row in first['decisions']]
    assert set(actors) == {'home', 'away'}
    assert any(a == b for a, b in zip(actors, actors[1:]))
    assert any(a != b for a, b in zip(actors, actors[1:]))
    assert first['truncated'] and not first['terminated']
    assert first['end_reason'] == 'decision_budget'


def test_installed_m1_reproducible_100_episode_plan_and_origin_splits(installed):
    work, run = installed
    first = run('external_sequences.py', 'generate', work / 'm1')
    second = run('external_sequences.py', 'generate', work / 'm1-repeat')
    assert first == second
    assert first['episodes'] == 100 and first['size'] == 3
    assert first['terminated'] + first['truncated'] == 100
    assert first['truncated'] == 100  # Eight decisions cannot complete a match.
    counts = []
    origins = []
    for split in ('train', 'validation', 'test'):
        summary = run('external_sequences.py', 'read', work / 'm1/consumer.json', '--split', split)
        assert summary == run('external_sequences.py', 'read', work / 'm1-repeat/consumer.json', '--split', split)
        assert summary['windows'] > 0
        assert summary['first']['history_slots'] == 3 and summary['first']['target_slots'] == 2
        counts.append(summary['windows'])
        origins.append(summary['first']['origin']['source_id'])
    assert sum(counts) == 800 and len(set(origins)) == 3
    manifest = json.loads((work / 'm1/consumer.json').read_text())['split_manifest']
    groups = {split: {entry['source']['origin_family_id'] for entry in manifest['sources']
                      if manifest['assignments'][entry['family_id']] == split}
              for split in ('train', 'validation', 'test')}
    assert [len(groups[s]) for s in ('train', 'validation', 'test')] == [78, 11, 11]
    assert not (groups['train'] & groups['test'] or groups['train'] & groups['validation'] or
                groups['test'] & groups['validation'])
