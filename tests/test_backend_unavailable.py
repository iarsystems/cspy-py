"""BackendUnavailableError: a missing/dead registry says what it means, not just "Could not connect".

Real Clients against real (dead or absent) sockets, so the transport failures
are the ones thriftpy2 raises, not scripted text.
"""

from __future__ import annotations

import json
import os
import socket

import pytest

import iar_cspy as cspy
from iar_cspy import BackendUnavailableError, Config, CSpyError, NotConnectedError
from iar_cspy.errors import (
    DISCOVERED_GONE,
    FIXED_UNREACHABLE,
    MANAGED_EXITED,
    NEVER_FOUND,
    classify,
    with_context,
)


@pytest.fixture()
def dead_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def fixed_client(port: int) -> cspy.Client:
    return cspy.Client(Config(mode="standalone", registry_uri=f"thrift://127.0.0.1:{port}"))


def write_remote_file(directory, port: int) -> None:
    (directory / "ide.remote").write_text(
        json.dumps(
            {
                "process_id": str(os.getpid()),
                "sessionid": str(os.getpid()),
                "start_time": "2026-10-29T14:55:49.691885848Z",
                "version": "IAR Embedded Workbench 9.6.1.0",
                "workspace": "/home/user/project/arm_main.eww",
                "remote_control_service_registry": f"thrift://127.0.0.1:{port}",
            }
        )
    )


# -- a fixed registry that is not answering ------------------------------------


def test_a_dead_fixed_registry_is_explained(dead_port):
    client = fixed_client(dead_port)
    with pytest.raises(BackendUnavailableError) as exc_info:
        client.debugger.is_online()
    exc = exc_info.value
    assert exc.kind == FIXED_UNREACHABLE
    assert f"127.0.0.1:{dead_port}" in exc.hint
    assert "Could not connect" in exc.reason  # the raw transport text is kept
    assert exc.hint in str(exc)
    assert str(exc).count(exc.hint) == 1  # not repeated by the wrapping in resolve()


def test_it_is_still_a_not_connected_error(dead_port):
    with pytest.raises(NotConnectedError):
        fixed_client(dead_port).registry.services()


def test_classify_reports_a_gone_backend_as_its_own_code(dead_port):
    with pytest.raises(BackendUnavailableError) as exc_info:
        fixed_client(dead_port).registry.services()
    record = classify(exc_info.value)
    assert record["code"] == "BACKEND_GONE"
    assert record["retryable"] is True
    assert record["details"]["kind"] == FIXED_UNREACHABLE
    assert record["details"]["hint"] == exc_info.value.hint
    assert exc_info.value.hint not in record["message"]


def test_an_ide_service_call_keeps_the_explanation(dead_port):
    """Not rewritten into "could not resolve the service ... hosted by IarServiceLauncher"."""
    client = fixed_client(dead_port)
    with pytest.raises(BackendUnavailableError) as exc_info:
        client.call("projectmanager", "GetProjectFiles")
    assert exc_info.value.kind == FIXED_UNREACHABLE
    assert "IarServiceLauncher" not in str(exc_info.value)


# -- never found ----------------------------------------------------------------


def test_nothing_discovered_is_never_found(tmp_path):
    client = cspy.Client(Config(mode="standalone", auto_discovery_dir=tmp_path))
    with pytest.raises(BackendUnavailableError) as exc_info:
        client.endpoint("debugger")
    assert exc_info.value.kind == NEVER_FOUND
    assert "No backend found" in exc_info.value.hint
    record = classify(exc_info.value)
    assert (record["code"], record["retryable"]) == ("BACKEND_NOT_FOUND", False)


def test_the_registry_error_for_a_never_found_backend_is_also_explained(tmp_path):
    client = cspy.Client(Config(mode="standalone", auto_discovery_dir=tmp_path))
    with pytest.raises(BackendUnavailableError) as exc_info:
        client.registry.services()
    assert exc_info.value.kind == NEVER_FOUND


def test_no_registry_configured_says_how_to_configure_one():
    backend = cspy.Backend(Config(mode="standalone", auto_discovery_enabled=False))
    with pytest.raises(BackendUnavailableError) as exc_info:
        backend.wait_for_registry(0.1)
    assert exc_info.value.kind == NEVER_FOUND
    assert "No registry configured" in exc_info.value.hint


def test_waiting_for_a_registry_that_never_comes_up_is_explained(dead_port):
    backend = cspy.Backend(Config(mode="standalone", registry_uri=f"thrift://127.0.0.1:{dead_port}"))
    with pytest.raises(BackendUnavailableError, match="Timed out") as exc_info:
        backend.wait_for_registry(0.2)
    assert exc_info.value.kind == FIXED_UNREACHABLE


# -- a discovered backend that went away ------------------------------------------


def test_a_discovered_backend_that_goes_away_is_gone_not_never_found(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    write_remote_file(tmp_path, listener.getsockname()[1])
    client = cspy.Client(Config(mode="standalone", auto_discovery_dir=tmp_path))
    assert client.backend.registry_endpoint() == listener.getsockname()  # found once

    listener.close()  # the IDE closes
    with pytest.raises(BackendUnavailableError) as exc_info:
        client.registry.services()
    assert exc_info.value.kind == DISCOVERED_GONE
    assert "discovers again" in exc_info.value.hint

    # The cache was forgotten; the *next* call finds nothing, which is still "gone".
    with pytest.raises(BackendUnavailableError) as exc_info:
        client.endpoint("debugger")
    assert exc_info.value.kind == DISCOVERED_GONE


# -- managed ------------------------------------------------------------------------


def test_a_managed_backend_that_stopped_answering_says_retry():
    backend = cspy.Backend(Config(mode="managed"))
    exc = backend.unavailable("registry reset", reached=True, diagnostics="last words")
    assert exc.kind == MANAGED_EXITED
    assert "next call starts a new one" in exc.hint
    assert exc.diagnostics == "last words"
    assert str(exc).endswith("last words")
    assert classify(exc)["message"] == "registry reset"  # diagnostics are separate from the message


def test_an_unreached_managed_backend_is_not_called_exited():
    """Never reached = a launch problem, not an exit: no claim that it ran and stopped."""
    assert cspy.Backend(Config(mode="managed")).unavailable("x").kind == NEVER_FOUND


# -- what is left alone -----------------------------------------------------------------


def test_a_slow_registry_is_not_called_gone(dead_port, monkeypatch):
    client = fixed_client(dead_port)

    def slow(*args, **kwargs):
        raise CSpyError("Thrift RPC failed: TTransportException: timed out")

    monkeypatch.setattr(client, "invoke_at", slow)
    with pytest.raises(CSpyError) as exc_info:
        client.registry.services()
    assert not isinstance(exc_info.value, BackendUnavailableError)


def test_with_context_keeps_the_kind_and_does_not_repeat_the_hint(dead_port):
    with pytest.raises(BackendUnavailableError) as exc_info:
        fixed_client(dead_port).registry.services()
    original = exc_info.value
    wrapped = with_context(original, f"while starting: {original}")
    assert isinstance(wrapped, BackendUnavailableError)
    assert (wrapped.kind, wrapped.hint) == (original.kind, original.hint)
    assert str(wrapped).count(original.hint) == 1


# -- status snapshots ----------------------------------------------------------------------


def test_debugger_status_adds_a_hint_next_to_the_raw_error(dead_port):
    status = fixed_client(dead_port).debugger.status()
    assert "Could not connect" in status["backend_online_error"]
    assert status["backend_online_error_kind"] == FIXED_UNREACHABLE
    assert status["backend_online_error_hint"].startswith("The registry at")
    assert status["backend_online_error_hint"] not in status["backend_online_error"]


def test_ide_services_status_adds_a_hint_next_to_the_raw_error(tmp_path):
    client = cspy.Client(Config(mode="standalone", auto_discovery_dir=tmp_path))
    data = client.ide_services.status()
    assert "registry_error" in data
    assert data["registry_error_kind"] == NEVER_FOUND
    assert "No backend found" in data["registry_error_hint"]


def test_status_of_a_failure_that_is_not_a_missing_backend_has_no_hint(monkeypatch):
    from iar_cspy.testing import cspy_exception, fake_client

    client, rpc = fake_client()
    rpc.on("debugger", "isOnline", cspy_exception(code=3, method="isOnline", message="Session aborted!"))
    status = client.debugger.status()
    assert "backend_online_error" in status
    assert "backend_online_error_hint" not in status
    assert "backend_online_error_kind" not in status
