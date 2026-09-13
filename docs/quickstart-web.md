# Quickstart: optional local web UI

The inherited UI is optional and intended for trusted local use.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install 'botbowl[web] @ git+https://github.com/murillo128/botbowl.git'
python -m botbowl web --host 127.0.0.1 --port 5000
```

Open http://127.0.0.1:5000/. The entry point always passes `debug=False` and
`use_reloader=False`; it does not read those settings from the environment.
[`examples/quickstart_web.py`](../examples/quickstart_web.py) provides the same
server command and a finite `--check` mode used by release validation.

Do not expose this legacy UI as an unauthenticated remote simulation service.
Built distributions intentionally omit inherited graphics and fonts whose rights
are separate, so the installed UI uses only the distributable code/style subset.
Run the complete inherited UI only from a checkout and after assessing its asset
permissions. See [rights inventory](../THIRD_PARTY_NOTICES.md) and
[web API boundaries](web-api.md).
