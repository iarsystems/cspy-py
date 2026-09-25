"""Stack, locals and parameters through the ``debugger.contextmanager`` service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from ._thrift import to_plain
from .errors import CSpyError
from .types import Context

if TYPE_CHECKING:
    from .client import Client

#: Anything that names a context: None (current base), a Context, a
#: ``{"type": ..., "level": ...}`` dict, or a ``shared.ContextRef`` struct.
ContextLike = Union[Context, dict, Any, None]

_CONTEXT_TYPE_NAMES = ("CurrentBase", "CurrentInspection", "Stack", "Target", "Task", "Unknown")


def context_ref(client: Client, context: ContextLike = None) -> Any:
    """Build a ``shared.ContextRef`` from anything :data:`ContextLike`."""
    shared = client.idl.shared
    if context is None:
        return shared.ContextRef(type=shared.ContextType.CurrentBase, level=0, core=0, task=0)
    if hasattr(context, "thrift_spec"):
        return context
    if isinstance(context, Context):
        context = {"type": context.type, "level": context.level, "core": context.core, "task": context.task}
    if not isinstance(context, dict):
        raise CSpyError(f"context must be a Context, a dict or None, not {type(context).__name__}")

    type_raw = context.get("type", "CurrentBase")
    if isinstance(type_raw, str):
        name = type_raw[1:] if type_raw.startswith("k") and type_raw[1:] in _CONTEXT_TYPE_NAMES else type_raw
        value = getattr(shared.ContextType, name, None)
        if value is None:
            raise CSpyError(f"Unknown context type: {type_raw}; expected one of {', '.join(_CONTEXT_TYPE_NAMES)}")
    else:
        value = int(type_raw)

    return shared.ContextRef(
        type=value,
        level=int(context.get("level", 0) or 0),
        core=int(context.get("core", 0) or 0),
        task=int(context.get("task", 0) or 0),
    )


class ContextManager:
    """Call stack and variables of a context (``client.context``).

    Every method takes an optional ``context`` (default: where the target is
    stopped); use :meth:`Context.frame` to look at an outer frame.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def _call(self, method: str, *args: Any) -> Any:
        return self._client.call("contextmanager", method, *args)

    def stack(self, low: int = 0, high: int = 20, context: ContextLike = None) -> list[Any]:
        """Frames ``low`` to ``high`` of the call stack (``ContextInfo`` structs)."""
        return self._call("getStack", context_ref(self._client, context), int(low), int(high))

    def depth(self, max_depth: int = 256, context: ContextLike = None) -> int:
        return int(self._call("getStackDepth", context_ref(self._client, context), int(max_depth)))

    def info(self, context: ContextLike = None) -> Any:
        """Function name, execution location and source ranges of a context."""
        return self._call("getContextInfo", context_ref(self._client, context))

    def locals(self, context: ContextLike = None) -> list[Any]:
        """Local variables (``Symbol`` structs)."""
        return self._call("getLocals", context_ref(self._client, context))

    def parameters(self, context: ContextLike = None) -> list[Any]:
        """Function parameters (``Symbol`` structs)."""
        return self._call("getParameters", context_ref(self._client, context))

    def visible_symbols(self, context: ContextLike = None) -> dict[str, list[str]]:
        """Names of the locals and parameters in scope.

        Returns ``{"locals": [...], "parameters": [...], "all": [...]}``,
        where ``all`` merges both without duplicates.
        """
        ref = context_ref(self._client, context)
        local_names = _names(self._call("getLocals", ref))
        param_names = _names(self._call("getParameters", ref))
        merged: list[str] = []
        for name in local_names + param_names:
            if name and name not in merged:
                merged.append(name)
        return {"locals": local_names, "parameters": param_names, "all": merged}

    def lookup(self, name: str, context: ContextLike = None, prefix: bool = False) -> dict[str, Any]:
        """Find visible symbols named ``name`` (or starting with it) and evaluate them."""
        query = name.strip()
        if not query:
            raise CSpyError("name must be non-empty")
        candidates = self.visible_symbols(context)["all"]
        matches = [s for s in candidates if (s.startswith(query) if prefix else s == query)]

        evaluations: list[dict[str, Any]] = []
        for symbol in matches:
            try:
                evaluations.append({"name": symbol, "value": to_plain(self._client.debugger.eval(symbol, context))})
            except Exception as exc:  # noqa: BLE001
                evaluations.append({"name": symbol, "error": str(exc)})
        return {"query": query, "prefix": bool(prefix), "matches": matches, "evaluations": evaluations}


def _names(symbols: Any) -> list[str]:
    return [str(item.get("name", "")) for item in (to_plain(symbols) or []) if isinstance(item, dict)]
