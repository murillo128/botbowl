"""Distribution, documentation and bounded quickstart release contracts."""
from importlib import util
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_version():
    spec = util.spec_from_file_location("botbowl_release_version", ROOT / "botbowl/_version.py")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.__version__


def test_version_has_one_source_and_runtime_export():
    import tomllib
    import botbowl

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "botbowl._version.__version__"
    }
    assert botbowl.__version__ == load_version()
    assert re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+(?:a[0-9]+)?", botbowl.__version__)


def test_release_workflow_is_artifact_only_and_not_triggered_by_pull_requests():
    workflow = (ROOT / ".github/workflows/release-artifacts.yml").read_text()
    lowered = workflow.lower()
    assert "workflow_dispatch:" in workflow and "tags:" in workflow
    assert "pull_request:" not in workflow and "pull_request_target:" not in workflow
    assert "contents: read" in workflow
    for forbidden in ("pypi", "twine", "docker push", "gh release", "id-token: write"):
        assert forbidden not in lowered
    assert "upload-artifact@" in workflow


def test_dockerfile_installs_verified_wheel_as_non_root_headless_default():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY ." not in dockerfile and "requirements.txt" not in dockerfile
    assert "dist/release/artifacts/*.whl" in dockerfile and "USER 10001:10001" in dockerfile
    assert dockerfile.rstrip().endswith(
        'CMD ["python", "-m", "botbowl", "smoke", "--max-steps", "100"]'
    )
    assert "/var/run/docker.sock" not in dockerfile


def run_example(name, tmp_path, *arguments):
    subprocess.run([sys.executable, ROOT / "examples" / name, *arguments],
                   cwd=tmp_path, check=True, timeout=30)


@pytest.mark.parametrize("name", ["quickstart_core.py", "quickstart_bot.py"])
def test_minimal_quickstarts_are_bounded_and_cwd_independent(name, tmp_path):
    run_example(name, tmp_path, "--max-steps", "100")


def test_gymnasium_quickstart_is_bounded_and_cwd_independent(tmp_path):
    pytest.importorskip("gymnasium")
    run_example("quickstart_gymnasium.py", tmp_path, "--max-steps", "100")


def test_web_quickstart_check_is_finite_and_non_debug(tmp_path):
    pytest.importorskip("flask")
    run_example("quickstart_web.py", tmp_path, "--check")


def test_installed_web_cli_passes_explicit_safe_server_flags(monkeypatch):
    import botbowl.__main__ as command
    import botbowl.web.server as server

    called = {}

    def start_server(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(server, "start_server", start_server)
    assert command.main(["web", "--host", "127.0.0.1", "--port", "8123"]) == 0
    assert called == {"host": "127.0.0.1", "port": 8123,
                      "debug": False, "use_reloader": False}


def test_all_repository_local_documentation_links_resolve(tmp_path):
    subprocess.run([sys.executable, ROOT / "tools/release/check_docs.py", "--root", ROOT],
                   cwd=tmp_path, check=True, timeout=30)
