"""Optional Flask transport for CommandGateway. Install ``botbowl[web]``.

The caller supplies authentication and creation grants. One process owns each
registry; a multi-process WSGI deployment must not fork live sessions.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass
import ipaddress
import math
import threading
import time

from flask import Blueprint, Flask, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

from .commands import (CapacityExceeded, Forbidden, GatewayError, InvalidRequest)


ERROR_STATUS = {
    "invalid_request": 400, "invalid_command": 400, "UnsupportedCapability": 400,
    "unauthenticated": 401, "forbidden": 403, "invalid_generation": 404,
    "conflict": 409, "closed": 409, "expired_request": 410, "event_gap": 410,
    "too_large": 413, "capacity_exceeded": 429, "execution_failure": 500,
    "internal_error": 500,
}


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


@dataclass(frozen=True)
class HTTPConfig:
    host: str = "127.0.0.1"
    tls: bool = False
    trusted_proxies: tuple = ()
    max_wait: float = 5.0
    max_page: int = 128
    requests_per_window: int = 120
    rate_seconds: float = 60.0
    max_clients: int = 64
    max_inflight: int = 32

    def __post_init__(self):
        if type(self.host) is not str or not self.host or type(self.tls) is not bool:
            raise ValueError("Invalid bind configuration")
        if type(self.trusted_proxies) is not tuple:
            raise ValueError("Trusted proxies must be explicit IP addresses")
        for peer in self.trusted_proxies:
            ipaddress.ip_address(peer)
        if not _loopback(self.host) and not (self.tls or self.trusted_proxies):
            raise ValueError("Remote binding requires TLS or an explicit TLS proxy")
        for value in (self.max_page, self.requests_per_window, self.max_clients, self.max_inflight):
            if type(value) is not int or value < 1:
                raise ValueError("Limits must be positive integers")
        for value in (self.max_wait, self.rate_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Time bounds must be finite and positive")
        if self.max_wait > 30:
            raise ValueError("Long poll wait cannot exceed 30 seconds")


def _error(code, status=None):
    return jsonify({"ok": False, "error": code}), status or ERROR_STATUS.get(code, 500)


def create_blueprint(gateway, config=HTTPConfig()):
    """Register explicitly; legacy web routes are unaffected.

    Proxy headers are never used for identity, host or address. Only an exact
    configured peer may attest TLS with one X-Forwarded-Proto: https value.
    """
    if not callable(gateway._authenticate):
        raise ValueError("Explicit authentication is required")
    bp = Blueprint("lab_http_v1", __name__, url_prefix="/api/v1")
    slots = threading.BoundedSemaphore(config.max_inflight)
    rates, lock = OrderedDict(), threading.Lock()

    @bp.before_request
    def guard():
        if current_app.debug:
            return _error("internal_error")
        peer = request.environ.get("REMOTE_ADDR", "")
        proxy_tls = peer in config.trusted_proxies and request.headers.get("X-Forwarded-Proto") == "https"
        if config.tls or config.trusted_proxies or not _loopback(peer):
            if not (request.is_secure or proxy_tls):
                raise Forbidden()
        if request.headers.get("Origin") is not None:
            raise Forbidden()
        auth = request.headers.get("Authorization", "")
        # Authentication and limits precede body decoding and privileged reads.
        token = auth[7:] if auth.startswith("Bearer ") and len(auth) <= 8192 else None
        principal = gateway._principal(token)
        g.lab_credential = token
        now = time.monotonic()
        with lock:
            for key, values in list(rates.items()):
                while values and values[0] <= now - config.rate_seconds:
                    values.popleft()
                if not values:
                    del rates[key]
            if principal not in rates and len(rates) >= config.max_clients:
                raise CapacityExceeded()
            values = rates.setdefault(principal, deque())
            if len(values) >= config.requests_per_window:
                raise CapacityExceeded()
            values.append(now)
        if not slots.acquire(blocking=False):
            raise CapacityExceeded()
        g.lab_slot = True
        request.max_content_length = gateway.registry.limits.max_request_bytes
        if request.content_length is not None and request.content_length > request.max_content_length:
            return _error("too_large")

    @bp.teardown_request
    def release(error):
        if g.pop("lab_slot", False):
            slots.release()

    @bp.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-BotBowl-Retry-Seconds"] = str(gateway.registry.limits.retry_seconds)
        return response

    @bp.errorhandler(GatewayError)
    def gateway_error(error):
        return _error(error.code)

    @bp.errorhandler(HTTPException)
    def http_error(error):
        return _error("too_large" if error.code == 413 else "invalid_request", error.code)

    @bp.errorhandler(Exception)
    def internal_error(error):
        # Never serialize exception messages, credentials, engine or tracebacks.
        return _error("internal_error")

    def raw_body():
        if request.mimetype != "application/json":
            raise InvalidRequest()
        return request.get_data(cache=False)

    def receipt(result):
        return jsonify(result), 200 if result["ok"] else ERROR_STATUS.get(result["error"], 500)

    @bp.get("/capabilities")
    def capabilities():
        from .scenarios import SCENARIO_RECIPES
        return jsonify({"schema_version": 1,
                        "retry_seconds": gateway.registry.limits.retry_seconds,
                        "max_request_bytes": gateway.registry.limits.max_request_bytes,
                        "max_response_bytes": gateway.registry.limits.max_response_bytes,
                        "max_wait": config.max_wait, "max_page": config.max_page,
                        "scenarios": {key: value.version for key, value in SCENARIO_RECIPES.items()}})

    @bp.post("/sessions")
    def create():
        return receipt(gateway.create(g.lab_credential, raw_body()))

    @bp.post("/commands")
    def execute():
        return receipt(gateway.execute(g.lab_credential, raw_body()))

    @bp.get("/sessions/<session_id>")
    def state(session_id):
        if request.args:
            raise InvalidRequest()
        return jsonify(gateway.read(g.lab_credential, session_id))

    @bp.get("/sessions/<session_id>/snapshot")
    def snapshot(session_id):
        if request.args:
            raise InvalidRequest()
        return jsonify(gateway.read(g.lab_credential, session_id, resource="snapshot"))

    @bp.get("/sessions/<session_id>/events")
    def events(session_id):
        if set(request.args) - {"cursor", "limit", "wait"} or any(len(v) != 1 for _, v in request.args.lists()):
            raise InvalidRequest()
        cursor = request.args.get("cursor")
        if cursor is None:
            raise InvalidRequest()
        try:
            limit = int(request.args.get("limit", str(config.max_page)))
            wait = float(request.args.get("wait", "0"))
        except ValueError:
            raise InvalidRequest() from None
        if not 1 <= limit <= config.max_page or not math.isfinite(wait) or not 0 <= wait <= config.max_wait:
            raise InvalidRequest()
        deadline = time.monotonic() + wait
        while True:
            result = gateway.read(g.lab_credential, session_id, cursor=cursor)
            if result["events"] or time.monotonic() >= deadline:
                result["events"] = result["events"][:limit]
                if result["events"]:
                    result["cursor"] = "%s:%d" % (session_id, result["events"][-1]["sequence"])
                return jsonify(result)
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    return bp


def create_app(gateway, config=HTTPConfig()):
    app = Flask(__name__)
    app.config.update(DEBUG=False, TESTING=False)
    app.register_blueprint(create_blueprint(gateway, config))

    @app.errorhandler(HTTPException)
    def routing_error(error):
        return _error("not_found" if error.code == 404 else "invalid_request", error.code)

    return app


def serve(gateway, config=HTTPConfig(), *, port=5000, ssl_context=None):
    """Blocking single-process server, primarily for local scripting.

    Remote installations should use a production WSGI server and the same
    configuration checks. No debug/reloader; teardown closes all live sessions.
    """
    from werkzeug.serving import make_server
    if config.tls and ssl_context is None:
        raise ValueError("TLS binding requires a configured SSL context")
    server = make_server(config.host, port, create_app(gateway, config),
                         threaded=True, ssl_context=ssl_context)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        gateway.registry.close()
