"""Run against an installed artifact from outside its source checkout (no pytest)."""
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import sys


def core_smoke(backend, minimal=False):
    if minimal:
        assert "DISPLAY" not in os.environ
        for distribution in (
            "Flask", "gym", "gymnasium", "docker", "matplotlib", "pytest", "torch"
        ):
            try:
                metadata.version(distribution)
            except metadata.PackageNotFoundError:
                continue
            raise AssertionError(f"Unexpected minimal dependency: {distribution}")
    import botbowl as bb
    import botbowl.core.pathfinding as pf
    from botbowl.lab import SessionConfig, SimulationSession
    from botbowl.lab.randomness import SeedSpec
    from botbowl.lab.rules import describe_rules

    for module in (
        "flask", "gym", "gymnasium", "docker", "matplotlib", "tkinter", "torch"
    ):
        assert module not in sys.modules, module
    assert pf.get_safest_path.__module__.endswith(
        ".cython_pathfinding" if backend == "native" else ".python_pathfinding")
    rules = bb.load_rule_set("BB2016")
    for size in (1, 3, 5, 7, 11):
        config = bb.load_config(f"gym-{size}")
        config.competition_mode = False
        home = bb.load_team_by_filename("human", rules, board_size=size)
        away = bb.load_team_by_filename("human", rules, board_size=size)
        descriptor = describe_rules(config, rules, bb.load_arena(config.arena), home, away)
        assert descriptor.ruleset_id == "BB2016" and descriptor.backend_id == backend
        assert descriptor.engine_version == metadata.version("botbowl")
        game = bb.create_game(config, home, away, seed=17, control="external")
        try:
            for action in (bb.ActionType.START_GAME, bb.ActionType.HEADS, bb.ActionType.KICK):
                assert action in [choice.action_type for choice in game.get_available_actions()]
                game.advance(bb.Action(action), max_steps=100)
            assert game.state.reports and game.get_available_actions()
        finally:
            game.close()
    assert isinstance(bb.make_bot("random"), bb.RandomBot)
    session = SimulationSession(
        SessionConfig(size=1), SeedSpec(17, "wheel-smoke", "engine", "session-v1")
    )
    try:
        legal = session.legal_actions()
        result = session.step(legal.actions[0], legal.state_revision)
        assert result.events and not result.terminated and not result.truncated
    finally:
        session.close()
    return {"backend": backend, "package": str(Path(bb.__file__).resolve()),
            "python": sys.version.split()[0], "numpy": metadata.version("numpy"),
            "sizes": [1, 3, 5, 7, 11], "decisions_per_size": 3}


def extra_smoke(extra):
    if extra == "web":
        from botbowl.web.server import app

        with app.test_client() as client:
            assert client.get("/").status_code == 200
            assert client.get("/static/lib/angular/angular.min.js").status_code == 200
            assert client.get("/static/dist/js/botbowl.js").status_code == 200
            assert client.get("/game-modes/").status_code == 200
            assert "random" in client.get("/bots/").get_json(force=True)
    elif extra == "rl":
        import gym
        import numpy as np
        import botbowl as bb

        env = gym.make("botbowl-1-v4")
        _, _, mask = env.reset()
        env.step(int(np.flatnonzero(mask)[0]))
        assert isinstance(env.unwrapped, bb.BotBowlEnv)
        env.close()
        assert "tkinter" not in sys.modules
    elif extra == "gymnasium":
        import gymnasium as gym
        import numpy as np
        from botbowl.ai import register_gymnasium_envs

        register_gymnasium_envs()
        for size in (1, 3, 5, 7, 11):
            env = gym.make(f"botbowl-{size}-v5")
            obs, info = env.reset(seed=17)
            assert env.observation_space.contains(obs)
            obs, reward, terminated, truncated, info = env.step(int(np.flatnonzero(obs['action_mask'])[0]))
            assert env.observation_space.contains(obs) and isinstance(reward, float)
            assert not terminated and not truncated
            env.close()
            env.close()
        assert "gym" not in sys.modules and "tkinter" not in sys.modules
    elif extra == "competition":
        import botbowl as bb
        from botbowl.ai.competition.python_socket import AgentCommand, Request

        request = Request(AgentCommand.ACT, None)
        assert request.command == AgentCommand.ACT
        assert bb.Competition and bb.DockerAgent and bb.TeamResult
    elif extra == "dev":
        import build
        import more_itertools
        import pytest

        assert callable(pytest.main) and build.ProjectBuilder and more_itertools.first([1]) == 1
    elif extra == "render":
        import botbowl as bb
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        figure = Figure(figsize=(1, 1))
        figure.add_subplot().plot([0, 1], [1, 0])
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
        assert len(canvas.buffer_rgba()) and bb.EnvRenderer
        assert "tkinter" not in sys.modules


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("python", "native"), default="python")
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument("--extra", choices=("web", "rl", "gymnasium", "competition", "dev", "render"))
    args = parser.parse_args()
    result = core_smoke(args.backend, args.minimal)
    if args.extra:
        extra_smoke(args.extra)
        result["extra"] = args.extra
    print(json.dumps(result, sort_keys=True))
