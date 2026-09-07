"""The headless engine; optional AI integrations are resolved on first use."""
from .core import *
from . import ai as _ai


def __getattr__(name):
    if name in _ai.__all__:
        value = getattr(_ai, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(dict.fromkeys(
    [name for name in globals() if not name.startswith("_")] + _ai.__all__
))


def __dir__():
    return sorted(set(globals()) | set(__all__))
