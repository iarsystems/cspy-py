"""Exceptions and the stable error taxonomy.

Every failure the package raises is a :class:`CSpyError`. When the backend itself
reported the failure (a ``shared.CSpyException``), the error is a
:class:`BackendError` carrying the decoded debugger result code.

:func:`classify` maps any exception onto a stable, machine-readable code
(``TIMEOUT``, ``TRANSPORT_CONNECTION_RESET``, ``CSPY_KDCERROR``, ...), which is
what scripts should branch on when deciding whether to retry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


class CSpyError(RuntimeError):
    """Raised when the package cannot load IDL, reach a service, or complete an RPC.

    ``diagnostics`` holds recent output of the managed backend processes when
    the failure looks like a backend fault (crash, reset, timeout), else ``""``.
    """

    def __init__(self, message: str, *, diagnostics: str = "") -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class BackendError(CSpyError):
    """The backend raised a ``CSpyException`` (a debugger result code)."""

    def __init__(self, message: str, info: CSpyExceptionInfo, *, diagnostics: str = "") -> None:
        super().__init__(message, diagnostics=diagnostics)
        self.info = info


class SessionError(CSpyError):
    """An operation was attempted in the wrong debug-session lifecycle state."""


class WaitTimeout(CSpyError):
    """A wait for a core state ran out of time."""

    def __init__(self, message: str, result: Any) -> None:
        super().__init__(message)
        self.result = result


#: Debugger result codes (``shared.DcResultConstant``).
DC_RESULTS: dict[int, str] = {
    0: "kDcOk",
    1: "kDcRequestedStop",
    2: "kDcOtherStop",
    3: "kDcUnconditionalStop",
    4: "kDcSympatheticStop",
    5: "kDcBusy",
    6: "kDcError",
    7: "kDcFatalError",
    8: "kDcLicenseViolation",
    9: "kDcSilentFatalError",
    10: "kDcFailure",
    11: "kDcDllLoadLibFailed",
    12: "kDcDllFuncNotFound",
    13: "kDcDllFuncSlotEmpty",
    14: "kDcDllVersionMismatch",
    15: "kDcUnavailable",
}

#: The result codes a caller is realistically going to see.
FOCUSED_DC_RESULTS: tuple[int, ...] = (0, 5, 6, 7, 8, 9, 10, 15)

#: Stable error codes produced by :func:`classify` and the MCP envelopes.
ERROR_CODES: list[dict[str, Any]] = [
    {
        "code": "TIMEOUT",
        "category": "timeout",
        "retryable": True,
        "description": "Requested state was not reached within timeout.",
    },
    {
        "code": "TRANSPORT_CONNECTION_RESET",
        "category": "transport",
        "retryable": True,
        "description": "Backend connection was reset unexpectedly.",
    },
    {
        "code": "TRANSPORT_CONNECTION_REFUSED",
        "category": "transport",
        "retryable": True,
        "description": "Backend endpoint refused connection.",
    },
    {
        "code": "SESSION_NOT_STARTED",
        "category": "lifecycle",
        "retryable": False,
        "description": "Operation requires configured+started session.",
    },
    {
        "code": "CAPABILITY_CHECK_FAILED",
        "category": "discovery",
        "retryable": True,
        "description": "A capability probe call failed; other probes may still succeed.",
    },
    {
        "code": "CLEANUP_STEP_FAILED",
        "category": "cleanup",
        "retryable": True,
        "description": "One cleanup sub-step failed.",
    },
    {
        "code": "PARTIAL_CLEANUP_FAILED",
        "category": "cleanup",
        "retryable": True,
        "description": "Cleanup completed with one or more failed sub-steps.",
    },
    {
        "code": "CSPY_KDCLICENSEVIOLATION",
        "category": "backend",
        "retryable": False,
        "description": "Debugger reported kDcLicenseViolation (typically license/token/entitlement issues).",
    },
    {
        "code": "UNKNOWN_ERROR",
        "category": "unknown",
        "retryable": False,
        "description": "Fallback code for unclassified failures.",
    },
]


@dataclass(frozen=True)
class CSpyExceptionInfo:
    """A decoded ``shared.CSpyException``."""

    code: int
    method: str
    message: str
    culprit: str

    @property
    def code_name(self) -> str | None:
        return DC_RESULTS.get(self.code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "code_name": self.code_name,
            "method": self.method,
            "message": self.message,
            "culprit": self.culprit,
        }


_CSPY_EXCEPTION_PATTERN = re.compile(
    r"CSpyException\(code=(\d+), method='([^']*)', message='([^']*)', culprit='([^']*)'\)"
)


def cspy_exception_info(exc: BaseException) -> CSpyExceptionInfo | None:
    """Decode a backend ``CSpyException``, from the object or its text."""
    if isinstance(exc, BackendError):
        return exc.info

    code = getattr(exc, "code", None)
    method = getattr(exc, "method", None)
    message = getattr(exc, "message", None)
    culprit = getattr(exc, "culprit", None)
    if code is not None and (method is not None or message is not None or culprit is not None):
        return CSpyExceptionInfo(int(code), str(method or ""), str(message or ""), str(culprit or ""))

    match = _CSPY_EXCEPTION_PATTERN.search(str(exc))
    if not match:
        return None
    return CSpyExceptionInfo(int(match.group(1)), match.group(2), match.group(3), match.group(4))


def with_context(exc: BaseException, message: str) -> CSpyError:
    """A new error saying ``message``, keeping what callers branch on.

    The class (:class:`BackendError`, :class:`SessionError`), the decoded
    backend result and the diagnostics carry over from ``exc``, so
    ``except BackendError`` and :func:`classify` see the failure the backend
    reported. Raise it ``from exc``.
    """
    diagnostics = getattr(exc, "diagnostics", "") or ""
    info = cspy_exception_info(exc)
    if info is not None:
        return BackendError(message, info, diagnostics=diagnostics)
    if isinstance(exc, SessionError):
        return SessionError(message, diagnostics=diagnostics)
    return CSpyError(message, diagnostics=diagnostics)


def describe(exc: BaseException) -> str:
    """One-line human-readable rendering, decoding CSpyExceptions."""
    info = cspy_exception_info(exc)
    if info is None or isinstance(exc, BackendError):
        return str(exc)
    return describe_cspy_exception(info)


def describe_cspy_exception(info: CSpyExceptionInfo) -> str:
    parts = [
        f"CSpyException {info.code_name or 'UnknownDcResult'} ({info.code})",
        f"method={info.method or '<unknown>'}",
        f"message={info.message or '<none>'}",
    ]
    if info.culprit:
        parts.append(f"culprit={info.culprit}")

    if info.code == 8:
        parts.append(
            "hint=License violation while loading/starting debug session. "
            "Check IAR_LMS_BEARER_TOKEN or license checkout/environment entitlement."
        )
    elif info.code == 6 and info.method == "DkStop":
        parts.append(
            "hint=Backend reported suspend failure; debugger may already be stopped. "
            "Wrapper can treat this as idempotent if core state is halted."
        )
    return "; ".join(parts)


def looks_like_backend_fault(error_text: str) -> bool:
    """Whether a failure is worth attaching backend process output to.

    Caller mistakes (bad arguments, invalid JSON) are not backend faults, so
    they do not get a log tail that would only distract from the real problem.
    """
    low = (error_text or "").lower()

    client_side_markers = (
        "needs type",
        "field '",
        "invalid json",
        "must be non-empty",
        "unexpected keyword argument",
        "missing required positional argument",
    )
    if any(marker in low for marker in client_side_markers):
        return False

    backend_markers = (
        "winerror",
        "connection reset",
        "connection refused",
        "actively refused",
        "could not connect",
        "timed out",
        "timeout",
        "failed to resolve service",
        "cspyexception",
        "read memory failed",
        "failed to suspend debugger",
        "thrift rpc failed",
    )
    return any(marker in low for marker in backend_markers)


def error_entry(
    *,
    code: str,
    category: str,
    message: str,
    retryable: bool,
    details: Any | None = None,
) -> dict[str, Any]:
    """A structured error record, as used in results and MCP envelopes."""
    out: dict[str, Any] = {
        "code": str(code),
        "category": str(category),
        "message": str(message),
        "retryable": bool(retryable),
    }
    if details is not None:
        out["details"] = details
    return out


def classify(
    exc: BaseException,
    fallback_code: str = "UNKNOWN_ERROR",
    diagnostics: Callable[[], dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Map an exception onto a stable error record (see :data:`ERROR_CODES`).

    ``diagnostics`` supplies backend process output keyed by record field
    (``backend_diagnostics``, ``service_launcher_diagnostics``); it is only
    consulted when the failure looks like a backend fault.
    """
    text = _summary(exc)
    low = text.lower()
    extra: dict[str, str] = {}
    if diagnostics is not None and looks_like_backend_fault(text):
        extra = {key: value for key, value in diagnostics().items() if value}

    def entry(**kwargs: Any) -> dict[str, Any]:
        out = error_entry(**kwargs)
        out.update(extra)
        return out

    if isinstance(exc, SessionError) or "requires an active debug session" in low:
        return entry(code="SESSION_NOT_STARTED", category="lifecycle", message=text, retryable=False)

    # A result code the backend reported is the most specific answer; the
    # text checks below would also match words in its message.
    info = cspy_exception_info(exc)
    if info is not None:
        dc_name = info.code_name or "UnknownDcResult"
        if info.code == 8:
            return entry(
                code="CSPY_KDCLICENSEVIOLATION",
                category="backend",
                message=(
                    "License violation reported by debugger while loading/starting session. "
                    f"method={info.method or '<unknown>'}; dc_result={dc_name} ({info.code})."
                ),
                retryable=False,
                details={
                    "cspy": info.to_dict(),
                    "hint": "Verify bearer token/license checkout and entitlement for target/debug feature.",
                },
            )
        return entry(
            code=f"CSPY_{dc_name.upper()}",
            category="backend",
            message=f"Debugger returned {dc_name} ({info.code})" + (f" from {info.method}." if info.method else "."),
            retryable=info.code in {5, 6, 7},
            details={"cspy": info.to_dict()},
        )

    if "timed out" in low or "timeout" in low:
        return entry(code="TIMEOUT", category="timeout", message=text, retryable=True)
    if "10054" in low or "connection reset" in low:
        return entry(code="TRANSPORT_CONNECTION_RESET", category="transport", message=text, retryable=True)
    if "connection refused" in low or "actively refused" in low or "could not connect" in low:
        return entry(code="TRANSPORT_CONNECTION_REFUSED", category="transport", message=text, retryable=True)

    return entry(code=fallback_code, category="unknown", message=text, retryable=False)


def _summary(exc: BaseException) -> str:
    """:func:`describe` without the attached backend output.

    The output (process ids, log lines) is context, not the failure: a log
    line saying "timeout" must not turn a license error into a TIMEOUT.
    """
    text = describe(exc)
    diagnostics = getattr(exc, "diagnostics", "") or ""
    if diagnostics:
        text = text.replace(diagnostics, "").strip()
    return text
