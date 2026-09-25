"""The services this process hosts for the backend, and their registration."""

from __future__ import annotations

from thriftpy2.rpc import make_client

from iar_cspy import Config
from iar_cspy._process import is_reachable
from iar_cspy.events import EVENTHANDLER_SERVICE
from iar_cspy.terminal import LIBSUPPORT_SERVICE


def test_callback_server_serves_and_restarts(fake):
    client, _ = fake
    server = client.terminal.server
    service = client.idl.service("libsupport.thrift", "LibSupportService2")
    host, port = server.ensure(service)
    assert is_reachable(host, port)
    assert server.ensure(service) == (host, port)  # still up: same endpoint

    backend_side = make_client(service, host, port, timeout=2000)
    try:
        backend_side.printOutput("hello\n")
        backend_side.exit(3)
    finally:
        backend_side.close()
    assert client.terminal.text == "hello\n" and client.terminal.exit_code == 3

    server.stop()
    assert not server.started
    assert server.ensure(service)[1] > 0 and server.started
    server.stop()


def test_eventhandler_is_registered_once_and_again_after_a_restart(fake):
    client, _ = fake
    events = client.events
    first = events.ensure_registered()
    assert first["status"] == "registered" and events.receiving
    entry = client.registry.registered[-1]
    assert entry == (EVENTHANDLER_SERVICE, first["host"], first["port"])

    # Our own registration is still there and reachable: left alone.
    client.registry.entries.append({"name": EVENTHANDLER_SERVICE, "host": first["host"], "port": first["port"]})
    again = events.ensure_registered()
    assert again["status"] == "already-registered" and events.receiving
    assert len(client.registry.registered) == 1

    # A fresh backend (e.g. a restarted CSpyServer2 with its own registry)
    # knows nothing of it: registered anew.
    client.registry.set([])
    assert events.ensure_registered()["status"] == "registered"
    assert len(client.registry.registered) == 2
    events.server.stop()


def test_a_foreign_eventhandler_is_kept_but_not_received_from(fake):
    client, _ = fake
    other = client.listwindows.server  # any reachable server stands in for an IDE's handler
    host, port = other.ensure(client.idl.service("listwindow.thrift", "ListWindowFrontend"))
    client.registry.entries.append({"name": EVENTHANDLER_SERVICE, "host": host, "port": port})
    result = client.events.ensure_registered()
    assert result["status"] == "already-registered"
    # Events go to the other frontend, so waits fall back to polling.
    assert client.events.receiving is False
    other.stop()


def test_events_reach_waiters_through_the_real_service(fake):
    client, _ = fake
    host, port = client.events.server.ensure(client.idl.service(None, "DebugEventListener"))
    mark = client.events.mark()
    backend_side = make_client(client.idl.service(None, "DebugEventListener"), host, port, timeout=2000)
    shared = client.idl.module(None)
    try:
        for note in (shared.DkNotifyConstant.kDkTargetStarted, shared.DkNotifyConstant.kDkTargetStopped):
            backend_side.postDebugEvent(shared.DebugEvent(note=note, descr="", params=[]))
    finally:
        backend_side.close()
    assert client.events.wait_for_stop(after=mark, timeout=2) is not None
    client.events.server.stop()


def test_libsupport_is_registered_on_every_call(fake):
    client, _ = fake
    assert client.terminal.ensure_registered()["status"] == "registered"
    assert client.terminal.ensure_registered()["status"] == "registered"
    assert [name for name, _, _ in client.registry.registered] == [LIBSUPPORT_SERVICE, LIBSUPPORT_SERVICE]
    client.terminal.server.stop()


def test_registration_needs_a_registry():
    from iar_cspy.testing import fake_client

    client, _ = fake_client(Config(mode="standalone"))
    assert client.events.ensure_registered() == {"ok": False, "reason": "no-registry-port"}
    assert client.terminal.ensure_registered() == {"ok": False, "reason": "no-registry-port"}
    disabled, _ = fake_client(Config(mode="standalone", registry_port=1, auto_eventhandler=False))
    assert disabled.events.ensure_registered()["reason"] == "auto-eventhandler-disabled"
