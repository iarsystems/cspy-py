"""Launch configurations: what to debug, and how.

A launch configuration is one entry of a C-SPY VS Code ``launch.json``, the
same JSON the IAR VS Code debug extension uses. The backend resolves it
(``Debugger.resolveLaunchConfiguration``) into a full session configuration.
A minimal simulator configuration::

    {
      "type": "cspy",
      "request": "launch",
      "name": "Simulator",
      "target": "arm",
      "program": "/abs/path/Debug/Exe/app.out",
      "driver": "Simulator",
      "driverOptions": ["--cpu=Cortex-M3", "--semihosting"],
      "stopOnSymbol": "main"
    }

For hardware, ``driver`` names the probe driver and ``driverOptions`` carries
its options. Rather than writing one by hand, let the IAR VS Code extension
generate a launch.json, or use
:meth:`iar_cspy.project.Project.launch_configuration`, which returns the
backend's own configuration for an Embedded Workbench project.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import CSpyError

_WORKSPACE_FOLDER = "${workspaceFolder}"

#: Fields holding one path. ``${workspaceFolder}`` is the current directory,
#: and relative paths are resolved against it.
_PATH_FIELDS = ("program", "projectPath")
#: Fields holding a list of paths, resolved like :data:`_PATH_FIELDS`.
_PATH_LIST_FIELDS = ("setupMacros", "plugins")
#: ``download`` fields holding one path, or a list of paths.
_DOWNLOAD_PATH_FIELDS = ("flashLoader",)
_DOWNLOAD_PATH_LIST_FIELDS = ("deviceMacros",)
#: Fields mixing paths with other values (``--cpu=...``): only
#: ``${workspaceFolder}`` is substituted, relative values are left as they are.
_MIXED_LIST_FIELDS = ("driverOptions",)


def _substitute(value: str) -> str:
    return value.replace(_WORKSPACE_FOLDER, str(Path.cwd()))


def _resolve_path(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    value = _substitute(value)
    return value if Path(value).is_absolute() else str(Path(value).absolute())


def _resolve_list(values: Any, resolve: Any) -> Any:
    if not isinstance(values, list):
        return values
    return [resolve(v) if isinstance(v, str) else v for v in values]


def _resolve_paths(config: dict[str, Any]) -> None:
    """Resolve the path fields of ``config`` in place (idempotent)."""
    for key in _PATH_FIELDS:
        if key in config:
            config[key] = _resolve_path(config[key])
    for key in _PATH_LIST_FIELDS:
        if key in config:
            config[key] = _resolve_list(config[key], _resolve_path)
    for key in _MIXED_LIST_FIELDS:
        if key in config:
            config[key] = _resolve_list(config[key], _substitute)
    download = config.get("download")
    if isinstance(download, dict):
        for key in _DOWNLOAD_PATH_FIELDS:
            if key in download:
                download[key] = _resolve_path(download[key])
        for key in _DOWNLOAD_PATH_LIST_FIELDS:
            if key in download:
                download[key] = _resolve_list(download[key], _resolve_path)
        images = download.get("extraImages")
        if isinstance(images, list):
            for image in images:
                if isinstance(image, dict) and "image" in image:
                    image["image"] = _resolve_path(image["image"])


class LaunchConfig(dict):
    """One launch configuration (a ``dict`` of the launch.json fields)."""

    @classmethod
    def from_json(cls, text: str) -> LaunchConfig:
        """Parse one configuration, or a ``{"configurations": [...]}`` file's first."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CSpyError(f"Invalid launch configuration JSON: {exc}") from exc
        return cls.from_obj(data)

    @classmethod
    def from_obj(cls, data: Mapping[str, Any], name: str | None = None) -> LaunchConfig:
        """Take one configuration, or a ``{"configurations": [...]}`` object's.

        Path fields are resolved: ``${workspaceFolder}`` is the current
        directory, and relative paths are made absolute against it. ``data``
        is not modified.
        """
        if not isinstance(data, Mapping):
            raise CSpyError("A launch configuration must be a JSON object")
        if "configurations" in data:
            configurations = data["configurations"]
            if not configurations:
                raise CSpyError("launch.json has no configurations")
            if name is None:
                entry = configurations[0]
            else:
                found = [entry for entry in configurations if entry.get("name") == name]
                if not found:
                    names = ", ".join(repr(entry.get("name")) for entry in configurations)
                    raise CSpyError(f"No launch configuration named {name!r}; available: {names}")
                entry = found[0]
        else:
            entry = data
        config = cls(copy.deepcopy(dict(entry)))
        _resolve_paths(config)
        return config

    @classmethod
    def from_file(cls, path: str | Path, name: str | None = None) -> LaunchConfig:
        """Read a launch.json, picking the configuration called ``name`` (default: first).
        """
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CSpyError(f"Cannot read launch configuration {path}: {exc}") from exc
        return cls.from_obj(data, name)

    @classmethod
    def coerce(cls, value: LaunchConfig | Mapping[str, Any] | str | Path) -> LaunchConfig:
        """Accept a LaunchConfig, a dict, JSON text, or a path to a launch.json."""
        if isinstance(value, LaunchConfig):
            return value
        if isinstance(value, Path):
            return cls.from_file(value)
        if isinstance(value, str):
            stripped = value.lstrip()
            if stripped.startswith("{"):
                return cls.from_json(value)
            return cls.from_file(value)
        return cls.from_obj(value)

    @property
    def stop_on_symbol(self) -> str | None:
        return self.get("stopOnSymbol") or None

    def to_json(self) -> str:
        return json.dumps(self)
