"""C-RUN through the ``CRunBackend`` and ``CRunDisplay`` services (``crun.thrift``).

:class:`Crun` adds rules, reads and sets the default (catch-all) rule's
action, and loads and saves a rule file.

``load_filters``/``save_filters`` resolve ``filename`` on the *backend's*
filesystem (CSpyServer2 or iaride), not this process's. That only holds when
the two share one - true for the managed/local setup this package otherwise
assumes throughout (program paths, project files, ...), not for a backend on a
different machine.

:class:`CRunMessages` hosts ``CRunDisplay``: the backend pushes the live
runtime-check messages to it, and it keeps them and hands them to subscribers.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from ._callbacks import CallbackServer
from ._process import is_reachable
from ._session import requires_started_session
from ._thrift import to_plain
from .errors import CSpyError
from .types import CRunBreakAction, CRunEvent, CRunMessage

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

CRUN_DISPLAY_SERVICE = "crun.display"

CRunListener = Callable[[str, CRunEvent], None]


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


def _int(value: Any, default: int = 0) -> int:
    return default if value is None else int(value)


def _message_from_thrift(message: Any) -> CRunMessage:
    """A :class:`CRunMessage` from the ``crun.CRunMessage`` struct the backend pushes.

    Tolerant of unset fields: this runs on the backend's callback thread, and
    an exception there would fail the backend's call.
    """
    return CRunMessage(
        id=_int(message.id),
        index=_int(message.index),
        core=_int(message.core),
        name=message.name or "",
        text=message.text or "",
        cycle=_int(message.cycle),
        repeat_count=_int(message.repeatCount),
        sub_messages=[_message_from_thrift(sub) for sub in message.subMessages or []],
        call_stack=list(message.callStack or []),
        no_stop=bool(message.noStop),
        run_to=to_plain(message.runTo),
        user_program_counter=to_plain(message.userProgramCounter),
        extra_source_ranges=to_plain(message.extraSourceRanges) or [],
        pc_source_range=to_plain(message.pcSourceRange),
        tooltip=message.tooltip or "",
        break_action=CRunBreakAction(_int(message.breakAction)),
    )


class _CRunDisplayHandler:
    def __init__(self, messages: CRunMessages) -> None:
        self._messages = messages

    def itemAdded(self, message: Any, partnerNamespace: str) -> None:
        converted = _message_from_thrift(message)
        self._messages._post(CRunEvent("item_added", partnerNamespace, converted, converted.index))

    def itemUpdated(self, message: Any, partnerNamespace: str) -> None:
        converted = _message_from_thrift(message)
        self._messages._post(CRunEvent("item_updated", partnerNamespace, converted, converted.index))

    def itemRemoved(self, index: int, partnerNamespace: str) -> None:
        self._messages._post(CRunEvent("item_removed", partnerNamespace, None, _int(index)))

    def updateAll(self, partnerNamespace: str) -> None:
        self._messages._post(CRunEvent("update_all", partnerNamespace))

    def filtersChanged(self, partnerNamespace: str) -> None:
        self._messages._post(CRunEvent("filters_changed", partnerNamespace))


class CRunMessages:
    """The live C-RUN runtime-check messages the backend pushes (``client.crun_messages``).

    Hosts ``CRunDisplay``. The pushes are available two ways, kept consistent
    under one lock:

    - as they arrive: :meth:`subscribe`, and a bounded log of the most recent
      pushes (:meth:`recent`, :meth:`mark`, :meth:`wait_for`), as on
      :class:`~iar_cspy.events.Events`;
    - as what is stored so far: :meth:`messages`, :meth:`all_messages`,
      :meth:`namespaces`. Messages are kept per ``partnerNamespace``, by
      ``index``, in the order they were first added.

    Listeners run on the backend's callback thread and must not block.

    ``updateAll`` carries no data: it drops that namespace's stored messages,
    and the pushes that follow fill it again, so until they arrive it can hold
    fewer messages than the backend's list.
    """

    def __init__(self, client: Client, maxlen: int = 1000) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._posted = threading.Condition(self._lock)
        self._recent: deque[tuple[int, str, CRunEvent]] = deque(maxlen=maxlen)
        self._seq = 0
        self._listeners: list[CRunListener] = []
        self._by_namespace: dict[str, dict[int, CRunMessage]] = {}
        #: Whether the registered ``crun.display`` is this client's,
        #: i.e. whether messages reach it at all.
        self.receiving = False
        self.server = CallbackServer(
            "crun-display", _CRunDisplayHandler(self), client.config.crun_display_client_timeout_ms
        )

    def subscribe(self, listener: CRunListener) -> Callable[[], None]:
        """Call ``listener(kind, event)`` for every push; returns an unsubscribe function."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def recent(self, clear: bool = False) -> list[tuple[str, CRunEvent]]:
        """Buffered (kind, event) pairs, oldest first."""
        with self._lock:
            out = [(kind, event) for _, kind, event in self._recent]
            if clear:
                self._recent.clear()
            return out

    def mark(self) -> int:
        """A position in the stream of pushes, for :meth:`wait_for`."""
        with self._lock:
            return self._seq

    def wait_for(
        self, predicate: Callable[[str, CRunEvent], bool], *, after: int, timeout: float
    ) -> tuple[str, CRunEvent] | None:
        """The first push after ``after`` that matches, or None on timeout."""
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

    def messages(self, namespace: str) -> list[CRunMessage]:
        """The stored messages of ``namespace``, in the order they were added."""
        with self._lock:
            return list(self._by_namespace.get(namespace, {}).values())

    def namespaces(self) -> list[str]:
        """The namespaces that have pushed anything since the last :meth:`clear`."""
        with self._lock:
            return sorted(self._by_namespace)

    def all_messages(self) -> dict[str, list[CRunMessage]]:
        with self._lock:
            return {namespace: list(stored.values()) for namespace, stored in self._by_namespace.items()}

    def clear(self, namespace: str | None = None) -> None:
        """Forget the stored messages of ``namespace``, or of every namespace."""
        with self._lock:
            if namespace is None:
                self._by_namespace.clear()
            else:
                self._by_namespace.pop(namespace, None)

    def ensure_registered(self) -> dict[str, Any]:
        """Make sure ``crun.display`` is in the registry.

        An existing, reachable registration (e.g. an IDE's C-RUN Messages
        window) is left alone, and then nothing reaches this client.
        """
        if not self._client.config.auto_crun_display:
            self.receiving = False
            return {"ok": False, "reason": "auto-crun-display-disabled"}
        if self._client.backend.registry_endpoint() is None:
            self.receiving = False
            return {"ok": False, "reason": "no-registry-port"}

        with self.server.lock:
            existing = self._client.registry.lookup(CRUN_DISPLAY_SERVICE)
            if existing is not None and is_reachable(existing["host"], existing["port"], timeout_s=0.5):
                self.receiving = (existing["host"], existing["port"]) == (self.server.host, self.server.port)
                return {
                    "ok": True,
                    "status": "already-registered",
                    "host": existing["host"],
                    "port": existing["port"],
                }

            host, port = self.server.ensure(self._client.idl.service("crun.thrift", "CRunDisplay"))
            self._client.registry.register(CRUN_DISPLAY_SERVICE, host, port)
            self.receiving = True
            return {"ok": True, "status": "registered", "host": host, "port": port}

    def _post(self, event: CRunEvent) -> None:
        with self._posted:
            self._apply(event)
            self._seq += 1
            self._recent.append((self._seq, event.kind, event))
            self._posted.notify_all()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event.kind, event)
            except Exception:  # noqa: BLE001
                log.exception("C-RUN message listener failed")

    def _apply(self, event: CRunEvent) -> None:
        if event.kind in ("item_added", "item_updated") and event.message is not None:
            self._by_namespace.setdefault(event.namespace, {})[event.message.index] = event.message
        elif event.kind == "item_removed":
            self._by_namespace.get(event.namespace, {}).pop(event.index, None)
        elif event.kind == "update_all":
            self._by_namespace[event.namespace] = {}
