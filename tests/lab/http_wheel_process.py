"""Installed-wheel acceptance harness; run from a cwd outside the checkout.

Uses public, deliberately nonsecret fixture identities on a disposable loopback
listener. The external example itself accesses only the HTTP SDK.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

import botbowl
from botbowl.lab.commands import Access, CommandGateway, SessionRegistry
from botbowl.lab.http import HTTPConfig, create_app
from werkzeug.serving import WSGIRequestHandler, make_server


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def main():
    assert "site-packages" in Path(botbowl.__file__).parts, botbowl.__file__
    example = Path(__file__).resolve().parents[2] / "examples/lab/http_match.py"
    grants = {"admin": Access("evaluator", capabilities=frozenset({"close"})),
              "home": Access("player", "home"), "away": Access("player", "away")}
    registry = SessionRegistry()
    gateway = CommandGateway(registry, lambda value: value if value in grants else None,
                             creators={"admin": grants})
    host = make_server("127.0.0.1", 0,
                       create_app(gateway, HTTPConfig(requests_per_window=10000)),
                       threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=host.serve_forever)
    thread.start()
    try:
        env = dict(os.environ, BOTBOWL_HTTP_URL="http://127.0.0.1:%d" % host.server_port,
                   BOTBOWL_HTTP_TOKEN_ADMIN="admin", BOTBOWL_HTTP_TOKEN_HOME="home",
                   BOTBOWL_HTTP_TOKEN_AWAY="away")
        # Do not inherit a checkout path into the child interpreter.
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory(prefix="botbowl-http-wheel-") as cwd:
            subprocess.run([sys.executable, str(example)], cwd=cwd, env=env,
                           check=True, timeout=60)
        assert not registry._entries
        print("Installed wheel: both teams controlled; owned session released")
    finally:
        host.shutdown()
        host.server_close()
        thread.join(2)
        registry.close()
    assert not thread.is_alive()


if __name__ == "__main__":
    main()
