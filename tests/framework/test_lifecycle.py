"""Natural completion, bot failures and administrative truncation are distinct."""
from types import SimpleNamespace
import pickle

import pytest
import botbowl as bb
from tests.baseline import progress_action
from tests.framework.test_external_control import fresh, Spy
from tests.framework.test_forced_action import FakeTime


class PolicySpy(Spy):
    def __init__(self, name, failure=None, late=False):
        super().__init__(name)
        self.failure = failure
        self.late = late
        self.calls = 0

    def act(self, game):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        action = progress_action(game)
        if self.late:
            game.time_source.now += 1000
        return action


@pytest.mark.parametrize("phase", ["new_game", "act"])
def test_bot_crash_preserves_original_cause_without_result(phase):
    game = fresh(external=False)
    error = RuntimeError("bot failure detail")
    home = PolicySpy("home", failure=error if phase == "act" else None)
    if phase == "new_game":
        def fail(*args):
            raise error
        home.new_game = fail
    game.home_agent = home
    game.away_agent = PolicySpy("away", failure=error if phase == "act" else None)
    with pytest.raises(RuntimeError) as caught:
        game.init()
    assert caught.value is error
    assert not game.state.game_over and game.end_time is None
    assert game.get_winner() is None


def test_late_bot_action_is_discarded_at_new_decision_boundary():
    game = fresh(external=False)
    game.time_source = FakeTime()
    game.config.competition_mode = True
    game.home_agent = PolicySpy("home", late=True)
    game.away_agent = PolicySpy("away", late=True)
    game.init(max_steps=1000)
    assert game.state.game_over
    assert game.home_agent.calls and game.away_agent.calls
    assert game.home_agent.ends == game.away_agent.ends == 1
    assert game.get_winner() is None


def test_finalization_attempts_all_callbacks_and_replay_once():
    game = fresh()
    game.init()
    error = RuntimeError("home finalizer failed")
    other_error = ValueError("away finalizer failed")
    calls = []

    def callback(name, failure):
        def finish(game):
            calls.append(name)
            raise failure
        return finish

    game.home_agent.end_game = callback("home", error)
    game.away_agent.end_game = callback("away", other_error)
    game.replay = SimpleNamespace(record_step=lambda game: calls.append("record"),
                                  dump=lambda game: calls.append("dump"))
    game.state.game_over = True
    with pytest.raises(RuntimeError) as caught:
        game._end_game()
    assert caught.value is error
    assert game.finalization_errors == [error, other_error]
    assert calls == ["home", "away", "record", "dump"]
    stamp = game.end_time
    game._end_game()
    game.step()
    game.refresh()
    game.init()
    assert calls == ["home", "away", "record", "dump"]
    assert stamp is not None and game.end_time == stamp


def test_repeated_terminal_calls_preserve_game():
    game = fresh()
    game.init()
    driver = bb.PolicyDriver(game, {t.team_id: progress_action for t in game.state.teams})
    assert driver.run(max_steps=1000).terminal
    before = pickle.dumps(game)
    game.refresh()
    game._end_game()
    game.advance()
    game.step()
    game.init()
    assert pickle.dumps(game) == before
    assert game.home_agent.ends == game.away_agent.ends == 1


def test_policy_decision_pause_is_distinct_from_administrative_budget():
    game = fresh()
    game.init()
    driver = bb.PolicyDriver(game, {t.team_id: progress_action for t in game.state.teams})
    assert not driver.run(max_decisions=1).terminal
    with pytest.raises(bb.GameTruncatedError, match="execution budget exhausted") as caught:
        driver.run(max_steps=0)
    assert caught.value.code == "step_budget"
    assert not game.state.game_over and game.end_time is None
    assert game.home_agent.ends == game.away_agent.ends == 0
    assert game.get_winner() is None
    assert driver.run(max_steps=1000).terminal  # A caller may explicitly resume.


def test_automatic_no_progress_is_bounded():
    game = fresh()
    game.init()
    proc = game.get_procedure()
    proc.step = lambda action: False
    proc.available_actions = lambda: []
    game.state.available_actions = []
    with pytest.raises(bb.GameTruncatedError, match="procedures="):
        game.advance(max_steps=4)
    assert not game.state.game_over and game.end_time is None


def test_competition_retries_share_one_budget(capsys):
    game = fresh(external=False)
    game.home_agent = PolicySpy("home")
    game.away_agent = PolicySpy("away")
    game.home_agent.act = game.away_agent.act = lambda game: bb.Action(bb.ActionType.USE_APOTHECARY)
    comp = bb.Competition(game.home_agent, game.away_agent, game.state.home_team,
                          game.state.away_team, game.config, game.ruleset, game.arena,
                          max_steps=30)
    with pytest.raises(bb.GameTruncatedError):
        comp._run_game(game)
    assert comp.results is None and not game.state.game_over
    assert game.end_time is None and game.get_winner() is None
    assert "Action type is not currently available." in capsys.readouterr().out


def test_replay_dump_is_attempted_after_record_failure():
    game = fresh()
    game.init()
    failure = RuntimeError('record failed')
    calls = []

    def record(game):
        calls.append('record')
        raise failure

    game.replay = SimpleNamespace(record_step=record, dump=lambda game: calls.append('dump'))
    game.state.game_over = True
    with pytest.raises(RuntimeError) as error:
        game._end_game()
    assert error.value is failure and game.finalization_errors == [failure]
    game._end_game()
    assert calls == ['record', 'dump']
    assert game.home_agent.ends == game.away_agent.ends == 1


def test_competition_rechecks_timeout_after_invalid_action_retry():
    game = fresh(external=False)
    game.time_source = FakeTime()
    game.home_agent = PolicySpy('home')
    game.away_agent = PolicySpy('away')
    submitted = []
    one_step = game._one_step

    def capture(action):
        if action is not None:
            submitted.append(action.action_type)
        return one_step(action)

    game._one_step = capture
    attempts = []

    def retry(game):
        attempts.append(game.actor)
        if len(attempts) == 1:
            return bb.Action(bb.ActionType.USE_APOTHECARY)
        if len(attempts) == 2:
            game.time_source.now += 1000
            return bb.Action(bb.ActionType.TAILS)
        return progress_action(game)

    game.away_agent.act = retry
    comp = bb.Competition(game.home_agent, game.away_agent, game.state.home_team,
                          game.state.away_team, game.config, game.ruleset, game.arena,
                          max_steps=1000)
    comp._run_game(game)
    assert game.state.game_over
    assert bb.ActionType.TAILS not in submitted
    assert bb.ActionType.HEADS in submitted
