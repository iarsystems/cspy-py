"""Debugger session lifecycle and run control against a scripted backend."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from iar_cspy import BackendError, Config, CoreState, CSpyError, SessionError, StopResult, WaitTimeout
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


def test_a_multicore_configuration_gets_a_cspyserver2_for_its_core_count(monkeypatch):
    client, rpc = fake_client(Config(mode="managed"))
    counts = []
    monkeypatch.setattr(client.backend, "use_core_count", lambda n: counts.append(n) or len(counts) == 2)
    callbacks = []
    client.ensure_callbacks = lambda: callbacks.append(True)
    resolved = {"driverOptions": ["--cpu=Cortex-M3", "--multicore_nr_of_cores=2"]}
    rpc.on("debugger", "resolveLaunchConfiguration", resolved).on("debugger", "configureSession")

    client.debugger.configure({**LAUNCH, "driverOptions": ["--multicore_nr_of_cores=2"]})
    # From the launch before resolving, then from what was resolved; the
    # second one replaced the process, so the callbacks were registered again.
    assert counts == [2, 2] and len(callbacks) == 2
    assert rpc.args("debugger", "configureSession") == [(resolved,)]


def test_core_count_of_a_launch_configuration():
    from iar_cspy.debugger import core_count

    assert core_count({"driverOptions": ["--cpu=Cortex-M3"]}) == 1
    assert core_count({"driverOptions": ["--multicore_nr_of_cores=4"]}) == 4
    assert core_count(SimpleNamespace(driverOptions=["--multicore_nr_of_cores=2"])) == 2
    assert core_count({}) == 1


def test_a_failed_multicore_configure_explains_the_command_line(fake):
    client, rpc = fake
    rpc.on("debugger", "resolveLaunchConfiguration", {"driverOptions": ["--multicore_nr_of_cores=2"]})
    rpc.on("debugger", "configureSession", cspy_exception(7, "DkConfigure", "failed to configure debugger driver"))
    with pytest.raises(BackendError, match="started with --multicore_nr_of_cores=2 on its command line"):
        client.debugger.configure(LAUNCH)


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
    monkeypatch.setattr(client.backend, "stop_debugger", lambda: restarts.append(True))

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
    monkeypatch.setattr(client.backend, "stop_debugger", lambda: restarts.append(True))
    started(client)
    result = client.debugger.stop_session()
    assert (result.backend_restarted, result.already_stopped) == (True, False)
    assert restarts == [True]
    # The process's Debugger.exit() ends the session: no deprecated stopSession.
    assert "stopSession" not in rpc.methods()
    assert client.debugger.state == {"configured": False, "started": False}


def test_cleanup_in_managed_mode_leaves_stopping_to_the_process_exit(monkeypatch):
    client, rpc = fake_client(Config(mode="managed"))
    restarts = []
    monkeypatch.setattr(client.backend, "stop_debugger", lambda: restarts.append(True))
    started(client)
    assert client.debugger.cleanup().ok is True
    assert restarts == [True] and rpc.calls == []


def test_cleanup_resets_everything_and_reports_failures(fake, monkeypatch):
    client, rpc = fake
    rpc.on("debugger", "reset", CSpyError("boom")).on("debugger", "stopSession", CSpyError("boom"))
    monkeypatch.setattr(
        client.backend, "stop_debugger", lambda: (_ for _ in ()).throw(RuntimeError("shutdown failed"))
    )
    client.terminal.send("abc")
    started(client)

    result = client.debugger.cleanup(reset_target=True)
    assert result.ok is False
    assert [e["message"] for e in result.errors] == [
        "reset failed",
        "stopSession failed",
        "stop_debugger failed",
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


def test_wait_for_state(session):
    client, rpc = session
    states = iter([1, 1, 0])
    rpc.on("debugger", "getCoreState", lambda core: next(states))
    result = client.debugger.wait_for_state(CoreState.STOPPED, timeout=1, poll_interval=0)
    assert (result.ok, result.attempts, result.state) == (True, 3, 0)


def test_wait_for_state_timeout(session):
    client, rpc = session
    rpc.on("debugger", "getCoreState", 1)
    with pytest.raises(WaitTimeout) as exc:
        client.debugger.wait_for_state(timeout=0, poll_interval=0)
    assert exc.value.result.timed_out is True
    result = client.debugger.wait_for_state(timeout=0, poll_interval=0, check=False)
    assert (result.ok, result.timed_out, result.state) == (False, True, 1)


def test_go_and_wait(session):
    client, rpc = session
    states = iter([1, 0])
    rpc.on("debugger", "go").on("debugger", "getCoreState", lambda core: next(states))
    assert client.debugger.go_and_wait(timeout=1, poll_interval=0).ok
    assert rpc.methods() == ["go", "getCoreState", "getCoreState"]


STARTED, STOPPED = SimpleNamespace(note=1), SimpleNamespace(note=0)
INSPECTED, FATAL = SimpleNamespace(note=4), SimpleNamespace(note=13, descr="kDkFatalError", params=[])


def post_later(client, delay, *events):
    handler = client.events.server.handler
    threading.Timer(delay, lambda: [handler.postDebugEvent(e) for e in events]).start()


def test_go_and_wait_waits_for_the_stop_event(session):
    # The core reads as stopped both before it starts and after it stops, so
    # the target-started/stopped events are what tell the two apart.
    client, rpc = session
    client.events.receiving = True
    rpc.on("debugger", "go", lambda: post_later(client, 0.2, STARTED, STOPPED)).on("debugger", "getCoreState", 0)
    result = client.debugger.go_and_wait(timeout=5, poll_interval=0)
    assert (result.ok, result.state) == (True, CoreState.STOPPED)
    # Counted from the go, not from the final poll.
    assert result.elapsed_ms >= 200


def test_go_and_wait_ignores_a_stop_before_the_target_started(session):
    # A late stop event of an earlier halt, posted after the go but before
    # the target has started running again, is not this run's stop.
    client, rpc = session
    client.events.receiving = True
    rpc.on("debugger", "go", lambda: post_later(client, 0.0, STOPPED, STARTED)).on("debugger", "getCoreState", 0)
    result = client.debugger.go_and_wait(timeout=0.3, poll_interval=0, check=False)
    assert (result.ok, result.timed_out) == (False, True)


def test_go_and_wait_without_a_stop_event_times_out(session):
    client, rpc = session
    client.events.receiving = True
    client.events.server.handler.postDebugEvent(STARTED)
    client.events.server.handler.postDebugEvent(STOPPED)  # from before the go
    rpc.on("debugger", "go").on("debugger", "getCoreState", 0)
    result = client.debugger.go_and_wait(timeout=0.2, poll_interval=0, check=False)
    # The core reads "stopped", but it was never seen to run and stop.
    assert (result.ok, result.timed_out, result.attempts) == (False, True, 1)
    assert result.elapsed_ms >= 200
    with pytest.raises(WaitTimeout, match="no target-stopped event followed a target-started one"):
        client.debugger.go_and_wait(timeout=0.05, poll_interval=0)


def test_steps_wait_for_the_stop(session):
    client, rpc = session
    client.events.receiving = True
    rpc.on("debugger", "stepOver", lambda: post_later(client, 0.05, STARTED, STOPPED))
    rpc.on("debugger", "getCoreState", 0)
    assert client.debugger.step_over(timeout=2).ok
    assert client.debugger.step_over(wait=False) is None


def test_a_stop_waits_for_the_inspection_context_to_follow(session):
    # The backend posts the stop before it has moved its inspection context
    # to the new location; values read in between could be stale.
    client, rpc = session
    client.events.receiving = True
    rpc.on("debugger", "stepOver", lambda: post_later(client, 0, STARTED, STOPPED) or post_later(client, 0.2, INSPECTED))
    rpc.on("debugger", "getCoreState", 0)
    started_at = time.perf_counter()
    assert client.debugger.step_over(timeout=2).ok
    assert 0.2 <= time.perf_counter() - started_at < 0.45  # until the context moved, not the 0.5 s bound


def test_a_stop_without_the_inspection_event_waits_only_the_settle_bound(fake):
    client, _ = fake
    handler = client.events.server.handler
    mark = client.events.mark()
    handler.postDebugEvent(STARTED)
    handler.postDebugEvent(STOPPED)
    started_at = time.perf_counter()
    assert client.events.wait_for_stop(after=mark, timeout=5, settle=0.1) is STOPPED
    assert 0.1 <= time.perf_counter() - started_at < 1


def test_a_fatal_error_ends_the_wait_and_the_session(session):
    client, rpc = session
    client.events.receiving = True
    rpc.on("debugger", "go", lambda: post_later(client, 0.05, STARTED, FATAL)).on("debugger", "getCoreState", 1)
    client.events.server.handler.postLogEvent(SimpleNamespace(text="Fatal error: probe lost   Session aborted!"))
    started_at = time.perf_counter()
    with pytest.raises(SessionError, match="fatal error.*probe lost"):
        client.debugger.go_and_wait(timeout=5)
    assert time.perf_counter() - started_at < 2  # not the whole timeout
    assert client.debugger.state == {"configured": False, "started": False}


def test_a_fatal_error_event_fails_the_next_session_call(session):
    client, rpc = session
    rpc.on("breakpoints", "getBreakpoints", [])
    client.events.server.handler.postDebugEvent(FATAL)
    with pytest.raises(SessionError, match="fatal error, which ends the debug session: kDkFatalError"):
        client.breakpoints.all()
    with pytest.raises(SessionError, match="requires an active debug session"):
        client.breakpoints.all()  # reported once; after that there is just no session
    assert rpc.calls == []


def test_run_control_methods(session):
    client, rpc = session
    for method in ("go", "stop", "reset", "stepOver", "step", "stepOut", "loadModule"):
        rpc.on("debugger", method)
    rpc.on("debugger", "getCoreState", 0)
    dbg = client.debugger
    dbg.go(), dbg.halt(), dbg.reset(), dbg.step_over(), dbg.step_into(True), dbg.step_out(wait=False)
    dbg.load_module("a.out")
    assert rpc.methods() == [
        "go", "stop", "reset", "stepOver", "getCoreState", "step", "getCoreState", "stepOut", "loadModule"
    ]
    # The step flag is only sent when given (see Debugger._step).
    assert rpc.args("debugger", "stepOver") == [()]
    assert rpc.args("debugger", "step") == [(True,)]


def test_core_state_is_an_enum(session):
    client, rpc = session
    rpc.on("debugger", "getCoreState", 1).on("debugger", "getNumberOfCores", 2)
    assert client.debugger.core_state() is CoreState.RUNNING
    assert client.debugger.core_states() == [CoreState.RUNNING, CoreState.RUNNING]


def test_eval_builds_a_context_ref(session):
    client, rpc = session
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


def test_registers(session, monkeypatch):
    client, rpc = session
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
    # No limit: the whole group.
    assert len(client.debugger.registers().registers) == 3


# -- errors keep what callers branch on ------------------------------------------


def test_lifecycle_failures_keep_the_backend_error(fake):
    client, rpc = fake
    license = cspy_exception(code=8, method="DkLoadModule", message="Failed")
    rpc.on("debugger", "resolveLaunchConfiguration", {}).on("debugger", "configureSession", license)
    with pytest.raises(BackendError) as exc:
        client.debugger.configure(LAUNCH)
    assert "Local lifecycle state was reset" in str(exc.value)
    assert client.classify_error(exc.value)["code"] == "CSPY_KDCLICENSEVIOLATION"

    rpc.on("debugger", "configureSession").on("debugger", "startSMPSession", license)
    client.debugger.configure(LAUNCH)
    with pytest.raises(BackendError, match="remains configured"):
        client.debugger.start()


# -- session guard ----------------------------------------------------------------


def test_session_calls_need_a_session(fake):
    client, rpc = fake
    rpc.on("debugger", "go").on("breakpoints", "getBreakpoints", []).on("memory", "readMemory", b"")
    for call in (client.debugger.go, client.breakpoints.all, lambda: client.memory.read(0, 4)):
        with pytest.raises(SessionError, match=r"client\.\w+\.\w+\(\) requires an active debug session"):
            call()
    with pytest.raises(SessionError, match="attach"):
        client.debugger.call("getCoreState", 0)
    assert rpc.calls == []
    # Pre-session methods and the diagnostic status do not need one.
    rpc.on("debugger", "getVersionString", "9.5").on("debugger", "isOnline", False)
    assert client.debugger.call("getVersionString") == "9.5"
    assert client.debugger.status()["started"] is False


def test_attach_adopts_a_running_session(fake):
    client, rpc = fake
    rpc.on("debugger", "isOnline", False)
    with pytest.raises(SessionError, match="offline"):
        client.debugger.attach()
    rpc.on("debugger", "isOnline", True).on("debugger", "go")
    client.debugger.attach()
    client.debugger.go()
    assert client.debugger.state == {"configured": True, "started": True}


def test_a_replaced_backend_ends_the_session(session):
    client, rpc = session
    client.backend.debugger_generation += 1  # CSpyServer2 was restarted
    with pytest.raises(SessionError, match="the debug session is gone"):
        client.debugger.check_backend("getCoreState")
    assert client.debugger.state == {"configured": False, "started": False}
    client.debugger.check_backend("configureSession")  # starting over is fine


def test_session_raises_when_stop_on_symbol_is_not_reached(fake, monkeypatch):
    client, rpc = fake
    for method in ("isOnline", "resolveLaunchConfiguration", "configureSession", "startSMPSession", "stopSession"):
        rpc.on("debugger", method, False if method == "isOnline" else {})
    rpc.on("debugger", "runToULE", cspy_exception(code=6, method="DkRunTo", message="no such symbol"))
    with pytest.raises(SessionError, match="did not stop at stopOnSymbol 'main'"):
        with client.debugger.session({**LAUNCH, "stopOnSymbol": "main"}):
            pytest.fail("the body must not run")
    assert rpc.methods()[-1] == "stopSession"
    assert client.debugger.last_start.ran_to_symbol is False

    # Opting out gives the session anyway.
    with client.debugger.session({**LAUNCH, "stopOnSymbol": "main"}, require_stop_on_symbol=False) as dbg:
        assert dbg.last_start.stop_on_symbol_error
        assert dbg.client is client


def test_call_stop_session_recovers_an_already_stopped_target(session):
    client, rpc = session
    rpc.on("debugger", "stopSession", cspy_exception())
    rpc.on("debugger", "isOnline", True).on("debugger", "getNumberOfCores", 1).on("debugger", "getCoreState", 0)
    result = client.debugger.call("stopSession")
    assert isinstance(result, StopResult) and result.stop_idempotent_recovered
    assert result.stop_idempotent_details["probe"]["already_stopped"] is True
    assert client.debugger.state == {"configured": False, "started": False}


def test_eval_int(session):
    client, rpc = session
    values = {"&g": "536'870'996", "c": "65", "n": "-5", "f": "1.5", "a": "<array>"}
    rpc.on(
        "debugger",
        "evalExpression",
        lambda ref, expr, sub, fmt, prefix: rpc.struct("debugger", "evalExpression", value=values[expr]),
    )
    assert [client.debugger.eval_int(e) for e in ("&g", "c", "n")] == [536870996, 65, -5]
    assert rpc.args("debugger", "evalExpression")[0][3] == "Dec"
    for expr in ("f", "a"):
        with pytest.raises(CSpyError, match="did not evaluate to an integer"):
            client.debugger.eval_int(expr)
