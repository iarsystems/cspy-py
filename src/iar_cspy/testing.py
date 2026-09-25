"""Test doubles: a Client whose RPCs are answered by a script, not a backend.

Useful for unit-testing code built on iar_cspy without an IAR installation::

    from iar_cspy.testing import fake_client

    client, rpc = fake_client()
    rpc.on("debugger", "getVersionString", "9.5.4")
    rpc.on("debugger", "getCoreState", lambda core: 0)
    assert client.debugger.version() == "9.5.4"
    assert rpc.methods() == ["getVersionString"]

The fake replaces :meth:`Client.call`, the registry and the callback-service
registration; the real IDL is still loaded, so structs built by the API
(context references, locations) are genuine.
"""

from __future__ import annotations

from typing import Any

from .client import Client
from .config import Config
from .errors import CSpyError

_MISSING = object()


class FakeRpc:
    """Records calls and answers them from registered responses.

    A response is a value, an exception instance (raised), or a callable
    (called with the RPC arguments). An unexpected call raises AssertionError.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []
        self.responses: dict[tuple[str, str], Any] = {}

    def on(self, service: str, method: str, response: Any = None) -> FakeRpc:
        self.responses[(service, method)] = response
        return self

    def __call__(self, service: str, method: str, *args: Any, timeout_ms: int | None = None, **kwargs: Any) -> Any:
        if timeout_ms is not None:
            kwargs = {**kwargs, "timeout_ms": timeout_ms}
        self.calls.append((service, method, args, kwargs))
        response = self.responses.get((service, method), _MISSING)
        if response is _MISSING:
            raise AssertionError(f"Unexpected RPC {service}.{method}{args!r}")
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            kwargs.pop("timeout_ms", None)
            return response(*args, **kwargs)
        return response

    def methods(self, service: str | None = None) -> list[str]:
        """Names of the methods called so far, in order."""
        return [m for s, m, _, _ in self.calls if service is None or s == service]

    def args(self, service: str, method: str) -> list[tuple[Any, ...]]:
        """Positional arguments of every call to one method."""
        return [a for s, m, a, _ in self.calls if (s, m) == (service, method)]


class FakeRegistry:
    """Stands in for :class:`~iar_cspy.registry.ServiceRegistry`."""

    def __init__(self, names: list[str] | None = None) -> None:
        self.entries: list[dict[str, Any]] = []
        self.registered: list[tuple[str, str, int]] = []
        self.set(names or [])

    def set(self, names: list[str]) -> None:
        """Replace the registered services with ``names``."""
        self.entries = [{"name": n, "host": "127.0.0.1", "port": 1, "protocol": 0, "transport": 0} for n in names]

    def services(self, name_filter: str = "") -> list[dict[str, Any]]:
        query = name_filter.strip().lower()
        return sorted((e for e in self.entries if query in e["name"].lower()), key=lambda e: e["name"])

    def lookup(self, name: str) -> dict[str, Any] | None:
        return next((e for e in self.entries if e["name"] == name), None)

    def resolve(self, name: str, *, registry: tuple[str, int] | None = None) -> tuple[str, int]:
        entry = self.lookup(name)
        if entry is None:
            raise CSpyError(f"Failed to resolve service '{name}' via registry: not registered")
        return entry["host"], entry["port"]

    def register(self, name: str, host: str, port: int) -> None:
        self.registered.append((name, host, port))


def fake_client(config: Config | None = None, registry: list[str] | None = None) -> tuple[Client, FakeRpc]:
    """A Client wired to a :class:`FakeRpc` and a :class:`FakeRegistry`.

    The default config is standalone mode with a (fake) registry, so no
    process is ever started.
    """
    client = Client(config or Config(mode="standalone", registry_port=4711))
    rpc = FakeRpc()
    client.call = rpc  # type: ignore[method-assign]
    client.registry = FakeRegistry(registry)  # type: ignore[assignment]
    client.ensure_callbacks = _no_callbacks  # type: ignore[method-assign]
    return client, rpc


def cspy_exception(
    code: int = 6, method: str = "DkStop", message: str = "Failed to suspend debugger", culprit: str = ""
) -> Exception:
    """A genuine ``shared.CSpyException``, as the backend raises it (default: kDcError from DkStop)."""
    from ._thrift import BUNDLED_IDL_DIR, load_thrift_module

    shared = load_thrift_module(str(BUNDLED_IDL_DIR / "shared.thrift"), (str(BUNDLED_IDL_DIR),))
    return shared.CSpyException(code=code, method=method, message=message, culprit=culprit)


def _no_callbacks() -> dict[str, Any]:
    return {"eventhandler": {"ok": True}, "libsupport": {"ok": True}}

