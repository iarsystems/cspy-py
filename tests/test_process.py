"""Process supervision and managed-backend startup, with stand-in programs.

The stand-ins print the registry banner CSpyServer2 and IarServiceLauncher
print, and listen on that port, which is all the supervisor looks at.
"""

from __future__ import annotations

import os
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

from iar_cspy import Backend, Config
from iar_cspy._process import ManagedProcess, is_reachable

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in programs are POSIX scripts")

PROGRAM = """\
#!{python}
import socket, sys, time
args = sys.argv[1:]
if {join} and "-registry" in args:
    # Joining another program's registry: report it, serve nothing there.
    print("Service registry running on local socket on port: %s" % args[args.index("-registry") + 1], flush=True)
    time.sleep(3600)
port = 0
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
sock.listen(16)
print("Service registry running on local socket on port: %d" % sock.getsockname()[1], flush=True)
{body}
"""

SERVE = """\
while True:
    conn, _ = sock.accept()
    conn.close()
"""


def program(tmp_path: Path, name: str, *, join: bool = True, body: str = SERVE) -> Path:
    path = tmp_path / name
    path.write_text(PROGRAM.format(python=sys.executable, join=join, body=textwrap.indent(body, "")))
    path.chmod(0o755)
    return path


def test_start_publishes_the_registry_port(tmp_path):
    proc = ManagedProcess("CSpyServer2", start_timeout_ms=10000)
    try:
        port = proc.ensure(program(tmp_path, "CSpyServer2"), ["-standalone"])
        assert proc.healthy() and is_reachable("127.0.0.1", port)
        work_dir = proc.work_dir
        assert work_dir is not None and work_dir.is_dir()
        # Healthy: ensure() keeps the process.
        pid = proc.process.pid
        assert proc.ensure(program(tmp_path, "CSpyServer2"), []) == port
        assert proc.process.pid == pid
        assert proc.status()["running"] is True
    finally:
        proc.shutdown()
    assert proc.process is None and not work_dir.exists()
    assert "Most recent CSpyServer2 process snapshot" in proc.diagnostics()


def test_a_dead_process_is_replaced(tmp_path):
    exe = program(tmp_path, "CSpyServer2")
    proc = ManagedProcess("CSpyServer2", start_timeout_ms=10000)
    try:
        proc.ensure(exe, [])
        first = proc.process
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=5)
        assert not proc.healthy()
        assert "appears to have exited" in proc.diagnostics()
        proc.ensure(exe, [])
        assert proc.process is not first and proc.healthy()
    finally:
        proc.shutdown()


def test_no_restart_when_disabled(tmp_path):
    exe = program(tmp_path, "CSpyServer2", body="time.sleep(60)\n")  # banner, then stops accepting
    proc = ManagedProcess("CSpyServer2", start_timeout_ms=10000, restart_on_failure=False)
    try:
        proc.ensure(exe, [])
        proc.registry_port = 1  # now unreachable, but still running
        with pytest.raises(RuntimeError, match="restart is disabled"):
            proc.ensure(exe, [])
    finally:
        proc.shutdown()


def test_a_program_that_exits_early_is_reported(tmp_path):
    exe = tmp_path / "CSpyServer2"
    exe.write_text(f"#!{sys.executable}\nprint('license checkout failed')\nraise SystemExit(3)\n")
    exe.chmod(0o755)
    proc = ManagedProcess("CSpyServer2", start_timeout_ms=5000, restart_on_failure=False)
    with pytest.raises(RuntimeError, match="exited before publishing a registry port") as exc:
        proc.ensure(exe, [])
    assert "Exit code: 3" in str(exc.value) and "license checkout failed" in str(exc.value)
    with pytest.raises(RuntimeError, match="executable not found"):
        proc.ensure(tmp_path / "missing", [])


def managed(tmp_path, cspy, launcher=None) -> Backend:
    return Backend(
        Config(
            mode="managed",
            cspy_executable=cspy,
            launcher_executable=launcher,
            cspy_start_timeout_ms=10000,
            launcher_start_timeout_ms=10000,
        )
    )


def test_cspyserver2_joins_the_launcher_registry(tmp_path, monkeypatch):
    backend = managed(tmp_path, program(tmp_path, "CSpyServer2"), program(tmp_path, "IarServiceLauncher"))
    pruned = []
    monkeypatch.setattr(backend, "_prune_stale_registrations", pruned.append)
    try:
        host, port = backend.registry_endpoint()
        assert backend.debugger_process.last_cmd[-2:] == ["-registry", str(port)]
        assert backend.launcher_process.registry_port == port
        assert pruned == [port]  # before CSpyServer2 joined
        assert (backend.launcher_generation, backend.debugger_generation) == (1, 1)

        # Nothing to do while both are healthy.
        assert backend.registry_endpoint() == (host, port)
        assert (backend.launcher_generation, backend.debugger_generation) == (1, 1)

        # A replaced CSpyServer2 counts as a new generation; the launcher stays.
        backend.stop_debugger()
        backend.registry_endpoint()
        assert (backend.launcher_generation, backend.debugger_generation) == (1, 2)
    finally:
        backend.shutdown()
    assert not backend.debugger_process.running and not backend.launcher_process.running


def test_a_cspyserver2_that_does_not_join_is_rejected(tmp_path, monkeypatch):
    backend = managed(
        tmp_path, program(tmp_path, "CSpyServer2", join=False), program(tmp_path, "IarServiceLauncher")
    )
    monkeypatch.setattr(backend, "_prune_stale_registrations", lambda port: None)
    try:
        with pytest.raises(Exception, match="did not attach to the IarServiceLauncher registry"):
            backend.registry_endpoint()
        assert not backend.debugger_process.running
    finally:
        backend.shutdown()


def test_without_a_launcher_cspyserver2_owns_the_registry(tmp_path):
    backend = managed(tmp_path, program(tmp_path, "CSpyServer2"))
    try:
        host, port = backend.registry_endpoint()
        assert backend.debugger_process.last_cmd[1:] == ["-standalone", "-sockets"]
        assert backend.current_registry_endpoint() == (host, port)
    finally:
        backend.shutdown()


def test_shutdown_asks_the_program_to_exit_before_terminating(tmp_path):
    exe = program(tmp_path, "CSpyServer2")
    asked = []

    def request_exit():
        # Stands in for Debugger.exit(): the program goes away by itself.
        asked.append(True)
        proc.process.send_signal(signal.SIGUSR1)
        return True

    proc = ManagedProcess("CSpyServer2", start_timeout_ms=10000, request_exit=request_exit, exit_timeout_s=5)
    proc.ensure(exe, [])
    first = proc.process
    proc.shutdown()
    assert asked == [True]
    assert first.returncode == -signal.SIGUSR1  # not terminated


def test_shutdown_terminates_a_program_that_does_not_exit(tmp_path):
    proc = ManagedProcess("CSpyServer2", start_timeout_ms=10000, request_exit=lambda: True, exit_timeout_s=0.2)
    proc.ensure(program(tmp_path, "CSpyServer2"), [])
    first = proc.process
    proc.shutdown()
    assert first.returncode == -signal.SIGTERM


def test_shutdown_terminates_when_the_exit_request_fails(tmp_path):
    def request_exit():
        raise ConnectionRefusedError

    proc = ManagedProcess("CSpyServer2", start_timeout_ms=10000, request_exit=request_exit, exit_timeout_s=5)
    proc.ensure(program(tmp_path, "CSpyServer2"), [])
    first = proc.process
    started = time.monotonic()
    proc.shutdown()
    assert first.returncode == -signal.SIGTERM
    assert time.monotonic() - started < 4  # no wait for an exit that was never asked for


def test_a_multicore_session_restarts_cspyserver2_with_the_core_count(tmp_path, monkeypatch):
    backend = managed(tmp_path, program(tmp_path, "CSpyServer2"), program(tmp_path, "IarServiceLauncher"))
    monkeypatch.setattr(backend, "_prune_stale_registrations", lambda port: None)
    try:
        backend.registry_endpoint()
        assert not any(arg.startswith("--multicore_nr_of_cores") for arg in backend.debugger_process.last_cmd)

        assert backend.use_core_count(2) is True  # started for 1 core: replaced
        assert not backend.debugger_process.running
        backend.registry_endpoint()
        assert backend.debugger_process.last_cmd[-1] == "--multicore_nr_of_cores=2"
        assert backend.use_core_count(2) is False
        assert backend.debugger_generation == 2
    finally:
        backend.shutdown()


def test_cspy_args_carry_the_core_count():
    backend = Backend(Config(mode="managed", cspy_args=("-standalone", "-sockets")))
    assert backend.use_core_count(3) is False  # nothing running yet
    assert backend.cspy_args(None) == ["-standalone", "-sockets", "--multicore_nr_of_cores=3"]
    assert backend.cspy_args(4711) == ["-sockets", "-registry", "4711", "--multicore_nr_of_cores=3"]
    given = Backend(Config(mode="managed", cspy_args=("-standalone", "--multicore_nr_of_cores=2")))
    given.use_core_count(3)
    assert given.cspy_args(None) == ["-standalone", "--multicore_nr_of_cores=2"]  # the user's own choice
    assert Backend(Config(mode="standalone", registry_port=1)).use_core_count(2) is False


def test_prune_stale_registrations_removes_unreachable_endpoints(tmp_path, monkeypatch):
    import iar_cspy.backend as backend_module

    class Location:
        def __init__(self, port, transport=0):
            self.host, self.port, self.transport = "127.0.0.1", port, transport

    live = program(tmp_path, "Live")
    proc = ManagedProcess("Live", start_timeout_ms=10000)
    live_port = proc.ensure(live, [])

    class Registry:
        deregistered: list[str] = []

        def getServices(self):
            return {"debugger": Location(1), "eventhandler": Location(live_port), "pipe": Location(1, transport=1)}

        def deregisterService(self, name):
            self.deregistered.append(name)

        def close(self):
            pass

    monkeypatch.setattr(backend_module, "make_client", lambda *a, **kw: Registry())
    try:
        Backend(Config(mode="managed"))._prune_stale_registrations(4711)
    finally:
        proc.shutdown()
    # Only the dead socket endpoint; other transports are not probed.
    assert Registry.deregistered == ["debugger"]


def test_a_crashed_cspyserver2_ends_the_session(tmp_path, monkeypatch):
    """A session does not survive into a replacement CSpyServer2."""
    from iar_cspy import Client, SessionError

    backend = managed(tmp_path, program(tmp_path, "CSpyServer2"))
    client = Client(backend)
    monkeypatch.setattr(client, "invoke_at", lambda *a, **kw: 0)
    monkeypatch.setattr(client.registry, "resolve", lambda name, registry=None: ("127.0.0.1", 1))
    try:
        backend.registry_endpoint()
        client.debugger.set_state(configured=True, started=True)
        client.call("debugger", "getCoreState", 0)

        first = backend.debugger_process.process
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=5)
        time.sleep(0.1)
        with pytest.raises(SessionError, match="the debug session is gone"):
            client.call("debugger", "getCoreState", 0)
        assert client.debugger.state == {"configured": False, "started": False}
        # Starting over is allowed.
        client.call("debugger", "configureSession", {})
    finally:
        backend.shutdown()
