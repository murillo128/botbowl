"""Disposable loopback tests, including lost responses after server commit."""
from contextlib import contextmanager
import json
import threading
import time

import pytest
from werkzeug.serving import WSGIRequestHandler, make_server

from botbowl.lab.http import HTTPConfig
from botbowl.lab.http_client import HTTPClient, HTTPError, UncertainResult
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.scenarios import scenario_spec
from botbowl.lab.session import SessionConfig
from tests.lab.test_http_api import creation, envelope, service


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


@contextmanager
def server(middleware=None, *, config=None):
    registry, gateway, app = service(config=config)
    if middleware:
        app.wsgi_app = middleware(app.wsgi_app)
    host = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=host.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % host.server_port, registry, gateway
    finally:
        host.shutdown()
        host.server_close()
        thread.join(2)
        registry.close()
        assert not thread.is_alive()


def normalize(value):
    value = dict(value)
    value.pop("session_id", None)
    if "cursor" in value:
        value["cursor"] = value["cursor"].rsplit(":", 1)[-1]
    return value


@pytest.mark.parametrize("scenario", [None, scenario_spec("pickup", size=1, scenario_seed=17),
                                      scenario_spec("block", size=3, scenario_seed=17)])
def test_two_remote_and_two_local_controllers_have_identical_results(scenario):
    local_registry, local, _ = service()
    with server() as (url, registry, gateway):
        try:
            with HTTPClient(url, "admin") as admin, HTTPClient(url, "home") as home, HTTPClient(url, "away") as away:
                kwargs = {"scenario": scenario} if scenario else {"config": SessionConfig(size=1, max_decisions=24), "seed": SeedSpec(17)}
                remote = admin.create(request_id=1, **kwargs)
                local_id = local.create("admin", json.dumps(creation(scenario=scenario)).encode())["session_id"]
                remote_clients = {"home": home, "away": away}
                local_controllers = {side: (lambda raw, side=side: local.execute(side, raw)) for side in remote_clients}
                for number in range(1, 30):
                    actual = admin.read(remote.session_id)
                    expected = local.read("admin", local_id)
                    assert normalize(actual) == normalize(expected)
                    actions = actual["legal_actions"]["actions"]
                    if not actions:
                        break
                    action = actions[(number - 1) % len(actions)]
                    actor = action["actor_id"]
                    cmd = envelope(remote.session_id, number, actual["state_revision"], "step", action=action)
                    receipt = remote_clients[actor].execute(cmd)
                    cmd["session_id"] = local_id
                    local_receipt = local_controllers[actor](json.dumps(cmd).encode())
                    assert normalize(receipt) == normalize(local_receipt)
                    actual_events = admin.events(remote.session_id, actual["cursor"])
                    expected_events = local.read("admin", local_id, cursor=expected["cursor"])
                    assert normalize(actual_events) == normalize(expected_events)
                assert normalize(admin.read(remote.session_id)) == normalize(local.read("admin", local_id))
            assert not registry._entries
        finally:
            local_registry.close()


class LoseFirstResponse:
    def __init__(self, app):
        self.app = app
        self.lost = False
        self.lock = threading.Lock()

    def __call__(self, environ, start_response):
        result = self.app(environ, start_response)
        with self.lock:
            lose = environ["PATH_INFO"] == "/api/v1/commands" and not self.lost
            if lose:
                self.lost = True
        if lose:
            # Flask has committed and cached the receipt; headers/body have not
            # reached the client. A retry may race this sleeping connection.
            time.sleep(.15)
        return result


def test_timeout_after_commit_retries_identical_body_only_once():
    with server(LoseFirstResponse) as (url, registry, gateway):
        with HTTPClient(url, "admin") as admin, HTTPClient(url, "away", read_timeout=.04, retries=2) as player:
            remote = admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
            state = remote.read()
            command = envelope(remote.session_id, 1, state["state_revision"], "step", action=state["legal_actions"]["actions"][0])
            result = player.execute(command)
            assert result["ok"]
            assert registry._entries[remote.session_id].cursor == 1
            assert player.execute(command) == result
            assert player._connection is None
            assert player._pending is None


def test_uncertain_result_blocks_new_play_and_expiry_never_resends(monkeypatch):
    with server() as (url, registry, gateway):
        with HTTPClient(url, "admin") as admin, HTTPClient(url, "away", retries=0) as player:
            remote = admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
            state = remote.read()
            command = envelope(remote.session_id, 1, state["state_revision"], "step", action=state["legal_actions"]["actions"][0])
            original = player._once
            def lost(method, path, raw=None):
                response = original(method, path, raw)
                if method == "POST":
                    raise TimeoutError()
                return response
            monkeypatch.setattr(player, "_once", lost)
            with pytest.raises(UncertainResult):
                player.execute(command)
            assert player.read(remote.session_id)["state_revision"] == 2
            newer = dict(command, request_id=2)
            with pytest.raises(UncertainResult):
                player.execute(newer)
            monkeypatch.setattr(player, "_once", original)
            assert player.retry_pending()["ok"]
            assert registry._entries[remote.session_id].cursor == 1
            player._pending = ("/commands", json.dumps(command).encode(), time.monotonic() - 1)
            monkeypatch.setattr(player, "_once", lambda *a, **k: pytest.fail("resent expired command"))
            with pytest.raises(UncertainResult):
                player.retry_pending()


def test_session_and_socket_cleanup_on_exception_and_disconnection():
    with server() as (url, registry, gateway):
        admin = HTTPClient(url, "admin")
        with pytest.raises(RuntimeError, match="caller failed"):
            with admin:
                with admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17)) as session:
                    raise RuntimeError("caller failed")
        assert not registry._entries
        assert admin._connection is None
        admin.close()
        session.close()
        viewer = HTTPClient(url, "viewer")
        viewer.capabilities()
    with pytest.raises(OSError):
        viewer.capabilities()
    assert viewer._connection is None
    viewer.close()


def test_roles_http_errors_and_bad_client_configuration():
    with server() as (url, registry, gateway):
        with HTTPClient(url, "bad") as client:
            with pytest.raises(HTTPError) as error:
                client.capabilities()
            assert error.value.status == 401
        with HTTPClient(url, "admin") as admin, HTTPClient(url, "viewer") as viewer:
            session = admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
            with pytest.raises(HTTPError) as error:
                viewer.snapshot(session.session_id)
            assert error.value.status == 403
            with pytest.raises(ValueError):
                viewer.events(session.session_id, session.read()["cursor"], wait=viewer.read_timeout)
    for url in ("http://example.com", "https://user:secret@example.com", "http://127.0.0.1/arbitrary", "ftp://127.0.0.1"):
        with pytest.raises(ValueError):
            HTTPClient(url, "fixture")
    for kwargs in ({"read_timeout": float("nan")}, {"connect_timeout": 0}, {"retries": 100}):
        with pytest.raises(ValueError):
            HTTPClient("http://127.0.0.1", "fixture", **kwargs)


def test_lost_creation_and_close_receipts_are_recovered(monkeypatch):
    with server() as (url, registry, gateway):
        admin = HTTPClient(url, "admin", retries=0)
        original = admin._once
        lose_ops = {"create", "close"}
        def once(method, path, raw=None):
            result = original(method, path, raw)
            if method == "POST" and json.loads(raw)["payload"]["op"] in lose_ops:
                lose_ops.remove(json.loads(raw)["payload"]["op"])
                raise TimeoutError()
            return result
        monkeypatch.setattr(admin, "_once", once)
        with pytest.raises(UncertainResult):
            admin.execute(creation())
        result = admin.retry_pending()
        assert len(registry._entries) == 1
        # Reusing the recovered creation identity returns an owned handle.
        session = admin.create(request_id=1, config=SessionConfig(size=1, max_decisions=24), seed=SeedSpec(17))
        assert session.session_id == result["session_id"]
        with pytest.raises(UncertainResult):
            session.close()
        assert not registry._entries
        session.close()
        session.close()
        admin.close()


def test_unreceipted_internal_error_remains_uncertain(monkeypatch):
    with server() as (url, registry, gateway):
        with HTTPClient(url, "admin", retries=0) as admin:
            admin.capabilities()
            original = admin._once
            def lost(method, path, raw=None):
                original(method, path, raw)
                raise HTTPError(500, {"ok": False, "error": "internal_error"})
            monkeypatch.setattr(admin, "_once", lost)
            with pytest.raises(UncertainResult):
                admin.execute(creation())
            monkeypatch.setattr(admin, "_once", original)
            receipt = admin.retry_pending()
            assert receipt["ok"] and len(registry._entries) == 1
            admin.execute(envelope(receipt["session_id"], 1, receipt["state_revision"], "close"))


def test_duplicate_creation_handle_and_explicit_close_are_idempotent():
    with server() as (url, registry, gateway):
        with HTTPClient(url, "admin") as admin:
            kwargs = dict(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
            first = admin.create(**kwargs)
            assert admin.create(**kwargs) is first
            assert len(registry._entries) == 1
            first.command({"op": "close"}, first.read()["state_revision"])
            first.close()
        assert not registry._entries



@pytest.mark.parametrize("interrupted", [False, True])
def test_lost_commit_then_real_rate_limit_keeps_pending_identity(monkeypatch, interrupted):
    from types import SimpleNamespace
    import botbowl.lab.http as transport
    now = [0.0]
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=time.sleep))
    config = HTTPConfig(requests_per_window=2, rate_seconds=60)
    with server(config=config) as (url, registry, gateway):
        with HTTPClient(url, "admin", retries=1) as admin:
            original = admin._once
            sent = []
            def lose_once(method, path, raw=None):
                if method == "POST":
                    sent.append(raw)
                result = original(method, path, raw)
                if method == "POST" and len(sent) == 1:
                    if interrupted:
                        raise KeyboardInterrupt()
                    raise TimeoutError()
                return result
            monkeypatch.setattr(admin, "_once", lose_once)
            # Capabilities and committed creation exhaust the real HTTP limiter.
            if interrupted:
                with pytest.raises(KeyboardInterrupt):
                    admin.execute(creation())
            with pytest.raises(UncertainResult) as error:
                admin.retry_pending() if interrupted else admin.execute(creation())
            assert isinstance(error.value.__cause__, HTTPError)
            assert error.value.__cause__.status == 429
            assert len(registry._entries) == 1
            pending = admin._pending
            assert len(sent) == (3 if interrupted else 2)
            assert all(raw == pending[1] for raw in sent)
            with pytest.raises(UncertainResult):
                admin.execute(creation(2))
            assert admin._pending == pending
            now[0] += config.rate_seconds + 1
            receipt = admin.retry_pending()
            assert sent[-1] == pending[1]
            assert receipt["ok"] and len(registry._entries) == 1
            admin.execute(envelope(receipt["session_id"], 1, receipt["state_revision"], "close"))


@pytest.mark.parametrize("unwind", [False, True])
def test_owned_creation_survives_lost_response_and_context_cleanup(monkeypatch, unwind):
    with server() as (url, registry, gateway):
        admin = HTTPClient(url, "admin", retries=0)
        original = admin._once
        lost = False
        def lose_once(method, path, raw=None):
            nonlocal lost
            result = original(method, path, raw)
            if method == "POST" and path == "/sessions" and not lost:
                lost = True
                raise TimeoutError()
            return result
        monkeypatch.setattr(admin, "_once", lose_once)
        if unwind:
            with pytest.raises(UncertainResult):
                with admin:
                    admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
        else:
            with admin:
                with pytest.raises(UncertainResult):
                    admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
                receipt = admin.retry_pending()
                assert receipt["session_id"] in admin._owned
        assert not registry._entries
        assert admin._closed and admin._connection is None
        admin.close()



def test_first_guard_rejection_does_not_create_uncertainty():
    with server() as (url, registry, gateway):
        with HTTPClient(url, "viewer") as viewer:
            with pytest.raises(HTTPError) as error:
                viewer.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
            assert error.value.status == 403 and viewer._pending is None
            assert not registry._entries


def test_matching_failed_creation_receipt_resolves_uncertainty(monkeypatch):
    with server() as (url, registry, gateway):
        with HTTPClient(url, "admin", retries=0) as admin:
            original = admin._once
            lost = False
            def lose_failure(method, path, raw=None):
                nonlocal lost
                try:
                    return original(method, path, raw)
                except HTTPError:
                    if method == "POST" and not lost:
                        lost = True
                        raise TimeoutError()
                    raise
            monkeypatch.setattr(admin, "_once", lose_failure)
            with pytest.raises(UncertainResult):
                admin.create(request_id=1, config=SessionConfig(size=2), seed=SeedSpec(17))
            with pytest.raises(HTTPError) as error:
                admin.retry_pending()
            assert error.value.status == 400 and error.value.response["request_id"] == 1
            assert admin._pending is None and not admin._owned and not registry._entries


def test_server_expiry_after_lost_creation_does_not_resolve_outcome(monkeypatch):
    with server() as (url, registry, gateway):
        with HTTPClient(url, "admin", retries=0) as admin:
            original = admin._once
            def lost(method, path, raw=None):
                result = original(method, path, raw)
                if method == "POST":
                    raise TimeoutError()
                return result
            monkeypatch.setattr(admin, "_once", lost)
            with pytest.raises(UncertainResult):
                admin.execute(creation())
            pending = admin._pending
            registry.clock = lambda: time.monotonic() + registry.limits.retry_seconds + 1
            monkeypatch.setattr(admin, "_once", original)
            with pytest.raises(UncertainResult) as error:
                admin.retry_pending()
            assert error.value.__cause__.status == 410
            assert admin._pending == pending and len(registry._entries) == 1
            with pytest.raises(UncertainResult):
                admin.execute(creation(2))


@pytest.mark.parametrize("change", ["request_id", "session_id", "partial"])
def test_foreign_or_incomplete_success_does_not_resolve_command(monkeypatch, change):
    with server() as (url, registry, gateway):
        with HTTPClient(url, "admin") as admin, HTTPClient(url, "away", retries=0) as player:
            session = admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
            state = session.read()
            command = envelope(session.session_id, 1, state["state_revision"], "step",
                               action=state["legal_actions"]["actions"][0])
            original = player._once
            def wrong_receipt(method, path, raw=None):
                result = original(method, path, raw)
                if method == "POST":
                    if change == "request_id":
                        return dict(result, request_id=99)
                    if change == "session_id":
                        return dict(result, session_id="another-session")
                    return {"request_id": 1, "ok": True}
                return result
            monkeypatch.setattr(player, "_once", wrong_receipt)
            with pytest.raises(UncertainResult):
                player.execute(command)
            assert registry._entries[session.session_id].cursor == 1
            with pytest.raises(UncertainResult):
                player.execute(dict(command, request_id=2))
            monkeypatch.setattr(player, "_once", original)
            assert player.retry_pending()["request_id"] == 1
            assert player._pending is None


def test_expired_owned_creation_cleanup_never_resends(monkeypatch):
    with server() as (url, registry, gateway):
        admin = HTTPClient(url, "admin", retries=0)
        original = admin._once
        def lost(method, path, raw=None):
            result = original(method, path, raw)
            if method == "POST":
                raise TimeoutError()
            return result
        monkeypatch.setattr(admin, "_once", lost)
        with pytest.raises(UncertainResult):
            admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
        path, raw, _ = admin._pending
        admin._pending = (path, raw, time.monotonic() - 1)
        monkeypatch.setattr(admin, "_once", lambda *a, **k: pytest.fail("expired cleanup sent a request"))
        with pytest.raises(UncertainResult):
            admin.close()
        assert admin._closed and admin._token is None and admin._connection is None
        assert len(registry._entries) == 1  # Expired orphan requires operator cleanup.



def test_failed_creation_recovery_still_closes_previously_owned_sessions(monkeypatch):
    from botbowl.lab.commands import GatewayLimits
    with server() as (url, registry, gateway):
        registry.limits = GatewayLimits(max_sessions=1)
        admin = HTTPClient(url, "admin", retries=0)
        original = admin._once
        lost = False
        def lose_failure(method, path, raw=None):
            nonlocal lost
            try:
                return original(method, path, raw)
            except HTTPError:
                if method == "POST" and not lost:
                    lost = True
                    raise TimeoutError()
                raise
        monkeypatch.setattr(admin, "_once", lose_failure)
        with pytest.raises(UncertainResult):
            with admin:
                admin.create(request_id=1, config=SessionConfig(size=1), seed=SeedSpec(17))
                admin.create(request_id=2, config=SessionConfig(size=1), seed=SeedSpec(18))
        assert not registry._entries
        assert admin._closed and admin._connection is None
