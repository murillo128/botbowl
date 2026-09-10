"""Generate, validate and read a small dataset with frozen origin-family splits."""
from botbowl.examples._common import arguments, positive, write
from botbowl.lab.dataset_client import DatasetManifestV1, DatasetReader
from botbowl.lab.generate import JobConfig, build_plan, execute_plan, validate_dataset
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1


def main():
    parser = arguments(__doc__)
    parser.add_argument('--episodes', type=positive, default=6)
    args = parser.parse_args()
    if args.episodes < 3:
        parser.error('--episodes must be at least 3 for three origin partitions')
    root = args.output.resolve()
    plan = build_plan(JobConfig(output=str(root), episodes=args.episodes,
                               master_seed=args.seed, scenario='match', size=3,
                               home_policy='scripted', away_policy='scripted',
                               max_decisions=args.max_decisions, max_steps=args.max_steps))
    execute_plan(plan, root)
    dataset = validate_dataset(root)
    paths = {row['episode_id']: row['episode_id'] for row in dataset['episodes']}
    splits = build_split_manifest(
        [origin_from_episode(EpisodeReader(root, path).manifest) for path in paths],
        proportions={'train': .6, 'validation': .2, 'test': .2},
        seed=args.seed, split_version='quickstart-v1')
    manifest = DatasetManifestV1.from_sources(
        paths, window=WindowSpecV1(3, 2, mode='passive'), split_manifest=splits)
    write(root / 'consumer.json', manifest.to_json())
    counts = {}
    for split in ('train', 'validation', 'test'):
        reader = DatasetReader(root / 'consumer.json', split=split)
        counts[split] = reader.validate()
        # A predictor sees only authorized history, never targets or audit data.
        for batch in reader.iter_batches(16):
            for sample in batch:
                latest = next((row for row in reversed(sample['inputs']['observations'])
                               if row is not None), None)
                if latest is None:
                    raise ValueError('Expected at least one historical observation')
    write(root / 'summary.json', {'windows': counts, 'episodes': len(paths),
          'semantic_sha256': [row['semantic_sha256'] for row in dataset['episodes']],
          'ends': [row['end'] for row in dataset['episodes']]})


if __name__ == '__main__':
    main()
