"""Which breakpoint categories a driver supports, and the descriptors that set them.

Ported from the IAR VS Code debug extension (``src/dap/breakpoints``). A
breakpoint is set from a descriptor (``setBreakpointFromDescriptor``) naming
its category: the category-only RPC drops categories outside the backend's
translation map, so it cannot set e.g. ``EMUL_CODE`` on emulator drivers.

A descriptor is ``_`` followed by space-separated fields: integers, booleans
as 0/1 and quoted strings. The fields after the category and ULE depend on the
category; the defaults here are those of the extension.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Sequence, Union

from .types import AccessType, CodeBreakpointMode

Field = Union[bool, int, str]

# -- descriptors ------------------------------------------------------------

_ESCAPES = {"\a": "%a", "\b": "%b", "\f": "%f", "\n": "%n", "\r": "%r", "\t": "%t", "\v": "%v", '"': '%"'}


def _string(text: str) -> str:
    text = text.replace("%", "%%")
    for char, escape in _ESCAPES.items():
        text = text.replace(char, escape)
    if text.endswith("%"):
        text += " "  # so that the closing quote is not read as escaped
    return f'"{text}"'


def write_descriptor(fields: Sequence[Field]) -> str:
    parts = ["_"]
    for value in fields:
        if isinstance(value, str):
            parts.append(_string(value))
        else:
            parts.append(str(int(value)))
    return " ".join(parts)


def _loc_only(category: str, ule: str) -> list[Field]:
    return [True, category, ule]


def _loc_etc(category: str, ule: str) -> list[Field]:
    # isThreadSpecific, threadId, stopWhenTrue, condition, skipCount, action
    return [*_loc_only(category, ule), False, 0, True, "", 0, ""]


def _emul_data(category: str, ule: str, access: int) -> list[Field]:
    # access, useExtendedRange, useMatchData, dataValue, dataMask
    return [*_loc_only(category, ule), access, False, False, 0, 0]


def _emul_trace(category: str, ule: str) -> list[Field]:
    # access (3: fetch), useExtendedRange, useMatchData, dataValue, dataMask,
    # rangeSize (unused, but the IDE writes this), linkWithNegate, linkWithAND
    return [*_loc_only(category, ule), 3, False, False, 0, 0xFFFFFFFF, 0xCCCCCCCC, False, False]


def _tdl_trace(category: str, ule: str) -> list[Field]:
    return _emul_data(category, ule, 3)  # 3: fetch


def _emu_timer(category: str, ule: str) -> list[Field]:
    # timerNo (one of four; always the first), typeStart
    return [*_loc_only(category, ule), 1, False]


#: ``shared.AccessType`` to the access values descriptors use.
_DESCRIPTOR_ACCESS = {AccessType.READ_WRITE: 0, AccessType.READ: 1, AccessType.WRITE: 2}
#: The same for EMUL_DATA_BREAK (RX), as in TdEmu.h.
_DATA_BREAK_ACCESS = {AccessType.READ: 0, AccessType.WRITE: 1, AccessType.READ_WRITE: 2}

CodeFactory = Callable[[str], list[Field]]
DataFactory = Callable[[str, AccessType], list[Field]]


def _emul_code(kind: int) -> CodeFactory:
    # kind: 0 auto, 1 hardware, 2 software (as the drivers number them)
    return lambda ule: [*_loc_etc("EMUL_CODE", ule), kind]


def _code(shape: Callable[[str, str], list[Field]], category: str) -> CodeFactory:
    return lambda ule: shape(category, ule)


STD_CODE2 = _code(_loc_etc, "STD_CODE2")
STD_TRACE_START2 = _code(_loc_only, "STD_TRACE_START2")
STD_TRACE_STOP2 = _code(_loc_only, "STD_TRACE_STOP2")
HW_CODE = _code(_loc_only, "HW_CODE")
EMU_HW_CODE = _code(_loc_etc, "EMU_HW_CODE")
EMU_SW_CODE = _code(_loc_etc, "EMU_SW_CODE")
EMUL_FLASH = _code(_loc_etc, "EMUL_FLASH")
EMUL_TRACE_START = _code(_emul_trace, "EMUL_TRACE_START")
EMUL_TRACE_STOP = _code(_emul_trace, "EMUL_TRACE_STOP")
EMUL_TRACE_FILTER = _code(_emul_trace, "EMUL_TRACE_FILTER")
TDL_EMUL_TRACE_START = _code(_tdl_trace, "TDL_EMUL_TRACE_START")
TDL_EMUL_TRACE_STOP = _code(_tdl_trace, "TDL_EMUL_TRACE_STOP")
EMU_TIMER_START = _code(_emu_timer, "EMU_TIMER_START")
EMU_TIMER_STOP = _code(_emu_timer, "EMU_TIMER_STOP")


def STD_DATA2(ule: str, access: AccessType) -> list[Field]:
    return [*_loc_etc("STD_DATA2", ule), _DESCRIPTOR_ACCESS[access]]


def EMUL_DATA(ule: str, access: AccessType) -> list[Field]:
    return _emul_data("EMUL_DATA", ule, _DESCRIPTOR_ACCESS[access])


def EMUL_DATA_BREAK(ule: str, access: AccessType) -> list[Field]:
    # adrCondition, adrMaskStr, adrMaskCompare, adrRange, adrEndLocStr, datAccess,
    # datAccessSize, datDataStr, datMaskCheck, datMaskStr, datMaskCompare
    return [*_loc_only("EMUL_DATA_BREAK", ule), 0, "0x0", 0, 0, "", _DATA_BREAK_ACCESS[access], 0, "", 0, "", 0]


def STD_LOG2(ule: str, message: str) -> list[Field]:
    # condition, triggerWhenTrue, message, msgIsArgList, threadSpecific, threadId
    return [*_loc_only("STD_LOG2", ule), "", True, ",".join(log_message_args(message)), True, False, 0]


def log_message_args(message: str) -> list[str]:
    """``"x = {x}"`` as C-SPY ``__message`` arguments: ``['"x = "', 'x']``.

    Text outside braces becomes string literals, text inside them is kept as
    expressions (as the VS Code extension reads log messages).
    """
    args: list[str] = []
    depth = 0
    part = ""
    for char in message:
        if char == "{":
            if depth == 0:
                if part:
                    args.append(_literal(part))
                part = ""
            else:
                part += char
            depth += 1
        elif char == "}":
            if depth == 1:
                if part:
                    args.append(part)
                part = ""
            else:
                part += char
            depth = max(depth - 1, 0)
        else:
            part += char
    if part:
        args.append(_literal(part))
    return args


def _literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# -- drivers ----------------------------------------------------------------

M = CodeBreakpointMode


@dataclass(frozen=True)
class Driver:
    """The breakpoint categories one debugger driver supports.

    ``code`` maps each supported mode to its factory; the first mode is the
    driver's default. ``data`` is None for drivers without data breakpoints.
    """

    name: str
    code: dict[CodeBreakpointMode, CodeFactory]
    data: DataFactory | None = EMUL_DATA
    log: Callable[[str, str], list[Field]] = STD_LOG2
    simulator: bool = False
    #: Base names of the driver library (``libarmsim2.so`` is ``sim2``).
    libraries: tuple[str, ...] = field(default=())

    @property
    def default_mode(self) -> CodeBreakpointMode:
        return next(iter(self.code))


_GENERIC_CODE = {M.AUTO: _emul_code(0), M.HARDWARE: _emul_code(1), M.SOFTWARE: _emul_code(2)}


def _simulator(libraries: tuple[str, ...]) -> Driver:
    code = {M.AUTO: STD_CODE2, M.TRACE_START: STD_TRACE_START2, M.TRACE_STOP: STD_TRACE_STOP2}
    return Driver("Simulator", code, STD_DATA2, simulator=True, libraries=libraries)


def _generic(name: str, libraries: tuple[str, ...]) -> Driver:
    return Driver(name, dict(_GENERIC_CODE), libraries=libraries)


def _ijet(name: str) -> Driver:
    code = {**_GENERIC_CODE, M.FLASH: EMUL_FLASH, M.TRACE_START: TDL_EMUL_TRACE_START, M.TRACE_STOP: TDL_EMUL_TRACE_STOP}
    return Driver(name, code, libraries=("ijet", "jet"))


def _rh850(name: str) -> Driver:
    code = {
        M.SOFTWARE: _emul_code(0),
        M.HARDWARE: _emul_code(1),
        M.TRACE_START: STD_TRACE_START2,
        M.TRACE_STOP: STD_TRACE_STOP2,
        M.TIMER_START: EMU_TIMER_START,
        M.TIMER_STOP: EMU_TIMER_STOP,
    }
    return Driver(name, code, STD_DATA2, libraries=("ocd",))


def _rl78(name: str, libraries: tuple[str, ...] = ("ocd",)) -> Driver:
    return Driver(name, {M.SOFTWARE: STD_CODE2, M.HARDWARE: HW_CODE}, None, libraries=libraries)


def _rx(name: str, libraries: tuple[str, ...]) -> Driver:
    code = {
        M.AUTO: STD_CODE2,
        M.HARDWARE: EMU_HW_CODE,
        M.SOFTWARE: EMU_SW_CODE,
        M.TRACE_START: STD_TRACE_START2,
        M.TRACE_STOP: STD_TRACE_STOP2,
    }
    return Driver(name, code, EMUL_DATA_BREAK, libraries=libraries)


def _avr(name: str, libraries: tuple[str, ...]) -> Driver:
    return Driver(name, {M.AUTO: STD_CODE2}, STD_DATA2, libraries=libraries)


@dataclass(frozen=True)
class _Entry:
    names: tuple[str, ...]
    driver: Driver
    targets: tuple[str, ...] | None = None
    #: A driver option (or part of one) the entry requires.
    option: str | None = None


def _entry(name: str | tuple[str, ...], driver: Driver, targets: tuple[str, ...] | None = None, option: str | None = None) -> _Entry:
    return _Entry(name if isinstance(name, tuple) else (name,), driver, targets, option)


# The driver names are those of launch.json's "driver". Some are shared by
# drivers that differ per target or driver option; the first match wins.
_DRIVERS: list[_Entry] = [
    _entry("Simulator", _simulator(("sim", "sim2"))),
    _entry("64-bit Simulator", Driver("64-bit Simulator", {M.AUTO: STD_CODE2}, STD_DATA2, simulator=True, libraries=("imperas",))),
    _entry("CMSIS DAP", _ijet("CMSIS DAP"), option="--jet_probe=cmsisdap"),
    _entry("I-jet", _ijet("I-jet")),
    _entry(
        "J-Link/J-Trace",
        Driver(
            "J-Link/J-Trace",
            {
                **_GENERIC_CODE,
                M.FLASH: EMUL_FLASH,
                M.TRACE_START: EMUL_TRACE_START,
                M.TRACE_STOP: EMUL_TRACE_STOP,
                M.TRACE_FILTER: EMUL_TRACE_FILTER,
            },
            libraries=("jlink", "jlink2"),
        ),
        targets=("arm",),
    ),
    _entry("GDB Server", _generic("GDB Server", ("gdbserv",))),
    _entry("CADI", Driver("CADI", dict(_GENERIC_CODE), None, libraries=("cadi",))),
    _entry("TI Stellaris", _generic("TI Stellaris", ("lmiftdi",))),
    _entry("PE micro", _generic("PE micro", ("pemicro",))),
    _entry("ST-LINK", _generic("ST-LINK", ("stlink", "stlink2"))),
    _entry("TI XDS", _generic("TI XDS", ("xds", "xds2"))),
    _entry("TI MSP-FET", _generic("TI MSP-FET", ("tifet",))),
    _entry(("E2/E2 Lite", "Renesas E2 / E2 Lite for Arm"), _generic("E2/E2 Lite", ("e2",)), targets=("arm",)),
    _entry("Renesas E1", _rh850("Renesas E1"), targets=("rh850",), option="e1"),
    _entry("Renesas E2", _rh850("Renesas E2"), targets=("rh850",), option="e2"),
    _entry("Renesas E20", _rh850("Renesas E20"), targets=("rh850",), option="e20"),
    _entry("Renesas E1", _rl78("Renesas E1"), targets=("rl78",), option="e1"),
    _entry("Renesas E2", _rl78("Renesas E2"), targets=("rl78",), option="e2"),
    _entry("Renesas E20", _rl78("Renesas E20"), targets=("rl78",), option="e20"),
    _entry("E1 / E20", _rx("E1 / E20", ("e1e20",)), targets=("rx",)),
    _entry("Renesas E2", _rx("Renesas E2", ("e2e2l",)), targets=("rx",), option="e2"),
    _entry("E2 Lite / EZ-CUBE2", _rx("E2 Lite / EZ-CUBE2", ("e2e2l",)), targets=("rx",), option="e2lite"),
    _entry("J-Link/J-Trace", _rx("J-Link/J-Trace", ("jlink", "jlink2")), targets=("rx",)),
    _entry("Renesas E2 Lite / E2 On-board", _rl78("Renesas E2 Lite / E2 On-board"), option="e2lite"),
    _entry("EZ-CUBE", _rl78("EZ-CUBE"), option="ezcube"),
    _entry("EZ-CUBE2", _rl78("EZ-CUBE2"), option="ezcube2"),
    _entry("TK", _rl78("TK"), option="tk"),
    _entry("IECUBE", _rl78("IECUBE", ("iecube",))),
    _entry("Power Debugger", _avr("Power Debugger", ("atmelice",)), option="--drv_power_debugger"),
    _entry("Atmel-ICE", _avr("Atmel-ICE", ("atmelice",))),
    _entry("AVR ONE!", _avr("AVR ONE!", ("one",))),
    _entry("Dragon", _avr("Dragon", ("jtagice-mkii",)), option="--drv_dragon"),
    _entry("JTAGICE mkII", _avr("JTAGICE mkII", ("jtagice-mkii",))),
    _entry("JTAGICE3", _avr("JTAGICE3", ("jtagice3",))),
    # The target presents itself as msp430, but the drivers use 430 as prefix.
    _entry("FET Debugger", Driver("FET Debugger", {M.AUTO: STD_CODE2}, None, libraries=("fet",)), targets=("msp430", "430")),
]


def _matches(entry: _Entry, target: str, options: Sequence[str]) -> bool:
    # The extension tests the option with Array.includes, i.e. for a whole
    # option; "e1"-style entries match a value such as "--drv_interface=e1" too.
    if entry.targets is not None and target not in entry.targets:
        return False
    if entry.option is None:
        return True
    return any(o == entry.option or o.endswith("=" + entry.option) for o in options)


def _library_base(library: str, target: str) -> str:
    base = re.sub(r"\.(so|dll)$", "", re.split(r"[\\/]", library)[-1], flags=re.IGNORECASE).lower()
    base = base.removeprefix("lib")
    if target and base.startswith(target.lower()):
        base = base[len(target):]
    return base


def find_driver(name: str = "", target: str = "", options: Sequence[str] = (), library: str = "") -> Driver | None:
    """The driver a launch configuration names (or loads), or None if unknown.

    ``name`` is the launch.json driver name (``Simulator``, ``I-jet``, ...),
    ``library`` the driver library a resolved configuration loads; it is used
    when the name is empty or unknown.
    """
    for entry in _DRIVERS:
        if name in entry.names and _matches(entry, target, options):
            return entry.driver
    if library:
        base = _library_base(library, target)
        for entry in _DRIVERS:
            if any(base.endswith(lib) for lib in entry.driver.libraries) and _matches(entry, target, options):
                return entry.driver
    return None


def generic_driver(name: str) -> Driver:
    """What the extension assumes for a driver it does not know: a hardware probe."""
    return _generic(name, ())
