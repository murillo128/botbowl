"""Inert, bounded external annotations. See docs/lab/annotations.md.

V1 intentionally accepts small numeric JSON only: no archive, pickle, module,
URL, transformation or executable loader is part of this format.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .records import _Record, context, decode_json, identifier, integer, keys, require


MAX_BUNDLE_BYTES = 1024 * 1024
MAX_ITEMS = 128
MAX_ELEMENTS = 16384
KINDS = ('embedding', 'probe_output', 'projection_2d', 'entity_score', 'human_note')
DTYPES = ('float32', 'float64', 'int32', 'int64')


def public_entity_ids(observation):
    """Use the stable IDs that ObservationV1 actually supplies (players/teams)."""
    return [entity['id'] for group in ('players', 'teams') for entity in observation[group]]


def _strings(values, *, empty=False):
    require(type(values) is list and (empty or bool(values)), 'Expected identifier list')
    for value in values:
        identifier(value)
    require(len(values) == len(set(values)), 'Duplicate identifiers')


def _matrix(value, rows, kind):
    keys(value, ('format', 'shape', 'dtype', 'nbytes', 'values'))
    require(value['format'] == 'numeric_json', 'Only numeric JSON is supported')
    shape = value['shape']
    require(type(shape) is list and len(shape) == 2, 'Expected matrix shape')
    for dimension in shape:
        integer(dimension, 1)
    require(shape[0] == rows and shape[0] * shape[1] <= MAX_ELEMENTS, 'Matrix size limit or entity mismatch')
    require(type(value['dtype']) is str and value['dtype'] in DTYPES, 'Unsupported numeric dtype')
    dtype = np.dtype(value['dtype'])
    integer(value['nbytes'], 1)
    require(value['nbytes'] == shape[0] * shape[1] * dtype.itemsize, 'Declared byte size mismatch')
    require(kind != 'projection_2d' or shape[1] == 2, 'Projection requires two columns')
    require(kind != 'entity_score' or shape[1] == 1, 'Entity score requires one column')
    values = value['values']
    require(type(values) is list and len(values) == shape[0], 'Wrong matrix rows')
    bounds = np.iinfo(dtype) if dtype.kind == 'i' else np.finfo(dtype)
    for row in values:
        require(type(row) is list and len(row) == shape[1], 'Wrong matrix columns')
        for number in row:
            require(type(number) in ((int,) if dtype.kind == 'i' else (int, float)), 'Expected numeric scalar')
            require(type(number) is not int or abs(number) <= 2**53 - 1, 'Integer exceeds lossless browser JSON range')
            require(bounds.min <= number <= bounds.max, 'Non-finite or out-of-range scalar')


def _provenance(value, kind):
    keys(value, ('source', 'access', 'fitting'))
    require(type(value['source']) is str and 0 < len(value['source']) <= 4096, 'Expected source declaration')
    require(value['access'] in ('public', 'privileged'), 'Unknown access classification')
    fit = value['fitting']
    if fit is None:
        require(kind not in ('projection_2d', 'probe_output'), 'Projection/probe fitting declaration required')
        return
    keys(fit, ('split_manifest_id', 'split_version', 'fit_partitions', 'evaluation_partition', 'held_out'))
    identifier(fit['split_manifest_id'])
    identifier(fit['split_version'])
    _strings(fit['fit_partitions'], empty=True)
    identifier(fit['evaluation_partition'])
    require(type(fit['held_out']) is bool, 'Expected held-out flag')
    if fit['held_out']:
        require('test' not in fit['fit_partitions'] and
                fit['evaluation_partition'] not in fit['fit_partitions'], 'Held-out split leakage')


@dataclass(frozen=True, init=False)
class AnnotationBundleV1(_Record):
    """Immutable canonical JSON with fresh copies at every public read."""

    @staticmethod
    def _validate(data):
        keys(data, ('format', 'schema_version', 'bundle_id', 'items'))
        require(data['format'] == 'AnnotationBundleV1' and
                type(data['schema_version']) is int and data['schema_version'] == 1, 'Unknown annotation version')
        identifier(data['bundle_id'])
        require(type(data['items']) is list and 1 <= len(data['items']) <= MAX_ITEMS, 'Annotation count limit')
        seen = set()
        for item in data['items']:
            keys(item, ('annotation_id', 'episode_id', 'origin_family_id', 'branch_id',
                        'target', 'target_kind', 'entity_ids', 'kind', 'method', 'issued_at',
                        'horizon', 'retrospective', 'provenance', 'data'))
            for field in ('annotation_id', 'episode_id', 'origin_family_id', 'branch_id'):
                identifier(item[field])
            require(item['annotation_id'] not in seen, 'Duplicate annotation ID')
            seen.add(item['annotation_id'])
            context(item['target'])
            context(item['issued_at'])
            require(item['target_kind'] in ('decision', 'event'), 'Unknown target kind')
            for ctx in (item['target'], item['issued_at']):
                require(all(ctx[k] == item[k] for k in ('episode_id', 'branch_id')), 'Context identity mismatch')
            integer(item['horizon'])
            require(type(item['retrospective']) is bool, 'Expected retrospective flag')
            target, issued = item['target'], item['issued_at']
            require(item['horizon'] == abs(target['decision_seq'] - issued['decision_seq']), 'Invalid decision horizon')
            require(item['retrospective'] or
                    (issued['decision_seq'] <= target['decision_seq'] and issued['event_seq'] <= target['event_seq']),
                    'Late emission cannot be a prior prediction')
            _strings(item['entity_ids'])
            require(type(item['kind']) is str and item['kind'] in KINDS, 'Unknown annotation kind')
            keys(item['method'], ('method_id', 'version'))
            identifier(item['method']['method_id'])
            identifier(item['method']['version'])
            _provenance(item['provenance'], item['kind'])
            if item['kind'] == 'human_note':
                require(type(item['data']) is str and 0 < len(item['data']) <= 4096, 'Expected bounded note')
            else:
                _matrix(item['data'], len(item['entity_ids']), item['kind'])

    def __init__(self, data):
        super().__init__(data)
        require(len(self._encoded) <= MAX_BUNDLE_BYTES, 'Annotation bundle byte limit')

    @classmethod
    def load(cls, path):
        """Read at most 1 MiB plus one byte; every non-JSON format is rejected."""
        with Path(path).open('rb') as stream:
            payload = stream.read(MAX_BUNDLE_BYTES + 1)
        require(len(payload) <= MAX_BUNDLE_BYTES, 'Annotation bundle byte limit')
        return cls(decode_json(payload))

    def aligned(self, annotation_id, entity_ids):
        """Return rows in requested entity order, never positional coincidence."""
        _strings(entity_ids)
        item = next((i for i in self.to_json()['items'] if i['annotation_id'] == annotation_id), None)
        require(item is not None and item['kind'] != 'human_note', 'Unknown numeric annotation')
        require(set(entity_ids) <= set(item['entity_ids']), 'Unknown annotation entity')
        matrix = np.asarray(item['data']['values'], dtype=item['data']['dtype'])
        return matrix[[item['entity_ids'].index(entity) for entity in entity_ids]].copy()


def validate_replay(bundle, reader):
    """Verify exact replay identity, boundary/event contexts and stable entity IDs.

    Emission is always an accepted decision boundary. Interior event targets use
    their recorded context and the preceding board, as in ReplayReader.seek_event.
    No live Game or original prediction is written.
    """
    require(type(bundle) is AnnotationBundleV1, 'Expected AnnotationBundleV1')
    manifest = reader.manifest
    boundaries = {}

    def boundary(decision):
        require(manifest['initial_context']['decision_seq'] <= decision <= manifest['final_context']['decision_seq'],
                'Decision outside replay')
        if decision not in boundaries:
            game = reader.seek_decision(decision)
            try:
                from .observations import observe
                observation = observe(game, game.timeline._entities, 'home').to_json()
                boundaries[decision] = (game.timeline.context.to_json(), set(public_entity_ids(observation)))
            finally:
                game.close()
        return boundaries[decision]

    for item in bundle.to_json()['items']:
        require(item['origin_family_id'] == manifest['origin_family_id'], 'Wrong origin family')
        for key in ('episode_id', 'branch_id'):
            require(item[key] == manifest['initial_context'][key], 'Wrong replay identity')
        issued, _ = boundary(item['issued_at']['decision_seq'])
        require(issued == item['issued_at'], 'Unknown emission boundary')
        if item['target_kind'] == 'decision':
            target, entities = boundary(item['target']['decision_seq'])
        else:
            require(manifest['initial_context']['event_seq'] < item['target']['event_seq'] <=
                    manifest['final_context']['event_seq'], 'Event outside replay')
            found = reader.seek_event(item['target']['event_seq'])
            try:
                target = found.event['context']
                _, entities = boundary(found.previous_decision)
            finally:
                found.game.close()
        require(target == item['target'], 'Unknown target context')
        require(set(item['entity_ids']) <= entities, 'Unknown replay entity')
    return bundle
