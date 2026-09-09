"""Finite comparison with scenario/style holdouts fixed before collection.

Run from the repository root: python -m examples.lab.policies
All generated data lives in a temporary directory. No policy tuning occurs.
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from botbowl.lab.coverage import coverage_report
from botbowl.lab.generate import JobConfig, generate, validate_dataset
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.splits import (build_split_manifest, origin_from_episode,
                               policy_group_id, validate_split_manifest)


# Frozen before observing outcomes. Four jobs, two episodes, eight decisions each.
COMPARISONS = (('movement', 'scripted'), ('pickup', 'possession'),
               ('possession_recovery', 'cautious'), ('touchdown', 'risk_taking'))
HOLDOUTS = {'scenario': {'touchdown': 'test'},
            'policy': {policy_group_id('risk_taking', '1'): 'test'}}


def collect_comparison(root):
    sources, episodes = [], []
    for index, (scenario, style) in enumerate(COMPARISONS):
        destination = Path(root) / ('job-%d' % index)
        data = generate(JobConfig(str(destination), episodes=2, master_seed=7200 + index,
                        episode_prefix='comparison-%d' % index, scenario=scenario,
                        home_policy=style, away_policy='random', max_decisions=8))
        validate_dataset(destination)
        episodes.extend(data['episodes'])
        for episode in data['episodes']:
            manifest = EpisodeReader(destination, episode['episode_id']).manifest
            sources.append(origin_from_episode(manifest))
    split = build_split_manifest(sources, proportions={'train': .5, 'validation': .25, 'test': .25},
                                 seed=72, split_version='policy-comparison-v1',
                                 held_out_groups=HOLDOUTS)
    validate_split_manifest(split, sources)
    return coverage_report(episodes), split.to_json()


if __name__ == '__main__':
    with TemporaryDirectory(prefix='botbowl-policy-comparison-') as root:
        report, split = collect_comparison(root)
        print('Episodes:', report['sample_episodes'], 'decisions:', report['sample_decisions'])
        print('Reached events:', report['reached_events'])
        print('Split counts:', split['counts'])
        print('Limitations:', report['limitations'])
