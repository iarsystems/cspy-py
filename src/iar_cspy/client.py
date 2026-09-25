"""The client: one connection to a backend, and the root of the API.

Everything goes through :meth:`Client.call`, which resolves a service through
the registry, coerces arguments to the IDL types, invokes the RPC, and turns
failures into :class:`~iar_cspy.errors.CSpyError`. The domain objects hanging
off the client (``debugger``, ``breakpoints``, ``memory``, ...) are thin,
typed layers over that one method.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from thriftpy2.rpc import make_client

from . import _thrift
from .backend import Backend
from .breakpoints import Breakpoints
from .config import Config
from .context import ContextManager
from .debugger import Debugger
from .errors import (
    BackendError,
    CSpyError,
    classify,
    cspy_exception_info,
    describe_cspy_exception,
    error_entry,
    looks_like_backend_fault,
)
from .events import Events
from .ide_services import SERVICE_MANAGER_SERVICE, IdeServices
from .source import Disassembly, SourceLookup
from .listwindow import ListWindows
from .memory import Memory
from .options import Options
from .project import Project
from .registry import ServiceRegistry
from .terminal import Terminal

log = logging.getLogger("iar_cspy.rpc")

#: Service key -> (IDL file or None for the main cspy.thrift, service name).
SERVICES: dict[str, tuple[str | None, str]] = {
    "debugger": (None, "Debugger"),
    "contextmanager": (None, "ContextManager"),
    "breakpoints": ("breakpoints.thrift", "Breakpoints"),
    "memory": ("memory.thrift", "CSpyMemory"),
    "disassembly": ("disassembly.thrift", "Disassembly"),
    "sourcelookup": ("sourcelookup.thrift", "SourceLookup"),
    "libsupport": ("libsupport.thrift", "LibSupportService2"),
    "projectmanager": ("projectmanager.thrift", "ProjectManager"),
    "options": ("OptionsService.thrift", "OptionsService"),
    "servicemanager": ("ServiceManager.thrift", "CSpyServiceManager"),
}

#: Services hosted by IarServiceLauncher/iaride, started on demand.
_IDE_SERVICE_KEYS = {"projectmanager", "options"}

#: Services whose state lives in the debug session of one CSpyServer2 process.
_SESSION_SERVICE_KEYS = {"debugger", "contextmanager", "breakpoints", "memory", "disassembly", "sourcelookup"}


def _safe_repr(value: Any, max_len: int = 180) -> str:
    text = repr(value)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


class Client:
    """A connection to one C-SPY backend.

    >>> with iar_cspy.launch(iar_path="/opt/iar/ewarm") as client:
    ...     client.debugger.version()

    ``Client(Config.from_env())`` is the same, configured by the environment
    variables. A client owns the backend (and on :meth:`close` stops the
    processes it started) when it created the backend itself, i.e. was given
    a Config or nothing, unless ``owns_backend=False``. A :class:`Backend`
    passed in stays running unless ``owns_backend=True``.
    """

    def __init__(
        self,
        backend: Backend | Config | None = None,
        *,
        owns_backend: bool | None = None,
    ) -> None:
        created = backend is None or isinstance(backend, Config)
        if backend is None or isinstance(backend, Config):
            backend = Backend(backend)
        self.backend = backend
        self.owns_backend = created if owns_backend is None else bool(owns_backend)
        self.idl = _thrift.Idl(self.config.thrift_file, self.config.include_dirs)

        self.registry = ServiceRegistry(self)
        self.ide_services = IdeServices(self)
        self.events = Events(self)
        self.terminal = Terminal(self)
        self.listwindows = ListWindows(self)
        self.debugger = Debugger(self)
        self.breakpoints = Breakpoints(self)
        self.context = ContextManager(self)
        self.memory = Memory(self)
        self.disassembly = Disassembly(self)
        self.source = SourceLookup(self)
        self.project = Project(self)
        self.options = Options(self)
        self._callbacks_lock = threading.Lock()

    @property
    def config(self) -> Config:
        return self.backend.config

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Stop the callback services, and the backend if this client owns it."""
        self.events.server.stop()
        self.terminal.server.stop()
        self.listwindows.server.stop()
        if self.owns_backend:
            self.backend.shutdown()

    # -- RPC -------------------------------------------------------------------

    def service_class(self, service: str) -> Any:
        try:
            filename, name = SERVICES[service]
        except KeyError:
            raise CSpyError(f"Unknown service {service!r}; known: {', '.join(sorted(SERVICES))}") from None
        return self.idl.service(filename, name)

    def registry_name(self, service: str) -> str:
        if service == "debugger":
            return self.config.registry_service_name
        if service == "contextmanager":
            return "debugger.contextmanager"
        if service == "memory":
            return "debugger.memory"
        if service in _IDE_SERVICE_KEYS:
            return self.ide_services.registry_name(service)
        if service == "servicemanager":
            return SERVICE_MANAGER_SERVICE
        return service

    def endpoint(self, service: str) -> tuple[str, int]:
        """Resolve a service key to a (host, port) through the registry."""
        registry = self.backend.registry_endpoint()
        if registry is None:
            if service == "debugger":
                return self.config.host, self.config.port
            raise CSpyError(
                "A service registry (registry_port, THRIFT_REGISTRY_PORT) is required to "
                f"resolve service {self.registry_name(service)!r}"
            )
        return self.registry.resolve(self.registry_name(service), registry=registry)

    def call(self, service: str, method: str, *args: Any, timeout_ms: int | None = None, **kwargs: Any) -> Any:
        """Invoke ``method`` on a service, e.g. ``client.call("debugger", "go")``.

        ``service`` is a key of :data:`SERVICES`. Arguments may be JSON-shaped
        (dicts for structs, enum names as strings); they are coerced to the
        IDL types. Returns the raw thriftpy2 result.
        """
        service_cls = self.service_class(service)
        args, kwargs = _thrift.coerce_call_args(service_cls, method, args, kwargs)

        if service in _IDE_SERVICE_KEYS:
            self.ide_services.auto_ensure(service)
            try:
                host, port = self.endpoint(service)
            except CSpyError as exc:
                self.ide_services.invalidate(service)
                raise CSpyError(
                    f"Could not resolve the {service} service '{self.registry_name(service)}'. "
                    "It is hosted by IarServiceLauncher or a Thrift-enabled iaride, not by a "
                    f"standalone CSpyServer2. Underlying error: {exc}"
                ) from exc
        else:
            host, port = self.endpoint(service)
        if service in _SESSION_SERVICE_KEYS:
            self.debugger.check_backend(method)

        return self.invoke_at(service_cls, host, port, service, method, *args, timeout_ms=timeout_ms, **kwargs)

    def invoke_at(
        self,
        service_cls: Any,
        host: str,
        port: int,
        label: str,
        method: str,
        *args: Any,
        timeout_ms: int | None = None,
        before: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Open a connection to (host, port), call ``method``, close it.

        ``before(client)`` runs on the open connection first (used for the
        listwindow ``connect`` handshake).
        """
        client = self._connect(service_cls, host, port, label, timeout_ms)
        try:
            if before is not None:
                before(client)
            fn = getattr(client, method, None)
            if fn is None or not callable(fn):
                raise CSpyError(f"{service_cls.__name__} method not found: {method}")
            return self.traced(label, method, fn, *args, **kwargs)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _connect(self, service_cls: Any, host: str, port: int, label: str, timeout_ms: int | None = None) -> Any:
        try:
            return make_client(service_cls, host, port, timeout=timeout_ms or self.config.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise self._convert_error(label, "connect", exc, 0.0) from exc

    def traced(self, label: str, method: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Call ``fn`` with logging, converting failures to CSpyError."""
        if log.isEnabledFor(logging.DEBUG):
            args_preview = ", ".join(_safe_repr(a) for a in args)
            kwargs_preview = ", ".join(f"{k}={_safe_repr(v)}" for k, v in kwargs.items())
            log.debug("call %s.%s(%s)", label, method, ", ".join(x for x in (args_preview, kwargs_preview) if x))

        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except CSpyError:
            raise
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            raise self._convert_error(label, method, exc, elapsed_ms) from exc

        if log.isEnabledFor(logging.DEBUG):
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            log.debug("ok %s.%s in %.1fms -> %s", label, method, elapsed_ms, type(result).__name__)
        return result

    def _convert_error(self, label: str, method: str, exc: Exception, elapsed_ms: float) -> CSpyError:
        info = cspy_exception_info(exc)
        text = describe_cspy_exception(info) if info is not None else f"Thrift RPC failed: {type(exc).__name__}: {exc}"
        log.debug("error %s.%s after %.1fms: %s", label, method, elapsed_ms, _safe_repr(text))

        diagnostics = self.backend.diagnostics_text() if looks_like_backend_fault(f"{text} {exc}") else ""
        message = f"{text}\n{diagnostics}" if diagnostics else text
        if info is not None:
            return BackendError(message, info, diagnostics=diagnostics)
        return CSpyError(message, diagnostics=diagnostics)

    @contextmanager
    def connection(self, service: str) -> Iterator[Connection]:
        """One connection for many calls: ``with client.connection("memory") as mem: ...``.

        Calls on the yielded object work like :meth:`call`; ``.raw`` is the
        underlying thriftpy2 client, for calls that should not be converted.
        """
        service_cls = self.service_class(service)
        if service in _IDE_SERVICE_KEYS:
            self.ide_services.auto_ensure(service)
        host, port = self.endpoint(service)
        if service in _SESSION_SERVICE_KEYS:
            self.debugger.check_backend()
        conn = self._connect(service_cls, host, port, service)
        try:
            yield Connection(self, service, service_cls, conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def classify_error(self, exc: BaseException, fallback_code: str = "UNKNOWN_ERROR") -> dict[str, Any]:
        """:func:`iar_cspy.errors.classify` with this backend's diagnostics."""
        return classify(exc, fallback_code, diagnostics=self.backend.diagnostics)

    # -- conveniences ----------------------------------------------------------

    def raw(self, service: str) -> RawService:
        """Untyped access to any method of a service: ``client.raw("debugger").getAllZones()``."""
        self.service_class(service)  # validate the key early
        return RawService(self, service)

    def methods(self, service: str = "debugger") -> list[str]:
        """RPC method names of a service, from the IDL."""
        return _thrift.method_names(self.service_class(service))

    def ensure_callbacks(self) -> dict[str, Any]:
        """Register the event handler and libsupport services (done by configure/start)."""
        with self._callbacks_lock:
            return {"eventhandler": self.events.ensure_registered(), "libsupport": self.terminal.ensure_registered()}

    def clear_session_caches(self) -> None:
        """Forget per-session state that goes stale across reconfigure/stop."""
        self.listwindows.forget_connections()
        self.terminal.forget_exit_code()

    def capabilities(self) -> dict[str, Any]:
        """What the backend offers right now; never raises.

        Session state, processes, whether the target is online, the Debugger
        methods and, with a started session, the core count and registered
        services. A probe that failed is listed in ``errors`` instead.
        """
        state = self.debugger.state
        out: dict[str, Any] = {
            "backend_mode": self.backend.mode,
            "session": state,
            "backend_online": None,
            "core_count": None,
            "debugger_methods": [],
            "services": [],
            "errors": [],
        }
        out.update(self.backend.process_status())

        def probe(message: str, fn: Any) -> Any:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(
                    error_entry(
                        code="CAPABILITY_CHECK_FAILED",
                        category="discovery",
                        message=message,
                        retryable=True,
                        details=self.classify_error(exc),
                    )
                )
                return None

        out["backend_online"] = probe("isOnline failed", self.debugger.is_online)
        out["debugger_methods"] = probe("debugger_list_methods failed", self.debugger.methods) or []
        if state["started"]:
            out["core_count"] = probe("getNumberOfCores failed", self.debugger.core_count)
            out["services"] = probe("list services failed", self.registry.services) or []
        return out

    def info(self) -> dict[str, Any]:
        """Configuration plus backend process state (does not contact the backend)."""
        return {"config": self.config.to_dict(), "backend": self.backend.status()}


class Connection:
    """An open connection to one service; see :meth:`Client.connection`."""

    def __init__(self, client: Client, service: str, service_cls: Any, raw: Any) -> None:
        self._client = client
        self._service = service
        self._service_cls = service_cls
        self.raw = raw

    def __getattr__(self, method: str) -> Any:
        if method.startswith("_"):
            raise AttributeError(method)
        fn = getattr(self.raw, method)

        def call(*args: Any, **kwargs: Any) -> Any:
            args, kwargs = _thrift.coerce_call_args(self._service_cls, method, args, kwargs)
            return self._client.traced(self._service, method, fn, *args, **kwargs)

        call.__name__ = method
        return call


class RawService:
    """Attribute access to a service's RPCs, routed through :meth:`Client.call`."""

    def __init__(self, client: Client, service: str) -> None:
        self._client = client
        self._service = service

    def __getattr__(self, method: str) -> Any:
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._client.call(self._service, method, *args, **kwargs)

        call.__name__ = method
        return call

    def __dir__(self) -> list[str]:
        return self._client.methods(self._service)


def _given(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def connect(registry_port: int | None = None, registry_host: str | None = None, **config: Any) -> Client:
    """Connect to an already-running backend through its service registry.

    Arguments not given come from the environment (``THRIFT_REGISTRY_PORT``,
    ``THRIFT_REGISTRY_HOST``, ...; see :meth:`Config.from_env`). Nothing is
    started, and closing the client leaves the backend running.
    """
    cfg = Config.from_env(
        mode="standalone", **_given(registry_port=registry_port, registry_host=registry_host), **config
    )
    return Client(Backend(cfg), owns_backend=False)


def launch(iar_path: str | Path | None = None, **config: Any) -> Client:
    """Start a managed backend from an IAR installation; the client owns it.

    ``iar_path`` is the directory with ``common/bin`` under it; without it,
    ``IAR_INSTALL_PATH`` is used, and other settings not given also come from
    the environment (see :meth:`Config.from_env`). The processes start on
    first use (or call ``client.backend.start()``) and stop when the client is
    closed.
    """
    cfg = Config.from_env(mode="managed", **_given(iar_path=iar_path), **config)
    return Client(Backend(cfg), owns_backend=True)
