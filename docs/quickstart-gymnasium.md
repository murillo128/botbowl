# Quickstart: Gymnasium v5

Install the Gymnasium extra separately:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install 'botbowl[gymnasium] @ git+https://github.com/murillo128/botbowl.git'
python /path/to/quickstart_gymnasium.py --max-steps 100
```

[`examples/quickstart_gymnasium.py`](../examples/quickstart_gymnasium.py) resets
`botbowl-3-v5` with seed 17, selects one legal index from the returned action
mask, performs exactly one checked step, validates the returned observation, and
closes. It does not train a policy or require a GPU/display.

Gymnasium v5 is not an alias for legacy Gym v4. Read the
[v5 contract and migration notes](gymnasium.md), especially observation/action
encoding, actor ownership, masks, termination and truncation.
