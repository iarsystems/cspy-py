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


# -- entry points -------------------------------------------------------------


def test_launch_reads_the_environment(monkeypatch, tmp_path):
    import iar_cspy

    monkeypatch.setenv("IAR_INSTALL_PATH", str(tmp_path))
    monkeypatch.setenv("THRIFT_TIMEOUT_MS", "1234")
    client = iar_cspy.launch()
    assert (client.config.mode, client.config.iar_path, client.config.timeout_ms) == ("managed", tmp_path, 1234)
    assert client.owns_backend
    other = tmp_path / "other"
    assert iar_cspy.launch(other, timeout_ms=5).config.iar_path == other.resolve()


def test_connect_reads_the_environment(monkeypatch):
    import iar_cspy

    monkeypatch.setenv("THRIFT_REGISTRY_PORT", "5000")
    monkeypatch.setenv("THRIFT_REGISTRY_HOST", "10.0.0.5")
    client = iar_cspy.connect()
    assert (client.config.mode, client.config.registry_host, client.config.registry_port) == (
        "standalone",
        "10.0.0.5",
        5000,
    )
    assert not client.owns_backend
    assert iar_cspy.connect(6000).config.registry_port == 6000


def test_a_client_owns_the_backend_it_created():
    from iar_cspy import Backend

    assert Client(Config()).owns_backend is True
    assert Client().owns_backend is True
    assert Client(Config(), owns_backend=False).owns_backend is False
    shared = Backend(Config())
    assert Client(shared).owns_backend is False
    assert Client(shared, owns_backend=True).owns_backend is True


def test_call_converts_errors_and_checks_the_session_backend(client, monkeypatch):
    from iar_cspy import SessionError

    monkeypatch.setattr(client, "endpoint", lambda service: ("127.0.0.1", 1))

    def invoke_at(service_cls, host, port, label, method, *args, **kwargs):
        return client.traced(label, method, lambda: (_ for _ in ()).throw(cspy_exception(code=8)))

    monkeypatch.setattr(client, "invoke_at", invoke_at)
    with pytest.raises(BackendError) as exc:
        client.call("debugger", "go")
    assert client.classify_error(exc.value)["code"] == "CSPY_KDCLICENSEVIOLATION"

    client.debugger.set_state(configured=True, started=True)
    client.backend.debugger_generation += 1
    with pytest.raises(SessionError, match="the debug session is gone"):
        client.call("memory", "readMemory", {"zone": {"id": 0}, "address": 0}, 1, 8, 4)
    # Services outside the debug session are not affected.
    client.debugger.set_state(configured=True, started=True)
    client.backend.debugger_generation += 1
    with pytest.raises(BackendError):
        client.call("libsupport", "requestInput", 1)


def test_capabilities_never_raise(fake):
    client, rpc = fake
    rpc.on("debugger", "isOnline", CSpyError("connection refused"))
    caps = client.capabilities()
    assert caps["backend_online"] is None and caps["debugger_methods"]
    assert [e["message"] for e in caps["errors"]] == ["isOnline failed"]
    assert caps["errors"][0]["details"]["code"] == "TRANSPORT_CONNECTION_REFUSED"
