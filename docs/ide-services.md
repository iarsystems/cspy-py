# Hosting the IDE services: ProjectManager and OptionsService

`client.project` and `client.options` (and the `project_*` / `options_*`
tools of the [MCP server](https://github.com/iarsystems/cspy-mcp)) talk to two
IDE platform Thrift services that **`CSpyServer2` cannot host**. This
document explains why, how to start them, and how to verify the result.

- [Why CSpyServer2 is not enough](#why-cspyserver2-is-not-enough)
- [Option A: let iar-cspy host them (managed mode)](#option-a-let-iar-cspy-host-them-managed-mode)
- [Option B: connect to a backend that already hosts them (standalone mode)](#option-b-connect-to-a-backend-that-already-hosts-them-standalone-mode)
- [Verifying and troubleshooting](#verifying-and-troubleshooting)
- [Reference: services, manifests, environment](#reference-services-manifests-environment)
- [Known quirks](#known-quirks)

## Why CSpyServer2 is not enough

`CSpyServer2 -standalone -sockets` starts a service registry and registers the
debugger side of the platform:

```
breakpoints                debugger.contextmanager     sourcelookup
debugger                   debugger.memory             disassembly
com.iar.thrift.service.registry                        SharedAmpSync.*
```

That is the whole list. `CSpyServer2` has no *service manager*, so it cannot
load additional service implementations — and ProjectManager and OptionsService
are exactly that: implementations in dynamic libraries
(`libProjectManagerHandler.so` / `ProjectManagerHandler.dll`,
`libOptionsService.so` / `OptionsService.dll`) that sit next to `CSpyServer2` in
`<install>/common/bin`.

`IarServiceLauncher`, in the same directory, is the generic host for them. With
`-standalone -sockets` it:

1. starts a service registry and writes its location to
   `CSpyServer2-ServiceRegistry.txt` in its working directory,
2. registers a `com.iar.thrift.service.manager` service
   (`ServiceManager.thrift`),
3. loads service libraries described by JSON manifest files — given on the
   command line, or at runtime via
   `CSpyServiceManager.startServicesFromJsonManifest`.

The key detail that makes this pleasant: a registry can be **shared**.
`CSpyServer2 -sockets -registry <port>` joins an existing registry instead of
starting its own. So one launcher-owned registry ends up holding the debugger
services *and* the IDE services, and every call resolves through
that single registry with no per-service special casing.

## Option A: let iar-cspy host them (managed mode)

Managed mode - the default - makes `iar_cspy` own the whole backend:

```
iar_cspy.Backend
 ├── spawns IarServiceLauncher -standalone -sockets   → owns the registry
 │      └── loads projectmanager.json / OptionsService.json on demand
 └── spawns CSpyServer2 -sockets -registry <port>     → joins that registry
```

Give it one path: the IAR installation or build stage, meaning the directory
with `common/bin` under it. Both programs are taken from there.

```python
import iar_cspy

with iar_cspy.launch("/path/to/install") as client:
    client.project.load("/abs/path/app.ewp")
    print(client.project.build().succeeded)
```

or via the environment, with `iar_cspy.Config.from_env()`:

```sh
export IAR_INSTALL_PATH=/path/to/install
```

The MCP server takes the same path as `iar-cspy-mcp --iar-path /path/to/install`.

The launcher comes up first and the registry it publishes is what everything
resolves through, so ordering is handled for you. The individual IDE services
are started lazily: the first `project_*` call loads the ProjectManager, the
first `options_*` call loads the OptionsService. Call `ide_services_ensure()` to
warm them up front, or set `THRIFT_AUTO_IDE_SERVICES=0` to require it
explicitly.

Programs that installation does not ship are simply not started. A compiler-only
toolchain has no `IarServiceLauncher`, and managed mode then runs `CSpyServer2`
on its own with the `project_*`/`options_*` tools unavailable. Pass
`--no-ide-services` to choose that deliberately.

Both processes are supervised the same way: stdout goes to a log file, a recent
tail is attached to failure envelopes (`service_launcher_diagnostics`), and both
are stopped on exit - CSpyServer2 first, then the launcher.

`--cspyserver2` and `--service-launcher` still take individual program paths and
override what the stage provides, but you should not normally need them.

## Option B: connect to a backend that already hosts them (standalone mode)

Anything that hosts a `com.iar.thrift.service.manager` works — a hand-started
`IarServiceLauncher`, or a Thrift-enabled `iaride`, which registers
ProjectManager and OptionsService itself.

Start the launcher yourself, with or without manifests on the command line:

```sh
cd /path/to/install/common/bin
./IarServiceLauncher -standalone -sockets projectmanager.json
```

```
     IAR Service Launcher V9.6.0.47
[1003892]
Service registry running on local socket on port: 41821
Also available in serialized form in CSpyServer2-ServiceRegistry.txt
[1003892] Starting service manager service...
[1003892] Service: com.iar.ProjectManager
[1003892] Loading native library .../common/bin/ProjectManagerHandler
[1003892] Service started: com.iar.ProjectManager
[1003892] Entering main loop...
```

Then connect to that registry port, which selects standalone mode:

```sh
iar-cspy-mcp --registry-port 41821          # MCP server
iar_cspy.connect(41821)                     # Python API
```

`-standalone` is required for the launcher to host a registry, and `-sockets`
is required because the Python Thrift runtime does not support named pipes.

Any manifest not given on the command line is still loaded on demand through
the service manager, so `-standalone -sockets` with no manifests at all is
enough — `iar-cspy` adds what it needs.

To attach a debugger to the *same* registry, start CSpyServer2 with
`-sockets -registry <port>` rather than `-standalone`.

## Verifying and troubleshooting

`ide_services_status()` (in Python, `client.ide_services.status()`) is the
first thing to call when a `project_*` or `options_*` tool cannot reach its
service. It reports the active mode, the
registry endpoint, the launcher process state, everything currently registered,
and which manifest would be used for each missing service:

```json
{
  "cspy_mode": "managed",
  "iar_path": "/path/to/install",
  "service_bin_dir": "/path/to/install/common/bin",
  "registry_port": 37523,
  "has_service_manager": true,
  "services": {
    "projectmanager": { "registry_name": "com.iar.thrift.service.projectmanager",
                        "registered": true,  "manifest": "projectmanager.json",
                        "depends_on": [] },
    "options":        { "registry_name": "com.iar.optionsservice",
                        "registered": false, "manifest": "OptionsService.json",
                        "depends_on": ["projectmanager"] }
  },
  "service_launcher": { "running": true, "pid": 1006182, "log_path": "/tmp/iar-service-launcher-….log" }
}
```

| Symptom | Cause and fix |
| --- | --- |
| `... has no com.iar.thrift.service.manager service to start it with` | Only a bare `CSpyServer2` is running. Pass `--iar-path` so `iar-cspy` hosts the services, or point at a launcher/iaride registry. The error lists the services the registry *did* have. |
| `Cannot reach IDE service ...: no service registry configured` | No registry at all: pass `--iar-path`, or set `THRIFT_REGISTRY_HOST`/`THRIFT_REGISTRY_PORT`. |
| `Cannot host IDE services: no IarServiceLauncher path` | No `--iar-path`/`IAR_INSTALL_PATH`, or that installation does not ship the launcher. |
| `neither the manifest ... nor the service library ... exists` | Wrong path. Check `--iar-path`/`IAR_INSTALL_PATH`. |
| `CSpyServer2 was asked to join registry port N but reported port M` | CSpyServer2 did not attach to the launcher's registry; check its log path in the error. |
| `Timed out waiting for the IarServiceLauncher registry port` | Raise `THRIFT_SERVICE_LAUNCHER_START_TIMEOUT_MS`; the error includes the launcher log path. |
| `OptionsService CreateSession failed: Project not found: <path>` | The project is not loaded in the project manager. Call `project_load_workspace(<path>)` first. |
| The host process dies on the first `options_*` call | OptionsService was started without ProjectManager in the same process. Let `iar-cspy` resolve the dependency (do not bypass it by loading only `OptionsService.json` by hand). |

`ide_services_ensure(force=true)` re-checks the registry after a backend
restart, and `ide_services_stop_launcher()` tears down a launcher the server
started. Note that the launcher owns the registry, so stopping it also takes the
managed CSpyServer2's registry away — expect to restart both.

## Reference: services, manifests, environment

A service's registry name is the `SERVICE_ID`/`*_ID` constant in its IDL, which
is *not* the `name` field in its manifest — that one is only a
ServiceManager-internal id:

| Key | Registry name (IDL constant) | Manifest `name` | Library | Entry points | Depends on |
| --- | --- | --- | --- | --- | --- |
| `projectmanager` | `com.iar.thrift.service.projectmanager` | `com.iar.ProjectManager` | `ProjectManagerHandler` | `StartProjectManager` / `StopProjectManager` | — |
| `options` | `com.iar.optionsservice` | `com.iar.OptionsService` | `OptionsService` | `OptionsServiceStart` / `OptionsServiceStop` | `projectmanager` |

OptionsService reaches the project manager directly rather than over Thrift, so
**ProjectManager must be hosted in the same process**. Starting `options` alone
and then calling `CreateSession` takes the whole host process down rather than
returning a failure. `iar-cspy` pulls the dependency in automatically, so
`THRIFT_IDE_SERVICES=options` and `ide_services_ensure("options")` both start
ProjectManager first.

The environment variables for all of this (`IAR_INSTALL_PATH`,
`THRIFT_HOST_IDE_SERVICES`, `THRIFT_IDE_SERVICES`, `THRIFT_AUTO_IDE_SERVICES`,
the program paths, timeouts and registry names) are described, with their
defaults, in [`.env.example`](../.env.example).

## Known quirks

**`OptionsService.json` may be missing from the installation.** The
OptionsService library is installed but its service manifest is not always
shipped alongside it. `iar-cspy` handles this by generating an equivalent
manifest in a temp directory; `ide_services_status()` shows the path under
`loaded_manifests`. When the installation does ship the file, that one is used
instead, so nothing needs changing if a later release adds it.

**Manifest `libraryName` is always relative to the manifest file.** The
service manager resolves `libraryName` against the manifest's own directory,
unconditionally, so an absolute `libraryName` ends up with that directory
prepended and points at a path that does not exist. Generated manifests
therefore use a relative path. This is also why passing a shipped manifest by
absolute path works fine: the `libraryName` inside it is relative.

**Stopping a debug session ends CSpyServer2.** Managed mode stops a session
by having CSpyServer2 exit (`Debugger.exit()`), and starts a fresh one for the
next session. Because the IDE services live in the IarServiceLauncher rather
than in CSpyServer2, a project or options session survives that.

**A stopped CSpyServer2 leaves its services in the shared registry.** When a
CSpyServer2 that joined the launcher's registry exits, its registrations
(`debugger`, `breakpoints`, ...) stay behind, pointing at dead ports, and
`waitForService` hands them out until a new CSpyServer2 re-registers. Before
starting a fresh CSpyServer2 into that registry, the backend therefore
deregisters every entry whose endpoint is no longer reachable. Without this, the
second debug session in one process failed with a connection error.

**Transport is loopback TCP, and that is not configurable here.** The IDE
platform picks named pipes by default on Windows and TCP sockets on Linux; the
backend is started with `-sockets` so both end up on TCP, because the Python Thrift
runtime cannot speak Windows named pipes. Those sockets bind to `127.0.0.1` on
an ephemeral port, so the Thrift traffic never leaves the machine.
