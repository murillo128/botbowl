"""Both causal modes, no training: python -m examples.lab.windows."""
import json
from tempfile import TemporaryDirectory

from botbowl.lab.recording import EpisodeReader
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1, iter_window_batches
from examples.lab.recording import record


def main():
    with TemporaryDirectory(prefix='botbowl-windows-') as destination:
        record(destination)
        reader = EpisodeReader(destination, 'example-17')
        splits = build_split_manifest([origin_from_episode(reader.manifest)],
                                      proportions={'train': 1}, seed=17, split_version='example-v1')
        for mode in ('passive', 'action_conditioned'):
            spec = WindowSpecV1(history_length=3, horizon=2, mode=mode)
            count = 0
            for batch in iter_window_batches(reader, spec, split_manifest=splits, batch_size=4):
                count += len(batch)
                assert all(('action' in sample['inputs']) == (mode == 'action_conditioned')
                           for sample in batch)
            print(json.dumps({'mode': mode, 'windows': count, 'split': 'train'}))


if __name__ == '__main__':
    main()
