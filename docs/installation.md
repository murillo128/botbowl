# Installation

This fork's new packaging line requires CPython **3.11 or newer**, replacing the
older 3.8 baseline. Use an isolated environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install 'git+https://github.com/murillo128/botbowl'
```

The default installation is a headless engine with Python pathfinding. It needs
NumPy and untangle (with its XML parser dependency), and runs without a display,
Gym, Flask, Docker, matplotlib, pytest, or a compiler. Package rules, teams,
formations, arenas, and configurations load independently of the working directory.
This remains the Bot Bowl framework described in the README, with upstream
attribution and rules preserved.

## Optional integrations

The legacy `rl` extra is supported only on CPython **3.11 and 3.12**. It uses
Gym 0.26.2 and NumPy <2. It is **unsupported on 3.13/3.14**: Gym's checker
accesses `np.bool8`, removed in NumPy 2, while NumPy 1.26 supports Python only
through 3.12. The Gymnasium migration and any later expansion belong to issue #17.
Do not install this extra on newer interpreters expecting compatibility.

Install only the integrations you use, for example from a checkout:

```bash
python -m pip install '.[web]'
python -m pip install '.[rl]'
python -m pip install '.[competition]'
python -m pip install '.[render]'
python -m pip install '.[dev]'
```

| Extra | Capability | Additional requirements |
| --- | --- | --- |
| `web` | Existing Flask HTTP routes | Flask and its dependencies |
| `rl` | Legacy Gym environments and wrappers | Gym 0.26.2, NumPy <2; see compatibility below |
| `competition` | Competition helpers and socket/Docker agents | Docker Python client, tabulate; a daemon is needed only to run container agents |
| `render` | Matplotlib plotting in examples | Matplotlib; the legacy `EnvRenderer` separately needs the interpreter's Tk installation and a display when constructed |
| `dev` | Tests and package builds | pytest, build, more-itertools; install other extras for their tests |

`import botbowl` loads the engine and bot helpers without loading these optional
integrations or starting services. Named exports such as `botbowl.BotBowlEnv`,
`botbowl.Competition`, and `botbowl.EnvRenderer` resolve on first use. Wildcard
imports also request optional exports, so use named imports for a minimal client.
Installed Gym plugins register the existing `botbowl-v4` and sized IDs when Gym
loads; `botbowl.ai.register_envs()` is an idempotent explicit alternative.

The wheel and sdist include the web templates and required JavaScript/CSS source,
with existing third-party license notices preserved. They exclude graphics and
fonts whose permissions are distinct from the source-code license. The `web`
extra supplies the Python HTTP API and web application source; installed browser
pages lack the excluded artwork. For the existing complete graphical UI, run
from a checkout with its existing assets and applicable permissions:

```bash
python examples/server_example.py
```

The package does not claim the source-code license covers artwork. See the
README's copyright notice; a distributable browser asset policy remains separate
work. The existing save/replay storage semantics are also unchanged.

## Build Python or native artifacts

The backend is setuptools; metadata lives in `pyproject.toml`. A runtime extra
cannot control an isolated build, so native selection uses an explicit environment
variable:

```bash
python -m pip install build
BOTBOWL_BUILD_NATIVE=0 python -m build  # default: Python wheel and sdist
BOTBOWL_BUILD_NATIVE=1 python -m build  # mandatory C++ pathfinding extension
```

Cython is a build dependency installed in the isolated build environment. It is
not a runtime dependency. Native builds require a working C++ compiler and fail
if compilation fails; there is no silent fallback after an explicit native
request. `BOTBOWL_BUILD_NATIVE` accepts only `0` or `1`. For installation directly
from source or VCS, the same environment variable applies:

```bash
BOTBOWL_BUILD_NATIVE=1 python -m pip install --no-cache-dir 'git+https://github.com/murillo128/botbowl'
```

Set the variable at the **build** step. Installing an already built wheel keeps
that wheel's backend. Build output remains under the build directory; ordinary
wheel builds do not copy extensions back into the source package. Use fresh
build directories or fresh source trees when comparing build modes.

To verify that a default source distribution also supports a later native build,
run the explicit packaging regression with a C++ compiler and `build` installed:

```bash
python tests/packaging/verify_sdist.py --output /tmp/botbowl-sdist-check
```

This exports committed `HEAD` into a fresh directory, builds the Python wheel and
sdist with an unavailable compiler, then builds and smoke-tests a native wheel
from that sdist. It ignores local build and manifest caches. The output directory
must be new; use `--revision` to test another commit.

Verify the actual installed backend:

```bash
python -c 'import botbowl.core.pathfinding as p; print(p.get_safest_path.__module__)'
```

The result ends with `python_pathfinding` or `cython_pathfinding`. Native module
files also carry the platform extension suffix. Both implementations are tested
against the inherited semantic corpus; this is not a claim of complete parity for
all possible skill interactions.

## Development and editable installs

The repository-native setup remains available:

```bash
python setup.py build
python -m pip install -e '.[dev,web,rl,competition,render]'
python -m pytest --require-pathfinding=python
```

For an explicit native editable build:

```bash
BOTBOWL_BUILD_NATIVE=1 python setup.py build
BOTBOWL_BUILD_NATIVE=1 python -m pip install -e '.[dev,web,rl,competition,render]'
python -m pytest --require-pathfinding=native
```

PEP 660/setuptools manages the extension for editable installs; the project has
no post-install binary-copy hook. A previous native editable build can leave an
untracked extension in the source tree: use a fresh checkout for a Python-only
comparison. `requirements.txt` is a constrained development install for CPython 3.11/3.12
with all extras, including legacy RL. Library compatibility ranges live in
`pyproject.toml`; dated CI pins live in `requirements/`. For core development on
3.13/3.14, use `python -m pip install -c requirements/core.txt -e
'.[dev,web,competition]'` and omit the unsupported RL extra. See
[CI profiles and dependency maintenance](ci.md) for the exact test commands.

## Compatibility evidence

The proposed core/native matrix is CPython 3.11–3.14. Installation and test results,
including exact tested dependency versions and limits, are recorded in
[the packaging report](reports/packaging-issue-5.md). No platform or optional
integration is supported merely because `requires-python` permits installation.
The legacy Gym adapter still uses its old reset/step API and emits existing
warnings; this packaging change does not implement the Gymnasium migration.
