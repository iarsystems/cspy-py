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

from typing import TYPE_CHECKING, Any

from ._thrift import to_plain
from .errors import CSpyError
from .types import AccessType

if TYPE_CHECKING:
    from .client import Client


class Breakpoints:
    """Create, list and remove breakpoints (``client.breakpoints``).

    Breakpoints are ``shared.Breakpoint`` structs: ``id``, ``ule``,
    ``category``, ``descriptor``, ``enabled``, ``accessType``, ``valid``, ...
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def _call(self, method: str, *args: Any) -> Any:
        return self._client.call("breakpoints", method, *args)

    def all(self) -> list[Any]:
        return self._call("getBreakpoints")

    def get(self, id: int) -> Any:
        return self._call("getBreakpoint", int(id))

    def add(self, ule: str, access: AccessType | int = AccessType.FETCH, *, category: str | None = None) -> Any:
        """Set a breakpoint (or with a data ``access``, a watchpoint) on ``ule``.

        ``category`` selects a breakpoint category explicitly; the backend may
        translate ids (``STD_CODE`` becomes ``STD_CODE2``). Raises CSpyError if
        the backend rejects the location.
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
            return self._validated("setBreakpointOnUle" if category is None else "setBreakpointOnUleWithCategory", bp, hint)
        except CSpyError as exc:
            raise CSpyError(
                f"Setting a breakpoint on {ule!r} failed. Ensure a configured and started debug "
                "session and a backend-valid ULE (see iar_cspy.breakpoints for the forms). "
                f"Backend error: {exc}"
            ) from exc

    def restore(self, descriptor: str) -> Any:
        """Recreate a breakpoint from a ``descriptor`` taken from :meth:`all`.

        Descriptors are opaque and backend-version dependent: round-trip them,
        do not construct them.
        """
        try:
            return self._validated(
                "setBreakpointFromDescriptor",
                self._call("setBreakpointFromDescriptor", descriptor),
                "Use descriptor values returned by getBreakpoints(); free-form descriptor strings are not supported.",
            )
        except CSpyError as exc:
            raise CSpyError(
                "setBreakpointFromDescriptor failed. Use descriptor values returned by an earlier "
                "listing (round-trip). Free-form descriptor formats are not portable across "
                f"backend versions. Backend error: {exc}"
            ) from exc

    def enable(self, id: int, enabled: bool = True) -> bool:
        return bool(self._call("enableBreakpoint", int(id), bool(enabled)))

    def disable(self, id: int) -> bool:
        return self.enable(id, False)

    def remove(self, id: int) -> bool:
        return bool(self._call("removeBreakpoint", int(id)))

    def clear(self) -> None:
        """Remove every breakpoint."""
        for bp in self.all():
            self.remove(to_plain(bp)["id"])

    def recently_hit(self) -> list[Any]:
        return self._call("getRecentlyHitBreakpoints")

    def _validated(self, op_name: str, bp: Any, hint: str) -> Any:
        data = to_plain(bp)
        if not isinstance(data, dict):
            raise CSpyError(f"{op_name} returned unexpected result type: {type(data).__name__}")
        is_valid = bool(data.get("valid", False))
        bp_id = int(data.get("id", 0) or 0)
        if not is_valid or bp_id <= 0:
            raise CSpyError(f"{op_name} returned an invalid breakpoint (id={bp_id}, valid={is_valid}). {hint}")
        # Refresh from the backend so fields like 'enabled' reflect post-create state.
        return self.get(bp_id)
