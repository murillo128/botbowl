"""Explicit separate job: pytest tests/issue25/broad_corpus.py -q -s.

Fixed inputs, no retries or failing-seed filters. Per-game limits are enforced
by Probe; use the documented external job timeout as a final process guard.
"""
import json

import pytest

from tests.interaction_corpus import generated, replay
from tests.interaction_scenarios import injury, movement, negatrait, push


@pytest.mark.parametrize('size', [1, 3, 5, 7, 11])
@pytest.mark.parametrize('seed', [0, 3, 17])
@pytest.mark.parametrize('pathfinding', [False, True])
def test_generated_broad(size, seed, pathfinding):
    probe = generated(size=size, seed=seed, pathfinding=pathfinding, turns=2)
    assert probe.game.state.game_over
    assert replay(probe.reproduction()).result() == probe.result()
    print(json.dumps(probe.result(), sort_keys=True))


@pytest.mark.parametrize('seed', [3, 17])
@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('scenario,variant,pathfinding', [
    (movement, 'team', False), (movement, 'sure_feet', True), (movement, 'decline', True),
    (push, 'accept', False), (push, 'decline', False),
    (injury, 'use', False), (injury, 'decline', False),
    (negatrait, 'success', False), (negatrait, 'failure', False)])
def test_mechanisms_broad(seed, side, scenario, variant, pathfinding):
    kwargs = dict(seed=seed, side=side, variant=variant)
    if scenario is movement:
        kwargs['pathfinding'] = pathfinding
    probe = scenario(**kwargs)
    print(json.dumps(probe.result(), sort_keys=True))
