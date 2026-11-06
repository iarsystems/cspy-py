"""Backend.wait_for_registry()/registry_configured, and connect()'s discovery-first default.

registry_endpoint() itself (the "find" step, tried fresh every call, no cache
to invalidate) is covered in test_discovery.py alongside discover(); this file
covers the blocking/waiting half, and the public connect() entry point.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

import iar_cspy as cspy
from iar_cspy import Backend, Config, discovery
from iar_cspy.errors import NotConnectedError


@pytest.fixture()
def listening_port():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


@pytest.fixture()
def dead_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def write_remote_file(tmp_path, name, **fields):
    defaults = {
        "process_id": str(os.getpid()),
        "sessionid": str(os.getpid()),
        "start_time": "2026-10-29T14:55:49.691885848Z",
        "version": "IAR Embedded Workbench 9.6.1.0",
        "workspace": "/home/user/project/arm_main.eww",
    }
    defaults.update(fields)
    path = tmp_path / f"{name}.remote"
    path.write_text(json.dumps(defaults))
    return path


# -- registry_configured -------------------------------------------------------


def test_registry_configured_managed():
    assert Backend(Config(mode="managed")).registry_configured is True


def test_registry_configured_registry_uri():
    backend = Backend(Config(mode="standalone", registry_uri="thrift://127.0.0.1:1"))
    assert backend.registry_configured is True


def test_registry_configured_discovery_opted_in(tmp_path):
    backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))
    assert backend.registry_configured is True


def test_registry_configured_nothing_at_all():
    assert Backend(Config(mode="standalone")).registry_configured is False


# -- wait_for_registry() -------------------------------------------------------


def test_wait_for_registry_uri_probes_and_waits(dead_port):
    backend = Backend(Config(mode="standalone", registry_uri=f"thrift://127.0.0.1:{dead_port}"))
    with pytest.raises(NotConnectedError, match="Timed out"):
        backend.wait_for_registry(timeout_s=0.3, poll_interval_s=0.05)


def test_wait_for_registry_uri_returns_once_reachable(tmp_path):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    sock.close()  # starts out dead

    def listen_soon():
        time.sleep(0.2)
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        time.sleep(2)
        s.close()

    threading.Thread(target=listen_soon, daemon=True).start()
    backend = Backend(Config(mode="standalone", registry_uri=f"thrift://127.0.0.1:{port}"))

    assert backend.wait_for_registry(timeout_s=5, poll_interval_s=0.05) == ("127.0.0.1", port)


def test_wait_for_registry_discovery_returns_once_found(tmp_path, listening_port):
    backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))

    def add_candidate_soon():
        time.sleep(0.2)
        write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")

    threading.Thread(target=add_candidate_soon).start()

    assert backend.wait_for_registry(timeout_s=5, poll_interval_s=0.05) == ("127.0.0.1", listening_port)


def test_wait_for_registry_discovery_times_out(tmp_path):
    backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))
    start = time.monotonic()
    with pytest.raises(NotConnectedError, match="Timed out"):
        backend.wait_for_registry(timeout_s=0.3, poll_interval_s=0.05)
    assert time.monotonic() - start < 2.0


def test_wait_for_registry_raises_immediately_for_no_registry():
    backend = Backend(Config(mode="standalone"))
    with pytest.raises(NotConnectedError):
        backend.wait_for_registry(timeout_s=5)


def test_wait_for_registry_managed_delegates_to_registry_endpoint(monkeypatch):
    backend = Backend(Config(mode="managed"))
    monkeypatch.setattr(backend, "launch_services", lambda: ("127.0.0.1", 12345))
    assert backend.wait_for_registry(timeout_s=5) == ("127.0.0.1", 12345)


# -- forget_registry(): the "reconnect when disconnected" trigger --------------


def test_forget_registry_makes_the_next_lookup_look_again(tmp_path):
    old_sock = socket.socket()
    old_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    old_sock.bind(("127.0.0.1", 0))
    old_sock.listen(1)
    old_port = old_sock.getsockname()[1]

    new_sock = socket.socket()
    new_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    new_sock.bind(("127.0.0.1", 0))
    new_sock.listen(1)
    new_port = new_sock.getsockname()[1]

    try:
        write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{old_port}")
        backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))
        assert backend.registry_endpoint() == ("127.0.0.1", old_port)

        # The old session closes; a new one starts and writes its own file.
        old_sock.close()
        write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{new_port}")
        # Cached and cheap: still the old (now-dead) endpoint until told to forget.
        assert backend.registry_endpoint() == ("127.0.0.1", old_port)

        backend.forget_registry()
        assert backend.registry_endpoint() == ("127.0.0.1", new_port)
    finally:
        new_sock.close()


def test_forget_registry_is_a_no_op_for_an_explicit_registry_uri(listening_port):
    backend = Backend(Config(mode="standalone", registry_uri=f"thrift://127.0.0.1:{listening_port}"))
    backend.forget_registry()
    assert backend.registry_endpoint() == ("127.0.0.1", listening_port)


def test_endpoint_forgets_the_registry_on_a_failed_resolve(tmp_path, listening_port, monkeypatch):
    write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")
    client = cspy.Client(Config(mode="standalone", auto_discovery_dir=tmp_path))
    assert client.backend.registry_endpoint() == ("127.0.0.1", listening_port)

    real_discover = discovery.discover
    scans: list[None] = []

    def counting_discover(*args, **kwargs):
        scans.append(None)
        return real_discover(*args, **kwargs)

    monkeypatch.setattr(discovery, "discover", counting_discover)

    def resolve_fails(*args, **kwargs):
        raise cspy.CSpyError("socket closed")

    monkeypatch.setattr(client.registry, "resolve", resolve_fails)
    with pytest.raises(cspy.CSpyError):
        client.endpoint("debugger")

    assert not scans, "still cached: the failed resolve itself shouldn't trigger a rescan"
    client.backend.registry_endpoint()  # the endpoint was forgotten, so this scans again
    assert scans


# -- Client.endpoint() / NotConnectedError -------------------------------------


def test_endpoint_raises_not_connected_when_discovery_finds_nothing(tmp_path):
    client = cspy.Client(Config(mode="standalone", auto_discovery_dir=tmp_path))
    with pytest.raises(NotConnectedError):
        client.endpoint("debugger")


def test_endpoint_direct_debugger_fallback_only_when_nothing_configured():
    client = cspy.Client(Config(mode="standalone", host="10.0.0.1", port=9091))
    assert client.endpoint("debugger") == ("10.0.0.1", 9091)
    with pytest.raises(NotConnectedError):
        client.endpoint("breakpoints")


# -- connect() defaults to discovery -------------------------------------------


def test_connect_tries_discovery_by_default(tmp_path, listening_port, monkeypatch):
    monkeypatch.setattr(discovery, "DEFAULT_REMOTE_CONTROL_DIR", tmp_path)
    write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")

    client = cspy.connect()

    assert client.backend.config.auto_discovery_enabled is True
    assert client.backend.registry_endpoint() == ("127.0.0.1", listening_port)


def test_connect_with_registry_port_skips_discovery(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("discovery should not run when registry_port is given")

    monkeypatch.setattr(discovery, "discover", fail)

    client = cspy.connect(registry_port=4711)

    assert client.backend.config.auto_discovery_enabled is False
    assert client.backend.registry_endpoint() == ("127.0.0.1", 4711)


def test_connect_with_registry_uri_skips_discovery(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("discovery should not run when registry_uri is given")

    monkeypatch.setattr(discovery, "discover", fail)

    client = cspy.connect(registry_uri="thrift://10.0.0.1:4711")

    assert client.backend.config.auto_discovery_enabled is False
    assert client.backend.registry_endpoint() == ("10.0.0.1", 4711)


def test_connect_rejects_both_registry_port_and_registry_uri():
    with pytest.raises(TypeError):
        cspy.connect(registry_port=4711, registry_uri="thrift://127.0.0.1:4712")


def test_connect_rejects_registry_host_without_port():
    with pytest.raises(TypeError):
        cspy.connect(registry_host="10.0.0.1")


# -- Backend.standalone()'s registry_uri parameter -----------------------------


def test_backend_standalone_accepts_registry_uri_directly():
    backend = Backend.standalone(registry_uri="thrift://10.0.0.2:5678")
    assert backend.config.registry_uri == "thrift://10.0.0.2:5678"


def test_backend_standalone_needs_a_port_or_uri():
    with pytest.raises(TypeError):
        Backend.standalone()


def test_backend_standalone_rejects_both_registry_port_and_registry_uri():
    with pytest.raises(TypeError):
        Backend.standalone(registry_port=1, registry_uri="thrift://127.0.0.1:2")


def test_connect_auto_discovery_enabled_false_opts_out(tmp_path, listening_port, monkeypatch):
    monkeypatch.setattr(discovery, "DEFAULT_REMOTE_CONTROL_DIR", tmp_path)
    write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")

    client = cspy.connect(auto_discovery_enabled=False)

    assert client.backend.config.auto_discovery_enabled is False
    assert client.backend.registry_endpoint() is None


def test_connect_discovery_timeout_blocks_until_ready(tmp_path, listening_port):
    def add_candidate_soon():
        time.sleep(0.2)
        write_remote_file(tmp_path, "live", remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")

    threading.Thread(target=add_candidate_soon).start()

    client = cspy.connect(auto_discovery_dir=tmp_path, discovery_timeout=5)

    assert client.backend.registry_endpoint() == ("127.0.0.1", listening_port)


def test_connect_discovery_timeout_raises_not_connected_error(tmp_path):
    with pytest.raises(NotConnectedError):
        cspy.connect(auto_discovery_dir=tmp_path, discovery_timeout=0.3)
