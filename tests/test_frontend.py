"""The headless ``frontend`` service: answers, rules, registration, error context."""

from __future__ import annotations

from pathlib import Path

import pytest
from thriftpy2.rpc import make_client
from thriftpy2.thrift import TApplicationException

from iar_cspy import BackendError, CSpyError
from iar_cspy.errors import classify, with_context
from iar_cspy.events import EVENTHANDLER_SERVICE
from iar_cspy.frontend import FRONTEND_IDL, FRONTEND_SERVICE
from iar_cspy.testing import cspy_exception


@pytest.fixture()
def backend_side(fake):
    """A thrift client to the hosted frontend, as the backend would hold."""
    client, _ = fake
    service = client.idl.service(FRONTEND_IDL, "Frontend")
    host, port = client.frontend.server.ensure(service)
    conn = make_client(service, host, port, timeout=2000)
    yield conn, client.idl.module(FRONTEND_IDL)
    conn.close()
    client.frontend.server.stop()


def test_message_boxes_decline_by_default(fake, backend_side):
    client, _ = fake
    conn, fe = backend_side
    conn.isAlive()
    assert conn.messageBox("Erase flash?", "Flash", fe.MsgIcon.kMsgIconQuestion, fe.MsgKind.kMsgYesNo, "") == (
        fe.MsgResult.kMsgResNo
    )
    assert conn.messageBox("x", "", 0, fe.MsgKind.kMsgOkCancel, "") == fe.MsgResult.kMsgResCancel
    assert conn.messageBox("Done", "", 0, fe.MsgKind.kMsgOk, "") == fe.MsgResult.kMsgResOk
    first = client.frontend.prompts[0]
    assert (first.kind, first.title, first.message, first.answer, first.answered_by) == (
        "message_box",
        "Flash",
        "Erase flash?",
        "no",
        "default",
    )
    assert first.details["buttons"] == ["yes", "no"] and first.details["icon"] == "kMsgIconQuestion"


def test_dialogs_are_cancelled_and_aliases_take_the_suggestion(fake, backend_side):
    client, _ = fake
    conn, fe = backend_side
    assert conn.openFileDialog("Locate main.c", "/src", "*.c", False, True) == []
    assert conn.openSaveDialog("Save trace", "trace.txt", "txt", "/tmp", "") == []
    assert conn.openDirectoryDialog("Pick", True, "/") == []
    filters = [fe.FileDialogFilter(displayName="C", filtering=["*.c"])]
    assert conn.openIHostFileDialog("Save", fe.FileDialogType.kSaveAs, 0, filters, [], "/", "a.c") == []
    assert conn.openElementSelectionDialog("Core", "Pick a core", ["a", "b"]) == -1
    assert conn.openMultipleElementSelectionDialog("Cores", "", ["a", "b"]) == []
    assert conn.resolveAliasForFile("C:/build/main.c", "/home/me/main.c") == "/home/me/main.c"
    assert conn.resolveAliasForFile("C:/build/util.c", "") == "C:/build/util.c"  # no suggestion: keep it
    result = conn.invokeDialog("dlg", "Settings", fe.shared.PropertyTreeItem(key="k", value="v", children=[]))
    assert result.type == fe.GenericDialogReturnType.kCancel and result.items.value == "v"
    kinds = [p.kind for p in client.frontend.prompts]
    assert kinds == [
        "file_dialog",
        "save_dialog",
        "directory_dialog",
        "save_dialog",
        "selection",
        "selection",
        "alias",
        "alias",
        "dialog",
    ]
    assert client.frontend.prompts[-1].to_dict()["details"]["entries"]["key"] == "k"  # JSON-shaped
    dialog = client.frontend.prompts[0]
    assert (dialog.title, dialog.message) == ("Locate main.c", "")
    assert dialog.summary() == "file_dialog: 'Locate main.c' -> [] (default)"


def test_rules_and_handler_override_the_defaults(fake, backend_side):
    client, _ = fake
    conn, fe = backend_side
    client.frontend.answer(r"Erase", "yes")
    remove = client.frontend.answer(r"Locate", ["/src/main.c"], kind="file_dialog")
    client.frontend.answer(r"core", "b", kind="selection")
    assert conn.messageBox("Erase flash?", "", 0, fe.MsgKind.kMsgYesNo, "") == fe.MsgResult.kMsgResYes
    assert conn.openFileDialog("Locate main.c", "", "", False, True) == ["/src/main.c"]
    assert conn.openElementSelectionDialog("", "Pick a core", ["a", "b"]) == 1
    remove()
    assert conn.openFileDialog("Locate main.c", "", "", False, True) == []
    assert [p.answered_by for p in client.frontend.prompts] == ["rule", "rule", "rule", "default"]

    # The handler comes first; None falls through to the rules.
    client.frontend.handler = lambda p: "/mapped.c" if p.kind == "alias" else None
    assert conn.resolveAliasForFile("a.c", "b.c") == "/mapped.c"
    assert conn.messageBox("Erase flash?", "", 0, fe.MsgKind.kMsgYesNo, "") == fe.MsgResult.kMsgResYes

    # An answer the box has no button for falls back to the default.
    client.frontend.handler = lambda p: "yes"
    assert conn.messageBox("Hm", "", 0, fe.MsgKind.kMsgOkCancel, "") == fe.MsgResult.kMsgResCancel


def test_answer_validates_its_arguments(fake):
    client, _ = fake
    with pytest.raises(ValueError, match="kind"):
        client.frontend.answer("x", "ok", kind="popup")
    with pytest.raises(ValueError, match="message box"):
        client.frontend.answer("x", "maybe")


def test_notices_and_progress(fake, backend_side):
    client, _ = fake
    conn, fe = backend_side
    conn.messageBoxAsync("Flash loader failed", "Error", fe.MsgIcon.kMsgIconStop, "")
    conn.editSourceLocation(fe.shared.SourceLocation(filename="/src/main.c", line=12, col=1, locations=[]), True)
    bar = conn.createProgressBar("Downloading", "Flash", 0, 100, True, False)
    # The return value is "cancelled" (frontend.thrift): never.
    assert conn.updateProgressBarValue(bar, 40) is False and client.frontend.progress[bar]["value"] == 40
    assert conn.updateProgressBarMessage(bar, "Verifying") is False
    conn.closeProgressBar(bar)
    assert conn.updateProgressBarValue(bar, 50) is False and client.frontend.progress == {}
    assert conn.handlesAliasStorage() is False and conn.loadAliases() == {}
    # No theme: a failed call makes the backend stop asking (an empty one is re-requested).
    with pytest.raises(TApplicationException):
        conn.getActiveTheme()
    conn.isAlive()  # the connection is still usable
    version = conn.GetServiceVersion()
    assert (version.major, version.minor) == (fe.FRONTEND_VERSION.major, fe.FRONTEND_VERSION.minor)
    assert conn.getCapabilities().supportsEditorHighlight is False
    notices = client.frontend.recent(clear=True)
    assert [(p.kind, p.answered_by) for p in notices] == [("message", None), ("edit_source", None)]
    assert notices[1].details["line"] == 12 and client.frontend.prompts == []


def test_registered_unless_another_frontend_is(fake):
    client, _ = fake
    first = client.frontend.ensure_registered()
    assert first["status"] == "registered" and client.frontend.hosting
    assert client.registry.registered[-1] == (FRONTEND_SERVICE, first["host"], first["port"])
    client.registry.entries.append({"name": FRONTEND_SERVICE, "host": first["host"], "port": first["port"]})
    assert client.frontend.ensure_registered()["status"] == "already-registered" and client.frontend.hosting
    client.frontend.server.stop()

    # A reachable foreign frontend (an IDE's) is never shadowed.
    other = client.events.server
    host, port = other.ensure(client.idl.service(None, "DebugEventListener"))
    client.registry.set([EVENTHANDLER_SERVICE])
    client.registry.entries.append({"name": FRONTEND_SERVICE, "host": host, "port": port})
    assert client.frontend.ensure_registered()["status"] == "already-registered"
    assert client.frontend.hosting is False and len(client.registry.registered) == 1
    other.stop()


def test_failing_call_carries_the_prompts_it_raised(fake):
    client, rpc = fake

    def asks_then_fails():
        client.frontend.service.messageBox("Overwrite flash?", "Flash", 1, 2, "")
        client.frontend.service.showView("Memory")  # notices that are not messages stay out
        raise cspy_exception(message="Timeout while erasing")

    rpc.on("debugger", "startSMPSession", asks_then_fails)
    with pytest.raises(BackendError) as exc:
        client.call("debugger", "startSMPSession")
    assert [p.kind for p in exc.value.prompts] == ["message_box"]
    assert "message_box: 'Flash / Overwrite flash?' -> 'no' (default)" in str(exc.value)

    record = classify(exc.value)
    assert record["code"] == "CSPY_KDCERROR"  # not misread from the prompt text
    assert record["frontend_prompts"][0]["answer"] == "no"
    wrapped = with_context(exc.value, f"Starting failed: {exc.value}")
    assert wrapped.prompts == exc.value.prompts


def test_traced_annotates_converted_errors(fake):
    client, _ = fake

    def call():
        client.frontend.service.openFileDialog("Locate crt0.s", "", "", False, True)
        raise ConnectionResetError("reset")

    with pytest.raises(CSpyError) as exc:
        client.traced("contextmanager", "getStack", call)
    assert exc.value.prompts[0].kind == "file_dialog" and "Locate crt0.s" in str(exc.value)

    with pytest.raises(CSpyError) as clean:
        client.traced("debugger", "go", lambda: (_ for _ in ()).throw(ConnectionResetError("reset")))
    assert clean.value.prompts == [] and "answered headless" not in str(clean.value)


def test_path_answers_and_empty_alias_answers(fake, backend_side):
    client, _ = fake
    conn, _ = backend_side
    client.frontend.answer(r"Select", Path("/src/main.c"), kind="file_dialog")
    client.frontend.answer(r"Pick", [Path("/a"), "/b"], kind="directory_dialog")
    assert conn.openFileDialog("Select main.c", "", "", False, True) == ["/src/main.c"]
    assert conn.openDirectoryDialog("Pick", True, "/") == ["/a", "/b"]
    client.frontend.answer(r".", Path("/mapped/util.c"), kind="alias")
    assert conn.resolveAliasForFile("C:/build/util.c", "") == "/mapped/util.c"
    # An empty answer would turn alias handling off in the backend: keep the file.
    client.frontend.answer(r".", "", kind="alias")
    assert conn.resolveAliasForFile("C:/build/util.c", "/x/util.c") == "/x/util.c"
    assert client.frontend.prompts[-1].answer == "/x/util.c"


def test_progress_bars_do_not_outlive_the_session(fake, backend_side):
    client, _ = fake
    conn, _ = backend_side
    conn.createProgressBar("Downloading", "Flash", 0, 100, True, False)
    assert client.frontend.progress
    client.clear_session_caches()
    assert client.frontend.progress == {}


def test_frontend_can_be_left_to_the_ide(fake):
    client, _ = fake
    object.__setattr__(client.config, "auto_frontend", False)
    assert client.frontend.ensure_registered() == {"ok": False, "reason": "auto-frontend-disabled"}
    assert not client.registry.registered
