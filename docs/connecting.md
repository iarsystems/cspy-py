# Connecting: finding a registry, and the find/use loop

How `iar_cspy` gets from "nothing" to a working RPC, for all three ways of
getting a backend, and how it notices and recovers when one goes away.

- [Three ways to get a backend](#three-ways-to-get-a-backend)
- [Find (outer loop) and use (inner loop)](#find-outer-loop-and-use-inner-loop)
- [Why a designated registry_uri isn't probed in registry_endpoint()](#why-a-designated-registry_uri-isnt-probed-in-registry_endpoint)
- [Noticing a replaced backend](#noticing-a-replaced-backend)
- [Inspecting the active config](#inspecting-the-active-config)

## Three ways to get a backend

1. **Start your own** (managed, `iar_cspy.launch()`): `Backend.launch_services()`
   starts (or restarts, if unhealthy) `IarServiceLauncher`/`CSpyServer2` from
   an IAR installation and returns their registry endpoint.
2. **Point at** a given host:port (standalone, `Config.registry_uri`, e.g.
   `iar_cspy.connect(51926)`): the registry of a backend someone else runs.
3. **Auto-discover** one (standalone, on by default, `discovery.py`): scan the
   `*.remote` files an IAR IDE writes (default `~/.iar/auto_discovery`, or the
   Windows equivalent) and use the first live one that answers. A bare
   standalone `Config`/`iar_cspy.connect()` does this whenever no
   `registry_uri` is given; set `auto_discovery_enabled=False` to opt out —
   see [guide.md](guide.md#attach-without-knowing-the-port).

`Config.mode` (`"managed"` vs `"standalone"`) plus whether `registry_uri` or
the `auto_discovery_*` fields are set is what selects between them; see
`config.py` for the exact fields.

## Find (outer loop) and use (inner loop)

Getting to a working connection, and staying connected, is two loops — the
same shape regardless of which of the three ways above is in play:

- **Find** (outer loop) — `Backend.registry_endpoint()`: one fresh attempt,
  tried again on every call, at whichever way this `Backend` is configured
  for. Returns `tuple[str, int] | None` and never raises, so a service-area
  module (`events.py`, `terminal.py`, `ide_services.py`, ...) checking
  "is there a registry right now" just gets `None`, no exception to handle.
- **Use it while it works** (inner loop) — ordinary `Client` RPCs
  (`client.debugger.go()`, ...). No special machinery: `Client.call` resolves
  a service through whatever `registry_endpoint()` currently returns and
  makes the RPC, oblivious to which of the three ways found it.
- **Possibly repeat** — if find comes up empty, or a resolve fails, the next
  call just runs find again. The one exception is discovery's result, which
  is cached once reachable (re-scanning the filesystem and re-probing on
  every RPC would be wasteful): `Backend.forget_registry()` drops that cache,
  and `Client.endpoint()` calls it reactively on a failed resolve — the
  "reconnect when disconnected" half of discovery. `Backend.wait_for_registry(timeout_s)`
  is the blocking version of find, for a caller that wants to wait rather
  than poll (`connect(discovery_timeout=...)`); managed mode and an explicit
  `registry_uri` have nothing to forget (managed already self-heals every
  call; a `registry_uri` is never cached in the first place).

The call sequence this supports:

1. A call (from a script, a test, or an MCP tool) reaches `Client.call`.
2. `Client.endpoint()` runs find and resolves the service through the
   registry it returns — or raises `NotConnectedError`, or falls back to the
   direct debugger endpoint (`Config.host`/`port`) for the one legacy case
   where nothing at all is configured (`Backend.registry_configured` is
   `False`).
3. The RPC is made on a fresh connection.
4. Failures become `CSpyError`/`BackendError`, with backend diagnostics
   attached when they look like crashes.

## Why a designated registry_uri isn't probed in registry_endpoint()

Managed mode and discovery both verify reachability as part of finding an
endpoint (`ManagedProcess.healthy()`; `is_reachable()` over discovery
candidates). A designated `registry_uri` deliberately does **not**: it is
returned unconditionally, exactly as given. Probing it on every call would
add a socket round-trip to every single RPC, and a lot of unit tests
construct a `Config(registry_uri=...)` with no real listener behind it,
relying on it being returned as-is rather than turned into `None`. Probing a
designated URI only happens in `wait_for_registry()`, which is opt-in
(`connect(discovery_timeout=...)`) — a real RPC against a dead designated
port fails clearly enough on its own otherwise.

## Noticing a replaced backend

`Debugger._session_identity()` is what detects "the backend behind this
session is gone" (`SessionError`, "the debug session is gone"), and it needs
two different signals, not one:

- `Backend.debugger_generation` (bumped only in managed mode, when a new
  CSpyServer2 process is started) catches a managed CSpyServer2 being
  replaced **even when it rejoins the same launcher-owned registry port** —
  confirmed with real processes that a CSpyServer2 joining a launcher's
  registry (`-registry <port>`) just echoes that port back rather than
  reporting one of its own, so the registry endpoint alone is unchanged
  across that restart (same port, different PID).
- The registry endpoint itself catches a standalone backend being replaced
  by a different one — e.g. a different discovered session — which the
  generation counter can't see (it stays `0` outside managed mode).

`IdeServices`'s registration cache (`ide_services.py`) only needs the plain
endpoint comparison: a restarted `IarServiceLauncher` always creates a fresh
registry rather than joining one, so a restart always does change the
endpoint — also confirmed empirically, which is why there's no equivalent
`launcher_generation` counter.

## Inspecting the active config

`Config.to_dict()` is a JSON-safe view of every field (`Path`s as strings,
tuples as lists) - for a settings object built from env vars, a launch.json,
or defaults layered with overrides, this is cheaper than re-deriving what was
actually resolved. `Client.info()` pairs it with `Backend.status()` (managed
process state, if any) in one dict: `{"config": ..., "backend": ...}`. Unlike
`debugger.status()`/`ide_services.status()` (session and service
diagnostics), `info()` never contacts the backend at all.
