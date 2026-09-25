"""Value types used across the API.

Backend data (breakpoints, stack frames, modules, expression values, ...) is
returned as the thriftpy2 struct objects the IDL defines, with attribute
access (``bp.id``, ``frame.functionName``); :func:`iar_cspy.to_plain` turns any
of them into plain dicts. The types here are the API's own: enums with
readable names, context references, and the results of multi-step operations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any


class CoreState(IntEnum):
    """Run state of a core (``DkCoreStatusConstants``)."""

    STOPPED = 0
    RUNNING = 1
    SLEEPING = 2
    UNKNOWN = 3
    NO_POWER = 4


class AccessType(IntEnum):
    """What a breakpoint triggers on (``shared.AccessType``).

    ``FETCH`` is a code breakpoint; the others are data watchpoints.
    """

    FETCH = 1
    READ = 2
    WRITE = 3
    READ_WRITE = 4


@dataclass(frozen=True)
class Context:
    """A reference to a debugger context (``shared.ContextRef``).

    Most inspection calls take one; the default is the current base context,
    i.e. where the target is stopped.
    """

    type: str = "CurrentBase"
    level: int = 0
    core: int = 0
    task: int = 0

    @classmethod
    def current(cls) -> Context:
        return cls("CurrentBase")

    @classmethod
    def inspection(cls) -> Context:
        """The context the user has selected, e.g. a stack frame in a GUI."""
        return cls("CurrentInspection")

    @classmethod
    def frame(cls, level: int) -> Context:
        """Stack frame ``level`` (0 is the innermost)."""
        return cls("Stack", level=level)

    @classmethod
    def for_core(cls, core: int) -> Context:
        return cls("Target", core=core)

    @classmethod
    def for_task(cls, task: int) -> Context:
        return cls("Task", task=task)


class _Result:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


@dataclass
class WaitResult(_Result):
    """Outcome of waiting for a core to reach a state."""

    ok: bool
    timed_out: bool
    core: int
    desired_state: int
    state: int | None
    attempts: int
    elapsed_ms: int


@dataclass
class StartResult(_Result):
    """Outcome of :meth:`Debugger.configure_and_start`.

    ``handoff`` describes how a previous session was torn down first.
    ``ran_to_symbol`` tells whether the launch configuration's
    ``stopOnSymbol`` was reached; a failure there does not fail the start, it
    is reported in ``stop_on_symbol_error``.
    """

    handoff: dict[str, Any]
    stop_on_symbol: str | None
    ran_to_symbol: bool
    stop_on_symbol_error: str | None = None


@dataclass
class StopResult(_Result):
    """Outcome of :meth:`Debugger.stop_session`."""

    already_stopped: bool = False
    #: The managed CSpyServer2 was shut down; the next session gets a fresh one.
    backend_restarted: bool = False
    #: stopSession failed with a benign "already stopped" error, verified by probing.
    stop_idempotent_recovered: bool = False
    stop_idempotent_details: dict[str, Any] | None = None


@dataclass
class CleanupResult(_Result):
    """Outcome of :meth:`Debugger.cleanup`; ``errors`` lists failed steps."""

    ok: bool
    errors: list[dict[str, Any]]
    before: dict[str, bool]
    after: dict[str, bool]


@dataclass
class Register(_Result):
    name: str
    alias: str
    description: str
    bit_size: int
    location: Any
    readonly: bool
    writeonly: bool
    raw: bytes | None = None
    read_error: str | None = None

    @property
    def value(self) -> int | None:
        """The value as an unsigned little-endian integer, if it could be read."""
        return None if self.raw is None else int.from_bytes(self.raw, "little", signed=False)


@dataclass
class RegisterSnapshot(_Result):
    group: str
    requested_group: str
    available_groups: list[str]
    total_in_group: int
    registers: list[Register] = field(default_factory=list)

    def __getitem__(self, name: str) -> Register:
        for reg in self.registers:
            if name in (reg.name, reg.alias):
                return reg
        raise KeyError(name)


@dataclass
class TerminalOutput(_Result):
    """Program output captured through libsupport (the target's stdout)."""

    text: str
    data: bytes
    exit_code: int | None
    asserts: list[dict[str, str]]


@dataclass
class BuildResult(_Result):
    project: dict[str, Any]
    configuration: str
    succeeded: bool
    output: list[str]

    def tail(self, lines: int) -> list[str]:
        return self.output[-max(1, int(lines)):]


@dataclass
class ProjectDebugResult(_Result):
    """Outcome of :meth:`Project.start_debugging`."""

    project: dict[str, Any]
    configuration: str
    build: BuildResult | None
    launch_configuration: Any = None
    configured: bool = False
    started: bool = False
