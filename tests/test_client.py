"""Client.call plumbing: service table, error conversion, diagnostics."""

from __future__ import annotations

import pytest

from iar_cspy import BackendError, Client, Config, CSpyError
from iar_cspy.errors import classify
from iar_cspy.testing import cspy_exception


@pytest.fixture()
def client(monkeypatch):
    client = Client(Config(mode="standalone", registry_port=4711))
    monkeypatch.setattr(client.backend, "diagnostics_text", lambda: "backend diagnostics")
    return client


def test_traced_wraps_client_side_errors_without_diagnostics(client):
    def bad_call():
        raise TypeError("Field 'ref(1)' of 'evalExpression_args' needs type 'ContextRef'")

    with pytest.raises(CSpyError) as exc:
        client.traced("debugger", "evalExpression", bad_call)

    assert "needs type" in str(exc.value)
    assert "backend diagnostics" not in str(exc.value)
    assert isinstance(exc.value.__cause__, TypeError)


def test_traced_appends_diagnostics_for_transport_errors(client):
    def bad_call():
        raise ConnectionResetError("WinError 10054")

    with pytest.raises(CSpyError) as exc:
        client.traced("debugger", "go", bad_call)

    assert "backend diagnostics" in str(exc.value)
    assert exc.value.diagnostics == "backend diagnostics"


def test_traced_decodes_cspy_exceptions(client):
    with pytest.raises(BackendError) as exc:
        client.traced("debugger", "stop", lambda: (_ for _ in ()).throw(cspy_exception()))

    assert exc.value.info.code == 6
    assert exc.value.info.code_name == "kDcError"
    assert "CSpyException kDcError (6)" in str(exc.value)
    assert classify(exc.value)["code"] == "CSPY_KDCERROR"


def test_unknown_service_key(client):
    with pytest.raises(CSpyError, match="Unknown service"):
        client.call("nosuchservice", "x")


def test_registry_names(client):
    assert client.registry_name("debugger") == "debugger"
    assert client.registry_name("contextmanager") == "debugger.contextmanager"
    assert client.registry_name("memory") == "debugger.memory"
    assert client.registry_name("projectmanager") == "com.iar.thrift.service.projectmanager"
    assert client.registry_name("servicemanager") == "com.iar.thrift.service.manager"
    assert client.registry_name("breakpoints") == "breakpoints"


def test_direct_debugger_endpoint_without_registry():
    client = Client(Config(mode="standalone", host="10.0.0.1", port=9091))
    assert client.endpoint("debugger") == ("10.0.0.1", 9091)
    with pytest.raises(CSpyError, match="registry"):
        client.endpoint("breakpoints")


def test_ide_service_resolution_failure_names_the_hosting_requirement(client, monkeypatch):
    monkeypatch.setattr(client.ide_services, "auto_ensure", lambda key: None)

    def fail(name, registry=None):
        raise CSpyError("not registered")

    monkeypatch.setattr(client.registry, "resolve", fail)
    with pytest.raises(CSpyError, match="IarServiceLauncher or a Thrift-enabled iaride"):
        client.call("projectmanager", "HasWorkspace")


def test_raw_service_routes_through_call(client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "call", lambda service, method, *a, **k: seen.append((service, method, a)))
    client.raw("debugger").getAllZones()
    assert seen == [("debugger", "getAllZones", ())]
    assert "getAllZones" in dir(client.raw("debugger"))


def test_methods_lists_idl_methods(client):
    assert "go" in client.methods("debugger")
    assert "setBreakpointOnUle" in client.methods("breakpoints")


def test_connection_failures_become_cspy_errors(client):
    # Nothing listens on port 1: opening the connection itself fails.
    service_cls = client.service_class("debugger")
    with pytest.raises(CSpyError) as exc:
        client.invoke_at(service_cls, "127.0.0.1", 1, "debugger", "isOnline")
    assert "Could not connect" in str(exc.value)
    assert classify(exc.value)["code"] == "TRANSPORT_CONNECTION_REFUSED"
    assert exc.value.diagnostics == "backend diagnostics"
