"""The API against a real backend (skipped without one; see the --cspy-* options).

Runs the bundled Cortex-M3 simulator firmware by default.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from iar_cspy import CoreState

pytestmark = pytest.mark.live

FIRMWARE = Path(__file__).resolve().parents[1] / "examples" / "firmware"


def test_session_breakpoints_and_expressions(cspy_client, cspy_session):
    assert cspy_session.version()
    assert cspy_session.core_state() is CoreState.STOPPED

    bp = cspy_client.breakpoints.add("commit_history")
    assert bp.valid and bp.id > 0
    assert any(b.id == bp.id for b in cspy_client.breakpoints.all())

    cspy_session.go_and_wait(timeout=10)
    assert int(cspy_session.eval("g_phase").value, 0) >= 0

    cspy_client.breakpoints.remove(bp.id)
    assert all(b.id != bp.id for b in cspy_client.breakpoints.all())


def test_memory_registers_and_terminal(cspy_client, cspy_session):
    regs = cspy_session.registers(limit=4)
    assert regs.registers and regs.registers[0].value is not None

    guard = cspy_session.eval("g_guard", format="Hex")
    assert guard.hasLocation
    in_memory = cspy_client.memory.read_u32(guard.location.address, zone=guard.location.zone.id)
    assert in_memory == int(guard.value.replace("'", ""), 16) == 0xCAFEBABE  # C-SPY groups hex digits

    # Run the program to completion; its printf output arrives via libsupport.
    cspy_client.breakpoints.clear()
    cspy_session.go()
    assert cspy_client.terminal.wait_for_text("DBG_DONE", timeout=30)
    cspy_session.halt()
    assert cspy_client.terminal.text.startswith("DBG_BOOT")
    assert any(kind == "debug" for kind, _ in cspy_client.events.recent())


def test_registry_lists_the_debugger_services(cspy_client, cspy_session):
    names = {entry["name"] for entry in cspy_client.registry.services()}
    assert {"debugger", "breakpoints", "debugger.memory", "libsupport", "debugger.eventhandler"} <= names


@pytest.fixture()
def project_copy(tmp_path, cspy_config):
    """The bundled project, copied so that building it does not touch the repository."""
    if cspy_config.mode == "managed" and cspy_config.launcher_executable is None:
        pytest.skip("Project tests need IDE services: an IAR installation with IarServiceLauncher")
    work = tmp_path / "firmware"
    shutil.copytree(FIRMWARE, work, ignore=shutil.ignore_patterns("Obj", "*.dnx.lock"))
    return work / "test.ewp"


def test_project_build_debug_and_options(cspy_client, project_copy):
    status = cspy_client.project.load(str(project_copy))
    assert status["current_project"]["filename"] == str(project_copy)

    build = cspy_client.project.build()
    assert build.succeeded, "\n".join(build.tail(30))

    result = cspy_client.project.start_debugging(build=False)
    try:
        assert result.started
        assert cspy_client.debugger.core_state() is CoreState.STOPPED
    finally:
        cspy_client.debugger.stop_session()

    with cspy_client.options.open() as options:
        assert "<pages>" in (options.category_tree().xml or "")
