"""Starting a session on a backend without the launch-configuration API.

CSpyServer2 from IDE platforms before 9.5 (EWARM 9.70.4 ships 9.4.7) has no
``resolveLaunchConfiguration``, ``configureSession`` or ``startSMPSession``:
it answers them with ``TApplicationException: Invalid method name``. The IAR
VS Code debug extension starts a session on those with its legacy init
sequence (``runLegacyInitSequence``), which this module follows:

1. ``startSession`` with a ``SessionConfiguration`` naming the driver,
   processor and LibSupport libraries of the installation,
2. the device macros, then the flash loader (``flashModule``) when the
   download configuration names one,
3. the setup macros, then ``loadModuleWithOptions`` for the program.

There is nothing to resolve, so :func:`launch_configuration` builds the
``shared.LaunchConfiguration`` the backend would have, for what the package
reads from it (the driver, its options).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import _drivers
from .errors import CSpyError
from .launch import LaunchConfig

if TYPE_CHECKING:
    from .client import Client

#: thriftpy2 ``TApplicationException.UNKNOWN_METHOD``.
_UNKNOWN_METHOD = 1
#: LibSupport library base names, in the order the extension tries them.
_LIBSUPPORT = ("LibSupportUniversal", "LibSupportEclipse", "LibSupport")
#: List windows an IDE has open. Without one connected, these backends do not
#: move their context to where an asynchronous stop (a pause) halted a
#: hardware target, and every register reads back empty until a breakpoint.
CONTEXT_WINDOWS = ("WIN_CALL_STACK", "WIN_REGISTER_1")


def missing_method(exc: BaseException, method: str) -> bool:
    """Whether ``exc`` is the backend saying it has no RPC called ``method``."""
    seen = exc
    while seen is not None:
        if getattr(seen, "type", None) == _UNKNOWN_METHOD and method in str(seen):
            return True
        seen = seen.__cause__
    return f"Invalid method name: '{method}'" in str(exc)


def launch_configuration(client: Client, config: LaunchConfig) -> Any:
    """The ``shared.LaunchConfiguration`` of ``config``, built here instead of by the backend."""
    target = _target(config)
    download = config.get("download") or {}
    project = config.get("projectPath") or ""
    return client.idl.shared.LaunchConfiguration(
        program=str(config.get("program") or ""),
        programArgs=list(config.get("args") or []),
        targetOrEmpty=target,
        processor="",
        driverNameOrEmpty=str(config.get("driver") or ""),
        driverLib=str(_driver_library(client, config)),
        driverOptions=list(config.get("driverOptions") or []),
        setupMacros=_setup_macros(config),
        macroParams={},
        plugins=list(config.get("plugins") or []),
        projectFilenameOrEmpty=Path(project).name if project else "",
        projectDirOrEmpty=_project_dir(config),
        configNameOrEmpty=str(config.get("projectConfiguration") or ""),
        attachToTarget=_attach(config),
        handleCRunEvents=bool(config.get("handleCRunEvents", False)),
        leaveTargetRunning=bool(config.get("leaveTargetRunning", False)),
        stopOnSymbol=str(config.get("stopOnSymbol") or ""),
        download=client.idl.shared.DownloadConfiguration(
            flashLoader=str(download.get("flashLoader") or ""),
            deviceMacros=list(download.get("deviceMacros") or []),
            suppressAllDownloads=bool(download.get("suppressAllDownloads", False)),
            suppressProgramDownload=bool(download.get("suppressProgramDownload", False)),
            performMassErase=bool(download.get("performMassErase", False)),
            verifyAllDownloads=bool(download.get("verifyAllDownloads", False)),
        ),
    )


def start(client: Client, config: LaunchConfig, resolved: Any) -> None:
    """Start and load the session the way the VS Code extension does on these backends."""
    target = _target(config)
    attach = _attach(config)
    download = config.get("download") or {}
    suppress = attach or bool(download.get("suppressProgramDownload")) or bool(download.get("suppressAllDownloads"))
    program = str(config.get("program") or "")
    project = config.get("projectPath")
    workbench = _workbench(client, config)
    plugins = list(config.get("plugins") or [])
    libsupport = _first_library(workbench, target, _LIBSUPPORT)
    if libsupport is not None:
        plugins.append(str(libsupport))

    client.call(
        "debugger",
        "startSession",
        {
            "type": "simulator",  # what the extension sends for every driver
            "driverName": resolved.driverLib,
            "processorName": str(_required_library(workbench, target, ("proc",))),
            "options": list(config.get("driverOptions") or []),
            "executable": program,
            "toolkitDir": str(workbench / target),
            "target": target,
            "projectName": Path(project).name if project else Path(program).stem,
            "projectDir": _project_dir(config),
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
            "setupMacros": resolved.setupMacros,
            "plugins": plugins,
            "configName": str(config.get("projectConfiguration") or ""),
            "enableCRun": False,
            "attachToTarget": attach,
            "leaveRunning": False,
        },
    )
    _open_context_windows(client)
    for macro in download.get("deviceMacros") or []:
        client.call("debugger", "loadMacroFile", macro)
    if not suppress and download.get("flashLoader"):
        client.call("debugger", "flashModule", download["flashLoader"], program, [], [])
    for macro in resolved.setupMacros:
        client.call("debugger", "loadMacroFile", macro)
    client.call(
        "debugger",
        "loadModuleWithOptions",
        program,
        {
            "resetAfterLoad": not attach,
            "callUserMacros": not attach,
            "onlyPrefixNotation": False,
            "suppressDownload": suppress,
            "shouldAttach": attach,
            "shouldLeaveRunning": bool(config.get("leaveTargetRunning", False)),
            "extraDebugFiles": [_extra_image(image) for image in download.get("extraImages") or []],
        },
    )
    _open_context_windows(client)


def _open_context_windows(client: Client) -> None:
    """Connect the :data:`CONTEXT_WINDOWS` that are registered by now.

    Only for a backend this process owns: one shared with an IDE has the
    IDE's windows, and would be left calling this process after it exits.
    """
    if client.backend.mode != "managed":
        return
    for window in CONTEXT_WINDOWS:
        client.listwindows.connect(window)


def _target(config: LaunchConfig) -> str:
    return str(config.get("target") or "arm").lower()


def _attach(config: LaunchConfig) -> bool:
    return config.get("request") == "attach"


def _setup_macros(config: LaunchConfig) -> list[str]:
    return list(config.get("setupMacros") or config.get("macros") or [])


def _project_dir(config: LaunchConfig) -> str:
    """Where the backend keeps the session's settings (``settings/*.dnx``).

    A project path that does not exist falls back to the program's directory:
    the backend would create a directory of that name.
    """
    project = Path(str(config.get("projectPath") or ""))
    if str(project) != "." and project.is_file():
        return str(project.parent)
    if str(project) != "." and project.is_dir():
        return str(project)
    return str(Path(str(config.get("program") or "")).parent)


def _extra_image(image: dict[str, Any]) -> dict[str, Any]:
    offset = image.get("offset", 0)
    return {
        "doDownload": not image.get("suppressDownload", False),
        "path": str(image.get("image") or ""),
        "offset": int(offset, 0) if isinstance(offset, str) else int(offset),
    }


def _workbench(client: Client, config: LaunchConfig) -> Path:
    """The installation: the launch's ``workbenchPath`` (as in the extension), else the backend's."""
    given = config.get("workbenchPath") or client.config.iar_path
    if given:
        return Path(given)
    executable = client.config.cspy_executable
    if executable is not None:
        return Path(executable).resolve().parents[2]  # <workbench>/common/bin/CSpyServer2
    raise CSpyError(
        "This backend has no launch-configuration API, so the session is started from the "
        "installation's driver libraries, and the installation is not known. Give the "
        'launch configuration a "workbenchPath", or run with --cspy-iar-path / IAR_INSTALL_PATH.'
    )


def _driver_library(client: Client, config: LaunchConfig) -> Path:
    target = _target(config)
    name = str(config.get("driver") or "")
    driver = _drivers.find_driver(name, target, list(config.get("driverOptions") or []))
    if driver is None or not driver.libraries:
        raise CSpyError(f"Unknown driver {name!r}: cannot tell which library starts it on this backend.")
    return _required_library(_workbench(client, config), target, driver.libraries)


def _required_library(workbench: Path, target: str, bases: tuple[str, ...]) -> Path:
    found = _first_library(workbench, target, bases)
    if found is None:
        wanted = ", ".join(_file_names(target, base)[0] for base in bases)
        raise CSpyError(f"None of {wanted} is in {_bin_dir(workbench, target)}.")
    return found


def _first_library(workbench: Path, target: str, bases: tuple[str, ...]) -> Path | None:
    bin_dir = _bin_dir(workbench, target)
    try:
        present = {entry.lower(): entry for entry in os.listdir(bin_dir)}
    except OSError:
        return None
    for base in bases:
        for name in _file_names(target, base):
            if name.lower() in present:
                return bin_dir / present[name.lower()]
    return None


def _bin_dir(workbench: Path, target: str) -> Path:
    return workbench / target / "bin"


def _file_names(target: str, base: str) -> tuple[str, str]:
    prefix = "430" if target == "msp430" else target  # the drivers use 430 as prefix
    return f"{prefix}{base}.dll", f"lib{prefix}{base}.so"
