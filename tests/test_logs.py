"""The IDE's logs, as the log forwarder posts them to client.logs."""

from __future__ import annotations

import threading

import pytest
from thriftpy2.rpc import make_client

from iar_cspy import Config, LogEntry, LogSeverity
from iar_cspy.logs import LOG_FORWARDER_KEY, LOGSERVICE_ID
from iar_cspy.testing import fake_client, log_entry


def post(client, **fields) -> None:
    client.logs.server.handler.postLogEntry(log_entry(**fields))


def texts(entries: list[LogEntry]) -> list[str]:
    return [entry.text for entry in entries]


def test_an_entry_is_stored_with_readable_fields(fake):
    client, _ = fake
    post(
        client,
        category="Log_Build",
        text="main.c(12): undefined symbol",
        severity=LogSeverity.ERROR,
        sender="iccarm",
        path="/src/main.c",
        srcPos=(12, 5),
        timestamp=1_700_000_000,
        entryId=7,
    )
    (entry,) = client.logs.entries("Log_Build")
    assert (entry.category, entry.text, entry.severity, entry.sender) == (
        "Log_Build",
        "main.c(12): undefined symbol",
        LogSeverity.ERROR,
        "iccarm",
    )
    assert (entry.path, entry.line, entry.column) == ("/src/main.c", 12, 5)
    assert (entry.end_line, entry.end_column) == (None, None)  # not valid: no position
    assert entry.timestamp is not None and entry.timestamp.timestamp() == 1_700_000_000
    assert (entry.entry_id, entry.is_sub_entry, entry.session) == (7, False, 0)

    plain = entry.to_dict()
    assert plain["severity"] == "ERROR" and plain["timestamp"].startswith("2023-11-14")


def test_an_entry_without_a_path_or_time_has_none(fake):
    client, _ = fake
    post(client, text="Done.")
    (entry,) = client.logs.entries()
    assert (entry.path, entry.line, entry.timestamp) == (None, None, None)


def test_entries_of_all_categories_come_in_arrival_order(fake):
    client, _ = fake
    post(client, category="Log_Build", text="b1")
    post(client, category="Log_Debug", text="d1")
    post(client, category="Log_Build", text="b2")
    assert texts(client.logs.entries()) == ["b1", "d1", "b2"]
    assert texts(client.logs.entries("Log_Build")) == ["b1", "b2"]
    assert client.logs.entries("Log_Find_in_Files") == []


def test_since_a_mark(fake):
    client, _ = fake
    post(client, text="before")
    mark = client.logs.mark()
    post(client, text="after")
    assert texts(client.logs.entries(since=mark)) == ["after"]


def test_min_severity_takes_the_enum_its_name_or_value(fake):
    client, _ = fake
    for severity in (LogSeverity.INFO, LogSeverity.WARNING, LogSeverity.ERROR):
        post(client, text=severity.name, severity=severity)
    assert texts(client.logs.entries(min_severity=LogSeverity.WARNING)) == ["WARNING", "ERROR"]
    assert texts(client.logs.entries(min_severity="error")) == ["ERROR"]
    assert texts(client.logs.entries(min_severity=3)) == ["WARNING", "ERROR"]
    with pytest.raises(ValueError, match="Unknown log severity"):
        client.logs.entries(min_severity="loud")


def test_a_new_session_numbers_entries_and_current_picks_the_latest(fake):
    client, _ = fake
    handler = client.logs.server.handler
    handler.startSession("Log_Build")
    post(client, text="first build")
    handler.startSession("Log_Build")
    post(client, text="second build")
    post(client, category="Log_Debug", text="still session 0")

    assert [e.session for e in client.logs.entries()] == [1, 2, 0]
    assert texts(client.logs.entries("Log_Build", session="current")) == ["second build"]
    assert texts(client.logs.entries("Log_Build", session=1)) == ["first build"]
    # "current" is per category.
    assert texts(client.logs.entries(session="current")) == ["second build", "still session 0"]


def test_problems_are_warnings_and_worse_of_the_current_session(fake):
    client, _ = fake
    handler = client.logs.server.handler
    handler.startSession("Log_Build")
    post(client, text="old error", severity=LogSeverity.ERROR)
    handler.startSession("Log_Build")
    post(client, text="note", severity=LogSeverity.INFO)
    post(client, text="new warning", severity=LogSeverity.WARNING)
    assert texts(client.logs.problems("Log_Build")) == ["new warning"]
    assert texts(client.logs.problems("Log_Build", session=None)) == ["old error", "new warning"]


def test_sub_entries_stay_flat_and_text_indents_them(fake):
    client, _ = fake
    post(client, text="Error[Pe020]: identifier undefined", severity=LogSeverity.ERROR)
    post(client, text="detail line\n", isSubEntry=True)
    post(client, text="Done. 1 error", severity=LogSeverity.INFO)

    assert [e.is_sub_entry for e in client.logs.entries("Log_Build")] == [False, True, False]
    assert client.logs.text("Log_Build") == (
        "Error: Error[Pe020]: identifier undefined\n  detail line\nDone. 1 error"
    )


def test_text_of_all_categories_names_each_line_s_category(fake):
    client, _ = fake
    post(client, category="Log_Build", text="b", severity=LogSeverity.WARNING)
    post(client, category="Log_Debug", text="d")
    assert client.logs.text() == "[Log_Build] Warning: b\n[Log_Debug] d"


def test_categories_follow_add_and_remove_and_add_is_idempotent(fake):
    client, _ = fake
    handler = client.logs.server.handler
    for name in ("Log_Build", "Log_Debug", "Log_Build"):  # a late client is primed, then told again
        handler.addCategory(name)
    assert client.logs.categories() == ["Log_Build", "Log_Debug"]

    post(client, category="Log_Debug", text="kept")
    handler.removeCategory("Log_Debug")
    assert client.logs.categories() == ["Log_Build"]
    assert texts(client.logs.entries("Log_Debug")) == ["kept"]


def test_clear_forgets_entries_but_keeps_categories_and_sessions(fake):
    client, _ = fake
    handler = client.logs.server.handler
    handler.addCategory("Log_Build")
    handler.startSession("Log_Build")
    post(client, category="Log_Build", text="b")
    post(client, category="Log_Debug", text="d")

    client.logs.clear("Log_Build")
    assert texts(client.logs.entries()) == ["d"]
    client.logs.clear()
    assert client.logs.entries() == []
    assert client.logs.categories() == ["Log_Build"]
    post(client, category="Log_Build", text="again")
    assert client.logs.entries("Log_Build")[0].session == 1


def test_each_category_keeps_its_own_maxlen(fake):
    client, _ = fake
    small = type(client.logs)(client, maxlen=2)
    for n in range(5):
        small._post(log_entry(category="Log_Debug", text=f"d{n}"))
    small._post(log_entry(category="Log_Build", text="b"))
    assert texts(small.entries()) == ["d3", "d4", "b"]
    small.server.stop()


def test_subscribe_and_wait_for(fake):
    client, _ = fake
    seen: list[str] = []
    unsubscribe = client.logs.subscribe(lambda entry: seen.append(entry.text))
    post(client, text="one")
    unsubscribe()
    post(client, text="two")
    assert seen == ["one"]

    mark = client.logs.mark()
    assert client.logs.wait_for(lambda e: True, after=mark, timeout=0.05) is None
    timer = threading.Timer(0.05, post, [client], {"text": "Build finished"})
    timer.start()
    found = client.logs.wait_for(lambda e: "finished" in e.text, after=mark, timeout=5)
    timer.join()
    assert found is not None and found.text == "Build finished"


def test_session_caches_do_not_clear_the_logs(fake):
    client, _ = fake
    post(client, text="kept")
    client.clear_session_caches()
    assert texts(client.logs.entries()) == ["kept"]


# -- registration ------------------------------------------------------------------


def test_registers_as_the_plain_logservice_id_when_it_is_free(fake):
    client, _ = fake
    logs = client.logs
    assert logs.service_name is None

    first = logs.ensure_registered()
    assert first["status"] == "registered" and "forwarder" not in first
    assert first["service_name"] == logs.service_name == LOGSERVICE_ID
    assert client.registry.registered[-1] == (LOGSERVICE_ID, first["host"], first["port"])
    assert logs.status()["registered"] and logs.status()["listening_since"]

    client.registry.entries.append({"name": LOGSERVICE_ID, "host": first["host"], "port": first["port"]})
    assert logs.ensure_registered()["status"] == "already-registered"
    assert len(client.registry.registered) == 1

    # A replaced backend knows nothing of it: registered anew.
    client.registry.set([])
    assert logs.ensure_registered()["status"] == "registered"
    assert len(client.registry.registered) == 2


def test_a_stale_registration_of_the_plain_id_is_taken_over(fake):
    client, _ = fake
    client.registry.set([LOGSERVICE_ID])  # port 1: nothing listens there
    assert client.logs.ensure_registered()["service_name"] == LOGSERVICE_ID


def test_a_live_listener_keeps_the_plain_id_and_this_one_takes_a_suffix(fake):
    client, _ = fake
    other = client.listwindows.server  # any reachable server stands in for an IDE's log viewer
    host, port = other.ensure(client.idl.service("listwindow.thrift", "ListWindowFrontend"))
    client.registry.entries.append({"name": LOGSERVICE_ID, "host": host, "port": port})

    result = client.logs.ensure_registered()
    assert result["service_name"] == client.logs.suffixed_name
    assert client.logs.suffixed_name.startswith(LOGSERVICE_ID + ".")
    assert client.logs.suffixed_name != fake_client()[0].logs.suffixed_name
    assert [name for name, _, _ in client.registry.registered] == [client.logs.suffixed_name]
    other.stop()


def test_auto_ensure_registers_once_per_registry(fake, monkeypatch):
    client, _ = fake
    client.logs.auto_ensure()
    client.logs.auto_ensure()
    assert len(client.registry.registered) == 1

    monkeypatch.setattr(client.backend, "registry_endpoint", lambda: ("127.0.0.1", 4712))
    client.logs.auto_ensure()
    assert len(client.registry.registered) == 2


def test_auto_ensure_never_raises(fake, monkeypatch):
    client, _ = fake

    def broken(*args, **kwargs):
        raise RuntimeError("registry gone")

    monkeypatch.setattr(client.registry, "lookup", broken)
    client.logs.auto_ensure()
    assert client.logs.status()["registered"] is False


def test_disabled_by_config():
    client, _ = fake_client(Config(mode="standalone", registry_uri="thrift://127.0.0.1:4711", auto_log_service=False))
    assert client.logs.ensure_registered() == {"ok": False, "reason": "auto-log-service-disabled"}
    client.logs.auto_ensure()
    assert client.registry.registered == []
    client.close()


def test_close_deregisters(fake, monkeypatch):
    client, _ = fake
    client.logs.ensure_registered()
    monkeypatch.setattr("iar_cspy.logs.is_reachable", lambda *a, **k: True)
    client.logs.close()
    assert client.registry.deregistered == [LOGSERVICE_ID]
    assert not client.logs.server.started
    client.logs.close()  # idempotent
    assert len(client.registry.deregistered) == 1


def test_entries_reach_the_store_through_the_real_service(fake):
    client, _ = fake
    service = client.idl.service("logservice.thrift", "LogService")
    host, port = client.logs.server.ensure(service)
    forwarder = make_client(service, host, port, timeout=2000)
    try:
        forwarder.isAlive()
        assert forwarder.GetServiceVersion().major == 1
        forwarder.addCategory("Log_Build")
        forwarder.startSession("Log_Build")
        forwarder.postLogEntry(log_entry(text="over the wire", srcPos=(3, 1)))
    finally:
        forwarder.close()
    (entry,) = client.logs.entries("Log_Build", session="current")
    assert (entry.text, entry.line) == ("over the wire", 3)


# -- the forwarder in a managed launcher ------------------------------------------------


@pytest.fixture()
def launcher(fake_stage, monkeypatch):
    def make(**stage):
        bin_dir = fake_stage(**stage)
        client, rpc = fake_client(
            Config(mode="standalone", registry_uri="thrift://127.0.0.1:4711", launcher_executable=bin_dir / "IarServiceLauncher")
        )
        monkeypatch.setattr(type(client.backend), "has_launcher", property(lambda self: True))
        rpc.on("servicemanager", "startServicesFromJsonManifest", None)
        return client, rpc, bin_dir

    return make


def test_a_managed_launcher_gets_the_forwarder_once(launcher):
    client, rpc, bin_dir = launcher(libraries=("ThriftLogForwarder",), manifests=("logforwarder.json",))
    result = client.logs.ensure_registered()
    assert result["forwarder"] == {"status": "started", "manifest": str(bin_dir / "logforwarder.json")}
    assert client.backend.loaded_manifests[LOG_FORWARDER_KEY] == str(bin_dir / "logforwarder.json")

    assert client.logs.ensure_registered()["forwarder"]["status"] == "loaded"
    assert len(rpc.args("servicemanager", "startServicesFromJsonManifest")) == 1


def test_a_forwarder_manifest_is_generated_when_only_the_library_ships(launcher):
    client, rpc, _ = launcher(libraries=("ThriftLogForwarder",))
    assert client.logs.ensure_registered()["forwarder"]["status"] == "started"
    (args,) = rpc.args("servicemanager", "startServicesFromJsonManifest")
    assert args[0].endswith("logforwarder.json")


def test_an_installation_without_the_forwarder_still_registers(launcher):
    client, rpc, _ = launcher(libraries=())
    result = client.logs.ensure_registered()
    assert result["status"] == "registered"
    assert result["forwarder"]["status"] == "error" and "ThriftLogForwarder" in result["forwarder"]["error"]
    assert rpc.args("servicemanager", "startServicesFromJsonManifest") == []
    assert client.logs.status()["forwarder"]["status"] == "error"


def test_an_ide_service_call_listens_to_the_logs_before_starting_the_service(monkeypatch):
    from iar_cspy import Client

    client = Client(Config(mode="standalone", registry_uri="thrift://127.0.0.1:4711"))
    order: list[str] = []
    monkeypatch.setattr(client.logs, "auto_ensure", lambda: order.append("logs"))

    def stop(key):
        order.append(key)
        raise RuntimeError("stop here")

    monkeypatch.setattr(client.ide_services, "auto_ensure", stop)
    with pytest.raises(RuntimeError, match="stop here"):
        client.call("projectmanager", "GetProjects")
    assert order == ["logs", "projectmanager"]
    client.close()
