# Quickstart: minimal headless core

Requires CPython 3.11+ and no display, GPU, compiler, web server or training
framework.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install 'git+https://github.com/murillo128/botbowl'
python -m botbowl --version
python -m botbowl smoke --max-steps 100
```

The smoke creates a size-3 game with seed 17, advances exactly three external
decisions and closes it. For code you can adapt, download and run
[`examples/quickstart_core.py`](../examples/quickstart_core.py) from any working
directory; it imports resources from the installed package.

This is a bounded installation check, not a full match or a claim of complete
rules coverage. Read the [support boundaries](support.md) before recording an
experiment.
