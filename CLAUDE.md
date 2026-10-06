# CLAUDE.md

## Project Summary
This repository is `iar-cspy` (import `iar_cspy`), a Python API that drives the
IAR C-SPY debugger (and the Embedded Workbench ProjectManager/OptionsService)
over its Thrift services. It starts or connects to the backend and wraps the
services in typed objects. It is used directly for scripts, pytest HIL tests,
and agent-written one-off scripts.

The MCP server, `iar-cspy-mcp` (https://github.com/iarsystems/cspy-mcp,
usually checked out next to this repository as `../cspy-mcp`), is a thin
adapter over this API. The dependency goes one way only: `iar_cspy` must never
import `mcp` or `iar_cspy_mcp` (enforced by `tests/test_package.py`).

## Key Files
`src/iar_cspy/`:
- `client.py`: `Client`, the root object. `Client.call(service, method, ...)`
  is the single RPC choke point (registry resolution, argument coercion,
  error conversion, tracing). Also `launch()`/`connect()`.
- `backend.py`: `Backend`, owning the managed processes (IarServiceLauncher
  plus CSpyServer2 joining its registry) or pointing at a standalone registry.
- `_process.py`: supervisor for one backend process (log capture, registry
  banner, restart, diagnostics).
- `config.py`: `Config` dataclass; `Config.from_env()` reads the `THRIFT_*`
  variables.
- `debugger.py`: session lifecycle (configure/start/stop/cleanup/handoff) and
  run control, eval, registers.
- `breakpoints.py`, `context.py`, `memory.py`, `source.py`, `terminal.py`,
  `events.py`, `listwindow.py`, `project.py`, `options.py`, `ide_services.py`:
  one module per service area.
- `errors.py`: `CSpyError` hierarchy, the stable error codes, `classify()`.
- `_thrift.py`: IDL loading, JSON-to-struct coercion, `to_plain`. The IDL is in `idl/`.
- `testing.py`: `fake_client()` / `FakeRpc` test doubles (public, also used
  by the MCP server's tests). Scripted exceptions go through the real error
  conversion; `fake_client(started=True)` starts with a session.
- `pytest_plugin.py`: `cspy_client` / `cspy_session` fixtures, `--cspy-*`
  options, the `live` marker. Registered through a `pytest11` entry point, so
  it loads wherever the package is installed (not through `pytest.ini`).
- `_session.py`: `requires_session`, the API's session guard.

Also: `examples/firmware/` is the simulator program + launch.json
used by the live tests and demos. `docs/` holds the API guide, IDE services
and backend notes.

## Dependency and Compatibility Notes
- `thriftpy2` loads the bundled IDL at runtime; it is the only dependency.
- The MCP server builds on the public API (and `iar_cspy.testing`). Its tool
  names, arguments and result shapes are a public contract for AI callers, so
  API changes that alter what it returns need a matching change there.

## Environment Contract
Everything has defaults; the IDL is bundled. The usual inputs:
- `IAR_INSTALL_PATH` / `--iar-path`: IAR installation (managed mode, default).
- `THRIFT_REGISTRY_HOST` / `THRIFT_REGISTRY_PORT` (+ `THRIFT_CSPYSERVER_MODE=standalone`):
  connect to a running backend.
See `.env.example` for the rest.

## Runtime Architecture
1. An API call (from a script, a test, or an MCP tool) reaches `Client.call`.
2. `Backend.registry_endpoint()` ensures the managed processes are up and returns the registry.
3. The service is resolved through the registry, and the RPC is made on a
   fresh connection.
4. Failures become `CSpyError`/`BackendError`, with backend diagnostics
   attached when they look like crashes.

Important behavior:
- The registry endpoint is not the debugger endpoint itself.
- The event handler and libsupport services are hosted by this process and
  registered on configure/start.
- One session per CSpyServer2 process: on stop, managed mode has it exit
  (`Debugger.exit()`, not the deprecated `stopSession`) and starts a fresh one. A
  replaced CSpyServer2 (`Backend.debugger_generation`) ends the session:
  `Client.call` raises `SessionError` for calls that needed it, as it does
  after the backend posts `kDkFatalError`.
- Session-dependent API methods raise `SessionError` without a started
  session; `debugger.attach()` adopts one another frontend started.
- `go_and_wait`/`run_to`/the steps wait for a target-started then a
  target-stopped event after the action. `runToULE` returns early, and the
  core reads "stopped" before it starts. They then wait (at most 0.5 s) for
  `kDkInspectionContextChanged`, which the backend posts after the stop.
- Target stdin reads (libsupport `requestInputBinary`) wait for
  `terminal.send()`, as at a console; ending the session releases them.

## Breakpoint API Guidance (Important)
`add_source(file, line)`, `add_code(location, mode)`, `add_data(location,
access)` and `add_log(location, message)` are the primary way to create
breakpoints: they build the ULE and a descriptor with the category the
session's driver uses (`_drivers.py`, ported from the VS Code extension's
`src/dap/breakpoints`). The driver comes from the configured launch
configuration, or `use_driver()` after `attach()`.
`client.breakpoints.add(ule, access)` takes a ULE and leaves the category to
the backend (simulator-safe, often rejected on emulators).
- `access`: `1` execute/fetch (code), `2` read, `3` write, `4` read/write
  (`iar_cspy.AccessType`).
- ULE forms: `main`, `func+4`, `0x100`, `{/abs/path/file.c}.123.1`
  (reliable source form), `<ule>@<size>`. `main()` and `file.c:123` are
  backend-dependent.
- `breakpoints.restore()` only takes
  descriptors returned by a listing. Descriptors are opaque, round-trip only,
  for callers; only `_drivers.py` writes them.

## Known Backend Issues
See `docs/backend-notes.md`. In short:
- Connection resets (`WinError 10054`) and the backend exiting around
  configure/start/stopSession; stopSession can assert in `-standalone -sockets` mode
  (managed mode ends sessions with `Debugger.exit()` instead, as cspy.thrift says).
- Multicore sessions need `--multicore_nr_of_cores=N` on the CSpyServer2 command
  line too (managed mode adds it).
- Headless: `ContextManager.getStack`/`getContextInfo` fail with "No such
  service: frontend" (no IDE frontend).
- Flash loading and license (`kDcLicenseViolation`) failures during start.
Failures may be backend lifecycle failures rather than API misuse.

## Useful Local Commands
```sh
pip install -r requirements-dev.txt                    # editable install + pytest
pytest                                                 # unit tests (live tests skip)
pytest -m live --cspy-iar-path=/opt/iar/ewarm          # live: managed, with IDE services
pytest -m live --cspy-cspyserver2=/path/CSpyServer2    # live: CSpyServer2 only
pytest examples/pytest --cspy-iar-path=/opt/iar/ewarm  # example firmware tests
python examples/scripts/live_demo.py --iar-path /opt/iar/ewarm
python -m compileall -q src tests examples
```
Use the `--opt=value` form for the `--cspy-*` paths: a separate path argument
confuses pytest's rootdir detection.

## Current Priority and Next Work
- Keep the API's errors actionable, for scripts and for AI callers through
  the MCP server.
- Behavior belongs in `iar_cspy`, so that scripts and the MCP tools behave
  the same.
- Preserve robust registry/eventhandler behavior across backend restarts.
- Open items are in `TODO.md` (breakpoint categories on emulators,
  stopSession crash reporting, a headless frontend service).

## Notes for Future Agents
- Do not assume backend stability; validate with short, focused call sequences.
- New backend behavior goes into `iar_cspy` with a unit test using
  `iar_cspy.testing.fake_client()`, and a live test when it is about backend
  semantics.
- Treat descriptor inputs as opaque round-trip values only.
- When re-raising an error with more context, use `errors.with_context(exc,
  message)` so the class (`BackendError`, ...) and the backend result code
  survive for `except` clauses and `classify()`.
- API results are dataclasses in `types.py`; the MCP server's tools shape them
  into their JSON. Changing an API result can change the MCP output, which is
  a public contract: check it in `../cspy-mcp` (its tests) as well.
