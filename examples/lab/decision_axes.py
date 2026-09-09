"""CPU-only finite tables and a small engine fixture; no training."""
from dataclasses import asdict
import json

import botbowl as bb
from botbowl.lab.actions import ActionControl
from botbowl.lab.evaluation import EvaluationContext, EvaluationOracle, LabelRequest
from botbowl.lab.evaluation.decision_axes import (
    AssessmentContext, assess_outcome, evaluate_decision, exact_reference,
    observed_impact, oracle_impact, reference_from_continuations, semantic_action_id,
)
from botbowl.lab.observations import ObservationControl
from botbowl.lab.rollouts import ContinuationPolicy, Horizon, SamplePlan, estimate_continuations
from botbowl.lab.rules import describe_rules
from botbowl.lab.snapshots import capture_snapshot
from botbowl.lab.timeline import Timeline


def synthetic_demo():
    context = AssessmentContext({'state_id': 'finite-state', 'model': {'id': 'finite-table', 'version': 'v1'},
        'policy': {'name': 'stop', 'version': 'v1', 'config': {}}, 'horizon': asdict(Horizon('decisions', 1)), 'budget': 2})
    utility = {'id': 'score', 'version': 'v1', 'definition': 'Toy home score gain; maximize',
               'unit': 'touchdown', 'values': {'score': 10., 'nothing': 0.}}
    reports = {}
    for name, chosen, other, outcome, delta in (
        ('rare_no_impact', .99, .8, 'nothing', 0),
        ('expected_valuable', .99, .8, 'score', 10),
        ('bad_decision_lucky_outcome', .01, .8, 'score', 10),
    ):
        refs = [exact_reference(context, action, 'toy_score', {'score': p, 'nothing': 1-p},
                                origin={'description': 'Synthetic exhaustive two-outcome table'})
                for action, p in [('chosen', chosen), ('alternative', other)]]
        quality = evaluate_decision('chosen', ['chosen', 'alternative'], refs, utility=utility,
                                    origin='Frozen synthetic experiment before outcome observation')
        impact = observed_impact('finite-state', name, 'home_score', 'touchdown', 0, delta,
                                 origin='Synthetic before/after state fixture')
        reports[name] = assess_outcome(quality, name, outcome, event=[outcome], impacts=[impact]).to_json()
    return reports


def first_factory(config, rng):
    return lambda observation, legal: legal.actions[0]


def engine_demo():
    config = bb.load_config('gym-1')
    config.pathfinding_enabled = False
    game = bb.create_game(config, size=1, seed=17, control='external')
    try:
        timeline = Timeline(game, episode_id='decision-axes-demo')
        actions, observations = ActionControl(game), ObservationControl(game)
        rules = describe_rules(config, game.ruleset, game.arena, game.state.home_team, game.state.away_team)
        requests = [LabelRequest(name, 'home') for name in ('team.score', 'team.rerolls')]

        def facts():
            return EvaluationOracle().evaluate(EvaluationContext.capture(game, observations,
                timeline.context, requests, rules=rules))

        before = facts()
        legal = actions.legal_actions()
        selected = legal.actions[0]
        report = estimate_continuations(capture_snapshot(game), selected,
            ContinuationPolicy('first-legal', 'v1', {}, first_factory), Horizon('decisions', 1),
            SamplePlan((0, 1, 2, 3), 42, experiment_id='decision-axes-demo'))
        reference = reference_from_continuations(report, model_id='botbowl-continuations',
            model_version=rules.engine_version, observable='touchdown', categories={'yes': True, 'no': False})
        quality = evaluate_decision(semantic_action_id(selected), [semantic_action_id(a) for a in legal.actions],
            [reference], utility={'id': 'any-score', 'version': 'v1', 'definition': 'Indicator of any touchdown',
                                 'unit': 'indicator', 'values': {'yes': 1., 'no': 0.}},
            origin='Engine first-action evaluation frozen before actual advancement')
        game.advance(actions.decode(actions.request(selected)))
        after = facts()
        state_id = reference.to_json()['context']['state_id']
        impacts = [oracle_impact(state_id, 'actual-start-game', a, b) for a, b in zip(before, after)]
        return assess_outcome(quality, 'actual-start-game', 'no', event=['no'], impacts=impacts).to_json()
    finally:
        game.close()


def run_demo():
    return {'synthetic': synthetic_demo(), 'engine': engine_demo()}


if __name__ == '__main__':
    print(json.dumps(run_demo(), sort_keys=True, allow_nan=False))
