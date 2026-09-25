"""Debug events the backend posts to ``debugger.eventhandler``."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from ._callbacks import CallbackServer
from ._process import is_reachable

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

EVENTHANDLER_SERVICE = "debugger.eventhandler"

#: ``DkNotifyConstant`` values of interest.
TARGET_STOPPED = 0
TARGET_STARTED = 1
INSPECTION_CONTEXT_CHANGED = 4
FATAL_ERROR = 13
SILENT_FATAL_ERROR = 19
#: A fatal error, reported to the user or not: the debug session is over.
FATAL_ERROR_NOTES = frozenset({FATAL_ERROR, SILENT_FATAL_ERROR})

#: How long, in seconds, a stop waits for the backend to move its inspection
#: context to the new location (see :meth:`Events.wait_for_stop`).
STOP_SETTLE_TIMEOUT = 0.5

Listener = Callable[[str, Any], None]


class _DebugEventListenerHandler:
    def __init__(self, events: Events) -> None:
        self._events = events

    def postDebugEvent(self, event: Any) -> None:
        self._events._post("debug", event)

    def postLogEvent(self, event: Any) -> None:
        self._events._post("log", event)

    def postInspectionContextChangedEvent(self, event: Any) -> None:
        self._events._post("inspection_context_changed", event)

    def postBaseContextChangedEvent(self, event: Any) -> None:
        self._events._post("base_context_changed", event)


class Events:
    """Receives debug/log events from the backend (``client.events``).

    Events are kept in a bounded buffer (:meth:`recent`) and passed to
    listeners registered with :meth:`subscribe`. Listeners run on the
    backend's callback thread and must not block.
    """

    def __init__(self, client: Client, maxlen: int = 1000) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._posted = threading.Condition(self._lock)
        self._recent: deque[tuple[int, str, Any]] = deque(maxlen=maxlen)
        self._seq = 0
        self._listeners: list[Listener] = []
        #: Whether the registered ``debugger.eventhandler`` is this client's,
        #: i.e. whether events reach it at all.
        self.receiving = False
        self.server = CallbackServer(
            "eventhandler", _DebugEventListenerHandler(self), client.config.eventhandler_client_timeout_ms
        )

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener(kind, event)`` for every event; returns an unsubscribe function.

        ``kind`` is ``debug``, ``log``, ``inspection_context_changed`` or
        ``base_context_changed``; ``event`` is the IDL struct.
        """
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def recent(self, clear: bool = False) -> list[tuple[str, Any]]:
        """Buffered (kind, event) pairs, oldest first."""
        with self._lock:
            out = [(kind, event) for _, kind, event in self._recent]
            if clear:
                self._recent.clear()
            return out

    def mark(self) -> int:
        """A position in the event stream, for :meth:`wait_for`."""
        with self._lock:
            return self._seq

    def wait_for(
        self, predicate: Callable[[str, Any], bool], *, after: int, timeout: float
    ) -> tuple[str, Any] | None:
        """The first event posted after ``after`` that matches, or None on timeout."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._posted:
            while True:
                for seq, kind, event in self._recent:
                    if seq > after and predicate(kind, event):
                        return kind, event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._posted.wait(remaining)

    def wait_for_stop(self, *, after: int, timeout: float, settle: float = STOP_SETTLE_TIMEOUT) -> Any | None:
        """The first target-stopped event that follows a target-started one, both after ``after``.

        A stop event posted before the target started running again belongs
        to an earlier halt or step, so it does not count. Returns the event,
        or None on timeout. A fatal error ends the wait early: its event is
        returned instead (see :data:`FATAL_ERROR_NOTES`).

        The backend posts the stop before it has moved its inspection context
        (and the variable windows) to the new location, so this then waits
        up to ``settle`` seconds more for the inspection-context-changed event
        that follows; values read right after the stop could be stale.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        with self._posted:
            while True:
                started = False
                stop: tuple[int, Any] | None = None
                for seq, kind, event in self._recent:
                    if seq <= after or kind != "debug":
                        continue
                    note = getattr(event, "note", None)
                    if note in FATAL_ERROR_NOTES:
                        return event
                    if note == TARGET_STARTED:
                        started = True
                    elif note == TARGET_STOPPED and started:
                        stop = (seq, event)
                        break
                if stop is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._posted.wait(remaining)

            stop_seq, stop_event = stop
            settled_by = time.monotonic() + max(0.0, settle)
            while True:
                for seq, kind, event in self._recent:
                    if seq > stop_seq and _is_inspection_change(kind, event):
                        return stop_event
                remaining = settled_by - time.monotonic()
                if remaining <= 0:
                    return stop_event
                self._posted.wait(remaining)

    def _post(self, kind: str, event: Any) -> None:
        with self._posted:
            self._seq += 1
            self._recent.append((self._seq, kind, event))
            self._posted.notify_all()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(kind, event)
            except Exception:  # noqa: BLE001
                log.exception("debug event listener failed")

    def ensure_registered(self) -> dict[str, Any]:
        """Make sure ``debugger.eventhandler`` is in the registry.

        Some backends fail configure/start without a registered debug-event
        listener. An existing, reachable registration (e.g. an IDE's) is left
        alone.
        """
        cfg = self._client.config
        if not cfg.auto_eventhandler:
            self.receiving = False
            return {"ok": False, "reason": "auto-eventhandler-disabled"}
        if self._client.backend.registry_endpoint() is None:
            self.receiving = False
            return {"ok": False, "reason": "no-registry-port"}

        with self.server.lock:
            existing = self._client.registry.lookup(EVENTHANDLER_SERVICE)
            if existing is not None and is_reachable(existing["host"], existing["port"], timeout_s=0.5):
                self.receiving = (existing["host"], existing["port"]) == (self.server.host, self.server.port)
                return {
                    "ok": True,
                    "status": "already-registered",
                    "host": existing["host"],
                    "port": existing["port"],
                }

            host, port = self.server.ensure(self._client.idl.service(None, "DebugEventListener"))
            self._client.registry.register(EVENTHANDLER_SERVICE, host, port)
            self.receiving = True
            return {"ok": True, "status": "registered", "host": host, "port": port}


def _is_inspection_change(kind: str, event: Any) -> bool:
    if kind == "inspection_context_changed":
        return True
    return kind == "debug" and getattr(event, "note", None) == INSPECTION_CONTEXT_CHANGED
