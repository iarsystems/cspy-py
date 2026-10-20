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

import json
from pathlib import Path
from typing import Any, Mapping

from .errors import CSpyError

#: Fields holding paths that are resolved against the launch.json directory.
_PATH_FIELDS = ("program", "projectPath")


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
        if not isinstance(data, Mapping):
            raise CSpyError("A launch configuration must be a JSON object")
        if "configurations" in data:
            configurations = data["configurations"]
            if not configurations:
                raise CSpyError("launch.json has no configurations")
            if name is None:
                return cls(configurations[0])
            for entry in configurations:
                if entry.get("name") == name:
                    return cls(entry)
            names = ", ".join(repr(entry.get("name")) for entry in configurations)
            raise CSpyError(f"No launch configuration named {name!r}; available: {names}")
        return cls(data)

    @classmethod
    def from_file(cls, path: str | Path, name: str | None = None) -> LaunchConfig:
        """Read a launch.json, picking the configuration called ``name`` (default: first).
        """
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CSpyError(f"Cannot read launch configuration {path}: {exc}") from exc
        config = cls.from_obj(data, name)
        for key in _PATH_FIELDS:
            value = config.get(key)
            if value:
                value = value.replace("${workspaceFolder}", str(Path.cwd()))
                if not Path(value).is_absolute():
                    value = str(Path(value).absolute())
                config[key] = value
        return config

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
