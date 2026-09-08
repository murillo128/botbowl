"""Replay navigation preserves every state when recorded actions leave gaps."""
import pickle

import pytest

import botbowl as bb
from botbowl.core.procedure import PlaceBall


def assert_complete_navigation(replay):
    indices = sorted(replay.steps)
    for _ in range(2):
        assert replay.first() is replay.steps[indices[0]]
        assert replay.prev() is None
        assert replay.idx == indices[0]
        for index in indices[1:]:
            assert replay.next() is replay.steps[index]
            assert replay.idx == index
        assert replay.next() is None
        assert replay.next() is None
        assert replay.idx == indices[-1]
        assert replay.last() is replay.steps[indices[-1]]
        for index in reversed(indices[:-1]):
            assert replay.prev() is replay.steps[index]
            assert replay.idx == index
        assert replay.prev() is None
        assert replay.prev() is None
        assert replay.idx == indices[0]


@pytest.mark.parametrize("indices", ((0,), (0, 1, 2), (0, 2, 3), (2, 4, 7), (0, 1000, 2000)))
def test_replay_navigation_uses_recorded_state_indices(indices):
    replay = bb.Replay("navigation")
    replay.steps = {index: bb.ReplayStep({"frame": index}, 0) for index in reversed(indices)}
    assert_complete_navigation(replay)


@pytest.mark.parametrize("record_action", (False, True))
def test_replay_without_states_has_no_navigation(record_action):
    replay = bb.Replay("empty")
    if record_action:
        replay.record_action(bb.Action(bb.ActionType.START_GAME))
    before = replay.idx
    for navigate in (replay.first, replay.next, replay.last, replay.prev):
        assert navigate() is None
        assert replay.idx == before


@pytest.mark.parametrize("restore", (False, True))
def test_recorded_kick_replay_reaches_every_state(restore):
    config = bb.load_config("gym-3")
    config.kick_off_table = False
    rules = bb.load_rule_set(config.ruleset)
    teams = [bb.load_team_by_filename("human", rules, board_size=3) for _ in range(2)]
    game = bb.Game("recorded-kick", *teams, bb.Agent("home", human=True),
                   bb.Agent("away", human=True), config, seed=0, record=True)
    game.init()
    game.step(bb.Action(bb.ActionType.START_GAME))
    game.step(bb.Action(bb.ActionType.HEADS))
    game.step(bb.Action(bb.ActionType.KICK))
    for _ in range(2):
        for action in game.get_procedure().formations[0].actions(game, game.active_team):
            game.step(action)
        game.step(bb.Action(bb.ActionType.END_SETUP))
    assert isinstance(game.get_procedure(), PlaceBall)
    positions = game.get_available_actions()[0].positions
    action = bb.Action(bb.ActionType.PLACE_BALL, position=positions[len(positions) // 2])
    action_index = game.replay.idx
    game.step(action)
    for _ in range(2):
        game.step(bb.Action(bb.ActionType.END_TURN))

    replay = pickle.loads(pickle.dumps(game.replay)) if restore else game.replay
    assert replay.actions == {action_index: action.to_json()}
    assert action_index not in replay.steps
    assert len([index for index in replay.steps if index > action_index]) >= 2
    before = replay.to_json()
    assert_complete_navigation(replay)
    assert replay.to_json() == before
