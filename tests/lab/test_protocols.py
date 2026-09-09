"""Public role contracts, ownership, explicit callbacks and legacy compatibility."""
from pathlib import Path
from random import Random
import subprocess
import sys

import numpy as np
import pytest

import botbowl as bb
from botbowl.lab import LegacyBotAdapter
from examples import lab_protocols as example


def test_namespace_preserves_legacy_policy_and_metadata_imports():
    from botbowl import lab
    from botbowl.lab import channels, observations, protocols, rules
    from botbowl.core import Policy

    assert bb.Policy is Policy and bb.Policy is not lab.Policy
    assert channels.InputProfile and observations.ObservationV1 and rules.describe_rules
    for name in protocols.__all__:
        assert getattr(lab, name) is getattr(protocols, name)


def test_all_six_structural_roles_execute_in_example():
    assert example.run() == 1


def test_example_runs_from_another_cwd(tmp_path):
    # CI copies examples beside tests, selecting the installed wheel.
    script = Path(__file__).resolve().parents[2] / "examples/lab_protocols.py"
    package_parent = str(Path(bb.__file__).resolve().parent.parent)
    code = (f"import sys, runpy; sys.path.insert(0, {package_parent!r}); "
            f"runpy.run_path({str(script)!r}, run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True,
                            capture_output=True, text=True, timeout=30)
    assert "all six structural roles executed explicitly" in result.stdout


@pytest.mark.parametrize("module", ["botbowl.lab", "botbowl.lab.protocols", "botbowl.lab.adapters"])
def test_imports_with_optional_modules_unavailable(module, tmp_path):
    package_parent = str(Path(bb.__file__).resolve().parent.parent)
    code = f'''
import importlib.abc
import importlib.metadata
import sys
sys.path.insert(0, {package_parent!r})
forbidden = {{'flask', 'gym', 'gymnasium', 'pygame', 'torch', 'nfl', 'nflenv',
             'nfl_env', 'nfl_data', 'nflgame', 'nfl_data_py', 'docker', 'matplotlib'}}
def blocked(name):
    return any(part.lower() in forbidden or part.lower().startswith('nfl')
               for part in name.split('.')) or name.startswith('botbowl.web')
assert not any(blocked(name) for name in sys.modules)
class MissingExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if blocked(fullname):
            raise AssertionError('unexpected import: ' + fullname)
def no_discovery(*args, **kwargs):
    raise AssertionError('unexpected plugin discovery')
importlib.metadata.entry_points = no_discovery
sys.meta_path.insert(0, MissingExtras())
importlib.import_module({module!r})
assert not any(blocked(name) for name in sys.modules)
'''
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, timeout=30)


def test_mutable_outputs_and_retained_records_are_detached():
    simulation = example.make_simulation(17)
    control = example.Control(True)
    checkpoint = simulation.snapshot()
    view = simulation.observe(control)
    view.values.append(99)
    actions = simulation.legal_actions(control)
    actions.clear()
    assert simulation.legal_actions(control) == [example.Increment(1)]
    assert simulation.snapshot() == checkpoint

    transformed = example.CopyObserver().transform(view, Random(23))
    transformed.values.append(88)
    assert 88 not in view.values
    result = simulation.step(example.Increment(1))
    recorder = example.MemoryRecorder()
    recorder.append(result)
    result.values.append(77)
    assert 77 not in recorder.records[0].values
    assert 77 not in simulation.observe(control).values
    simulation.restore(checkpoint)
    assert simulation.observe(control).values == list(checkpoint)
    simulation.step(example.Increment(1))
    assert len(simulation.snapshot()) == len(checkpoint) + 1
    recorder.close()
    simulation.close()


def test_restricted_callbacks_receive_only_explicit_authorized_values():
    game = bb.create_game(size=1, seed=17, control="external")
    simulation = example.make_simulation(17)
    policy_rng, observer_rng = Random(23), Random(29)
    engine_before = game.rng.get_state()
    simulation_before = simulation._rng.getstate()
    calls = []

    class PolicySpy(example.IncrementPolicy):
        def act(self, observation, control, rng):
            assert type(observation) is example.View
            assert set(vars(observation)) == {"values"}
            assert all(type(value) is int for value in observation.values)
            assert type(control) is example.Control and vars(control) == {"can_increment": True}
            assert rng is policy_rng and rng is not game.rng and rng is not simulation._rng
            calls.append("policy")
            return super().act(observation, control, rng)

    class ObserverSpy(example.CopyObserver):
        def transform(self, observation, rng):
            assert type(observation) is example.View
            assert set(vars(observation)) == {"values"}
            assert all(type(value) is int for value in observation.values)
            assert rng is observer_rng and rng is not game.rng and rng is not simulation._rng
            calls.append("observer")
            return super().transform(observation, rng)

    evaluator = example.TotalEvaluator()
    policy, observer = PolicySpy(), ObserverSpy()
    recorder = example.MemoryRecorder()
    scenario = example.CounterScenario()
    try:
        assert not calls and evaluator.calls == 0 and not recorder.records
        assert not recorder.flushed and not recorder.closed
        factory_calls = []

        def factory(seed):
            factory_calls.append(seed)
            return simulation

        assert not factory_calls
        assert scenario.build(factory, 17) is simulation and factory_calls == [17]
        control = example.Control(True)
        view = observer.transform(simulation.observe(control), observer_rng)
        result = simulation.step(policy.act(view, control, policy_rng))
        assert calls == ["observer", "policy"] and evaluator.calls == 0
        recorder.append(result)
        assert not recorder.flushed and not recorder.closed
        context = example.EvaluationContext(tuple(result.values), 100)
        assert evaluator.evaluate(context) == sum(result.values) - 100
        assert evaluator.calls == 1
        recorder.flush()
        assert recorder.flushed
        simulation.close()
        assert not recorder.closed and evaluator.calls == 1
        recorder.close()
        assert simulation._rng.getstate() == simulation_before
        after = game.rng.get_state()
        assert after[0] == engine_before[0] and after[2:] == engine_before[2:]
        np.testing.assert_array_equal(after[1], engine_before[1])
    finally:
        game.close()


def test_real_legacy_bot_operates_through_explicit_adapter_and_driver():
    game = bb.create_game(size=1, seed=17, control="external")
    bot = bb.make_bot("random")
    bot.rnd.seed(23)
    adapter = LegacyBotAdapter(bot)
    try:
        assert adapter.requires_game_access is True
        assert adapter.bot is bot and bot.my_team is None and bot.actions_taken == 0
        adapter.new_game(game, game.state.home_team)
        assert bot.my_team is game.state.home_team and bot.actions_taken == 0
        driver = bb.PolicyDriver(game, {
            game.state.home_team.team_id: adapter,
            game.state.away_team.team_id: adapter,
        })
        driver.run(max_decisions=3, max_steps=100)
        assert len(driver.trace) == bot.actions_taken == 3
        adapter.end_game(game)
    finally:
        game.close()


def test_legacy_callbacks_forward_identity_and_errors_only_when_called():
    calls = []
    failure = RuntimeError("legacy callback failed")

    class Spy(bb.Agent):
        def new_game(self, game, team):
            calls.append(("new_game", game, team))

        def act(self, game):
            calls.append(("act", game, game.rng))
            raise failure

        def end_game(self, game):
            calls.append(("end_game", game))

    game = bb.create_game(size=1, seed=17, control="external")
    try:
        adapter = LegacyBotAdapter(Spy("spy"))
        assert calls == []
        adapter.new_game(game, game.state.home_team)
        assert calls == [("new_game", game, game.state.home_team)]
        with pytest.raises(RuntimeError) as error:
            adapter(game)
        assert error.value is failure
        assert calls[-1] == ("act", game, game.rng)
        game.close()
        assert len(calls) == 2
        adapter.end_game(game)
        assert calls[-1] == ("end_game", game)
    finally:
        game.close()
