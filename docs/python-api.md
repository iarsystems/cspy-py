# Python API guide

> **Experimental.** The `iar_cspy` Python API is experimental. IAR makes no
> guarantees about its stability: it may change incompatibly, or be removed,
> in any release and without notice.

`iar_cspy` scripts the IAR C-SPY debugger. This guide shows the model and a
set of recipes; the [README](../README.md) has the reference
overview, and every class and method has a docstring (`help(client.debugger)`).

If you are an AI agent with the [MCP server](https://github.com/iarsystems/cspy-mcp)
available: use a script like the ones below when a task needs loops,
conditions or many steps. The objects mirror the MCP tools (`debugger_go_and_wait_for_core_state` is
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
  else's. [Backend modes](../README.md#backend-modes) has the details.
- A **client** is your connection to it. Everything hangs off the client, and
  closing it stops a managed backend.
- A **session** is one debug session: configure (from a launch
  configuration), start, and finally stop. CSpyServer2 supports one session
  per process, so with a managed backend each session gets a fresh process.
  That is why `client.debugger.session(...)` is a context manager. The
  methods that talk to a session (run control, `eval`, registers,
  breakpoints, memory, the stack) raise `SessionError` without one.
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
`cspy.launch("/opt/iar/ewarm", host_ide_services=False, timeout_ms=30000)`;
the rest comes from the environment variables the MCP server uses (see
[`.env.example`](../.env.example)), so `cspy.launch()` alone works with
`IAR_INSTALL_PATH` set. `cspy.Client(cspy.Config.from_env())` also honours
`THRIFT_CSPYSERVER_MODE`, and like `launch()` stops the processes it started
when closed.

`session()` runs to the launch configuration's `stopOnSymbol` and raises
`SessionError` when the target does not get there, so the body never starts
from an unexpected place; `session(launch, require_stop_on_symbol=False)`
lets it through, and `dbg.last_start` tells what happened.

## Recipes

### Run to a function and look around

```python
with client.debugger.session("launch.json") as dbg:
    client.breakpoints.add("parse_header")
    dbg.go_and_wait(timeout=10)

    print("PC", hex(dbg.registers()["PC"].value))
    for name in client.context.visible_symbols().all:
        print(name, "=", dbg.eval(name).value)       # the value as C-SPY shows it
    print("len =", dbg.eval_int("len"))              # as a Python int

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

### Step through a function

```python
with client.debugger.session("launch.json") as dbg:
    with client.breakpoints.temporary("parse_header"):   # removed after the block
        dbg.go_and_wait(timeout=10)
    for _ in range(5):
        dbg.step_over()                                  # waits until it has stopped
        print(hex(dbg.registers()["PC"].value), dbg.eval_int("offset"))
    print(client.memory.read(dbg.eval("header"), 8).hex())   # a variable's memory
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
client.debugger.attach()                  # adopt the session the IDE started
print(client.debugger.version(), client.debugger.core_state())
print(client.breakpoints.all())           # the IDE's session: do not stop it from here
```

`attach()` checks that the target is online and then lets session calls
through without configuring or starting anything. Waits fall back to polling
the core state, because the IDE receives the debug events.

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
`kDcLicenseViolation`, ...), also for errors the API re-raised with more
context, such as a failed configure or a rejected breakpoint location. `exc.diagnostics` has the managed processes' last
output when the failure looks like a crash. Backend behavior worth knowing
about is collected in [backend-notes.md](backend-notes.md).

## Tests

The pytest plugin comes with the package: pytest loads it wherever
`iar-cspy` is installed (install `iar-cspy[test]` to get pytest along). Each
test gets a started session stopped at `stopOnSymbol`, with the terminal
output and event buffer cleared, and tests are skipped when no backend is
configured:

```python
# conftest.py
from pathlib import Path

import pytest
from iar_cspy import LaunchConfig


@pytest.fixture
def cspy_launch():
    return LaunchConfig.from_file(Path(__file__).parent / "launch.json")
```

```python
# test_protocol.py
import pytest


@pytest.mark.live                      # registered by the plugin
def test_handshake(cspy_session):
    cspy_session.client.breakpoints.add("on_handshake_done")
    cspy_session.go_and_wait(timeout=5)
    assert cspy_session.eval("g_link_state").value == "LINK_UP"
```

```sh
pytest --cspy-iar-path=/opt/iar/ewarm             # or --cspy-registry=PORT for a running backend
```

For code that uses the API, `iar_cspy.testing.fake_client()` returns a client
whose RPCs you script, and no backend is needed. Scripted exceptions are
converted as real ones are, `rpc.struct()` builds a method's return struct,
and `rpc.sequence()` answers successive calls in turn:

```python
from iar_cspy import BackendError
from iar_cspy.testing import cspy_exception, fake_client

client, rpc = fake_client(started=True)
rpc.on("debugger", "go", cspy_exception(code=8, method="DkGo"))
try:
    client.debugger.go()
except BackendError as exc:
    assert client.classify_error(exc)["code"] == "CSPY_KDCLICENSEVIOLATION"
```
