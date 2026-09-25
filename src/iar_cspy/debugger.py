"""The debugger: session lifecycle, run control and expressions.

A debug session goes through configure (resolve a launch configuration, set up
the target) and start. CSpyServer2 supports a single start/stop cycle per
process, so in managed mode stopping a session also restarts the backend
process, and :meth:`Debugger.configure_and_start` always starts from a fresh
one.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

from ._session import requires_session
from ._thrift import to_plain
from .context import ContextLike, context_ref
from .errors import CSpyError, SessionError, WaitTimeout, cspy_exception_info, describe, error_entry, with_context
from .launch import LaunchConfig
from .types import CleanupResult, CoreState, Register, RegisterSnapshot, StartResult, StopResult, WaitResult

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

#: Debugger methods that make sense before a session has started.
PRE_SESSION_METHODS = frozenset(
    {"getVersionString", "isOnline", "resolveLaunchConfiguration", "configureSession", "startSMPSession", "stopSession"}
)

#: Default timeout, in seconds, of the run-control waits.
DEFAULT_TIMEOUT = 10.0

_DECIMAL = re.compile(r"[-+]?\d+")


def _core_state(value: Any) -> CoreState | int:
    try:
        return CoreState(int(value))
    except ValueError:
        return int(value)


class Debugger:
    """The ``debugger`` service plus session bookkeeping (``client.debugger``).

    Typical use::

        with client.debugger.session("launch.json") as dbg:
            client.breakpoints.add("main")
            dbg.go_and_wait(timeout=10)
            print(dbg.eval_int("counter"))

    Methods that need a started session raise :class:`SessionError` without
    one; :meth:`attach` adopts a session another frontend started.
    """

    _api_name = "debugger"

    def __init__(self, client: Client) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._configured = False
        self._started = False
        #: ``Backend.debugger_generation`` the session runs in.
        self._generation = 0
        #: The :class:`StartResult` of the last :meth:`configure_and_start`.
        self.last_start: StartResult | None = None

    @property
    def client(self) -> Client:
        """The client this debugger belongs to (``cspy_session.client.memory``, ...)."""
        return self._client

    def _call(self, method: str, *args: Any) -> Any:
        return self._client.call("debugger", method, *args)

    # -- session state ---------------------------------------------------------

    @property
    def state(self) -> dict[str, bool]:
        """This client's view of the session: ``{"configured": ..., "started": ...}``."""
        self._lost_session()
        with self._lock:
            return {"configured": self._configured, "started": self._started}

    @property
    def started(self) -> bool:
        self._lost_session()
        with self._lock:
            return self._configured and self._started

    def set_state(self, configured: bool | None = None, started: bool | None = None) -> None:
        """Override the session bookkeeping, e.g. after configuring a session by hand."""
        with self._lock:
            if configured is not None:
                self._configured = bool(configured)
            if started is not None:
                self._started = bool(started)
            if configured or started:
                self._generation = self._client.backend.debugger_generation

    def require_started(self, what: str) -> None:
        """Raise SessionError unless this client has a started session."""
        if not self.started:
            raise SessionError(
                f"{what} requires an active debug session. Start one with "
                "client.debugger.session(launch) or configure_and_start(launch) (or configure() "
                "then start()), or call client.debugger.attach() to use a session another "
                "frontend, such as the IDE, started."
            )

    def attach(self) -> None:
        """Adopt a session that another frontend (e.g. a Thrift-enabled IDE) started.

        For standalone mode: nothing is configured or started, this client
        just stops refusing session calls. Raises SessionError when the
        backend reports no target online.
        """
        if not self.is_online():
            raise SessionError("attach() found no debug session: the backend reports the target offline.")
        self.set_state(configured=True, started=True)

    def _lost_session(self) -> bool:
        """Whether the managed CSpyServer2 the session ran in has been replaced.

        A managed CSpyServer2 that crashed or stopped responding is restarted
        on the next call, and the new process has no session. Resets the
        bookkeeping when that happened.
        """
        with self._lock:
            active = self._configured or self._started
            lost = active and self._generation != self._client.backend.debugger_generation
        if lost:
            log.warning("The managed CSpyServer2 was replaced; the debug session is gone.")
            self._reset_state()
        return lost

    def check_backend(self, method: str | None = None) -> None:
        """Raise SessionError if the session's CSpyServer2 was replaced under it.

        :meth:`Client.call` runs this for every debug-session service call,
        after the backend has been brought up. ``method`` is the RPC; the
        :data:`PRE_SESSION_METHODS` are let through.
        """
        if not self._lost_session() or method in PRE_SESSION_METHODS:
            return
        diagnostics = self._client.backend.debugger_process.last_process_diagnostics
        raise SessionError(
            "The managed CSpyServer2 process exited or stopped responding and was replaced by a "
            "fresh one, so the debug session is gone. Start a new session."
            + (f"\n{diagnostics}" if diagnostics else ""),
            diagnostics=diagnostics,
        )

    def _reset_state(self) -> None:
        self.set_state(configured=False, started=False)
        self._client.clear_session_caches()

    # -- information -----------------------------------------------------------

    def version(self) -> str:
        return str(self._call("getVersionString"))

    def is_online(self) -> bool:
        return bool(self._call("isOnline"))

    def methods(self) -> list[str]:
        """RPC method names of the Debugger service."""
        return self._client.methods("debugger")

    @requires_session
    def core_count(self) -> int:
        return int(self._call("getNumberOfCores"))

    @requires_session
    def core_state(self, core: int = 0) -> CoreState | int:
        return _core_state(self._call("getCoreState", int(core)))

    @requires_session
    def core_states(self) -> list[CoreState | int]:
        return [self.core_state(idx) for idx in range(max(0, self.core_count()))]

    def status(self) -> dict[str, Any]:
        """A diagnostic snapshot: session flags, backend mode and processes, core states.

        Never requires a session; the parts that could not be read have an
        ``*_error`` entry instead.
        """
        state = self.state
        backend = self._client.backend
        endpoint = backend.current_registry_endpoint()
        status: dict[str, Any] = {
            "configured": state["configured"],
            "started": state["started"],
            "backend_mode": backend.mode,
            "registry_host": endpoint[0] if endpoint else self._client.config.registry_host,
            "registry_port": endpoint[1] if endpoint else self._client.config.registry_port,
            "backend_online": None,
            "core_count": None,
            "core_states": None,
        }
        status.update(backend.process_status())

        try:
            status["backend_online"] = self.is_online()
        except Exception as exc:  # noqa: BLE001
            status["backend_online_error"] = str(exc)

        if state["started"]:
            try:
                count = int(self._call("getNumberOfCores"))
                status["core_count"] = count
                status["core_states"] = [int(self._call("getCoreState", idx)) for idx in range(max(0, count))]
            except Exception as exc:  # noqa: BLE001
                status["core_state_error"] = str(exc)
        return status

    # -- lifecycle -------------------------------------------------------------

    def configure(self, launch: LaunchConfig | dict | str | Any) -> None:
        """Resolve a launch configuration and configure a session; does not start it.

        ``launch`` is a :class:`LaunchConfig`, a dict, JSON text or the path of
        a launch.json (first configuration). This does not stop a previous
        session first: some backends assert when stopSession is called in
        certain states. Use :meth:`configure_and_start` for a clean handoff.
        """
        config = LaunchConfig.coerce(launch)
        self._configure(lambda: self._call("resolveLaunchConfiguration", config.to_json()))

    def configure_resolved(self, launch_configuration: Any) -> None:
        """Configure from an already resolved ``shared.LaunchConfiguration``.

        That is what :meth:`Project.launch_configuration` returns.
        """
        self._configure(lambda: launch_configuration)

    def _configure(self, resolve: Any) -> None:
        try:
            self._client.ensure_callbacks()
            self._call("configureSession", resolve())
        except Exception as exc:  # noqa: BLE001
            self._reset_state()
            raise with_context(
                exc,
                "Configuring the debug session failed. Local lifecycle state was reset; retry "
                f"configure -> start once the backend is healthy. Backend error: {exc}",
            ) from exc
        self._client.clear_session_caches()
        self.set_state(configured=True, started=False)

    def start(self) -> None:
        """Start the configured session (``startSMPSession``)."""
        if not self.state["configured"]:
            raise SessionError("start() requires a configured session: call configure(launch) first.")
        try:
            self._client.ensure_callbacks()
            self._call("startSMPSession")
        except Exception as exc:  # noqa: BLE001
            self.set_state(started=False)
            raise with_context(
                exc,
                "Starting the debug session failed. The session remains configured but not "
                f"started. Backend error: {exc}",
            ) from exc
        self.set_state(started=True)

    def configure_and_start(self, launch: LaunchConfig | dict | str | Any) -> StartResult:
        """Tear down any previous session, configure, start, and run to ``stopOnSymbol``.

        In managed mode the backend process is always replaced by a fresh one
        first. Otherwise an active or stale session is stopped, falling back
        to :meth:`cleanup` if stopping fails.

        CSpyServer2 never acts on ``stopOnSymbol`` itself: running there is the
        frontend's job (the IDE and the VS Code adapter do it), so it is done
        here after start. Not reaching it does not fail the start: check
        ``result.ran_to_symbol`` (:meth:`session` does, and raises).
        """
        config = LaunchConfig.coerce(launch)
        handoff: dict[str, Any] = {
            "had_active_session": False,
            "stale_backend_online": False,
            "stop_attempted": False,
            "strict_cleanup_used": False,
            "managed_fresh_process": False,
        }
        before = self.state
        local_active = bool(before["configured"] or before["started"])

        if self._client.backend.mode == "managed":
            handoff.update(managed_fresh_process=True, had_active_session=local_active, stop_attempted=True)
            cleanup = self.cleanup(reset_target=False)
            handoff.update(strict_cleanup_used=True, strict_cleanup_ok=cleanup.ok, stop_ok=cleanup.ok)
            if not cleanup.ok:
                raise CSpyError(
                    "configure_and_start (managed mode) could not establish a clean fresh "
                    f"backend handoff. Cleanup result: {cleanup.to_dict()}"
                )
        else:
            try:
                backend_online = self.is_online()
            except Exception:  # noqa: BLE001
                backend_online = False
            stale_backend_online = not local_active and backend_online

            if local_active or stale_backend_online:
                handoff.update(had_active_session=True, stale_backend_online=stale_backend_online, stop_attempted=True)
                try:
                    if local_active:
                        self.stop_session()
                    else:
                        self._call("stopSession")
                        self._reset_state()
                    handoff["stop_ok"] = True
                except Exception as exc:  # noqa: BLE001
                    handoff.update(stop_ok=False, stop_error=str(exc))
                    cleanup = self.cleanup(reset_target=False)
                    handoff.update(strict_cleanup_used=True, strict_cleanup_ok=cleanup.ok)
                    if not cleanup.ok:
                        raise CSpyError(
                            "configure_and_start could not establish a clean handoff from a "
                            f"previous session. Cleanup result: {cleanup.to_dict()}"
                        ) from exc

        self.configure(config)
        self.start()

        result = StartResult(handoff=handoff, stop_on_symbol=config.stop_on_symbol, ran_to_symbol=False)
        if result.stop_on_symbol:
            try:
                self.run_to(result.stop_on_symbol)
                result.ran_to_symbol = True
            except Exception as exc:  # noqa: BLE001
                result.stop_on_symbol_error = str(exc)
        self.last_start = result
        return result

    def stop_session(self) -> StopResult:
        """Stop the session; idempotent.

        A ``DkStop`` "failed to suspend" error is treated as success when the
        cores turn out to be stopped already. In managed mode the CSpyServer2
        process is shut down, so the next session gets a fresh one. The local
        state is reset even when stopping fails.
        """
        managed = self._client.backend.mode == "managed"
        state = self.state

        if not state["configured"] and not state["started"]:
            shutdown_error: Exception | None = None
            try:
                self._client.backend.restart_debugger()
            except Exception as exc:  # noqa: BLE001
                shutdown_error = exc
            self._reset_state()
            if shutdown_error is not None:
                raise CSpyError(
                    "stop_session completed the local stop reset, but the managed backend "
                    f"shutdown failed: {shutdown_error}"
                ) from shutdown_error
            return StopResult(already_stopped=True, backend_restarted=managed)

        result = StopResult(backend_restarted=managed)
        stop_error: Exception | None = None
        try:
            self._call("stopSession")
        except Exception as exc:  # noqa: BLE001
            stop_error = exc
            idempotent, details = self.benign_stop_error(exc)
            if idempotent:
                result.stop_idempotent_recovered = True
                result.stop_idempotent_details = details
                stop_error = None

        shutdown_error = None
        try:
            self._client.backend.restart_debugger()
        except Exception as exc:  # noqa: BLE001
            shutdown_error = exc

        self._reset_state()

        if stop_error is not None or shutdown_error is not None:
            parts = ["stop_session failed. Local lifecycle state was still reset to stopped."]
            if stop_error is not None:
                parts.append(f"stopSession error: {describe(stop_error)}")
            if shutdown_error is not None:
                parts.append(f"managed shutdown error: {shutdown_error}")
            cause = stop_error if stop_error is not None else shutdown_error
            raise with_context(cause, " ".join(parts)) from cause
        return result

    def cleanup(self, reset_target: bool = False) -> CleanupResult:
        """Force a known-good baseline; never raises.

        Best effort: optionally reset the target, stop the session, clear
        local session state and buffers, and shut down the managed CSpyServer2.
        """
        errors: list[dict[str, Any]] = []
        before = self.state
        active = before["configured"] or before["started"]

        def failed(message: str, exc: Exception) -> None:
            errors.append(
                error_entry(
                    code="CLEANUP_STEP_FAILED",
                    category="cleanup",
                    message=message,
                    retryable=True,
                    details=self._client.classify_error(exc),
                )
            )

        if reset_target and active:
            try:
                self._call("reset")
            except Exception as exc:  # noqa: BLE001
                failed("reset failed", exc)

        if active:
            try:
                self._call("stopSession")
            except Exception as exc:  # noqa: BLE001
                idempotent, details = self.benign_stop_error(exc)
                if idempotent:
                    log.debug("cleanup: stopSession DkStop error, but cores are stopped: %r", details)
                else:
                    failed("stopSession failed", exc)

        self._reset_state()
        self._client.listwindows.clear_notifications()
        self._client.terminal.clear_input()

        try:
            self._client.backend.restart_debugger()
        except Exception as exc:  # noqa: BLE001
            failed("shutdown_managed_server failed", exc)

        return CleanupResult(ok=not errors, errors=errors, before=before, after=self.state)

    @contextmanager
    def session(
        self, launch: LaunchConfig | dict | str | Any, *, require_stop_on_symbol: bool = True
    ) -> Iterator[Debugger]:
        """``configure_and_start`` on entry, ``stop_session`` on exit.

        When the launch configuration has a ``stopOnSymbol`` that was not
        reached, the session is stopped again and SessionError raised, so the
        body never runs from an unexpected place. Pass
        ``require_stop_on_symbol=False`` to get the session anyway;
        :attr:`last_start` then tells what happened.
        """
        result = self.configure_and_start(launch)
        if require_stop_on_symbol and result.stop_on_symbol and not result.ran_to_symbol:
            try:
                self.stop_session()
            except Exception:  # noqa: BLE001
                log.warning("stop_session failed after not reaching stopOnSymbol", exc_info=True)
            raise SessionError(
                f"The session started, but did not stop at stopOnSymbol {result.stop_on_symbol!r}: "
                f"{result.stop_on_symbol_error}. Pass require_stop_on_symbol=False to use it anyway."
            )
        try:
            yield self
        except BaseException:
            try:
                self.stop_session()
            except Exception:  # noqa: BLE001
                log.warning("stop_session failed while unwinding", exc_info=True)
            raise
        else:
            self.stop_session()

    def _probe_stopped(self) -> dict[str, Any]:
        """Best-effort check whether the target is already stopped."""
        out: dict[str, Any] = {"already_stopped": False}
        try:
            out["online"] = self.is_online()
        except Exception as exc:  # noqa: BLE001
            out["online_error"] = describe(exc)
            return out
        if not out["online"]:
            out["already_stopped"] = True
            return out
        try:
            core_count = int(self._call("getNumberOfCores"))
            out["core_count"] = core_count
        except Exception as exc:  # noqa: BLE001
            out["core_count_error"] = describe(exc)
            return out
        if core_count <= 0:
            return out
        try:
            states = [int(self._call("getCoreState", idx)) for idx in range(core_count)]
        except Exception as exc:  # noqa: BLE001
            out["core_state_error"] = describe(exc)
            return out
        out["core_states"] = states
        out["already_stopped"] = all(s == CoreState.STOPPED for s in states)
        return out

    def benign_stop_error(self, exc: Exception) -> tuple[bool, dict[str, Any] | None]:
        """Whether a stopSession failure only means "already stopped".

        Some backends report ``DkStop`` kDcError ("failed to suspend") when
        the target is already halted; that is confirmed by probing the cores.
        Returns (benign, details).
        """
        info = cspy_exception_info(exc)
        if info is None:
            return False, None
        if info.code != 6 or info.method != "DkStop":
            return False, info.to_dict()
        probe = self._probe_stopped()
        return bool(probe.get("already_stopped")), {"exception": info.to_dict(), "probe": probe}

    # -- run control -----------------------------------------------------------

    @requires_session
    def go(self) -> None:
        """Start execution; returns immediately (see :meth:`go_and_wait`)."""
        self._call("go")

    @requires_session
    def halt(self) -> None:
        """Stop execution (``Debugger.stop``)."""
        self._call("stop")

    @requires_session
    def reset(self) -> None:
        """Reset the target per the active reset style."""
        self._call("reset")

    @requires_session
    def step_over(
        self, enter_functions_without_source: bool | None = None, *, wait: bool = True, timeout: float = DEFAULT_TIMEOUT
    ) -> WaitResult | None:
        """Step over one source statement; by default waits until it has stopped again."""
        return self._step("stepOver", enter_functions_without_source, wait, timeout)

    @requires_session
    def step_into(
        self, enter_functions_without_source: bool | None = None, *, wait: bool = True, timeout: float = DEFAULT_TIMEOUT
    ) -> WaitResult | None:
        """Step into one source statement; by default waits until it has stopped again."""
        return self._step("step", enter_functions_without_source, wait, timeout)

    @requires_session
    def step_out(self, *, wait: bool = True, timeout: float = DEFAULT_TIMEOUT) -> WaitResult | None:
        """Run until the current function returns; by default waits until it has."""
        return self._step("stepOut", None, wait, timeout)

    def _step(
        self, method: str, enter_functions_without_source: bool | None, wait: bool, timeout: float
    ) -> WaitResult | None:
        # Sent only when given: CSpyServer2 behaves differently for an explicit
        # false than for the argument left out, and the latter is what works
        # without a frontend service.
        if enter_functions_without_source is None:
            action = lambda: self._call(method)  # noqa: E731
        else:
            action = lambda: self._call(method, bool(enter_functions_without_source))  # noqa: E731
        if not wait:
            action()
            return None
        return self._run_and_wait(action, CoreState.STOPPED, core=0, timeout=timeout, poll_interval=0.05, check=True)

    @requires_session
    def run_to(
        self, ule: str, *, allow_single_step: bool = True, timeout: float = DEFAULT_TIMEOUT, wait: bool = True
    ) -> WaitResult | None:
        """Run until ``ule`` (e.g. ``"main"``) is reached.

        ``runToULE`` returns while the target is still running, so by default
        this waits until it has stopped there (see :meth:`go_and_wait`).
        Raises :class:`WaitTimeout` if it does not stop within ``timeout``.
        """
        action = lambda: self._call("runToULE", ule, bool(allow_single_step))  # noqa: E731
        if not wait:
            action()
            return None
        return self._run_and_wait(action, CoreState.STOPPED, core=0, timeout=timeout, poll_interval=0.05, check=True)

    @requires_session
    def wait_for_state(
        self,
        state: CoreState | int = CoreState.STOPPED,
        *,
        core: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = 0.05,
        check: bool = True,
    ) -> WaitResult:
        """Poll until ``core`` reaches ``state``.

        Raises :class:`WaitTimeout` on timeout, or with ``check=False``
        returns a result with ``timed_out=True``.
        """
        start = time.perf_counter()
        return self._poll_state(
            state, core=core, start=start, deadline=start + max(0.0, float(timeout)), timeout=timeout,
            poll_interval=poll_interval, check=check,
        )

    @requires_session
    def go_and_wait(
        self,
        state: CoreState | int = CoreState.STOPPED,
        *,
        core: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = 0.05,
        check: bool = True,
    ) -> WaitResult:
        """:meth:`go`, then :meth:`wait_for_state` (by default: until it stops again).

        Right after ``go`` the core can still read as stopped, before it has
        actually started. So when waiting for a stop and this client receives
        the backend's debug events, it first waits for the target-started and
        then the target-stopped event that follow the ``go``, then confirms
        the core state. Without those events within ``timeout`` the wait has
        timed out, whatever the core state reads.
        """
        return self._run_and_wait(self.go, state, core=core, timeout=timeout, poll_interval=poll_interval, check=check)

    def _run_and_wait(
        self, action: Any, state: CoreState | int, *, core: int, timeout: float, poll_interval: float, check: bool
    ) -> WaitResult:
        events = self._client.events
        mark = events.mark()
        start = time.perf_counter()
        deadline = start + max(0.0, float(timeout))
        action()
        if int(state) == CoreState.STOPPED and events.receiving:
            if events.wait_for_stop(after=mark, timeout=max(0.0, deadline - time.perf_counter())) is None:
                return self._poll_state(
                    state, core=core, start=start, deadline=start, timeout=timeout,
                    poll_interval=0, check=check, stop_event_missing=True,
                )
        return self._poll_state(
            state, core=core, start=start, deadline=deadline, timeout=timeout, poll_interval=poll_interval, check=check
        )

    def _poll_state(
        self,
        state: CoreState | int,
        *,
        core: int,
        start: float,
        deadline: float,
        timeout: float,
        poll_interval: float,
        check: bool,
        stop_event_missing: bool = False,
    ) -> WaitResult:
        """Poll the core state until ``state`` or ``deadline``; times are ``perf_counter`` values.

        ``stop_event_missing``: no target-stopped event came, so reading
        "stopped" does not count (the core may simply not have started).
        """
        target, core = int(state), int(core)
        attempts = 0
        while True:
            attempts += 1
            last = int(self._call("getCoreState", core))
            now = time.perf_counter()
            reached = last == target and not stop_event_missing
            if reached or now >= deadline:
                result = WaitResult(
                    ok=reached,
                    timed_out=not reached,
                    core=core,
                    desired_state=_core_state(target),
                    state=_core_state(last),
                    attempts=attempts,
                    elapsed_ms=int((now - start) * 1000.0),
                )
                if result.timed_out and check:
                    if stop_event_missing:
                        message = (
                            f"Core {core} was not seen to stop within {timeout}s: no target-stopped event "
                            f"followed a target-started one (last state {result.state!r})"
                        )
                    else:
                        message = (
                            f"Core {core} did not reach state {result.desired_state!r} within {timeout}s "
                            f"(last state {result.state!r})"
                        )
                    raise WaitTimeout(message, result)
                return result
            if poll_interval > 0:
                time.sleep(poll_interval)

    # -- target data -----------------------------------------------------------

    @requires_session
    def eval(
        self,
        expression: str,
        *,
        context: ContextLike = None,
        format: int | str = 0,
        prefix: bool = False,
    ) -> Any:
        """Evaluate a C expression; returns an ``ExprValue`` struct.

        ``.value`` is the value as text, ``.type`` the C type, and
        ``.location`` where it lives (pass the ExprValue to
        ``client.memory.read`` to read it). ``format`` is an ``ExprFormat``: a
        number or a name such as ``"Hex"``; ``prefix`` adds the format's
        prefix (``0x``) to the text. For a number use :meth:`eval_int`.
        """
        if not expression.strip():
            raise CSpyError("expression must be non-empty")
        shared = self._client.idl.shared
        expr_format = format
        if expr_format in (0, "", None):
            expr_format = int(shared.ExprFormat.kDefault)
        return self._call(
            "evalExpression", context_ref(self._client, context), expression, [], expr_format, bool(prefix)
        )

    def eval_int(self, expression: str, *, context: ContextLike = None) -> int:
        """Evaluate a C expression of integer, pointer, char or bool type to an ``int``."""
        value = self.eval(expression, context=context, format="Dec")
        # Decimal text, with ' as digit group separator: 536'870'996.
        text = str(getattr(value, "value", "") or "").replace("'", "").strip()
        if not _DECIMAL.fullmatch(text):
            raise CSpyError(f"{expression!r} did not evaluate to an integer: {getattr(value, 'value', None)!r}")
        return int(text, 10)

    @requires_session
    def modules(self) -> list[Any]:
        """Loaded modules (``ModuleData`` structs)."""
        return self._call("getModules")

    @requires_session
    def load_module(self, filename: str) -> None:
        self._call("loadModule", str(filename))

    @requires_session
    def threads(self) -> list[Any]:
        return self._call("getThreadList")

    @requires_session
    def cycle_counter(self, core: int = 0) -> int:
        return int(self._call("getCycleCounter", int(core)))

    @requires_session
    def zones(self) -> list[Any]:
        """Memory zones (``ZoneInfo`` structs); zone ids are what memory calls take."""
        return self._call("getAllZones")

    @requires_session
    def registers(self, group: str = "CPU Registers (ABI)", limit: int | None = None) -> RegisterSnapshot:
        """Metadata and values of the registers in ``group`` (the first ``limit``, if given).

        Falls back to the first group when ``group`` does not exist; on
        targets without an ABI view that is usually ``"CPU Registers"``. A
        register whose value cannot be read has ``read_error`` set.
        """
        groups = list(self._call("getRegisterGroups") or [])
        if not groups:
            return RegisterSnapshot(group=group, requested_group=group, available_groups=[], total_in_group=0)

        selected = group if group in groups else groups[0]
        names = list(self._call("getLocationNamesInGroup", selected) or [])
        wanted = names if limit is None else names[: max(1, int(limit))]
        named_locations = [self._call("getNamedLocation", name) for name in wanted]

        snapshot = RegisterSnapshot(
            group=selected, requested_group=group, available_groups=groups, total_in_group=len(names)
        )
        with self._client.connection("memory") as memory:
            for named in named_locations:
                full_bits = int(getattr(named, "fullBitSize", 0) or 0)
                bit_size = full_bits if full_bits > 0 else 32
                location = getattr(named, "location", None)
                reg = Register(
                    name=getattr(named, "name", ""),
                    alias=getattr(named, "nameAlias", ""),
                    description=getattr(named, "description", ""),
                    bit_size=bit_size,
                    location=to_plain(location),
                    readonly=bool(getattr(named, "readonly", False)),
                    writeonly=bool(getattr(named, "writeonly", False)),
                )
                try:
                    reg.raw = bytes(memory.raw.readMemory(location, 1, 8, max(1, (bit_size + 7) // 8)))
                except Exception as exc:  # noqa: BLE001
                    reg.read_error = str(exc)
                snapshot.registers.append(reg)
        return snapshot

    # -- escape hatch ----------------------------------------------------------

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call any Debugger RPC, keeping the session bookkeeping in sync.

        Methods other than :data:`PRE_SESSION_METHODS` need a started
        session. ``configureSession``, ``startSMPSession`` and ``stopSession``
        update :attr:`state` as the dedicated methods would. A ``stopSession``
        failure that only means "already stopped" (see
        :meth:`benign_stop_error`) is not raised: a :class:`StopResult`
        saying so is returned instead.
        """
        if method not in PRE_SESSION_METHODS:
            self.require_started(f"client.debugger.call({method!r})")
        try:
            result = self._client.call("debugger", method, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if method == "configureSession":
                self._reset_state()
            elif method == "startSMPSession":
                self.set_state(started=False)
            elif method == "stopSession":
                # Probe before the reset: the probe reads the core states.
                benign, details = self.benign_stop_error(exc)
                self._reset_state()
                if benign:
                    return StopResult(
                        already_stopped=True, stop_idempotent_recovered=True, stop_idempotent_details=details
                    )
            raise

        if method == "configureSession":
            self._client.clear_session_caches()
            self.set_state(configured=True, started=False)
        elif method == "startSMPSession":
            self.set_state(started=True)
        elif method == "stopSession":
            self._reset_state()
        return result

