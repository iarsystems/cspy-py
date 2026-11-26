"""C-RUN runtime-checking rules (client.crun_rules) and live messages (client.crun_messages)."""

from __future__ import annotations

import threading

import pytest

from iar_cspy import CRunBreakAction, CRunEvent, CRunMessage, CSpyError, SessionError
from iar_cspy._thrift import BUNDLED_IDL_DIR, load_thrift_module
from iar_cspy.testing import crun_message


def _void(*args):
    return None


def test_add_rule_for_a_message_anywhere(session):
    client, rpc = session
    rpc.on("crun", "addRuleOnMessage", _void)
    assert client.crun_rules.add_rule("AssertFailure") is None
    assert rpc.args("crun", "addRuleOnMessage") == [("AssertFailure",)]


def test_add_rule_for_a_message_in_a_file(session):
    client, rpc = session
    rpc.on("crun", "addRuleOnMessageFile", _void)
    client.crun_rules.add_rule("AssertFailure", file="main.c")
    assert rpc.args("crun", "addRuleOnMessageFile") == [("AssertFailure", "main.c")]


def test_add_rule_for_a_message_at_a_source_position(session):
    client, rpc = session
    rpc.on("crun", "addRuleOnMessageRange", _void)
    client.crun_rules.add_rule("AssertFailure", file="main.c", start_line=10, start_col=1)
    point = {"filename": "main.c", "line": 10, "col": 1}
    assert rpc.args("crun", "addRuleOnMessageRange") == [("AssertFailure", {"first": point, "last": point})]


def test_add_rule_for_a_message_in_a_source_range(session):
    client, rpc = session
    rpc.on("crun", "addRuleOnMessageRange", _void)
    client.crun_rules.add_rule("AssertFailure", file="main.c", start_line=10, start_col=1, end_line=12, end_col=5)
    [(_, range_)] = rpc.args("crun", "addRuleOnMessageRange")
    assert range_["first"] == {"filename": "main.c", "line": 10, "col": 1}
    assert range_["last"] == {"filename": "main.c", "line": 12, "col": 5}


def test_a_missing_column_is_a_wildcard(session):
    client, rpc = session
    rpc.on("crun", "addRuleOnMessageRange", _void)
    client.crun_rules.add_rule("AssertFailure", file="main.c", start_line=10)
    [(_, range_)] = rpc.args("crun", "addRuleOnMessageRange")
    assert range_["first"] == range_["last"] == {"filename": "main.c", "line": 10, "col": -1}


def test_a_source_position_needs_a_file(session):
    client, rpc = session
    with pytest.raises(CSpyError, match="needs 'file'"):
        client.crun_rules.add_rule("AssertFailure", start_line=10)
    assert rpc.methods("crun") == []


def test_default_action_get_and_set(session):
    client, rpc = session
    rpc.on("crun", "getDefaultAction", lambda: 0)
    rpc.on("crun", "setDefaultAction", _void)
    assert client.crun_rules.default_action() == CRunBreakAction.STOP_AND_LOG
    client.crun_rules.set_default_action(CRunBreakAction.LOG)
    client.crun_rules.set_default_action("IGNORE")
    client.crun_rules.set_default_action(0)
    assert rpc.args("crun", "setDefaultAction") == [(1,), (2,), (0,)]


def test_an_unknown_default_action_is_rejected_before_the_call(session):
    client, rpc = session
    with pytest.raises(CSpyError, match="Unknown C-RUN break action"):
        client.crun_rules.set_default_action("EXPLODE")
    assert rpc.methods("crun") == []


def test_load_and_save_filters_pass_the_path_through(session, tmp_path):
    client, rpc = session
    rpc.on("crun", "saveFilters", _void)
    rpc.on("crun", "loadFilters", _void)
    path = str(tmp_path / "rules.xml")
    client.crun_rules.save_filters(path)
    client.crun_rules.load_filters(path)
    assert rpc.args("crun", "saveFilters") == [(path,)]
    assert rpc.args("crun", "loadFilters") == [(path,)]


def test_the_rule_calls_need_a_started_session(fake):
    client, rpc = fake
    for call in (
        lambda: client.crun_rules.add_rule("X"),
        lambda: client.crun_rules.default_action(),
        lambda: client.crun_rules.set_default_action("LOG"),
        lambda: client.crun_rules.load_filters("rules.xml"),
        lambda: client.crun_rules.save_filters("rules.xml"),
    ):
        with pytest.raises(SessionError):
            call()
    assert rpc.methods("crun") == []


# -- client.crun_messages, the live CRunDisplay pushes -------------------------------


def _crun_thrift():
    return load_thrift_module(str(BUNDLED_IDL_DIR / "crun.thrift"), (str(BUNDLED_IDL_DIR),))


def test_a_message_is_stored_and_converted(fake):
    client, _ = fake
    crun = _crun_thrift()
    client.crun_messages.server.handler.itemAdded(
        crun_message(
            id=7,
            index=3,
            core=1,
            name="BoundsCheck",
            text="out of bounds",
            cycle=1234,
            repeatCount=2,
            callStack=["main", "f"],
            noStop=True,
            tooltip="tip",
            breakAction=crun.CRunBreakAction.kStopAndLog,
        ),
        "ns",
    )
    [message] = client.crun_messages.messages("ns")
    assert isinstance(message, CRunMessage)
    assert (message.id, message.index, message.core, message.name) == (7, 3, 1, "BoundsCheck")
    assert (message.text, message.cycle, message.repeat_count) == ("out of bounds", 1234, 2)
    assert message.call_stack == ["main", "f"] and message.no_stop is True and message.tooltip == "tip"
    assert message.break_action == CRunBreakAction.STOP_AND_LOG
    assert message.run_to is None and message.pc_source_range is None
    assert message.extra_source_ranges == [] and message.sub_messages == []


def test_locations_become_plain_dicts(fake):
    client, _ = fake
    shared = load_thrift_module(str(BUNDLED_IDL_DIR / "shared.thrift"), (str(BUNDLED_IDL_DIR),))
    source_range = shared.SourceRange(
        filename="main.c",
        first=shared.SourceLocation(filename="main.c", line=10, col=1),
        last=shared.SourceLocation(filename="main.c", line=10, col=9),
        text="x = y;",
    )
    client.crun_messages.server.handler.itemAdded(
        crun_message(pcSourceRange=source_range, extraSourceRanges=[source_range]), "ns"
    )
    [message] = client.crun_messages.messages("ns")
    assert message.pc_source_range["filename"] == "main.c"
    assert message.pc_source_range["first"]["line"] == 10
    assert message.extra_source_ranges[0]["last"]["col"] == 9


def test_an_update_replaces_the_message_in_place(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0, text="first"), "ns")
    handler.itemAdded(crun_message(index=1, text="second"), "ns")
    handler.itemUpdated(crun_message(index=0, text="first again", repeatCount=5), "ns")
    messages = client.crun_messages.messages("ns")
    assert [m.text for m in messages] == ["first again", "second"]
    assert messages[0].repeat_count == 5


def test_an_update_for_an_unknown_index_is_stored(fake):
    client, _ = fake
    client.crun_messages.server.handler.itemUpdated(crun_message(index=4, text="late"), "ns")
    assert [m.text for m in client.crun_messages.messages("ns")] == ["late"]


def test_a_message_is_removed_by_index(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0), "ns")
    handler.itemAdded(crun_message(index=1), "ns")
    handler.itemRemoved(0, "ns")
    assert [m.index for m in client.crun_messages.messages("ns")] == [1]


def test_removing_what_is_not_there_is_harmless(fake):
    client, _ = fake
    client.crun_messages.server.handler.itemRemoved(9, "never-seen")
    assert client.crun_messages.messages("never-seen") == []
    assert [event.kind for _, event in client.crun_messages.recent()] == ["item_removed"]


def test_update_all_empties_the_namespace(fake):
    # updateAll carries no data: the store drops the namespace and rebuilds it
    # from the item pushes that follow.
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0), "ns")
    handler.itemAdded(crun_message(index=0), "other")
    handler.updateAll("ns")
    assert client.crun_messages.messages("ns") == []
    assert len(client.crun_messages.messages("other")) == 1
    handler.itemAdded(crun_message(index=0, text="replayed"), "ns")
    assert [m.text for m in client.crun_messages.messages("ns")] == ["replayed"]


def test_filters_changed_leaves_the_messages(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0), "ns")
    handler.filtersChanged("ns")
    assert len(client.crun_messages.messages("ns")) == 1
    assert [event.kind for _, event in client.crun_messages.recent()] == ["item_added", "filters_changed"]


def test_namespaces_are_kept_apart(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0, text="a0"), "a")
    handler.itemAdded(crun_message(index=0, text="b0"), "b")
    handler.itemAdded(crun_message(index=1, text="a1"), "a")
    messages = client.crun_messages
    assert messages.namespaces() == ["a", "b"]
    assert {ns: [m.text for m in found] for ns, found in messages.all_messages().items()} == {
        "a": ["a0", "a1"],
        "b": ["b0"],
    }
    messages.clear("a")
    assert messages.namespaces() == ["b"] and messages.messages("a") == []
    messages.clear()
    assert messages.namespaces() == [] and messages.all_messages() == {}


def test_nothing_stored_reads_as_empty(fake):
    client, _ = fake
    assert client.crun_messages.messages("ns") == []
    assert client.crun_messages.namespaces() == []
    assert client.crun_messages.all_messages() == {}
    assert client.crun_messages.recent() == []


def test_the_returned_lists_are_snapshots(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0), "ns")
    snapshot = client.crun_messages.messages("ns")
    everything = client.crun_messages.all_messages()
    handler.itemAdded(crun_message(index=1), "ns")
    assert len(snapshot) == 1 and len(everything["ns"]) == 1
    snapshot.clear()
    assert len(client.crun_messages.messages("ns")) == 2


def test_sub_messages_nest_and_serialize(fake):
    client, _ = fake
    crun = _crun_thrift()
    inner = crun_message(index=1, name="inner", breakAction=crun.CRunBreakAction.kIgnore)
    client.crun_messages.server.handler.itemAdded(
        crun_message(index=0, name="outer", subMessages=[inner], breakAction=crun.CRunBreakAction.kStopAndLog), "ns"
    )
    [message] = client.crun_messages.messages("ns")
    assert [m.name for m in message.sub_messages] == ["inner"]
    assert message.sub_messages[0].break_action == CRunBreakAction.IGNORE
    plain = message.to_dict()
    assert plain["break_action"] == "STOP_AND_LOG"
    assert plain["sub_messages"][0]["break_action"] == "IGNORE"


def test_unset_fields_do_not_fail_the_backend_call(fake):
    client, _ = fake
    crun = _crun_thrift()
    client.crun_messages.server.handler.itemAdded(crun.CRunMessage(), "ns")
    [message] = client.crun_messages.messages("ns")
    assert (message.index, message.name, message.text, message.call_stack) == (0, "", "", [])
    assert message.break_action == CRunBreakAction.STOP_AND_LOG


def test_events_serialize(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=2, name="X"), "ns")
    handler.updateAll("ns")
    added, update_all = (event.to_dict() for _, event in client.crun_messages.recent())
    assert (added["kind"], added["namespace"], added["index"], added["message"]["name"]) == ("item_added", "ns", 2, "X")
    assert update_all == {"kind": "update_all", "namespace": "ns", "message": None, "index": None}


def test_subscribers_hear_every_push_until_they_unsubscribe(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    heard: list[tuple[str, CRunEvent]] = []
    unsubscribe = client.crun_messages.subscribe(lambda kind, event: heard.append((kind, event)))
    handler.itemAdded(crun_message(index=0), "ns")
    handler.itemUpdated(crun_message(index=0), "ns")
    handler.itemRemoved(0, "ns")
    handler.updateAll("ns")
    handler.filtersChanged("ns")
    unsubscribe()
    unsubscribe()  # idempotent
    handler.itemAdded(crun_message(index=1), "ns")
    assert [kind for kind, _ in heard] == ["item_added", "item_updated", "item_removed", "update_all", "filters_changed"]
    assert all(kind == event.kind and event.namespace == "ns" for kind, event in heard)
    assert heard[0][1].message.index == 0 and heard[2][1].index == 0
    assert len(client.crun_messages.recent()) == 6


def test_a_failing_subscriber_does_not_stop_the_others_or_the_store(fake):
    client, _ = fake
    heard: list[str] = []

    def failing(kind: str, event: CRunEvent) -> None:
        raise RuntimeError("boom")

    client.crun_messages.subscribe(failing)
    client.crun_messages.subscribe(lambda kind, event: heard.append(kind))
    client.crun_messages.server.handler.itemAdded(crun_message(index=0), "ns")
    assert heard == ["item_added"]
    assert len(client.crun_messages.messages("ns")) == 1


def test_a_subscriber_may_read_the_store(fake):
    client, _ = fake
    seen: list[int] = []
    client.crun_messages.subscribe(lambda kind, event: seen.append(len(client.crun_messages.messages("ns"))))
    client.crun_messages.server.handler.itemAdded(crun_message(index=0), "ns")
    client.crun_messages.server.handler.itemAdded(crun_message(index=1), "ns")
    assert seen == [1, 2]


def test_recent_can_drain_and_is_bounded(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0), "ns")
    assert len(client.crun_messages.recent()) == 1
    assert len(client.crun_messages.recent(clear=True)) == 1
    assert client.crun_messages.recent() == []
    assert len(client.crun_messages.messages("ns")) == 1  # draining the log keeps the store

    from iar_cspy.crun import CRunMessages

    small = CRunMessages(client, maxlen=2)
    for index in range(4):
        small._post(CRunEvent("item_added", "ns", crun_message_plain(index), index))
    assert [event.index for _, event in small.recent()] == [2, 3]
    assert [m.index for m in small.messages("ns")] == [0, 1, 2, 3]  # the store is not bounded
    small.server.stop()


def crun_message_plain(index: int) -> CRunMessage:
    from iar_cspy.crun import _message_from_thrift

    return _message_from_thrift(crun_message(index=index))


def test_wait_for_sees_only_pushes_after_the_mark(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0, name="before"), "ns")
    mark = client.crun_messages.mark()
    assert client.crun_messages.wait_for(lambda kind, event: True, after=mark, timeout=0.05) is None

    timer = threading.Timer(0.05, handler.itemAdded, [crun_message(index=1, name="after"), "ns"])
    timer.start()
    found = client.crun_messages.wait_for(lambda kind, event: kind == "item_added", after=mark, timeout=5)
    timer.join()
    assert found is not None and found[1].message.name == "after"
    # The earlier push is behind the mark; only the predicate-matching one counts.
    assert client.crun_messages.wait_for(lambda kind, event: kind == "update_all", after=mark, timeout=0.05) is None


def test_wait_for_returns_a_push_already_made(fake):
    client, _ = fake
    mark = client.crun_messages.mark()
    client.crun_messages.server.handler.updateAll("ns")
    found = client.crun_messages.wait_for(lambda kind, event: kind == "update_all", after=mark, timeout=0)
    assert found is not None and found[1].namespace == "ns"


def test_session_caches_forget_the_messages(fake):
    client, _ = fake
    handler = client.crun_messages.server.handler
    handler.itemAdded(crun_message(index=0), "a")
    handler.itemAdded(crun_message(index=0), "b")
    client.clear_session_caches()
    assert client.crun_messages.all_messages() == {}
