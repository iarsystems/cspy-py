"""C-RUN runtime-checking rules (client.crun_rules)."""

from __future__ import annotations

import pytest

from iar_cspy import CRunBreakAction, CSpyError, SessionError


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
