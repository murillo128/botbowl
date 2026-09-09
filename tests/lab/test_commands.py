"""Synchronized concurrency, replay, authorization and resource boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading

import pytest

from botbowl.lab.commands import (
    Access, CapacityExceeded, Closed, CommandGateway, Conflict, EventGap,
    ExpiredRequest, Forbidden, GatewayLimits, InvalidGeneration, InvalidRequest,
    SessionRegistry, Unauthenticated,
)
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.session import SessionConfig


class Clock:
    now = 0

    def __call__(self):
        return self.now


def setup(**limits):
    clock = Clock()
    registry = SessionRegistry(GatewayLimits(**limits), clock=clock)
    grants = {
        "home": Access("player", "home"), "away": Access("player", "away"),
        "viewer": Access("spectator"), "limited": Access("evaluator"),
        "admin": Access("evaluator", capabilities=frozenset(
            ("labels", "snapshot", "restore", "reset", "close"))),
    }
    sid = registry.register(SessionConfig(size=1), SeedSpec(17), grants,
                            labels={"outcome": "PRIVATE"})
    # Tokens are fixture data; production authentication belongs to the caller.
    gateway = CommandGateway(registry, lambda token: {
        "home-token": "home", "away-token": "away", "view-token": "viewer",
        "admin-token": "admin", "limited-token": "limited",
    }.get(token))
    return registry, gateway, sid, clock


def request(sid, number, revision, op="step", **payload):
    return json.dumps(dict(session_id=sid, request_id=number,
                           expected_revision=revision,
                           payload=dict(op=op, **payload))).encode()


def step_request(gateway, sid, number):
    state = gateway.read("view-token", sid)
    action = state["legal_actions"]["actions"][0]
    return action["actor_id"] + "-token", request(
        sid, number, state["state_revision"], action=action)


def evidence(registry, gateway, sid):
    session = registry._entries[sid].session
    return (gateway.read("view-token", sid), session._game.capture_rng_state(),
            deepcopy([e.to_json() for e in session._timeline.events]))


def test_two_writers_one_revision_and_retries_preserve_state_rng_events():
    registry, gateway, sid, _ = setup()
    token, raw = step_request(gateway, sid, 1)
    other = json.loads(raw)
    other["request_id"] = 2
    # Distinct authenticated principals for the same seat isolate revision CAS
    # from each principal's ordered request-ID contract.
    registry._entries[sid].grants["second"] = Access("player", "away")
    original = gateway._authenticate
    gateway._authenticate = lambda t: "second" if t == "second-token" else original(t)
    barrier = threading.Barrier(3)

    def submit(credential, data):
        barrier.wait()
        return gateway.execute(credential, data)

    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(submit, token, raw),
                pool.submit(submit, "second-token", json.dumps(other).encode())]
        barrier.wait()
        results = [j.result(timeout=10) for j in jobs]
    assert sorted(r["ok"] for r in results) == [False, True]
    assert next(r for r in results if not r["ok"])["error"] == "conflict"
    assert registry._entries[sid].session.state_revision == 2
    assert registry._entries[sid].cursor == 1
    assert registry._entries[sid].session._timeline.context.decision_seq == 1
    control_registry, control_gateway, control_sid, _ = setup()
    control_token, control_raw = step_request(control_gateway, control_sid, 1)
    assert control_gateway.execute(control_token, control_raw)["ok"]
    actual = evidence(registry, gateway, sid)
    expected = evidence(control_registry, control_gateway, control_sid)
    assert actual[1:] == expected[1:]
    assert actual[0]["state"] == expected[0]["state"]
    before = actual
    assert gateway.execute(token, raw) == results[0]
    assert evidence(registry, gateway, sid) == before
    token2, next_raw = step_request(gateway, sid, 3)
    assert gateway.execute(token2, next_raw)["ok"]
    advanced = evidence(registry, gateway, sid)
    assert gateway.execute(token, raw) == results[0]
    assert evidence(registry, gateway, sid) == advanced


def test_same_id_concurrent_and_changed_payload():
    registry, gateway, sid, _ = setup()
    token, raw = step_request(gateway, sid, 1)
    barrier = threading.Barrier(3)

    def submit():
        barrier.wait()
        return gateway.execute(token, raw)

    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(submit), pool.submit(submit)]
        barrier.wait()
        first, second = [j.result(timeout=10) for j in jobs]
    assert first == second and first["ok"]
    assert registry._entries[sid].cursor == 1
    changed = json.loads(raw)
    changed["expected_revision"] += 1
    before = evidence(registry, gateway, sid)
    with pytest.raises(Conflict):
        gateway.execute(token, json.dumps(changed).encode())
    assert evidence(registry, gateway, sid) == before


@pytest.mark.parametrize("evict", [False, True])
def test_expired_requests_never_execute_again(evict):
    registry, gateway, sid, clock = setup(max_retries=1)
    token, raw = step_request(gateway, sid, 1)
    assert gateway.execute(token, raw)["ok"]
    if evict:
        token2, data = step_request(gateway, sid, 2)
        assert gateway.execute(token2, data)["ok"]
    else:
        clock.now = 300
    before = evidence(registry, gateway, sid)
    with pytest.raises(ExpiredRequest):
        gateway.execute(token, raw)
    assert evidence(registry, gateway, sid) == before
    assert len(registry._entries[sid].retries) <= 1


def test_restart_and_retired_session_generation():
    _, gateway, sid, _ = setup()
    token, raw = step_request(gateway, sid, 1)
    _, restarted, new_sid, _ = setup()
    assert new_sid != sid
    with pytest.raises(InvalidGeneration):
        restarted.execute(token, raw)
    with pytest.raises(Unauthenticated):
        gateway.read(sid, sid)


@pytest.mark.parametrize("token", ["view-token", "limited-token", "admin-token", "home-token"])
def test_permissions_and_actor_spoofing(token):
    registry, gateway, sid, _ = setup()
    _, raw = step_request(gateway, sid, 1)
    before = evidence(registry, gateway, sid)
    with pytest.raises(Forbidden):
        gateway.execute(token, raw)
    if token == "home-token":
        data = json.loads(raw)
        data["payload"]["action"]["actor_id"] = "home"
        assert gateway.execute(token, json.dumps(data).encode())["error"] == "forbidden"
    assert evidence(registry, gateway, sid) == before


@pytest.mark.parametrize("resource", ["labels", "snapshot"])
@pytest.mark.parametrize("token", ["home-token", "away-token", "view-token", "limited-token"])
def test_privileged_reads_require_explicit_capability(token, resource):
    _, gateway, sid, _ = setup()
    with pytest.raises(Forbidden):
        gateway.read(token, sid, resource=resource)
    assert "PRIVATE" not in json.dumps(gateway.read(token, sid))


def test_authentication_before_parsing_and_materialization(monkeypatch):
    _, gateway, sid, _ = setup()
    import botbowl.lab.commands as commands

    def sentinel(*args, **kwargs):
        pytest.fail("Untrusted input reached privileged materialization")

    monkeypatch.setattr(commands, "read_snapshot", sentinel)
    with pytest.raises(Unauthenticated):
        gateway.execute("bad", object())
    with pytest.raises(Forbidden):
        gateway.execute("view-token", request(sid, 1, 1, "restore", snapshot={
            "module": "sentinel", "callable": "sentinel", "path": "/tmp/sentinel"}))
    for field in ("role", "principal", "module", "callable", "path"):
        data = json.loads(request(sid, 1, 1, "reset"))
        data[field] = "sentinel"
        with pytest.raises(InvalidRequest):
            gateway.execute("admin-token", json.dumps(data).encode())
        data = json.loads(request(sid, 1, 1, "reset"))
        data["payload"][field] = "sentinel"
        with pytest.raises(InvalidRequest):
            gateway.execute("admin-token", json.dumps(data).encode())
    assert not gateway.execute("admin-token", request(
        sid, 1, 1, "restore", snapshot={"module": "sentinel"}))["ok"]


def test_snapshot_roundtrip_invalid_import_atomic_and_reset_monotonic():
    registry, gateway, sid, _ = setup()
    original = evidence(registry, gateway, sid)
    exported = gateway.read("admin-token", sid, resource="snapshot")["snapshot"]
    assert "SnapshotFileV1" in exported["engine"]
    assert gateway.read("admin-token", sid, resource="labels")["labels"] == {"outcome": "PRIVATE"}
    bad = dict(exported, engine='{"module":"sentinel","callable":"sentinel"}')
    assert not gateway.execute("admin-token", request(sid, 1, 1, "restore", snapshot=bad))["ok"]
    assert evidence(registry, gateway, sid) == original
    token, raw = step_request(gateway, sid, 1)
    assert gateway.execute(token, raw)["ok"]
    restored = gateway.execute("admin-token", request(sid, 2, 2, "restore", snapshot=exported))
    assert restored["ok"] and restored["state_revision"] == 3
    assert registry._entries[sid].session._game.capture_rng_state() == original[1]
    assert registry._entries[sid].session._timeline.context.decision_seq == 0
    reset = gateway.execute("admin-token", request(sid, 3, 3, "reset"))
    assert reset["ok"] and reset["state_revision"] == 4
    assert registry._entries[sid].cursor == 3


def test_branches_have_explicit_independent_permissions():
    registry, gateway, sid, _ = setup()
    parent = registry._entries[sid]
    branch = registry.register(parent.config, parent.seed,
                               {"limited": Access("evaluator")},
                               branch=parent.session.snapshot())
    assert gateway.read("limited-token", branch)["state_revision"] == 2
    with pytest.raises(Forbidden):
        gateway.read("admin-token", branch)
    before = evidence(registry, gateway, sid)
    with pytest.raises(Forbidden):
        gateway.execute("limited-token", request(branch, 1, 2, "reset"))
    assert evidence(registry, gateway, sid) == before


def test_event_overflow_and_detached_slow_consumer():
    registry, gateway, sid, _ = setup(max_events=2)
    slow = gateway.read("view-token", sid)
    cursor = slow["cursor"]
    for number in range(1, 4):
        assert gateway.execute("admin-token", request(sid, number, number, "reset"))["ok"]
    assert len(registry._entries[sid].events) == 2
    with pytest.raises(EventGap):
        gateway.read("view-token", sid, cursor=cursor)
    events = gateway.read("view-token", sid, cursor=sid + ":1")["events"]
    assert [e["sequence"] for e in events] == [2, 3]
    events.clear()
    slow["state"]["primary"]["data"]["players"].clear()
    fresh = gateway.read("view-token", sid)
    assert fresh["state"]["primary"]["data"]["players"]
    assert gateway.read("view-token", sid, cursor=fresh["cursor"])["events"] == []
    for bad in (sid + ":99", "other:1", -1, sid + ":-1"):
        with pytest.raises(EventGap):
            gateway.read("view-token", sid, cursor=bad)


def test_pending_admission_is_bounded_without_sleep(monkeypatch):
    registry, gateway, sid, _ = setup(max_pending=1)
    session = registry._entries[sid].session
    original = session.step
    entered, release = threading.Event(), threading.Event()

    def held(*args):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(session, "step", held)
    token, raw = step_request(gateway, sid, 1)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(gateway.execute, token, raw)
        assert entered.wait(timeout=10)
        try:
            with pytest.raises(CapacityExceeded):
                gateway.read("view-token", sid)
            with pytest.raises(CapacityExceeded):
                gateway.execute(token, raw)
        finally:
            release.set()
        assert future.result(timeout=10)["ok"]
    assert gateway.read("view-token", sid)["state_revision"] == 2


def test_close_concurrent_releases_once_and_reuses_capacity(monkeypatch):
    registry, gateway, sid, _ = setup(max_sessions=1)
    entry = registry._entries[sid]
    calls = []
    original = entry.session.close

    def close():
        calls.append(1)
        original()

    monkeypatch.setattr(entry.session, "close", close)
    barrier = threading.Barrier(3)
    raw = request(sid, 1, 1, "close")

    def submit():
        barrier.wait()
        try:
            return gateway.execute("admin-token", raw)
        except (InvalidGeneration, Closed) as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(submit), pool.submit(submit)]
        barrier.wait()
        results = [j.result(timeout=10) for j in jobs]
    assert calls == [1]
    assert results[0] == results[1] and results[0]["ok"]
    assert gateway.execute("admin-token", raw) == results[0]
    assert entry.session is None and not registry._entries
    with pytest.raises((InvalidGeneration, Closed)):
        gateway.execute("admin-token", request(sid, 2, 2, "reset"))
    new_sid = registry.register(SessionConfig(size=1), SeedSpec(18), {"viewer": Access("spectator")})
    assert new_sid != sid


def test_session_and_input_bounds():
    registry, gateway, sid, _ = setup(max_sessions=1, max_request_bytes=1024, max_depth=8)
    with pytest.raises(CapacityExceeded):
        registry.register(SessionConfig(size=1), SeedSpec(18), {"v": Access("spectator")})
    for raw in (b" " * 1025, b"[" * 9 + b"]" * 9, b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'\xff'):
        with pytest.raises(InvalidRequest):
            gateway.execute("admin-token", raw)
    assert gateway.read("view-token", sid)["state_revision"] == 1


def test_failed_command_replay_does_not_become_legal_later():
    registry, gateway, sid, _ = setup()
    token, valid = step_request(gateway, sid, 1)
    data = json.loads(valid)
    data["payload"]["action"]["type"] = "HEADS"
    invalid = json.dumps(data).encode()
    failed = gateway.execute(token, invalid)
    assert not failed["ok"]
    data = json.loads(valid)
    data["request_id"] = 2
    assert gateway.execute(token, json.dumps(data).encode())["ok"]
    before = evidence(registry, gateway, sid)
    assert gateway.execute(token, invalid) == failed
    assert evidence(registry, gateway, sid) == before


def test_engine_failure_receipt_and_events_are_replayed(monkeypatch, caplog):
    registry, gateway, sid, _ = setup()
    entry = registry._entries[sid]
    token, raw = step_request(gateway, sid, 1)
    original = entry.session.step

    def fail_after_applying(*args):
        original(*args)
        raise RuntimeError("SECRET snapshot token PRIVATE")

    monkeypatch.setattr(entry.session, "step", fail_after_applying)
    result = gateway.execute(token, raw)
    assert result["error"] == "execution_failure"
    assert result["state_revision"] == 3  # one step, then administrative close
    assert entry.session is None
    assert entry.events[-1]["state_revision"] == 3
    assert gateway.execute(token, raw) == result
    assert "SECRET" not in caplog.text and "PRIVATE" not in caplog.text
    assert "execution_failure" in caplog.text


def test_retired_receipts_and_capacity_are_bounded():
    registry, gateway, sid, _ = setup(max_sessions=1)
    old = request(sid, 1, 1, "close")
    receipt = gateway.execute("admin-token", old)
    assert gateway.execute("admin-token", old) == receipt
    second = registry.register(SessionConfig(size=1), SeedSpec(18), {
        "admin": Access("evaluator", capabilities=frozenset(("close",)))})
    assert gateway.execute("admin-token", request(second, 1, 1, "close"))["ok"]
    assert len(registry._retired) == 1
    with pytest.raises(InvalidGeneration):
        gateway.execute("admin-token", old)


def test_other_session_reader_runs_while_a_writer_is_busy(monkeypatch):
    registry, gateway, sid, _ = setup()
    second = registry.register(SessionConfig(size=1), SeedSpec(18), {"viewer": Access("spectator")})
    session = registry._entries[sid].session
    original = session.step
    entered, release = threading.Event(), threading.Event()

    def held(*args):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(session, "step", held)
    token, raw = step_request(gateway, sid, 1)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(gateway.execute, token, raw)
        assert entered.wait(timeout=10)
        try:
            assert gateway.read("view-token", second)["state_revision"] == 1
        finally:
            release.set()
        assert future.result(timeout=10)["ok"]
