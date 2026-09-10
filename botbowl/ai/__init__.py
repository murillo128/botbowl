"""Bot helpers with optional integrations loaded on first use."""
from importlib import import_module as _import_module

from .layers import *
from .registry import *
from .proc_bot import *
from .bots import *

_LAZY_EXPORTS = {
    "GymnasiumEnv": ".gymnasium_env",
    "LegacyV4Env": ".env",
    **dict.fromkeys(("BotBowlEnv", "EnvConf", "BotBowlWrapper", "RewardWrapper",
                     "ScriptedActionWrapper", "PPCGWrapper"), ".env"),
    "EnvRenderer": ".env_render",
    "register": "gym.envs.registration",
    **dict.fromkeys(("Competition", "MultiAgentCompetition", "TimeoutException",
                     "default_score_calculator"), ".competition.competition"),
    **dict.fromkeys(("TeamResult", "GameResult", "CompetitionResults",
                     "AgentSummaryResult"), ".competition.result_structures"),
    **dict.fromkeys(("AgentCommand", "Request", "Response", "send_data", "receive_data",
                     "PythonSocketClient", "DockerAgent", "PythonSocketServer",
                     "docker_image_exists", "get_free_port", "T"), ".competition.python_socket"),
}


def __getattr__(name):
    if name == "ruleset":
        from botbowl.core.load import load_rule_set

        value = load_rule_set("BB2016")
    elif name in _LAZY_EXPORTS:
        try:
            value = getattr(_import_module(_LAZY_EXPORTS[name], __name__), name)
        except ModuleNotFoundError as error:
            extras = {"gym": "rl", "gymnasium": "gymnasium", "docker": "competition", "tabulate": "competition"}
            if error.name not in extras:
                raise
            extra = extras[error.name]
            raise ModuleNotFoundError(
                f"{name} needs an optional dependency; install botbowl[{extra}]"
            ) from error
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def register_envs():
    """Register legacy Gym IDs when Gym loads its installed plugins."""
    from gym.envs.registration import register, registry

    for size in (11, 7, 5, 3, 1):
        env_id = f"botbowl-{size}-v4"
        if env_id not in registry:
            register(id=env_id, entry_point="botbowl.ai:_make_env", kwargs={"size": size})
    if "botbowl-v4" not in registry:
        register(id="botbowl-v4", entry_point="botbowl.ai:_make_env")


def _make_env(size=11, **kwargs):
    from .env import BotBowlEnv, EnvConf

    kwargs.setdefault("env_conf", EnvConf(size=size))
    return BotBowlEnv(**kwargs)


def register_gymnasium_envs():
    """Explicit registration, also usable through gym.make('botbowl.ai:...')."""
    from gymnasium.envs.registration import register, registry

    for size in (1, 3, 5, 7, 11):
        name = f"botbowl-{size}-v5"
        if name not in registry:
            register(name, entry_point="botbowl.ai.gymnasium_env:GymnasiumEnv",
                     kwargs={"size": size})


__all__ = [name for name in globals() if not name.startswith("_")] + list(_LAZY_EXPORTS) + ["ruleset"]


def __dir__():
    return sorted(set(globals()) | set(__all__))
