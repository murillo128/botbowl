"""Generate, validate and replay a small dataset without optional packages."""
from pathlib import Path
from tempfile import TemporaryDirectory

from botbowl.lab.generate import JobConfig, generate, replay_episode, validate_dataset
from botbowl.lab.recording import EpisodeReader


def main():
    with TemporaryDirectory(prefix='botbowl-generate-') as temporary:
        root = Path(temporary)
        original = generate(JobConfig(str(root / 'dataset'), episodes=2, scenario='pickup',
                                      side='away', master_seed=17, max_decisions=8))
        validate_dataset(root / 'dataset')
        for expected in original['episodes']:
            episode_id = expected['episode_id']
            episode = EpisodeReader(root / 'dataset', episode_id).read_episode()
            actions = [row['action'] for row in episode['channels']['transitions']]
            replay = replay_episode(root / 'dataset', episode_id,
                                    str(root / ('replay-' + episode_id)), actions)
            assert replay['episodes'] == [expected]
            print(episode_id, expected['end']['reason'], expected['semantic_sha256'])


if __name__ == '__main__':
    main()
