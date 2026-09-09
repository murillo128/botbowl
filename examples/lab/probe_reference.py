"""Small CPU ridge probes over exported data: python -m examples.lab.probe_reference.

No encoder import, load, update or artifact access. NumPy fits only linear heads.
"""
import argparse
from collections import Counter
import hashlib
import json

import numpy as np

from botbowl.lab.evaluation.probes import ProbeTask, align_embeddings, _id
from botbowl.lab.records import encode_json, integer, require


CONTROLS = ('external', 'raw', 'random_projection', 'constant')


def family_weights(families):
    counts = Counter(families)
    require(bool(counts), 'No eligible families')
    return np.array([1.0 / (len(counts) * counts[f]) for f in families])


def metrics(kind, targets, predictions, families, classes=()):
    """Each family has equal mass; windows share their family's mass."""
    require(len(targets) == len(predictions) == len(families) and len(targets) > 0,
            'Metric arrays must be nonempty and aligned')
    weights = family_weights(families)
    truth, pred = np.asarray(targets), np.asarray(predictions)
    result = {'rows': len(targets), 'families': len(set(families))}
    if kind == 'regression':
        error = pred.astype(float) - truth.astype(float)
        require(np.isfinite(error).all(), 'Nonfinite metric input')
        result.update(mae=float(weights @ np.abs(error)), rmse=float(np.sqrt(weights @ (error ** 2))))
    else:
        require(kind == 'classification', 'Unknown metric kind')
        result['accuracy'] = float(weights @ (truth == pred))
        result['per_class'] = {}
        for label in sorted(set(classes) | set(targets) | set(predictions)):
            actual, selected = truth == label, pred == label
            tp = float(weights @ (actual & selected))
            support, called = float(weights @ actual), float(weights @ selected)
            precision = tp / called if called else None
            recall = tp / support if support else None
            result['per_class'][label] = {
                'support': int(actual.sum()), 'family_mass': support,
                'precision': precision, 'recall': recall,
                'f1': 2 * tp / (support + called) if support + called else None}
    return result


def _select(rows, budgets, seed):
    require(type(budgets) is dict and set(budgets) == {'train', 'validation', 'test'},
            'Explicit train/validation/test budgets required')
    selected = {}
    for split, budget in budgets.items():
        integer(budget, 1)
        eligible = [r for r in rows if r['split'] == split and r['present']]
        require(len(eligible) >= budget, 'Insufficient present examples for ' + split)
        eligible.sort(key=lambda r: (hashlib.sha256(encode_json([seed, r['id']])).digest(), _id(r['id'])))
        # Canonical fit order makes source permutations numerically invariant.
        selected[split] = sorted(eligible[:budget], key=lambda r: _id(r['id']))
    return selected


def _features(rows, name, projection):
    field = 'embedding' if name == 'external' else 'raw'
    values = np.array([r[field] for r in rows], dtype=float)
    return values @ projection if name == 'random_projection' else values


def _standardize(x, weights):
    mean = weights @ x
    scale = np.sqrt(weights @ ((x - mean) ** 2))
    scale[scale < 1e-12] = 1.0
    return mean, scale


def _design(x, mean, scale):
    return np.column_stack(((x - mean) / scale, np.ones(len(x))))


def _predict(model, rows, projection, classes):
    if model['name'] == 'constant':
        return [model['value']] * len(rows)
    x = _features(rows, model['name'], projection)
    scores = _design(x, np.asarray(model['mean']), np.asarray(model['scale'])) @ np.asarray(model['coefficients'])
    return [classes[i] for i in np.argmax(scores, axis=1)] if classes else scores[:, 0].tolist()


def fit_reference(task, embeddings, *, budgets, seed=17, alphas=(0.0, 0.01, 1.0), projection_dim=8):
    """Select once by IDs/masks, fit train only, choose ridge penalty on validation.

    The returned fit contains no test labels or metrics. Calling evaluate_final
    is the explicit opening of test. All four controls share exact selected IDs.
    """
    integer(seed)
    integer(projection_dim, 1)
    require(bool(alphas) and all(type(a) in (int, float) and np.isfinite(a) and a >= 0 for a in alphas),
            'Ridge penalties must be finite nonnegative numbers')
    data = task.to_json()
    rows = align_embeddings(task, embeddings)
    require({row['split'] for row in rows} <= {'train', 'validation', 'test'}, 'Unknown reference split')
    selected = _select(rows, budgets, seed)
    train, validation = selected['train'], selected['validation']
    weights = family_weights([r['family_id'] for r in train])
    labels = [r['target'] for r in train]
    classes = sorted(set(labels)) if data['kind'] == 'classification' else []
    y = (np.array([[float(label == c) for c in classes] for label in labels]) if classes
         else np.array(labels, dtype=float)[:, None])
    projection = np.random.default_rng(seed).normal(
        size=(len(train[0]['raw']), projection_dim)) / np.sqrt(projection_dim)
    models = {}
    for name in CONTROLS:
        if name == 'constant':
            value = classes[int(np.argmax(weights @ y))] if classes else float(weights @ y[:, 0])
            models[name] = {'name': name, 'value': value, 'parameters': 1}
            continue
        x = _features(train, name, projection)
        mean, scale = _standardize(x, weights)
        design = _design(x, mean, scale)
        penalty = np.eye(design.shape[1])
        penalty[-1, -1] = 0.0  # Unpenalized intercept.
        best = None
        for alpha in sorted(set(alphas)):
            # Augmented least squares handles singular zero-penalty fixtures.
            lhs = np.vstack((design * np.sqrt(weights[:, None]), np.sqrt(alpha) * penalty))
            rhs = np.vstack((y * np.sqrt(weights[:, None]), np.zeros((len(penalty), y.shape[1]))))
            coefficients = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
            candidate = {'name': name, 'alpha': alpha, 'mean': mean.tolist(), 'scale': scale.tolist(),
                         'coefficients': coefficients.tolist(), 'parameters': int(coefficients.size)}
            score = metrics(data['kind'], [r['target'] for r in validation],
                            _predict(candidate, validation, projection, classes),
                            [r['family_id'] for r in validation], classes)
            loss = 1 - score['accuracy'] if classes else score['rmse']
            if best is None or loss < best[0]:
                best = loss, candidate
        models[name] = best[1]
    train_scale = None
    if not classes:
        values = y[:, 0]
        mean = float(weights @ values)
        train_scale = {'unit': data['unit'], 'mean': mean,
                       'std': float(np.sqrt(weights @ ((values - mean) ** 2))),
                       'min': float(values.min()), 'max': float(values.max())}
    return {'schema_version': 1, 'seed': seed, 'budgets': dict(budgets),
            'encoder': {key: embeddings[key] for key in ('encoder_id', 'encoder_revision', 'observation_view')},
            'classes': classes, 'train_target_scale': train_scale,
            'projection': projection.tolist(), 'models': models,
            'selected_ids': {split: [r['id'] for r in values] for split, values in selected.items()}}


def evaluate_final(task, embeddings, fit):
    """Open held-out test once after all fitting/protocol choices are frozen."""
    data = task.to_json()
    rows = align_embeddings(task, embeddings)
    require(fit['encoder'] == {key: embeddings[key] for key in fit['encoder']}, 'Encoder identity changed')
    index = {_id(r['id']): r for r in rows}
    result = {'seed': fit['seed'], 'target': data['spec'], 'kind': data['kind'],
              'encoder': fit['encoder'], 'train_target_scale': fit['train_target_scale'],
              'literal_in_observation': data['literal_in_observation'],
              'literal_in_input_profile': data['literal_in_input_profile'], 'controls': {},
              'interpretation': 'Predictability under this protocol; no causal or general-understanding claim.'}
    projection = np.asarray(fit['projection'])
    for name in CONTROLS:
        result['controls'][name] = {'parameters': fit['models'][name]['parameters'],
                                    'alpha': fit['models'][name].get('alpha'), 'splits': {}}
        for split, ids in fit['selected_ids'].items():
            selected = [index[_id(identity)] for identity in ids]
            require(len({_id(r['id']) for r in selected}) == fit['budgets'][split] and
                    all(r['split'] == split and r['present'] for r in selected),
                    'Selected IDs violate split, mask or budget')
            predictions = _predict(fit['models'][name], selected, projection, fit['classes'])
            families = [r['family_id'] for r in selected]
            report = metrics(data['kind'], [r['target'] for r in selected], predictions, families, fit['classes'])
            report['per_family'] = {}
            for family in sorted(set(families)):
                indices = [i for i, f in enumerate(families) if f == family]
                report['per_family'][family] = metrics(
                    data['kind'], [selected[i]['target'] for i in indices],
                    [predictions[i] for i in indices], [family] * len(indices), fit['classes'])
            result['controls'][name]['splits'][split] = report
    return result


def synthetic_fixture(seed=17, families=9, decisions=12, classification=False, random_target=False):
    """Inert synthetic boundaries, not simulated trajectories or trained embeddings.

    Home score is the raw signal; target is synthetic rerolls or player.up.
    No fixtures, arrays, encoder weights or reports are written by this helper.
    """
    from botbowl.lab.channels import InputProfile
    from botbowl.lab.evaluation.oracle import EvaluationRecord, label_spec
    from botbowl.lab.evaluation.probes import ProbeSpec, export_probe_task, sample_id
    from botbowl.lab.rules import RulesDescriptor
    from botbowl.lab.splits import build_split_manifest
    from botbowl.lab.timeline import TimelineContext
    from botbowl.lab.windows import WindowSpecV1

    rng = np.random.default_rng(seed)
    profile = InputProfile('custom', (('primary.teams[].score', 'integer'),))
    window_spec = WindowSpecV1(1, 1, profile=profile)
    label = 'player.up' if classification else 'team.rerolls'
    entity = 'home:0' if classification else 'home'
    spec = ProbeSpec(label, entity)
    sources, payloads = [], {}
    for f in range(families):
        episode = 'synthetic-' + str(f)
        signal = rng.integers(0, 10, size=decisions + 1).tolist()
        noise = rng.integers(0, 10, size=decisions + 1).tolist()
        targets = [bool((n if random_target else x) >= 5) if classification else
                   (n if random_target else 2 * x + 3) for x, n in zip(signal, noise)]
        payloads[episode] = signal, targets
        sources.append({'schema_version': 1, 'source_id': episode, 'source_version': 'synthetic-probe-v1',
                        'kind': 'episode', 'episode_id': episode, 'origin_family_id': episode,
                        'content_digest': 'sha256:' + hashlib.sha256(encode_json([signal, targets])).hexdigest(),
                        'semantic_origin_fingerprint': None, 'variant_group_id': None,
                        'relationships': [], 'groups': {'scenario': [], 'policy': []}})
    splits = build_split_manifest(sources, proportions={'train': 0.6, 'validation': 0.2, 'test': 0.2},
                                  seed=seed, split_version='synthetic-probe-v1')
    manifest = splits.to_json()
    # Explicit synthetic identity, never a claim about a loaded engine instance.
    rules = RulesDescriptor('synthetic', '1', 'synthetic', 'synthetic', 'sha256:' + '0' * 64,
                            'python', '1').to_json()
    windows, records, embedding_rows = [], [], []
    for entry in manifest['sources']:
        episode, family = entry['source']['episode_id'], entry['family_id']
        signal, targets = payloads[episode]
        for d, value in enumerate(targets):
            ctx = TimelineContext(episode, 'root', decision_seq=d).to_json()
            records.append(EvaluationRecord.from_json({
                'schema_version': 1, 'label': label_spec(label).to_json(), 'entity_id': entity,
                'related_entity_id': None, 'context': ctx, 'available_at': ctx, 'rules': rules,
                'status': 'available', 'value': value, 'unavailability': None, 'provenance': None}))
        for d in range(decisions):
            cutoff = TimelineContext(episode, 'root', decision_seq=d).to_json()
            future = TimelineContext(episode, 'root', decision_seq=d + 1).to_json()
            window = {'inputs': {'observations': [{'primary.teams[].score': [signal[d], 0]}], 'presence': [True]},
                      'targets': {'observations': [{'primary.teams[].score': [signal[d + 1], 0]}], 'presence': [True]},
                      'origin': {'source_id': episode, 'episode_id': episode, 'source_family': episode},
                      'split': manifest['assignments'][family],
                      'metadata': {'spec': window_spec.to_json(), 'cutoff': cutoff, 'branch_id': 'root',
                                   'family_id': family, 'split_version': manifest['split_version'],
                                   'history': [{'observation_id': d + 1, 'available_at': cutoff}],
                                   'target_observations': [{'observation_id': d + 2, 'available_at': future}]}}
            windows.append(window)
            embedding_rows.append({'id': sample_id(window, entity),
                                   'values': [float(signal[d]), float(signal[d] * 3 + 1)]})
    task = export_probe_task(windows, records, spec, split_manifest=splits,
                             raw_features=[{'field': 'primary.teams[].score', 'path': [0]}])
    embeddings = {'schema_version': 1, 'encoder_id': 'synthetic-affine-v1',
                  'encoder_revision': 'sha256:' + hashlib.sha256(b'synthetic-affine-v1:x,3*x+1').hexdigest(),
                  'observation_view': 'synthetic numeric home score; no trained encoder',
                  'window_spec': window_spec.to_json(), 'rows': embedding_rows}
    return task, embeddings


def main():
    from pathlib import Path
    from botbowl.lab.records import decode_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, help='ProbeTask JSON; requires --embeddings')
    parser.add_argument('--embeddings', type=Path, help='Plain JSON arrays; no pickle or model files')
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--budget', type=int, default=8, help='Same requested count in each split and control')
    args = parser.parse_args()
    if (args.task is None) != (args.embeddings is None):
        parser.error('--task and --embeddings must be supplied together')
    if args.task is None:
        task, embeddings = synthetic_fixture(args.seed)
    else:
        task = ProbeTask(decode_json(args.task.read_bytes()))
        embeddings = decode_json(args.embeddings.read_bytes())
    fit = fit_reference(task, embeddings, budgets={s: args.budget for s in ('train', 'validation', 'test')}, seed=args.seed)
    print(json.dumps(evaluate_final(task, embeddings, fit), sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
