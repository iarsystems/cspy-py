"""List windows (trace, and other tabular debugger views) over ``listwindow.thrift``.

A list window backend is a service registered under its own name. Before
use, a frontend must ``connect`` to it, handing over the location of a
``ListWindowFrontend`` service for notifications; this process hosts that.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING, Any

from ._callbacks import CallbackServer
from ._thrift import coerce_call_args, to_plain
from .errors import CSpyError
from .types import ListWindowChunk, ListWindowOverview, ListWindowRows, TraceStatus

if TYPE_CHECKING:
    from .client import Client


class _ListWindowFrontendHandler:
    def __init__(self, windows: ListWindows) -> None:
        self._windows = windows

    def notify(self, note: Any) -> None:
        with self._windows._lock:
            self._windows._notes.append(to_plain(note))

    def notifyToolbar(self, note: Any) -> None:
        with self._windows._lock:
            self._windows._toolbar_notes.append(to_plain(note))


class ListWindows:
    """Discovery of list windows and their notifications (``client.listwindows``)."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._connected: set[str] = set()
        self._notes: deque[dict[str, Any]] = deque(maxlen=500)
        self._toolbar_notes: deque[dict[str, Any]] = deque(maxlen=500)
        self.server = CallbackServer(
            "listwindow-frontend", _ListWindowFrontendHandler(self), client.config.listwindow_client_timeout_ms
        )

    def services(self, name_filter: str = "listwindow") -> list[dict[str, Any]]:
        """Registry entries whose name contains ``name_filter``."""
        return self._client.registry.services(name_filter)

    def window(self, service_name: str) -> ListWindow:
        return ListWindow(self, service_name)

    def trace(self, service_name: str) -> TraceWindow:
        return TraceWindow(self, service_name)

    def notifications(self, clear: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(notes, toolbar notes) received from list window backends."""
        with self._lock:
            notes, toolbar = list(self._notes), list(self._toolbar_notes)
            if clear:
                self._notes.clear()
                self._toolbar_notes.clear()
        return notes, toolbar

    def clear_notifications(self) -> None:
        self.notifications(clear=True)

    def forget_connections(self) -> None:
        """Connections are per session; reconnect after the next configure."""
        with self._lock:
            self._connected.clear()

    def is_connected(self, service_name: str) -> bool:
        with self._lock:
            return service_name in self._connected

    def call(self, service_name: str, method: str, *args: Any, trace: bool = False) -> Any:
        """Call a ``ListWindowBackend`` (or with ``trace``, ``TraceListWindowBackend``) method."""
        client = self._client
        mod = client.idl.module("listwindow.thrift")
        service_cls = client.idl.service("listwindow.thrift", "TraceListWindowBackend" if trace else "ListWindowBackend")
        args, _ = coerce_call_args(service_cls, method, args, {})
        frontend_host, frontend_port = self.server.ensure(client.idl.service("listwindow.thrift", "ListWindowFrontend"))

        kind = "Trace listwindow" if trace else "Listwindow"
        try:
            entry = client.registry.lookup(service_name)
        except Exception:
            if trace:
                raise
            entry = None
        if entry is not None:
            if int(entry.get("transport", -1)) != 0:
                raise CSpyError(f"{kind} service '{service_name}' uses non-socket transport={entry.get('transport')}")
            host, port = str(entry.get("host", "")), int(entry.get("port", 0) or 0)
        elif trace:
            raise CSpyError(f"Trace listwindow service not found in registry: {service_name}")
        else:
            host, port = "", 0
        if not host or port <= 0:
            if trace:
                raise CSpyError(f"Trace listwindow service has invalid endpoint: {service_name}")
            host, port = client.registry.resolve(service_name)

        def connect(conn: Any) -> None:
            if self.is_connected(service_name):
                return
            listener = mod.ServiceRegistry.ServiceLocation(
                host=frontend_host,
                port=frontend_port,
                protocol=mod.ServiceRegistry.Protocol.Binary,
                transport=mod.ServiceRegistry.Transport.Socket,
            )
            client.traced("trace_listwindow" if trace else "listwindow", "connect", conn.connect, listener)
            with self._lock:
                self._connected.add(service_name)

        label = f"{'trace_listwindow' if trace else 'listwindow'}[{service_name}]"
        return client.invoke_at(service_cls, host, port, label, method, *args, before=connect)


class ListWindow:
    """One list window backend."""

    def __init__(self, windows: ListWindows, service_name: str) -> None:
        self._windows = windows
        self.service_name = service_name

    def call(self, method: str, *args: Any) -> Any:
        return self._windows.call(self.service_name, method, *args)

    def is_sliding(self) -> bool:
        """Sliding windows (e.g. trace) hold a movable chunk of a larger list."""
        return bool(self.call("isSliding"))

    def row_count(self) -> int:
        if self.is_sliding():
            return int(to_plain(self.call("getChunkInfo")).get("numberOfRows", 0) or 0)
        return int(self.call("getNumberOfRows"))

    def overview(self) -> ListWindowOverview:
        """Display name, columns, list spec and row count."""
        display_name = str(self.call("getDisplayName"))
        columns = to_plain(self.call("getColumnInfo"))
        spec = to_plain(self.call("getListSpec"))
        is_sliding = self.is_sliding()
        if is_sliding:
            chunk = to_plain(self.call("getChunkInfo"))
            n_rows = int(chunk.get("numberOfRows", 0) or 0)
        else:
            chunk = None
            n_rows = int(self.call("getNumberOfRows"))
        return ListWindowOverview(
            display_name=display_name,
            is_sliding=is_sliding,
            row_count=n_rows,
            chunk_info=chunk,
            columns=columns,
            list_spec=spec,
        )

    def rows(self, first_row: int = 0, count: int | None = None) -> ListWindowRows:
        """Read ``count`` rows (default: all the rest) starting at ``first_row``.

        A sliding window reporting no rows is navigated first, since it only
        materializes rows for an explicitly requested chunk; for a sliding
        window "all" means the rows of the current chunk.
        """
        start = max(0, int(first_row))

        is_sliding = self.is_sliding()
        if is_sliding:
            chunk_info = to_plain(self.call("getChunkInfo"))
            row_count = int(chunk_info.get("numberOfRows", 0) or 0)
            if row_count == 0:
                self.call("navigateToFraction", 0.5, 0, int(max(100, count or 0)))
                chunk_info = to_plain(self.call("getChunkInfo"))
                row_count = int(chunk_info.get("numberOfRows", 0) or 0)
        else:
            chunk_info = None
            row_count = int(self.call("getNumberOfRows"))

        end = row_count if count is None else min(row_count, start + max(1, int(count)))
        if row_count > 0:
            self.call("setVisibleRows", int(start), int(max(start, end - 1)))

        rows = [{"index": idx, "row": to_plain(self.call("getRow", int(idx)))} for idx in range(start, end)]
        return ListWindowRows(
            is_sliding=is_sliding, chunk_info=chunk_info, row_count=row_count, first_row=start, rows=rows
        )

    def navigate(self, fraction: float = 0.5, chunk_pos: int = 0, min_lines: int = 100) -> ListWindowChunk:
        """Move a sliding window's chunk to ``fraction`` (0..1) of the full list."""
        result = to_plain(self.call("navigateToFraction", float(fraction), int(chunk_pos), int(max(1, min_lines))))
        chunk = to_plain(self.call("getChunkInfo"))
        return ListWindowChunk(
            navigate_result=result, chunk_info=chunk, row_count=int(chunk.get("numberOfRows", 0) or 0)
        )


class TraceWindow:
    """The trace controls of a ``TraceListWindowBackend``."""

    def __init__(self, windows: ListWindows, service_name: str) -> None:
        self._windows = windows
        self.service_name = service_name

    def call(self, method: str, *args: Any) -> Any:
        return self._windows.call(self.service_name, method, *args, trace=True)

    def status(self) -> TraceStatus:
        return TraceStatus(
            is_enabled=bool(self.call("isEnabled")),
            can_enable=bool(self.call("canEnable")),
            can_clear=bool(self.call("canClear")),
            can_browse=bool(self.call("canBrowse")),
            is_browsing=bool(self.call("isBrowsing")),
            supports_trace_settings=bool(self.call("supportsTraceSettings")),
            progress=to_plain(self.call("getProgress")),
        )

    def set_enabled(self, enabled: bool = True) -> bool:
        """Enable or disable tracing; returns the resulting state."""
        self.call("setEnabled", bool(enabled))
        return bool(self.call("isEnabled"))

    def clear(self) -> None:
        self.call("clear")
