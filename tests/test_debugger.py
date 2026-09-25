"""Debugger session lifecycle and run control against a scripted backend."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from iar_cspy import Config, CoreState, CSpyError, SessionError, WaitTimeout
from iar_cspy.testing import cspy_exception, fake_client


LAUNCH = {"type": "cspy", "request": "launch", "program": "/w/a.out"}


def started(client):
    client.debugger.set_state(configured=True, started=True)
    return client


def test_configure_resolves_then_configures(fake):
    client, rpc = fake
    rpc.on("debugger", "resolveLaunchConfiguration", {"resolved": True}).on("debugger", "configureSession")

    client.debugger.configure(LAUNCH)
    assert rpc.methods() == ["resolveLaunchConfiguration", "configureSession"]
    assert json.loads(rpc.args("debugger", "resolveLaunchConfiguration")[0][0]) == LAUNCH
    assert rpc.args("debugger", "configureSession") == [({"resolved": True},)]
    assert client.debugger.state == {"configured": True, "started": False}


def test_configure_accepts_launch_json_wrapper_and_text(fake):
    client, rpc = fake
    rpc.on("debugger", "resolveLaunchConfiguration", {}).on("debugger", "configureSession")
    client.debugger.configure(json.dumps({"configurations": [LAUNCH]}))
    assert json.loads(rpc.args("debugger", "resolveLaunchConfiguration")[0][0]) == LAUNCH


def test_configure_failure_resets_state(fake):
    client, rpc = fake
    started(client)
    rpc.on("debugger", "resolveLaunchConfiguration", {}).on("debugger", "configureSession", CSpyError("boom"))
    with pytest.raises(CSpyError, match="Local lifecycle state was reset"):
        client.debugger.configure(LAUNCH)
    assert client.debugger.state == {"configured": False, "started": False}


def test_start_requires_configure(fake):
    client, _ = fake
    with pytest.raises(SessionError):
        client.debugger.start()


def test_require_started(fake):
    client, _ = fake
    with pytest.raises(SessionError, match="requires an active debug session"):
        client.debugger.require_started("reading the stack")
    started(client).debugger.require_started("reading the stack")


def lifecycle_rpc(rpc, **overrides):
    rpc.on("debugger", "resolveLaunchConfiguration", {"resolved": True})
    for method in ("configureSession", "startSMPSession", "stopSession", "runToULE"):
        rpc.on("debugger", method, None)
    rpc.on("debugger", "isOnline", False)
    rpc.on("debugger", "getCoreState", 0)  # run_to(stopOnSymbol) waits for the stop
    for method, response in overrides.items():
        rpc.on("debugger", method, response)
    return rpc


def test_configure_and_start_stops_an_active_session_first(fake):
    client, rpc = fake
    lifecycle_rpc(rpc)
    started(client)

    result = client.debugger.configure_and_start(LAUNCH)
    assert result.handoff["had_active_session"] is True
    assert result.handoff["stop_ok"] is True
    assert result.handoff["strict_cleanup_used"] is False
    assert rpc.methods() == [
        "isOnline",
        "stopSession",
        "resolveLaunchConfiguration",
        "configureSession",
        "startSMPSession",
    ]
    assert client.debugger.started


def test_configure_and_start_runs_to_stop_on_symbol_after_start(fake):
    client, rpc = fake
    lifecycle_rpc(rpc)
    result = client.debugger.configure_and_start({**LAUNCH, "stopOnSymbol": "main"})
    assert (result.stop_on_symbol, result.ran_to_symbol) == ("main", True)
    assert rpc.args("debugger", "runToULE") == [("main", True)]
    assert rpc.methods().index("startSMPSession") < rpc.methods().index("runToULE")


def test_configure_and_start_reports_stop_on_symbol_failure(fake):
    client, rpc = fake
    lifecycle_rpc(rpc, runToULE=CSpyError("symbol not found: nope"))
    result = client.debugger.configure_and_start({**LAUNCH, "stopOnSymbol": "nope"})
    assert result.ran_to_symbol is False
    assert "nope" in result.stop_on_symbol_error
    assert client.debugger.started


def test_configure_and_start_stops_a_stale_backend_session(fake):
    client, rpc = fake
    stale = {"active": True}

    def stop():
        stale["active"] = False

    def start():
        if stale["active"]:
            raise CSpyError("stale backend session still active")

    lifecycle_rpc(rpc, isOnline=lambda: stale["active"], stopSession=stop, startSMPSession=start)
    result = client.debugger.configure_and_start(LAUNCH)
    assert result.handoff["stale_backend_online"] is True
    assert rpc.methods().count("stopSession") == 1


def test_configure_and_start_falls_back_to_cleanup_when_stop_fails(fake):
    client, rpc = fake
    lifecycle_rpc(rpc, stopSession=CSpyError("stop failed"))
    started(client)
    result = client.debugger.configure_and_start(LAUNCH)
    assert result.handoff["stop_ok"] is False
    assert result.handoff["strict_cleanup_used"] is True
    # stop_session reset the local state even though it failed, so the
    # cleanup has no session left to stop and succeeds.
    assert result.handoff["strict_cleanup_ok"] is True


def test_configure_and_start_in_managed_mode_always_cleans_up(monkeypatch):
    client, rpc = fake_client(Config(mode="managed"))
    lifecycle_rpc(rpc)
    restarts = []
    monkeypatch.setattr(client.backend, "restart_debugger", lambda: restarts.append(True))

    result = client.debugger.configure_and_start(LAUNCH)
    assert result.handoff["managed_fresh_process"] is True
    assert result.handoff["strict_cleanup_used"] is True
    assert restarts == [True]
    assert "isOnline" not in rpc.methods()


def test_stop_session_when_already_stopped_is_a_no_op(fake):
    client, rpc = fake
    result = client.debugger.stop_session()
    assert result.already_stopped is True
    assert result.backend_restarted is False
    assert rpc.calls == []


def test_stop_session_failure_still_resets_state(fake):
    client, rpc = fake
    rpc.on("debugger", "stopSession", CSpyError("stop failed"))
    started(client)
    with pytest.raises(CSpyError, match="stop failed"):
        client.debugger.stop_session()
    assert client.debugger.state == {"configured": False, "started": False}


def test_stop_session_treats_dkstop_on_a_stopped_target_as_success(fake):
    client, rpc = fake
    rpc.on("debugger", "stopSession", cspy_exception())
    rpc.on("debugger", "isOnline", True).on("debugger", "getNumberOfCores", 1).on("debugger", "getCoreState", 0)
    started(client)

    result = client.debugger.stop_session()
    assert result.stop_idempotent_recovered is True
    assert result.stop_idempotent_details["probe"]["already_stopped"] is True


def test_stop_session_in_managed_mode_restarts_the_backend(monkeypatch):
    client, rpc = fake_client(Config(mode="managed"))
    rpc.on("debugger", "stopSession")
    restarts = []
    monkeypatch.setattr(client.backend, "restart_debugger", lambda: restarts.append(True))
    started(client)
    assert client.debugger.stop_session().backend_restarted is True
    assert restarts == [True]


def test_cleanup_resets_everything_and_reports_failures(fake, monkeypatch):
    client, rpc = fake
    rpc.on("debugger", "reset", CSpyError("boom")).on("debugger", "stopSession", CSpyError("boom"))
    monkeypatch.setattr(
        client.backend, "restart_debugger", lambda: (_ for _ in ()).throw(RuntimeError("shutdown failed"))
    )
    client.terminal.send("abc")
    started(client)

    result = client.debugger.cleanup(reset_target=True)
    assert result.ok is False
    assert [e["message"] for e in result.errors] == [
        "reset failed",
        "stopSession failed",
        "shutdown_managed_server failed",
    ]
    assert result.before == {"configured": True, "started": True}
    assert result.after == {"configured": False, "started": False}
    assert client.terminal.send("") == 0  # input queue was cleared


def test_session_context_manager_stops_on_exit(fake):
    client, rpc = fake
    lifecycle_rpc(rpc)
    with client.debugger.session(LAUNCH) as dbg:
        assert dbg.started
    assert rpc.methods()[-1] == "stopSession"
    assert not client.debugger.started


def test_session_context_manager_keeps_the_original_error(fake):
    client, rpc = fake
    lifecycle_rpc(rpc)
    with pytest.raises(ZeroDivisionError):
        with client.debugger.session(LAUNCH):
            rpc.on("debugger", "stopSession", CSpyError("teardown failed too"))
            raise ZeroDivisionError


def test_wait_for_state(fake):
    client, rpc = fake
    states = iter([1, 1, 0])
    rpc.on("debugger", "getCoreState", lambda core: next(states))
    result = client.debugger.wait_for_state(CoreState.STOPPED, timeout=1, poll_interval=0)
    assert (result.ok, result.attempts, result.state) == (True, 3, 0)


def test_wait_for_state_timeout(fake):
    client, rpc = fake
    rpc.on("debugger", "getCoreState", 1)
    with pytest.raises(WaitTimeout) as exc:
        client.debugger.wait_for_state(timeout=0, poll_interval=0)
    assert exc.value.result.timed_out is True
    result = client.debugger.wait_for_state(timeout=0, poll_interval=0, check=False)
    assert (result.ok, result.timed_out, result.state) == (False, True, 1)


def test_go_and_wait(fake):
    client, rpc = fake
    states = iter([1, 0])
    rpc.on("debugger", "go").on("debugger", "getCoreState", lambda core: next(states))
    assert client.debugger.go_and_wait(timeout=1, poll_interval=0).ok
    assert rpc.methods() == ["go", "getCoreState", "getCoreState"]


def test_go_and_wait_waits_for_the_stop_event(fake):
    # The core reads as stopped both before it starts and after it stops, so
    # the target-stopped event is what tells the two apart.
    client, rpc = fake
    client.events.receiving = True
    handler = client.events.server.handler

    def go():
        threading.Timer(0.2, handler.postDebugEvent, [SimpleNamespace(note=0)]).start()

    rpc.on("debugger", "go", go).on("debugger", "getCoreState", 0)
    start = time.perf_counter()
    result = client.debugger.go_and_wait(timeout=5, poll_interval=0)
    assert result.ok
    assert time.perf_counter() - start >= 0.2


def test_go_and_wait_ignores_events_from_before_the_go(fake):
    client, rpc = fake
    client.events.receiving = True
    client.events.server.handler.postDebugEvent(SimpleNamespace(note=0))
    rpc.on("debugger", "go").on("debugger", "getCoreState", 0)
    result = client.debugger.go_and_wait(timeout=0.2, poll_interval=0, check=False)
    # No stop event after the go within the timeout: only one final state poll.
    assert result.attempts == 1


def test_run_control_methods(fake):
    client, rpc = fake
    for method in ("go", "stop", "reset", "stepOver", "step", "stepOut", "loadModule"):
        rpc.on("debugger", method)
    dbg = client.debugger
    dbg.go(), dbg.halt(), dbg.reset(), dbg.step_over(), dbg.step_into(True), dbg.step_out()
    dbg.load_module("a.out")
    assert rpc.methods() == ["go", "stop", "reset", "stepOver", "step", "stepOut", "loadModule"]
    # The step flag is only sent when given (see Debugger._step).
    assert rpc.args("debugger", "stepOver") == [()]
    assert rpc.args("debugger", "step") == [(True,)]


def test_core_state_is_an_enum(fake):
    client, rpc = fake
    rpc.on("debugger", "getCoreState", 1).on("debugger", "getNumberOfCores", 2)
    assert client.debugger.core_state() is CoreState.RUNNING
    assert client.debugger.core_states() == [CoreState.RUNNING, CoreState.RUNNING]


def test_eval_builds_a_context_ref(fake):
    client, rpc = fake
    rpc.on("debugger", "evalExpression", {"value": "7"})
    assert client.debugger.eval("x + 1") == {"value": "7"}
    ref, expr, sub, fmt, prefix = rpc.args("debugger", "evalExpression")[0]
    assert type(ref).__name__ == "ContextRef" and ref.type == 0
    assert (expr, sub, fmt, prefix) == ("x + 1", [], 0, False)
    with pytest.raises(CSpyError):
        client.debugger.eval("  ")


def test_call_keeps_lifecycle_state_in_sync(fake):
    client, rpc = fake
    for method in ("configureSession", "startSMPSession", "stopSession"):
        rpc.on("debugger", method)
    dbg = client.debugger
    dbg.call("configureSession", {})
    assert dbg.state == {"configured": True, "started": False}
    dbg.call("startSMPSession")
    assert dbg.state == {"configured": True, "started": True}
    dbg.call("stopSession")
    assert dbg.state == {"configured": False, "started": False}


def test_status(fake):
    client, rpc = fake
    rpc.on("debugger", "isOnline", True).on("debugger", "getNumberOfCores", 2)
    rpc.on("debugger", "getCoreState", lambda core: core)
    assert client.debugger.status()["core_states"] is None  # not started
    started(client)
    status = client.debugger.status()
    assert status["backend_online"] is True
    assert status["core_states"] == [0, 1]
    assert status["registry_port"] == 4711


class NamedLocation:
    def __init__(self, name):
        self.name, self.nameAlias, self.description = name, name.upper(), "reg"
        self.fullBitSize, self.readonly, self.writeonly = 32, False, False
        self.location = f"LOC:{name}"


def test_registers(fake, monkeypatch):
    client, rpc = fake
    rpc.on("debugger", "getRegisterGroups", ["CPU Registers (ABI)"])
    rpc.on("debugger", "getLocationNamesInGroup", ["r0", "r1", "r2"])
    rpc.on("debugger", "getNamedLocation", NamedLocation)

    class Memory:
        def readMemory(self, location, wordsize, bitsize, count):
            assert (wordsize, bitsize, count) == (1, 8, 4)
            return b"\x34\x12\x00\x00"

    class Conn:
        raw = Memory()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(client, "connection", lambda service: Conn())
    snapshot = client.debugger.registers(limit=2)
    assert snapshot.total_in_group == 3
    assert [r.name for r in snapshot.registers] == ["r0", "r1"]
    assert snapshot["R1"].value == 0x1234
    assert snapshot.registers[0].raw == b"\x34\x12\x00\x00"
