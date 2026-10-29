# Backend notes

Behavior of the C-SPY backend (CSpyServer2, IarServiceLauncher) observed in
live testing. The ones marked *handled* are dealt with by `iar_cspy`, and so
also by the MCP server. The others are useful to know when a call fails.

## Sessions

- **One session per CSpyServer2 process.** Per `cspy.thrift`, there is a
  single start/stop cycle per process. *Handled:* in managed mode,
  `stop_session()` shuts the process down and `configure_and_start()` always
  begins with a fresh one.
- **Clients end a session with `Debugger.exit()`, not `stopSession`.**
  cspy.thrift deprecates calling `stopSession` directly: `exit()` stops the
  session itself, and CSpyServer2 then exits (code 0). The VS Code extension
  never calls `stopSession`. *Handled:* in managed mode `stop_session()` and
  `cleanup()` ask the process to exit that way and wait up to 15 s before
  terminating it. Terminating instead would skip the driver's disconnect,
  and on Windows it is `TerminateProcess`.
- **`stopSession` can fail benignly or crash.** A `DkStop` kDcError ("failed
  to suspend debugger") is reported when the target is already halted.
  *Handled* (standalone mode, where `stop_session()` still uses it, because
  the process is not ours to exit): treated as success when probing shows the
  cores are stopped. In `-standalone -sockets` mode stopSession has also been
  seen to hit a backend assertion and end the process (not reproduced with
  EWARM 10.10).
- **Calling stopSession in some lifecycle states can crash some builds.**
  `configure()` therefore does not stop anything first;
  `configure_and_start()` does a guarded handoff instead.
- **`stopOnSymbol` is a frontend contract.** CSpyServer2 never acts on it: the
  IDE and the VS Code adapter run to the symbol after start. *Handled:*
  `configure_and_start()` does it.
- **`runToULE` and the steps return before the target gets there**, and
  right after `go` the core state can still read "stopped". The backend posts
  a target-started and then a target-stopped debug event for every run and
  step. *Handled:* `run_to()`, `go_and_wait()` and the steps wait for that
  pair (this process hosts `debugger.eventhandler`), then confirm the core
  state; a stop event left over from an earlier halt does not count. When
  another frontend owns the event handler (e.g. attaching to an IDE), they fall
  back to polling.
- **A stop is posted before the inspection context has moved.** The
  target-stopped event arrives some 30-50 ms before
  `kDkInspectionContextChanged`, which marks the variable windows starting
  to update for the new location. *Handled:* the waits above then wait for
  that event too (at most 0.5 s more), as the VS Code extension does.
- **A fatal error ends the session.** The backend logs the reason ("Fatal
  error: ... Session aborted!") and posts `kDkFatalError` (or
  `kDkSilentFatalError`). *Handled:* a wait in progress ends right away, and
  the next session call raises `SessionError` with that message.
- **Multicore sessions need the core count on the CSpyServer2 command
  line.** With `--multicore_nr_of_cores=N` (N > 1) in the driver options,
  configuring fails with `kDcFatalError`, "failed to configure debugger
  driver" ("Unknown exception in driver (SD2)"), unless CSpyServer2 was also
  started with that argument. *Handled:* in managed mode `configure()` starts
  CSpyServer2 with the configuration's core count. In standalone mode the
  configure error says what is missing.
- **IDE platforms before 9.5 have no launch-configuration API.** The
  CSpyServer2 of EWARM 9.70.4 (platform 9.4.7) answers
  `resolveLaunchConfiguration`, `configureSession` and `startSMPSession`
  with `TApplicationException: Invalid method name`. The VS Code extension
  starts those with its legacy init sequence. *Handled:* `configure()` falls
  back to it when resolving finds no such method, and `start()` then calls
  `startSession`, the device macros, `flashModule`, the setup macros and
  `loadModuleWithOptions`. That needs the installation's driver libraries:
  the managed backend's `iar_path`, or `workbenchPath` in the launch
  configuration.
- **On those platforms a pause leaves registers empty unless a context
  window is open.** Seen on 9.4.7 with a J-Link target: after `stop()` (or
  an attach, which halts the core the same way) the core is halted, but
  every register reads back empty and the context is `[PC = 0x0]` until the
  next breakpoint; a simulator is not affected. The IDE and the VS Code
  extension always have the call stack and register windows open, and with
  those connected the context follows every stop. *Handled:* in managed
  mode the legacy start connects `WIN_CALL_STACK` and `WIN_REGISTER_1`. The
  halt an attach itself makes still reads empty; the next stop does not.
- **A list window keeps calling the frontend that connected to it.** A
  process that connected one and then exited leaves CSpyServer2 calling a
  dead listener, and it has been seen to crash on the next `go`.
- **A crashed CSpyServer2 takes the session with it.** *Handled:* in managed
  mode it is replaced on the next call, and calls that needed the old session
  raise `SessionError` with its last output, rather than failing against a
  process that has no session.
- **Some backends require a registered `debugger.eventhandler`** before
  configure/start succeed. *Handled:* registered automatically.
- **The target's stdin reads ask the frontend for input** through
  `libsupport` (`requestInputBinary`, one byte at a time for `getchar`). An
  empty answer is end-of-file to the program. *Handled:* like a console (and
  like the IDE and the VS Code extension), the read waits until
  `terminal.send()` queues input, however long that takes; the backend does
  not time it out. The target can be halted meanwhile. Ending the session, or
  `terminal.cancel_input()`, answers it with end-of-file.

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
  headless; against a Thrift-enabled `iaride` these calls work. The package
  leaves the step flag out unless you set it, which avoids the step failure.
  Hosting a headless `frontend` stand-in (as is done for the event handler)
  would likely unblock the rest, but it would have to answer the backend's
  prompts on the user's behalf.
- **Registry files.** CSpyServer2 and IarServiceLauncher write
  `CSpyServer2-ServiceRegistry.txt` into their working directory, which fails
  in a read-only installation. *Handled:* managed processes run in a private
  temporary directory.

## Values and formats

- **Values are text, digit-grouped, and formatted per type**:
  `eval("x", format="Hex").value` gives `cafe'babe` (no `0x` unless
  `prefix=True`), a pointer shows as `g_phase (2000'0054)` and a `char` as
  `A`. *Handled:* `eval_int()` asks for decimal and parses that.
- **Register group names differ per target.** `registers()` falls back to
  the first group when the requested one (default `CPU Registers (ABI)`) does
  not exist; `available_groups` lists them.
- **Created breakpoints report `accessType` 0.** A code breakpoint set with
  `AccessType.FETCH` (1) reads back as `accessType=0`, which is not a
  `shared.AccessType` value. Pass 1-4 when creating; do not branch on the
  value read back.
- **`removeBreakpoint` reports failure by returning false**, not by
  raising. *Handled:* `breakpoints.clear()` raises for the ones it could not
  remove; `remove()` returns the flag.
- **Breakpoint categories are translated**, e.g. `STD_CODE` becomes
  `STD_CODE2`. Emulator drivers have been seen rejecting the default
  categories, which leaves breakpoints effectively simulator-only there.
  Explicit category ids outside the backend's translation map are dropped,
  so `setBreakpointOnUleWithCategory` cannot set `EMUL_CODE` either.
  *Handled:* `add_source`/`add_code`/`add_data`/`add_log` set breakpoints
  from a descriptor with the driver's category, as the VS Code extension
  does. `run_to(ule)` is an alternative for "stop at X".
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
