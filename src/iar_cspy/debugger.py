"""The debugger: session lifecycle, run control and expressions.

A debug session goes through configure (resolve a launch configuration, set up
the target) and start. CSpyServer2 supports a single start/stop cycle per
process, so in managed mode stopping a session also restarts the backend
process, and :meth:`Debugger.configure_and_start` always starts from a fresh
one.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

from ._thrift import to_plain
from .context import ContextLike, context_ref
from .errors import CSpyError, SessionError, WaitTimeout, cspy_exception_info, describe, error_entry
from .events import TARGET_STOPPED
from .launch import LaunchConfig
from .types import CleanupResult, CoreState, Register, RegisterSnapshot, StartResult, StopResult, WaitResult

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

#: Debugger methods that make sense before a session has started.
PRE_SESSION_METHODS = frozenset(
    {"getVersionString", "isOnline", "resolveLaunchConfiguration", "configureSession", "startSMPSession", "stopSession"}
)


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
            print(dbg.eval("counter").value)
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._configured = False
        self._started = False

    def _call(self, method: str, *args: Any) -> Any:
        return self._client.call("debugger", method, *args)

    # -- session state ---------------------------------------------------------

    @property
    def state(self) -> dict[str, bool]:
        """This client's view of the session: ``{"configured": ..., "started": ...}``."""
        with self._lock:
            return {"configured": self._configured, "started": self._started}

    @property
    def started(self) -> bool:
        with self._lock:
            return self._configured and self._started

    def set_state(self, configured: bool | None = None, started: bool | None = None) -> None:
        """Override the session bookkeeping, e.g. after configuring a session by hand."""
        with self._lock:
            if configured is not None:
                self._configured = bool(configured)
            if started is not None:
                self._started = bool(started)

    def require_started(self, what: str) -> None:
        """Raise SessionError unless this client configured and started a session."""
        if not self.started:
            raise SessionError(
                f"{what} requires an active debug session. Required order: configure(launch) -> start()."
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

    def core_count(self) -> int:
        return int(self._call("getNumberOfCores"))

    def core_state(self, core: int = 0) -> CoreState | int:
        return _core_state(self._call("getCoreState", int(core)))

    def core_states(self) -> list[CoreState | int]:
        return [self.core_state(idx) for idx in range(max(0, self.core_count()))]

    def status(self) -> dict[str, Any]:
        """Session flags, backend mode and process state, and core states if started."""
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
        processes = backend.status()
        if "debugger" in processes:
            status["managed_server"] = {"mode": "managed", **processes["debugger"]}
        if "service_launcher" in processes:
            status["service_launcher"] = processes["service_launcher"]

        try:
            status["backend_online"] = self.is_online()
        except Exception as exc:  # noqa: BLE001
            status["backend_online_error"] = str(exc)

        if state["started"]:
            try:
                count = self.core_count()
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
            raise CSpyError(
                "Configuring the debug session failed. Local lifecycle state was reset; retry "
                f"configure -> start once the backend is healthy. Backend error: {exc}",
                diagnostics=getattr(exc, "diagnostics", ""),
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
            raise CSpyError(
                "Starting the debug session failed. The session remains configured but not "
                f"started. Backend error: {exc}",
                diagnostics=getattr(exc, "diagnostics", ""),
            ) from exc
        self.set_state(started=True)

    def configure_and_start(self, launch: LaunchConfig | dict | str | Any) -> StartResult:
        """Tear down any previous session, configure, start, and run to ``stopOnSymbol``.

        In managed mode the backend process is always replaced by a fresh one
        first. Otherwise an active or stale session is stopped, falling back
        to :meth:`cleanup` if stopping fails.

        CSpyServer2 never acts on ``stopOnSymbol`` itself: running there is the
        frontend's job (the IDE and the VS Code adapter do it), so it is done
        here after start.
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
            raise CSpyError(" ".join(parts)) from (shutdown_error or stop_error)
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
    def session(self, launch: LaunchConfig | dict | str | Any) -> Iterator[Debugger]:
        """``configure_and_start`` on entry, ``stop_session`` on exit."""
        self.configure_and_start(launch)
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
            core_count = self.core_count()
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

    def go(self) -> None:
        """Start execution; returns immediately (see :meth:`go_and_wait`)."""
        self._call("go")

    def halt(self) -> None:
        """Stop execution (``Debugger.stop``)."""
        self._call("stop")

    def reset(self) -> None:
        """Reset the target per the active reset style."""
        self._call("reset")

    def step_over(self, enter_functions_without_source: bool | None = None) -> None:
        """Step over one source statement."""
        self._step("stepOver", enter_functions_without_source)

    def step_into(self, enter_functions_without_source: bool | None = None) -> None:
        """Step into one source statement."""
        self._step("step", enter_functions_without_source)

    def _step(self, method: str, enter_functions_without_source: bool | None) -> None:
        # Sent only when given: CSpyServer2 behaves differently for an explicit
        # false than for the argument left out, and the latter is what works
        # without a frontend service.
        if enter_functions_without_source is None:
            self._call(method)
        else:
            self._call(method, bool(enter_functions_without_source))

    def step_out(self) -> None:
        self._call("stepOut")

    def run_to(
        self, ule: str, *, allow_single_step: bool = True, timeout: float = 10.0, wait: bool = True
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

    def wait_for_state(
        self,
        state: CoreState | int = CoreState.STOPPED,
        *,
        core: int = 0,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
        check: bool = True,
    ) -> WaitResult:
        """Poll until ``core`` reaches ``state``.

        Raises :class:`WaitTimeout` on timeout, or with ``check=False``
        returns a result with ``timed_out=True``.
        """
        target, core = int(state), int(core)
        start = time.perf_counter()
        deadline = start + max(0.0, float(timeout))
        attempts = 0
        last: int | None = None
        while True:
            attempts += 1
            last = int(self._call("getCoreState", core))
            now = time.perf_counter()
            if last == target or now >= deadline:
                result = WaitResult(
                    ok=last == target,
                    timed_out=last != target,
                    core=core,
                    desired_state=target,
                    state=last,
                    attempts=attempts,
                    elapsed_ms=int((now - start) * 1000.0),
                )
                if result.timed_out and check:
                    raise WaitTimeout(
                        f"Core {core} did not reach state {_core_state(target)!r} within {timeout}s "
                        f"(last state {_core_state(last)!r})",
                        result,
                    )
                return result
            if poll_interval > 0:
                time.sleep(poll_interval)

    def go_and_wait(
        self,
        state: CoreState | int = CoreState.STOPPED,
        *,
        core: int = 0,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
        check: bool = True,
    ) -> WaitResult:
        """:meth:`go`, then :meth:`wait_for_state` (by default: until it stops again).

        Right after ``go`` the core can still read as stopped, before it has
        actually started. So when waiting for a stop and this client receives
        the backend's debug events, it first waits for the target-stopped
        event that follows the ``go``, then confirms the core state.
        """
        return self._run_and_wait(self.go, state, core=core, timeout=timeout, poll_interval=poll_interval, check=check)

    def _run_and_wait(
        self, action: Any, state: CoreState | int, *, core: int, timeout: float, poll_interval: float, check: bool
    ) -> WaitResult:
        events = self._client.events
        mark = events.mark()
        start = time.perf_counter()
        action()
        if int(state) == CoreState.STOPPED and events.receiving:
            events.wait_for(
                lambda kind, event: kind == "debug" and getattr(event, "note", None) == TARGET_STOPPED,
                after=mark,
                timeout=timeout,
            )
        remaining = max(0.0, timeout - (time.perf_counter() - start))
        return self.wait_for_state(state, core=core, timeout=remaining, poll_interval=poll_interval, check=check)

    # -- target data -----------------------------------------------------------

    def eval(
        self,
        expression: str,
        context: ContextLike = None,
        *,
        format: int | str = 0,
        dereference: bool = False,
    ) -> Any:
        """Evaluate a C expression; returns an ``ExprValue`` (``.value``, ``.type``, ...).

        ``format`` is an ``ExprFormat``: a number or a name such as ``"Hex"``.
        """
        if not expression.strip():
            raise CSpyError("expression must be non-empty")
        shared = self._client.idl.shared
        expr_format = format
        if expr_format in (0, "", None):
            expr_format = int(shared.ExprFormat.kDefault)
        return self._call(
            "evalExpression", context_ref(self._client, context), expression, [], expr_format, bool(dereference)
        )

    def modules(self) -> list[Any]:
        """Loaded modules (``ModuleData`` structs)."""
        return self._call("getModules")

    def load_module(self, filename: str) -> None:
        self._call("loadModule", str(filename))

    def threads(self) -> list[Any]:
        return self._call("getThreadList")

    def cycle_counter(self, core: int = 0) -> int:
        return int(self._call("getCycleCounter", int(core)))

    def zones(self) -> list[Any]:
        """Memory zones (``ZoneInfo`` structs); zone ids are what memory calls take."""
        return self._call("getAllZones")

    def registers(self, group: str = "CPU Registers (ABI)", limit: int = 64) -> RegisterSnapshot:
        """Metadata and values of the registers in ``group``.

        Falls back to the first group when ``group`` does not exist. A
        register whose value cannot be read has ``read_error`` set.
        """
        groups = list(self._call("getRegisterGroups") or [])
        if not groups:
            return RegisterSnapshot(group=group, requested_group=group, available_groups=[], total_in_group=0)

        selected = group if group in groups else groups[0]
        names = list(self._call("getLocationNamesInGroup", selected) or [])
        named_locations = [self._call("getNamedLocation", name) for name in names[: max(1, int(limit))]]

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

        ``configureSession``, ``startSMPSession`` and ``stopSession`` update
        :attr:`state` as the dedicated methods would.
        """
        try:
            result = self._client.call("debugger", method, *args, **kwargs)
        except Exception:  # noqa: BLE001
            if method == "configureSession":
                self._reset_state()
            elif method == "startSMPSession":
                self.set_state(started=False)
            elif method == "stopSession":
                self._reset_state()
            raise

        if method == "configureSession":
            self._client.clear_session_caches()
            self.set_state(configured=True, started=False)
        elif method == "startSMPSession":
            self.set_state(started=True)
        elif method == "stopSession":
            self._reset_state()
        return result

