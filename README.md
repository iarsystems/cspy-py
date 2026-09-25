# iar-cspy: Python API for IAR C-SPY

A typed Python API for the IAR C-SPY debugger and the Embedded Workbench IDE
services, over their Thrift interfaces. Use it to script debug sessions, write
hardware-in-the-loop tests, or let an AI agent do multi-step debugging in one
script. It is also what the [MCP server](https://github.com/iarsystems/cspy-mcp)
is built on.

[![CI](https://github.com/iarsystems/cspy-py/actions/workflows/ci.yml/badge.svg)](https://github.com/iarsystems/cspy-py/actions/workflows/ci.yml)

```sh
pip install .        # from a checkout of this repository
```

The only dependency is `thriftpy2`; the Thrift IDL ships with the package.
You need Python 3.10+ and either an IAR installation (the directory with
`common/bin` under it; the backend is then started for you) or an
already-running backend to connect to.

Licensed under the [MIT License](LICENSE).

## Quick start

```python
import iar_cspy as cspy

with cspy.launch("/opt/iar/ewarm") as client:              # managed backend from an installation
    with client.debugger.session("launch.json") as dbg:   # configure, start, run to stopOnSymbol
        bp = client.breakpoints.add("process_packet")
        dbg.go_and_wait(timeout=10)                        # run until it stops again

        print(dbg.eval("len").value)
        print(client.memory.read(0x2000_0000, 16).hex())
        print(dbg.registers()["PC"].value)
```

To use a backend that is already running (a Thrift-enabled `iaride`, or a
CSpyServer2 you started), connect to its service registry instead:

```python
client = cspy.connect(51926)          # registry port; registry_host= for another host
```

## The pieces

| Object | What it does |
| --- | --- |
| `launch(iar_path, **config)` / `connect(port)` | Create a `Client`; `launch` owns the backend and stops it on close. |
| `Config` | All settings, with defaults. `Config.from_env()` reads the `THRIFT_*` / `IAR_INSTALL_PATH` variables (see [.env.example](.env.example)). |
| `Backend` | The processes: starts and supervises `IarServiceLauncher` + `CSpyServer2` (managed), or points at a registry (standalone). |
| `client.debugger` | Session lifecycle (`session()`, `configure_and_start()`, `stop_session()`, `cleanup()`), run control (`go`, `halt`, `step_over`, `step_into`, `step_out`, `run_to`, `reset`, `go_and_wait`, `wait_for_state`), `eval`, `registers`, `modules`, `zones`, `core_state`. |
| `client.breakpoints` | `add(ule, access=AccessType.FETCH)`, `all`, `get`, `enable`/`disable`, `remove`, `clear`, `restore(descriptor)`, `recently_hit`. |
| `client.context` | `stack`, `depth`, `info`, `locals`, `parameters`, `visible_symbols`, `lookup`. All take `context=Context.frame(n)` and the like. |
| `client.memory` | `read`, `write`, `read_u32`, `write_u32`, by zone. |
| `client.disassembly`, `client.source` | `range(start, end)`; `ranges(address)`. |
| `client.terminal` | The target's console via libsupport: `text`, `output()`, `send(input)`, `wait_for_text("READY")`, `wait_for_exit()`. |
| `client.events` | Debug events from the backend: `recent()`, `subscribe(fn)`. |
| `client.listwindows` | Trace and other list windows: `services()`, `window(name).rows()`, `trace(name).set_enabled()`. |
| `client.project` | Embedded Workbench projects: `load`, `status`, `build`, `files`, `launch_configuration`, `start_debugging`. |
| `client.options` | The options dialog model: `open(project, configuration)` returns a session with `category_tree`, `option_tree`, `update`, `commit`. |
| `client.ide_services` | Hosting ProjectManager/OptionsService: `status`, `ensure`. |
| `client.raw("debugger")` | Any RPC the typed layer does not wrap: `client.raw("debugger").getAllZones()`. |

Backend data comes back as the IDL's thriftpy2 struct objects, with attribute
access (`bp.id`, `frame.functionName`, `value.type`); `cspy.to_plain(x)`
converts any of them to dicts and lists. The API's own types (`CoreState`,
`AccessType`, `Context`, `WaitResult`, `RegisterSnapshot`, `BuildResult`, ...)
are in `iar_cspy.types`.

Launch configurations are one entry of a C-SPY VS Code `launch.json`.
`LaunchConfig.from_file()` reads one, picking a configuration by name and
resolving relative paths. Anything that takes a launch configuration also
accepts a dict, JSON text or a path. For Embedded Workbench projects,
`client.project.start_debugging()` uses the project's own configuration, so no
launch.json is needed.

## Errors

Everything raises `CSpyError`. `BackendError` means the backend itself
reported a failure (`.info` has the decoded debugger result code), and
`SessionError` means the call needs a session. For failures that look like a
backend crash or reset, `.diagnostics` carries the managed processes' recent
output. `client.classify_error(exc)` maps any exception onto a stable code
(`TRANSPORT_CONNECTION_RESET`, `TIMEOUT`, `CSPY_KDCERROR`, ...) with a
`retryable` flag. `go_and_wait` and `wait_for_state` raise `WaitTimeout`, or
return a result with `timed_out=True` when given `check=False`.

The backend has quirks, and the API absorbs the known ones. Details are in
[docs/backend-notes.md](docs/backend-notes.md):

- `configure_and_start` hands off cleanly from a previous session and gives
  every managed session a fresh CSpyServer2.
- `stop_session` treats "failed to suspend" on a stopped target as success.
- `go_and_wait` and `run_to` wait for the backend's stop event, not just a
  core state that reads "stopped" before the target has even started.

## Testing

`iar_cspy.pytest_plugin` gives firmware tests a backend, and skips them when
there is none:

```python
# conftest.py
pytest_plugins = ["iar_cspy.pytest_plugin"]

# test_firmware.py
def test_boot_banner(cspy_client, cspy_session):
    cspy_session.go()
    assert cspy_client.terminal.wait_for_text("READY", timeout=10)
```

```sh
pytest --cspy-iar-path=/opt/iar/ewarm --cspy-launch=launch.json
pytest --cspy-registry=51926 --cspy-launch=launch.json        # a running backend
```

Fixtures: `cspy_client` (one per test session), `cspy_session` (a fresh
debug session per test) and `cspy_launch` (override it to hard-wire your
launch.json). See [examples/pytest](examples/pytest).

`iar_cspy.testing` has test doubles for code built on the API, so it can be
unit-tested without a backend:

```python
from iar_cspy.testing import fake_client

client, rpc = fake_client()
rpc.on("debugger", "getCoreState", lambda core: 0)
assert client.debugger.core_state() == cspy.CoreState.STOPPED
```

More in the [API guide](docs/python-api.md).

## How it works

```
 your script / pytest / MCP server ──▶ iar-cspy ──Thrift──▶ service registry ──▶ CSpyServer2  (debugger, breakpoints, memory, ...)
                                                                             └─▶ IarServiceLauncher (ProjectManager, OptionsService)
```

- **Managed mode** (default): given an IAR installation, `iar-cspy` starts
  `IarServiceLauncher` (owning the service registry and hosting the IDE
  services) and a `CSpyServer2` that joins that registry, supervises both, and
  restarts them as needed. Installations without the launcher run
  CSpyServer2 alone.
- **Standalone mode**: connect to the registry of a backend someone else runs,
  for example a Thrift-enabled `iaride` or a hand-started CSpyServer2.
- Every service is resolved through the registry. This process also hosts the
  callback services the backend expects from a frontend (debug events, terminal
  I/O, list window notifications).

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
3.10-3.12, builds the distribution, and runs the live tests, example tests
and demo against the public cxarm toolchain.
