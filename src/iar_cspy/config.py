"""Configuration: where the backend is, how to start it, and how to talk to it.

Build a :class:`Config` directly (every field has a default)::

    Config(iar_path="/opt/iar/ewarm")                       # managed backend
    Config(mode="standalone", registry_port=51926)          # already running

or read the ``THRIFT_*`` / ``IAR_INSTALL_PATH`` environment variables the MCP
server documents with :meth:`Config.from_env`.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, Mapping

from ._thrift import BUNDLED_IDL_DIR

Mode = Literal["managed", "standalone"]

#: ``external`` is the name standalone mode had before the modes were reduced
#: to two; ``launcher`` named what is now just part of managed mode.
_MODE_ALIASES = {"external": "standalone", "launcher": "managed"}

DEFAULT_CSPY_ARGS = ("-standalone", "-sockets")


def normalize_mode(raw: str | None) -> Mode:
    """Resolve a mode name to ``managed`` or ``standalone``.

    managed:    this process starts and supervises the backend itself.
    standalone: the backend is already running; connect to its registry.
    """
    mode = (raw or "managed").strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    return mode if mode in {"managed", "standalone"} else "managed"  # type: ignore[return-value]


def iar_bin_dir(iar_path: Path | None) -> Path | None:
    """Executables and service libraries live in <iar_path>/common/bin.

    That layout is stable across IAR products and build stages, so it is
    derived rather than configured per program.
    """
    return iar_path / "common" / "bin" if iar_path is not None else None


def iar_executable(bin_dir: Path | None, name: str) -> Path | None:
    """Locate a program in an installation, or None when it does not ship it.

    Existence is checked here, and only for installation-derived paths: a
    compiler-only toolchain has no IarServiceLauncher, and the backend should
    quietly do without rather than fail. A path given explicitly is used
    unchecked so that a typo surfaces as an error later on.
    """
    if bin_dir is None:
        return None
    candidate = bin_dir / (f"{name}.exe" if os.name == "nt" else name)
    return candidate if candidate.exists() else None


def _path(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class Config:
    """Settings for a :class:`~iar_cspy.Backend` and :class:`~iar_cspy.Client`.

    Program paths not given explicitly are derived from ``iar_path`` (the
    directory with ``common/bin`` under it). ``host_ide_services=False`` drops
    the IarServiceLauncher, leaving a debugger-only backend.
    """

    mode: Mode = "managed"
    iar_path: Path | None = None

    # Standalone mode: the registry of an already-running backend.
    registry_host: str | None = None
    registry_port: int | None = None
    registry_service_name: str = "debugger"
    # Direct debugger endpoint, used only when there is no registry at all.
    host: str = "127.0.0.1"
    port: int = 9090
    timeout_ms: int = 15000

    # IDL. Defaults to the files bundled with this package.
    thrift_file: Path = BUNDLED_IDL_DIR / "cspy.thrift"
    include_dirs: tuple[str, ...] = ()

    # Managed mode: CSpyServer2.
    cspy_executable: Path | None = None
    cspy_args: tuple[str, ...] = DEFAULT_CSPY_ARGS
    cspy_start_timeout_ms: int = 20000
    cspy_restart_on_failure: bool = True

    # Managed mode: IarServiceLauncher, hosting the IDE services. It has to
    # load the service implementations (for ProjectManager, the whole legacy
    # project manager), so it is noticeably slower to become ready.
    launcher_executable: Path | None = None
    launcher_start_timeout_ms: int = 40000
    launcher_restart_on_failure: bool = True
    service_bin_dir: Path | None = None
    host_ide_services: bool = True
    #: IDE services to host (``projectmanager``, ``options``); empty means all.
    ide_services: tuple[str, ...] = ()
    #: Start a missing IDE service on first use.
    auto_ide_services: bool = True
    projectmanager_service_name: str = "com.iar.thrift.service.projectmanager"
    options_service_name: str = "com.iar.optionsservice"

    # Callback services this process registers with the backend.
    auto_eventhandler: bool = True
    auto_libsupport: bool = True
    eventhandler_client_timeout_ms: int = 3600000
    libsupport_client_timeout_ms: int = 3600000
    listwindow_client_timeout_ms: int = 3600000

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731

        set_("mode", normalize_mode(self.mode))
        set_("iar_path", _path(self.iar_path))
        set_("thrift_file", _path(self.thrift_file) or BUNDLED_IDL_DIR / "cspy.thrift")
        set_("cspy_executable", _path(self.cspy_executable))
        set_("launcher_executable", _path(self.launcher_executable))
        set_("service_bin_dir", _path(self.service_bin_dir))
        set_("cspy_args", tuple(self.cspy_args))
        set_("ide_services", tuple(s.strip().lower() for s in self.ide_services if s.strip()))
        if self.registry_port is not None:
            set_("registry_port", int(self.registry_port))

        include_dirs = [str(d) for d in self.include_dirs]
        thrift_parent = str(self.thrift_file.parent)
        if thrift_parent not in include_dirs:
            include_dirs.append(thrift_parent)
        set_("include_dirs", tuple(include_dirs))

        # One path for the whole toolchain; explicit program paths win over it.
        bin_dir = iar_bin_dir(self.iar_path)
        if self.cspy_executable is None:
            set_("cspy_executable", iar_executable(bin_dir, "CSpyServer2"))
        if self.launcher_executable is None:
            set_("launcher_executable", iar_executable(bin_dir, "IarServiceLauncher"))
        if self.service_bin_dir is None:
            set_("service_bin_dir", bin_dir)
        if not self.host_ide_services:
            # Debugger only: do not start an IarServiceLauncher at all.
            set_("launcher_executable", None)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: object) -> Config:
        """Read the environment variables documented for the MCP server.

        ``overrides`` are applied on top, by field name.
        """
        env = os.environ if env is None else env

        def get(name: str, default: str | None = None) -> str | None:
            value = env.get(name)
            return default if value is None else value

        def get_bool(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None:
                return default
            return raw.strip().lower() not in {"0", "false", "no", "off"}

        def get_int(name: str, default: int) -> int:
            raw = env.get(name)
            return int(raw) if raw else default

        def split_list(raw: str | None, seps: str) -> tuple[str, ...]:
            if not raw:
                return ()
            for sep in seps[1:]:
                raw = raw.replace(sep, seps[0])
            return tuple(part.strip() for part in raw.split(seps[0]) if part.strip())

        cspy_args_raw = get("THRIFT_CSPYSERVER_ARGS")
        registry_port = get("THRIFT_REGISTRY_PORT")
        values: dict[str, object] = {
            "mode": normalize_mode(get("THRIFT_CSPYSERVER_MODE")),
            "iar_path": get("IAR_INSTALL_PATH"),
            "registry_host": get("THRIFT_REGISTRY_HOST"),
            "registry_port": int(registry_port) if registry_port else None,
            "registry_service_name": get("THRIFT_REGISTRY_SERVICE", "debugger"),
            "host": get("THRIFT_HOST", "127.0.0.1"),
            "port": get_int("THRIFT_PORT", 9090),
            "timeout_ms": get_int("THRIFT_TIMEOUT_MS", 15000),
            "thrift_file": get("THRIFT_FILE") or BUNDLED_IDL_DIR / "cspy.thrift",
            "include_dirs": split_list(get("THRIFT_INCLUDE_DIRS"), ";"),
            "cspy_executable": get("THRIFT_CSPYSERVER_EXE"),
            "cspy_args": (
                tuple(p for p in shlex.split(cspy_args_raw, posix=False) if p)
                if cspy_args_raw
                else DEFAULT_CSPY_ARGS
            ),
            "cspy_start_timeout_ms": get_int("THRIFT_CSPYSERVER_START_TIMEOUT_MS", 20000),
            "cspy_restart_on_failure": get_bool("THRIFT_CSPYSERVER_RESTART_ON_FAILURE", True),
            "launcher_executable": get("THRIFT_SERVICE_LAUNCHER_EXE"),
            "launcher_start_timeout_ms": get_int("THRIFT_SERVICE_LAUNCHER_START_TIMEOUT_MS", 40000),
            "launcher_restart_on_failure": get_bool("THRIFT_SERVICE_LAUNCHER_RESTART_ON_FAILURE", True),
            "service_bin_dir": get("THRIFT_SERVICE_BIN_DIR"),
            "host_ide_services": get_bool("THRIFT_HOST_IDE_SERVICES", True),
            "ide_services": tuple(s.lower() for s in split_list(get("THRIFT_IDE_SERVICES"), ",;")),
            "auto_ide_services": get_bool("THRIFT_AUTO_IDE_SERVICES", True),
            "projectmanager_service_name": get(
                "THRIFT_PROJECTMANAGER_SERVICE_NAME", "com.iar.thrift.service.projectmanager"
            ),
            "options_service_name": get("THRIFT_OPTIONSSERVICE_SERVICE_NAME", "com.iar.optionsservice"),
            "auto_eventhandler": get_bool("THRIFT_AUTO_EVENTHANDLER", True),
            "auto_libsupport": get_bool("THRIFT_AUTO_LIBSUPPORT", True),
            "eventhandler_client_timeout_ms": get_int("THRIFT_EVENTHANDLER_CLIENT_TIMEOUT_MS", 3600000),
            "libsupport_client_timeout_ms": get_int("THRIFT_LIBSUPPORT_CLIENT_TIMEOUT_MS", 3600000),
            "listwindow_client_timeout_ms": get_int("THRIFT_LISTWINDOW_CLIENT_TIMEOUT_MS", 3600000),
        }
        unknown = set(overrides) - {f.name for f in fields(cls)}
        if unknown:
            raise TypeError(f"Unknown Config field(s): {', '.join(sorted(unknown))}")
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-safe view of the settings."""
        out: dict[str, object] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Path):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(value)
            out[f.name] = value
        return out
