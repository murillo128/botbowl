"""Harmless local peers only: framing, deadlines, cleanup, and public checks."""
from contextlib import contextmanager, redirect_stdout
import io
import multiprocessing
import os
import pickle
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, Mock

import pytest

import botbowl
from botbowl.ai.competition import python_socket as protocol
from botbowl.ai.competition.competition import Competition, MultiAgentCompetition


def frame(value):
    payload = pickle.dumps(value)
    return f"{len(payload):<10}".encode("ascii") + payload


def request():
    return protocol.Request(protocol.AgentCommand.STATE_NAME)


@contextmanager
def socket_pair():
    left, right = socket.socketpair()
    try:
        yield left, right
    finally:
        left.close()
        right.close()


@contextmanager
def writer(connection, chunks, delay=0):
    errors = []

    def write():
        try:
            for chunk in chunks:
                connection.sendall(chunk)
                if delay:
                    time.sleep(delay)
        except OSError:
            pass  # receiver may close at its deadline
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    try:
        yield
    finally:
        connection.close()
        thread.join(2)
        assert not thread.is_alive()
        assert not errors


@pytest.mark.parametrize("chunk_size", [1, 3, 10, 31])
def test_fragmented_header_and_payload(chunk_size):
    value = request()
    wire = frame(value)
    with socket_pair() as (left, right):
        with writer(left, [wire[i:i + chunk_size] for i in range(0, len(wire), chunk_size)], 0.001):
            result = protocol.receive_data(right, timeout=2)
        assert result.request_id == value.request_id


def test_concatenated_frames_preserve_next_header():
    first, second = request(), request()
    with socket_pair() as (left, right):
        left.sendall(frame(first) + frame(second))
        assert protocol.receive_data(right).request_id == first.request_id
        assert protocol.receive_data(right).request_id == second.request_id


# Exercise EOF at every header and payload boundary of one complete message.
@pytest.mark.parametrize("offset", range(len(frame(protocol.Response(None, "local", "id")))))
def test_eof_at_every_offset(offset):
    wire = frame(protocol.Response(None, "local", "id"))
    with socket_pair() as (left, right):
        left.sendall(wire[:offset])
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(protocol.UnexpectedEOFError):
            protocol.receive_data(right, timeout=0.5)


@pytest.mark.parametrize("header", [b"          ", b"-1        ", b"+1        ",
    b"1x        ", b" 1        ", b"1 2       ", b"1\t        ", b"\xff123456789", b"0000000000"])
def test_malformed_or_empty_header(header):
    with socket_pair() as (left, right):
        left.sendall(header)
        with pytest.raises(protocol.InvalidFrameError):
            protocol.receive_data(right, timeout=0.5)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "100", None, 10 ** 10])
@pytest.mark.parametrize("operation", [protocol.send_data, protocol.receive_data])
def test_invalid_limit_fails_before_io(limit, operation):
    args = (request(), None) if operation is protocol.send_data else (None,)
    with pytest.raises(ValueError, match="max_frame_size"):
        operation(*args, max_frame_size=limit)


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), True, "1"])
@pytest.mark.parametrize("operation", [protocol.send_data, protocol.receive_data])
def test_invalid_timeout_fails_before_io(timeout, operation):
    args = (request(), None) if operation is protocol.send_data else (None,)
    with pytest.raises(ValueError, match="timeout"):
        operation(*args, timeout=timeout)


def test_exact_size_limit_and_oversize_rejection_before_payload_or_unpickle(monkeypatch):
    value = request()
    size = len(pickle.dumps(value))
    with socket_pair() as (left, right):
        protocol.send_data(value, left, max_frame_size=size)
        assert protocol.receive_data(right, max_frame_size=size).request_id == value.request_id
        with pytest.raises(protocol.FrameTooLargeError):
            protocol.send_data(value, left, max_frame_size=size - 1)
        left.sendall(b"9999999999")
        loader = Mock(side_effect=AssertionError("must not deserialize an oversized frame"))
        monkeypatch.setattr(protocol.pickle, "loads", loader)
        with pytest.raises(protocol.FrameTooLargeError):
            protocol.receive_data(right, max_frame_size=size)
        loader.assert_not_called()


def test_invalid_pickle():
    with socket_pair() as (left, right):
        left.sendall(b"3         bad")
        with pytest.raises(protocol.InvalidFrameError, match="pickle"):
            protocol.receive_data(right)


@pytest.mark.parametrize("operation", [protocol.send_data, protocol.receive_data])
def test_zero_timeout_is_typed_and_restores_socket(operation):
    with socket_pair() as (left, right):
        right.settimeout(7)
        args = (request(), right) if operation is protocol.send_data else (right,)
        with pytest.raises(protocol.ProtocolTimeoutError):
            operation(*args, timeout=0)
        assert right.gettimeout() == 7


@pytest.mark.parametrize("header_first", [False, True])
def test_trickling_peer_cannot_renew_deadline(header_first):
    wire = frame(request())
    with socket_pair() as (left, right):
        if header_first:
            left.sendall(wire[:10])
            wire = wire[10:]
        start = time.monotonic()
        with writer(left, [bytes([byte]) for byte in wire], 0.02):
            with pytest.raises(protocol.ProtocolTimeoutError):
                protocol.receive_data(right, timeout=0.12)
            elapsed = time.monotonic() - start
        assert 0.08 <= elapsed < 0.8


def test_send_deadline_when_peer_does_not_read():
    with socket_pair() as (left, right):
        left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        start = time.monotonic()
        with pytest.raises(protocol.ProtocolTimeoutError):
            protocol.send_data(protocol.Response(b"x" * 2 ** 20, "local", "id"), left, timeout=0.1)
        assert time.monotonic() - start < 0.8
        assert left.gettimeout() is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "8.8.8.8", "example.com", ""])
@pytest.mark.parametrize("constructor", [protocol.PythonSocketClient, protocol.PythonSocketServer])
def test_remote_activation_rejected_before_socket_creation(monkeypatch, host, constructor):
    socket_factory = Mock(side_effect=AssertionError("must reject before networking"))
    monkeypatch.setattr(protocol.socket, "socket", socket_factory)
    with pytest.raises(protocol.UnsafeTransportError):
        constructor("local", host=host)
    socket_factory.assert_not_called()


@pytest.mark.parametrize("host,expected", [("localhost", "127.0.0.1"), ("127.0.0.2", "127.0.0.2"), ("::1", "::1")])
def test_loopback_addresses(host, expected):
    with protocol.PythonSocketClient("local", host=host) as client:
        assert client.host == expected


@pytest.mark.parametrize("change", [
    {"command": "unknown"}, {"command": []}, {"game": "wrong"},
    {"team": "wrong"}, {"request_id": None}, {"request_id": ""},
    {"command": protocol.AgentCommand.ACT}, {"command": protocol.AgentCommand.NEW_GAME},
    {"command": protocol.AgentCommand.END_GAME},
])
def test_request_validation(change):
    value = request()
    value.__dict__.update(change)
    with pytest.raises(protocol.InvalidMessageError):
        value.validate()


def test_missing_request_fields_and_wrong_envelope():
    with protocol.PythonSocketServer(botbowl.make_bot("random")) as server:
        with pytest.raises(protocol.InvalidMessageError):
            server.handle_request("not a request")
        value = request()
        del value.game
        with pytest.raises(protocol.InvalidMessageError):
            server.handle_request(value)


@contextmanager
def running_server(agent=None, **kwargs):
    server = protocol.PythonSocketServer(agent or botbowl.make_bot("random"), port=0, **kwargs)
    errors = []

    def run():
        try:
            server.run()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while server.port == 0:
            if errors:
                raise errors[0]
            if time.monotonic() > deadline:
                raise RuntimeError("Server did not bind in time")
            time.sleep(0.005)
        # run() writes the port just before listen(); establish actual readiness.
        while True:
            try:
                with socket.create_connection((server.host, server.port), timeout=0.1):
                    break
            except ConnectionRefusedError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.005)
        yield server
    finally:
        server.close()
        thread.join(2)
        assert not thread.is_alive(), "Server must stop within bounded join"
        assert not errors


def make_game():
    config = botbowl.load_config("gym-1")
    config.pathfinding_enabled = False
    rules = botbowl.load_rule_set(config.ruleset)
    team = botbowl.load_team_by_filename("human", rules, board_size=1)
    away = botbowl.load_team_by_filename("human", rules, board_size=1)
    return botbowl.Game("socket-test", team, away, botbowl.make_bot("random"),
                        botbowl.make_bot("random"), config, ruleset=rules)


@pytest.fixture
def game():
    return make_game()


@pytest.mark.parametrize("mutation", ["envelope", "token", "request_id", "result", "missing"])
def test_client_response_checks_close_connection(game, monkeypatch, mutation, capsys):
    value = protocol.Response(None, "TOKEN-MUST-NOT-APPEAR", "unused")
    if mutation == "envelope":
        value = "not a response"
    elif mutation == "token":
        value.token = "wrong"
    elif mutation == "result":
        value.object = "wrong"
    elif mutation == "missing":
        del value.object
    with socket_pair() as (left, right):
        client = protocol.PythonSocketClient("local", token="TOKEN-MUST-NOT-APPEAR")
        def connect(deadline):
            protocol.sockets[client.agent_id] = right
            return right
        def send(request, *args):
            if mutation not in ("envelope", "request_id"):
                value.request_id = request.request_id
            left.sendall(frame(value))
        monkeypatch.setattr(client, "_connect", connect)
        monkeypatch.setattr(protocol, "_send_data", send)
        with pytest.raises(protocol.InvalidMessageError) as error:
            client.end_game(game)
        assert "TOKEN-MUST-NOT-APPEAR" not in str(error.value)
        assert right.fileno() == -1
        assert client.agent_id not in protocol.sockets
        client.close()
        client.close()
    out, err = capsys.readouterr()
    assert "TOKEN-MUST-NOT-APPEAR" not in out + err


def test_client_uses_one_deadline_including_connect_and_send(game, monkeypatch):
    # Inspect exact I/O budgets independently of unpreemptible pickle/GC work or
    # OS scheduling; real stalled/trickling sockets are exercised separately.
    now = [100.0]
    monkeypatch.setattr(protocol.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(botbowl.Game, "get_seconds_left", lambda self: None)
    client = protocol.PythonSocketClient("local", timeout=10)
    connection = Mock(spec=socket.socket)
    connection.gettimeout.return_value = None
    budgets = []
    def connect(deadline):
        assert deadline == 110.0
        now[0] += 3  # connection consumes three seconds
        protocol.sockets[client.agent_id] = connection
        return connection
    def send(payload):
        budgets.append(connection.settimeout.call_args.args[0])
        now[0] += 4  # sending consumes four more seconds
    def receive(size):
        budgets.append(connection.settimeout.call_args.args[0])
        now[0] = 110.0
        raise socket.timeout()
    connection.sendall.side_effect = send
    connection.recv.side_effect = receive
    monkeypatch.setattr(client, "_connect", connect)
    with pytest.raises(protocol.ProtocolTimeoutError):
        client.end_game(game)
    assert budgets == [7.0, 3.0]
    connection.close.assert_called_once_with()
    assert client.agent_id not in protocol.sockets


def test_game_clock_caps_deadline_and_expired_clock_never_connects(game, monkeypatch):
    client = protocol.PythonSocketClient("local", timeout=10)
    monkeypatch.setattr(botbowl.Game, "get_seconds_left", lambda self: -1)
    with pytest.raises(protocol.ProtocolTimeoutError):
        client.end_game(game)
    assert client.agent_id not in protocol.sockets
    monkeypatch.setattr(botbowl.Game, "get_seconds_left", lambda self: 0.05)
    with socket_pair() as (left, right):
        def connect(deadline):
            protocol.sockets[client.agent_id] = right
            return right
        monkeypatch.setattr(client, "_connect", connect)
        start = time.monotonic()
        with pytest.raises(protocol.ProtocolTimeoutError):
            client.end_game(game)
        assert time.monotonic() - start < 0.5


def fd_count():
    return len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None


def _check_roundtrip_resources(game):
    class CountingAgent:
        # Count calls without retaining all received Game graphs in mock history.
        calls = 0
        def end_game(self, game):
            self.calls += 1
    agent = CountingAgent()
    before = fd_count()
    registry = len(protocol.sockets)
    # Resource retention is measured independently of the short deadline tests.
    # Unpickling/GC of a whole Game can exceed 50 ms on a loaded host.
    with running_server(agent, token="LOG-SECRET", timeout=2) as server:
        with protocol.PythonSocketClient("local", port=server.port, token="LOG-SECRET") as client:
            live = fd_count()
            for _ in range(30):
                client.end_game(game)
                assert client.agent_id not in protocol.sockets
                with socket.create_connection((server.host, server.port), timeout=1) as peer:
                    peer.sendall(b"bad header")
                    assert peer.recv(1) == b""
            if live is not None:
                assert fd_count() <= live
        assert agent.calls == 30
    assert len(protocol.sockets) == registry
    if before is not None:
        assert fd_count() == before


def _resource_roundtrips(pipe):
    try:
        with redirect_stdout(io.StringIO()) as output:
            _check_roundtrip_resources(make_game())
        if "LOG-SECRET" in output.getvalue():
            raise RuntimeError("Transport logged a token")
        pipe.send("ok")
    except BaseException:
        import traceback
        pipe.send(traceback.format_exc())
    finally:
        pipe.close()


def test_repeated_calls_and_protocol_failures_do_not_grow_resources():
    _run_in_process(_resource_roundtrips, 30)


def test_repeated_connect_and_serialization_failures_close_resources(game, monkeypatch):
    before = fd_count()
    registry = len(protocol.sockets)
    # Reserve but do not listen, so every connect deterministically fails.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        client = protocol.PythonSocketClient("local", port=reserved.getsockname()[1])
        for _ in range(30):
            with pytest.raises(ConnectionRefusedError):
                client.end_game(game)
            assert client.agent_id not in protocol.sockets
    with running_server(timeout=0.1) as server:
        client = protocol.PythonSocketClient("local", port=server.port)
        home, away, rng = game.home_agent, game.away_agent, game.rng
        monkeypatch.setattr(protocol.pickle, "dumps", Mock(side_effect=ValueError("serialization failed")))
        for _ in range(30):
            with pytest.raises(ValueError, match="serialization failed"):
                client.end_game(game)
            assert client.agent_id not in protocol.sockets
            assert (game.home_agent, game.away_agent) == (home, away)
            assert game.rng is rng
    assert len(protocol.sockets) == registry
    if before is not None:
        assert fd_count() == before


def test_server_timeout_recovers_and_close_interrupts_partial_frame():
    with running_server(timeout=0.05) as server:
        with socket.create_connection((server.host, server.port), timeout=1) as peer:
            peer.sendall(b"1")
            assert peer.recv(1) == b""
        with socket.create_connection((server.host, server.port), timeout=1) as peer:
            protocol.send_data(request(), peer)
            assert isinstance(protocol.receive_data(peer).object, str)
        with socket.create_connection((server.host, server.port), timeout=1) as peer:
            peer.sendall(b"1")
            server.close()
    assert server.socket_ is None
    assert server._connection is None


def test_close_before_run_twice_and_bind_failure():
    server = protocol.PythonSocketServer("local")
    server.close()
    server.close()
    with pytest.raises(RuntimeError, match="closed"):
        server.run()
    client = protocol.PythonSocketClient("local")
    client.close()
    client.close()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        reserved.listen(1)
        server = protocol.PythonSocketServer("local", port=reserved.getsockname()[1])
        with pytest.raises(OSError):
            server.run()
        assert server.socket_ is None
        server.close()


def mock_docker(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    api = Mock()
    api.api.base_url = "http+docker://localhost"
    api.images.list.return_value = [Mock(tags=["trusted-image"])]
    monkeypatch.setattr(protocol.docker, "from_env", Mock(return_value=api))
    monkeypatch.setattr(protocol.DockerAgent, "_wait_until_ready", lambda self: None)
    return api


@pytest.mark.parametrize("failure", ["api", "remote", "pull", "missing", "run", "ready"])
def test_docker_startup_failure_cleanup(monkeypatch, failure):
    api = mock_docker(monkeypatch)
    if failure == "api":
        protocol.docker.from_env.side_effect = RuntimeError("api failed")
    elif failure == "remote":
        api.api.base_url = "http://192.0.2.1:2375"
    elif failure in ("pull", "missing"):
        api.images.list.return_value = []
        if failure == "pull":
            api.images.pull.side_effect = RuntimeError("pull failed")
    elif failure == "run":
        api.containers.run.side_effect = RuntimeError("run failed")
    elif failure == "ready":
        monkeypatch.setattr(protocol.DockerAgent, "_wait_until_ready", Mock(side_effect=RuntimeError("ready failed")))
    with pytest.raises((RuntimeError, protocol.UnsafeTransportError)):
        protocol.DockerAgent("local", image="trusted-image", command=None)
    if failure == "api":
        api.close.assert_not_called()
    else:
        api.close.assert_called_once_with()
    if failure == "ready":
        api.containers.run.return_value.kill.assert_called_once_with()
    if failure == "remote":
        api.containers.run.assert_not_called()


def test_docker_host_network_and_explicit_idempotent_cleanup(monkeypatch):
    api = mock_docker(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-context")
    with protocol.DockerAgent("local", image="trusted-image", command=None) as agent:
        protocol.docker.from_env.assert_called_once_with(
            environment={"DOCKER_HOST": "unix:///var/run/docker.sock"})
        options = api.containers.run.call_args.kwargs
        assert "ports" not in options
        assert "publish_all_ports" not in options
        assert options["network_mode"] == "host"
        assert options["environment"] == {"BOTBOWL_SOCKET_PORT": str(agent.port)}
        assert agent.host == "127.0.0.1"
    agent.close()
    api.containers.run.return_value.kill.assert_called_once_with()
    api.close.assert_called_once_with()


def test_docker_kill_failure_still_closes_api(monkeypatch):
    api = mock_docker(monkeypatch)
    agent = protocol.DockerAgent("local", image="trusted-image", command=None)
    api.containers.run.return_value.kill.side_effect = RuntimeError("kill failed")
    with pytest.raises(RuntimeError, match="kill failed"):
        agent.close()
    api.close.assert_called_once_with()
    # Failed cleanup is explicit and retryable; do not discard a live container.
    assert agent.container is api.containers.run.return_value
    api.containers.run.return_value.kill.side_effect = None
    agent.close()
    assert agent.container is None
    assert api.containers.run.return_value.kill.call_count == 2


def test_server_uses_docker_port_environment(monkeypatch):
    monkeypatch.setenv("BOTBOWL_SOCKET_PORT", "5317")
    with protocol.PythonSocketServer("local") as server:
        assert server.port == 5317
    with protocol.PythonSocketServer("local", port=0) as server:
        assert server.port == 0


def test_factory_agents_closed_for_name_probes_and_failed_matchups(game, monkeypatch):
    created = []
    def factory(name):
        def create():
            agent = Mock()
            agent.name = name
            created.append(agent)
            return agent
        return create
    comp = MultiAgentCompetition([factory("a"), factory("b")], game.state.home_team,
                                 game.state.away_team, game.config, game.ruleset)
    for agent in created:
        agent.close.assert_called_once_with()
    monkeypatch.setattr(Competition, "run", Mock(side_effect=RuntimeError("match failed")))
    with pytest.raises(RuntimeError, match="match failed"):
        comp.run()
    assert len(created) == 4
    for agent in created:
        agent.close.assert_called_once_with()
    created.clear()
    with pytest.raises(ValueError, match="commas"):
        MultiAgentCompetition([factory("bad,name")], game.state.home_team,
                              game.state.away_team, game.config)
    created[0].close.assert_called_once_with()
    created.clear()
    comp = MultiAgentCompetition([factory("a"), factory("b")], game.state.home_team,
                                 game.state.away_team, game.config)
    comp.matchups = [(factory("a"), Mock(side_effect=RuntimeError("factory failed")))]
    with pytest.raises(RuntimeError, match="factory failed"):
        comp.run()
    for agent in created:
        agent.close.assert_called_once_with()


def _competition_smoke(pipe):
    try:
        game = make_game()
        game.config.rounds = 1
        with running_server(botbowl.make_bot("random")) as a, running_server(botbowl.make_bot("random")) as b:
            with protocol.PythonSocketClient("a", port=a.port) as ca, protocol.PythonSocketClient("b", port=b.port) as cb:
                competition = Competition(ca, cb, game.state.home_team, game.state.away_team,
                                          game.config, game.ruleset, game.arena)
                result = competition.run()
                if len(result.game_results) != 2 or protocol.sockets:
                    raise RuntimeError("Competition did not finish/clean up")
        pipe.send("ok")
    except BaseException:
        import traceback
        pipe.send(traceback.format_exc())
    finally:
        pipe.close()


def _run_in_process(target, budget):
    ctx = multiprocessing.get_context("spawn")
    reader, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=target, args=(sender,))
    process.start()
    sender.close()
    try:
        process.join(budget)
        assert not process.is_alive(), f"{target.__name__} exceeded its {budget}-second process budget"
        assert process.exitcode == 0
        assert reader.poll(1)
        assert reader.recv() == "ok"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
        reader.close()
        process.close()


def test_socket_competition_in_bounded_process():
    _run_in_process(_competition_smoke, 40)


def test_public_boundaries_under_python_optimized():
    # Plain checks in a separate -O interpreter: pytest assertion rewriting must
    # not hide production checks that disappear with optimization.
    code = '''
from botbowl.ai.competition.python_socket import *
from botbowl.ai.competition.competition import Competition, MultiAgentCompetition

def rejects(error, call):
    try:
        call()
    except error:
        return
    raise RuntimeError("Public validation disappeared under -O")

rejects(UnsafeTransportError, lambda: PythonSocketClient("x", host="0.0.0.0"))
rejects(UnsafeTransportError, lambda: PythonSocketServer("x", host="192.0.2.1"))
rejects(ValueError, lambda: receive_data(None, max_frame_size=0))
rejects(ValueError, lambda: send_data(Request(AgentCommand.STATE_NAME), None, timeout=-1))
rejects(InvalidMessageError, lambda: Request(AgentCommand.ACT).validate())
rejects(InvalidMessageError, lambda: PythonSocketServer("x").handle_request("wrong"))
rejects(InvalidMessageError, lambda: Response(None, "secret", "id").validate("secret", "other"))
rejects(InvalidMessageError, lambda: Response(None, "secret", "id").validate("wrong", "id"))
rejects(InvalidMessageError, lambda: Response("wrong", "secret", "id").validate("secret", "id", Action))
rejects(ValueError, lambda: Competition(None, None, None, None, None, None, None, n=1))
rejects(ValueError, lambda: MultiAgentCompetition([], None, None, None, number_of_games=1))
print("optimized public boundaries passed")
'''
    result = subprocess.run([sys.executable, "-O", "-c", code], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "secret" not in result.stdout + result.stderr
    assert "optimized public boundaries passed" in result.stdout


@pytest.mark.parametrize("operation", [protocol.send_data, protocol.receive_data])
def test_raw_helpers_reject_connected_nonloopback_peers(operation):
    peer = Mock(family=socket.AF_INET)
    peer.getpeername.return_value = ("192.0.2.1", 5100)
    args = (request(), peer) if operation is protocol.send_data else (peer,)
    with pytest.raises(protocol.UnsafeTransportError):
        operation(*args)
    peer.recv.assert_not_called()
    peer.sendall.assert_not_called()


def test_docker_readiness_timeout_is_bounded_and_cleans_up(monkeypatch):
    wait_until_ready = protocol.DockerAgent._wait_until_ready
    api = mock_docker(monkeypatch)
    def bounded_wait(agent):
        agent.connection_timeout = 0.05
        wait_until_ready(agent)
    monkeypatch.setattr(protocol.DockerAgent, "_wait_until_ready", bounded_wait)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        monkeypatch.setattr(protocol, "get_free_port", lambda: reserved.getsockname()[1])
        start = time.monotonic()
        with pytest.raises(protocol.ProtocolTimeoutError):
            protocol.DockerAgent("local", image="trusted-image", command=None)
        assert time.monotonic() - start < 0.8
    api.containers.run.return_value.kill.assert_called_once_with()
    api.close.assert_called_once_with()


def test_server_deadline_includes_callback_time():
    agent = Mock()
    agent.name = "local"
    def slow_reply(request):
        time.sleep(0.12)
        return "late reply"
    with running_server(agent, timeout=0.08) as server:
        server.handle_request = slow_reply
        with socket.create_connection((server.host, server.port), timeout=1) as peer:
            protocol.send_data(request(), peer)
            with pytest.raises(protocol.UnexpectedEOFError):
                protocol.receive_data(peer, timeout=0.5)


def test_zero_connection_timeout_is_typed(game):
    with protocol.PythonSocketClient("local", connection_timeout=0) as client:
        with pytest.raises(protocol.ProtocolTimeoutError):
            client.end_game(game)
        assert client.agent_id not in protocol.sockets



def test_server_close_racing_accept_does_not_read_new_peer(monkeypatch):
    server = protocol.PythonSocketServer("local", port=0)
    listener = MagicMock(spec=socket.socket)
    listener.__enter__.return_value = listener
    listener.getsockname.return_value = ("127.0.0.1", 5100)
    peer = MagicMock(spec=socket.socket)
    peer.__enter__.return_value = peer
    peer.recv.side_effect = AssertionError("Closed server must not read a new peer")
    def accept():
        server.close()
        return peer, ("127.0.0.1", 5101)
    listener.accept.side_effect = accept
    monkeypatch.setattr(protocol.socket, "socket", Mock(return_value=listener))
    server.run()
    peer.recv.assert_not_called()
    peer.__exit__.assert_called_once()
    assert server.socket_ is None
    assert server._connection is None



@pytest.mark.parametrize("host", ["tcp://192.0.2.1:2375", "ssh://example.com",
                                  "http://127.0.0.1:2375", "npipe:////./pipe/docker_engine"])
def test_docker_rejects_remote_configuration_before_sdk_initialization(monkeypatch, host):
    monkeypatch.setenv("DOCKER_HOST", host)
    factory = Mock(side_effect=AssertionError("Must reject before SDK version negotiation"))
    monkeypatch.setattr(protocol.docker, "from_env", factory)
    with pytest.raises(protocol.UnsafeTransportError):
        protocol.DockerAgent("local", image="trusted-image", command=None)
    factory.assert_not_called()



class ExtendedRequest(protocol.Request):
    pass


def test_request_subclasses_keep_legacy_compatibility():
    value = ExtendedRequest(protocol.AgentCommand.STATE_NAME)
    agent = Mock()
    agent.name = "local"
    with socket_pair() as (left, right):
        protocol.send_data(value, left)
        decoded = protocol.receive_data(right)
    with protocol.PythonSocketServer(agent) as server:
        assert server.handle_request(decoded) == "local"
