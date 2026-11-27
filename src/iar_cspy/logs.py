"""The IDE's logs (Build, Debug Log, Tool Output, ...): ``client.logs``.

The IDE platform's log forwarder (``ThriftLogForwarder``) posts everything
the IDE logs to the ``LogService`` registered in the service registry under
``LOGSERVICE_ID``, and newer forwarders also to every one registered as
``LOGSERVICE_ID.<suffix>``. This module hosts one and stores what arrives so
it can be read back at any time: :meth:`Logs.entries`, :meth:`Logs.text`,
:meth:`Logs.problems`.

Categories are the IDE's log ids: ``Log_Build``, ``Log_Debug``,
``Log_Tool_Output``, ``Log_Find_in_Files``, ``Log_CMake_CMSIS``, ...

The forwarder delivers entries from the moment a service registers; nothing
logged before that is replayed. So the service is registered as early as
this client has a registry: before the first ProjectManager/OptionsService
call, on configure/start, or by :meth:`Logs.ensure_registered`.

A Thrift-enabled iaride runs a forwarder while remote control is on. With a
managed IarServiceLauncher, this module loads one into it when the
installation ships the library.
"""

from __future__ import annotations

import bisect
import itertools
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Literal

from ._callbacks import CallbackServer
from ._process import is_reachable
from .errors import CSpyError
from .ide_services import LOG_FORWARDER, resolve_manifest
from .types import LogCategorySummary, LogEntry, LogPage, LogSeverity, LogSummary

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

#: ``LOGSERVICE_ID``: every forwarder delivers to this name; newer ones also
#: to any name that extends it with a dot and a suffix.
LOGSERVICE_ID = "com.iar.thrift.service.logservice"

#: ``Backend.loaded_manifests`` key for the forwarder loaded into a managed launcher.
LOG_FORWARDER_KEY = "logforwarder"

#: Prefixes :meth:`Logs.text` gives entries by severity, as the IDE's windows mark them.
SEVERITY_PREFIXES = {
    LogSeverity.WARNING: "Warning: ",
    LogSeverity.ERROR: "Error: ",
    LogSeverity.ALERT: "Error: ",
    LogSeverity.SUPER: "Error: ",
}

Session = int | Literal["current"] | None
Severity = LogSeverity | str | int
Match = str | re.Pattern[str]
LogListener = Callable[[LogEntry], None]

_instance_numbers = itertools.count(1)


def _severity(value: LogSeverity | str | int) -> LogSeverity:
    if isinstance(value, str):
        try:
            return LogSeverity[value.strip().upper()]
        except KeyError:
            names = ", ".join(s.name for s in LogSeverity)
            raise ValueError(f"Unknown log severity {value!r}; known: {names}") from None
    return LogSeverity(int(value))


def _position(pos: Any) -> tuple[int | None, int | None]:
    if pos is None or not getattr(pos, "valid", False):
        return None, None
    return int(getattr(pos, "row", 0) or 0), int(getattr(pos, "col", 0) or 0)


def _entry_from_thrift(entry: Any, session: int, seq: int) -> LogEntry:
    try:
        severity = LogSeverity(int(getattr(entry, "severity", LogSeverity.INFO)))
    except (TypeError, ValueError):
        severity = LogSeverity.INFO
    line, column = _position(getattr(entry, "srcPos", None))
    end_line, end_column = _position(getattr(entry, "srcEndPos", None))
    stamp = int(getattr(entry, "timestamp", 0) or 0)
    return LogEntry(
        category=str(getattr(entry, "category", "") or ""),
        text=str(getattr(entry, "text", "") or ""),
        severity=severity,
        sender=str(getattr(entry, "sender", "") or ""),
        path=str(getattr(entry, "path", "") or "") or None,
        line=line,
        column=column,
        end_line=end_line,
        end_column=end_column,
        # time_t; 0 means the IDE gave none.
        timestamp=datetime.fromtimestamp(stamp).astimezone() if stamp > 0 else None,
        entry_id=int(getattr(entry, "entryId", 0) or 0),
        is_sub_entry=bool(getattr(entry, "isSubEntry", False)),
        session=session,
        seq=seq,
    )


def _matcher(match: Match | None) -> Callable[[str], bool] | None:
    if match is None:
        return None
    if isinstance(match, re.Pattern):
        return lambda text: match.search(text) is not None
    needle = str(match).casefold()
    return lambda text: needle in text.casefold()


_ERRORS = (LogSeverity.ERROR, LogSeverity.ALERT, LogSeverity.SUPER)


@dataclass
class _Category:
    entries: deque[LogEntry]
    session: int = 0
    #: Whether the IDE has the category now (addCategory, not yet removeCategory).
    open: bool = False


class _LogServiceHandler:
    """The ``LogService`` the forwarder calls (``logservice.thrift``)."""

    def __init__(self, logs: Logs) -> None:
        self._logs = logs

    def isAlive(self) -> None:
        return None

    def GetServiceVersion(self) -> Any:
        mod = self._logs._module()
        return mod.shared.ServiceVersion(major=mod.LOGSERVICE_VERSION.major, minor=mod.LOGSERVICE_VERSION.minor)

    def addCategory(self, category: str) -> None:
        self._logs._add_category(str(category))

    def removeCategory(self, category: str) -> None:
        self._logs._remove_category(str(category))

    def startSession(self, category: str) -> None:
        self._logs._start_session(str(category))

    def postLogEntry(self, entry: Any) -> None:
        self._logs._post(entry)


class Logs:
    """What the IDE has logged since this client started listening (``client.logs``).

    Entries are stored per category, at most ``maxlen`` each, so a busy
    Debug Log does not push the build output out. Reading them::

        mark = client.logs.mark()
        client.project.build(...)
        for entry in client.logs.problems("Log_Build", after=mark):
            print(entry.path, entry.line, entry.text)

    ``session="current"`` narrows a category to what was logged since the
    IDE last restarted that log (a new build clears the Build window, say).
    Entries already stored are not affected by the IDE clearing its windows;
    :meth:`clear` forgets them here.

    Listeners added with :meth:`subscribe` run on the forwarder's callback
    thread and must not block.
    """

    def __init__(self, client: Client, maxlen: int = 5000) -> None:
        self._client = client
        self._maxlen = maxlen
        self._lock = threading.Lock()
        self._posted = threading.Condition(self._lock)
        self._categories: dict[str, _Category] = {}
        self._seq = 0
        self._listeners: list[LogListener] = []
        #: The registry name this client listens under, once registered:
        #: ``LOGSERVICE_ID`` if that was free, else :attr:`suffixed_name`.
        self.service_name: str | None = None
        #: The name used when another listener holds ``LOGSERVICE_ID``.
        self.suffixed_name = f"{LOGSERVICE_ID}.iar-cspy-{os.getpid()}-{next(_instance_numbers)}"
        #: The registry endpoint the service was last registered at, or None.
        self._registered_at: tuple[str, int] | None = None
        self._listening_since: datetime | None = None
        self._forwarder: dict[str, Any] | None = None
        self.server = CallbackServer("logservice", _LogServiceHandler(self), client.config.log_service_client_timeout_ms)

    # -- reading ------------------------------------------------------------------

    def categories(self) -> list[str]:
        """The logs the IDE has now (``Log_Build``, ``Log_Debug``, ...), in the order it added them."""
        with self._lock:
            return [name for name, category in self._categories.items() if category.open]

    def page(
        self,
        category: str | None = None,
        *,
        session: Session = None,
        min_severity: Severity | None = None,
        match: Match | None = None,
        after: int | None = None,
        before: int | None = None,
        limit: int | None = None,
    ) -> LogPage:
        """A page of stored entries, oldest first; of one category, or of all of them.

        Filters:

        - ``session``: a session number (see :attr:`LogEntry.session`) or
          ``"current"``, each category's latest.
        - ``min_severity``: drop less serious entries; a :class:`LogSeverity`,
          its name or value.
        - ``match``: on the entry's text; a string is a case-insensitive
          substring, a compiled pattern is ``search``-ed.

        An entry kept by ``min_severity``/``match`` keeps the sub-entries
        that directly follow it (its detail lines, usually less severe).

        Page bounds are ``seq`` positions (:attr:`LogEntry.seq`,
        :meth:`mark`), which stay valid as the log grows and old entries
        are dropped:

        - ``after``: entries with ``seq > after``, the first ``limit`` of them;
        - ``before``: entries with ``seq < before``, the last ``limit`` of them;
        - neither: the newest ``limit``. ``limit=None`` is no limit.

        With both ``after`` and ``before``, the first ``limit`` between them.
        """
        floor = None if min_severity is None else _severity(min_severity)
        matches = _matcher(match)
        filtered = floor is not None or matches is not None
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be >= 0, not {limit}")

        def wanted(entry: LogEntry) -> bool:
            return (floor is None or entry.severity >= floor) and (matches is None or matches(entry.text))

        with self._lock:
            if category is None:
                selected = list(self._categories.values())
            else:
                one = self._categories.get(category)
                selected = [] if one is None else [one]
            found: list[LogEntry] = []
            for stored in selected:
                number = stored.session if session == "current" else session
                parent_kept = False
                for entry in stored.entries:
                    if number is not None and entry.session != number:
                        continue
                    if entry.is_sub_entry:
                        keep = not filtered or parent_kept or wanted(entry)
                    else:
                        keep = parent_kept = not filtered or wanted(entry)
                    if keep:
                        found.append(entry)
        if len(selected) > 1:
            found.sort(key=lambda entry: entry.seq)

        seqs = [entry.seq for entry in found]
        lo = 0 if after is None else bisect.bisect_right(seqs, after)
        hi = len(found) if before is None else bisect.bisect_left(seqs, before)
        hi = max(lo, hi)
        if limit is not None:
            if after is not None:
                hi = min(hi, lo + limit)  # the first `limit` after the mark
            else:
                lo = max(lo, hi - limit)  # the last `limit` before `before`, or the tail
        entries = found[lo:hi]
        return LogPage(
            entries=entries,
            total=len(found),
            before=lo,
            after=len(found) - hi,
            first_seq=entries[0].seq if entries else None,
            last_seq=entries[-1].seq if entries else None,
        )

    def entries(
        self,
        category: str | None = None,
        *,
        session: Session = None,
        min_severity: Severity | None = None,
        match: Match | None = None,
        after: int | None = None,
        before: int | None = None,
        limit: int | None = None,
    ) -> list[LogEntry]:
        """The entries of :meth:`page`, without the counts."""
        return self.page(
            category, session=session, min_severity=min_severity, match=match, after=after, before=before, limit=limit
        ).entries

    def problems(
        self,
        category: str | None = None,
        *,
        session: Session = "current",
        match: Match | None = None,
        after: int | None = None,
        before: int | None = None,
        limit: int | None = None,
    ) -> list[LogEntry]:
        """Warnings and worse with their detail lines, by default of each category's current session."""
        return self.entries(
            category,
            session=session,
            min_severity=LogSeverity.WARNING,
            match=match,
            after=after,
            before=before,
            limit=limit,
        )

    def text(
        self,
        category: str | None = None,
        *,
        session: Session = None,
        min_severity: Severity | None = None,
        match: Match | None = None,
        after: int | None = None,
        before: int | None = None,
        limit: int | None = None,
    ) -> str:
        """The entries :meth:`page` selects, as text, one line per entry.

        Warnings and errors get a ``Warning:``/``Error:`` prefix, sub-entries
        are indented. Without ``category``, each line starts with its
        category in brackets.
        """
        lines = []
        selected = self.entries(
            category, session=session, min_severity=min_severity, match=match, after=after, before=before, limit=limit
        )
        for entry in selected:
            prefix = "  " if entry.is_sub_entry else ""
            prefix += "" if category is not None else f"[{entry.category}] "
            prefix += SEVERITY_PREFIXES.get(entry.severity, "")
            lines.append(prefix + entry.text.rstrip("\n"))
        return "\n".join(lines)

    def summary(self) -> LogSummary:
        """Per category: whether the IDE has it open, its current session, and what is stored."""
        with self._lock:
            categories = []
            for name, stored in self._categories.items():
                current = [entry for entry in stored.entries if entry.session == stored.session]
                categories.append(
                    LogCategorySummary(
                        name=name,
                        open=stored.open,
                        session=stored.session,
                        entries=len(stored.entries),
                        errors=sum(1 for entry in current if entry.severity in _ERRORS),
                        warnings=sum(1 for entry in current if entry.severity == LogSeverity.WARNING),
                        last_seq=stored.entries[-1].seq if stored.entries else None,
                    )
                )
            return LogSummary(mark=self._seq, categories=categories)

    def clear(self, category: str | None = None) -> None:
        """Forget the stored entries of ``category``, or of every category.

        The IDE's own logs are not touched, and the categories and their
        session numbers are kept.
        """
        with self._lock:
            for name, stored in self._categories.items():
                if category is None or name == category:
                    stored.entries.clear()

    # -- following ----------------------------------------------------------------

    def mark(self) -> int:
        """The ``seq`` of the newest entry so far (0 before any): for ``after=`` and :meth:`wait_for`."""
        with self._lock:
            return self._seq

    def subscribe(self, listener: LogListener) -> Callable[[], None]:
        """Call ``listener(entry)`` for every entry from now on; returns an unsubscribe function."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def wait_for(
        self, predicate: Callable[[LogEntry], bool], *, after: int, timeout: float
    ) -> LogEntry | None:
        """The first stored entry after the mark ``after`` that matches, or None on timeout."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._posted:
            while True:
                found = [
                    entry
                    for stored in self._categories.values()
                    for entry in stored.entries
                    if entry.seq > after and predicate(entry)
                ]
                if found:
                    return min(found, key=lambda entry: entry.seq)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._posted.wait(remaining)

    # -- registration -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Whether this client is listening, since when, and what it holds."""
        with self._lock:
            stored = sum(len(c.entries) for c in self._categories.values())
            categories = [name for name, c in self._categories.items() if c.open]
        return {
            "registered": self._registered_at is not None,
            "service_name": self.service_name,
            "listening_since": None if self._listening_since is None else self._listening_since.isoformat(),
            "entries": stored,
            "categories": categories,
            "forwarder": self._forwarder,
        }

    def ensure_registered(self) -> dict[str, Any]:
        """Register this client's ``LogService``, and in a managed launcher start a forwarder.

        Registering again is needed after the backend was replaced; this
        checks the registry and does it when the registration is missing.
        """
        if not self._client.config.auto_log_service:
            return {"ok": False, "reason": "auto-log-service-disabled"}
        endpoint = self._client.backend.registry_endpoint()
        if endpoint is None:
            return {"ok": False, "reason": "no-registry-port"}

        with self.server.lock:
            registry = self._client.registry
            existing = None if self.service_name is None else registry.lookup(self.service_name)
            ours = (self.server.host, self.server.port)
            if existing is not None and (existing["host"], existing["port"]) == ours and is_reachable(*ours, timeout_s=0.5):
                result: dict[str, Any] = {"ok": True, "status": "already-registered", "host": ours[0], "port": ours[1]}
            else:
                host, port = self.server.ensure(self._client.idl.service("logservice.thrift", "LogService"))
                # Older forwarders deliver to LOGSERVICE_ID only: take it unless
                # another listener holds it. One name only, since a newer
                # forwarder would deliver to both.
                bare = registry.lookup(LOGSERVICE_ID)
                taken = bare is not None and (bare["host"], bare["port"]) != (host, port) and is_reachable(
                    bare["host"], bare["port"], timeout_s=0.5
                )
                self.service_name = self.suffixed_name if taken else LOGSERVICE_ID
                registry.register(self.service_name, host, port)
                result = {"ok": True, "status": "registered", "host": host, "port": port}
            self._registered_at = endpoint
            if self._listening_since is None:
                self._listening_since = datetime.now().astimezone()
        result["service_name"] = self.service_name
        forwarder = self._ensure_forwarder()
        if forwarder is not None:
            result["forwarder"] = forwarder
        return result

    def auto_ensure(self) -> None:
        """Best-effort :meth:`ensure_registered` before an IDE service call.

        Only looks at the registry when its endpoint changed since the last
        registration, so it is cheap on every call. Never raises: a failure
        here must not fail the call it runs before.
        """
        if not self._client.config.auto_log_service:
            return
        try:
            if self._registered_at is not None and self._client.backend.registry_endpoint() == self._registered_at:
                return
            self.ensure_registered()
        except Exception as exc:  # noqa: BLE001
            log.debug("LogService registration failed: %s", exc)

    def close(self) -> None:
        """Deregister (if the registry is still there) and stop the service."""
        endpoint, self._registered_at = self._registered_at, None
        if endpoint is not None and self.service_name is not None and is_reachable(*endpoint, timeout_s=0.5):
            try:
                self._client.registry.deregister(self.service_name, registry=endpoint)
            except Exception as exc:  # noqa: BLE001
                log.debug("LogService deregistration failed: %s", exc)
        self.server.stop()

    def _ensure_forwarder(self) -> dict[str, Any] | None:
        """Load the forwarder into a managed IarServiceLauncher, once per launcher process."""
        backend = self._client.backend
        if not backend.has_launcher:
            return None
        if LOG_FORWARDER_KEY in backend.loaded_manifests:
            self._forwarder = {"status": "loaded", "manifest": backend.loaded_manifests[LOG_FORWARDER_KEY]}
            return self._forwarder
        try:
            manifest = resolve_manifest(self._client.config, LOG_FORWARDER_KEY, LOG_FORWARDER)
            self._client.ide_services.load_manifest(manifest)
        except CSpyError as exc:
            self._forwarder = {"status": "error", "error": str(exc)}
            return self._forwarder
        backend.loaded_manifests[LOG_FORWARDER_KEY] = str(manifest)
        self._forwarder = {"status": "started", "manifest": str(manifest)}
        return self._forwarder

    # -- from the forwarder -------------------------------------------------------

    def _module(self) -> Any:
        return self._client.idl.module("logservice.thrift")

    def _category(self, name: str) -> _Category:
        stored = self._categories.get(name)
        if stored is None:
            stored = self._categories[name] = _Category(entries=deque(maxlen=self._maxlen))
        return stored

    def _add_category(self, name: str) -> None:
        # Arrives twice for one category when a client attaches late: idempotent.
        with self._lock:
            self._category(name).open = True

    def _remove_category(self, name: str) -> None:
        # Keeps what was stored: a log the IDE just closed can still be read.
        with self._lock:
            stored = self._categories.get(name)
            if stored is not None:
                stored.open = False

    def _start_session(self, name: str) -> None:
        with self._lock:
            self._category(name).session += 1

    def _post(self, raw: Any) -> None:
        with self._posted:
            name = str(getattr(raw, "category", "") or "")
            stored = self._category(name)
            self._seq += 1
            entry = _entry_from_thrift(raw, stored.session, self._seq)
            stored.entries.append(entry)
            self._posted.notify_all()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(entry)
            except Exception:  # noqa: BLE001
                log.exception("log listener failed")
