# Backend notes

Behavior of the C-SPY backend (CSpyServer2, IarServiceLauncher) observed in
live testing. The ones marked *handled* are dealt with by `iar_cspy`, and so
also by the MCP server. The others are useful to know when a call fails.

## Sessions

- **One session per CSpyServer2 process.** Per `cspy.thrift`, there is a
  single start/stop cycle per process. *Handled:* in managed mode,
  `stop_session()` shuts the process down and `configure_and_start()` always
  begins with a fresh one.
- **`stopSession` can fail benignly or crash.** A `DkStop` kDcError ("failed
  to suspend debugger") is reported when the target is already halted.
  *Handled:* treated as success when probing shows the cores are stopped. In
  `-standalone -sockets` mode stopSession has also been seen to hit a backend
  assertion and end the process; managed mode restarts it.
- **Calling stopSession in some lifecycle states can crash some builds.**
  `configure()` therefore does not stop anything first;
  `configure_and_start()` does a guarded handoff instead.
- **`stopOnSymbol` is a frontend contract.** CSpyServer2 never acts on it: the
  IDE and the VS Code adapter run to the symbol after start. *Handled:*
  `configure_and_start()` does it.
- **`runToULE` returns before the target gets there**, and right after `go`
  the core state can still read "stopped". *Handled:* `run_to()` and
  `go_and_wait()` wait for the backend's target-stopped debug event (this
  process hosts `debugger.eventhandler`), then confirm the core state. When
  another frontend owns the event handler (e.g. attaching to an IDE), they fall
  back to polling.
- **Some backends require a registered `debugger.eventhandler`** before
  configure/start succeed. *Handled:* registered automatically.

## Services

- **CSpyServer2 hosts only the debugger services.** ProjectManager and
  OptionsService need IarServiceLauncher or iaride; see
  [ide-services.md](ide-services.md).
- **Stale registrations in a shared registry.** A CSpyServer2 that joined the
  launcher's registry leaves its services registered when it exits.
  *Handled:* entries with unreachable endpoints are removed before a new
  CSpyServer2 joins.
- **A `frontend` service is expected by some calls.** Without an IDE
  frontend, `ContextManager.getStack`/`getContextInfo`, source lookup,
  disassembly at the current location, and `stepOver` with an explicit
  argument fail with `ServiceException: No such service,
  serviceName=frontend`. That service (`frontend.thrift`) is the IDE's UI:
  message boxes and file dialogs. So this is a limitation of running
  headless; against a Thrift-enabled `iaride` these calls work. The API
  leaves the step flag out unless you set it, which avoids the step failure.
  Hosting a headless `frontend` stand-in (as is done for the event handler)
  would likely unblock the rest, but it would have to answer the backend's
  prompts on the user's behalf.
- **Registry files.** CSpyServer2 and IarServiceLauncher write
  `CSpyServer2-ServiceRegistry.txt` into their working directory, which fails
  in a read-only installation. *Handled:* managed processes run in a private
  temporary directory.

## Values and formats

- **Hex values are digit-grouped**: `eval("x", format="Hex").value` gives
  `cafe'babe`. Strip the `'` before `int(..., 16)`.
- **Register group names differ per target.** `registers()` falls back to
  the first group when the requested one (default `CPU Registers (ABI)`) does
  not exist; `available_groups` lists them.
- **Breakpoint categories are translated**, e.g. `STD_CODE` becomes
  `STD_CODE2`. Emulator drivers have been seen rejecting the default
  categories, which leaves breakpoints effectively simulator-only there.
  `run_to(ule)` is an alternative for "stop at X".
- **The cycle counter** has been seen returning a negative, 32-bit truncated
  value on hardware.

## Transport

- Everything is loopback TCP (`-sockets`); the Python Thrift runtime cannot use
  Windows named pipes. Connection resets (`WinError 10054`) and refused
  connections during or after configure/start usually mean the backend process
  died. `exc.diagnostics` / `backend_diagnostics` show its last output, and a
  managed backend is restarted on the next call.
- Flash loading and license failures (`kDcLicenseViolation`; check
  `IAR_LMS_BEARER_TOKEN`) surface as `BackendError` from configure or start.
