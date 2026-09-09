"""Descriptive coverage of recorded decisions and public timeline events."""
from collections import Counter

from botbowl.core.table import OutcomeType


# Instrumentation support, not a claim that each event is reachable in a recipe.
SUPPORTED_EVENTS = tuple(sorted(
    ['report:' + name for name in OutcomeType.__members__] +
    ['half_started', 'round_started', 'drive_started', 'drive_ended',
     'team_turn_started', 'team_turn_ended', 'activation_started', 'activation_ended']))


def option_counts(legal, action):
    """Count complete semantic choices, never just their action types."""
    if isinstance(action, dict):
        from .actions import ActionV1
        action = ActionV1.from_json(action)
    if action not in legal.actions or action.actor_id != legal.actor_id:
        raise ValueError('Selected action is not a legal choice')
    return {'actor': legal.actor_id,
            'available': dict(sorted(Counter(a.type for a in legal.actions).items())),
            'selected': action.type}


def episode_coverage(options, events, *, decision_budget, max_steps, horizon=None):
    """Fresh counters per episode, including zero-decision fragments.

    Availability = decisions with type / all seat decisions; selection given
    opportunity = selections / decisions offering type. Event incidence is
    aggregated over episodes, never over just episodes with the event.
    """
    seats = {}
    for side in ('home', 'away'):
        rows = [row for row in options if row['actor'] == side]
        available, offered, selected = Counter(), Counter(), Counter()
        for row in rows:
            counts = row['available']
            if (not counts or any(type(n) is not int or n <= 0 for n in counts.values()) or
                    row['selected'] not in counts):
                raise ValueError('Invalid option counters')
            available.update(counts)
            offered.update(counts.keys())
            selected[row['selected']] += 1
        decisions = len(rows)
        total = sum(available.values())
        seats[side] = {
            'decisions': decisions, 'available_actions': total,
            'mean_available_actions': total / decisions if decisions else None,
            'types': {name: {
                'available_actions': available[name], 'offered_decisions': offered[name],
                'selected': selected[name], 'availability_rate': offered[name] / decisions,
                'selection_rate': selected[name] / decisions,
                'selection_given_opportunity': selected[name] / offered[name],
            } for name in sorted(offered)},
        }
    if sum(seat['decisions'] for seat in seats.values()) != len(options):
        raise ValueError('Unknown decision actor')
    observed = Counter(('report:' + row['data']['outcome_type'])
                       if row['kind'] == 'report' else row['kind'] for row in events)
    if set(observed) - set(SUPPORTED_EVENTS):
        raise ValueError('Unknown event instrumentation')
    return {'schema_version': 1, 'budget': {'decisions': decision_budget,
            'automatic_steps_per_decision': max_steps, 'horizon': horizon},
            'decisions': len(options), 'sides': seats,
            'events': dict(sorted(observed.items()))}


def coverage_report(episodes):
    """Aggregate confirmed generator episode summaries; no win-rate threshold.

    Each row must carry coverage, scenario and both versioned policy specs.
    Overlapping/replayed samples must be excluded by the caller.
    """
    episodes = list(episodes)
    occurrences, incidence = Counter(), Counter()
    groups = {}
    for episode in episodes:
        coverage = episode['coverage']
        occurrences.update(coverage['events'])
        incidence.update(coverage['events'].keys())
        for side, spec in episode['policy_specs'].items():
            key = (episode['scenario'], side, spec['name'], spec['version'], spec['config_digest'])
            group = groups.setdefault(key, {'episodes': 0, 'decisions': 0,
                                            'available_actions': 0, 'selected': Counter()})
            group['episodes'] += 1
            seat = coverage['sides'][side]
            group['decisions'] += seat['decisions']
            group['available_actions'] += seat['available_actions']
            group['selected'].update({name: row['selected'] for name, row in seat['types'].items()})
    n = len(episodes)
    return {'schema_version': 1, 'sample_episodes': n,
            'sample_decisions': sum(e['coverage']['decisions'] for e in episodes),
            'budgets': [e['coverage']['budget'] for e in episodes],
            'supported_events': list(SUPPORTED_EVENTS), 'reached_events': sorted(occurrences),
            'unexercised_events': sorted(set(SUPPORTED_EVENTS) - set(occurrences)),
            'events': {name: {'occurrences': occurrences[name], 'episodes': incidence[name],
                             'episode_rate': incidence[name] / n if n else None}
                       for name in SUPPORTED_EVENTS},
            'groups': [{'scenario': key[0], 'side': key[1], 'policy_id': key[2],
                        'version': key[3], 'config_digest': key[4], **value,
                        'selected': dict(sorted(value['selected'].items()))}
                       for key, value in sorted(groups.items())],
            'limitations': ['Unexercised does not mean impossible.',
                            'Instrumentation support does not imply recipe reachability.',
                            'Small synthetic samples and scripted styles do not represent human play.',
                            'Risk preferences are action-type heuristics, not calibrated probabilities.']}
