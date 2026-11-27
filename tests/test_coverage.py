"""Code coverage (client.coverage)."""

from __future__ import annotations

import pytest

from iar_cspy import BackendError, CodeCoverage, CoverageLocation, CoverageReport, CSpyError, SessionError
from iar_cspy.coverage import parse_report
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


# As CssCodeCoverageHandler::getXMLData writes it: no whitespace, names and files unescaped.
FULL_REPORT = (
    "<?xml version = \"1.0\"?><cpMetaDataInfo><program><name>R&D app</name>"
    "<execCount>3</execCount><totalCount>4</totalCount></program>"
    "<module><name>main</name><execCount>3</execCount><totalCount>4</totalCount>"
    "<function><name>main</name><execCount>2</execCount><totalCount>3</totalCount>"
    "<sourceLoc file='/src/a&b/main.c' startLine = '10' endLine = '-1' startCol = '5' endCol = '-1'/>"
    "<step><name>x = f(1) &amp;&amp; y</name><execCount>1</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/a&b/main.c' startLine = '12' endLine = '12' startCol = '3' endCol = '18'/></step>"
    "<step><name>return 0</name><execCount>0</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/a&b/main.c' startLine = '13' endLine = '13' startCol = '3' endCol = '11'/></step>"
    "</function>"
    "<function><name>f</name><execCount>1</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/f.c' startLine = '1' endLine = '-1' startCol = '1' endCol = '-1'/></function>"
    "</module></cpMetaDataInfo>"
)


def test_parse_report_builds_the_tree():
    report = parse_report(FULL_REPORT)
    assert (report.program, report.exec_count, report.total_count) == ("R&D app", 3, 4)
    [module] = report.modules
    assert (module.name, module.exec_count, module.total_count) == ("main", 3, 4)
    main, f = module.functions
    assert main.name == "main" and (main.exec_count, main.total_count) == (2, 3)
    assert main.location == CoverageLocation("/src/a&b/main.c", 10, 5, None, None)
    first, second = main.steps
    assert first.code == "x = f(1) && y"
    assert first.location == CoverageLocation("/src/a&b/main.c", 12, 3, 12, 18)
    assert (second.code, second.exec_count, second.total_count) == ("return 0", 0, 1)
    assert f.steps == []


def test_parse_report_to_dict_is_plain():
    out = parse_report(FULL_REPORT).to_dict()
    assert out["modules"][0]["functions"][0]["steps"][1]["location"]["end_col"] == 11


@pytest.mark.parametrize("bad", ["", "  ", "<cpMetaDataInfo><program>"])
def test_parse_report_rejects_what_is_not_a_report(bad):
    with pytest.raises(CSpyError, match="code coverage report"):
        parse_report(bad)


def test_report_parses_the_xml(session):
    client, rpc = session
    rpc.on("codecoverage", "isInitialized", True)
    rpc.on("codecoverage", "refreshMetaData", _void)
    rpc.on("codecoverage", "getXMLData", FULL_REPORT)
    report = client.coverage.report()
    assert isinstance(report, CoverageReport) and report.modules[0].functions[1].name == "f"
