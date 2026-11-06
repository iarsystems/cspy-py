"""Remote-control discovery files: parsing, filtering, and the try-in-turn order."""

from __future__ import annotations

import json
import os
import socket

import pytest

from iar_cspy import Backend, Config, discovery
from iar_cspy.discovery import discover, is_process_alive


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
    path = tmp_path / f"{name}.remote"
    path.write_text(json.dumps(fields))
    return path


def example_fields(**overrides):
    fields = {
        "process_id": str(os.getpid()),
        "remote_control_service_registry": "thrift://127.0.0.1:35695",
        "sessionid": str(os.getpid()),
        "start_time": "2026-10-29T14:55:49.691885848Z",
        "version": "IAR Embedded Workbench 9.6.1.0",
        "workspace": "/home/user/project/arm_main.eww",
    }
    fields.update(overrides)
    return fields


def test_discover_parses_the_example_format(tmp_path):
    write_remote_file(tmp_path, "2545927", **example_fields())

    [info] = discover(directory=tmp_path)

    assert info.registry_uri == "thrift://127.0.0.1:35695"
    assert info.process_id == os.getpid()
    assert info.sessionid == str(os.getpid())
    assert info.workspace == "/home/user/project/arm_main.eww"
    assert info.version == "IAR Embedded Workbench 9.6.1.0"
    assert info.start_time == "2026-10-29T14:55:49.691885848Z"


def test_discover_defaults_to_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "DEFAULT_REMOTE_CONTROL_DIR", tmp_path)
    write_remote_file(tmp_path, "1", **example_fields())

    assert len(discover()) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"remote_control_service_registry": "not-a-url"},
        {"remote_control_service_registry": "http://127.0.0.1:35695"},
        {},  # no registry key at all
    ],
)
def test_discover_skips_files_without_a_usable_registry_url(tmp_path, fields):
    write_remote_file(tmp_path, "bad", **fields)
    assert discover(directory=tmp_path) == []


def test_discover_skips_unparsable_files(tmp_path):
    (tmp_path / "broken.remote").write_text("{not json")
    assert discover(directory=tmp_path) == []


def test_discover_ignores_files_outside_the_directory(tmp_path):
    (tmp_path / "ignored.txt").write_text(json.dumps(example_fields()))
    assert discover(directory=tmp_path) == []


def test_discover_require_alive_defaults_to_true(tmp_path):
    write_remote_file(tmp_path, "dead", **example_fields(process_id="999999999"))

    assert discover(directory=tmp_path) == []
    assert len(discover(directory=tmp_path, require_alive=False)) == 1


def test_discover_workspace_filter_is_a_hard_filter(tmp_path):
    write_remote_file(tmp_path, "a", **example_fields(workspace="/one/project.eww"))
    write_remote_file(tmp_path, "b", **example_fields(workspace="/other/project.eww"))

    matches = discover("/one/project.eww", tmp_path)

    assert [m.workspace for m in matches] == ["/one/project.eww"]


def test_discover_workspace_filter_with_no_match_returns_empty(tmp_path):
    write_remote_file(tmp_path, "a", **example_fields(workspace="/one/project.eww"))

    assert discover("/no/such/project.eww", tmp_path) == []


def test_discover_filter_accepts_a_predicate(tmp_path):
    write_remote_file(tmp_path, "a", **example_fields(sessionid="111"))
    write_remote_file(tmp_path, "b", **example_fields(sessionid="222"))

    matches = discover(lambda info: info.sessionid == "222", tmp_path)

    assert [m.sessionid for m in matches] == ["222"]


def test_discover_orders_newest_first(tmp_path):
    write_remote_file(tmp_path, "older", **example_fields(start_time="2026-01-01T00:00:00Z"))
    write_remote_file(tmp_path, "newer", **example_fields(start_time="2026-06-01T00:00:00Z"))

    matches = discover(directory=tmp_path)

    assert [m.start_time for m in matches] == ["2026-06-01T00:00:00Z", "2026-01-01T00:00:00Z"]


def test_is_process_alive(monkeypatch):
    monkeypatch.setattr(discovery.os, "name", "posix")
    assert is_process_alive(os.getpid()) is True
    assert is_process_alive(999999999) is False


# -- Backend.registry_endpoint() integration ----------------------------------


def test_backend_tries_discovered_candidates_in_turn(tmp_path, dead_port, listening_port):
    write_remote_file(
        tmp_path,
        "stale",
        **example_fields(
            start_time="2026-06-01T00:00:00Z",
            remote_control_service_registry=f"thrift://127.0.0.1:{dead_port}",
        ),
    )
    write_remote_file(
        tmp_path,
        "live",
        **example_fields(
            start_time="2026-01-01T00:00:00Z",
            remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}",
        ),
    )
    backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))

    # Newest first, but the newest one's registry is dead: fall through to the live one.
    assert backend.registry_endpoint() == ("127.0.0.1", listening_port)


def test_backend_discovery_result_is_cached(tmp_path, listening_port):
    write_remote_file(
        tmp_path, "live", **example_fields(remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")
    )
    backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))

    assert backend.registry_endpoint() == ("127.0.0.1", listening_port)

    tmp_path.joinpath("live.remote").unlink()
    # The directory no longer has anything to discover; the cached endpoint still answers.
    assert backend.registry_endpoint() == ("127.0.0.1", listening_port)


def test_backend_does_not_discover_without_opting_in(tmp_path, listening_port, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("discovery should not run without opting in")

    monkeypatch.setattr(discovery, "DEFAULT_REMOTE_CONTROL_DIR", tmp_path)
    monkeypatch.setattr(discovery, "discover", fail)
    write_remote_file(
        tmp_path, "live", **example_fields(remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")
    )

    # A bare standalone Config (no registry_uri, no auto_discovery_* fields)
    # must never scan the filesystem on its own.
    assert Backend(Config(mode="standalone")).registry_endpoint() is None


def test_backend_auto_discovery_enabled_opts_in(tmp_path, listening_port):
    write_remote_file(
        tmp_path, "live", **example_fields(remote_control_service_registry=f"thrift://127.0.0.1:{listening_port}")
    )
    backend = Backend(Config(mode="standalone", auto_discovery_enabled=True, auto_discovery_dir=tmp_path))

    assert backend.registry_endpoint() == ("127.0.0.1", listening_port)


def test_backend_registry_endpoint_is_none_when_nothing_is_reachable(tmp_path):
    backend = Backend(Config(mode="standalone", auto_discovery_dir=tmp_path))
    assert backend.registry_endpoint() is None


def test_backend_explicit_registry_uri_skips_discovery(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("discovery should not run when registry_uri is given")

    monkeypatch.setattr(discovery, "discover", fail)
    backend = Backend(Config(mode="standalone", registry_uri="thrift://127.0.0.1:4711", auto_discovery_dir=tmp_path))

    assert backend.registry_endpoint() == ("127.0.0.1", 4711)
