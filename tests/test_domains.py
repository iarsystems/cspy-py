"""Breakpoints, memory, context, terminal, list windows, project and options."""

from __future__ import annotations

import io
import json
import threading
from types import SimpleNamespace

import pytest

from iar_cspy import AccessType, Context, CSpyError, LaunchConfig, ProjectNotLoaded
from iar_cspy.context import context_ref


# -- breakpoints --------------------------------------------------------------


def test_breakpoint_add_validates_and_refreshes(session):
    client, rpc = session
    rpc.on("breakpoints", "setBreakpointOnUle", {"id": 11, "valid": True, "enabled": False})
    rpc.on("breakpoints", "getBreakpoint", lambda id: {"id": id, "valid": True, "enabled": True})

    bp = client.breakpoints.add("main")
    assert bp == {"id": 11, "valid": True, "enabled": True}
    assert rpc.args("breakpoints", "setBreakpointOnUle") == [("main", int(AccessType.FETCH))]


def test_breakpoint_add_rejects_an_invalid_result(session):
    client, rpc = session
    rpc.on("breakpoints", "setBreakpointOnUle", {"id": 0, "valid": False})
    with pytest.raises(CSpyError, match="invalid breakpoint"):
        client.breakpoints.add("no_such_symbol", AccessType.WRITE)


def test_breakpoint_with_category_and_restore(session):
    client, rpc = session
    rpc.on("breakpoints", "setBreakpointOnUleWithCategory", {"id": 12, "valid": True})
    rpc.on("breakpoints", "setBreakpointFromDescriptor", {"id": 0, "valid": False})
    rpc.on("breakpoints", "getBreakpoint", lambda id: {"id": id, "valid": True})

    assert client.breakpoints.add("main", 1, category="STD_CODE")["id"] == 12
    assert rpc.args("breakpoints", "setBreakpointOnUleWithCategory") == [("main", 1, "STD_CODE")]
    with pytest.raises(CSpyError, match="round-trip"):
        client.breakpoints.restore('{"ule":"main"}')


def test_breakpoint_listing_and_removal(session):
    client, rpc = session
    rpc.on("breakpoints", "getBreakpoints", [{"id": 1}, {"id": 2}])
    rpc.on("breakpoints", "enableBreakpoint", True).on("breakpoints", "removeBreakpoint", True)
    rpc.on("breakpoints", "getRecentlyHitBreakpoints", [{"id": 1}])

    assert client.breakpoints.disable(1) is True
    assert rpc.args("breakpoints", "enableBreakpoint") == [(1, False)]
    assert client.breakpoints.recently_hit() == [{"id": 1}]
    client.breakpoints.clear()
    assert rpc.args("breakpoints", "removeBreakpoint") == [(1,), (2,)]


def test_clear_reports_breakpoints_the_backend_kept(session):
    client, rpc = session
    rpc.on("breakpoints", "getBreakpoints", [{"id": 1, "ule": "main"}, {"id": 2, "ule": "f"}])
    rpc.on("breakpoints", "removeBreakpoint", lambda id: id != 2)  # false, not an exception
    with pytest.raises(CSpyError, match=r"did not remove these breakpoints: 2 \(f\)"):
        client.breakpoints.clear()
    assert rpc.args("breakpoints", "removeBreakpoint") == [(1,), (2,)]


def test_breakpoints_take_the_struct_or_its_id(session):
    client, rpc = session
    bp = rpc.struct("breakpoints", "getBreakpoint", id=7, ule="main", valid=True)
    rpc.on("breakpoints", "setBreakpointOnUle", bp).on("breakpoints", "getBreakpoint", bp)
    rpc.on("breakpoints", "enableBreakpoint", True).on("breakpoints", "removeBreakpoint", True)

    client.breakpoints.disable(bp)
    client.breakpoints.remove(7)
    assert rpc.args("breakpoints", "enableBreakpoint") == [(7, False)]
    with pytest.raises(CSpyError, match="breakpoint id or a Breakpoint"):
        client.breakpoints.remove("main")

    with client.breakpoints.temporary("main") as temp:
        assert temp.id == 7
    assert rpc.args("breakpoints", "removeBreakpoint") == [(7,), (7,)]


def test_a_rejected_breakpoint_keeps_the_backend_error(session):
    from iar_cspy import BackendError
    from iar_cspy.testing import cspy_exception

    client, rpc = session
    rpc.on("breakpoints", "setBreakpointOnUle", cspy_exception(code=6, method="DkUle", message="bad ULE"))
    with pytest.raises(BackendError, match="Setting a breakpoint on 'nope' failed") as exc:
        client.breakpoints.add("nope")
    assert client.classify_error(exc.value)["code"] == "CSPY_KDCERROR"


# -- memory, disassembly, source ----------------------------------------------


def test_memory_read_and_write(session):
    client, rpc = session
    rpc.on("memory", "readMemory", b"\x01\x02\x03\x04").on("memory", "writeMemory")

    assert client.memory.read(0x100, 4) == b"\x01\x02\x03\x04"
    loc, wordsize, bitsize, count = rpc.args("memory", "readMemory")[0]
    assert (loc.zone.id, loc.address, wordsize, bitsize, count) == (0, 0x100, 1, 8, 4)
    assert client.memory.read_u32(0x100) == 0x04030201

    assert client.memory.write(0x200, b"\xaa\xbb", zone=1) == 2
    loc, _, _, count, data = rpc.args("memory", "writeMemory")[0]
    assert (loc.zone.id, loc.address, count, data) == (1, 0x200, 2, b"\xaa\xbb")


def test_memory_takes_a_location_or_an_expression_value(session):
    client, rpc = session
    rpc.on("memory", "readMemory", b"\x00" * 4)
    value = rpc.struct(
        "debugger", "evalExpression", expression="g", hasLocation=True, location={"zone": {"id": 2}, "address": 0x40}
    )
    client.memory.read(value, 4)
    client.memory.read(value.location, 4, zone=0)
    locs = [args[0] for args in rpc.args("memory", "readMemory")]
    assert [(loc.zone.id, loc.address) for loc in locs] == [(2, 0x40), (0, 0x40)]

    register = rpc.struct("debugger", "evalExpression", expression="r0", hasLocation=False)
    with pytest.raises(CSpyError, match="has no address"):
        client.memory.read(register, 4)


def test_disassembly_and_source(session):
    client, rpc = session
    rpc.on("disassembly", "disassembleRange", [{"asm": "nop"}])
    rpc.on("sourcelookup", "getSourceRanges", [{"filename": "main.c"}])

    assert client.disassembly.range(0, 4, end_zone=1)[0]["asm"] == "nop"
    frm, to, ctx = rpc.args("disassembly", "disassembleRange")[0]
    assert (frm.zone.id, to.zone.id, to.address, ctx.type) == (0, 1, 4, 0)
    assert client.source.ranges(0x20)[0]["filename"] == "main.c"


# -- context ------------------------------------------------------------------


def test_context_refs(fake):
    client, _ = fake
    shared = client.idl.shared
    assert context_ref(client).type == shared.ContextType.CurrentBase
    ref = context_ref(client, {"type": "Task", "level": 2, "core": 1, "task": 9})
    assert (ref.type, ref.level, ref.core, ref.task) == (shared.ContextType.Task, 2, 1, 9)
    assert context_ref(client, Context.frame(3)).level == 3
    assert context_ref(client, {"type": 4}).type == 4
    with pytest.raises(CSpyError, match="Unknown context type"):
        context_ref(client, {"type": "Bogus"})
    with pytest.raises(CSpyError):
        context_ref(client, [])  # type: ignore[arg-type]


def test_stack_locals_and_symbol_lookup(session):
    client, rpc = session
    rpc.on("contextmanager", "getStack", [{"functionName": "main"}])
    rpc.on("contextmanager", "getStackDepth", 3)
    rpc.on("contextmanager", "getLocals", [{"name": "a"}, {"name": "x"}])
    rpc.on("contextmanager", "getParameters", [{"name": "x"}, {"name": "y"}])
    rpc.on("debugger", "evalExpression", {"value": "42"})

    assert client.context.stack(0, 10)[0]["functionName"] == "main"
    assert rpc.args("contextmanager", "getStack")[0][1:] == (0, 10)
    assert client.context.depth() == 3
    assert client.context.visible_symbols().all == ["a", "x", "y"]

    lookup = client.context.lookup("x")
    assert lookup.matches == ["x"]
    assert lookup.values == {"x": {"value": "42"}}
    with pytest.raises(CSpyError):
        client.context.lookup("  ")


# -- terminal -----------------------------------------------------------------


def test_terminal_buffers_output_and_queues_input(fake):
    client, rpc = fake
    handler = client.terminal.server.handler

    handler.printOutputBinary(b"hello ")
    handler.printOutput("world")
    handler.reportAssert("main.c", "12", "x > 0")
    handler.exit(3)
    out = client.terminal.output()
    assert out.text == "hello world"
    assert out.data == b"hello world"
    assert out.exit_code == 3
    assert out.asserts == [{"file": "main.c", "line": "12", "message": "x > 0"}]

    assert client.terminal.send("ab", newline=True) == 3
    assert handler.requestInput(2) == "ab"
    assert handler.requestInputBinary(5) == b"\n"

    client.terminal.output(clear=True)
    assert client.terminal.text == ""


def test_a_stdin_read_waits_for_input(fake):
    # Like a console: the target's read is answered once there is input, not
    # with an empty (end-of-file) answer because none was queued yet.
    client, _ = fake
    handler, terminal = client.terminal.server.handler, client.terminal
    result = []
    reader = threading.Thread(target=lambda: result.append(handler.requestInputBinary(1)))
    reader.start()
    assert terminal.wait_for_input_request(timeout=5) is True
    assert terminal.waiting_for_input is True and result == []
    terminal.send("xy")
    reader.join(5)
    assert result == [b"x"] and terminal.waiting_for_input is False
    assert handler.requestInputBinary(4) == b"y"  # queued input needs no wait


def test_cancel_input_answers_a_waiting_read_with_end_of_file(fake):
    client, _ = fake
    handler, terminal = client.terminal.server.handler, client.terminal
    result = []
    reader = threading.Thread(target=lambda: result.append(handler.requestInput(8)))
    reader.start()
    assert terminal.wait_for_input_request(timeout=5)
    terminal.cancel_input()
    reader.join(5)
    assert result == [""]
    assert terminal.wait_for_input_request(timeout=0.05) is False


def test_ending_the_session_releases_a_waiting_read(session):
    client, rpc = session
    rpc.on("debugger", "stopSession")
    reader = threading.Thread(target=client.terminal.server.handler.requestInputBinary, args=(1,))
    reader.start()
    assert client.terminal.wait_for_input_request(timeout=5)
    client.debugger.stop_session()
    reader.join(5)
    assert not reader.is_alive()


def test_request_input_takes_queued_input_without_waiting(fake):
    client, rpc = fake
    client.terminal.server.ensure(client.idl.service("libsupport.thrift", "LibSupportService2"))
    try:
        client.terminal.send("abc")
        assert client.terminal.request_input_binary(2) == b"ab"
        assert client.terminal.request_input(8) == "c"
        assert client.terminal.request_input_binary(1) == b""  # nothing queued: no wait
        assert rpc.calls == []  # its own queue, not a round trip that would block
    finally:
        client.terminal.server.stop()


def test_terminal_waits(fake):
    client, _ = fake
    handler = client.terminal.server.handler
    threading.Timer(0.05, handler.printOutput, ["DBG_DONE\n"]).start()
    assert client.terminal.wait_for_text("DBG_DONE", timeout=5) is True
    assert client.terminal.wait_for_text("never", timeout=0.05) is False

    assert client.terminal.wait_for_exit(timeout=0.05) is None
    threading.Timer(0.05, handler.exit, [3]).start()
    assert client.terminal.wait_for_exit(timeout=5) == 3
    client.clear_session_caches()  # a new session forgets the exit code
    assert client.terminal.exit_code is None


# -- list windows -------------------------------------------------------------


def test_list_window_rows_navigate_a_sliding_window_first(fake, monkeypatch):
    client, _ = fake
    chunk = {"numberOfRows": 0}
    calls = []

    def call(service_name, method, *args, trace=False):
        calls.append(method)
        if method == "isSliding":
            return True
        if method == "getChunkInfo":
            return dict(chunk)
        if method == "navigateToFraction":
            chunk["numberOfRows"] = 2
            return {"ok": True}
        if method == "getRow":
            return {"cells": [{"text": f"row{args[0]}"}]}
        return None

    monkeypatch.setattr(client.listwindows, "call", call)
    rows = client.listwindows.window("WIN_SLIDING_TRACE_WINDOW").rows(0, 10)
    assert (rows.row_count, len(rows.rows)) == (2, 2)
    assert rows.rows[1]["row"]["cells"][0]["text"] == "row1"
    assert calls.index("navigateToFraction") < calls.index("setVisibleRows")
    # Without a count, every row from first_row on.
    assert [r["index"] for r in client.listwindows.window("W").rows(1).rows] == [1]


def test_list_window_notifications(fake):
    client, _ = fake
    handler = client.listwindows.server.handler
    handler.notify({"kind": "n"})
    handler.notifyToolbar({"kind": "t"})
    notes, toolbar = client.listwindows.notifications(clear=True)
    assert (notes, toolbar) == ([{"kind": "n"}], [{"kind": "t"}])
    assert client.listwindows.notifications() == ([], [])


# -- events -------------------------------------------------------------------


def test_events_are_buffered_and_dispatched(fake):
    client, _ = fake
    seen = []
    unsubscribe = client.events.subscribe(lambda kind, event: seen.append(kind))
    client.events.server.handler.postDebugEvent({"note": 1})
    unsubscribe()
    client.events.server.handler.postLogEvent({"text": "x"})
    assert seen == ["debug"]
    assert [kind for kind, _ in client.events.recent()] == ["debug", "log"]


def test_forward_log_prints_log_events(fake):
    client, _ = fake
    out = io.StringIO()
    unsubscribe = client.events.forward_log(out)
    handler = client.events.server.handler
    handler.postLogEvent(SimpleNamespace(cat=1, text="Loaded debugee\n", timestamp=0))
    handler.postDebugEvent({"note": 1})
    handler.postLogEvent(SimpleNamespace(cat=3, text="Flash failed", timestamp=0))
    unsubscribe()
    handler.postLogEvent(SimpleNamespace(cat=1, text="after", timestamp=0))
    assert out.getvalue() == "Loaded debugee\nError: Flash failed\n"


# -- project ------------------------------------------------------------------


def test_project_resolve_defaults_to_current(fake):
    client, rpc = fake
    rpc.on("projectmanager", "GetCurrentProject", {"filename": "/w/proj.ewp"})
    rpc.on("projectmanager", "GetCurrentConfiguration", {"name": "Debug"})
    assert client.project.resolve() == ({"filename": "/w/proj.ewp"}, "Debug")
    assert client.project.resolve("/w/p.ewp", "Release") == ({"filename": "/w/p.ewp"}, "Release")
    assert rpc.methods() == ["GetCurrentProject", "GetCurrentConfiguration"]


def test_project_resolve_without_current_project(fake):
    client, rpc = fake
    rpc.on("projectmanager", "GetCurrentProject", {"filename": ""})
    with pytest.raises(CSpyError, match="No current project"):
        client.project.resolve()


def test_project_build(fake):
    client, rpc = fake
    rpc.on("projectmanager", "BuildProject", {"succeded": False, "buildOutput": ["a", "error: b"]})
    result = client.project.build("/w/p.ewp", "Debug", jobs=2)
    assert result.succeeded is False
    assert result.tail(1) == ["error: b"]
    assert rpc.args("projectmanager", "BuildProject") == [({"filename": "/w/p.ewp"}, "Debug", 2)]


def test_project_load_dispatches_on_extension(fake, monkeypatch):
    client, rpc = fake
    rpc.on("projectmanager", "LoadEwwFile").on("projectmanager", "LoadEwpFile")
    monkeypatch.setattr(client.project, "status", lambda: {})
    client.project.load("/w/space.eww")
    client.project.load("/w/proj.EWP", fetch_dependency_data=False)
    assert rpc.calls == [
        ("projectmanager", "LoadEwwFile", ("/w/space.eww", True), {}),
        ("projectmanager", "LoadEwpFile", ("/w/proj.EWP", False), {}),
    ]


def test_project_start_debugging(fake):
    client, rpc = fake
    launch = {"program": "/w/Debug/Exe/a.out"}
    rpc.on("projectmanager", "BuildProject", {"succeded": True, "buildOutput": []})
    rpc.on("projectmanager", "GetLaunchConfigurationForConfiguration", launch)
    rpc.on("debugger", "configureSession").on("debugger", "startSMPSession")

    result = client.project.start_debugging("/w/p.ewp", "Debug")
    assert (result.build.succeeded, result.configured, result.started) == (True, True, True)
    # The backend's launch configuration is used as is, not re-resolved.
    assert rpc.methods("debugger") == ["configureSession", "startSMPSession"]
    assert rpc.args("debugger", "configureSession") == [(launch,)]
    assert client.debugger.started


def test_project_start_debugging_stops_after_a_failed_build(fake):
    client, rpc = fake
    rpc.on("projectmanager", "BuildProject", {"succeded": False, "buildOutput": ["error"]})
    result = client.project.start_debugging("/w/p.ewp", "Debug")
    assert result.build.succeeded is False
    assert result.configured is False
    assert rpc.methods() == ["BuildProject"]


# -- options ------------------------------------------------------------------


def ok(**fields):
    return {"success": {"value": True, "failureMessage": ""}, **fields}


def test_options_session_round_trip(fake):
    client, rpc = fake
    rpc.on("options", "CreateSession", ok(sessionId={"value": "3", "type": "OptionsService"}, readOnly=False))
    rpc.on("options", "GetCategoryTree", ok(tree={"id": {"value": "Category"}, "data": "<pages/>"}))
    rpc.on("options", "UpdateOptionsState", ok(tree={"data": "<x/>"}, verificationErrors=[{"e": 1}]))
    rpc.on("options", "DestroySession", ok())

    with client.options.open("/w/p.ewp", "Debug") as opts:
        assert (opts.id, opts.read_only) == ("3", False)
        assert opts.category_tree().xml == "<pages/>"
        update = opts.update("General", updated=[{"optionDefinitionId": "u"}])
        assert update.ok is False and update.verification_errors == [{"e": 1}]

    request = rpc.args("options", "UpdateOptionsState")[0][0]
    assert request["sessionId"] == {"value": "3", "type": "OptionsService"}
    assert request["treeId"] == {"value": "General", "type": "OptionsService"}
    assert request["updatedOptionValues"] == [{"optionDefinitionId": "u"}]
    assert rpc.methods()[-1] == "DestroySession"
    create = rpc.args("options", "CreateSession")[0][0]
    assert create == {
        "projectPath": "/w/p.ewp",
        "configurationName": "Debug",
        "nodePathOrIndex": "",
        "showHiddenOptions": False,
    }


def test_options_failures_are_raised(fake):
    client, rpc = fake
    failure = {"success": {"value": False, "failureMessage": "Project not found: /w/p.ewp"}}
    rpc.on("options", "CreateSession", failure)
    with pytest.raises(ProjectNotLoaded) as exc:
        client.options.open("/w/p.ewp", "Debug")
    assert exc.value.project_path == "/w/p.ewp"
    assert "client.project.load" in str(exc.value)

    rpc.on("options", "CreateSession", {"success": {"value": False, "failureMessage": "no such configuration"}})
    with pytest.raises(CSpyError, match="no such configuration") as exc2:
        client.options.open("/w/p.ewp", "Nope")
    assert not isinstance(exc2.value, ProjectNotLoaded)


# -- launch configurations ----------------------------------------------------


def test_launch_config_from_file_resolves_paths(tmp_path):
    (tmp_path / "launch.json").write_text(
        json.dumps({"configurations": [{"name": "a", "program": "Debug/a.out"}, {"name": "b", "program": "/abs/b.out"}]})
    )
    first = LaunchConfig.from_file(tmp_path / "launch.json")
    assert first["program"] == str(tmp_path / "Debug" / "a.out")
    assert LaunchConfig.from_file(tmp_path / "launch.json", name="b")["program"] == "/abs/b.out"
    with pytest.raises(CSpyError, match="No launch configuration named"):
        LaunchConfig.from_file(tmp_path / "launch.json", name="c")
    assert LaunchConfig.coerce(str(tmp_path / "launch.json")) == first


def test_launch_config_from_json():
    assert LaunchConfig.from_json('{"stopOnSymbol": "main"}').stop_on_symbol == "main"
    assert LaunchConfig.from_json('{"configurations": [{"name": "x"}]}') == {"name": "x"}
    with pytest.raises(CSpyError, match="Invalid launch configuration JSON"):
        LaunchConfig.from_json("{not json")
