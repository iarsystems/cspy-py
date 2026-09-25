"""Python API for the IAR C-SPY debugger and IDE services.

Drive a debug session from a script or test::

    import iar_cspy as cspy

    with cspy.launch(iar_path="/opt/iar/ewarm") as client:
        with client.debugger.session("launch.json") as dbg:   # runs to stopOnSymbol
            client.breakpoints.add("my_function")
            dbg.go_and_wait(timeout=10)                        # until it stops again
            print(dbg.eval("counter").value)
            print(hex(dbg.registers()["PC"].value))
            print(client.memory.read(0x20000000, 16).hex())

:func:`launch` starts and supervises the backend from an IAR installation;
:func:`connect` attaches to one that is already running. Everything hangs off
the :class:`Client`: ``debugger``, ``breakpoints``, ``context``, ``memory``,
``disassembly``, ``source``, ``terminal``, ``events``, ``listwindows``,
``project``, ``options`` and ``ide_services``. ``client.raw("debugger")``
reaches any RPC the typed layer does not wrap.
"""

from ._thrift import to_plain
from .backend import Backend
from .client import Client, connect, launch
from .config import Config
from .errors import BackendError, CSpyError, SessionError, WaitTimeout
from .launch import LaunchConfig
from .options import ProjectNotLoaded
from .types import (
    AccessType,
    BuildResult,
    CleanupResult,
    Context,
    CoreState,
    ProjectDebugResult,
    Register,
    RegisterSnapshot,
    StartResult,
    StopResult,
    TerminalOutput,
    WaitResult,
)

__version__ = "0.1.0"

__all__ = [
    "AccessType",
    "Backend",
    "BackendError",
    "BuildResult",
    "CSpyError",
    "CleanupResult",
    "Client",
    "Config",
    "Context",
    "CoreState",
    "LaunchConfig",
    "ProjectDebugResult",
    "ProjectNotLoaded",
    "Register",
    "RegisterSnapshot",
    "SessionError",
    "StartResult",
    "StopResult",
    "TerminalOutput",
    "WaitResult",
    "WaitTimeout",
    "connect",
    "launch",
    "to_plain",
]
