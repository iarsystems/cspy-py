"""C-RUN runtime-checking rules through the ``CRunBackend`` service (``crun.thrift``).

:class:`Crun` adds rules, reads and sets the default (catch-all) rule's
action, and loads and saves a rule file.

``load_filters``/``save_filters`` resolve ``filename`` on the *backend's*
filesystem (CSpyServer2 or iaride), not this process's. That only holds when
the two share one - true for the managed/local setup this package otherwise
assumes throughout (program paths, project files, ...), not for a backend on a
different machine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._session import requires_started_session
from .errors import CSpyError
from .types import CRunBreakAction

if TYPE_CHECKING:
    from .client import Client


def _action_value(action: CRunBreakAction | str | int) -> int:
    """Coerce an action (enum, member name, or raw int) to its int value."""
    if isinstance(action, CRunBreakAction):
        return int(action)
    if isinstance(action, str):
        try:
            return int(CRunBreakAction[action])
        except KeyError:
            names = ", ".join(a.name for a in CRunBreakAction)
            raise CSpyError(f"Unknown C-RUN break action {action!r}; valid: {names}") from None
    return int(action)


class Crun:
    """C-RUN runtime-checking rules (``client.crun_rules``)."""

    _api_name = "crun"

    def __init__(self, client: Client) -> None:
        self._client = client

    def _call(self, method: str, *args: Any) -> Any:
        return self._client.call("crun", method, *args)

    # -- default action ------------------------------------------------------

    @requires_started_session
    def default_action(self) -> CRunBreakAction:
        return CRunBreakAction(self._call("getDefaultAction"))

    @requires_started_session
    def set_default_action(self, action: CRunBreakAction | str | int) -> None:
        self._call("setDefaultAction", _action_value(action))

    # -- rules ------------------------------------------------------------------

    @requires_started_session
    def add_rule(
        self,
        message_pattern: str,
        *,
        file: str | None = None,
        start_line: int | None = None,
        start_col: int | None = None,
        end_line: int | None = None,
        end_col: int | None = None,
    ) -> None:
        """Add a rule, ahead of the existing ones, for messages matching ``message_pattern``.

        With no location it matches anywhere; with ``file`` alone, anywhere
        in that file; with a line/col too (``file`` required), in that source
        range (``end_line``/``end_col`` default to ``start_line``/``start_col``,
        for a single-point rule). The backend gives the new rule the ``Log``
        action.
        """
        has_location = any(v is not None for v in (start_line, start_col, end_line, end_col))
        if file is None:
            if has_location:
                raise CSpyError(
                    "A C-RUN rule range needs 'file' (start_line/start_col/end_line/end_col alone is not enough)."
                )
            self._call("addRuleOnMessage", str(message_pattern))
        elif not has_location:
            self._call("addRuleOnMessageFile", str(message_pattern), str(file))
        else:
            first = {
                "filename": str(file),
                "line": -1 if start_line is None else int(start_line),
                "col": -1 if start_col is None else int(start_col),
            }
            last = {
                "filename": str(file),
                "line": first["line"] if end_line is None else int(end_line),
                "col": first["col"] if end_col is None else int(end_col),
            }
            self._call("addRuleOnMessageRange", str(message_pattern), {"first": first, "last": last})

    # -- rule files ---------------------------------------------------------------

    @requires_started_session
    def load_filters(self, filename: str) -> None:
        """Replace the whole rule set from an XML file on the *backend's* filesystem."""
        self._call("loadFilters", str(filename))

    @requires_started_session
    def save_filters(self, filename: str) -> None:
        """Write the whole rule set to an XML file on the *backend's* filesystem."""
        self._call("saveFilters", str(filename))
