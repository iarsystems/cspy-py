"""Breakpoints and watchpoints through the ``breakpoints`` service.

Breakpoints are placed on ULEs (Universal Location Expressions), parsed by the
debugger:

- expressions: ``main``, ``func+4``, ``*ptr``
- absolute: ``0x100``, ``Memory:0x42``
- source, full form (the reliable one): ``{/abs/path/file.c}.123.1``
- an optional size suffix: ``<ule>@<size>``

``main()`` may fail on some backends where ``main`` works, and ``file.c:123``
shorthand is backend-dependent.

:meth:`Breakpoints.add_source`, :meth:`~Breakpoints.add_code`,
:meth:`~Breakpoints.add_data` and :meth:`~Breakpoints.add_log` build the ULE
from a location and pick the breakpoint category the session's driver uses
(e.g. ``EMUL_CODE`` on I-jet, ``STD_CODE2`` on the simulator), as the IDE does.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Iterator, Sequence, Union

from . import _drivers
from ._session import requires_session
from ._thrift import to_plain
from .errors import CSpyError, with_context
from .types import AccessType, CodeBreakpointMode

if TYPE_CHECKING:
    from .client import Client

#: A breakpoint id, or a ``shared.Breakpoint`` struct as the methods here return.
BreakpointLike = Union[int, Any]


@dataclass(frozen=True)
class SourceLocation:
    """A position in a source file; ``line`` and ``column`` count from 1.

    A relative ``file`` is made absolute against the current directory: the
    debugger matches it against the absolute paths in the debug information.
    """

    file: str | Path
    line: int
    column: int = 1

    @property
    def ule(self) -> str:
        path = str(self.file)
        if not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()):
            path = os.path.abspath(path)
        return f"{{{path}}}.{int(self.line)}.{int(self.column)}"


#: Where to break: a ULE or expression (``main``, ``g_state``), an address,
#: or a :class:`SourceLocation`.
Location = Union[str, int, SourceLocation]


def location_ule(location: Location) -> str:
    """The ULE for a location (an int is an address)."""
    if isinstance(location, SourceLocation):
        return location.ule
    if isinstance(location, bool):
        raise CSpyError("Expected a breakpoint location, not a bool")
    if isinstance(location, int):
        return hex(location)
    return str(location)


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
        self._driver: _drivers.Driver | None = None

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

    # -- typed breakpoints -------------------------------------------------------

    def use_driver(self, name: str, target: str = "", driver_options: Sequence[str] = ()) -> None:
        """Name the session's driver, for a session this client did not configure.

        After :meth:`Debugger.attach` the driver is unknown, and only the
        driver's default code breakpoints and data breakpoints can be set. ``name``
        is the launch.json driver name (``Simulator``, ``I-jet``, ``J-Link/J-Trace``,
        ...); some depend on ``target`` (``arm``, ``rx``, ...) and driver options.
        Forgotten when the session ends.
        """
        self._driver = _drivers.find_driver(name, target, driver_options) or _drivers.generic_driver(name)

    def forget_driver(self) -> None:
        self._driver = None

    def _session_driver(self) -> _drivers.Driver | None:
        if self._driver is not None:
            return self._driver
        config = self._client.debugger.launch_configuration
        if config is None:
            return None
        name = getattr(config, "driverNameOrEmpty", "") or ""
        library = getattr(config, "driverLib", "") or ""
        driver = _drivers.find_driver(
            name, getattr(config, "targetOrEmpty", "") or "", list(getattr(config, "driverOptions", None) or []), library
        )
        # Like the IDE, take a driver it does not know for a hardware probe.
        return driver or _drivers.generic_driver(name or library)

    def modes(self) -> list[CodeBreakpointMode]:
        """The code breakpoint modes the session's driver supports, its default first.

        Empty when the driver is unknown (see :meth:`use_driver`).
        """
        driver = self._session_driver()
        return list(driver.code) if driver else []

    @requires_session
    def add_code(self, location: Location, mode: CodeBreakpointMode | str | None = None) -> Any:
        """Set a code breakpoint at ``location``: an expression (``main``, ``func+4``), an address or a SourceLocation.

        ``mode`` picks the kind (hardware, software, trace start, ...) among
        those the driver supports (:meth:`modes`); the default is the driver's
        default. Raises CSpyError when the driver does not support the mode or
        the backend rejects the location.
        """
        ule = location_ule(location)
        if mode is not None:
            try:
                mode = CodeBreakpointMode(mode)
            except ValueError:
                names = ", ".join(m.value for m in CodeBreakpointMode)
                raise CSpyError(f"Unknown breakpoint mode {mode!r}; the modes are: {names}.") from None
        driver = self._session_driver()
        if driver is None:
            if mode not in (None, CodeBreakpointMode.AUTO):
                raise CSpyError(self._unknown_driver(f"a {mode.value} code breakpoint"))
            return self.add(ule)
        mode = mode or driver.default_mode
        factory = driver.code.get(mode)
        if factory is None:
            supported = ", ".join(m.value for m in driver.code)
            raise CSpyError(f"The {driver.name} driver does not support {mode.value} breakpoints; it supports: {supported}.")
        return self._set_from_fields(ule, factory(ule), f"a code breakpoint ({mode.value}, {driver.name} driver)")

    @requires_session
    def add_source(
        self, file: str | Path, line: int, column: int = 1, *, mode: CodeBreakpointMode | str | None = None
    ) -> Any:
        """Set a code breakpoint on a source line (``line`` and ``column`` count from 1).

        See :meth:`add_code` for ``mode``. The debugger may move the
        breakpoint to the nearest line with code: the result's ``ule`` says where.
        """
        return self.add_code(SourceLocation(file, line, column), mode)

    @requires_session
    def add_data(
        self, location: Location, access: AccessType | int = AccessType.READ_WRITE, *, size: int | None = None
    ) -> Any:
        """Set a data breakpoint (watchpoint) on a variable or address.

        ``location`` is an expression (``g_state``, ``buf[3]``) or an address;
        ``access`` is READ, WRITE or READ_WRITE. ``size`` (bytes) watches a
        range from the location instead of its type's size.
        """
        access = AccessType(int(access))
        if access is AccessType.FETCH:
            raise CSpyError("A data breakpoint needs READ, WRITE or READ_WRITE access; use add_code() for code.")
        ule = location_ule(location)
        if size is not None:
            ule = f"{ule}@{int(size)}"
        driver = self._session_driver()
        if driver is None:
            return self.add(ule, access)
        if driver.data is None:
            raise CSpyError(f"The {driver.name} driver does not support data breakpoints.")
        return self._set_from_fields(ule, driver.data(ule, access), f"a data breakpoint ({driver.name} driver)")

    @requires_session
    def add_log(self, location: Location, message: str) -> Any:
        """Set a log breakpoint: when execution reaches ``location``, log ``message`` and continue.

        Text in braces is evaluated when the breakpoint triggers:
        ``"count = {count}"``. The message is posted as a debug log event
        (see :meth:`Events.forward_log`).
        """
        ule = location_ule(location)
        driver = self._session_driver()
        log = driver.log if driver else _drivers.STD_LOG2
        return self._set_from_fields(ule, log(ule, message), "a log breakpoint")

    def _set_from_fields(self, ule: str, fields: list[Any], what: str) -> Any:
        try:
            return self._validated(
                self._call("setBreakpointFromDescriptor", _drivers.write_descriptor(fields)),
                "Verify that the location exists in the current image.",
            )
        except CSpyError as exc:
            raise with_context(
                exc, f"Setting {what} on {ule!r} failed. Use a backend-valid location. Backend error: {exc}"
            ) from exc

    @staticmethod
    def _unknown_driver(what: str) -> str:
        return (
            f"Setting {what} needs the session's driver, which is unknown: the session was not "
            "configured by this client. Name it with client.breakpoints.use_driver(...)."
        )

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
        """Remove every breakpoint.

        ``removeBreakpoint`` reports a breakpoint it could not remove by
        returning false, not by raising; this raises CSpyError naming those
        (after trying all of them).
        """
        kept = [bp for bp in self.all() if not self.remove(bp)]
        if kept:
            names = ", ".join(f"{breakpoint_id(bp)} ({(to_plain(bp) or {}).get('ule', '')})" for bp in kept)
            raise CSpyError(f"The backend did not remove these breakpoints: {names}")

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
