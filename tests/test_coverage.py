"""Code coverage (client.coverage)."""

from __future__ import annotations

import pytest

from iar_cspy import BackendError, CodeCoverage, CSpyError, SessionError
from iar_cspy.testing import cspy_exception

REPORT = "<?xml version = \"1.0\"?><cpMetaDataInfo><program><name>app</name></program></cpMetaDataInfo>"


def _void(*args):
    return None


def test_the_client_has_a_coverage_object(fake):
    client, _ = fake
    assert isinstance(client.coverage, CodeCoverage)
    assert client.registry_name("codecoverage") == "codecoverage"


@pytest.mark.parametrize(
    ("method", "rpc_method", "response", "expected"),
    [
        ("has_metadata_support", "hasMetaDataSupport", True, True),
        ("is_enabled", "isEnabled", False, False),
        ("is_initialized", "isInitialized", True, True),
        ("session_id", "getSessionId", 1234567890123, 1234567890123),
        ("initialize", "initializeMetaData", None, None),
        ("refresh", "refreshMetaData", None, None),
        ("clear_cache", "clearCachedData", None, None),
        ("reset", "reinitMetaData", True, None),
    ],
)
def test_methods_forward_to_the_service(session, method, rpc_method, response, expected):
    client, rpc = session
    rpc.on("codecoverage", rpc_method, response)
    assert getattr(client.coverage, method)() == expected
    assert rpc.methods("codecoverage") == [rpc_method]


def test_enable_and_disable(session):
    client, rpc = session
    rpc.on("codecoverage", "enable", True)
    client.coverage.enable()
    client.coverage.disable()
    assert rpc.args("codecoverage", "enable") == [(True,), (False,)]


@pytest.mark.parametrize(("method", "still_enabled"), [("enable", False), ("disable", True)])
def test_enable_raises_when_the_state_did_not_change(session, method, still_enabled):
    client, rpc = session
    rpc.on("codecoverage", "enable", False)
    rpc.on("codecoverage", "isEnabled", still_enabled)
    with pytest.raises(CSpyError, match=f"failed to {method}"):
        getattr(client.coverage, method)()


def test_enable_trusts_the_state_over_the_result(session):
    # Switching coverage on from off returns False but works (docs/backend-notes.md).
    client, rpc = session
    rpc.on("codecoverage", "enable", False)
    rpc.on("codecoverage", "isEnabled", True)
    client.coverage.enable()
    assert rpc.methods("codecoverage") == ["enable", "isEnabled"]


def test_a_refused_reset_raises(session):
    client, rpc = session
    rpc.on("codecoverage", "reinitMetaData", False)
    with pytest.raises(CSpyError, match="failed to reset"):
        client.coverage.reset()


def test_xml_initializes_first(session):
    client, rpc = session
    rpc.on("codecoverage", "isInitialized", False)
    rpc.on("codecoverage", "initializeMetaData", _void)
    rpc.on("codecoverage", "getXMLData", REPORT)
    assert client.coverage.xml() == REPORT
    assert rpc.methods("codecoverage") == ["isInitialized", "initializeMetaData", "getXMLData"]


def test_xml_refreshes_once_initialized(session):
    client, rpc = session
    rpc.on("codecoverage", "isInitialized", True)
    rpc.on("codecoverage", "refreshMetaData", _void)
    rpc.on("codecoverage", "getXMLData", REPORT)
    assert client.coverage.xml() == REPORT
    assert rpc.methods("codecoverage") == ["isInitialized", "refreshMetaData", "getXMLData"]


def test_xml_without_refresh(session):
    client, rpc = session
    rpc.on("codecoverage", "isInitialized", True)
    rpc.on("codecoverage", "getXMLData", REPORT)
    assert client.coverage.xml(refresh=False) == REPORT
    assert rpc.methods("codecoverage") == ["isInitialized", "getXMLData"]


def test_needs_a_started_session(fake):
    client, rpc = fake
    with pytest.raises(SessionError, match=r"client\.coverage\.xml\(\)"):
        client.coverage.xml()
    assert rpc.calls == []


def test_an_unregistered_service_says_why(session):
    client, rpc = session
    rpc.on("codecoverage", "isEnabled", CSpyError("Failed to resolve service 'codecoverage' via registry"))
    with pytest.raises(CSpyError, match="supports code coverage") as caught:
        client.coverage.is_enabled()
    assert "Failed to resolve service" in str(caught.value)


def test_a_registered_service_keeps_its_error(session):
    client, rpc = session
    client.registry.set(["codecoverage"])
    rpc.on("codecoverage", "isEnabled", CSpyError("connection reset"))
    with pytest.raises(CSpyError, match="^connection reset$"):
        client.coverage.is_enabled()


def test_a_backend_error_passes_through(session):
    client, rpc = session
    rpc.on("codecoverage", "initializeMetaData", cspy_exception(method="DkInit", message="nope"))
    with pytest.raises(BackendError, match="nope"):
        client.coverage.initialize()
