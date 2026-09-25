from __future__ import annotations

from iar_cspy import CSpyError, SessionError
from iar_cspy.errors import DC_RESULTS, classify, cspy_exception_info, describe, looks_like_backend_fault
from iar_cspy.testing import cspy_exception


def test_backend_fault_detection_ignores_client_side_shape_errors():
    assert looks_like_backend_fault("Field 'ref(1)' of 'evalExpression_args' needs type 'ContextRef'") is False
    assert looks_like_backend_fault("WinError 10054 connection reset") is True


def test_cspy_exception_decoded_from_object_and_text():
    exc = cspy_exception(code=8, method="DkLoadModule", message="Failed", culprit="test.out")
    info = cspy_exception_info(exc)
    assert (info.code, info.method, info.culprit) == (8, "DkLoadModule", "test.out")
    assert cspy_exception_info(CSpyError(str(exc))) == info
    assert "hint=License violation" in describe(exc)


def test_classify_license_violation():
    out = classify(cspy_exception(code=8, method="DkLoadModule", message="Failed to load module"))
    assert out["code"] == "CSPY_KDCLICENSEVIOLATION"
    assert out["category"] == "backend"
    assert "license" in out["message"].lower()
    assert out["details"]["cspy"]["code"] == 8


def test_classify_transport_and_lifecycle_errors():
    assert classify(CSpyError("Connection reset (WinError 10054)"))["code"] == "TRANSPORT_CONNECTION_RESET"
    assert classify(CSpyError("connection refused"))["code"] == "TRANSPORT_CONNECTION_REFUSED"
    assert classify(CSpyError("timed out"))["retryable"] is True
    assert classify(SessionError("x"))["code"] == "SESSION_NOT_STARTED"
    assert classify(ValueError("?"))["code"] == "UNKNOWN_ERROR"


def test_classify_attaches_diagnostics_only_for_backend_faults():
    diagnostics = lambda: {"backend_diagnostics": "tail", "service_launcher_diagnostics": ""}  # noqa: E731
    assert classify(CSpyError("connection reset"), diagnostics=diagnostics)["backend_diagnostics"] == "tail"
    entry = classify(CSpyError("connection reset"), diagnostics=diagnostics)
    assert "service_launcher_diagnostics" not in entry
    assert "backend_diagnostics" not in classify(CSpyError("invalid json"), diagnostics=diagnostics)


def test_dc_result_names():
    assert DC_RESULTS[8] == "kDcLicenseViolation"
