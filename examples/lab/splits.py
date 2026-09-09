"""Build and validate a tiny deterministic origin-family split."""
from botbowl.lab.splits import build_split_manifest, validate_window_membership


def source(source_id, content_digit, *, family=None, relationship=None):
    return {
        'schema_version': 1,
        'source_id': source_id,
        'source_version': 'example-v1',
        'kind': 'episode' if relationship is None else 'augmentation',
        'episode_id': source_id,
        'origin_family_id': family,
        'content_digest': 'sha256:' + content_digit * 64,
        'semantic_origin_fingerprint': None,
        'variant_group_id': None,
        'relationships': [] if relationship is None else [
            {'kind': 'augmentation_source', 'source_id': relationship}],
        'groups': {'scenario': ['standard'], 'policy': ['random-v1']},
    }


sources = [
    source('match-a', '1', family='match-a-family'),
    source('match-a-augmented', '2', family='match-a-family', relationship='match-a'),
    source('match-b', '3', family='match-b-family'),
    source('match-c', '4', family='match-c-family'),
]
manifest = build_split_manifest(
    sources,
    proportions={'train': 2 / 3, 'test': 1 / 3},
    seed=70,
    split_version='example-v1',
)

data = manifest.to_json()
entry = next(item for item in data['sources'] if item['source']['source_id'] == 'match-a')
assigned = data['assignments'][entry['family_id']]
validate_window_membership(manifest, [{
    'window': {'start': 0, 'stop': 8},
    'split': assigned,
    'origin': {
        'source_id': 'match-a',
        'episode_id': 'match-a',
        'source_family': 'match-a-family',
    },
}])

print(data['counts'])
print('match-a and its augmentation:', assigned)
