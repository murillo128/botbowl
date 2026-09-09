"""CPU forecast demo and SIM-07 continuation smoke: python -m examples.lab.temporal."""
import json
from tempfile import TemporaryDirectory

import botbowl as bb
from botbowl.lab.evaluation.oracle import EvaluationContext, EvaluationOracle, LabelRequest
from botbowl.lab.evaluation.temporal import (TemporalTask, prepare_case, make_forecast,
                                             evaluate_forecasts, forecast_request)
from botbowl.lab.recording import EpisodeRecorder, EpisodeReader
from botbowl.lab.rollouts import ContinuationPolicy, Horizon, SamplePlan, estimate_continuations
from botbowl.lab.rules import RulesDescriptor
from botbowl.lab.snapshots import capture_snapshot
from botbowl.lab.timeline import Timeline
from botbowl.lab.actions import ActionControl, ActionV1
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1, iter_windows


def first_factory(config, rng):
    return lambda observation, legal: legal.actions[0]


POLICY = {'name': 'first-legal', 'version': 'v1', 'config': {}}


def run_demo(destination):
    config = bb.load_config('gym-1')
    config.pathfinding_enabled = False
    game = bb.create_game(config, size=1, seed=17, control='external')
    source = bb.create_game(config, size=1, seed=17, control='external')
    Timeline(source, episode_id='temporal-source')
    recorder = EpisodeRecorder(game, destination, 'temporal', episode_id='temporal',
        source_family='temporal-demo', scenario_id='gym-1',
        policies={s: {'id': 'first-legal', 'version': 'v1'} for s in ('home', 'away')},
        seed_plan={'schema_version': 1, 'algorithm': 'legacy-numpy-seed', 'sources': {'engine': 17}})
    records = []
    task = TemporalTask('team.score', 'home')
    try:
        first = ActionControl(source).legal_actions().actions[0]
        continuations = estimate_continuations(capture_snapshot(source), first,
            ContinuationPolicy(**POLICY, factory=first_factory), Horizon('decisions', 2), SamplePlan((0, 1), 17))
        # Reuse a simulator continuation's primitive command prefix. No command
        # or simulator outcome is passed to the artificial passive predictor.
        actions = continuations['records'][0]['actions']
        for raw in actions:
            recorder.advance(recorder.actions.request(ActionV1.from_json(raw)))
            captured = EvaluationContext.capture(game, recorder.timeline._entities, recorder.timeline.context,
                [LabelRequest(task.name, task.entity_id)], rules=RulesDescriptor(**recorder._provenance['rules']))
            records.extend(EvaluationOracle().evaluate(captured))
        recorder.finish(truncation_reason='demo_limit')
        reader = EpisodeReader(destination, 'temporal')
        split = build_split_manifest([origin_from_episode(reader.manifest)], proportions={'test': 1},
                                     seed=17, split_version='temporal-v1')
        windows = list(iter_windows(reader, WindowSpecV1(2, 2), split_manifest=split))
        cases, forecasts = [], []
        for protocol in ('teacher_forced', 'autonomous'):
            for window in windows:
                case = prepare_case(window, split_manifest=split, protocol=protocol,
                    continuation_policy=POLICY, budget=8, horizons=(1, 2),
                    initial_window=windows[0] if protocol == 'autonomous' else None)
                inputs = forecast_request(case)['inputs']
                value = inputs['observations'][-1]['primary.teams[].score'][0]
                forecasts.append(make_forecast(case, [
                    {'horizon': h, 'task': task.to_json(), 'value': value} for h in (1, 2)],
                    model_id='artificial', model_revision='v1'))
                cases.append(case)
        report = evaluate_forecasts(cases, forecasts, [task], models=[('artificial', 'v1')], records=records)
        return {'continuation_counts': continuations['counts'], 'evaluation': report}
    finally:
        recorder.close()
        game.close()
        source.close()


def main():
    with TemporaryDirectory(prefix='botbowl-temporal-') as destination:
        result = run_demo(destination)
        print(json.dumps({'continuation_counts': result['continuation_counts'],
                          'case_count': result['evaluation']['case_count'],
                          'metrics': result['evaluation']['metrics']}, sort_keys=True))


if __name__ == '__main__':
    main()
