"""The lifecycle guard shared by the package classes that need a debug session.

One of two guards, which catch different things and are both needed:

* this one asks whether a session was ever *started*, before the call goes out.
  The backend is not a reliable judge of that: in some lifecycle states a
  wrong-state call ends the process rather than refusing (see
  ``docs/backend-notes.md``), in managed mode there may be no process to ask
  yet, and a *replaced* backend would answer for a session this client never
  configured.
* :meth:`~iar_cspy.debugger.Debugger.check_backend`, which
  :meth:`~iar_cspy.client.Client.call` runs for the services in
  ``_SESSION_SERVICE_KEYS``, asks whether a session that *was* started has
  since been replaced or died. It returns early when nothing was ever
  configured, so it does not subsume this guard.

Which methods need a started session is per-method, not per-service -
``Debugger`` hosts both kinds - so it is expressed as a decorator on the
method rather than as a list at the call chokepoint.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def requires_started_session(fn: F) -> F:
    """Raise SessionError unless the client has a started debug session.

    For methods of the classes hanging off the client; ``self._api_name`` is
    the attribute name (``client.<name>``) used in the error message.
    """

    @functools.wraps(fn)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        self._client.debugger.require_started(f"client.{self._api_name}.{fn.__name__}()")
        return fn(self, *args, **kwargs)

    return guarded  # type: ignore[return-value]
