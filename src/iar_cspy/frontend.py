"""A headless stand-in for the IDE's ``frontend`` service.

The backend calls back into the IDE's UI (``frontend.thrift``) for message
boxes, file dialogs, progress bars and source navigation, and some calls
(``ContextManager.getStack``/``getContextInfo``, source lookup, disassembly
at the PC) fail with "No such service, serviceName=frontend" without one.
This process hosts the service when no other frontend (e.g. a Thrift-enabled
IDE) has registered one.

Nobody is there to click, so every prompt is answered on the user's behalf
and recorded (:attr:`Frontend.prompts`). The defaults decline: message boxes
answer Cancel/No (Ok when that is the only button), dialogs are cancelled,
and alias lookups take the backend's suggested file (the original name
when there is none). Override them with
:meth:`Frontend.answer` rules or a :attr:`Frontend.handler`::

    client.frontend.answer(r"Overwrite", "yes")              # message boxes
    client.frontend.answer(r"Select .* file", "/abs/file.c", kind="file_dialog")
    client.frontend.handler = lambda prompt: ...             # None: fall through

When an RPC fails, the prompts the backend raised during it are attached to
the error (``CSpyError.prompts``, and appended to its message), since a
declined prompt is often the cause.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from ._callbacks import CallbackServer
from ._process import is_reachable
from ._thrift import to_plain
from .errors import PROMPTS_HEADER, CSpyError
from .types import FrontendPrompt

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

FRONTEND_SERVICE = "frontend"
FRONTEND_IDL = "frontend.thrift"

#: Prompt kinds that need an answer.
PROMPT_KINDS = ("message_box", "file_dialog", "save_dialog", "directory_dialog", "selection", "alias", "dialog")
#: Callbacks that only inform; recorded, but nothing to answer.
NOTICE_KINDS = ("message", "edit_source", "show_view", "file_properties", "file_explorer")
#: Kinds attached to a failing call's error.
ERROR_CONTEXT_KINDS = frozenset(PROMPT_KINDS) | {"message"}

_MSG_ANSWERS = {"ok": "kMsgResOk", "cancel": "kMsgResCancel", "yes": "kMsgResYes", "no": "kMsgResNo"}
#: MsgKind -> (default answer, allowed answers).
_MSG_KINDS = {
    "kMsgOk": ("ok", ("ok",)),
    "kMsgOkCancel": ("cancel", ("ok", "cancel")),
    "kMsgYesNo": ("no", ("yes", "no")),
    "kMsgYesNoCancel": ("cancel", ("yes", "no", "cancel")),
}

Handler = Callable[[FrontendPrompt], Any]


class _Rule:
    def __init__(self, pattern: str, result: Any, kind: str | None) -> None:
        self.pattern = re.compile(pattern)
        self.result = result
        self.kind = kind

    def matches(self, prompt: FrontendPrompt) -> bool:
        if self.kind is not None and self.kind != prompt.kind:
            return False
        return bool(self.pattern.search(prompt.message) or self.pattern.search(prompt.title))


class _FrontendHandler:
    """The ``Frontend`` service the backend calls (``frontend.thrift``)."""

    def __init__(self, frontend: Frontend) -> None:
        self._fe = frontend

    def isAlive(self) -> None:
        return None

    # -- prompts -----------------------------------------------------------------

    def messageBox(self, msg: str, caption: str, icon: int, kind: int, dontAskMgrKey: str) -> int:
        mod = self._fe._module()
        kind_name = _enum_name(mod.MsgKind, kind) or "kMsgOk"
        default, allowed = _MSG_KINDS.get(kind_name, ("ok", ("ok",)))
        details = {"icon": _enum_name(mod.MsgIcon, icon), "buttons": list(allowed), "dont_ask_key": dontAskMgrKey}
        prompt = self._fe._prompt("message_box", "messageBox", caption, msg, details, default)
        answer = str(prompt.answer).lower()
        if answer not in allowed:
            log.warning("frontend: answer %r is not one of %s for %r; using %r", answer, allowed, msg, default)
            prompt.answer, answer = default, default
        return getattr(mod.MsgResult, _MSG_ANSWERS[answer])

    def openFileDialog(self, title: str, startdir: str, filter: str, allowMultiple: bool, existing: bool) -> list[str]:
        details = {"start_dir": startdir, "filter": filter, "multiple": allowMultiple, "existing": existing}
        return self._paths("file_dialog", "openFileDialog", title, details)

    def openIHostFileDialog(
        self,
        title: str,
        type: int,
        returnType: int,
        filters: list[Any],
        options: list[int],
        startdir: str,
        defaultName: str,
    ) -> list[str]:
        mod = self._fe._module()
        dialog_type = _enum_name(mod.FileDialogType, type)
        details = {
            "type": dialog_type,
            "return_type": _enum_name(mod.FileDialogReturnType, returnType),
            "filters": [{"name": f.displayName, "patterns": list(f.filtering or [])} for f in filters or []],
            "options": [_enum_name(mod.FileDialogOptions, o) for o in options or []],
            "start_dir": startdir,
            "default_name": defaultName,
        }
        kind = "save_dialog" if dialog_type == "kSaveAs" else "file_dialog"
        return self._paths(kind, "openIHostFileDialog", title, details)

    def openDirectoryDialog(self, title: str, existing: bool, startdir: str) -> list[str]:
        return self._paths("directory_dialog", "openDirectoryDialog", title, {"existing": existing, "start_dir": startdir})

    def openSaveDialog(self, title: str, fileName: str, defExt: str, startDir: str, filter: str) -> list[str]:
        details = {"file_name": fileName, "default_ext": defExt, "start_dir": startDir, "filter": filter}
        return self._paths("save_dialog", "openSaveDialog", title, details)

    def openElementSelectionDialog(self, title: str, message: str, elements: list[str]) -> int:
        prompt = self._fe._prompt("selection", "openElementSelectionDialog", title, message, {"elements": list(elements)}, -1)
        return _index(prompt, elements)

    def openMultipleElementSelectionDialog(self, title: str, message: str, elements: list[str]) -> list[int]:
        details = {"elements": list(elements), "multiple": True}
        prompt = self._fe._prompt("selection", "openMultipleElementSelectionDialog", title, message, details, [])
        answer = prompt.answer if isinstance(prompt.answer, (list, tuple)) else [prompt.answer]
        indices = [i for i in (_index_of(a, elements) for a in answer) if i >= 0]
        prompt.answer = indices
        return indices

    def resolveAliasForFile(self, fileName: str, suggestedFile: str) -> str:
        details = {"file": fileName, "suggested": suggestedFile}
        # Without a suggestion, keep the original name: an empty answer makes the
        # backend drop the file's source ranges (stack, context info, lookup).
        default = suggestedFile or fileName
        prompt = self._fe._prompt("alias", "resolveAliasForFile", "", fileName, details, default)
        return str(prompt.answer or "")

    def invokeDialog(self, id: str, title: str, entries: Any) -> Any:
        mod = self._fe._module()
        details = {"id": id, "entries": to_plain(entries)}
        prompt = self._fe._prompt("dialog", "invokeDialog", title, id, details, "cancel")
        answer = prompt.answer
        if isinstance(answer, mod.GenericDialogResults):
            return answer
        accepted = str(answer).lower() == "ok"
        prompt.answer = "ok" if accepted else "cancel"
        return mod.GenericDialogResults(
            type=mod.GenericDialogReturnType.kOk if accepted else mod.GenericDialogReturnType.kCancel, items=entries
        )

    # -- notices -----------------------------------------------------------------

    def messageBoxAsync(self, msg: str, caption: str, icon: int, dontAskMgrKey: str) -> None:
        icon_name = _enum_name(self._fe._module().MsgIcon, icon)
        self._fe._notice("message", "messageBoxAsync", caption, msg, {"icon": icon_name})

    def editSourceLocation(self, loc: Any, focus: bool = True) -> None:
        details = {"file": loc.filename, "line": loc.line, "col": loc.col, "focus": focus}
        self._fe._notice("edit_source", "editSourceLocation", "", f"{loc.filename}:{loc.line}", details)

    def showView(self, id: str) -> None:
        self._fe._notice("show_view", "showView", "", id, {"id": id})

    def showFileProperties(self, filePath: str) -> None:
        self._fe._notice("file_properties", "showFileProperties", "", filePath, {"file": filePath})

    def openFileExplorer(self, filePath: str) -> None:
        self._fe._notice("file_explorer", "openFileExplorer", "", filePath, {"file": filePath})

    # -- progress ----------------------------------------------------------------

    def createProgressBar(
        self, msg: str, caption: str, minvalue: int, maxvalue: int, canCancel: bool, indeterminate: bool
    ) -> int:
        with self._fe._lock:
            self._fe._progress_ids += 1
            bar_id = self._fe._progress_ids
            self._fe.progress[bar_id] = {
                "caption": caption,
                "message": msg,
                "min": minvalue,
                "max": maxvalue,
                "value": minvalue,
                "indeterminate": indeterminate,
            }
        log.debug("frontend progress %d: %s %s", bar_id, caption, msg)
        return bar_id

    def updateProgressBarValue(self, id: int, value: int) -> bool:
        return self._fe._update_progress(id, value=value)

    def updateProgressBarMessage(self, id: int, message: str) -> bool:
        return self._fe._update_progress(id, message=message)

    def closeProgressBar(self, id: int) -> None:
        with self._fe._lock:
            self._fe.progress.pop(id, None)

    # -- IDE state the backend may ask for ----------------------------------------

    def handlesAliasStorage(self) -> bool:
        return False

    def loadAliases(self) -> dict[str, str]:
        return {}

    def getActiveTheme(self) -> dict[Any, Any]:
        return {}

    def getCapabilities(self) -> Any:
        return self._fe._module().shared.Capabilities(supportsEditorHighlight=False)

    def _paths(self, kind: str, method: str, title: str, details: dict[str, Any]) -> list[str]:
        # A dialog has a title only: rules match it there, and summaries show it once.
        prompt = self._fe._prompt(kind, method, title, "", details, [])
        answer = prompt.answer
        paths = [] if answer is None else [str(answer)] if isinstance(answer, str) else [str(p) for p in answer]
        prompt.answer = paths
        return paths


class Frontend:
    """The headless ``frontend`` service (``client.frontend``).

    Registered by configure/start, unless another reachable frontend (an
    IDE) is registered already: then the backend talks to that one and
    :attr:`hosting` is False. :attr:`prompts` records what the backend asked
    and how it was answered; :attr:`progress` holds the open progress bars.
    """

    def __init__(self, client: Client, maxlen: int = 200) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._seq = 0
        self._recent: deque[tuple[int, FrontendPrompt]] = deque(maxlen=maxlen)
        self._rules: list[_Rule] = []
        self._progress_ids = 0
        self.progress: dict[int, dict[str, Any]] = {}
        #: Called with each :class:`FrontendPrompt` before the rules; a
        #: non-None return value is the answer.
        self.handler: Handler | None = None
        self.hosting = False
        #: The service object the backend calls. Tests call its methods
        #: (``messageBox``, ``openFileDialog``, ...) to simulate the backend.
        self.service = _FrontendHandler(self)
        self.server = CallbackServer("frontend", self.service, client.config.frontend_client_timeout_ms)

    # -- answering ---------------------------------------------------------------

    def answer(self, pattern: str, result: Any, *, kind: str | None = "message_box") -> Callable[[], None]:
        """Answer prompts whose message or title matches ``pattern`` (a regex) with ``result``.

        ``kind`` is one of :data:`PROMPT_KINDS` (None: any). Results by kind:
        ``message_box``: "ok", "cancel", "yes" or "no"; ``file_dialog``,
        ``save_dialog``, ``directory_dialog``: a path or a list of paths;
        ``selection``: an index or element text (a list for multiple);
        ``alias``: a path; ``dialog``: "ok" or "cancel". The most recent
        matching rule wins. Returns a function that removes the rule.
        """
        if kind is not None and kind not in PROMPT_KINDS:
            raise ValueError(f"Unknown prompt kind {kind!r}; one of {', '.join(PROMPT_KINDS)}")
        if kind == "message_box" and str(result).lower() not in _MSG_ANSWERS:
            raise ValueError(f"A message box answer is one of {', '.join(_MSG_ANSWERS)}, not {result!r}")
        rule = _Rule(pattern, result, kind)
        with self._lock:
            self._rules.append(rule)

        def remove() -> None:
            with self._lock:
                if rule in self._rules:
                    self._rules.remove(rule)

        return remove

    def clear_rules(self) -> None:
        """Forget every :meth:`answer` rule."""
        with self._lock:
            self._rules.clear()

    # -- what happened -----------------------------------------------------------

    @property
    def prompts(self) -> list[FrontendPrompt]:
        """Recorded prompts and notices, oldest first (the last ``maxlen``)."""
        with self._lock:
            return [p for _, p in self._recent]

    def recent(self, clear: bool = False) -> list[FrontendPrompt]:
        """:attr:`prompts`, optionally forgetting them."""
        with self._lock:
            out = [p for _, p in self._recent]
            if clear:
                self._recent.clear()
            return out

    def mark(self) -> int:
        """A position in the record, for :meth:`since`."""
        with self._lock:
            return self._seq

    def since(self, mark: int) -> list[FrontendPrompt]:
        """What was recorded after :meth:`mark` returned ``mark``."""
        with self._lock:
            return [p for seq, p in self._recent if seq > mark]

    def annotate(self, error: CSpyError, mark: int) -> CSpyError:
        """Attach the prompts recorded since ``mark`` to ``error`` (done for failing RPCs)."""
        prompts = [p for p in self.since(mark) if p.kind in ERROR_CONTEXT_KINDS]
        if prompts and not getattr(error, "prompts", None):
            error.prompts = prompts
            lines = "\n".join(f"  - {p.summary()}" for p in prompts)
            error.args = (f"{error}{PROMPTS_HEADER}\n{lines}",)
        return error

    # -- registration ------------------------------------------------------------

    def ensure_registered(self) -> dict[str, Any]:
        """Host and register ``frontend``, unless a reachable one is registered already."""
        if self._client.backend.registry_endpoint() is None:
            self.hosting = False
            return {"ok": False, "reason": "no-registry-port"}
        with self.server.lock:
            existing = self._client.registry.lookup(FRONTEND_SERVICE)
            if existing is not None and is_reachable(existing["host"], existing["port"], timeout_s=0.5):
                self.hosting = (existing["host"], existing["port"]) == (self.server.host, self.server.port)
                return {"ok": True, "status": "already-registered", "host": existing["host"], "port": existing["port"]}
            host, port = self.server.ensure(self._client.idl.service(FRONTEND_IDL, "Frontend"))
            self._client.registry.register(FRONTEND_SERVICE, host, port)
            self.hosting = True
            return {"ok": True, "status": "registered", "host": host, "port": port}

    # -- internals ---------------------------------------------------------------

    def _module(self) -> Any:
        return self._client.idl.module(FRONTEND_IDL)

    def _prompt(
        self, kind: str, method: str, title: str, message: str, details: dict[str, Any], default: Any
    ) -> FrontendPrompt:
        prompt = FrontendPrompt(kind, method, str(title or ""), str(message or ""), details, default, "default", time.time())
        answer, answered_by = self._resolve(prompt)
        if answered_by != "default":
            prompt.answer, prompt.answered_by = answer, answered_by
        self._record(prompt)
        # A declined prompt may explain a failure; alias lookups are routine.
        level = logging.WARNING if answered_by == "default" and kind != "alias" else logging.INFO
        log.log(level, "frontend %s", prompt.summary())
        return prompt

    def _notice(self, kind: str, method: str, title: str, message: str, details: dict[str, Any]) -> None:
        prompt = FrontendPrompt(kind, method, str(title or ""), str(message or ""), details, None, None, time.time())
        self._record(prompt)
        log.info("frontend %s", prompt.summary())

    def _resolve(self, prompt: FrontendPrompt) -> tuple[Any, str]:
        handler = self.handler
        if handler is not None:
            try:
                answer = handler(prompt)
            except Exception:  # noqa: BLE001
                log.exception("frontend handler failed for %s; trying the rules", prompt.method)
            else:
                if answer is not None:
                    return answer, "handler"
        with self._lock:
            rules = list(reversed(self._rules))
        for rule in rules:
            if rule.matches(prompt):
                return rule.result, "rule"
        return prompt.answer, "default"

    def _record(self, prompt: FrontendPrompt) -> None:
        with self._lock:
            self._seq += 1
            self._recent.append((self._seq, prompt))

    def _update_progress(self, bar_id: int, **fields: Any) -> bool:
        with self._lock:
            bar = self.progress.get(bar_id)
            if bar is None:
                return False
            bar.update(fields)
        return True  # never cancelled


def _enum_name(enum_cls: Any, value: int) -> str | None:
    names = getattr(enum_cls, "_VALUES_TO_NAMES", {})
    return names.get(value)


def _index_of(answer: Any, elements: list[str]) -> int:
    if isinstance(answer, bool):
        return -1
    if isinstance(answer, int):
        return answer if 0 <= answer < len(elements) else -1
    try:
        return list(elements).index(str(answer))
    except ValueError:
        return -1


def _index(prompt: FrontendPrompt, elements: list[str]) -> int:
    index = _index_of(prompt.answer, elements)
    prompt.answer = index
    return index
