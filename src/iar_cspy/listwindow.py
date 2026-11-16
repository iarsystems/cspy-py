"""List windows (trace, and other tabular debugger views).

Two hosts implement these entirely differently, and which one answers
depends on what is registered:

* CSpyServer2 registers one ``ListWindowBackend`` (``listwindow.thrift``)
  per window, under its own name. A frontend must ``connect`` to it first,
  handing over the location of a ``ListWindowFrontend`` service for
  notifications; this process hosts that. Sliding windows, navigation and
  trace control all work.
* An IDE registers one shared, read-only ``ListWindowSnapshot``
  (``listwindowsnapshot.thrift``) under ``listwindow.snapshot``, covering the
  windows it has open. No notifications, no sliding windows, no trace control.

Whenever ``listwindow.snapshot`` is registered it is the implementation used,
and calls it cannot serve raise :class:`~iar_cspy.errors.UnsupportedMethod`.
The two use different window ids (a registry service name vs. a model name
like ``"Watch"``), so take ids from :meth:`ListWindows.services` rather than
hardcoding them.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING, Any

from ._callbacks import CallbackServer
from ._thrift import coerce_call_args, to_plain
from .errors import CSpyError, UnsupportedMethod
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

    _SNAPSHOT_SERVICE = "listwindow.snapshot"
    # Answerable without the backend: the snapshot interface never reports a sliding
    # window, and setVisibleRows is a prefetch hint with no observable semantics.
    _SNAPSHOT_LOCAL_ANSWERS: dict[str, Any] = {"isSliding": False, "setVisibleRows": None}
    # Forwarded, with the window id prepended as the first argument.
    _SNAPSHOT_FORWARDED = frozenset({"getDisplayName", "getListSpec", "getColumnInfo", "getNumberOfRows", "getRow"})

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
        """The list windows this host offers, whose name contains ``name_filter``.

        Each entry is ``{name, host, port, protocol, transport, source, open}``,
        the same shape from either implementation. ``name`` is the id to pass to
        :meth:`window`.
        """
        endpoint = self._snapshot_endpoint()
        if endpoint is None:
            entries = self._client.registry.services(name_filter)
            return [{**entry, "source": "backend", "open": True} for entry in entries]
        return self._snapshot_services(endpoint, name_filter)

    def read_only(self) -> bool:
        """Whether this host serves list windows read-only.

        True when the host offers only the shared ``listwindow.snapshot``
        service (an IDE): rows and columns can be read, but trace control and
        sliding-window navigation raise
        :class:`~iar_cspy.errors.UnsupportedMethod`.
        """
        return self._snapshot_endpoint() is not None

    def _snapshot_endpoint(self) -> tuple[str, int] | None:
        entry = self._client.registry.lookup(self._SNAPSHOT_SERVICE)
        if entry is None or int(entry.get("transport", -1)) != 0 or int(entry.get("port", 0) or 0) <= 0:
            return None
        return str(entry["host"]), int(entry["port"])

    def _snapshot_services(self, endpoint: tuple[str, int], name_filter: str) -> list[dict[str, Any]]:
        client = self._client
        service_cls = client.idl.service("listwindowsnapshot.thrift", "ListWindowSnapshot")
        host, port = endpoint
        conn = client._connect(service_cls, host, port, self._SNAPSHOT_SERVICE)
        try:
            open_windows = {str(w) for w in client.traced(self._SNAPSHOT_SERVICE, "getOpenWindows", conn.getOpenWindows)}
            try:
                available = {
                    str(w) for w in client.traced(self._SNAPSHOT_SERVICE, "getAvailableWindows", conn.getAvailableWindows)
                }
            except Exception:  # noqa: BLE001
                available = set()
            windows = open_windows | available
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

        query = name_filter.strip().lower()
        # The default is a substring of every ListWindowBackend registry name, but not of
        # snapshot ids, which are model names like "Watch". Treat it as "no filter".
        if query == "listwindow":
            query = ""
        return [
            {
                "name": w,
                "host": host,
                "port": port,
                "protocol": 0,
                "transport": 0,
                "source": "snapshot",
                "open": w in open_windows,
            }
            for w in sorted(windows)
            if not query or query in w.lower()
        ]

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

    def _call_snapshot(
        self, endpoint: tuple[str, int], window_id: str, method: str, args: tuple[Any, ...], trace: bool
    ) -> Any:
        if trace or not (method in self._SNAPSHOT_LOCAL_ANSWERS or method in self._SNAPSHOT_FORWARDED):
            raise UnsupportedMethod(
                f"'{method}' is not available for list window '{window_id}': this host serves list windows "
                "read-only through the listwindow snapshot service, which has no trace control and no "
                "sliding windows.",
                method,
            )
        if method in self._SNAPSHOT_LOCAL_ANSWERS:
            return self._SNAPSHOT_LOCAL_ANSWERS[method]

        client = self._client
        service_cls = client.idl.service("listwindowsnapshot.thrift", "ListWindowSnapshot")
        host, port = endpoint
        conn = client._connect(service_cls, host, port, self._SNAPSHOT_SERVICE)
        try:
            open_windows = [str(w) for w in client.traced(self._SNAPSHOT_SERVICE, "getOpenWindows", conn.getOpenWindows)]
            resolved = next(
                (w for w in open_windows if w == window_id),
                next((w for w in open_windows if w.lower() == window_id.lower()), None),
            )
            if resolved is None:
                available = [
                    str(w) for w in client.traced(self._SNAPSHOT_SERVICE, "getAvailableWindows", conn.getAvailableWindows)
                ]
                resolved = next((w for w in available if w.lower() == window_id.lower()), None)
                if resolved is not None:
                    client.traced(self._SNAPSHOT_SERVICE, "showWindow", conn.showWindow, resolved, True, False)
            if resolved is None:
                raise CSpyError(
                    f"List window '{window_id}' is not open in the IDE. "
                    f"Open windows: {', '.join(open_windows) or '<none>'}."
                )
            return client.traced(f"{self._SNAPSHOT_SERVICE}[{resolved}]", method, getattr(conn, method), resolved, *args)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def call(self, service_name: str, method: str, *args: Any, trace: bool = False) -> Any:
        """Call a list window method, on whichever implementation this host offers."""
        # Before the frontend server below: a snapshot host never calls back.
        endpoint = self._snapshot_endpoint()
        if endpoint is not None:
            return self._call_snapshot(endpoint, service_name, method, args, trace)

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
