"""A tiny selected collection; run with an explicit output directory."""
import argparse
from pathlib import Path

from botbowl.lab.collections import CollectionSpecV1, collect
from botbowl.lab.records import encode_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    spec = CollectionSpecV1.create(
        generation={'scenario': 'pickup', 'home_policy': 'possession',
                    'master_seed': 17, 'max_decisions': 4},
        mode='selected', predicate='possession_gain-v1', target=1,
        max_episodes=2, max_decisions=8, before=2, after=2)
    print(encode_json(collect(spec, args.output)).decode('utf-8'))


if __name__ == '__main__':
    main()
