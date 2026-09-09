"""Bounded, transport-independent commands for exclusively owned lab sessions.

Only ``CommandGateway`` accepts untrusted input. Registry construction and
registration are trusted configuration APIs; session IDs are never credentials.
"""
from collections import OrderedDict, deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable, Dict, FrozenSet, Optional
import uuid

from .actions import ActionV1
from .randomness import SeedSpec
from .session import ExecutionFailure, SessionConfig, SessionError, SessionSnapshot, SimulationSession
from .snapshot_io import SnapshotLimits, read_snapshot, write_snapshot
from .snapshots import SnapshotError


class GatewayError(Exception):
    code = "gateway_error"

    def __init__(self):
        # Never retain user payloads, credentials or engine diagnostics.
        super().__init__(self.code)


class UnsupportedCapability(GatewayError):
    code = "UnsupportedCapability"


class Unauthenticated(GatewayError):
    code = "unauthenticated"


class Forbidden(GatewayError):
    code = "forbidden"


class InvalidRequest(GatewayError):
    code = "invalid_request"


class Conflict(GatewayError):
    code = "conflict"


class ExpiredRequest(GatewayError):
    code = "expired_request"


class InvalidGeneration(GatewayError):
    code = "invalid_generation"


class CapacityExceeded(GatewayError):
    code = "capacity_exceeded"


class Closed(GatewayError):
    code = "closed"


class EventGap(GatewayError):
    """Use read() without a cursor to atomically resynchronize state and cursor."""

    code = "event_gap"


@dataclass(frozen=True)
class GatewayLimits:
    max_sessions: int = 32
    max_request_bytes: int = 4 * 1024 * 1024
    max_depth: int = 32
    max_pending: int = 32
    max_retries: int = 64
    retry_seconds: int = 300
    max_events: int = 128
    max_response_bytes: int = 4 * 1024 * 1024
    max_principals: int = 64

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in self.__dict__.values()):
            raise ValueError("Limits must be positive integers")
        if self.max_depth > 128:
            raise ValueError("Request nesting limit exceeds 128")


@dataclass(frozen=True)
class Access:
    """Trusted per-principal grant on one session (including a named branch).

    Evaluator capabilities are individually selected; its role grants no write
    or privileged access implicitly. Players can step only their configured side.
    """

    role: str
    team: Optional[str] = None
    capabilities: FrozenSet[str] = frozenset()

    def __post_init__(self):
        if self.role not in ("player", "spectator", "evaluator"):
            raise ValueError("Unknown role")
        if (self.role == "player" and self.team not in ("home", "away")) or (
            self.role != "player" and self.team is not None
        ):
            raise ValueError("Only players have a configured team")
        if type(self.capabilities) is not frozenset or self.capabilities - {
            "labels", "snapshot", "restore", "reset", "close", "pause", "resume"
        }:
            raise ValueError("Unknown capabilities")
        if self.role != "evaluator" and self.capabilities:
            raise ValueError("Privileged capabilities require evaluator role")


@dataclass(frozen=True)
class Command:
    session_id: str
    request_id: int
    expected_revision: int
    payload: dict

    @classmethod
    def parse(cls, data):
        _keys(data, {"session_id", "request_id", "expected_revision", "payload"})
        if (type(data["session_id"]) is not str or len(data["session_id"]) > 128
                or type(data["request_id"]) is not int
                or not 1 <= data["request_id"] < 2**63
                or type(data["expected_revision"]) is not int
                or not 0 <= data["expected_revision"] < 2**63
                or type(data["payload"]) is not dict):
            raise InvalidRequest()
        payload = data["payload"]
        op = payload.get("op")
        schemas = {
            "step": {"op", "action"}, "reset": {"op"}, "close": {"op"},
            "restore": {"op", "snapshot"},
            "pause": {"op"}, "resume": {"op"},
            "create": {"op", "config", "seed"} if "config" in payload else {"op", "scenario"},
        }
        if type(op) is not str:
            raise InvalidRequest()
        if op not in schemas:
            raise UnsupportedCapability()
        _keys(payload, schemas[op])
        return cls(**data)


def _keys(data, expected):
    if type(data) is not dict or set(data) != expected:
        raise InvalidRequest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest()
        result[key] = value
    return result


def _invalid_constant(value):
    raise InvalidRequest()


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise InvalidRequest()
    return number


def _encode(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _parse(raw, limits):
    if type(raw) is not bytes or len(raw) > limits.max_request_bytes:
        raise InvalidRequest()
    # Check structural depth before invoking the recursive JSON decoder.
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > limits.max_depth:
                raise InvalidRequest()
        elif char in (93, 125):
            depth -= 1
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_invalid_constant, parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidRequest() from None


@dataclass
class _Entry:
    session: Optional[SimulationSession]
    config: SessionConfig
    seed: SeedSpec
    grants: Dict[str, Access]
    labels: dict
    lock: object = field(default_factory=threading.Lock)
    retries: OrderedDict = field(default_factory=OrderedDict)
    highwater: dict = field(default_factory=dict)
    events: deque = field(default_factory=deque)
    cursor: int = 0
    revision: int = 0


class SessionRegistry:
    """Trusted owner. Register only configured sessions and branch snapshots.

    Closing retires the entry and releases its live session slot. At most
    max_sessions retired entries retain retry receipts; eviction invalidates the
    retired generation. UUID generations
    and never-reused counters invalidate old IDs without unbounded tombstones.
    Do not use a registered session through another writer or fork this registry
    into another process. Restarting requires a new registry and new session IDs.
    """

    def __init__(self, limits=GatewayLimits(), snapshot_limits=SnapshotLimits(),
                 clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.snapshot_limits = snapshot_limits
        self.clock = clock
        self._generation = uuid.uuid4().hex
        self._serial = 0
        self._closed = False
        self._entries = {}
        self._retired = OrderedDict()
        self._lock = threading.Lock()
        self._pending = threading.BoundedSemaphore(limits.max_pending)

    def register(self, config: SessionConfig, seed: SeedSpec,
                 grants: Dict[str, Access], *, labels=None,
                 branch: Optional[SessionSnapshot] = None, scenario=None) -> str:
        """Create a session or trusted branch with explicit, independent grants."""
        if (not grants or len(grants) > self.limits.max_principals
                or any(type(k) is not str or not k or len(k) > 128
                       or type(v) is not Access for k, v in grants.items())):
            raise ValueError("Invalid trusted grants")
        label_data = {} if labels is None else labels
        # Apply the same bounds to trusted labels retained by the gateway.
        label_data = _parse(_encode(label_data), self.limits)
        if type(label_data) is not dict:
            raise ValueError("Labels must be an object")
        with self._lock:
            if self._closed:
                raise Closed()
            if len(self._entries) >= self.limits.max_sessions:
                raise CapacityExceeded()
            if scenario is None:
                session = SimulationSession(config, seed)
            else:
                from .scenarios import create_scenario
                session = create_scenario(scenario)
            try:
                if branch is not None:
                    session.restore(branch, session.state_revision)
                self._serial += 1
                session_id = "%s:%d" % (self._generation, self._serial)
                self._entries[session_id] = _Entry(
                    session, deepcopy(config), deepcopy(seed), dict(grants), label_data,
                    revision=session.state_revision,
                )
                return session_id
            except Exception:
                session.close()
                raise

    def close(self):
        """Idempotent owner shutdown; release all sessions and retained receipts."""
        with self._lock:
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
            self._retired.clear()
        for entry in entries:
            with entry.lock:
                if entry.session is not None:
                    entry.session.close()
                    entry.session = None
                entry.retries.clear()
                entry.labels.clear()

    @contextmanager
    def _admit(self):
        if not self._pending.acquire(blocking=False):
            raise CapacityExceeded()
        try:
            yield
        finally:
            self._pending.release()

    def _get(self, session_id, principal):
        if type(session_id) is not str or len(session_id) > 128:
            raise InvalidGeneration()
        with self._lock:
            entry = self._entries.get(session_id) or self._retired.get(session_id)
        if entry is None:
            raise InvalidGeneration()
        grant = entry.grants.get(principal)
        if grant is None:
            raise Forbidden()
        return entry, grant

    def _remove(self, session_id, entry):
        with self._lock:
            if self._entries.get(session_id) is entry:
                del self._entries[session_id]
                self._retired[session_id] = entry
                while len(self._retired) > self.limits.max_sessions:
                    self._retired.popitem(last=False)


class CommandGateway:
    """Authenticate transport credentials before decoding any request bytes.

    ``authenticate`` is a trusted callable returning a stable principal ID or
    None. It must verify credentials itself; never pass a request's principal or
    role through as authentication. Methods return detached JSON dictionaries;
    no callback/network response is invoked while holding a session lock.
    """

    def __init__(self, registry: SessionRegistry, authenticate: Callable, *, creators=None):
        self.registry = registry
        self._authenticate = authenticate
        self._creators = deepcopy(creators or {})
        if len(self._creators) > registry.limits.max_principals:
            raise ValueError("Too many creators")
        for principal, grants in self._creators.items():
            if (type(principal) is not str or not principal or len(principal) > 128
                    or principal not in grants or not grants
                    or len(grants) > registry.limits.max_principals
                    or any(type(k) is not str or not k or len(k) > 128 or type(v) is not Access
                           for k, v in grants.items())):
                raise ValueError("Invalid creation grants")
        self._creation_lock = threading.Lock()
        self._creation_retries = OrderedDict()
        self._creation_highwater = {}

    def _principal(self, credential):
        try:
            principal = self._authenticate(credential)
        except Exception:
            raise Unauthenticated() from None
        if type(principal) is not str or not principal or len(principal) > 128:
            raise Unauthenticated()
        return principal

    def _bounded(self, response):
        if len(_encode(response)) > self.registry.limits.max_response_bytes:
            raise CapacityExceeded()
        return deepcopy(response)

    def create(self, credential, raw):
        """Idempotent allocation using operator-owned grants and closed inputs."""
        principal = self._principal(credential)
        if principal not in self._creators:
            raise Forbidden()
        with self.registry._admit(), self._creation_lock:
            command = Command.parse(_parse(raw, self.registry.limits))
            if (command.session_id != "new" or command.expected_revision != 0
                    or command.payload["op"] != "create"):
                raise InvalidRequest()
            now = self.registry.clock()
            for key, (_, expiry, _) in list(self._creation_retries.items()):
                if expiry <= now:
                    del self._creation_retries[key]
            key = (principal, command.request_id)
            digest = hashlib.sha256(_encode(command.__dict__)).digest()
            cached = self._creation_retries.get(key)
            if cached:
                if cached[0] != digest:
                    raise Conflict()
                return deepcopy(cached[2])
            if command.request_id <= self._creation_highwater.get(principal, 0):
                raise ExpiredRequest()
            self._creation_highwater[principal] = command.request_id
            result = {"session_id": "new", "request_id": command.request_id,
                      "ok": False, "error": None, "state_revision": 0, "cursor": None}
            try:
                config, seed, scenario = self._creation_inputs(command.payload)
                sid = self.registry.register(config, seed, self._creators[principal], scenario=scenario)
                entry, _ = self.registry._get(sid, principal)
                result.update(session_id=sid, ok=True, state_revision=entry.revision,
                              cursor="%s:0" % sid)
            except GatewayError as error:
                result["error"] = error.code
            except (SessionError, ValueError, TypeError):
                result["error"] = "invalid_request"
            self._creation_retries[key] = (digest, self.registry.clock() + self.registry.limits.retry_seconds,
                                           deepcopy(result))
            while len(self._creation_retries) > self.registry.limits.max_retries:
                self._creation_retries.popitem(last=False)
            return result

    @staticmethod
    def _creation_inputs(payload):
        if "scenario" in payload:
            from .scenarios import ScenarioSpecV1, SCENARIO_RECIPES
            data = payload["scenario"]
            if type(data) is not dict:
                raise InvalidRequest()
            recipe = data.get("scenario_id")
            if type(recipe) is not str or recipe not in SCENARIO_RECIPES:
                raise UnsupportedCapability()
            if data.get("version") != SCENARIO_RECIPES[recipe].version:
                raise UnsupportedCapability()
            return None, None, ScenarioSpecV1.from_json(data)
        data = payload["config"]
        _keys(data, set(SessionConfig.__dataclass_fields__))
        # Only this explicit packaged profile is exposed, never config/team paths.
        if (data["game_config"] is not None or data["home_team"] != "human"
                or data["away_team"] != "human"):
            raise UnsupportedCapability()
        if (type(data["size"]) is not int or data["size"] not in (1, 3, 5, 7, 11)
                or type(data["max_decisions"]) is not int
                or not 0 <= data["max_decisions"] <= 10000
                or type(data["max_steps"]) is not int or not 1 <= data["max_steps"] <= 100000):
            raise InvalidRequest()
        _keys(payload["seed"], set(SeedSpec.__dataclass_fields__))
        return SessionConfig(**data), SeedSpec(**payload["seed"]), None

    def read(self, credential, session_id, *, cursor=None, resource="state"):
        """Read a coherent copy, or replay bounded public change notifications.

        No cursor returns the current state and cursor atomically. A cursor must
        be copied from this session's read/execute response; a missing prefix,
        future cursor or overwritten range explicitly raises EventGap.
        """
        principal = self._principal(credential)
        with self.registry._admit():
            entry, grant = self.registry._get(session_id, principal)
            with entry.lock:
                session = entry.session
                if session is None:
                    raise Closed()
                if type(resource) is not str or resource not in ("state", "labels", "snapshot"):
                    raise InvalidRequest()
                if resource != "state" and resource not in grant.capabilities:
                    raise Forbidden()
                token = "%s:%d" % (session_id, entry.cursor)
                result = {"session_id": session_id, "state_revision": entry.revision,
                          "cursor": token, "paused": session.paused}
                if cursor is not None:
                    if resource != "state":
                        raise InvalidRequest()
                    if type(cursor) is not str or len(cursor) > 160:
                        raise EventGap()
                    prefix, _, suffix = cursor.rpartition(":")
                    if prefix != session_id or not suffix.isascii() or not suffix.isdecimal():
                        raise EventGap()
                    seq = int(suffix)
                    oldest = entry.events[0]["sequence"] if entry.events else entry.cursor + 1
                    if seq < oldest - 1 or seq > entry.cursor:
                        raise EventGap()
                    result["events"] = [deepcopy(e) for e in entry.events if e["sequence"] > seq]
                elif resource == "state":
                    result["state"] = session.observe(grant.team).to_json()
                    result["legal_actions"] = session.legal_actions(grant.team).to_json()
                elif resource == "labels":
                    result["labels"] = deepcopy(entry.labels)
                else:
                    result["snapshot"] = self._export(session)
                return self._bounded(result)

    def execute(self, credential, raw: bytes):
        principal = self._principal(credential)
        with self.registry._admit():
            command = Command.parse(_parse(raw, self.registry.limits))
            if command.payload["op"] == "create":
                raise InvalidRequest()
            entry, grant = self.registry._get(command.session_id, principal)
            digest = hashlib.sha256(_encode(command.__dict__)).digest()
            with entry.lock:
                now = self.registry.clock()
                for key, (_, expiry, _) in list(entry.retries.items()):
                    if expiry <= now:
                        del entry.retries[key]
                key = (principal, command.request_id)
                cached = entry.retries.get(key)
                if cached is not None:
                    if cached[0] != digest:
                        raise Conflict()
                    return deepcopy(cached[2])
                if command.request_id <= entry.highwater.get(principal, 0):
                    raise ExpiredRequest()
                if entry.session is None:
                    raise Closed()
                # Check capability before inspecting nested privileged material.
                op = command.payload["op"]
                if op == "step":
                    if grant.role != "player":
                        raise Forbidden()
                    action = command.payload["action"]
                    if type(action) is not dict or action.get("actor_id") != grant.team:
                        raise Forbidden()
                elif op not in grant.capabilities:
                    raise Forbidden()
                entry.highwater[principal] = command.request_id
                previous = entry.session.state_revision
                response = {"session_id": command.session_id, "request_id": command.request_id,
                            "ok": False, "error": None}
                try:
                    if command.expected_revision != previous:
                        raise Conflict()
                    self._apply(entry, command, grant)
                    response["ok"] = True
                except GatewayError as error:
                    response["error"] = error.code
                except ExecutionFailure:
                    response["error"] = "execution_failure"
                except (SessionError, SnapshotError, ValueError, TypeError):
                    response["error"] = "invalid_command"
                except Exception:
                    # Uncertain application is never retried. Fail closed and
                    # release the engine even if an unexpected adapter fails.
                    response["error"] = "execution_failure"
                    try:
                        entry.session.close()
                    except Exception:
                        pass
                    logging.getLogger(__name__).error("gateway execution_failure")
                entry.revision = entry.session.state_revision
                if entry.revision != previous:
                    entry.cursor += 1
                    entry.events.append({"sequence": entry.cursor,
                                         "state_revision": entry.revision, "operation": op,
                                         "ok": response["ok"]})
                    while len(entry.events) > self.registry.limits.max_events:
                        entry.events.popleft()
                response.update(state_revision=entry.revision,
                                cursor="%s:%d" % (command.session_id, entry.cursor))
                entry.retries[key] = (digest, self.registry.clock() + self.registry.limits.retry_seconds, deepcopy(response))
                while len(entry.retries) > self.registry.limits.max_retries:
                    entry.retries.popitem(last=False)
                if entry.session.closed:
                    entry.session = None
                    entry.labels.clear()
                    entry.config = None
                    entry.seed = None
                    self.registry._remove(command.session_id, entry)
                return deepcopy(response)

    def _apply(self, entry, command, grant):
        session = entry.session
        op = command.payload["op"]
        if op == "step":
            action = ActionV1.from_json(command.payload["action"])
            if session.observe(grant.team).next_actor != grant.team:
                raise Forbidden()
            session.step(action, command.expected_revision)
        elif op in ("pause", "resume"):
            session.set_paused(op == "pause", command.expected_revision)
        elif op == "reset":
            if entry.config is None:
                raise UnsupportedCapability()
            session.reset(entry.config, entry.seed)
        elif op == "restore":
            snapshot = command.payload["snapshot"]
            from .scenarios import ScenarioSession, ScenarioSnapshot, ScenarioSpecV1
            scenario_spec = None
            if isinstance(session, ScenarioSession):
                _keys(snapshot, {"spec", "session"})
                scenario_spec = ScenarioSpecV1.from_json(snapshot["spec"])
                snapshot = snapshot["session"]
            _keys(snapshot, {"schema_version", "scope", "accepted_decisions", "max_decisions",
                             "max_steps", "truncation_reason", "engine"})
            if type(snapshot["engine"]) is not str:
                raise InvalidRequest()
            raw = snapshot["engine"].encode("utf-8")
            if len(raw) > self.registry.snapshot_limits.max_bytes:
                raise InvalidRequest()
            with tempfile.TemporaryDirectory(prefix="botbowl-command-") as directory:
                path = Path(directory) / "snapshot.json"
                path.write_bytes(raw)
                engine = read_snapshot(path, limits=self.registry.snapshot_limits)
            data = dict(snapshot, engine=engine)
            saved = SessionSnapshot(**data)
            if scenario_spec is not None:
                saved = ScenarioSnapshot(scenario_spec, saved)
            session.restore(saved, command.expected_revision)
        else:
            session.close()

    def _export(self, session):
        from .scenarios import ScenarioSnapshot
        snapshot = session.snapshot()
        spec = None
        if isinstance(snapshot, ScenarioSnapshot):
            spec, snapshot = snapshot.spec, snapshot.session
        with tempfile.TemporaryDirectory(prefix="botbowl-command-") as directory:
            path = Path(directory) / "snapshot.json"
            write_snapshot(path, snapshot.engine, limits=self.registry.snapshot_limits)
            engine = path.read_text(encoding="utf-8")
        result = {name: (engine if name == "engine" else getattr(snapshot, name))
                  for name in snapshot.__dataclass_fields__}
        return result if spec is None else {"spec": spec.to_json(), "session": result}
