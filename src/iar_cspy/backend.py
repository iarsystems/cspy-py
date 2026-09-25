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
from pathlib import Path
from typing import Any

from thriftpy2.rpc import make_client

from . import _thrift
from ._process import ManagedProcess, is_reachable
from .config import Config
from .errors import CSpyError


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
            host=self.registry_host,
            start_timeout_ms=self.config.cspy_start_timeout_ms,
            restart_on_failure=self.config.cspy_restart_on_failure,
            terminate_timeout_s=3.0,
        )
        self.launcher_process = ManagedProcess(
            "IarServiceLauncher",
            host=self.registry_host,
            start_timeout_ms=self.config.launcher_start_timeout_ms,
            restart_on_failure=self.config.launcher_restart_on_failure,
            terminate_timeout_s=10.0,
        )
        #: IDE service key -> manifest handed to the launcher's ServiceManager.
        self.loaded_manifests: dict[str, str] = {}
        #: Incremented whenever a new IarServiceLauncher process is started.
        self.launcher_generation = 0
        atexit.register(self.shutdown)

    @classmethod
    def managed(cls, iar_path: str | Path | None = None, **config: Any) -> Backend:
        """A backend started from an IAR installation (the dir with common/bin)."""
        return cls(Config(mode="managed", iar_path=iar_path, **config))

    @classmethod
    def standalone(cls, registry_port: int, registry_host: str = "127.0.0.1", **config: Any) -> Backend:
        """A backend someone else runs, reached through its service registry."""
        return cls(Config(mode="standalone", registry_host=registry_host, registry_port=registry_port, **config))

    def __enter__(self) -> Backend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    # -- registry --------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def registry_host(self) -> str:
        return self.config.registry_host or "127.0.0.1"

    @property
    def has_launcher(self) -> bool:
        return self.mode == "managed" and self.config.launcher_executable is not None

    def registry_endpoint(self) -> tuple[str, int] | None:
        """The service registry to resolve services through.

        In managed mode this starts or restarts the processes as needed. None
        means there is no registry at all (standalone mode without a registry
        port), in which case only a direct debugger endpoint can be used.
        """
        if self.mode != "managed":
            if self.config.registry_port is None:
                return None
            return self.registry_host, int(self.config.registry_port)
        try:
            return self._ensure_managed()
        except Exception as exc:  # noqa: BLE001
            raise CSpyError(f"Failed to prepare the managed IAR backend: {exc}") from exc

    def current_registry_endpoint(self) -> tuple[str, int] | None:
        """The registry endpoint as of now, without starting anything."""
        if self.mode != "managed":
            return self.registry_endpoint()
        owner = self.launcher_process if self.has_launcher else self.debugger_process
        port = owner.registry_port
        return (self.registry_host, int(port)) if port else None

    def start(self) -> tuple[str, int] | None:
        """Bring the backend up now rather than on first use."""
        return self.registry_endpoint()

    def _ensure_managed(self) -> tuple[str, int]:
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
                    self.launcher_generation += 1
                    self.loaded_manifests.clear()
                if cfg.cspy_executable is None:
                    # No debugger at that path: project/options work, and the
                    # debugger fails to resolve with a clear registry error.
                    return self.registry_host, join_port

            exe = cfg.cspy_executable
            if exe is None:
                raise RuntimeError(
                    "No CSpyServer2 path. Pass iar_path (--iar-path, IAR_INSTALL_PATH) and it is "
                    "taken from <iar_path>/common/bin, or give the executable directly "
                    "(cspy_executable, THRIFT_CSPYSERVER_EXE)."
                )
            if join_port is not None and not self.debugger_process.healthy():
                self._prune_stale_registrations(join_port)
            port = self.debugger_process.ensure(exe, self.cspy_args(join_port))
            if join_port is not None and port != join_port:
                self.debugger_process.shutdown()
                raise RuntimeError(
                    f"CSpyServer2 was asked to join registry port {join_port} but reported "
                    f"port {port}; it did not attach to the IarServiceLauncher registry"
                )
            return self.registry_host, port

    def _prune_stale_registrations(self, port: int) -> None:
        """Deregister services whose endpoints are gone from the launcher's registry.

        A CSpyServer2 that joined the registry leaves its services registered
        when it exits. The next one registers them anew, but until it has,
        waitForService would hand out the dead endpoints of the previous one.
        """
        registry_cls = _thrift.Idl(self.config.thrift_file, self.config.include_dirs).registry.CSpyServiceRegistry
        try:
            client = make_client(registry_cls, self.registry_host, port, timeout=self.config.timeout_ms)
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
        and IDE services end up in a single registry.
        """
        if join_registry_port is None:
            return list(self.config.cspy_args)
        return ["-sockets", "-registry", str(int(join_registry_port))]

    # -- lifecycle -------------------------------------------------------------

    def restart_debugger(self) -> None:
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

    def diagnostics(self) -> dict[str, str]:
        """Recent process output to attach to a failure that looks like a crash."""
        return {
            "backend_diagnostics": self.debugger_process.diagnostics(),
            "service_launcher_diagnostics": self.launcher_process.diagnostics(),
        }

    def diagnostics_text(self) -> str:
        return "\n".join(text for text in self.diagnostics().values() if text)
