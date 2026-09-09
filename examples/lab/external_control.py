"""Installed local control: python /path/to/external_control.py --max-decisions 12."""
import argparse
import json

from botbowl.lab import SessionConfig, SimulationSession
from botbowl.lab.channels import project_inputs
from botbowl.lab.generate import ReferencePolicy
from botbowl.lab.randomness import SeedSpec


def run(seed=17, max_decisions=12):
    session = SimulationSession(
        SessionConfig(size=3, max_decisions=max_decisions, max_steps=1000),
        SeedSpec(seed, 'external-control', 'engine', 'example-v1'))
    policies = {side: ReferencePolicy('scripted', SeedSpec(
        seed, 'external-control', 'policy-' + side, 'scripted-v1'))
        for side in ('home', 'away')}
    decisions = []
    try:
        result = session.observe()
        while not result.terminated and not result.truncated:
            legal = session.legal_actions()
            actor = result.next_actor
            if legal.actor_id != actor:
                raise ValueError('Session actor and legal actions disagree')
            features = project_inputs({'primary': result.primary})['features']
            action = policies[actor].act(features, legal)
            decisions.append({'actor': actor, 'action': action.to_json()})
            result = session.step(action, legal.state_revision)
        return {'decisions': decisions, 'terminated': result.terminated,
                'truncated': result.truncated, 'end_reason': result.end_reason}
    finally:
        for policy in policies.values():
            policy.close()
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--max-decisions', type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(run(args.seed, args.max_decisions), sort_keys=True))


if __name__ == '__main__':
    main()
