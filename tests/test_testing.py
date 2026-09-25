"""The public test doubles in iar_cspy.testing."""

from __future__ import annotations

import pytest

from iar_cspy import BackendError, CSpyError
from iar_cspy.testing import cspy_exception, fake_client


def test_module_docstring_example():
    import textwrap

    import iar_cspy.testing

    source = textwrap.dedent(iar_cspy.testing.__doc__.split("::", 1)[1].split("\n\n")[1])
    exec(compile(source, "example", "exec"), {})


def test_scripted_exceptions_are_converted_like_real_ones():
    client, rpc = fake_client(started=True)
    rpc.on("debugger", "go", cspy_exception(code=8, method="DkGo"))
    with pytest.raises(BackendError) as exc:
        client.debugger.go()
    assert exc.value.info.code_name == "kDcLicenseViolation"

    rpc.on("debugger", "stop", ConnectionResetError("WinError 10054"))
    with pytest.raises(CSpyError, match="Thrift RPC failed") as reset:
        client.debugger.halt()
    assert client.classify_error(reset.value)["code"] == "TRANSPORT_CONNECTION_RESET"

    # Unexpected calls stay assertion failures.
    with pytest.raises(AssertionError, match="Unexpected RPC"):
        client.debugger.reset()


def test_sequence_and_struct():
    client, rpc = fake_client(started=True)
    rpc.sequence("debugger", "getCoreState", 1, cspy_exception(), 0)
    assert int(client.debugger.core_state()) == 1
    with pytest.raises(BackendError):
        client.debugger.core_state()
    assert [int(client.debugger.core_state()) for _ in range(2)] == [0, 0]

    bp = rpc.struct("breakpoints", "getBreakpoint", id=3, ule="main", accessType="kDkFetchAccess")
    assert (bp.id, bp.ule) == (3, "main") and type(bp).__name__ == "Breakpoint"
    with pytest.raises(ValueError, match="does not return a struct"):
        rpc.struct("debugger", "getCoreState")
