"""Breakpoints and watchpoints through the ``breakpoints`` service.

Breakpoints are placed on ULEs (Universal Location Expressions), parsed by the
debugger:

- expressions: ``main``, ``func+4``, ``*ptr``
- absolute: ``0x100``, ``Memory:0x42``
- source, full form (the reliable one): ``{/abs/path/file.c}.123.1``
- an optional size suffix: ``<ule>@<size>``

``main()`` may fail on some backends where ``main`` works, and ``file.c:123``
shorthand is backend-dependent.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Union

from ._session import requires_session
from ._thrift import to_plain
from .errors import CSpyError, with_context
from .types import AccessType

if TYPE_CHECKING:
    from .client import Client

#: A breakpoint id, or a ``shared.Breakpoint`` struct as the methods here return.
BreakpointLike = Union[int, Any]


def breakpoint_id(bp: BreakpointLike) -> int:
    """The id of a breakpoint given as an id or a ``Breakpoint`` struct."""
    if isinstance(bp, int):
        return bp
    value = getattr(bp, "id", None)
    if value is None and isinstance(bp, dict):
        value = bp.get("id")
    if value is None:
        raise CSpyError(f"Expected a breakpoint id or a Breakpoint, not {type(bp).__name__}")
    return int(value)


class Breakpoints:
    """Create, list and remove breakpoints (``client.breakpoints``).

    Breakpoints are ``shared.Breakpoint`` structs: ``id``, ``ule``,
    ``category``, ``descriptor``, ``enabled``, ``accessType``, ``valid``, ...
    Methods taking a breakpoint accept the struct or its id.
    """

    _api_name = "breakpoints"

    def __init__(self, client: Client) -> None:
        self._client = client

    def _call(self, method: str, *args: Any) -> Any:
        return self._client.call("breakpoints", method, *args)

    @requires_session
    def all(self) -> list[Any]:
        return self._call("getBreakpoints")

    @requires_session
    def get(self, bp: BreakpointLike) -> Any:
        return self._call("getBreakpoint", breakpoint_id(bp))

    @requires_session
    def add(self, ule: str, access: AccessType | int = AccessType.FETCH, *, category: str | None = None) -> Any:
        """Set a breakpoint (or with a data ``access``, a watchpoint) on ``ule``.

        ``category`` selects a breakpoint category explicitly; the backend may
        translate ids (``STD_CODE`` becomes ``STD_CODE2``). Raises CSpyError
        (a BackendError when the backend rejected it) if the location is not
        accepted.
        """
        try:
            if category is None:
                bp = self._call("setBreakpointOnUle", ule, int(access))
                hint = (
                    "Verify ULE syntax in backend format (for source ULE prefer "
                    "{path}.line.col), and ensure symbols/locations exist in the current image."
                )
            else:
                bp = self._call("setBreakpointOnUleWithCategory", ule, int(access), category)
                hint = (
                    "Category IDs may be translated by backend (for example STD_CODE -> STD_CODE2). "
                    "Verify category supports the requested access type."
                )
            return self._validated(bp, hint)
        except CSpyError as exc:
            raise with_context(
                exc,
                f"Setting a breakpoint on {ule!r} failed. Use a backend-valid ULE (see "
                f"iar_cspy.breakpoints for the forms). Backend error: {exc}",
            ) from exc

    @contextmanager
    def temporary(self, ule: str, access: AccessType | int = AccessType.FETCH) -> Iterator[Any]:
        """A breakpoint that is removed again when the ``with`` block ends."""
        bp = self.add(ule, access)
        try:
            yield bp
        finally:
            try:
                self.remove(bp)
            except CSpyError:
                pass  # the session may already be gone

    @requires_session
    def restore(self, descriptor: str) -> Any:
        """Recreate a breakpoint from a ``descriptor`` taken from :meth:`all`.

        Descriptors are opaque and backend-version dependent: round-trip them,
        do not construct them.
        """
        try:
            return self._validated(
                self._call("setBreakpointFromDescriptor", descriptor),
                "Use descriptor values from client.breakpoints.all(); free-form descriptor strings are not supported.",
            )
        except CSpyError as exc:
            raise with_context(
                exc,
                "Restoring a breakpoint from a descriptor failed. Use a descriptor from an earlier "
                "client.breakpoints.all() (round-trip). Free-form descriptor formats are not portable "
                f"across backend versions. Backend error: {exc}",
            ) from exc

    @requires_session
    def enable(self, bp: BreakpointLike, enabled: bool = True) -> bool:
        return bool(self._call("enableBreakpoint", breakpoint_id(bp), bool(enabled)))

    def disable(self, bp: BreakpointLike) -> bool:
        return self.enable(bp, False)

    @requires_session
    def remove(self, bp: BreakpointLike) -> bool:
        return bool(self._call("removeBreakpoint", breakpoint_id(bp)))

    def clear(self) -> None:
        """Remove every breakpoint."""
        for bp in self.all():
            self.remove(bp)

    @requires_session
    def recently_hit(self) -> list[Any]:
        return self._call("getRecentlyHitBreakpoints")

    def _validated(self, bp: Any, hint: str) -> Any:
        data = to_plain(bp)
        if not isinstance(data, dict):
            raise CSpyError(f"The backend returned an unexpected result type: {type(data).__name__}")
        is_valid = bool(data.get("valid", False))
        bp_id = int(data.get("id", 0) or 0)
        if not is_valid or bp_id <= 0:
            raise CSpyError(f"The backend returned an invalid breakpoint (id={bp_id}, valid={is_valid}). {hint}")
        # Refresh from the backend so fields like 'enabled' reflect post-create state.
        return self.get(bp_id)
