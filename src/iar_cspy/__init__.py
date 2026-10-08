"""Python utility package for the IAR C-SPY debugger and IDE services.

Experimental: IAR makes no guarantees about the stability of this package. It may
change incompatibly, or be removed, in any release and without notice.

Drive a debug session from a script or test::

    import iar_cspy as cspy

    with cspy.launch(iar_path="/opt/iar/ewarm") as client:
        with client.debugger.session("launch.json") as dbg:   # runs to stopOnSymbol
            client.breakpoints.add("my_function")
            dbg.go_and_wait(timeout=10)                        # until it stops again
            print(dbg.eval_int("counter"))
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
from .breakpoints import Breakpoints, SourceLocation
from .client import Client, connect, launch
from .config import Config
from .context import ContextManager
from .debugger import Debugger
from .errors import BackendError, CSpyError, CSpyExceptionInfo, SessionError, WaitTimeout, classify
from .events import Events
from .launch import LaunchConfig
from .listwindow import ListWindow, ListWindows, TraceWindow
from .memory import Memory
from .options import Options, OptionsSession, ProjectNotLoaded
from .project import Project
from .source import Disassembly, SourceLookup
from .terminal import Terminal
from .types import (
    AccessType,
    BuildResult,
    CleanupResult,
    CodeBreakpointMode,
    Context,
    CoreState,
    ListWindowChunk,
    ListWindowOverview,
    ListWindowRows,
    ProjectDebugResult,
    ProjectInfo,
    ProjectStatus,
    Register,
    RegisterSnapshot,
    StartResult,
    StopResult,
    SymbolLookup,
    TerminalOutput,
    TraceStatus,
    VisibleSymbols,
    WaitResult,
)

__version__ = "0.1.0"

__all__ = [
    "AccessType",
    "Backend",
    "BackendError",
    "Breakpoints",
    "BuildResult",
    "CSpyError",
    "CSpyExceptionInfo",
    "CleanupResult",
    "Client",
    "CodeBreakpointMode",
    "Config",
    "Context",
    "ContextManager",
    "CoreState",
    "Debugger",
    "Disassembly",
    "Events",
    "LaunchConfig",
    "ListWindow",
    "ListWindowChunk",
    "ListWindowOverview",
    "ListWindowRows",
    "ListWindows",
    "Memory",
    "Options",
    "OptionsSession",
    "Project",
    "ProjectDebugResult",
    "ProjectInfo",
    "ProjectNotLoaded",
    "ProjectStatus",
    "Register",
    "RegisterSnapshot",
    "SessionError",
    "SourceLocation",
    "SourceLookup",
    "StartResult",
    "StopResult",
    "SymbolLookup",
    "Terminal",
    "TerminalOutput",
    "TraceStatus",
    "TraceWindow",
    "VisibleSymbols",
    "WaitResult",
    "WaitTimeout",
    "classify",
    "connect",
    "launch",
    "to_plain",
]
