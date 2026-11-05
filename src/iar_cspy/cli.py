"""A standard ``argparse`` CLI for :class:`~iar_cspy.Config`.

Any tool built on `iar_cspy` needs to let its user pick a backend the same
handful of ways: an IAR installation to start, or the registry of one already
running, found by a port or by remote-control discovery. Rather than each
tool inventing its own flags for this, `iar_cspy` ships one definition, so
`iar-cspy`-based tools default to the same CLI for it::

    parser = argparse.ArgumentParser()
    add_connection_arguments(parser)
    args = parser.parse_args()
    config = config_from_args(args)

A tool with no flags of its own can skip the parser entirely::

    config = parse_config()

Either way, a flag only overrides :meth:`Config.from_env`'s environment
variable when actually given, so the two keep layering the way they already
do: ``iar-cspy-tool`` with nothing set reads ``IAR_INSTALL_PATH`` etc., same
as calling :meth:`Config.from_env` directly would.
"""

from __future__ import annotations

import argparse
import shlex
from typing import Any, Mapping

from .config import Config
from .registry_uri import format_registry_uri

#: Flags that, when given, mean "connect to an existing backend" rather than
#: "start a managed one" (see :func:`config_from_args`).
_STANDALONE_FLAGS = (
    "registry_uri",
    "remote_control_enabled",
    "remote_control_dir",
    "remote_control_workspace",
)


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the flags :func:`config_from_args` understands.

    Managed mode (start your own backend) is the default; any of
    ``--registry-uri``, ``--registry-host``/``--registry-port``,
    ``--remote-control``, ``--remote-control-dir``, or ``--workspace``
    switches to standalone mode (connect to one you don't own) instead.
    """
    managed = parser.add_argument_group("start your own backend (managed mode, the default)")
    managed.add_argument("--iar-path", help="IAR installation (the directory with common/bin under it).")
    managed.add_argument("--cspyserver2", help="CSpyServer2 executable, instead of --iar-path.")
    managed.add_argument("--cspyserver2-args", help="Extra CSpyServer2 command-line arguments (shell-quoted).")
    managed.add_argument("--service-launcher", help="IarServiceLauncher executable, instead of --iar-path.")
    ide_services = managed.add_mutually_exclusive_group()
    ide_services.add_argument(
        "--ide-services",
        metavar="NAMES",
        help="Comma-separated IDE services to host (projectmanager,options); default: both.",
    )
    ide_services.add_argument(
        "--no-ide-services",
        action="store_true",
        help="Debugger only: do not start an IarServiceLauncher at all.",
    )

    standalone = parser.add_argument_group("connect to one you don't own (standalone mode)")
    standalone.add_argument(
        "--registry-uri", metavar="URI", help='Service registry URI, e.g. "thrift://127.0.0.1:51926".'
    )
    standalone.add_argument("--registry-host", help="Service registry host, with --registry-port.")
    standalone.add_argument("--registry-port", type=int, help="Service registry port, with --registry-host.")
    standalone.add_argument("--registry-service", help="Registry name of the debugger service.")
    standalone.add_argument(
        "--remote-control",
        action="store_true",
        default=None,
        help="Find the registry through remote-control discovery files (~/.iar/remote_control by default).",
    )
    standalone.add_argument(
        "--remote-control-dir", help="Directory of *.remote discovery files to scan, instead of the default."
    )
    standalone.add_argument(
        "--workspace",
        help="Narrow remote-control discovery to IDEs running this workspace/project; implies --remote-control.",
    )
    require_alive = standalone.add_mutually_exclusive_group()
    require_alive.add_argument(
        "--remote-control-require-alive",
        dest="remote_control_require_alive",
        action="store_true",
        default=None,
        help="Skip discovery files whose process is no longer running (the default).",
    )
    require_alive.add_argument(
        "--remote-control-allow-dead",
        dest="remote_control_require_alive",
        action="store_false",
        help="Also consider discovery files whose process is no longer running.",
    )


def config_from_args(args: argparse.Namespace, env: Mapping[str, str] | None = None) -> Config:
    """The :class:`Config` described by ``args`` (from :func:`add_connection_arguments`) and the environment.

    Only flags actually given override the environment/defaults, so
    ``Config.from_env(env, **overrides)`` layering still applies. Raises
    ``ValueError`` for a combination that doesn't make sense (both a registry
    URI and a port, or a host with no port).
    """
    overrides: dict[str, Any] = {}

    if getattr(args, "iar_path", None):
        overrides["iar_path"] = args.iar_path
    if getattr(args, "cspyserver2", None):
        overrides["cspy_executable"] = args.cspyserver2
    cspyserver2_args = getattr(args, "cspyserver2_args", None)
    if cspyserver2_args:
        overrides["cspy_args"] = tuple(p for p in shlex.split(cspyserver2_args, posix=False) if p)
    if getattr(args, "service_launcher", None):
        overrides["launcher_executable"] = args.service_launcher
    if getattr(args, "no_ide_services", False):
        overrides["host_ide_services"] = False
    ide_services = getattr(args, "ide_services", None)
    if ide_services:
        overrides["ide_services"] = tuple(s.strip().lower() for s in ide_services.split(",") if s.strip())

    registry_uri = getattr(args, "registry_uri", None)
    registry_host = getattr(args, "registry_host", None)
    registry_port = getattr(args, "registry_port", None)
    if registry_port is not None:
        if registry_uri is not None:
            raise ValueError("Both --registry-uri and --registry-port were given; use one.")
        registry_uri = format_registry_uri(registry_host or "127.0.0.1", registry_port)
    elif registry_host is not None:
        raise ValueError("--registry-host was given without --registry-port.")
    if registry_uri is not None:
        overrides["registry_uri"] = registry_uri

    if getattr(args, "registry_service", None):
        overrides["registry_service_name"] = args.registry_service

    if getattr(args, "remote_control", None):
        overrides["remote_control_enabled"] = True
    if getattr(args, "remote_control_dir", None):
        overrides["remote_control_dir"] = args.remote_control_dir
    if getattr(args, "workspace", None):
        overrides["remote_control_workspace"] = args.workspace
    require_alive = getattr(args, "remote_control_require_alive", None)
    if require_alive is not None:
        overrides["remote_control_require_alive"] = require_alive

    if any(overrides.get(flag) is not None for flag in _STANDALONE_FLAGS):
        overrides["mode"] = "standalone"

    return Config.from_env(env, **overrides)


def parse_config(
    argv: list[str] | None = None, *, description: str | None = None, env: Mapping[str, str] | None = None
) -> Config:
    """One-shot convenience for a tool with no flags of its own besides these.

    Builds a parser with just :func:`add_connection_arguments`, parses
    ``argv`` (``sys.argv[1:]`` if not given), and returns the resulting
    :class:`Config`. For a tool with its own flags too, use
    :func:`add_connection_arguments`/:func:`config_from_args` on its own
    parser instead.
    """
    parser = argparse.ArgumentParser(description=description)
    add_connection_arguments(parser)
    return config_from_args(parser.parse_args(argv), env=env)
