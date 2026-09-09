"""Optional backend assertion for reproducible Python/native test jobs."""
import importlib
import importlib.machinery
import pytest


def pytest_addoption(parser):
    parser.addoption("--require-pathfinding", choices=("python", "native"), default=None,
                     help="Fail before collection unless this pathfinding backend is loaded")


def pytest_configure(config):
    required = config.getoption("--require-pathfinding")
    if required is None:
        return
    pathfinding = importlib.import_module("botbowl.core.pathfinding")
    actual = pathfinding.get_safest_path.__module__
    expected = "botbowl.core.pathfinding." + ("cython_pathfinding" if required == "native" else "python_pathfinding")
    if actual != expected:
        raise pytest.UsageError(f"Required {required} pathfinding; loaded {actual}")
    module = importlib.import_module(expected)
    if required == "native" and not any(module.__file__.endswith(suffix)
                                         for suffix in importlib.machinery.EXTENSION_SUFFIXES):
        raise pytest.UsageError(f"Native pathfinding did not load an extension: {module.__file__}")
    config._baseline_backend = f"{required}: {module.__file__}"


def pytest_report_header(config):
    return getattr(config, "_baseline_backend", None)
