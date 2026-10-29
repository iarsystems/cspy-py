"""Session start on a backend without the launch-configuration API (IDE platform before 9.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from thriftpy2.thrift import TApplicationException

from iar_cspy import CodeBreakpointMode, Config, CSpyError
from iar_cspy._legacy import CONTEXT_WINDOWS
from iar_cspy.testing import fake_client


def no_such_method(method):
    return TApplicationException(TApplicationException.UNKNOWN_METHOD, f"Invalid method name: '{method}'")


@pytest.fixture()
def workbench(tmp_path):
    bin_dir = tmp_path / "ewarm" / "arm" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("armsim2.dll", "armproc.dll", "armLibsupportUniversal.dll", "armjlink.dll"):
        (bin_dir / name).write_bytes(b"")
    return tmp_path / "ewarm"


@pytest.fixture()
def launch(workbench):
    return {
        "type": "cspy",
        "request": "launch",
        "target": "arm",
        "program": "/w/Debug/Exe/a.out",
        "driver": "Simulator",
        "driverOptions": ["--cpu=Cortex-M3", "--semihosting"],
        "setupMacros": ["/w/setup.mac"],
        "download": {
            "flashLoader": "/w/flash.board",
            "deviceMacros": ["/w/device.dmac"],
            "extraImages": [{"image": "/w/boot.out", "offset": "0x100", "suppressDownload": True}],
        },
        "workbenchPath": str(workbench),
    }


def old_backend(rpc):
    rpc.on("debugger", "resolveLaunchConfiguration", no_such_method("resolveLaunchConfiguration"))
    for method in ("startSession", "loadMacroFile", "flashModule", "loadModuleWithOptions", "runToULE"):
        rpc.on("debugger", method, None)
    rpc.on("debugger", "getCoreState", 0)
    return rpc


def test_configure_falls_back_without_configure_session(fake, launch, workbench):
    client, rpc = fake
    old_backend(rpc)

    client.debugger.configure(launch)
    assert rpc.methods() == ["resolveLaunchConfiguration"]
    assert client.debugger.state == {"configured": True, "started": False}
    resolved = client.debugger.launch_configuration
    assert resolved.driverNameOrEmpty == "Simulator"
    assert resolved.driverLib == str(workbench / "arm" / "bin" / "armsim2.dll")
    assert resolved.driverOptions == ["--cpu=Cortex-M3", "--semihosting"]
    # The breakpoint categories come from the driver, as after a resolved configuration.
    assert client.breakpoints.modes()[0] == CodeBreakpointMode.AUTO


def test_start_runs_the_extension_legacy_sequence(fake, launch, workbench):
    client, rpc = fake
    old_backend(rpc)
    client.debugger.configure(launch)
    client.debugger.start()

    assert rpc.methods()[1:] == [
        "startSession",
        "loadMacroFile",
        "flashModule",
        "loadMacroFile",
        "loadModuleWithOptions",
    ]
    assert rpc.args("debugger", "loadMacroFile") == [("/w/device.dmac",), ("/w/setup.mac",)]
    assert rpc.args("debugger", "flashModule") == [("/w/flash.board", "/w/Debug/Exe/a.out", [], [])]
    (session,) = rpc.args("debugger", "startSession")[0]
    bin_dir = workbench / "arm" / "bin"
    assert session["driverName"] == str(bin_dir / "armsim2.dll")
    assert session["processorName"] == str(bin_dir / "armproc.dll")
    assert session["plugins"] == [str(bin_dir / "armLibsupportUniversal.dll")]
    assert session["toolkitDir"] == str(workbench / "arm")
    assert session["options"] == ["--cpu=Cortex-M3", "--semihosting"]
    assert session["attachToTarget"] is False
    program, options = rpc.args("debugger", "loadModuleWithOptions")[0]
    assert program == "/w/Debug/Exe/a.out"
    assert options["resetAfterLoad"] is True and options["suppressDownload"] is False
    assert options["extraDebugFiles"] == [{"doDownload": False, "path": "/w/boot.out", "offset": 0x100}]
    assert client.debugger.started


def test_attach_loads_without_downloading(fake, launch):
    client, rpc = fake
    old_backend(rpc)
    client.debugger.configure({**launch, "request": "attach"})
    client.debugger.start()

    assert "flashModule" not in rpc.methods()
    (session,) = rpc.args("debugger", "startSession")[0]
    assert session["attachToTarget"] is True
    _, options = rpc.args("debugger", "loadModuleWithOptions")[0]
    assert options["shouldAttach"] is True
    assert options["suppressDownload"] is True
    assert options["resetAfterLoad"] is False and options["callUserMacros"] is False


def test_suppressed_program_download_skips_the_flash_loader(fake, launch):
    client, rpc = fake
    old_backend(rpc)
    client.debugger.configure({**launch, "download": {**launch["download"], "suppressProgramDownload": True}})
    client.debugger.start()

    assert "flashModule" not in rpc.methods()
    _, options = rpc.args("debugger", "loadModuleWithOptions")[0]
    assert options["suppressDownload"] is True and options["resetAfterLoad"] is True


def test_configure_and_start_runs_to_stop_on_symbol(fake, launch):
    client, rpc = fake
    old_backend(rpc).on("debugger", "isOnline", False)
    result = client.debugger.configure_and_start({**launch, "stopOnSymbol": "main"})

    assert result.ran_to_symbol is True
    assert "configureSession" not in rpc.methods() and "startSMPSession" not in rpc.methods()
    assert rpc.methods().index("loadModuleWithOptions") < rpc.methods().index("runToULE")


def test_settings_go_next_to_the_program_when_the_project_is_missing(fake, launch, tmp_path):
    client, rpc = fake
    old_backend(rpc)
    client.debugger.configure({**launch, "projectPath": str(tmp_path / "missing" / "test.ewp")})
    client.debugger.start()

    (session,) = rpc.args("debugger", "startSession")[0]
    assert session["projectDir"] == str(Path("/w/Debug/Exe/a.out").parent)
    assert session["projectName"] == "test.ewp"


def test_settings_go_next_to_an_existing_project(fake, launch, tmp_path):
    client, rpc = fake
    old_backend(rpc)
    project = tmp_path / "proj" / "test.ewp"
    project.parent.mkdir()
    project.write_text("<project/>")
    client.debugger.configure({**launch, "projectPath": str(project)})
    client.debugger.start()

    (session,) = rpc.args("debugger", "startSession")[0]
    assert session["projectDir"] == str(project.parent)


def test_a_new_backend_does_not_use_the_legacy_start(fake, launch):
    client, rpc = fake
    rpc.on("debugger", "resolveLaunchConfiguration", {"resolved": True})
    rpc.on("debugger", "configureSession").on("debugger", "startSMPSession")
    client.debugger.configure(launch)
    client.debugger.start()
    assert rpc.methods() == ["resolveLaunchConfiguration", "configureSession", "startSMPSession"]


def test_other_resolve_failures_are_not_taken_for_an_old_backend(fake, launch):
    client, rpc = fake
    rpc.on("debugger", "resolveLaunchConfiguration", CSpyError("Invalid launch configuration"))
    with pytest.raises(CSpyError, match="Invalid launch configuration"):
        client.debugger.configure(launch)
    assert client.debugger.state == {"configured": False, "started": False}


def test_a_missing_driver_library_says_where_it_looked(fake, launch, workbench):
    client, rpc = fake
    old_backend(rpc)
    (workbench / "arm" / "bin" / "armsim2.dll").unlink()
    with pytest.raises(CSpyError, match=r"None of armsim\.dll, armsim2\.dll is in"):
        client.debugger.configure(launch)
    assert client.debugger.state == {"configured": False, "started": False}


def test_without_an_installation_the_error_says_how_to_give_one(fake, launch):
    client, rpc = fake
    old_backend(rpc)
    launch = {key: value for key, value in launch.items() if key != "workbenchPath"}
    with pytest.raises(CSpyError, match="workbenchPath"):
        client.debugger.configure(launch)


def test_a_failed_legacy_start_leaves_the_session_configured(fake, launch):
    client, rpc = fake
    old_backend(rpc).on("debugger", "loadModuleWithOptions", CSpyError("download failed"))
    client.debugger.configure(launch)
    with pytest.raises(CSpyError, match="Starting the debug session failed"):
        client.debugger.start()
    assert client.debugger.state == {"configured": True, "started": False}


def test_a_managed_backend_gets_the_context_windows_before_the_program_loads(launch, monkeypatch):
    client, rpc = fake_client(Config(mode="managed"), registry=list(CONTEXT_WINDOWS))
    monkeypatch.setattr(client.backend, "use_core_count", lambda cores: False)
    old_backend(rpc)
    opened = []
    monkeypatch.setattr(client.listwindows, "connect", lambda name: opened.append((name, rpc.methods()[-1])))
    client.debugger.configure(launch)
    client.debugger.start()

    # Right after startSession, and again after the load for windows registered late.
    assert opened == [(name, "startSession") for name in CONTEXT_WINDOWS] + [
        (name, "loadModuleWithOptions") for name in CONTEXT_WINDOWS
    ]


def test_a_shared_backend_keeps_to_its_own_windows(fake, launch, monkeypatch):
    client, rpc = fake
    old_backend(rpc)
    monkeypatch.setattr(client.listwindows, "connect", lambda name: pytest.fail(f"connected {name}"))
    client.debugger.configure(launch)
    client.debugger.start()
    assert client.debugger.started


def test_connecting_a_list_window_needs_it_registered(monkeypatch):
    client, _ = fake_client(registry=["WIN_CALL_STACK"])
    calls = []
    monkeypatch.setattr(client.listwindows, "call", lambda name, method: calls.append((name, method)))

    assert client.listwindows.connect("WIN_REGISTER_1") is False
    client.listwindows.connect("WIN_CALL_STACK")
    assert calls == [("WIN_CALL_STACK", "getNumberOfRows")]


def test_a_list_window_that_fails_to_connect_is_not_an_error(monkeypatch):
    client, _ = fake_client(registry=["WIN_CALL_STACK"])

    def refuse(name, method):
        raise CSpyError("no frontend")

    monkeypatch.setattr(client.listwindows, "call", refuse)
    assert client.listwindows.connect("WIN_CALL_STACK") is False


def test_an_attach_resumes_the_core_it_halted(fake, launch):
    client, rpc = fake
    old_backend(rpc).on("debugger", "go", None)
    client.debugger.configure_and_start({**launch, "request": "attach"})
    assert rpc.methods()[-1] == "go"


def test_an_attach_with_stop_on_entry_stays_halted(fake, launch):
    client, rpc = fake
    old_backend(rpc).on("debugger", "go", None)
    client.debugger.configure_and_start({**launch, "request": "attach", "stopOnEntry": True})
    assert "go" not in rpc.methods()


def test_a_launch_without_a_symbol_is_not_resumed(fake, launch):
    client, rpc = fake
    old_backend(rpc).on("debugger", "go", None)
    client.debugger.configure_and_start(launch)
    assert "go" not in rpc.methods()
