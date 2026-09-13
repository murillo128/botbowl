# Development Guide
We'd love your help with testing, bug fixing and developing the framework. Fork
work is tracked in the [maintained issue tracker](https://github.com/murillo128/botbowl/issues).
The [upstream tracker](https://github.com/njustesen/botbowl/issues) remains useful
history; do not erase upstream attribution or assume an old request is accepted
fork scope. Check [features.md](features.md) for the historical feature list.

Please join our Discord channel to discuss the development of botbowl [botbowl Discord Server](https://discord.gg/MTXMuae).

## Install for development
Use CPython 3.11+ and an editable install to test modifications:

```bash
git clone https://github.com/murillo128/botbowl
cd botbowl
python setup.py build
python -m pip install -e '.[dev]'
```

`python setup.py build` uses the reference Python backend by default. Set
`BOTBOWL_BUILD_NATIVE=1` to require the C++ pathfinder. Builds do not copy
extensions into the checkout. See [installation](installation.md).

## Run tests
Install pytest and run the unit and integration tests in [tests/](../tests) by running:
```bash
python -m pytest --require-pathfinding=python
```
from the root of the repository.

Before making a pull request, please make sure that all tests pass. You should also consider if the changes you have made requires a new test.

Release candidates use the manual procedure in [releasing](releasing.md). Do not
publish under the upstream PyPI name or create a tag merely because tests pass.
