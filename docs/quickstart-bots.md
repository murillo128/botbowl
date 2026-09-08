# Quickstart: registered bots

Install the minimal package as in the [core quickstart](quickstart-core.md), then
run [`examples/quickstart_bot.py`](../examples/quickstart_bot.py):

```bash
python /path/to/quickstart_bot.py --max-steps 100
```

The example registers a tiny deterministic policy for both seats, creates a
size-3 game, and calls `PolicyDriver.run(max_decisions=3, max_steps=100)`.
Construction alone never invokes policy `act`; the explicit driver owns the
three-decision budget. No model, GPU, private file or long training job is used.

For realistic bot lifecycle and competition guidance, see [bots](bots.md). The
Docker-agent pickle protocol is trusted-local only; registering a Python bot
does not expose a remote service.
