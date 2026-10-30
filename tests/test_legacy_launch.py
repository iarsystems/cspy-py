"""The launch sequence for backends without resolveLaunchConfiguration (IDE platform before 9.5)."""

from __future__ import annotations

import pytest
from thriftpy2.thrift import TApplicationException

from iar_cspy import Config, CSpyError, UnsupportedMethod, classify
from iar_cspy.testing import fake_client


def unknown_method(name):
    return TApplicationException(TApplicationException.UNKNOWN_METHOD, f"Invalid method name: '{name}'")


@pytest.fixture
def install(tmp_path):
    """A fake installation with the arm simulator, proc and libsupport libraries."""
    bin_dir = tmp_path / "ewarm" / "arm" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("libarmsim2.so", "libarmPROC.so", "libarmLibsupportUniversal.so", "armsim2.dll", "armproc.dll", "armLibSupportUniversal.dll"):
        (bin_dir / name).touch()
    program = tmp_path / "app.out"
    program.touch()
    return tmp_path / "ewarm", program


@pytest.fixture
def legacy(install):
    iar_path, program = install
    client, rpc = fake_client(Config(mode="standalone", registry_port=4711, iar_path=iar_path))
    rpc.on("debugger", "resolveLaunchConfiguration", unknown_method("resolveLaunchConfiguration"))
    for method in ("startSession", "loadMacroFile", "flashModule", "loadModuleWithOptions"):
        rpc.on("debugger", method)
    launch = {
        "type": "cspy",
        "request": "launch",
        "target": "arm",
        "program": str(program),
        "driver": "Simulator",
        "driverOptions": ["--cpu=Cortex-M3"],
        "setupMacros": ["/m/setup.mac"],
        "download": {"deviceMacros": ["/m/device.mac"], "flashLoader": "/f/board.board"},
    }
    return client, rpc, launch, iar_path


def test_unknown_methods_become_unsupported_method():
    client, rpc = fake_client()
    rpc.on("debugger", "getVersionString", unknown_method("getVersionString"))
    with pytest.raises(UnsupportedMethod) as err:
        client.debugger.version()
    assert err.value.method == "getVersionString"
    assert classify(err.value)["code"] == "UNSUPPORTED_METHOD"
    assert err.value.diagnostics == ""


def test_configure_builds_the_session_configuration_without_configuring(legacy):
    client, rpc, launch, iar_path = legacy
    client.debugger.configure(launch)

    assert rpc.methods() == ["resolveLaunchConfiguration"]
    assert client.debugger.state == {"configured": True, "started": False}
    resolved = client.debugger.launch_configuration
    assert resolved.driverNameOrEmpty == "Simulator"
    assert resolved.driverLib.lower().endswith(("libarmsim2.so", "armsim2.dll"))
    # The breakpoints pick the driver up from it, as for newer backends.
    assert client.breakpoints.modes()[0].value == "auto"


def test_start_runs_the_legacy_sequence(legacy):
    client, rpc, launch, iar_path = legacy
    client.debugger.configure(launch)
    client.debugger.start()

    assert rpc.methods() == [
        "resolveLaunchConfiguration",
        "startSession",
        "loadMacroFile",
        "flashModule",
        "loadMacroFile",
        "loadModuleWithOptions",
    ]
    assert client.debugger.state == {"configured": True, "started": True}
    (session,) = rpc.args("debugger", "startSession")[0]
    assert session["executable"] == launch["program"]
    assert session["toolkitDir"] == str(iar_path / "arm")
    assert session["setupMacros"] == ["/m/setup.mac"]
    assert any("ibsupport" in plugin.lower() for plugin in session["plugins"])
    assert [a[0] for a in rpc.args("debugger", "loadMacroFile")] == ["/m/device.mac", "/m/setup.mac"]
    assert rpc.args("debugger", "flashModule") == [("/f/board.board", launch["program"], [], [])]
    program, options = rpc.args("debugger", "loadModuleWithOptions")[0]
    assert program == launch["program"]
    assert options["resetAfterLoad"] and not options["shouldAttach"] and not options["suppressDownload"]


def test_attach_neither_flashes_nor_downloads(legacy):
    client, rpc, launch, _ = legacy
    client.debugger.configure({**launch, "request": "attach"})
    client.debugger.start()

    assert "flashModule" not in rpc.methods()
    _, options = rpc.args("debugger", "loadModuleWithOptions")[0]
    assert options["shouldAttach"] and options["suppressDownload"] and not options["callUserMacros"]


def test_extra_images_take_string_or_number_offsets(legacy):
    client, rpc, launch, _ = legacy
    images = [{"image": "/x/a.out", "offset": "0x100"}, {"image": "/x/b.out", "offset": 16, "suppressDownload": True}]
    client.debugger.configure({**launch, "download": {"extraImages": images}})
    client.debugger.start()

    _, options = rpc.args("debugger", "loadModuleWithOptions")[0]
    assert options["extraDebugFiles"] == [
        {"doDownload": True, "path": "/x/a.out", "offset": 0x100},
        {"doDownload": False, "path": "/x/b.out", "offset": 16},
    ]


def test_a_failing_macro_names_the_macro(legacy):
    client, rpc, launch, _ = legacy
    rpc.on("debugger", "loadMacroFile", CSpyError("boom"))
    client.debugger.configure(launch)
    with pytest.raises(CSpyError, match="Failed to load macro '/m/device.mac'"):
        client.debugger.start()
    assert client.debugger.state == {"configured": True, "started": False}


def test_legacy_configure_needs_driver_options(legacy):
    client, _, launch, _ = legacy
    del launch["driverOptions"]
    with pytest.raises(CSpyError, match="needs \"driverOptions\""):
        client.debugger.configure(launch)
    assert client.debugger.state == {"configured": False, "started": False}


def test_legacy_configure_needs_the_installation(install):
    _, program = install
    client, rpc = fake_client()
    rpc.on("debugger", "resolveLaunchConfiguration", unknown_method("resolveLaunchConfiguration"))
    launch = {"target": "arm", "program": str(program), "driver": "Simulator", "driverOptions": []}
    with pytest.raises(CSpyError, match="workbenchPath"):
        client.debugger.configure(launch)
