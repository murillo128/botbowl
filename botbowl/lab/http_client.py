"""Synchronous, dependency-free HTTP/JSON SDK over the lab command contract.

Clients are single-threaded. Allocate increasing request IDs per principal and
session, including when several clients share a principal. Credentials belong to
the caller; no environment, token file, redirect or proxy discovery is implicit.
"""
from dataclasses import asdict
import http.client
import ipaddress
import json
import math
import time
from urllib.parse import quote, urlencode, urlsplit


class HTTPError(Exception):
    def __init__(self, status, response):
        self.status = status
        self.response = response
        self.code = response.get("error", "invalid_response")
        super().__init__(self.code)


class UncertainResult(Exception):
    """A write may have committed. Call retry_pending() or read to reconcile."""

    def __init__(self):
        super().__init__("Write outcome is uncertain; retain the same request identity")


class HTTPClient:
    def __init__(self, url, token, *, connect_timeout=5.0, read_timeout=10.0,
                 retries=2, max_response_bytes=4 * 1024 * 1024):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/", "/api/v1", "/api/v1/")):
            raise ValueError("Expected an HTTP(S) server URL without credentials")
        if parsed.scheme == "http":
            try:
                local = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                local = parsed.hostname == "localhost"
            if not local:
                raise ValueError("Remote credentials require HTTPS")
        if type(token) is not str or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("Expected a caller-supplied bearer credential")
        for timeout in (connect_timeout, read_timeout):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Timeouts must be finite and positive")
        if type(retries) is not int or not 0 <= retries <= 5:
            raise ValueError("Retries must be between zero and five")
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError("Response limit must be positive")
        self._parsed, self._token = parsed, token
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout
        self.retries, self.max_response_bytes = retries, max_response_bytes
        self._connection = None
        self._closed = False
        self._pending = None
        self._pending_uncertain = False
        self._pending_owns_creation = False
        self._capabilities = None
        self._owned = {}

    def _disconnect(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _once(self, method, path, raw=None):
        if self._closed:
            raise RuntimeError("Client is closed")
        # A fresh connection per operation bounds socket lifetime, including
        # application errors and servers that close keepalive connections.
        cls = http.client.HTTPSConnection if self._parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(self._parsed.hostname, self._parsed.port, timeout=self.connect_timeout)
        self._connection = connection
        try:
            connection.connect()
            if method == "POST" and self._pending is not None:
                remaining = self._pending[2] - time.monotonic()
                if remaining <= 0:
                    raise UncertainResult()
                connection.sock.settimeout(min(self.read_timeout, remaining))
            else:
                connection.sock.settimeout(self.read_timeout)
            connection.request(method, "/api/v1" + path, body=raw,
                               headers={"Authorization": "Bearer " + self._token,
                                        "Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise ValueError("Response exceeds configured limit")
            value = json.loads(data)
            if type(value) is not dict:
                raise ValueError("Expected JSON object")
            if not 200 <= response.status < 300:
                raise HTTPError(response.status, value)
            return value
        finally:
            self._disconnect()

    def capabilities(self):
        value = self._once("GET", "/capabilities")
        if (value.get("schema_version") != 1 or type(value.get("retry_seconds")) is not int
                or value["retry_seconds"] < 1):
            raise ValueError("Unsupported server contract")
        self._capabilities = value
        return value

    def read(self, session_id):
        return self._once("GET", "/sessions/" + quote(session_id, safe=""))

    def snapshot(self, session_id):
        return self._once("GET", "/sessions/" + quote(session_id, safe="") + "/snapshot")

    def events(self, session_id, cursor, *, limit=128, wait=0):
        if wait >= self.read_timeout:
            raise ValueError("Event wait must be less than the read timeout")
        query = urlencode({"cursor": cursor, "limit": limit, "wait": wait})
        return self._once("GET", "/sessions/" + quote(session_id, safe="") + "/events?" + query)

    def execute(self, command):
        """Submit an exact Command dictionary; IDs are never synthesized on retry."""
        return self._execute(command)

    def _execute(self, command, *, own_creation=False):
        if self._pending is not None:
            raise UncertainResult()
        if self._capabilities is None:
            self.capabilities()
        raw = json.dumps(command, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        path = "/sessions" if command.get("payload", {}).get("op") == "create" else "/commands"
        self._pending = (path, raw, time.monotonic() + self._capabilities["retry_seconds"])
        self._pending_uncertain = False
        self._pending_owns_creation = own_creation
        return self.retry_pending()

    @staticmethod
    def _matching_receipt(command, result):
        """A guard error is not proof of what happened to an earlier send."""
        fields = {"session_id", "request_id", "ok", "error", "state_revision", "cursor"}
        if (type(result) is not dict or set(result) != fields
                or type(result["request_id"]) is not int
                or result["request_id"] != command["request_id"]
                or type(result["ok"]) is not bool
                or type(result["state_revision"]) is not int or result["state_revision"] < 0
                or (result["ok"] and result["error"] is not None)
                or (not result["ok"] and type(result["error"]) is not str)):
            return False
        sid = result["session_id"]
        if type(sid) is not str or not 1 <= len(sid) <= 128:
            return False
        if command["payload"]["op"] == "create":
            if not result["ok"]:
                return sid == "new" and result["state_revision"] == 0 and result["cursor"] is None
            if sid == "new":
                return False
        elif sid != command["session_id"]:
            return False
        cursor = result["cursor"]
        if type(cursor) is not str or len(cursor) > 160:
            return False
        prefix, _, sequence = cursor.rpartition(":")
        return prefix == sid and sequence.isascii() and sequence.isdecimal()

    def _finish_pending(self, command, result):
        if result["ok"]:
            sid = result["session_id"]
            if self._pending_owns_creation and sid not in self._owned:
                self._owned[sid] = RemoteSession(self, sid, owned=True)
            if command["payload"]["op"] == "close" and sid in self._owned:
                self._owned[sid]._closed = True
        self._clear_pending()

    def _clear_pending(self):
        self._pending = None
        self._pending_uncertain = False
        self._pending_owns_creation = False

    def retry_pending(self):
        if self._pending is None:
            raise ValueError("No uncertain command")
        path, raw, deadline = self._pending
        command = json.loads(raw)
        for attempt in range(self.retries + 1):
            if time.monotonic() >= deadline:
                raise UncertainResult()
            was_uncertain = self._pending_uncertain
            # Cancellation can escape the transport too. Keep the send pending
            # until a receipt or an unambiguous first-send rejection resolves it.
            self._pending_uncertain = True
            try:
                result = self._once("POST", path, raw)
                if not self._matching_receipt(command, result) or not result["ok"]:
                    raise ValueError("Response is not a matching successful receipt")
                self._finish_pending(command, result)
                return result
            except HTTPError as error:
                if self._matching_receipt(command, error.response) and not error.response["ok"]:
                    self._finish_pending(command, error.response)
                    raise
                if (not was_uncertain and error.status < 500
                        and set(error.response) == {"ok", "error"}
                        and error.response["ok"] is False and type(error.response["error"]) is str):
                    # The first send was explicitly rejected. After any lost or
                    # ambiguous response, this same guard cannot resolve it.
                    self._clear_pending()
                    raise
                if attempt == self.retries:
                    raise UncertainResult() from error
            except (OSError, http.client.HTTPException, ValueError):
                if attempt == self.retries:
                    raise UncertainResult() from None
        raise UncertainResult()

    def create(self, *, request_id, config=None, seed=None, scenario=None):
        if scenario is not None:
            if config is not None or seed is not None:
                raise ValueError("Choose configuration or scenario")
            payload = {"op": "create", "scenario": scenario.to_json() if hasattr(scenario, "to_json") else scenario}
        else:
            payload = {"op": "create", "config": asdict(config) if hasattr(config, "__dataclass_fields__") else config,
                       "seed": seed.to_json() if hasattr(seed, "to_json") else seed}
        result = self._execute({"session_id": "new", "request_id": request_id,
                                "expected_revision": 0, "payload": payload}, own_creation=True)
        return self._owned[result["session_id"]]

    def attach(self, session_id):
        return RemoteSession(self, session_id)

    def close(self):
        if self._closed:
            return
        failure = None
        try:
            if self._pending is not None and self._pending_owns_creation:
                # Retain ownership intent before create() can return a handle.
                # Resolve within the original deadline before credentials go away.
                try:
                    self.retry_pending()
                except Exception as error:
                    if self._pending is not None:
                        raise  # Still uncertain: further writes remain blocked.
                    failure = error
            for session in list(self._owned.values()):
                try:
                    session.close()
                except Exception as error:
                    failure = error
        finally:
            self._disconnect()
            self._token = None
            self._closed = True
        if failure is not None:
            raise failure

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise


class RemoteSession:
    """Convenience handle; reads and commands retain gateway JSON types.

    Created handles own remote close. Attached player/spectator handles only
    release their local handle. Close authorization remains a server grant.
    """
    def __init__(self, client, session_id, owned=False):
        self.client, self.session_id = client, session_id
        self._owned, self._closed = owned, False
        self._request_id = 0

    def read(self):
        return self.client.read(self.session_id)

    def command(self, payload, expected_revision, *, request_id=None):
        if self._closed:
            raise RuntimeError("Session handle is closed")
        number = self._request_id + 1 if request_id is None else request_id
        self._request_id = max(self._request_id, number)
        return self.client.execute({"session_id": self.session_id, "request_id": number,
                                    "expected_revision": expected_revision, "payload": payload})

    def step(self, action, expected_revision, **kwargs):
        return self.command({"op": "step", "action": action.to_json() if hasattr(action, "to_json") else action},
                            expected_revision, **kwargs)

    def close(self):
        if self._closed:
            return
        failure = None
        if self._owned:
            if self.client._pending is not None:
                path, raw, _ = self.client._pending
                pending = json.loads(raw)
                try:
                    result = self.client.retry_pending()
                except HTTPError as error:
                    if self.client._pending is not None:
                        raise
                    # A failed receipt resolves the command, so cleanup can
                    # continue. Report it after releasing this allocation.
                    failure = error
                else:
                    if (pending["session_id"] == self.session_id
                            and pending["payload"]["op"] == "close" and result["ok"]):
                        self._closed = True
                        return
            state = self.read()
            self.command({"op": "close"}, state["state_revision"])
        self._closed = True
        if failure is not None:
            raise failure

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
