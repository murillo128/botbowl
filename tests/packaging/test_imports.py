"""Protect the installed core boundary and legacy optional public exports."""
import subprocess
import sys

import pytest


def test_headless_import_does_not_load_optional_integrations(tmp_path):
    code = '''
import importlib.abc
import sys
class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'gym', 'flask', 'docker', 'tkinter', 'matplotlib'}:
            raise AssertionError('unexpected optional import: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
import botbowl as bb
import botbowl.ai
assert bb.Game and bb.Action and bb.ProcBot and bb.EnvRenderer
assert bb.ruleset.name == 'BB2016'
assert isinstance(bb.make_bot('random'), bb.RandomBot)
assert bb.load_team_by_filename('human', bb.ruleset, board_size=1).players
'''
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True)


@pytest.mark.parametrize("name", ["BotBowlEnv", "EnvConf", "BotBowlWrapper", "RewardWrapper",
                                  "ScriptedActionWrapper", "PPCGWrapper"])
def test_legacy_rl_exports(name):
    pytest.importorskip("gym")
    import botbowl
    import botbowl.ai.env as env

    assert getattr(botbowl, name) is getattr(env, name)
    assert getattr(botbowl.ai, name) is getattr(env, name)


@pytest.mark.parametrize("name", ["Competition", "MultiAgentCompetition", "DockerAgent",
                                  "PythonSocketClient", "TeamResult", "GameResult", "T"])
def test_legacy_competition_exports(name):
    pytest.importorskip("docker")
    pytest.importorskip("tabulate")
    import botbowl
    import botbowl.ai.competition as competition

    assert getattr(botbowl, name) is getattr(competition, name)


def test_legacy_gym_registration_export():
    gym = pytest.importorskip("gym")
    import botbowl

    assert botbowl.register is gym.envs.registration.register
