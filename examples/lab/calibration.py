"""CPU-only frozen forecasts and disjoint SIM-07 reference/evaluation samples."""
import json
from tempfile import TemporaryDirectory

import botbowl as bb
from botbowl.lab.evaluation.calibration import CalibrationTarget, evaluate_calibration, binary_scores
from botbowl.lab.evaluation.temporal import TemporalTask, prepare_case, make_forecast
from botbowl.lab.recording import EpisodeRecorder, EpisodeReader
from botbowl.lab.rollouts import ContinuationPolicy, Horizon, SamplePlan, estimate_continuations, exact_dice_distribution
from botbowl.lab.snapshots import capture_snapshot
from botbowl.lab.timeline import Timeline
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1, iter_windows


def first_factory(config, rng):
    return lambda observation, legal: legal.actions[0]


POLICY = {'name': 'first-legal', 'version': 'v1', 'config': {}}


def demo_inputs(destination, name='calibration', n=4):
    config = bb.load_config('gym-1')
    config.pathfinding_enabled = False
    game = bb.create_game(config, size=1, seed=17, control='external')
    source = bb.create_game(config, size=1, seed=17, control='external')
    Timeline(source, episode_id=name)
    recorder = EpisodeRecorder(game, destination, name, episode_id=name,
        source_family=name, scenario_id='gym-1',
        policies={s: {'id': 'first-legal', 'version': 'v1'} for s in ('home', 'away')},
        seed_plan={'schema_version': 1, 'algorithm': 'legacy-numpy-seed', 'sources': {'engine': 17}})
    try:
        snapshot = capture_snapshot(source)
        recorder.advance(recorder.actions.request(recorder.actions.legal_actions().actions[0]))
        recorder.finish(truncation_reason='demo_limit')
        reader = EpisodeReader(destination, name)
        split = build_split_manifest([origin_from_episode(reader.manifest)], proportions={'test': 1},
                                     seed=17, split_version='calibration-v1')
        window = next(iter_windows(reader, WindowSpecV1(1, 1), split_manifest=split))
        case = prepare_case(window, split_manifest=split, protocol='teacher_forced',
                            continuation_policy=POLICY, budget=8, horizons=(1,))
        target = CalibrationTarget(TemporalTask('event.TOUCHDOWN'), 'binary')
        # Frozen artificial prediction: a START_GAME decision cannot score.
        forecast = make_forecast(case, [{'horizon': 1, 'task': target.task.to_json(),
            'value': {'kind': 'binary', 'probability': 0.}}], model_id='artificial', model_revision='v1')
        pair = {}
        for role, ids in [('reference', tuple(range(n))), ('evaluation', tuple(range(n, 2 * n)))]:
            pair[role] = estimate_continuations(snapshot, None, ContinuationPolicy(**POLICY, factory=first_factory),
                Horizon('decisions', 1, max_decisions=8), SamplePlan(ids, 721, experiment_id=name))
        return case, forecast, target, pair
    finally:
        recorder.close()
        game.close()
        source.close()


def run_demo(destination):
    case, forecast, target, pair = demo_inputs(destination)
    report = evaluate_calibration([case], [forecast], [target], models=[('artificial', 'v1')],
                                  continuations={(case['case_id'], 1): pair})
    # Exact enumeration has no Monte Carlo error. This raw D6 event is distinct
    # from a tactical game continuation; it checks the scoring convention.
    dice = exact_dice_distribution('D6')
    probability = sum(r['probability'] for r in dice['outcomes'] if r['faces'][0] >= 2)
    expected = sum(r['probability'] * binary_scores(probability, r['faces'][0] >= 2)['brier']
                   for r in dice['outcomes'])
    return {'calibration': report, 'exact_d6_at_least_two': {
        'probability': probability, 'expected_brier': expected, 'monte_carlo_error': 0.}}


if __name__ == '__main__':
    with TemporaryDirectory(prefix='botbowl-calibration-') as destination:
        print(json.dumps(run_demo(destination), sort_keys=True, allow_nan=False))
