"""Code coverage through the ``CodeCoverage`` service (``codecoverage.thrift``).

CSpyServer2 registers the service (as ``codecoverage``) once the program is
loaded, and only when the session's driver supports code coverage metadata,
so it belongs to one debug session and can be missing altogether.

The report comes back as the backend's XML, unchanged::

    <cpMetaDataInfo>
      <program><name/><execCount/><totalCount/></program>
      <module><name/><execCount/><totalCount/>
        <function><name/><execCount/><totalCount/><sourceLoc file= startLine= .../>
          <step><name/><execCount/><totalCount/><sourceLoc .../></step>
        </function>
      </module>
    </cpMetaDataInfo>
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._session import requires_started_session
from .errors import BackendError, BackendUnavailableError, CSpyError, with_context

if TYPE_CHECKING:
    from .client import Client

CODECOVERAGE_SERVICE = "codecoverage"


class CodeCoverage:
    """Code coverage of the debugged program (``client.coverage``).

    >>> client.coverage.enable()
    >>> dbg.go_and_wait(timeout=10)
    >>> report = client.coverage.xml()
    """

    _api_name = "coverage"

    def __init__(self, client: Client) -> None:
        self._client = client

    def _call(self, method: str, *args: Any) -> Any:
        try:
            return self._client.call("codecoverage", method, *args)
        except (BackendUnavailableError, BackendError):
            raise
        except CSpyError as exc:
            if self._registered() is False:
                raise with_context(
                    exc,
                    f"Code coverage is not available: the backend has not registered the "
                    f"'{CODECOVERAGE_SERVICE}' service. CSpyServer2 registers it after the program "
                    f"is loaded, and only when the debugger driver supports code coverage. ({exc})",
                ) from exc
            raise

    def _registered(self) -> bool | None:
        """Whether ``codecoverage`` is in the registry; None if the registry can't tell."""
        try:
            return self._client.registry.lookup(CODECOVERAGE_SERVICE) is not None
        except Exception:  # noqa: BLE001 - only refines the message of an error already raised
            return None

    # -- state ---------------------------------------------------------------

    @requires_started_session
    def has_metadata_support(self) -> bool:
        """Whether the session's driver supports code coverage."""
        return bool(self._call("hasMetaDataSupport"))

    @requires_started_session
    def is_enabled(self) -> bool:
        return bool(self._call("isEnabled"))

    @requires_started_session
    def enable(self) -> None:
        """Start collecting coverage.

        The backend persists the setting with the session's other settings
        (the ``.dnx`` file), so the next session of the program starts with
        coverage on as well.
        """
        self._set_enabled(True)

    @requires_started_session
    def disable(self) -> None:
        """Stop collecting coverage; what was collected so far is kept."""
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        # enable() reports failure when it switches coverage on from off, though
        # it does switch it on (docs/backend-notes.md): judge by the outcome.
        if not self._call("enable", on) and bool(self._call("isEnabled")) != on:
            raise CSpyError(f"The backend failed to {'enable' if on else 'disable'} code coverage.")

    @requires_started_session
    def session_id(self) -> int:
        """An id of this coverage service instance, to tell reports of different sessions apart."""
        return int(self._call("getSessionId"))

    # -- metadata --------------------------------------------------------------

    @requires_started_session
    def is_initialized(self) -> bool:
        """Whether :meth:`initialize` has run; :meth:`xml` returns ``""`` before it has."""
        return bool(self._call("isInitialized"))

    @requires_started_session
    def initialize(self) -> None:
        """Build the program structure (modules, functions, steps) the report is made of."""
        self._call("initializeMetaData")

    @requires_started_session
    def refresh(self) -> None:
        """Update the execution counts from the target."""
        self._call("refreshMetaData")

    @requires_started_session
    def reset(self) -> None:
        """Discard the coverage collected so far."""
        if not self._call("reinitMetaData"):
            raise CSpyError("The backend failed to reset the code coverage data.")

    @requires_started_session
    def clear_cache(self) -> None:
        """Drop the backend's cached coverage data."""
        self._call("clearCachedData")

    # -- report ------------------------------------------------------------------

    @requires_started_session
    def xml(self, *, refresh: bool = True) -> str:
        """The coverage report as the backend's XML (see the module docstring).

        Runs :meth:`initialize` first if that has not happened yet, otherwise
        :meth:`refresh` unless ``refresh=False``.
        """
        if not self._call("isInitialized"):
            self._call("initializeMetaData")
        elif refresh:
            self._call("refreshMetaData")
        return self._call("getXMLData") or ""
