"""Test doubles: a Client whose RPCs are answered by a script, not a backend.

Useful for unit-testing code built on iar_cspy without an IAR installation::

    from iar_cspy.testing import fake_client

    client, rpc = fake_client(started=True)
    rpc.on("debugger", "getVersionString", "9.5.4")
    rpc.on("debugger", "evalExpression", rpc.struct("debugger", "evalExpression", value="42"))
    rpc.sequence("debugger", "getCoreState", 1, 1, 0)      # one response per call
    assert client.debugger.version() == "9.5.4"
    assert client.debugger.eval_int("counter") == 42
    assert rpc.methods() == ["getVersionString", "evalExpression"]

The fake replaces :meth:`Client.call`, the registry and the callback-service
registration; the real IDL is still loaded, so structs built by the package
(context references, locations) are genuine. Scripted exceptions go through
the client's real error conversion: a :func:`cspy_exception` surfaces as a
:class:`~iar_cspy.BackendError`, as it would from a backend.
"""

from __future__ import annotations

from typing import Any

from thriftpy2.thrift import TType

from ._thrift import _coerce_value
from .client import Client
from .config import Config
from .errors import CSpyError

_MISSING = object()


class FakeRpc:
    """Records calls and answers them from registered responses.

    A response is a value, an exception instance (raised), or a callable
    (called with the RPC arguments). An unexpected call raises AssertionError.
    Exceptions that are not already a CSpyError are converted the way
    :meth:`Client.call` converts them, once a client is attached (as
    :func:`fake_client` does).
    """

    def __init__(self, client: Client | None = None) -> None:
        self.client = client
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []
        self.responses: dict[tuple[str, str], Any] = {}

    def on(self, service: str, method: str, response: Any = None) -> FakeRpc:
        """Answer every call of ``service.method`` with ``response``."""
        self.responses[(service, method)] = response
        return self

    def sequence(self, service: str, method: str, *responses: Any) -> FakeRpc:
        """Answer successive calls with ``responses`` in turn; the last one repeats."""
        if not responses:
            raise ValueError("sequence() needs at least one response")
        queue = list(responses)

        def next_response(*args: Any, **kwargs: Any) -> Any:
            response = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(response, BaseException):
                raise response
            return response(*args, **kwargs) if callable(response) else response

        return self.on(service, method, next_response)

    def struct(self, service: str, method: str, **fields: Any) -> Any:
        """The struct ``service.method`` returns, built from ``fields``.

        Fields are given as in JSON (nested dicts for structs, enum names), e.g.
        ``rpc.struct("breakpoints", "getBreakpoint", id=1, ule="main", valid=True)``.
        """
        if self.client is None:
            raise RuntimeError("struct() needs the client: use fake_client()")
        result_cls = getattr(self.client.service_class(service), f"{method}_result", None)
        spec = getattr(result_cls, "thrift_spec", {}).get(0)
        if spec is None or spec[0] != TType.STRUCT:
            raise ValueError(f"{service}.{method} does not return a struct")
        return _coerce_value(fields, spec[0], spec[2], f"{method} result")

    def __call__(self, service: str, method: str, *args: Any, timeout_ms: int | None = None, **kwargs: Any) -> Any:
        if timeout_ms is not None:
            kwargs = {**kwargs, "timeout_ms": timeout_ms}
        self.calls.append((service, method, args, kwargs))
        response = self.responses.get((service, method), _MISSING)
        if response is _MISSING:
            raise AssertionError(f"Unexpected RPC {service}.{method}{args!r}")
        try:
            if isinstance(response, BaseException):
                raise response
            if callable(response):
                kwargs.pop("timeout_ms", None)
                return response(*args, **kwargs)
            return response
        except CSpyError:
            raise
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted as Client.call would
            if self.client is None:
                raise
            raise self.client._convert_error(service, method, exc, 0.0) from exc

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


def fake_client(
    config: Config | None = None, registry: list[str] | None = None, *, started: bool = False
) -> tuple[Client, FakeRpc]:
    """A Client wired to a :class:`FakeRpc` and a :class:`FakeRegistry`.

    The default config is standalone mode with a (fake) registry, so no
    process is ever started. ``started=True`` begins with a started debug
    session, for code that expects one.
    """
    client = Client(config or Config(mode="standalone", registry_port=4711))
    rpc = FakeRpc(client)
    client.call = rpc  # type: ignore[method-assign]
    client.registry = FakeRegistry(registry)  # type: ignore[assignment]
    client.ensure_callbacks = _no_callbacks  # type: ignore[method-assign]
    if started:
        client.debugger.set_state(configured=True, started=True)
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

