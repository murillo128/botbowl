"""HTTP authorization, bounded resources, lifecycle and command contract."""
from dataclasses import asdict
import json
import threading
import time

import pytest

from botbowl.lab.commands import Access, CommandGateway, GatewayLimits, SessionRegistry
from botbowl.lab.http import HTTPConfig, create_app
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.scenarios import scenario_spec
from botbowl.lab.session import SessionConfig


# Deliberately public fixture strings, never production credentials.
GRANTS = {"home": Access("player", "home"), "away": Access("player", "away"),
          "viewer": Access("spectator"), "limited": Access("evaluator"),
          "admin": Access("evaluator", capabilities=frozenset(
              {"snapshot", "restore", "close", "pause", "resume"}))}


def service(limits=None, config=None, clock=time.monotonic):
    registry = SessionRegistry(limits or GatewayLimits(), clock=clock)
    gateway = CommandGateway(registry, lambda credential: credential if credential in GRANTS else None,
                             creators={"admin": GRANTS})
    app = create_app(gateway, config or HTTPConfig(requests_per_window=10000))
    return registry, gateway, app


@pytest.fixture
def api():
    registry, gateway, app = service()
    yield registry, gateway, app.test_client()
    registry.close()


def headers(token="admin"):
    return {"Authorization": "Bearer " + token}


def envelope(sid, number, revision, op, **payload):
    return dict(session_id=sid, request_id=number, expected_revision=revision,
                payload=dict(op=op, **payload))


def creation(number=1, scenario=None):
    payload = {"scenario": scenario.to_json()} if scenario else {
        "config": asdict(SessionConfig(size=1, max_decisions=24)), "seed": SeedSpec(17).to_json()}
    return envelope("new", number, 0, "create", **payload)


def post(client, body, token="admin", path="/commands"):
    return client.post("/api/v1" + path, json=body, headers=headers(token))


def create(client, scenario=None):
    response = post(client, creation(scenario=scenario), path="/sessions")
    assert response.status_code == 200, response.json
    return response.json["session_id"]


def read(client, sid, token="viewer", suffix=""):
    return client.get("/api/v1/sessions/" + sid + suffix, headers=headers(token))


def step(client, sid, number=1, action=None):
    state = read(client, sid).json
    action = action or state["legal_actions"]["actions"][0]
    command = envelope(sid, number, state["state_revision"], "step", action=action)
    return post(client, command, action["actor_id"]), command


def test_auth_limits_and_schema_before_privileged_materialization(api, monkeypatch):
    registry, gateway, client = api
    monkeypatch.setattr(gateway, "_creation_inputs", lambda payload: pytest.fail("decoded privileged creation"))
    for token, status in (("bad", 401), ("viewer", 403)):
        response = client.post("/api/v1/sessions", data=b'{bad', content_type="application/json", headers=headers(token))
        assert response.status_code == status
    sid = registry.register(SessionConfig(size=1), SeedSpec(17), GRANTS)
    monkeypatch.setattr(gateway, "_export", lambda session: pytest.fail("snapshot materialized"))
    for token in ("home", "away", "viewer", "limited"):
        assert read(client, sid, token, "/snapshot").status_code == 403
        assert post(client, envelope(sid, 1, 1, "restore", snapshot={"engine": "secret"}), token).status_code == 403
        assert post(client, envelope(sid, 1, 1, "pause"), token).status_code == 403
    assert "secret" not in post(client, envelope(sid, 1, 1, "restore", snapshot={"engine": "secret"})).text
    assert client.post("/api/v1/commands", data=b"x" * (registry.limits.max_request_bytes + 1), headers=headers()).status_code == 413
    assert not registry._pending._value < registry.limits.max_pending


def test_creation_closed_inputs_and_idempotency(api):
    registry, gateway, client = api
    body = creation()
    result = post(client, body, path="/sessions")
    assert result.status_code == 200
    assert post(client, body, path="/sessions").json == result.json
    assert len(registry._entries) == 1
    body["payload"]["seed"]["master_seed"] = 18
    assert post(client, body, path="/sessions").status_code == 409
    for number, path in enumerate(("/tmp/config.json", "os.system"), 2):
        body = creation(number)
        body["payload"]["config"]["game_config"] = path
        response = post(client, body, path="/sessions")
        assert response.status_code == 400
        assert response.json["error"] == "UnsupportedCapability"
    for number, update in enumerate(({"scenario_id": "os.system"}, {"version": 999}), 4):
        body = creation(number, scenario_spec("movement", size=1))
        body["payload"]["scenario"].update(update)
        assert post(client, body, path="/sessions").json["error"] == "UnsupportedCapability"
    body = creation(6, scenario_spec("movement", size=1))
    body["payload"]["scenario"]["parameters"]["callback"] = "os.system"
    assert post(client, body, path="/sessions").status_code == 400


def test_errors_revision_retries_close_and_read_purity(api):
    registry, gateway, client = api
    sid = create(client)
    before = read(client, sid).json
    rng = registry._entries[sid].session._game.capture_rng_state()
    for _ in range(3):
        assert read(client, sid).json == before
    assert registry._entries[sid].session._game.capture_rng_state() == rng
    result, body = step(client, sid)
    assert result.status_code == 200
    assert post(client, body, body["payload"]["action"]["actor_id"]).json == result.json
    stale = envelope(sid, 2, 1, "step", action=body["payload"]["action"])
    assert post(client, stale, body["payload"]["action"]["actor_id"]).status_code == 409
    state = read(client, sid).json
    action = state["legal_actions"]["actions"][0]
    action["schema_version"] = 999
    assert post(client, envelope(sid, 3, state["state_revision"], "step", action=action), action["actor_id"]).status_code == 400
    assert post(client, envelope(sid, 4, state["state_revision"], "step", action=0), "away").status_code == 403
    assert read(client, "missing").status_code == 404
    close = envelope(sid, 1, state["state_revision"], "close")
    result = post(client, close)
    assert result.status_code == 200
    assert post(client, close).json == result.json
    assert read(client, sid).status_code == 409
    assert not registry._entries


def test_cursor_pagination_loss_expiry_and_slow_consumers():
    now = [0]
    registry, gateway, app = service(GatewayLimits(max_events=2, retry_seconds=1), clock=lambda: now[0])
    client = app.test_client()
    try:
        sid = create(client)
        cursor = read(client, sid).json["cursor"]
        for number in range(1, 4):
            assert step(client, sid, number)[0].status_code == 200
        assert read(client, sid, suffix="/events?cursor=" + cursor).status_code == 410
        page = read(client, sid, suffix="/events?cursor=" + sid + ":1&limit=1").json
        assert [e["sequence"] for e in page["events"]] == [2]
        page2 = read(client, sid, suffix="/events?cursor=" + page["cursor"] + "&limit=1").json
        assert [e["sequence"] for e in page2["events"]] == [3]
        assert read(client, sid, suffix="/events?cursor=" + page2["cursor"]).json["events"] == []
        for query in ("wait=nan", "wait=31", "limit=0", "limit=100000", "limit=1&limit=2"):
            assert read(client, sid, suffix="/events?cursor=" + page2["cursor"] + "&" + query).status_code == 400
        assert len(registry._entries[sid].events) == 2
        now[0] = 2
        assert post(client, creation(), path="/sessions").status_code == 410
    finally:
        registry.close()


def test_long_poll_does_not_lock_out_writers(api):
    registry, gateway, client = api
    sid = create(client)
    cursor = read(client, sid).json["cursor"]
    responses = []
    def poll():
        with client.application.test_client() as reader:
            responses.append(read(reader, sid, suffix="/events?cursor=" + cursor + "&wait=1"))
    thread = threading.Thread(target=poll)
    thread.start()
    time.sleep(.05)
    assert step(client, sid)[0].status_code == 200
    thread.join(2)
    assert not thread.is_alive()
    assert len(responses[0].json["events"]) == 1


def test_rate_capacity_and_sanitized_internal_failures():
    registry, gateway, app = service(GatewayLimits(max_sessions=1), HTTPConfig(requests_per_window=2))
    client = app.test_client()
    try:
        create(client)
        assert post(client, creation(2), path="/sessions").status_code == 429
        assert client.get("/api/v1/capabilities", headers=headers()).status_code == 429
        assert not registry._pending._value < registry.limits.max_pending
        gateway.read = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("PRIVATE"))
        response = read(client, "any")
        assert response.status_code == 500
        assert response.json == {"ok": False, "error": "internal_error"}
    finally:
        registry.close()


@pytest.mark.parametrize("config", [dict(host="0.0.0.0"), dict(host="::"), dict(max_wait=float("inf")), dict(max_inflight=0)])
def test_insecure_config_rejected(config):
    with pytest.raises(ValueError):
        HTTPConfig(**config)


def test_proxy_tls_debug_and_cors_boundaries(api):
    registry, gateway, client = api
    assert client.get("/api/v1/capabilities", headers={**headers(), "Origin": "https://other.test"}).status_code == 403
    response = client.get("/api/v1/capabilities", headers=headers())
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    secure = create_app(gateway, HTTPConfig(host="0.0.0.0", trusted_proxies=("127.0.0.2",))).test_client()
    assert secure.get("/api/v1/capabilities", headers={**headers(), "X-Forwarded-Proto": "https"}).status_code == 403
    assert secure.get("/api/v1/capabilities", headers={**headers(), "X-Forwarded-Proto": "https"}, environ_overrides={"REMOTE_ADDR": "127.0.0.2"}).status_code == 200
    client.application.debug = True
    assert client.get("/api/v1/capabilities", headers=headers()).status_code == 500


def test_pause_resume_and_scenario_snapshot_random_continuation(api):
    registry, gateway, client = api
    sid = create(client, scenario_spec("pickup", size=1, scenario_seed=13))
    start = read(client, sid).json
    action = next(a for a in start["legal_actions"]["actions"] if a["type"] == "START_MOVE")
    assert step(client, sid, action=action)[0].status_code == 200
    state = read(client, sid).json
    saved = read(client, sid, "admin", "/snapshot").json["snapshot"]
    move = next(a for a in state["legal_actions"]["actions"] if a["type"] == "MOVE" and a["position"] == {"x": 2, "y": 2})
    first, _ = step(client, sid, 2, move)
    assert first.status_code == 200
    outcome = read(client, sid).json["state"]
    assert post(client, envelope(sid, 1, first.json["state_revision"], "restore", snapshot=saved)).status_code == 200
    repeated, _ = step(client, sid, 3, move)
    assert repeated.status_code == 200
    actual = read(client, sid).json["state"]
    for key in ("primary", "derived", "next_actor", "terminated", "truncated", "scenario_success", "scenario_terminal"):
        assert actual[key] == outcome[key]
    # Pause a fresh nonterminal standard session and reject driver decisions.
    registry2, gateway2, app2 = service()
    try:
        c = app2.test_client()
        sid2 = create(c)
        assert post(c, envelope(sid2, 1, 1, "pause")).status_code == 200
        state = read(c, sid2).json
        assert state["paused"]
        assert step(c, sid2)[0].status_code == 400
        assert post(c, envelope(sid2, 2, state["state_revision"], "resume")).status_code == 200
        assert not read(c, sid2).json["paused"]
        assert step(c, sid2, 2)[0].status_code == 200
    finally:
        registry2.close()


def test_pause_preserves_running_clock_subset_and_snapshot_is_read_only(api):
    from botbowl.core.model import Clock
    from botbowl.lab.snapshots import LogicalTime
    registry, gateway, client = api
    sid = create(client)
    session = registry._entries[sid].session
    game = session._game
    clock = LogicalTime()
    game.time_source = clock
    primary = Clock(game.state.home_team, 60, True, time_source=clock)
    secondary = Clock(game.state.away_team, 10, False, time_source=clock)
    primary.pause()
    game.state.clocks = [primary, secondary]
    clock.value = 2
    assert post(client, envelope(sid, 1, 1, "pause")).status_code == 200
    clock.value = 20
    assert primary.get_running_time() == 0
    assert secondary.get_running_time() == 2
    before = (primary.to_json(), secondary.to_json())
    response = read(client, sid, "admin", "/snapshot")
    assert response.status_code == 200
    assert (primary.to_json(), secondary.to_json()) == before
    assert read(client, sid).json["paused"]
    assert post(client, envelope(sid, 2, 2, "resume")).status_code == 200
    assert not primary.is_running() and secondary.is_running()
    assert secondary.get_running_time() == 2
    assert post(client, envelope(sid, 3, 3, "restore", snapshot=response.json["snapshot"])).status_code == 200
    assert not read(client, sid).json["paused"]
    assert not game.state.clocks[0].is_running() and game.state.clocks[1].is_running()
    game.config.competition_mode = True
    revision = session.state_revision
    assert post(client, envelope(sid, 4, revision, "pause")).status_code == 400
    assert session.state_revision == revision


def test_inflight_admission_released_after_waiter_finishes():
    registry, gateway, app = service(config=HTTPConfig(max_inflight=1))
    client = app.test_client()
    sid = create(client)
    cursor = read(client, sid).json["cursor"]
    entered = threading.Event()
    release = threading.Event()
    original = gateway.read
    def held(*args, **kwargs):
        if kwargs.get("cursor"):
            entered.set()
            assert release.wait(2)
        return original(*args, **kwargs)
    gateway.read = held
    responses = []
    def wait():
        with app.test_client() as other:
            responses.append(read(other, sid, suffix="/events?cursor=" + cursor))
    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert entered.wait(2)
        assert read(client, sid).status_code == 429
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert responses[0].status_code == 200
        assert read(client, sid).status_code == 200
    finally:
        release.set()
        thread.join(2)
        registry.close()


def test_typed_engine_failure_maps_to_sanitized_500_and_cached_receipt(api, monkeypatch):
    from botbowl.lab.session import ExecutionFailure
    registry, gateway, client = api
    sid = create(client)
    session = registry._entries[sid].session
    def fail(*args):
        raise ExecutionFailure("PRIVATE engine details")
    monkeypatch.setattr(session, "step", fail)
    response, body = step(client, sid)
    assert response.status_code == 500
    assert response.json["error"] == "execution_failure"
    assert "PRIVATE" not in response.text
    assert post(client, body, body["payload"]["action"]["actor_id"]).json == response.json
