# Python API guide

`iar_cspy` scripts the IAR C-SPY debugger. This guide shows the model and a
set of recipes; the [README](../README.md) has the reference
overview, and every class and method has a docstring (`help(client.debugger)`).

If you are an AI agent with the [MCP server](https://github.com/iarsystems/cspy-mcp) available: use a script like the
ones below when a task needs loops, conditions or many steps. The objects
mirror the MCP tools (`debugger_go_and_wait_for_core_state` is
`client.debugger.go_and_wait`, `breakpoints_set_on_ule` is
`client.breakpoints.add`, and so on).

Install it with

```sh
pip install "iar-cspy @ git+https://github.com/iarsystems/cspy-py"
```

## The model

- A **backend** is the set of IAR processes hosting the services:
  CSpyServer2 for debugging, IarServiceLauncher for projects and options. A
  managed backend (`iar_cspy.launch(iar_path)`) is started and restarted for
  you. A standalone one (`iar_cspy.connect(registry_port)`) is someone
  else's.
- A **client** is your connection to it. Everything hangs off the client, and
  closing it stops a managed backend.
- A **session** is one debug session: configure (from a launch
  configuration), start, and finally stop. CSpyServer2 supports one session
  per process, so with a managed backend each session gets a fresh process.
  That is why `client.debugger.session(...)` is a context manager.
- Calls return the backend's own data structures (thriftpy2 structs with
  attribute access). `iar_cspy.to_plain(x)` turns any of them into
  dicts/lists, handy for printing or JSON.

```python
import iar_cspy as cspy

client = cspy.launch("/opt/iar/ewarm")            # nothing starts until first use
try:
    with client.debugger.session("launch.json") as dbg:
        ...
finally:
    client.close()
```

`launch()` takes any `Config` field as a keyword, e.g.
`cspy.launch("/opt/iar/ewarm", host_ide_services=False, timeout_ms=30000)`.
`cspy.Client(cspy.Config.from_env())` uses the same environment variables as
the MCP server.

## Recipes

### Run to a function and look around

```python
with client.debugger.session("launch.json") as dbg:
    client.breakpoints.add("parse_header")
    dbg.go_and_wait(timeout=10)

    print("PC", hex(dbg.registers()["PC"].value))
    for name in client.context.visible_symbols()["all"]:
        print(name, "=", dbg.eval(name).value)

    info = client.context.info()              # needs an IDE frontend, see below
    for frame in client.context.stack(0, 10):
        print(" ", frame.functionName)
```

The call stack, context info, source lookup and disassembly make the backend
call back into the IDE's `frontend` service. Headless, with no IDE, they fail
with `No such service, serviceName=frontend` (see
[backend-notes.md](backend-notes.md)); against a Thrift-enabled `iaride`
they work.

### Capture state on a fault

```python
with client.debugger.session("launch.json") as dbg:
    client.breakpoints.add("HardFault_Handler")
    try:
        dbg.go_and_wait(timeout=60)
    except cspy.WaitTimeout:
        print("no fault within a minute")
    else:
        regs = dbg.registers("CPU Registers")
        print({r.name: hex(r.value) for r in regs.registers if r.value is not None})
        sp = regs["R13_main"].value
        print("stack:", client.memory.read(sp, 64).hex(" ", 4))
```

### Watch a variable

```python
from iar_cspy import AccessType

with client.debugger.session("launch.json") as dbg:
    client.breakpoints.add("g_state", AccessType.WRITE)        # data watchpoint
    for _ in range(5):
        dbg.go_and_wait(timeout=10)
        print("g_state =", dbg.eval("g_state", format="Hex").value,
              "at PC", hex(dbg.registers()["PC"].value))
```

### Run until the program prints something

```python
with client.debugger.session("launch.json") as dbg:
    dbg.go()
    if not client.terminal.wait_for_text("self-test passed", timeout=30):
        dbg.halt()
        raise SystemExit(client.terminal.text)
```

`client.terminal.send("input\n")` feeds the program's stdin, and
`wait_for_exit()` waits for a program that calls `exit()`.

### Build and debug an Embedded Workbench project

```python
client.project.load("/abs/path/app.eww")
build = client.project.build()                   # current project and configuration
if not build.succeeded:
    raise SystemExit("\n".join(build.tail(40)))

result = client.project.start_debugging(build=False)   # uses the project's own launch configuration
try:
    ...
finally:
    client.debugger.stop_session()
```

Project and option calls need the IDE services, which a managed backend from a
full IAR installation provides; see [ide-services.md](ide-services.md).

### Attach to an IDE's session

```python
client = cspy.connect(51926)              # the port iaride reports for its service registry
print(client.debugger.version(), client.debugger.core_state())
print(client.breakpoints.all())           # the IDE's session: do not stop it from here
```

The API does not require a session this client started. Only the MCP tools
guard on that, and `client.debugger.set_state(configured=True, started=True)`
tells them the session exists.

### Anything not wrapped

```python
zones = client.raw("debugger").getAllZones()
cycles = client.raw("debugger").getCyclesPerSecond()
```

`client.methods("debugger")` lists the RPCs. Arguments can be given
JSON-shaped (dicts for structs, enum names as strings); they are converted to
the IDL types.

## Errors and retries

```python
try:
    client.debugger.configure_and_start("launch.json")
except cspy.CSpyError as exc:
    info = client.classify_error(exc)            # {"code": ..., "retryable": ..., ...}
    if info["retryable"]:
        client.debugger.cleanup()
        client.debugger.configure_and_start("launch.json")
    else:
        raise
```

`BackendError.info` holds the debugger's result code (`kDcError`,
`kDcLicenseViolation`, ...). `exc.diagnostics` has the managed processes' last
output when the failure looks like a crash. Backend behavior worth knowing
about is collected in [backend-notes.md](backend-notes.md).

## Tests

With the pytest plugin, each test gets a started session on the target, and
tests are skipped when no backend is configured:

```python
# conftest.py
from pathlib import Path

import pytest
from iar_cspy import LaunchConfig

pytest_plugins = ["iar_cspy.pytest_plugin"]


@pytest.fixture
def cspy_launch():
    return LaunchConfig.from_file(Path(__file__).parent / "launch.json")
```

```python
# test_protocol.py
def test_handshake(cspy_client, cspy_session):
    cspy_client.breakpoints.add("on_handshake_done")
    cspy_session.go_and_wait(timeout=5)
    assert cspy_session.eval("g_link_state").value == "LINK_UP"
```

```sh
pytest --cspy-iar-path=/opt/iar/ewarm             # or --cspy-registry=PORT for a running backend
```

For code that uses the API, `iar_cspy.testing.fake_client()` returns a client
whose RPCs you script, and no backend is needed:

```python
from iar_cspy.testing import cspy_exception, fake_client

client, rpc = fake_client()
rpc.on("debugger", "stopSession", cspy_exception(code=6, method="DkStop"))
```
