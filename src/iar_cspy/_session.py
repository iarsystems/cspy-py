"""The lifecycle guard shared by the API classes that need a debug session."""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def requires_session(fn: F) -> F:
    """Raise SessionError unless the client has a started debug session.

    For methods of the classes hanging off the client; ``self._api_name`` is
    the attribute name (``client.<name>``) used in the error message.
    """

    @functools.wraps(fn)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        self._client.debugger.require_started(f"client.{self._api_name}.{fn.__name__}()")
        return fn(self, *args, **kwargs)

    return guarded  # type: ignore[return-value]
