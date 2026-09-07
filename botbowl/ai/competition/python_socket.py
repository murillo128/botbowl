"""
==========================
Author: Niels Justesen
Year: 2020
==========================
Legacy pickle transport for mutually trusted processes on the same host only.

Unpickling can execute code. Loopback limits exposure; it does not authenticate
local peers. Tokens and request IDs are checked AFTER unpickling and only detect
protocol mixups. Do not use this transport with untrusted bots or port tunnels.
"""
from contextlib import contextmanager
import enum
import ipaddress
import math
import os
import pickle
import re
import secrets
import socket
import time
from typing import Optional, Type, TypeVar, Union

import docker

from botbowl.core.game import Game
from botbowl.core.model import Action, Agent, Team

T = TypeVar("T")

HEADERSIZE = 10
DEFAULT_PORT = 5100
DEFAULT_TOKEN = "32"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_FRAME_SIZE = 64 * 1024 * 1024


class SocketProtocolError(Exception):
    """A failed legacy transport exchange; discard the connection."""


class InvalidFrameError(SocketProtocolError, ValueError):
    """Malformed frame header or pickle payload."""


class FrameTooLargeError(InvalidFrameError):
    """The frame exceeds the configured byte limit."""


class UnexpectedEOFError(SocketProtocolError, EOFError):
    """The peer closed before the frame was complete."""


class ProtocolTimeoutError(SocketProtocolError, TimeoutError):
    """The total exchange deadline expired."""


class InvalidMessageError(SocketProtocolError, ValueError):
    """An envelope or its command, correlation, or result is invalid."""


class UnsafeTransportError(ValueError):
    """Legacy pickle transport is restricted to trusted local processes."""


class AgentCommand(str, enum.Enum):
    ACT = "act"
    END_GAME = "end"
    NEW_GAME = "new_game"
    STATE_NAME = "state_name"


# Maintain only active sockets outside Agent to avoid serialization.
sockets = {}


class Request:
    command: AgentCommand
    game: Optional[Game]
    team: Optional[Team]
    request_id: str

    def __init__(self, command, game=None, team=None):
        self.command = command
        self.game = game
        self.team = team
        self.request_id = secrets.token_hex(32)

    def validate(self):
        if not isinstance(getattr(self, "request_id", None), str) or not self.request_id:
            raise InvalidMessageError("Request ID must be a nonempty string")
        command = getattr(self, "command", None)
        if not isinstance(command, (str, AgentCommand)) or command not in AgentCommand._value2member_map_:
            raise InvalidMessageError("Unknown command")
        if not hasattr(self, "game") or not hasattr(self, "team"):
            raise InvalidMessageError("Request is missing game or team")
        if command == AgentCommand.STATE_NAME:
            valid = self.game is None and self.team is None
        elif command == AgentCommand.NEW_GAME:
            valid = isinstance(self.game, Game) and isinstance(self.team, Team)
        else:
            valid = isinstance(self.game, Game) and self.team is None
        if not valid:
            raise InvalidMessageError("Invalid game or team for command")


class Response:
    def __init__(self, object, token, request_id):
        self.object = object
        self.token = token
        self.request_id = request_id

    def validate(self, token, request_id, expected_return_type=None):
        # Consistency checks only: the peer has already been unpickled.
        if not hasattr(self, "token") or str(self.token) != str(token):
            raise InvalidMessageError("Response token mismatch")
        if not isinstance(getattr(self, "request_id", None), str) or self.request_id != request_id:
            raise InvalidMessageError("Response request ID mismatch")
        if not hasattr(self, "object"):
            raise InvalidMessageError("Response is missing its result")
        if expected_return_type is not None and type(self.object) is not expected_return_type:
            raise InvalidMessageError("Unexpected response result type")


def _validate_limit(max_frame_size):
    if type(max_frame_size) is not int or not 0 < max_frame_size < 10 ** HEADERSIZE:
        raise ValueError("max_frame_size must be a positive integer fitting the header")


def _validate_timeout(timeout):
    if timeout is None:
        return DEFAULT_TIMEOUT
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be finite and nonnegative")
    return timeout


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProtocolTimeoutError("Transport deadline expired")
    return remaining


@contextmanager
def _socket_timeout(connection):
    previous = connection.gettimeout()
    try:
        yield
    except socket.timeout as exc:
        raise ProtocolTimeoutError("Transport deadline expired") from exc
    finally:
        connection.settimeout(previous)


def _read_exact(connection, size, deadline):
    """Read only this frame's bytes, sharing the caller's absolute deadline."""
    data = bytearray()
    while len(data) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(min(size - len(data), 65536))
        if not chunk:
            raise UnexpectedEOFError("Peer closed before completing the frame")
        data.extend(chunk)
    _remaining(deadline)
    return data


def _send_data(data, connection, deadline, max_frame_size):
    _remaining(deadline)
    if not isinstance(data, (Request, Response)):
        raise InvalidMessageError("Expected a Request or Response envelope")
    payload = pickle.dumps(data)
    if len(payload) > max_frame_size:
        raise FrameTooLargeError("Frame exceeds max_frame_size")
    header = f"{len(payload):<{HEADERSIZE}}".encode("ascii")
    with _socket_timeout(connection):
        connection.settimeout(_remaining(deadline))
        connection.sendall(header + payload)
        _remaining(deadline)


def send_data(data: Union[Request, Response], socket, timeout=None, *,
              max_frame_size=DEFAULT_MAX_FRAME_SIZE):
    """Send one bounded frame to a trusted local peer, within a total deadline.

    None uses DEFAULT_TIMEOUT, zero expires immediately. The frame limit bounds
    serialized bytes, not pickle's CPU or decoded object memory usage.
    """
    _validate_limit(max_frame_size)
    deadline = time.monotonic() + _validate_timeout(timeout)
    _require_local_peer(socket)
    _send_data(data, socket, deadline, max_frame_size)


def _receive_data(connection, deadline, max_frame_size):
    with _socket_timeout(connection):
        header = _read_exact(connection, HEADERSIZE, deadline)
        if re.fullmatch(rb"[0-9]+ *", header) is None:
            raise InvalidFrameError("Expected an ASCII decimal length padded with spaces")
        size = int(header)
        if size == 0:
            raise InvalidFrameError("Empty frames are not valid pickle messages")
        if size > max_frame_size:
            raise FrameTooLargeError("Frame exceeds max_frame_size")
        payload = _read_exact(connection, size, deadline)
    try:
        data = pickle.loads(payload)
    except Exception as exc:
        # Do not echo peer-controlled content (including possible tokens).
        raise InvalidFrameError("Invalid pickle payload") from exc
    _remaining(deadline)
    return data


def receive_data(connection, timeout=None, *, max_frame_size=DEFAULT_MAX_FRAME_SIZE) -> Union[Request, Response]:
    """Read one frame without consuming the next; unpickle TRUSTED data only."""
    _validate_limit(max_frame_size)
    deadline = time.monotonic() + _validate_timeout(timeout)
    _require_local_peer(connection)
    return _receive_data(connection, deadline, max_frame_size)


def _local_address(host):
    # Do not resolve arbitrary DNS names: validate the address actually used.
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise UnsafeTransportError("Use a literal loopback address or localhost") from exc
    if not address.is_loopback:
        raise UnsafeTransportError("Legacy pickle transport requires loopback")
    return str(address), socket.AF_INET6 if address.version == 6 else socket.AF_INET


def _require_local_peer(connection):
    # socketpair/Unix sockets are local; for TCP inspect the connected address,
    # not a caller-supplied hostname. This still cannot establish peer trust.
    if connection.family == getattr(socket, "AF_UNIX", None):
        return
    if connection.family not in (socket.AF_INET, socket.AF_INET6):
        raise UnsafeTransportError("Unsupported local transport socket family")
    _local_address(connection.getpeername()[0])


class PythonSocketClient(Agent):
    def __init__(self, name, host="127.0.0.1", port=DEFAULT_PORT, token=DEFAULT_TOKEN,
                 connection_timeout=1, *, timeout=DEFAULT_TIMEOUT,
                 max_frame_size=DEFAULT_MAX_FRAME_SIZE):
        super().__init__(name)
        self.host, self._family = _local_address(host)
        _validate_limit(max_frame_size)
        self.port = port
        self.token = token
        self.connection_timeout = _validate_timeout(connection_timeout)
        self.timeout = _validate_timeout(timeout)
        self.max_frame_size = max_frame_size

    def _connect(self, deadline):
        self._close_connection()
        s = socket.socket(self._family, socket.SOCK_STREAM)
        try:
            connect_deadline = min(deadline, time.monotonic() + self.connection_timeout)
            s.settimeout(_remaining(connect_deadline))
            s.connect((self.host, self.port))
            _remaining(deadline)
        except socket.timeout as exc:
            s.close()
            raise ProtocolTimeoutError("Connection deadline expired") from exc
        except BaseException:
            s.close()
            raise
        sockets[self.agent_id] = s
        return s

    def _send_command(self, command: AgentCommand, *, expected_return_type: Type[T] = None,
                      game: Game, team=None) -> T:
        seconds_left = game.get_seconds_left()
        budget = self.timeout if seconds_left is None else min(self.timeout, max(0, seconds_left))
        deadline = time.monotonic() + budget
        try:
            connection = self._connect(deadline)
            with game.hide_agents_and_rng():
                request = Request(command, game=game, team=team)
                request.validate()
                _send_data(request, connection, deadline, self.max_frame_size)
            response = _receive_data(connection, deadline, self.max_frame_size)
            if type(response) is not Response:
                raise InvalidMessageError("Expected a Response envelope")
            response.validate(self.token, request.request_id, expected_return_type)
            return response.object
        finally:
            self._close_connection()

    def act(self, game) -> Action:
        return self._send_command(AgentCommand.ACT, expected_return_type=Action, game=game)

    def new_game(self, game, team) -> None:
        self._send_command(AgentCommand.NEW_GAME, expected_return_type=type(None), game=game, team=team)

    def end_game(self, game):
        self._send_command(AgentCommand.END_GAME, expected_return_type=type(None), game=game)

    def _close_connection(self):
        connection = sockets.pop(self.agent_id, None)
        if connection is not None:
            connection.close()

    def close(self):
        self._close_connection()

    def _close(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def docker_image_exists(docker_client, img_name: str) -> bool:
    return any(img_name in image.tags for image in docker_client.images.list())


class DockerAgent(PythonSocketClient):
    """Run a trusted image using host networking and a loopback-only server.

    Requires a local Docker daemon with host networking support. The image must
    honor BOTBOWL_SOCKET_PORT (PythonSocketServer does by default). This is not
    sandboxing for untrusted images and does not publish bridge ports.
    """
    def __init__(self, name: str, *, image: str, command: Optional[str]):
        self.container = None
        self._docker_api = None
        daemon_host = os.environ.get("DOCKER_HOST") or "unix:///var/run/docker.sock"
        if not daemon_host.startswith("unix://"):
            raise UnsafeTransportError("DockerAgent requires a local Unix-socket Docker daemon")
        super().__init__(name, port=get_free_port(), connection_timeout=4)
        try:
            # SDK construction negotiates an API version. Pin a prevalidated
            # endpoint before that I/O, including SDKs that auto-load contexts.
            self._docker_api = docker.from_env(environment={"DOCKER_HOST": daemon_host})
            api = self._docker_api
            if api.api.base_url != "http+docker://localhost":
                raise UnsafeTransportError("DockerAgent requires a local Unix-socket Docker daemon")
            if not docker_image_exists(api, image):
                api.images.pull(image)
            if not docker_image_exists(api, image):
                raise RuntimeError("Docker image not found after pull")
            self.container = api.containers.run(
                image, command, detach=True, auto_remove=True,
                network_mode="host", environment={"BOTBOWL_SOCKET_PORT": str(self.port)},
            )
            self._wait_until_ready()
        except BaseException:
            self.close()
            raise

    def _wait_until_ready(self):
        deadline = time.monotonic() + self.connection_timeout
        while True:
            try:
                self._connect(deadline)
                return
            except (ConnectionRefusedError, ProtocolTimeoutError):
                time.sleep(min(0.05, _remaining(deadline)))
            finally:
                self._close_connection()

    def close(self):
        super().close()
        container = self.container
        api = self._docker_api
        try:
            if container is not None:
                try:
                    container.kill()
                except docker.errors.NotFound:
                    pass  # auto_remove may already have removed an exited container
                self.container = None
        finally:
            if api is not None:
                api.close()
                if self.container is None:
                    self._docker_api = None


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PythonSocketServer:
    def __init__(self, agent, port: Optional[int] = None, token: Optional[str] = DEFAULT_TOKEN,
                 *, host="127.0.0.1", timeout=DEFAULT_TIMEOUT,
                 max_frame_size=DEFAULT_MAX_FRAME_SIZE):
        self.host, self._family = _local_address(host)
        _validate_limit(max_frame_size)
        self.agent = agent
        self.port = int(os.environ.get("BOTBOWL_SOCKET_PORT", DEFAULT_PORT)) if port is None else port
        self.token = token
        self.timeout = _validate_timeout(timeout)
        self.max_frame_size = max_frame_size
        self.socket_ = None
        self._connection = None
        self._closed = False

    def run(self):
        if self._closed:
            raise RuntimeError("Server is closed")
        if self.socket_ is not None:
            raise RuntimeError("Server is already running")
        try:
            with socket.socket(self._family, socket.SOCK_STREAM) as s:
                self.socket_ = s
                if self._closed:
                    return
                s.bind((self.host, self.port))
                self.port = s.getsockname()[1]
                s.listen()
                s.settimeout(0.1)  # periodically observe close() while idle
                print(f"Agent listening on {self.host}:{self.port} (trusted local pickle)")
                while not self._closed:
                    try:
                        connection, _ = s.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        self._connection = connection
                        try:
                            if self._closed:
                                break
                            deadline = time.monotonic() + self.timeout
                            request = _receive_data(connection, deadline, self.max_frame_size)
                            data = self.handle_request(request)
                            response = Response(data, self.token, request.request_id)
                            _send_data(response, connection, deadline, self.max_frame_size)
                        except (SocketProtocolError, OSError):
                            # Bad/disconnected local peers must not retain resources
                            # or stop later exchanges. Never log their payloads.
                            pass
                        finally:
                            self._connection = None
        except OSError:
            if not self._closed:
                raise
        finally:
            self.close()

    def handle_request(self, request: Request) -> Union[Action, str, None]:
        if not isinstance(request, Request):
            raise InvalidMessageError("Expected a Request envelope")
        request.validate()
        if request.command == AgentCommand.ACT:
            return self.agent.act(request.game)
        if request.command == AgentCommand.STATE_NAME:
            return str(self.agent.name)
        if request.command == AgentCommand.NEW_GAME:
            self.agent.new_game(request.game, request.team)
        elif request.command == AgentCommand.END_GAME:
            self.agent.end_game(request.game)
        return None

    def close(self):
        self._closed = True
        for name in ("_connection", "socket_"):
            connection = getattr(self, name)
            setattr(self, name, None)
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

    def _close(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
