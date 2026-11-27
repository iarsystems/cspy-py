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
the :class:`Client`: ``debugger``, ``breakpoints``, ``crun_rules``,
``crun_messages``, ``context``, ``memory``, ``disassembly``, ``source``,
``terminal``, ``events``, ``frontend``, ``listwindows``, ``project``, ``options``
and ``ide_services``.
``client.raw("debugger")`` reaches any RPC the typed layer does not wrap.
"""

from ._thrift import to_plain
from .backend import Backend
from .breakpoints import Breakpoints, SourceLocation
from .client import Client, connect, launch
from .config import Config
from .context import ContextManager
from .crun import CRunMessages, Crun
from .debugger import Debugger
from .discovery import RemoteControlInfo, discover
from .errors import (
    BackendError,
    BackendUnavailableError,
    BusyError,
    CSpyError,
    CSpyExceptionInfo,
    NotConnectedError,
    ProjectManagerErrorInfo,
    SessionError,
    UnsupportedMethod,
    WaitTimeout,
    classify,
)
from .events import Events
from .frontend import Frontend
from .launch import LaunchConfig
from .listwindow import ListWindow, ListWindows, TraceWindow
from .logs import Logs
from .memory import Memory
from .options import Options, OptionsSession, ProjectNotLoaded
from .project import Project
from .registry_uri import format_registry_uri, parse_registry_uri
from .source import Disassembly, SourceLookup
from .terminal import Terminal
from .types import (
    AccessType,
    BuildResult,
    CleanupResult,
    CodeBreakpointMode,
    Context,
    CoreState,
    CRunBreakAction,
    CRunEvent,
    CRunMessage,
    FrontendPrompt,
    ListWindowChunk,
    ListWindowOverview,
    ListWindowRows,
    LogCategorySummary,
    LogEntry,
    LogPage,
    LogSeverity,
    LogSummary,
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
    "BackendUnavailableError",
    "Breakpoints",
    "BuildResult",
    "BusyError",
    "CSpyError",
    "CSpyExceptionInfo",
    "CleanupResult",
    "Client",
    "CodeBreakpointMode",
    "Config",
    "Context",
    "ContextManager",
    "CoreState",
    "Crun",
    "CRunBreakAction",
    "CRunEvent",
    "CRunMessage",
    "CRunMessages",
    "Debugger",
    "Disassembly",
    "Events",
    "Frontend",
    "FrontendPrompt",
    "LaunchConfig",
    "ListWindow",
    "ListWindowChunk",
    "ListWindowOverview",
    "ListWindowRows",
    "ListWindows",
    "LogCategorySummary",
    "LogEntry",
    "LogPage",
    "LogSeverity",
    "LogSummary",
    "Logs",
    "Memory",
    "NotConnectedError",
    "Options",
    "OptionsSession",
    "Project",
    "ProjectDebugResult",
    "ProjectInfo",
    "ProjectManagerErrorInfo",
    "ProjectNotLoaded",
    "ProjectStatus",
    "Register",
    "RegisterSnapshot",
    "RemoteControlInfo",
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
    "UnsupportedMethod",
    "VisibleSymbols",
    "WaitResult",
    "WaitTimeout",
    "classify",
    "connect",
    "discover",
    "format_registry_uri",
    "launch",
    "parse_registry_uri",
    "to_plain",
]
