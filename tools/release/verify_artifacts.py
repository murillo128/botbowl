"""Build and verify repeatable release-candidate wheel/sdist membership."""
import argparse
import ast
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


GRAPHICS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".eot", ".ttf", ".woff", ".woff2"}
REQUIRED = {
    "botbowl/__init__.py",
    "botbowl/__main__.py",
    "botbowl/_version.py",
    "botbowl/data/rules/BB2016.xml",
    "botbowl/data/config/gym-3.json",
    "botbowl/web/templates/index.html",
    "botbowl/web/static/dist/js/botbowl.js",
}


def source_version(source: Path) -> str:
    tree = ast.parse((source / "botbowl/_version.py").read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__version__"
                   for target in statement.targets):
                if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                    return statement.value.value
    raise ValueError("botbowl/_version.py must contain one literal __version__ assignment")


def copy_source(source: Path, target: Path) -> None:
    root_ignored = {".git", ".venv", "venv", "build", "dist"}

    def ignored(directory, names):
        ignored_names = {name for name in names
                         if name == "__pycache__" or name.endswith((".pyc", ".so", ".egg-info"))}
        if Path(directory).resolve() == source:
            ignored_names.update(root_ignored.intersection(names))
        return ignored_names

    shutil.copytree(source, target, ignore=ignored)


def run(command, *, cwd: Path, env=None) -> None:
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def build_once(source: Path, target: Path) -> tuple[Path, Path]:
    checkout = target / "source"
    copy_source(source, checkout)
    dist = target / "dist"
    env = {**os.environ, "BOTBOWL_BUILD_NATIVE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", dist],
        cwd=checkout, env=env)
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("each build must produce exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def wheel_details(path: Path) -> tuple[list[str], str]:
    with zipfile.ZipFile(path) as archive:
        members = sorted(name for name in archive.namelist() if not name.endswith("/"))
        metadata_names = [name for name in members if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("wheel must have exactly one METADATA file")
        version = BytesParser().parsebytes(archive.read(metadata_names[0]))["Version"]
    return members, version


def sdist_details(path: Path) -> tuple[list[str], str]:
    with tarfile.open(path, "r:gz") as archive:
        files = sorted(member.name for member in archive.getmembers() if member.isfile())
        roots = {name.split("/", 1)[0] for name in files}
        if len(roots) != 1:
            raise ValueError("sdist must have exactly one archive root")
        members = sorted(name.split("/", 1)[1] for name in files if "/" in name)
        metadata_names = [name for name in files if name.count("/") == 1 and name.endswith("/PKG-INFO")]
        if len(metadata_names) != 1:
            raise ValueError("sdist must have exactly one root PKG-INFO")
        stream = archive.extractfile(metadata_names[0])
        if stream is None:
            raise ValueError("cannot read sdist PKG-INFO")
        version = BytesParser().parsebytes(stream.read())["Version"]
    return members, version


def audit_members(kind: str, members: list[str]) -> None:
    package_members = {name for name in members if name.startswith("botbowl/")}
    missing = REQUIRED - package_members
    if missing:
        raise ValueError(f"{kind} is missing required files: {sorted(missing)}")
    if kind == "wheel" and not any(name.endswith(".dist-info/licenses/THIRD_PARTY_NOTICES.md")
                                   for name in members):
        raise ValueError("wheel is missing the rights inventory")
    if kind == "sdist":
        expected_docs = {"CHANGELOG.md", "THIRD_PARTY_NOTICES.md", "docs/migration.md",
                         "docs/support.md", "docs/releasing.md",
                         "examples/quickstart_core.py", "examples/quickstart_bot.py",
                         "examples/quickstart_gymnasium.py", "examples/quickstart_web.py"}
        missing_docs = expected_docs - set(members)
        if missing_docs:
            raise ValueError(f"sdist is missing release docs/examples: {sorted(missing_docs)}")
    forbidden = []
    for name in members:
        path = Path(name)
        lowered = name.lower()
        if (path.suffix.lower() in GRAPHICS or "__pycache__" in path.parts
                or path.parts[0] == "tests" or lowered.endswith((".bb", ".rep", ".env"))
                or "credentials" in lowered or "secret" in lowered):
            forbidden.append(name)
    if forbidden:
        raise ValueError(f"{kind} contains forbidden release files: {forbidden[:20]}")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def install_smoke(artifact: Path, expected: str, target: Path) -> None:
    run([sys.executable, "-m", "venv", target], cwd=target.parent)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "BOTBOWL_BUILD_NATIVE": "0"}
    env.pop("DISPLAY", None)
    run([python, "-m", "pip", "install", "--disable-pip-version-check", artifact],
        cwd=target.parent, env=env)
    code = (
        "from importlib.metadata import version; import botbowl, os; "
        f"assert version('botbowl') == botbowl.__version__ == {expected!r}; "
        "assert 'DISPLAY' not in os.environ"
    )
    run([python, "-c", code], cwd=target.parent, env=env)
    run([python, "-m", "botbowl", "smoke", "--max-steps", "100"],
        cwd=target.parent, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install-smoke", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("--output must not already exist")
    output.mkdir(parents=True)
    expected = source_version(source)

    with tempfile.TemporaryDirectory(prefix="botbowl-release-") as temporary:
        temp = Path(temporary)
        first_wheel, first_sdist = build_once(source, temp / "first")
        second_wheel, second_sdist = build_once(source, temp / "second")
        first_wheel_members, first_wheel_version = wheel_details(first_wheel)
        second_wheel_members, second_wheel_version = wheel_details(second_wheel)
        first_sdist_members, first_sdist_version = sdist_details(first_sdist)
        second_sdist_members, second_sdist_version = sdist_details(second_sdist)
        if first_wheel_members != second_wheel_members:
            raise ValueError("repeated wheels do not contain the same files")
        if first_sdist_members != second_sdist_members:
            raise ValueError("repeated sdists do not contain the same files")
        versions = {expected, first_wheel_version, second_wheel_version,
                    first_sdist_version, second_sdist_version}
        if versions != {expected}:
            raise ValueError(f"source/artifact versions disagree: {sorted(versions)}")
        audit_members("wheel", first_wheel_members)
        audit_members("sdist", first_sdist_members)

        artifacts = output / "artifacts"
        artifacts.mkdir()
        wheel = artifacts / first_wheel.name
        sdist = artifacts / first_sdist.name
        shutil.copy2(first_wheel, wheel)
        shutil.copy2(first_sdist, sdist)
        if args.install_smoke:
            install_smoke(wheel, expected, output / "wheel-venv")
            install_smoke(sdist, expected, output / "sdist-venv")

    manifest = {
        "version": expected,
        "repeated_membership_equal": {"wheel": True, "sdist": True},
        "artifacts": {
            wheel.name: {"sha256": digest(wheel), "members": first_wheel_members},
            sdist.name: {"sha256": digest(sdist), "members": first_sdist_members},
        },
        "install_smoke": args.install_smoke,
        "publication": "none",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"version": expected, "wheel_files": len(first_wheel_members),
                      "sdist_files": len(first_sdist_members), "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
