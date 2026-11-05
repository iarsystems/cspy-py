"""The backend: the IAR processes that host the Thrift services.

Two modes:

managed
    This process starts and supervises the backend out of an IAR installation:
    an ``IarServiceLauncher`` that owns the service registry and hosts the IDE
    services (ProjectManager, OptionsService), plus a ``CSpyServer2`` that joins
    that registry with ``-registry <port>``. Every service then resolves
    through one registry. Programs the installation does not ship are not
    started: without a launcher, CSpyServer2 runs with its own registry.
    Unhealthy processes are restarted on the next use.

standalone
    Something else runs the backend (a Thrift-enabled ``iaride``, or a
    hand-started CSpyServer2/IarServiceLauncher); connect to its registry.
"""

from __future__ import annotations

import atexit
import threading
import time
from pathlib import Path
from typing import Any

from thriftpy2.rpc import make_client

from . import _thrift, discovery
from ._process import ManagedProcess, is_reachable
from .config import Config
from .errors import CSpyError, NotConnectedError
from .registry_uri import format_registry_uri, parse_registry_uri

#: How often :meth:`Backend.wait_for_registry` re-checks :meth:`Backend.registry_endpoint`.
WAIT_POLL_INTERVAL_S = 1.0

#: The driver option (and CSpyServer2 argument) giving a multicore session's core count.
MULTICORE_OPTION = "--multicore_nr_of_cores="

#: How long a managed CSpyServer2 gets to exit after ``Debugger.exit()``
#: before it is terminated (the VS Code extension waits as long).
DEBUGGER_EXIT_TIMEOUT_S = 15.0


def remote_control_opted_in(config: Config) -> bool:
    """Whether ``config`` asks for remote-control discovery at all.

    Opt-in only (``remote_control_enabled``, or giving a ``remote_control_workspace``/
    ``remote_control_dir``) — a bare standalone ``Config`` never scans the
    filesystem on its own.
    """
    return (
        config.remote_control_enabled
        or config.remote_control_workspace is not None
        or config.remote_control_dir is not None
    )


class Backend:
    """Owns (managed mode) or points at (standalone mode) the service registry.

    Use as a context manager, or call :meth:`shutdown`, to stop managed
    processes; they are also stopped at interpreter exit.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._lock = threading.RLock()
        self.debugger_process = ManagedProcess(
            "CSpyServer2",
            host=self._registry_host,
            start_timeout_ms=self.config.cspy_start_timeout_ms,
            restart_on_failure=self.config.cspy_restart_on_failure,
            terminate_timeout_s=3.0,
            request_exit=self._request_debugger_exit,
            exit_timeout_s=DEBUGGER_EXIT_TIMEOUT_S,
        )
        self.launcher_process = ManagedProcess(
            "IarServiceLauncher",
            host=self._registry_host,
            start_timeout_ms=self.config.launcher_start_timeout_ms,
            restart_on_failure=self.config.launcher_restart_on_failure,
            terminate_timeout_s=10.0,
        )
        #: IDE service key -> manifest handed to the launcher's ServiceManager.
        self.loaded_manifests: dict[str, str] = {}
        #: Incremented whenever a new CSpyServer2 process is started: a debug
        #: session does not survive into the next one. Needed alongside the
        #: registry endpoint for debugger.py's session tracking: a CSpyServer2
        #: that joins a launcher's registry just echoes that launcher's port
        #: back unchanged, so the endpoint alone can't tell it was replaced. A
        #: restarted launcher, in contrast, always gets a fresh port (it
        #: creates its own registry rather than joining one), so nothing
        #: equivalent is needed for the launcher/IDE-services side.
        self.debugger_generation = 0
        #: Cores the managed CSpyServer2 is started for; see :meth:`use_core_count`.
        self.debugger_cores = 1
        self._running_cores = 1
        #: A registry found through remote-control discovery, cached once reachable
        #: (like an explicit registry_uri, it is not re-validated per call).
        self._discovered_endpoint: tuple[str, int] | None = None
        atexit.register(self.shutdown)

    @classmethod
    def managed(cls, iar_path: str | Path | None = None, **config: Any) -> Backend:
        """A backend started from an IAR installation (the dir with common/bin)."""
        return cls(Config(mode="managed", iar_path=iar_path, **config))

    @classmethod
    def standalone(
        cls,
        registry_port: int | None = None,
        registry_host: str = "127.0.0.1",
        *,
        registry_uri: str | None = None,
        **config: Any,
    ) -> Backend:
        """A backend someone else runs, reached through its service registry."""
        if registry_port is not None:
            if registry_uri is not None:
                raise TypeError("standalone() got both registry_port and registry_uri; give one")
            registry_uri = format_registry_uri(registry_host, registry_port)
        elif registry_uri is None:
            raise TypeError("standalone() needs registry_port or registry_uri")
        return cls(Config(mode="standalone", registry_uri=registry_uri, **config))

    def __enter__(self) -> Backend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    # -- registry --------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def _registry_host(self) -> str:
        """The host this backend's own managed processes bind their registry to.

        Unrelated to a standalone target's host, which is part of
        ``Config.registry_uri`` — this is only ever loopback in practice, and
        private: nothing outside this class should care which host its own
        managed processes happen to bind to.
        """
        return "127.0.0.1"

    @property
    def has_launcher(self) -> bool:
        return self.mode == "managed" and self.config.launcher_executable is not None

    @property
    def registry_configured(self) -> bool:
        """Whether a registry (or a managed backend) is configured at all.

        A static fact, not a live check — used only to decide the one
        legitimate case for a direct ``Config.host``/``port`` debugger
        endpoint (:meth:`~iar_cspy.Client.endpoint`), and to short-circuit
        :meth:`wait_for_registry` when there is nothing to wait for.
        """
        return self.mode == "managed" or self.config.registry_uri is not None or remote_control_opted_in(self.config)

    def registry_endpoint(self) -> tuple[str, int] | None:
        """The service registry to resolve services through: find it, fresh, every call.

        Three ways to find one, one tried per ``Config``:
          - managed: start or restart the managed processes as needed.
          - a designated ``registry_uri``: used unconditionally, no reachability
            check (a real RPC against it fails clearly enough if it's down; see
            :meth:`wait_for_registry` for a version that does wait for it).
          - remote-control discovery (see :mod:`iar_cspy.discovery`), opted
            into via ``remote_control_enabled``/``remote_control_workspace``/
            ``remote_control_dir`` — a bare standalone Config never scans the
            filesystem on its own.

        None means no registry could be found at all right now. A discovered
        endpoint is cached once reachable (re-scanning and re-probing on every
        RPC would be wasteful); call :meth:`forget_registry` to drop that
        cache once it's known to be stale - :meth:`~iar_cspy.Client.endpoint`
        does this on a failed resolve, so the *next* call looks again rather
        than repeating the same dead attempt forever. A designated
        ``registry_uri`` or a managed backend have nothing to forget: the
        former is never cached, the latter already self-heals every call.
        """
        if self.mode != "managed":
            if self.config.registry_uri is not None:
                return parse_registry_uri(self.config.registry_uri)
            if remote_control_opted_in(self.config):
                return self._discover_registry()
            return None
        try:
            return self.launch_services()
        except Exception as exc:  # noqa: BLE001
            raise CSpyError(f"Failed to prepare the managed IAR backend: {exc}") from exc

    def wait_for_registry(self, timeout_s: float, poll_interval_s: float = WAIT_POLL_INTERVAL_S) -> tuple[str, int]:
        """Block until :meth:`registry_endpoint` answers, or raise :class:`NotConnectedError`.

        Unlike ``registry_endpoint()``, a designated ``registry_uri`` is
        actively probed for reachability here - this is the one place that
        happens, used by ``connect(discovery_timeout=...)`` rather than on
        every RPC (which would turn a merely slow backend into a slow call,
        and would make a lot of unit tests that point at a Config with no
        real listener behind it return None instead of the plain endpoint).
        """
        if not self.registry_configured:
            raise NotConnectedError(
                "no registry configured (registry_uri, remote-control discovery) and no "
                "direct debugger endpoint override"
            )
        if self.mode == "managed":
            return self.registry_endpoint()

        deadline = time.monotonic() + timeout_s
        while True:
            if self.config.registry_uri is not None:
                endpoint = parse_registry_uri(self.config.registry_uri)
                if is_reachable(*endpoint):
                    return endpoint
            else:
                endpoint = self._discover_registry()
                if endpoint is not None:
                    return endpoint
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NotConnectedError(f"Timed out after {timeout_s:g}s waiting for a registry")
            time.sleep(min(poll_interval_s, remaining))

    def _discover_registry(self) -> tuple[str, int] | None:
        if self._discovered_endpoint is not None:
            return self._discovered_endpoint
        cfg = self.config
        for candidate in discovery.discover(
            cfg.remote_control_workspace,
            cfg.remote_control_dir,
            require_alive=cfg.remote_control_require_alive,
        ):
            endpoint = parse_registry_uri(candidate.registry_uri)
            if is_reachable(*endpoint):
                self._discovered_endpoint = endpoint
                return endpoint
        return None

    def forget_registry(self) -> None:
        """Drop a cached discovery result, so the next lookup looks again.

        Call after a service registry at a previously-resolved endpoint turns
        out to be gone (the IDE closed, a hand-started CSpyServer2 exited) -
        :meth:`~iar_cspy.Client.endpoint` does this on a failed resolve. A
        no-op in managed mode or with an explicit ``registry_uri`` (nothing
        cached to forget either way).
        """
        self._discovered_endpoint = None

    def current_registry_endpoint(self) -> tuple[str, int] | None:
        """The registry endpoint as of now, without starting anything."""
        if self.mode != "managed":
            return self.registry_endpoint()
        owner = self.launcher_process if self.has_launcher else self.debugger_process
        port = owner.registry_port
        return (self._registry_host, int(port)) if port else None

    def start(self) -> tuple[str, int] | None:
        """Bring the backend up now rather than on first use."""
        return self.registry_endpoint()

    def launch_services(self) -> tuple[str, int]:
        """Start the managed processes (if not already) and return their registry endpoint.

        The "start your own" find-strategy: restarts an unhealthy process on
        every call (``ManagedProcess.ensure()``/``healthy()``), same as the
        other two strategies get re-tried fresh by :meth:`registry_endpoint`
        on every call.
        """
        cfg = self.config
        with self._lock:
            join_port: int | None = None
            if cfg.launcher_executable is not None:
                # An IarServiceLauncher is available, so it hosts the IDE
                # services and owns the registry: it comes up first and its
                # endpoint is what everything resolves through.
                before = self.launcher_process.process
                join_port = self.launcher_process.ensure(
                    cfg.launcher_executable, ["-standalone", "-sockets"]
                )
                if self.launcher_process.process is not before:
                    # A new launcher hosts none of the services the old one did.
                    self.loaded_manifests.clear()
                if cfg.cspy_executable is None:
                    # No debugger at that path: project/options work, and the
                    # debugger fails to resolve with a clear registry error.
                    return self._registry_host, join_port

            exe = cfg.cspy_executable
            if exe is None:
                raise RuntimeError(
                    "No CSpyServer2 path. Pass iar_path (--iar-path, IAR_INSTALL_PATH) and it is "
                    "taken from <iar_path>/common/bin, or give the executable directly "
                    "(cspy_executable, THRIFT_CSPYSERVER_EXE)."
                )
            if join_port is not None and not self.debugger_process.healthy():
                self._prune_stale_registrations(join_port)
            before = self.debugger_process.process
            cores = self.debugger_cores
            port = self.debugger_process.ensure(exe, self.cspy_args(join_port))
            if self.debugger_process.process is not before:
                self.debugger_generation += 1
                self._running_cores = cores
            if join_port is not None and port != join_port:
                self.debugger_process.shutdown()
                raise RuntimeError(
                    f"CSpyServer2 was asked to join registry port {join_port} but reported "
                    f"port {port}; it did not attach to the IarServiceLauncher registry"
                )
            return self._registry_host, port

    def _prune_stale_registrations(self, port: int) -> None:
        """Deregister services whose endpoints are gone from the launcher's registry.

        A CSpyServer2 that joined the registry leaves its services registered
        when it exits. The next one registers them anew, but until it has,
        waitForService would hand out the dead endpoints of the previous one.
        """
        registry_cls = _thrift.Idl(self.config.thrift_file, self.config.include_dirs).registry.CSpyServiceRegistry
        try:
            client = make_client(registry_cls, self._registry_host, port, timeout=self.config.timeout_ms)
        except Exception:  # noqa: BLE001 - best effort; resolution reports real problems
            return
        try:
            for name, location in client.getServices().items():
                if int(location.transport) == 0 and not is_reachable(location.host, location.port, timeout_s=0.3):
                    client.deregisterService(name)
        except Exception:  # noqa: BLE001
            pass
        finally:
            client.close()

    def cspy_args(self, join_registry_port: int | None) -> list[str]:
        """Arguments for CSpyServer2.

        When an IarServiceLauncher owns the registry, CSpyServer2 must not
        start one of its own: it joins with ``-registry <port>``, so debugger
        and IDE services end up in a single registry. A multicore session
        needs the core count on the command line as well (see
        :meth:`use_core_count`).
        """
        if join_registry_port is None:
            args = list(self.config.cspy_args)
        else:
            args = ["-sockets", "-registry", str(int(join_registry_port))]
        if self.debugger_cores > 1 and not any(arg.startswith(MULTICORE_OPTION) for arg in args):
            args.append(f"{MULTICORE_OPTION}{self.debugger_cores}")
        return args

    def use_core_count(self, cores: int) -> bool:
        """Have the managed CSpyServer2 run with ``cores`` cores; True if it was restarted for it.

        CSpyServer2 takes the core count of a multicore session
        (``--multicore_nr_of_cores=N`` in the driver options) on its command
        line, not only from the session configuration: without it,
        configuring the session fails ("failed to configure debugger driver").
        A running process started for a different count is shut down, so the
        next use starts one with the right arguments. Does nothing in
        standalone mode.
        """
        cores = max(1, int(cores))
        if self.mode != "managed":
            return False
        with self._lock:
            self.debugger_cores = cores
            if self.debugger_process.running and self._running_cores != cores:
                self.debugger_process.shutdown()
                return True
        return False

    def _request_debugger_exit(self) -> bool:
        """Ask the managed CSpyServer2 to exit through ``Debugger.exit()``; False if it could not be asked.

        That is the shutdown cspy.thrift prescribes: it ends the session
        (``stopSession`` is deprecated for clients) and lets the driver
        disconnect from the target, where terminating the process would not.
        """
        port = self.debugger_process.registry_port
        if port is None:
            return False
        idl = _thrift.Idl(self.config.thrift_file, self.config.include_dirs)
        registry = make_client(idl.registry.CSpyServiceRegistry, self._registry_host, int(port), timeout=2000)
        try:
            location = registry.waitForService(self.config.registry_service_name, 1000)
        finally:
            registry.close()
        timeout_ms = int(DEBUGGER_EXIT_TIMEOUT_S * 1000)
        debugger = make_client(idl.service(None, "Debugger"), location.host, int(location.port), timeout=timeout_ms)
        try:
            debugger.exit()
        except Exception:  # noqa: BLE001 - the process may drop the connection as it exits
            pass
        finally:
            debugger.close()
        return True

    # -- lifecycle -------------------------------------------------------------

    def stop_debugger(self) -> None:
        """Stop the managed CSpyServer2 so the next use starts a fresh one.

        CSpyServer2 supports a single start/stop session cycle per process, so
        this is how a new debug session gets a clean backend. The launcher and
        the IDE services it hosts are left running.
        """
        if self.mode == "managed":
            self.debugger_process.shutdown()

    def stop_launcher(self) -> None:
        """Stop the managed IarServiceLauncher (and with it the IDE services).

        In managed mode the launcher owns the registry, so the managed
        CSpyServer2 loses it too; both are restarted on the next use.
        """
        self.launcher_process.shutdown()
        self.loaded_manifests.clear()

    def shutdown(self) -> None:
        """Stop every process this backend started. Idempotent."""
        self.debugger_process.shutdown()
        self.launcher_process.shutdown()
        self.loaded_manifests.clear()

    # -- introspection ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Process state for both managed programs."""
        out: dict[str, Any] = {"mode": self.mode}
        if self.mode == "managed":
            out["debugger"] = self.debugger_process.status()
        if self.config.launcher_executable is not None:
            launcher = self.launcher_process.status()
            launcher["loaded_manifests"] = dict(self.loaded_manifests)
            out["service_launcher"] = launcher
        return out

    def process_status(self) -> dict[str, Any]:
        """The ``managed_server`` / ``service_launcher`` entries of the status reports."""
        processes = self.status()
        out: dict[str, Any] = {}
        if "debugger" in processes:
            out["managed_server"] = {"mode": "managed", **processes["debugger"]}
        if "service_launcher" in processes:
            out["service_launcher"] = processes["service_launcher"]
        return out

    def diagnostics(self) -> dict[str, str]:
        """Recent process output to attach to a failure that looks like a crash."""
        return {
            "backend_diagnostics": self.debugger_process.diagnostics(),
            "service_launcher_diagnostics": self.launcher_process.diagnostics(),
        }

    def diagnostics_text(self) -> str:
        return "\n".join(text for text in self.diagnostics().values() if text)
