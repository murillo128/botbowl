"""Regression: a fresh Python-mode sdist must build a working native wheel.

Run explicitly with the dev extra/build frontend and a C++ compiler available:
python tests/packaging/verify_sdist.py --output /tmp/botbowl-sdist-check

Only committed source at --revision (default HEAD) is exported. Existing build
outputs and SOURCES.txt caches in the checkout cannot influence this check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path, required=True,
                        help="New directory for artifacts, logs, and the result JSON")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("DISPLAY", None)

    def run(name, command, cwd, process_env=env):
        with (output / (name + ".log")).open("w") as log:
            subprocess.run(list(map(str, command)), cwd=cwd, env=process_env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)

    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", args.revision], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="botbowl-sdist-") as temporary:
        work = Path(temporary)
        exported = work / "source"
        exported.mkdir()
        archive = work / "source.tar"
        run("export", ["git", "-C", source, "archive", revision, "-o", archive], work)
        # The archive is created locally from the selected repository revision.
        with tarfile.open(archive) as members:
            members.extractall(exported, filter="data")
        assert not list(exported.glob("*.egg-info"))
        assert not (exported / "build").exists()
        unavailable_compiler = str(work / "compiler-does-not-exist")
        run("python-build", [sys.executable, "-m", "build", "--outdir", output / "python"],
            exported, {**env, "BOTBOWL_BUILD_NATIVE": "0",
                       "CC": unavailable_compiler, "CXX": unavailable_compiler})
        sdist, = (output / "python").glob("*.tar.gz")
        extracted = work / "sdist"
        extracted.mkdir()
        with tarfile.open(sdist) as members:
            names = members.getnames()
            package_root = names[0].split("/")[0]
            for filename in ("pathing_node.cpp", "pathing_node.h"):
                required = package_root + "/botbowl/core/pathfinding/" + filename
                assert required in names, "Fresh Python sdist is missing " + filename
            members.extractall(extracted, filter="data")
        run("native-build", [sys.executable, "-m", "build", "--wheel",
                             "--outdir", output / "native"], extracted / package_root,
            {**env, "BOTBOWL_BUILD_NATIVE": "1"})
        native_wheel, = (output / "native").glob("*.whl")
        with zipfile.ZipFile(native_wheel) as members:
            assert any(name.startswith("botbowl/core/pathfinding/cython_pathfinding.")
                       and name.endswith((".so", ".pyd")) for name in members.namelist())
        run("venv", [sys.executable, "-m", "venv", work / "installed"], work)
        python = work / "installed" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run("install", [python, "-m", "pip", "install", native_wheel], work)
        run("pip-check", [python, "-m", "pip", "check"], work)
        run("native-smoke", [python, exported / "tests/packaging/smoke.py",
                             "--minimal", "--backend", "native"], work)
    artifacts = sorted(output.glob("*/*.whl")) + sorted(output.glob("*/*.tar.gz"))
    result = {"source_revision": revision, "python": sys.version.split()[0],
              "artifacts": [{"filename": path.name,
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                            for path in artifacts]}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
