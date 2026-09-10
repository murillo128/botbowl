"""Disposable authenticated server for the HTTP example, never a deployment."""
from contextlib import contextmanager
import secrets
import threading


@contextmanager
def loopback(tokens):
    from werkzeug.serving import WSGIRequestHandler, make_server
    from botbowl.lab.commands import Access, CommandGateway, SessionRegistry
    from botbowl.lab.http import HTTPConfig, create_app

    class QuietHandler(WSGIRequestHandler):
        def log(self, *args, **kwargs):
            pass

    grants = {'admin': Access('evaluator', capabilities=frozenset({'close'})),
              'home': Access('player', 'home'), 'away': Access('player', 'away')}
    credentials = {tokens[role]: role for role in grants}
    registry = SessionRegistry()
    gateway = CommandGateway(registry, credentials.get, creators={'admin': grants})
    server = make_server('127.0.0.1', 0,
                        create_app(gateway, HTTPConfig(requests_per_window=10000)),
                        threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d' % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        registry.close()


def ephemeral_tokens():
    return {role: secrets.token_urlsafe(32) for role in ('admin', 'home', 'away')}
