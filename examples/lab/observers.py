"""One CPU episode, full and partial causal histories: python -m examples.lab.observers."""
import json
from tempfile import TemporaryDirectory

from botbowl.lab.observers import NumericNoiseV1, ObservationTransformSpecV1, iter_observed_windows
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1, iter_windows
from examples.lab.recording import record


def main():
    with TemporaryDirectory(prefix='botbowl-observers-') as destination:
        record(destination)
        reader = EpisodeReader(destination, 'example-17')
        splits = build_split_manifest([origin_from_episode(reader.manifest)],
                                      proportions={'train': 1}, seed=17, split_version='observer-example-v1')
        history = WindowSpecV1(4, 2)
        transform = ObservationTransformSpecV1(
            hidden_fields=('primary.teams[].score',), hidden_entities=('away:0',), every_k=3,
            noise=(NumericNoiseV1('primary.players[].attributes.ma', 'normal', 1.0,
                                  'attribute_point', 0, 30, bounds='clip'),))
        full = list(iter_windows(reader, history, split_manifest=splits))
        partial = list(iter_observed_windows(reader, history, transform, observer_seed=23,
                                             split_manifest=splits))
        repeated = list(iter_observed_windows(reader, history, transform, observer_seed=23,
                                              split_manifest=splits))
        assert partial == repeated
        assert len(full) == len(partial)
        assert [s['targets'] for s in full] == [s['targets'] for s in partial]
        assert [s['metadata']['source_transitions'] for s in full] == [
            s['metadata']['source_transitions'] for s in partial]
        print(json.dumps({'windows': len(full), 'every_k': transform.every_k,
                          'history_at_decision_3': partial[3]['inputs']['presence'],
                          'same_targets': True, 'repeatable': True}, sort_keys=True))


if __name__ == '__main__':
    main()
