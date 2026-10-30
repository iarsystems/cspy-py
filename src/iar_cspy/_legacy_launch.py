"""The launch sequence for products without ``resolveLaunchConfiguration``.

Products before IDE platform 9.5 (e.g. EWARM 9.70) lack ``resolveLaunchConfiguration``,
``configureSession`` and ``startSMPSession``: the frontend builds a
``cspy.SessionConfiguration`` itself, starts it with ``startSession``, and
loads the macros, flashes and loads the program by hand. Ported from the VS
Code debug extension (``runLegacyInitSequence`` in ``src/dap/cspyDebug.ts``
and ``LaunchArgumentConfigurationResolver``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import _drivers
from .errors import CSpyError, with_context
from .launch import LaunchConfig

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)

#: Names of the libsupport plugin over the years, newest first.
_LIBSUPPORT_NAMES = ("LibSupportUniversal", "LibSupportEclipse", "LibSupport")


@dataclass
class LegacyLaunch:
    """A launch configuration resolved on this side, for :func:`start`."""

    launch: LaunchConfig
    #: The ``cspy.SessionConfiguration`` for ``startSession``, JSON-shaped.
    session: dict[str, Any]

    @property
    def attach(self) -> bool:
        return self.launch.get("request") == "attach"


def workbench_path(launch: LaunchConfig, client: Client) -> Path:
    """The IAR installation whose target libraries the session loads."""
    config = client.config
    candidates = [launch.get("workbenchPath"), config.iar_path]
    exe = config.cspy_executable
    if exe is not None and exe.parent.name == "bin" and exe.parent.parent.name == "common":
        candidates.append(exe.parent.parent.parent)
    for candidate in candidates:
        if candidate:
            return Path(candidate)
    raise CSpyError(
        "This backend has no resolveLaunchConfiguration (IDE platform before 9.5), so the session "
        "configuration is built here, from the target libraries of the IAR installation. Give the "
        "installation as iar_path (--iar-path, IAR_INSTALL_PATH) or as \"workbenchPath\" in the "
        "launch configuration."
    )


def resolve_target_library(workbench: Path, target: str, base_name: str) -> Path | None:
    """``<workbench>/<target>/bin/[lib]<target><base>[tty].{so,dll}``, matched case-insensitively."""
    stem = base_name
    if not stem.startswith(target):
        stem = target + stem
    windows = os.name == "nt"
    if not windows and not stem.startswith("lib"):
        stem = "lib" + stem
    ext = ".dll" if windows else ".so"
    # Newer products split some libraries into command-line, Qt and MFC
    # versions: prefer the command-line ("tty") one.
    wanted = [(stem + "tty" + ext).lower(), (stem + ext).lower()]
    bin_dir = workbench / target.lower() / "bin"
    try:
        files = {f.name.lower(): f for f in bin_dir.iterdir()}
    except OSError:
        return None
    return next((files[name] for name in wanted if name in files), None)


def resolve(launch: LaunchConfig, client: Client) -> tuple[LegacyLaunch, Any]:
    """Build the session configuration, and the ``shared.LaunchConfiguration`` it amounts to.

    The latter is what :attr:`Debugger.launch_configuration` holds, as for
    newer products (the breakpoints take the driver from it).
    """
    for key in ("program", "target", "driver"):
        if not launch.get(key):
            raise CSpyError(f"The launch configuration has no {key!r}.")
    if "driverOptions" not in launch:
        raise CSpyError(
            "This backend has no resolveLaunchConfiguration (IDE platform before 9.5): the launch "
            "configuration needs \"driverOptions\", as the backend cannot fill them in."
        )
    program = str(launch["program"])
    if not Path(program).exists():
        raise CSpyError(f"The program '{program}' does not exist.")
    project_path = launch.get("projectPath")
    if project_path and not Path(project_path).exists():
        # Newer backends do not insist on it either; it only places the settings.
        log.warning("The project path '%s' does not exist; using the program's directory.", project_path)
        project_path = None

    workbench = workbench_path(launch, client)
    # The msp430 libraries lack the msp prefix.
    target = "430" if launch["target"] == "msp430" else str(launch["target"])
    options = [str(o) for o in launch["driverOptions"]]
    driver_name = str(launch["driver"])

    driver = _drivers.find_driver(driver_name, target, options)
    bases = driver.libraries if driver is not None else (driver_name,)
    driver_file = next((f for b in bases if (f := resolve_target_library(workbench, target, b))), None)
    if driver_file is None:
        raise CSpyError(f"Could not find driver file(s) '{','.join(bases)}' for '{workbench}'.")
    proc = resolve_target_library(workbench, target, "proc")
    if proc is None:
        raise CSpyError(f"Could not find library 'proc' for '{workbench}'.")

    plugins = [str(p) for p in launch.get("plugins") or []]
    libsupport = next((f for n in _LIBSUPPORT_NAMES if (f := resolve_target_library(workbench, target, n))), None)
    if libsupport is not None:
        plugins.append(str(libsupport))
    else:
        log.warning("No libsupport library for target %s: terminal I/O will not work.", target)
    macros = [str(m) for m in launch.get("setupMacros") or launch.get("macros") or []]

    project_dir = Path(project_path) if project_path else None
    if project_dir is not None and project_dir.is_file():
        project_dir = project_dir.parent
    project_name = Path(project_path).name if project_path else Path(program).stem

    session = {
        "type": "simulator",
        "driverName": str(driver_file),
        "processorName": str(proc),
        "options": options,
        "executable": program,
        "toolkitDir": str(workbench / target),
        "target": target,
        "projectName": project_name,
        "projectDir": str(project_dir or Path(program).parent),
        "stackSettings": {
            "fillEnabled": False,
            "overflowWarningsEnabled": True,
            "spWarningsEnabled": True,
            "warnLogOnly": True,
            "warningThreshold": 90,
            "useTrigger": True,
            "triggerName": "main",
            "limitDisplay": False,
            "displayLimit": 50,
        },
        "setupMacros": macros,
        "plugins": plugins,
        "configName": str(launch.get("projectConfiguration") or ""),
        "enableCRun": False,
        "attachToTarget": False,
        "leaveRunning": False,
    }
    legacy = LegacyLaunch(launch, session)

    resolved = client.idl.shared.LaunchConfiguration(
        program=program,
        programArgs=[str(a) for a in launch.get("args") or []],
        targetOrEmpty=target,
        processor=str(proc),
        driverNameOrEmpty=driver_name,
        driverLib=str(driver_file),
        driverOptions=options,
        setupMacros=macros,
        macroParams={},
        plugins=plugins,
        projectFilenameOrEmpty=Path(project_path).name if project_path else "",
        projectDirOrEmpty=str(project_dir) if project_dir else "",
        configNameOrEmpty=session["configName"],
        attachToTarget=legacy.attach,
        handleCRunEvents=bool(launch.get("handleCRunEvents", False)),
        leaveTargetRunning=bool(launch.get("leaveTargetRunning", False)),
        stopOnSymbol=str(launch.get("stopOnSymbol") or ""),
    )
    return legacy, resolved


def start(legacy: LegacyLaunch, call: Callable[..., Any]) -> None:
    """``startSession``, then the device macros, flashing, setup macros and the program.

    ``call(method, *args)`` calls a Debugger RPC.
    """
    launch = legacy.launch
    program = legacy.session["executable"]
    call("startSession", legacy.session)

    download = launch.get("download") or {}
    _load_macros(call, download.get("deviceMacros") or [])
    if not legacy.attach and download.get("flashLoader"):
        call("flashModule", str(download["flashLoader"]), program, [], [])

    extra = []
    for image in download.get("extraImages") or []:
        offset = image.get("offset", 0)
        extra.append(
            {
                "doDownload": not image.get("suppressDownload", False),
                "path": str(image["image"]),
                "offset": int(offset, 0) if isinstance(offset, str) else int(offset),
            }
        )
    options = {
        "resetAfterLoad": not legacy.attach,
        "callUserMacros": not legacy.attach,
        "onlyPrefixNotation": False,
        "suppressDownload": legacy.attach,
        "shouldAttach": legacy.attach,
        "shouldLeaveRunning": bool(launch.get("leaveTargetRunning", False)),
        "extraDebugFiles": extra,
    }
    _load_macros(call, legacy.session["setupMacros"])
    call("loadModuleWithOptions", program, options)


def _load_macros(call: Callable[..., Any], macros: list[str]) -> None:
    for macro in macros:
        try:
            call("loadMacroFile", str(macro))
        except CSpyError as exc:
            raise with_context(exc, f"Failed to load macro '{macro}': {exc}") from exc
