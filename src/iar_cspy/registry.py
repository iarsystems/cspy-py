"""The C-SPY service registry: where every backend service is published."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import BackendUnavailableError, CSpyError, classify, with_context

#: classify() codes meaning the registry itself stopped answering (a slow one,
#: TIMEOUT, is not "gone").
_UNREACHABLE_CODES = {"TRANSPORT_CONNECTION_REFUSED", "TRANSPORT_CONNECTION_RESET"}

if TYPE_CHECKING:
    from .client import Client


class ServiceRegistry:
    """Lookup and registration of services (``client.registry``)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def _endpoint(self, registry: tuple[str, int] | None = None) -> tuple[str, int]:
        endpoint = registry or self._client.backend.registry_endpoint()
        if endpoint is None:
            raise self._client.backend.unavailable(
                "A service registry (registry_uri, THRIFT_REGISTRY_URI) is required to query services"
            )
        return endpoint

    def _call(self, method: str, *args: Any, registry: tuple[str, int] | None = None) -> Any:
        host, port = self._endpoint(registry)
        service_cls = self._client.idl.registry.CSpyServiceRegistry
        try:
            return self._client.invoke_at(service_cls, host, port, "registry", method, *args)
        except CSpyError as exc:
            # A cached discovered endpoint (registry_endpoint()'s "find" step)
            # can be stale even when callers resolve services directly through
            # here instead of Client.endpoint() - forget it uniformly so the
            # next call rediscovers (see TODO.md #22).
            backend = self._client.backend
            backend.forget_registry()
            if isinstance(exc, BackendUnavailableError) or classify(exc).get("code") not in _UNREACHABLE_CODES:
                raise
            # The registry itself is not answering: say what that means.
            diagnostics = exc.diagnostics
            reason = str(exc).replace(diagnostics, "").strip() if diagnostics else str(exc)
            raise backend.unavailable(
                f"The service registry at {host}:{port} is unreachable: {reason}",
                reached=True,
                diagnostics=diagnostics,
            ) from exc

    def services(self, name_filter: str = "") -> list[dict[str, Any]]:
        """Registered services, sorted by name; ``name_filter`` is a substring match.

        Each entry has ``name``, ``host``, ``port``, ``protocol`` and
        ``transport`` (0 = socket).
        """
        services = self._call("getServices")
        query = name_filter.strip().lower()
        out: list[dict[str, Any]] = []
        for name, loc in services.items():
            if query and query not in str(name).lower():
                continue
            out.append(
                {
                    "name": str(name),
                    "host": str(getattr(loc, "host", "")),
                    "port": int(getattr(loc, "port", 0) or 0),
                    "protocol": int(getattr(loc, "protocol", -1)),
                    "transport": int(getattr(loc, "transport", -1)),
                }
            )
        return sorted(out, key=lambda x: x["name"])

    def lookup(self, name: str) -> dict[str, Any] | None:
        """The registry entry for ``name``, or None; does not wait."""
        return next((entry for entry in self.services() if entry["name"] == name), None)

    def resolve(self, name: str, *, registry: tuple[str, int] | None = None) -> tuple[str, int]:
        """(host, port) of a service, waiting up to the client timeout for it to appear."""
        host, port = self._endpoint(registry)
        timeout_ms = self._client.config.timeout_ms
        try:
            location = self._call("waitForService", name, timeout_ms, registry=(host, port))
        except BackendUnavailableError:
            raise  # already names the registry address, and carries the hint
        except Exception as exc:  # noqa: BLE001
            raise with_context(exc, f"Failed to resolve service '{name}' via registry at {host}:{port}: {exc}") from exc

        transport = getattr(location, "transport", None)
        if int(transport) != 0:
            raise CSpyError(
                "Registry resolved a non-socket endpoint. Only socket endpoints are supported. "
                f"Resolved host={getattr(location, 'host', '')}, transport={transport}."
            )
        return str(location.host), int(location.port)

    def register(self, name: str, host: str, port: int) -> None:
        """Publish a (binary protocol, socket transport) service under ``name``."""
        mod = self._client.idl.registry
        location = mod.ServiceLocation(
            host=host, port=int(port), protocol=mod.Protocol.Binary, transport=mod.Transport.Socket
        )
        self._call("registerService", name, location)
