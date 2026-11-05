"""Finding a running IDE's service registry through remote-control files.

An IAR IDE that hosts remote control writes one ``<pid>.remote`` JSON file per
running instance (default directory ``~/.iar/remote_control``, or
``%LOCALAPPDATA%\\IAR Embedded Workbench\\remote_control`` on Windows), e.g.::

    {
        "process_id" : "2545927",
        "remote_control_service_registry" : "thrift://127.0.0.1:35695",
        "sessionid" : "2545927",
        "start_time" : "2026-10-29T14:55:49.691885848Z",
        "version" : "IAR Embedded Workbench 9.6.1.0",
        "workspace" : "/path/to/project.eww"
    }

:func:`discover` globs that directory and returns the matching files, newest
first, for a caller to try connecting to in turn: a stale file can outlive its
process, and a live process is not proof its registry still answers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .registry_uri import parse_registry_uri

def _default_local_running_state_folder() -> Path:
    """Mirrors the IDE's own ``WuFileHelpers::GetLocalRunningStateFolder()``."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "IAR Embedded Workbench"
    return Path.home() / ".iar"


#: Where IAR IDEs write ``*.remote`` discovery files by default.
DEFAULT_REMOTE_CONTROL_DIR = _default_local_running_state_folder() / "remote_control"


@dataclass(frozen=True)
class RemoteControlInfo:
    """One parsed ``.remote`` file."""

    path: Path
    registry_uri: str
    process_id: int | None = None
    sessionid: str | None = None
    workspace: str | None = None
    version: str | None = None
    start_time: str | None = None
    #: The full parsed JSON, for fields not promoted to an attribute above.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        """Best-effort: a PID match is not proof the registry still answers."""
        return self.process_id is not None and is_process_alive(self.process_id)


def is_process_alive(pid: int) -> bool:
    """Whether a process with this PID currently exists."""
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, just owned by someone else.
        return True
    return True


def _str_or_none(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    return None if value is None else str(value)


def _load_one(path: Path) -> RemoteControlInfo | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    registry_uri = str(raw.get("remote_control_service_registry", ""))
    try:
        parse_registry_uri(registry_uri)
    except ValueError:
        return None
    pid_raw = raw.get("process_id")
    try:
        pid = int(pid_raw) if pid_raw is not None else None
    except (TypeError, ValueError):
        pid = None
    return RemoteControlInfo(
        path=path,
        registry_uri=registry_uri,
        process_id=pid,
        sessionid=_str_or_none(raw, "sessionid"),
        workspace=_str_or_none(raw, "workspace"),
        version=_str_or_none(raw, "version"),
        start_time=_str_or_none(raw, "start_time"),
        raw=raw,
    )


def _resolved(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def discover(
    filter: str | Callable[[RemoteControlInfo], bool] | None = None,
    directory: Path | str | None = None,
    *,
    require_alive: bool = True,
) -> list[RemoteControlInfo]:
    """Find candidate service registries from ``*.remote`` discovery files.

    ``filter`` is first since it's the one callers almost always give; the
    rest (``directory``, ``require_alive``) are almost always left default.
    A string is shorthand for "only this workspace" (resolved to an absolute
    path, as ``workspace=`` used to work); anything else is a predicate over
    :class:`RemoteControlInfo` for other criteria (sessionid, version, ...).
    Either way it's a hard filter: without it, every candidate is returned.

    ``directory`` defaults to :data:`DEFAULT_REMOTE_CONTROL_DIR`. The result
    is newest (``start_time``) first either way, for the caller to try
    connecting to in turn.
    """
    base = Path(directory) if directory is not None else DEFAULT_REMOTE_CONTROL_DIR
    candidates: list[RemoteControlInfo] = []
    if base.is_dir():
        for file_path in sorted(base.glob("*.remote")):
            info = _load_one(file_path)
            if info is None:
                continue
            if require_alive and not info.alive:
                continue
            candidates.append(info)

    if isinstance(filter, str):
        target = _resolved(filter)
        candidates = [c for c in candidates if c.workspace is not None and _resolved(c.workspace) == target]
    elif filter is not None:
        candidates = [c for c in candidates if filter(c)]

    candidates.sort(key=lambda c: c.start_time or "", reverse=True)
    return candidates
