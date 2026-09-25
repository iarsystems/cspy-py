# iar-cspy: Python API for IAR C-SPY

> **Experimental.** The `iar_cspy` Python API is experimental. IAR makes no
> guarantees about its stability: it may change incompatibly, or be removed,
> in any release and without notice.

A typed Python API for the IAR C-SPY debugger and the Embedded Workbench IDE
services, over their Thrift interfaces. Use it to script debug sessions, write
hardware-in-the-loop tests, or let an AI agent do multi-step debugging in one
script. It is also what the [MCP server](https://github.com/iarsystems/cspy-mcp)
is built on.

[![CI](https://github.com/iarsystems/cspy-py/actions/workflows/ci.yml/badge.svg)](https://github.com/iarsystems/cspy-py/actions/workflows/ci.yml)

```sh
pip install "iar-cspy @ git+https://github.com/iarsystems/cspy-py"
```

Add `@<branch, tag or commit>` after the repository URL to pin a version, and
`[test]` after `iar-cspy` for pytest. From a clone of this repository, use
`pip install .`.

Besides Python 3.10+ on Linux or Windows, you need either an IAR installation
or an already-running backend to connect to. The installation is the
directory with `common/bin` under it (for example IAR Embedded Workbench for
Arm), and it needs to ship `CSpyServer2`; the backend is then started for
you. The project and options features also need `IarServiceLauncher`.
To debug a program you also need a launch configuration (see below).

The only dependency is `thriftpy2`; the Thrift IDL ships with the package.

Licensed under the [MIT License](LICENSE).

## Quick start

```python
import iar_cspy as cspy

with cspy.launch("/opt/iar/ewarm") as client:              # managed backend from an installation
    with client.debugger.session("launch.json") as dbg:   # configure, start, run to stopOnSymbol
        bp = client.breakpoints.add("process_packet")
        dbg.go_and_wait(timeout=10)                        # run until it stops again

        print(dbg.eval_int("len"))                         # dbg.eval("len") has .value, .type, ...
        print(client.memory.read(dbg.eval("rx_buf"), 16).hex())
        print(hex(dbg.registers()["PC"].value))
```

`launch()` without a path uses `IAR_INSTALL_PATH`. To use a backend that is
already running (a Thrift-enabled `iaride`, or a CSpyServer2 you started),
connect to its service registry instead; see
[Backend modes](#backend-modes) for the difference:

```python
client = cspy.connect(51926)          # registry port; registry_host= for another host
```

## The pieces

| Object | What it does |
| --- | --- |
| `launch(iar_path, **config)` / `connect(port)` | Create a `Client`; `launch` owns the backend and stops it on close. Settings not given come from the environment. |
| `Config` | All settings, with defaults. `Config.from_env()` reads the `THRIFT_*` / `IAR_INSTALL_PATH` variables listed in [`.env.example`](.env.example). `Client(Config.from_env())` owns the backend it creates. |
| `Backend` | The processes: starts and supervises `IarServiceLauncher` + `CSpyServer2` (managed), or points at a registry (standalone). |
| `client.debugger` | Session lifecycle (`session()`, `configure_and_start()`, `stop_session()`, `cleanup()`, `attach()`), run control (`go`, `halt`, `step_over`, `step_into`, `step_out`, `run_to`, `reset`, `go_and_wait`, `wait_for_state`; the steps and `run_to` wait until the target has stopped), `eval`, `eval_int`, `registers`, `modules`, `zones`, `core_state`. |
| `client.breakpoints` | `add(ule, access=AccessType.FETCH)`, `temporary(ule)` (a `with` block), `all`, `get`, `enable`/`disable`, `remove`, `clear`, `restore(descriptor)`, `recently_hit`. Take a breakpoint or its id. |
| `client.context` | `stack`, `depth`, `info`, `locals`, `parameters`, `visible_symbols`, `lookup`. All take `context=Context.frame(n)` and the like. |
| `client.memory` | `read`, `write`, `read_u32`, `write_u32`, at an address (with `zone=`), a `Location`, or the `ExprValue` of a variable. |
| `client.disassembly`, `client.source` | `range(start, end)`; `ranges(address)`. |
| `client.terminal` | The target's console via libsupport: `text`, `output()`, `send(input)`, `wait_for_text("READY")`, `wait_for_exit()`. |
| `client.events` | Debug events from the backend: `recent()`, `subscribe(fn)`. |
| `client.listwindows` | Trace and other list windows: `services()`, `window(name).rows()`, `trace(name).set_enabled()`. |
| `client.project` | Embedded Workbench projects: `load`, `status`, `build`, `files`, `launch_configuration`, `start_debugging`. |
| `client.options` | The options dialog model: `open(project, configuration)` returns a session with `category_tree`, `option_tree`, `update`, `commit`. |
| `client.ide_services` | Hosting ProjectManager/OptionsService: `status`, `ensure`. |
| `client.raw("debugger")` | Any RPC the typed layer does not wrap: `client.raw("debugger").getAllZones()`. |
| `client.capabilities()` | What the backend offers right now, without raising. |

Backend data comes back as the IDL's thriftpy2 struct objects, with attribute
access (`bp.id`, `frame.functionName`, `value.type`); `cspy.to_plain(x)`
converts any of them to dicts and lists. The API's own types (`CoreState`,
`AccessType`, `Context`, `WaitResult`, `RegisterSnapshot`, `BuildResult`,
`ProjectStatus`, ...) are dataclasses in `iar_cspy.types`, all exported from
`iar_cspy`; `.to_dict()` gives plain data.

Launch configurations are one entry of a C-SPY VS Code `launch.json`.
`LaunchConfig.from_file()` reads one, picking a configuration by name and
resolving relative paths. Anything that takes a launch configuration also
accepts a dict, JSON text or a path. For Embedded Workbench projects,
`client.project.start_debugging()` uses the project's own configuration, so no
launch.json is needed.

## Errors

Everything raises `CSpyError`. `BackendError` means the backend itself
reported a failure (`.info` has the decoded debugger result code), also when
the API adds context to the message. `SessionError` means the call needs a
debug session: the methods that talk to one (run control, `eval`, registers,
breakpoints, memory, stack) raise it without one, and `session()` raises it
when the target did not stop at `stopOnSymbol`. When another frontend owns the
session (an IDE you connected to), `client.debugger.attach()` adopts it. For
failures that look like a
backend crash or reset, `.diagnostics` carries the managed processes' recent
output. `client.classify_error(exc)` maps any exception onto a stable code
(`TRANSPORT_CONNECTION_RESET`, `TIMEOUT`, `CSPY_KDCERROR`, ...) with a
`retryable` flag. The waits (`go_and_wait`, `run_to`, the steps,
`wait_for_state`) raise `WaitTimeout`, or return a result with
`timed_out=True` when given `check=False`.

The backend has quirks, and the API absorbs the known ones. Details are in
[docs/backend-notes.md](docs/backend-notes.md):

- `configure_and_start` hands off cleanly from a previous session and gives
  every managed session a fresh CSpyServer2.
- `stop_session` treats "failed to suspend" on a stopped target as success.
- `go_and_wait`, `run_to` and the steps wait for the backend's started and
  stopped events, not just a core state that reads "stopped" before the target
  has even started.
- A managed CSpyServer2 that crashed is replaced on the next call; the lost
  session then shows up as a `SessionError`, not as confusing backend errors.

## Testing

`iar_cspy.pytest_plugin` gives firmware tests a backend, and skips them when
there is none. Installing `iar-cspy` registers it with pytest, so there is
nothing to enable:

```python
# test_firmware.py
def test_boot_banner(cspy_session):
    cspy_session.go()
    assert cspy_session.client.terminal.wait_for_text("READY", timeout=10)
```

```sh
pytest --cspy-iar-path=/opt/iar/ewarm --cspy-launch=launch.json
pytest --cspy-registry=51926 --cspy-launch=launch.json        # a running backend
```

Give the paths in the `--opt=value` form: when pytest looks for the rootdir
and its configuration file, it mistakes a separate path argument for a test
path.

Fixtures: `cspy_client` (one per test session), `cspy_session` (a fresh
debug session per test, stopped at `stopOnSymbol`, with the terminal output
and events cleared) and `cspy_launch` (override it to hard-wire your
launch.json). The plugin also registers the `live` marker. See
[examples/pytest](examples/pytest).

`iar_cspy.testing` has test doubles for code built on the API, so it can be
unit-tested without a backend:

```python
from iar_cspy.testing import fake_client

client, rpc = fake_client(started=True)           # with a debug session
rpc.sequence("debugger", "getCoreState", 1, 0)     # one response per call
rpc.on("debugger", "evalExpression", rpc.struct("debugger", "evalExpression", value="42"))
assert client.debugger.core_state() == cspy.CoreState.RUNNING
assert client.debugger.eval_int("counter") == 42
```

Scripted exceptions go through the same error conversion as real ones, so a
`cspy_exception(...)` arrives as a `BackendError`. More in the
[API guide](docs/python-api.md).

## For coding agents

[examples/skills/iar-cspy](examples/skills/iar-cspy) is an agent skill
that points an agent at this documentation and sets a few ground rules. Copy
it into a project's `.claude/skills/` directory.

## How it works

```
 your script / pytest / MCP server ──▶ iar-cspy ──Thrift──▶ service registry ──▶ CSpyServer2  (debugger, breakpoints, memory, ...)
                                                                             └─▶ IarServiceLauncher (ProjectManager, OptionsService)
```

Every service is resolved through the *service registry*, a small Thrift
directory service on a local port. `iar-cspy` also hosts the callback services
the backend expects from a frontend (debug events, terminal I/O, list window
notifications) and registers them there.

### Backend modes

- **Managed** (default): given an IAR installation (`iar_cspy.launch(path)` or
  `IAR_INSTALL_PATH`), `iar-cspy` starts `IarServiceLauncher`, which owns the
  service registry and hosts the IDE services, and a `CSpyServer2` that joins
  that registry with `-registry <port>`. It supervises both and restarts them
  when they fail. CSpyServer2 supports one debug session per process, so
  every session gets a fresh one. Installations without the launcher, or
  `host_ide_services=False`, run CSpyServer2 alone, without the project and
  options features.
- **Standalone**: connect to the registry of a backend someone else runs,
  such as a Thrift-enabled `iaride` or a hand-started
  CSpyServer2/IarServiceLauncher (`iar_cspy.connect(port)` or
  `THRIFT_REGISTRY_PORT`). The port is that of the service registry, not of
  the debugger service itself. Nothing is started or stopped.

See [docs/ide-services.md](docs/ide-services.md) for the IDE services and
[docs/backend-notes.md](docs/backend-notes.md) for known backend behavior.

## Repository layout

```
src/iar_cspy/        client, backend, debugger, breakpoints, ... ; idl/ holds the Thrift IDL
tests/               unit tests (fakes) and live tests (a backend)
examples/
  firmware/          Cortex-M3 simulator program + launch.json used by demos and live tests
  scripts/           API scripts: live_demo.py, repro_session_cycles.py
  pytest/            firmware tests using the pytest plugin
  skills/iar-cspy/   an agent skill for the API, to copy into your own projects
docs/                API guide, IDE services, backend notes
```

## Development

```sh
pip install -r requirements-dev.txt                 # editable install + pytest
pytest                                              # unit tests; live tests skip without a backend
pytest -m live --cspy-iar-path=/opt/iar/ewarm       # live tests, managed backend
pytest -m live --cspy-cspyserver2=/path/CSpyServer2 # live tests, CSpyServer2 only
pytest examples/pytest --cspy-iar-path=/opt/iar/ewarm
python examples/scripts/live_demo.py --iar-path /opt/iar/ewarm
```

The live tests run the bundled firmware (`examples/firmware`) on the
simulator. CI (`.github/workflows/ci.yml`) runs the unit tests on Python
3.10-3.12 and builds the distribution. It does not run the live tests, since
it has no IAR license: run them locally before merging.
