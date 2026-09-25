"""pytest fixtures for testing firmware against C-SPY (hardware or simulator).

Installing iar-cspy registers the plugin with pytest, so the fixtures are
available without further setup (``-p no:iar_cspy.pytest_plugin`` turns it
off). Then::

    def test_counter_increments(cspy_session):
        cspy_session.client.breakpoints.add("tick")
        cspy_session.go_and_wait(timeout=5)
        assert cspy_session.eval_int("counter") > 0

and run ``pytest --cspy-iar-path=/opt/iar/ewarm --cspy-launch=launch.json``
(use the ``--opt=value`` form for paths: pytest mistakes a separate path
argument for a test path when it looks for the rootdir).

Where the backend comes from, in order: the ``--cspy-*`` options below, then
the environment variables the MCP server uses (``IAR_INSTALL_PATH``,
``THRIFT_CSPYSERVER_EXE``, ``THRIFT_REGISTRY_PORT``, ...). Tests using these
fixtures are skipped when no backend is configured, so the same suite runs on
machines without an IAR installation.

Fixtures:

``cspy_client``
    Session-scoped :class:`~iar_cspy.Client`; a managed backend is shut down
    at the end of the test session.
``cspy_launch``
    The :class:`~iar_cspy.LaunchConfig` from ``--cspy-launch``. Override this
    fixture to point at your own launch.json.
``cspy_session``
    A started debug session for ``cspy_launch`` (a :class:`~iar_cspy.Debugger`;
    ``cspy_session.client`` is the client), stopped at its ``stopOnSymbol``
    and stopped after the test. Every test gets a fresh session, with the
    terminal output and the debug event buffer cleared.

The ``live`` marker is registered for tests that need a backend.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from .client import Client
from .config import Config
from .debugger import Debugger
from .launch import LaunchConfig


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("iar-cspy", "C-SPY backend for iar_cspy fixtures")
    group.addoption("--cspy-iar-path", default=None, help="IAR installation to start a managed backend from.")
    group.addoption("--cspy-cspyserver2", default=None, help="CSpyServer2 executable (overrides --cspy-iar-path).")
    group.addoption(
        "--cspy-registry",
        default=None,
        metavar="[HOST:]PORT",
        help="Service registry of an already-running backend (standalone mode).",
    )
    group.addoption("--cspy-launch", default=None, help="launch.json for the cspy_session fixture.")


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "live: requires a C-SPY backend (see the --cspy-* options)")


def config_from_options(pytestconfig: Any) -> Config:
    """The :class:`Config` the ``--cspy-*`` options and environment describe."""
    overrides: dict[str, Any] = {}
    iar_path = pytestconfig.getoption("--cspy-iar-path")
    cspyserver2 = pytestconfig.getoption("--cspy-cspyserver2")
    registry = pytestconfig.getoption("--cspy-registry")
    if iar_path or cspyserver2:
        overrides.update(mode="managed", registry_host=None, registry_port=None)
    if iar_path:
        overrides["iar_path"] = iar_path
    if cspyserver2:
        overrides["cspy_executable"] = cspyserver2
    if registry:
        host, _, port = str(registry).rpartition(":")
        overrides.update(mode="standalone", registry_host=host or "127.0.0.1", registry_port=int(port))
    return Config.from_env(**overrides)


def backend_configured(config: Config) -> bool:
    if config.mode == "standalone":
        return config.registry_port is not None
    return config.cspy_executable is not None or config.launcher_executable is not None


@pytest.fixture(scope="session")
def cspy_config(pytestconfig: Any) -> Config:
    config = config_from_options(pytestconfig)
    if not backend_configured(config):
        pytest.skip("No C-SPY backend configured (--cspy-iar-path, --cspy-cspyserver2 or --cspy-registry)")
    return config


@pytest.fixture(scope="session")
def cspy_client(cspy_config: Config) -> Iterator[Client]:
    client = Client(cspy_config)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def cspy_launch(pytestconfig: Any) -> LaunchConfig:
    path = pytestconfig.getoption("--cspy-launch")
    if not path:
        pytest.skip("No launch configuration given (--cspy-launch)")
    return LaunchConfig.from_file(path)


@pytest.fixture
def cspy_session(cspy_client: Client, cspy_launch: LaunchConfig) -> Iterator[Debugger]:
    cspy_client.terminal.clear()
    cspy_client.events.recent(clear=True)
    with cspy_client.debugger.session(cspy_launch) as debugger:
        yield debugger
