"""Typed breakpoints: the category and descriptor follow from the kind and the driver."""

from __future__ import annotations

import os

import pytest

from iar_cspy import AccessType, CodeBreakpointMode, CSpyError, SessionError, SourceLocation
from iar_cspy._drivers import find_driver, log_message_args, write_descriptor
from iar_cspy.testing import fake_client


def _configured(driver: str = "Simulator", target: str = "arm", options: list[str] | None = None, lib: str = ""):
    client, rpc = fake_client()
    rpc.on("debugger", "resolveLaunchConfiguration", rpc.struct(
        "debugger", "resolveLaunchConfiguration",
        driverNameOrEmpty=driver, targetOrEmpty=target, driverOptions=options or [], driverLib=lib,
    ))
    rpc.on("debugger", "configureSession").on("debugger", "startSMPSession")
    client.debugger.configure({"driver": driver, "target": target})
    client.debugger.start()
    rpc.on("breakpoints", "setBreakpointFromDescriptor", {"id": 5, "valid": True})
    rpc.on("breakpoints", "getBreakpoint", lambda id: {"id": id, "valid": True})
    return client, rpc


def _descriptors(rpc) -> list[str]:
    return [args[0] for args in rpc.args("breakpoints", "setBreakpointFromDescriptor")]


def test_simulator_code_breakpoints_are_std_code2():
    client, rpc = _configured()
    assert client.breakpoints.modes() == [
        CodeBreakpointMode.AUTO, CodeBreakpointMode.TRACE_START, CodeBreakpointMode.TRACE_STOP
    ]
    assert client.breakpoints.add_code("main")["id"] == 5
    client.breakpoints.add_code(0x1CC2, "trace_start")
    assert _descriptors(rpc) == [
        '_ 1 "STD_CODE2" "main" 0 0 1 "" 0 ""',
        '_ 1 "STD_TRACE_START2" "0x1cc2"',
    ]


def test_source_breakpoints_use_the_full_source_ule():
    client, rpc = _configured()
    client.breakpoints.add_source(r"C:\src\main.c", 82)
    client.breakpoints.add_source("/abs/main.c", 82, 9)
    client.breakpoints.add_source("rel/main.c", 3)
    assert [d.split(" ")[3] for d in _descriptors(rpc)] == [
        r'"{C:\src\main.c}.82.1"',
        '"{/abs/main.c}.82.9"',
        f'"{{{os.path.abspath("rel/main.c")}}}.3.1"',
    ]
    assert SourceLocation("/abs/a.c", 1).ule == "{/abs/a.c}.1.1"


def test_hardware_probes_use_emul_code_with_a_type():
    client, rpc = _configured("I-jet")
    client.breakpoints.add_code("main")
    client.breakpoints.add_code("main", CodeBreakpointMode.HARDWARE)
    client.breakpoints.add_code("main", CodeBreakpointMode.SOFTWARE)
    client.breakpoints.add_code("main", CodeBreakpointMode.TRACE_START)
    assert _descriptors(rpc) == [
        '_ 1 "EMUL_CODE" "main" 0 0 1 "" 0 "" 0',
        '_ 1 "EMUL_CODE" "main" 0 0 1 "" 0 "" 1',
        '_ 1 "EMUL_CODE" "main" 0 0 1 "" 0 "" 2',
        '_ 1 "TDL_EMUL_TRACE_START" "main" 3 0 0 0 0',
    ]


def test_an_unsupported_mode_names_the_supported_ones():
    client, rpc = _configured()
    with pytest.raises(CSpyError, match="Simulator driver does not support hardware breakpoints; it supports: auto"):
        client.breakpoints.add_code("main", "hardware")
    with pytest.raises(CSpyError, match="Unknown breakpoint mode 'fast'"):
        client.breakpoints.add_code("main", "fast")
    assert _descriptors(rpc) == []


def test_drivers_without_auto_default_to_their_first_mode():
    client, rpc = _configured("Renesas E1", target="rl78", options=["--drv_interface=e1"])
    assert client.breakpoints.modes()[0] is CodeBreakpointMode.SOFTWARE
    client.breakpoints.add_code("main")
    assert _descriptors(rpc) == ['_ 1 "STD_CODE2" "main" 0 0 1 "" 0 ""']
    with pytest.raises(CSpyError, match="does not support data breakpoints"):
        client.breakpoints.add_data("g_state")


def test_data_breakpoints_per_driver():
    client, rpc = _configured()
    client.breakpoints.add_data("g_state", AccessType.WRITE)
    client.breakpoints.add_data(0x20000000, size=4)
    assert _descriptors(rpc) == [
        '_ 1 "STD_DATA2" "g_state" 0 0 1 "" 0 "" 2',
        '_ 1 "STD_DATA2" "0x20000000@4" 0 0 1 "" 0 "" 0',
    ]
    with pytest.raises(CSpyError, match="use add_code"):
        client.breakpoints.add_data("g_state", AccessType.FETCH)

    client, rpc = _configured("ST-LINK")
    client.breakpoints.add_data("g_state", AccessType.READ)
    assert _descriptors(rpc) == ['_ 1 "EMUL_DATA" "g_state" 1 0 0 0 0']


def test_log_breakpoints_turn_braces_into_expressions():
    client, rpc = _configured()
    client.breakpoints.add_log("phase_step", 'n = {n} "q"')
    assert _descriptors(rpc) == ['_ 1 "STD_LOG2" "phase_step" "" 1 "%"n = %",n,%" \\%"q\\%"%"" 1 0 0']
    assert log_message_args("a {x} {{y}} b\\") == ['"a "', "x", '" "', "{y}", '" b\\\\"']


def test_the_driver_is_found_from_its_library_when_the_name_is_unknown():
    assert find_driver("", "arm", [], "/opt/iar/arm/bin/libarmsim2.so").name == "Simulator"
    assert find_driver("", "arm", [], r"C:\iar\arm\bin\armJLINK.dll").name == "J-Link/J-Trace"
    assert find_driver("J-Link/J-Trace", "rx").data.__name__ == "EMUL_DATA_BREAK"
    assert find_driver("CMSIS DAP", "arm", ["--jet_probe=cmsisdap"]).name == "CMSIS DAP"
    assert find_driver("Nonexistent", "arm") is None

    # An unknown driver is taken for a hardware probe, as the IDE does.
    client, rpc = _configured("Some New Probe")
    client.breakpoints.add_code("main", CodeBreakpointMode.HARDWARE)
    assert _descriptors(rpc) == ['_ 1 "EMUL_CODE" "main" 0 0 1 "" 0 "" 1']


def test_an_attached_session_falls_back_to_the_default_categories():
    client, rpc = fake_client(started=True)
    rpc.on("breakpoints", "setBreakpointOnUle", {"id": 3, "valid": True})
    rpc.on("breakpoints", "getBreakpoint", lambda id: {"id": id, "valid": True})
    assert client.breakpoints.modes() == []
    client.breakpoints.add_code("main")
    client.breakpoints.add_data("g_state", AccessType.WRITE)
    assert rpc.args("breakpoints", "setBreakpointOnUle") == [("main", 1), ("g_state", 3)]
    with pytest.raises(CSpyError, match="use_driver"):
        client.breakpoints.add_code("main", CodeBreakpointMode.HARDWARE)

    client.breakpoints.use_driver("I-jet")
    rpc.on("breakpoints", "setBreakpointFromDescriptor", {"id": 4, "valid": True})
    client.breakpoints.add_code("main", CodeBreakpointMode.HARDWARE)
    assert _descriptors(rpc) == ['_ 1 "EMUL_CODE" "main" 0 0 1 "" 0 "" 1']


def test_the_driver_is_forgotten_with_the_session():
    client, rpc = _configured("I-jet")
    rpc.on("debugger", "exit").on("debugger", "stopSession")
    client.debugger._reset_state()
    assert client.debugger.launch_configuration is None
    assert client.breakpoints.modes() == []
    with pytest.raises(SessionError):
        client.breakpoints.add_source("/a.c", 1)


def test_a_rejected_location_names_the_breakpoint_kind():
    client, rpc = _configured()
    rpc.on("breakpoints", "setBreakpointFromDescriptor", {"id": 1, "valid": False})
    with pytest.raises(CSpyError, match=r"Setting a code breakpoint \(auto, Simulator driver\) on 'nope' failed"):
        client.breakpoints.add_code("nope")


def test_descriptor_strings_are_escaped():
    assert write_descriptor([True, "a%b\n\"c\"%", 7]) == '_ 1 "a%%b%n%"c%"%% " 7'
