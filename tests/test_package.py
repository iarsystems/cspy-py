"""Packaging invariants of the distribution."""

from __future__ import annotations

import subprocess
import sys

from iar_cspy._thrift import BUNDLED_IDL_DIR


def test_api_does_not_depend_on_mcp():
    # iar-cspy must stay usable without the MCP SDK: the dependency only goes
    # from iar-cspy-mcp to iar-cspy.
    code = (
        "import sys; sys.modules['mcp'] = None\n"
        "import iar_cspy, iar_cspy.testing, iar_cspy.pytest_plugin\n"
        "assert not any(m == 'iar_cspy_mcp' or m.startswith('mcp.') for m in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_idl_is_bundled():
    names = {path.name for path in BUNDLED_IDL_DIR.glob("*.thrift")}
    assert {"cspy.thrift", "shared.thrift", "ServiceRegistry.thrift", "breakpoints.thrift"} <= names
