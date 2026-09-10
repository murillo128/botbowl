"""Artificial external annotations: python -m examples.lab.annotations DESTINATION."""
import argparse
import base64
from pathlib import Path

from botbowl.lab.annotations import AnnotationBundleV1
from examples.lab.research_viewer import build_demo


def add_annotations(store, destination):
    """Attach all five v1 kinds to a seeded demo and write an inspectable export."""
    destination = Path(destination)
    row = next(store.summary(key) for key in store.entries
               if store.summary(key)['provenance']['kind'] == 'observed')
    frame = store.frame(row['id'], 1)
    context = frame['context']
    entities = list(reversed([p['id'] for p in frame['observation']['players']]))
    items = []
    for kind in ('embedding', 'probe_output', 'projection_2d', 'entity_score', 'human_note'):
        columns = 2 if kind in ('embedding', 'projection_2d') else 1
        values = [[float(i + j) for j in range(columns)] for i in range(len(entities))]
        fitting = None
        if kind in ('projection_2d', 'probe_output'):
            fitting = {'split_manifest_id': 'artificial-no-fit', 'split_version': 'v1',
                       'fit_partitions': [], 'evaluation_partition': 'demo', 'held_out': False}
        items.append({'annotation_id': kind, 'episode_id': context['episode_id'],
            'origin_family_id': row['origin_family_id'], 'branch_id': context['branch_id'],
            'target': context, 'target_kind': 'decision', 'entity_ids': entities, 'kind': kind,
            'method': {'method_id': 'artificial-index', 'version': 'v1'}, 'issued_at': context,
            'horizon': 0, 'retrospective': False,
            'provenance': {'source': 'Deterministic artificial row indices; no fitting or causal claim',
                           'access': 'public', 'fitting': fitting},
            'data': '<b>Artificial note displayed as text</b>' if kind == 'human_note' else {
                'format': 'numeric_json', 'shape': [len(entities), columns], 'dtype': 'float64',
                'nbytes': len(entities) * columns * 8, 'values': values}})
    bundle = AnnotationBundleV1({'format': 'AnnotationBundleV1', 'schema_version': 1,
                                 'bundle_id': 'artificial-layers', 'items': items})
    import json
    (destination / 'annotations.json').write_text(json.dumps(bundle.to_json(), indent=2) + '\n', encoding='utf-8')
    store.import_annotations('demo', row['id'], bundle.to_json())
    export = store.export_fragment('demo', row['id'], {'decisions': [1, 2]})
    output = destination / 'synthetic-export'
    output.mkdir()
    # Names here are produced by our own exporter, never by an untrusted bundle.
    for name, encoded in export['files'].items():
        (output / name).write_bytes(base64.b64decode(encoded))
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    store = build_demo(args.destination)
    try:
        add_annotations(store, args.destination)
        print('Wrote factual-upload.json, annotations.json and synthetic-export/ to', args.destination)
    finally:
        store.close()


if __name__ == '__main__':
    main()
