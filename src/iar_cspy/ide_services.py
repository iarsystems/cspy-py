"""Hosting the IDE platform services (ProjectManager, OptionsService).

CSpyServer2 hosts only the debugger-side services (``debugger``,
``breakpoints``, ``debugger.contextmanager``, ``debugger.memory``,
``disassembly``, ``sourcelookup``). It has no service manager, so it cannot
load the IDE platform's service libraries. Those live in dynamic libraries
next to it in the installation (``libProjectManagerHandler.so``,
``libOptionsService.so``) and are hosted by ``IarServiceLauncher``, which:

1. with ``-standalone -sockets``, starts a service registry and writes its
   location to ``CSpyServer2-ServiceRegistry.txt`` in its working directory,
2. starts a ``com.iar.thrift.service.manager`` (ServiceManager.thrift) service,
3. loads service libraries named by JSON manifest files, either on the command
   line or at runtime via ``CSpyServiceManager.startServicesFromJsonManifest``.

A registry can be *shared*: ``CSpyServer2 -sockets -registry <port>`` joins an
existing registry instead of starting its own. So one launcher-owned registry
holds the debugger services and the IDE services together, and every service
resolves through the same registry with no special-casing per service. That is
what a managed :class:`~iar_cspy.Backend` sets up whenever the installation
ships an IarServiceLauncher.

Manifest path gotcha
--------------------
``CSpyServiceManagerHandler::GetServiceConfigsFromJsonFile`` resolves each
manifest's ``libraryName`` against *the manifest file's own directory*, and
does so unconditionally: an absolute ``libraryName`` gets the manifest
directory prepended to it and becomes a nonexistent path. So a manifest that
lives somewhere other than that directory must name its library relative to
itself, which is what :func:`render_manifest` does.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Config
from .errors import CSpyError

if TYPE_CHECKING:
    from .client import Client

SERVICE_MANAGER_SERVICE = "com.iar.thrift.service.manager"

#: Known IDE services, keyed by short name. ``registry_name`` is the name the
#: service registers itself under (the ``*_ID``/``SERVICE_ID`` constant in its
#: IDL, *not* the ``name`` field of the manifest, which is only a
#: ServiceManager-internal id). ``depends_on`` names services that must be
#: hosted in the same process first.
IDE_SERVICES: dict[str, dict[str, Any]] = {
    "projectmanager": {
        "registry_name": "com.iar.thrift.service.projectmanager",
        "manifest": "projectmanager.json",
        "manifest_service_name": "com.iar.ProjectManager",
        "library": "ProjectManagerHandler",
        "start_entry_point": "StartProjectManager",
        "stop_entry_point": "StopProjectManager",
        "depends_on": (),
    },
    "options": {
        "registry_name": "com.iar.optionsservice",
        "manifest": "OptionsService.json",
        "manifest_service_name": "com.iar.OptionsService",
        "library": "OptionsService",
        "start_entry_point": "OptionsServiceStart",
        "stop_entry_point": "OptionsServiceStop",
        # OptionsServiceHandler reaches the project manager by linkage
        # (GetProjectManager()), not over Thrift, so it needs the
        # ProjectManager service started in the same process. Without it,
        # CreateSession takes the whole host process down rather than
        # returning a failure - so this dependency is not optional.
        "depends_on": ("projectmanager",),
    },
}


def service_dependencies(key: str) -> tuple[str, ...]:
    return tuple(IDE_SERVICES[key].get("depends_on", ()))


def normalize_service_keys(raw: object) -> list[str]:
    """Normalize a service selection into known keys, in startup order.

    Accepts a list of names or a comma/semicolon separated string. Empty input
    means "all known services". Dependencies are pulled in and placed before
    the services that need them, so the result can be started front to back.
    """
    if raw is None or raw == "":
        names: list[str] = list(IDE_SERVICES)
    elif isinstance(raw, str):
        names = [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]
    else:
        names = [str(p).strip() for p in raw if str(p).strip()]  # type: ignore[attr-defined]

    requested: list[str] = []
    for name in names or list(IDE_SERVICES):
        key = name.lower()
        if key not in IDE_SERVICES:
            raise CSpyError(f"Unknown IDE service {name!r}. Known services: {', '.join(IDE_SERVICES)}")
        if key not in requested:
            requested.append(key)

    result: list[str] = []

    def add(key: str, seen: tuple[str, ...] = ()) -> None:
        if key in result:
            return
        if key in seen:
            raise CSpyError("Circular IDE service dependency: " + " -> ".join((*seen, key)))
        for dependency in service_dependencies(key):
            add(dependency, (*seen, key))
        result.append(key)

    for key in requested:
        add(key)
    return result


def service_bin_dir(cfg: Config) -> Path:
    """Directory holding the service libraries and their stock manifests.

    Normally ``<iar_path>/common/bin``. Falls back to the directory of
    whichever of IarServiceLauncher/CSpyServer2 was given individually, since
    both live in that same directory.
    """
    if cfg.service_bin_dir is not None:
        return cfg.service_bin_dir
    for exe in (cfg.launcher_executable, cfg.cspy_executable):
        if exe is not None:
            return exe.parent
    raise CSpyError(
        "Cannot locate the IAR service libraries: give an IAR installation "
        "(iar_path, --iar-path or IAR_INSTALL_PATH) so it is known which "
        "<path>/common/bin to use."
    )


def render_manifest(spec: dict[str, Any], bin_dir: Path, manifest_dir: Path) -> str:
    """Render a service manifest whose libraryName is relative to manifest_dir.

    The ServiceManager prepends the manifest's directory to ``libraryName``
    unconditionally, so the only portable way to point a generated manifest at
    a library elsewhere on disk is a relative path.
    """
    library = os.path.relpath(bin_dir / spec["library"], manifest_dir)
    return json.dumps(
        {
            "services": [
                {
                    "name": spec["manifest_service_name"],
                    "libraryName": library,
                    "spawnNewProcess": False,
                    "startupEntryPoint": spec["start_entry_point"],
                    "shutdownEntryPoint": spec["stop_entry_point"],
                    "registerInLauncher": False,
                }
            ]
        },
        indent=2,
    )


def resolve_manifest(cfg: Config, key: str) -> Path:
    """Return a manifest path for one IDE service.

    Prefers the manifest shipped next to the library. Falls back to
    generating an equivalent one in a temp directory, which is needed in
    practice: OptionsService's CMakeLists has an ``install(FILES DESTINATION
    ...)`` with an empty file list, so ``OptionsService.json`` is absent from
    the installation even though ``libOptionsService.so`` is there.
    """
    spec = IDE_SERVICES[key]
    bin_dir = service_bin_dir(cfg)

    stock = bin_dir / spec["manifest"]
    if stock.exists():
        return stock

    library = bin_dir / f"lib{spec['library']}.so"
    library_dll = bin_dir / f"{spec['library']}.dll"
    if not library.exists() and not library_dll.exists():
        raise CSpyError(
            f"Cannot host IDE service {key!r}: neither the manifest {stock} nor the "
            f"service library ({library.name} / {library_dll.name}) exists in {bin_dir}. "
            "Check the IAR installation path."
        )

    manifest_dir = Path(tempfile.mkdtemp(prefix="iar-service-manifests-"))
    manifest = manifest_dir / spec["manifest"]
    manifest.write_text(render_manifest(spec, bin_dir, manifest_dir), encoding="utf-8")
    return manifest


class IdeServices:
    """Makes sure the IDE services a call needs are registered (``client.ide_services``)."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._lock = threading.RLock()
        # Service keys already observed in the registry. Only a cache: cleared
        # whenever an RPC against the service fails, so a restarted backend is
        # re-checked.
        self._present: set[str] = set()
        self._generation = client.backend.launcher_generation

    def registry_name(self, key: str) -> str:
        cfg = self._client.config
        if key == "projectmanager":
            return cfg.projectmanager_service_name
        if key == "options":
            return cfg.options_service_name
        return IDE_SERVICES[key]["registry_name"]

    def ensure(self, key: str, force: bool = False) -> dict[str, Any]:
        """Make sure one IDE service is registered, starting it if it is not.

        Returns a record of what was found or done. Raises CSpyError when the
        service is absent and cannot be started.
        """
        if key not in IDE_SERVICES:
            normalize_service_keys(key)  # raises with the list of known names
        registry_name = self.registry_name(key)

        # Some services reach each other by linkage rather than over Thrift,
        # so a dependency has to be hosted in the same process and started first.
        dependencies = [self.ensure(dependency, force=force) for dependency in service_dependencies(key)]

        with self._lock:
            backend = self._client.backend
            endpoint = backend.registry_endpoint()
            if backend.launcher_generation != self._generation:
                # The launcher was restarted: nothing it hosted is there any more.
                self._present.clear()
                self._generation = backend.launcher_generation
            if not force and key in self._present:
                return {"service": key, "registry_name": registry_name, "status": "cached"}

            if endpoint is None:
                raise CSpyError(
                    f"Cannot reach IDE service {key!r} ({registry_name}): no service registry "
                    "configured. Either start a managed backend from an IAR installation that "
                    "ships IarServiceLauncher (iar_path=..., or --iar-path for the MCP server), "
                    "or connect to the registry of a backend that already hosts them."
                )

            present = {entry["name"] for entry in self._client.registry.services()}
            if registry_name in present:
                self._present.add(key)
                return {"service": key, "registry_name": registry_name, "status": "already-registered"}

            if SERVICE_MANAGER_SERVICE not in present:
                raise CSpyError(
                    f"IDE service {key!r} ({registry_name}) is not registered, and the backend "
                    f"has no {SERVICE_MANAGER_SERVICE} service to start it with. CSpyServer2 "
                    "alone cannot host IDE services: start a managed backend from an IAR "
                    "installation that ships IarServiceLauncher (--iar-path <path> for the MCP "
                    "server), or connect to an IarServiceLauncher/iaride backend. "
                    f"Services currently registered: {', '.join(sorted(present)) or '<none>'}."
                )

            manifest = resolve_manifest(self._client.config, key)
            self.load_manifest(manifest)
            self._client.backend.loaded_manifests[key] = str(manifest)
            self._present.add(key)
            return {
                "service": key,
                "registry_name": registry_name,
                "status": "started",
                "manifest": str(manifest),
                "dependencies": dependencies,
            }

    def ensure_all(self, services: object = None, force: bool = False) -> list[dict[str, Any]]:
        """Ensure a selection (default: the configured one, else all).

        Never raises for a single service: failures are reported per service
        with ``status == "error"``.
        """
        keys = normalize_service_keys(services or self._client.config.ide_services)
        results: list[dict[str, Any]] = []
        for key in keys:
            try:
                results.append(self.ensure(key, force=force))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "service": key,
                        "registry_name": self.registry_name(key),
                        "status": "error",
                        "error": str(exc),
                    }
                )
        return results

    def auto_ensure(self, key: str) -> None:
        """Best-effort start before an IDE service RPC, unless disabled."""
        if self._client.config.auto_ide_services:
            self.ensure(key)

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._present.clear()
            else:
                self._present.discard(key)

    def load_manifest(self, manifest: Path) -> None:
        """Have the backend's ServiceManager load a service manifest file."""
        cfg = self._client.config
        # Loading ProjectManagerHandler pulls in the whole legacy project
        # manager, so this call is far slower than a normal RPC.
        timeout_ms = max(int(cfg.timeout_ms), int(cfg.launcher_start_timeout_ms))
        self._client.call(
            "servicemanager", "startServicesFromJsonManifest", str(manifest), timeout_ms=timeout_ms
        )

    def status(self) -> dict[str, Any]:
        """How the IDE services are hosted: mode, registry contents, launcher."""
        cfg = self._client.config
        backend = self._client.backend
        data: dict[str, Any] = {
            "cspy_mode": cfg.mode,
            "iar_path": str(cfg.iar_path) if cfg.iar_path else None,
            "auto_ide_services": cfg.auto_ide_services,
            "configured_services": list(cfg.ide_services) or sorted(IDE_SERVICES),
            "service_launcher_exe": str(cfg.launcher_executable) if cfg.launcher_executable else None,
            "cspy_executable": str(cfg.cspy_executable) if cfg.cspy_executable else None,
        }
        try:
            data["service_bin_dir"] = str(service_bin_dir(cfg))
        except Exception as exc:  # noqa: BLE001
            data["service_bin_dir"] = None
            data["service_bin_dir_error"] = str(exc)

        registered: list[str] = []
        try:
            # Querying the registry is what brings a managed backend up, so it
            # has to happen before the launcher snapshot is taken.
            services = self._client.registry.services()
            registered = [entry["name"] for entry in services]
            data["registry_services"] = services
        except Exception as exc:  # noqa: BLE001
            data["registry_error"] = str(exc)

        endpoint = backend.current_registry_endpoint()
        data["registry_host"] = endpoint[0] if endpoint else cfg.registry_host
        data["registry_port"] = endpoint[1] if endpoint else cfg.registry_port
        data["loaded_manifests"] = dict(backend.loaded_manifests)
        launcher = backend.launcher_process.status()
        launcher["loaded_manifests"] = dict(backend.loaded_manifests)
        data["service_launcher"] = launcher

        data["has_service_manager"] = SERVICE_MANAGER_SERVICE in registered
        data["services"] = {
            key: {
                "registry_name": self.registry_name(key),
                "registered": self.registry_name(key) in registered,
                "manifest": spec["manifest"],
                "depends_on": list(service_dependencies(key)),
            }
            for key, spec in IDE_SERVICES.items()
        }
        return data

    def stop_launcher(self) -> dict[str, Any]:
        """Stop the IarServiceLauncher this backend started, if any.

        An externally started launcher or iaride is left alone.
        """
        before = self._client.backend.launcher_process.status()
        self._client.backend.stop_launcher()
        self.invalidate()
        return {"was_running": before.get("running"), "previous": before}
