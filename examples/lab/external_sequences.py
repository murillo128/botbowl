"""Generate the M1 fixture, or consume its frozen manifest in another process.

python /path/to/external_sequences.py generate /tmp/m1
python /path/to/external_sequences.py read /tmp/m1/consumer.json --split train
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from botbowl.lab.dataset_client import DatasetManifestV1, DatasetReader


def generate_fixture(destination, seed=17, max_decisions=8):
    # Producer capabilities are deliberately separate from the reader path.
    from botbowl.lab.generate import JobConfig, build_plan, execute_plan, validate_dataset
    from botbowl.lab.recording import EpisodeReader
    from botbowl.lab.splits import build_split_manifest, origin_from_episode
    from botbowl.lab.windows import WindowSpecV1

    root = Path(destination)
    plan = build_plan(JobConfig(output=str(root), episodes=100, master_seed=seed,
                               scenario='match', size=3, home_policy='scripted',
                               away_policy='scripted', max_decisions=max_decisions,
                               max_steps=1000))
    execute_plan(plan, root)
    dataset = validate_dataset(root)
    paths = {entry['episode_id']: entry['episode_id'] for entry in dataset['episodes']}
    sources = [origin_from_episode(EpisodeReader(root, path).manifest)
               for path in paths.values()]
    splits = build_split_manifest(sources, proportions={'train': .8, 'validation': .1, 'test': .1},
                                  seed=seed, split_version='m1-v1')
    manifest = DatasetManifestV1.from_sources(paths, window=WindowSpecV1(3, 2), split_manifest=splits)
    (root / 'consumer.json').write_text(json.dumps(manifest.to_json(), sort_keys=True), encoding='utf-8')
    ends = [entry['end'] for entry in dataset['episodes']]
    return {'episodes': len(ends), 'size': 3, 'decision_budget': max_decisions,
            'terminated': sum(end['terminated'] for end in ends),
            'truncated': sum(end['truncated'] for end in ends),
            'semantic_sha256': [entry['semantic_sha256'] for entry in dataset['episodes']],
            'splits': splits.to_json()['counts']}


def consume(manifest, split):
    reader = DatasetReader(manifest, split=split)
    expected = reader.validate()
    count, first = 0, None
    digest = hashlib.sha256()
    for batch in reader.iter_batches(batch_size=16):
        for sample in batch:
            inputs = sample['inputs']
            # A persistence predictor sees only the authorized history. It
            # demonstrates data shapes, not a trained model or success metric.
            latest = next((row for row in reversed(inputs['observations']) if row is not None), None)
            prediction = deepcopy(latest)
            if first is None:
                first = {'history_slots': len(inputs['observations']),
                         'target_slots': len(sample['targets']['observations']),
                         'input_presence': inputs['presence'],
                         'target_presence': sample['targets']['presence'],
                         'feature_paths': sorted(prediction or {}),
                         'origin': sample['origin'],
                         'cutoff': sample['metadata']['cutoff']}
            digest.update(json.dumps(sample, sort_keys=True, separators=(',', ':')).encode('utf-8'))
            count += 1
    if count != expected:
        raise ValueError('Dataset changed during consumption')
    return {'split': split, 'windows': count, 'first': first, 'window_sha256': digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generate = commands.add_parser('generate')
    generate.add_argument('destination')
    generate.add_argument('--seed', type=int, default=17)
    generate.add_argument('--max-decisions', type=int, default=8)
    read = commands.add_parser('read')
    read.add_argument('manifest')
    read.add_argument('--split', required=True)
    args = parser.parse_args()
    result = (generate_fixture(args.destination, args.seed, args.max_decisions)
              if args.command == 'generate' else consume(args.manifest, args.split))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
