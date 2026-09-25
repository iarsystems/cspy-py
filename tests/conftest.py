from __future__ import annotations

import pytest

from iar_cspy.testing import fake_client


@pytest.fixture()
def fake_stage(tmp_path):
    """Factory for an installation-like common/bin with service libraries and stock manifests."""
    return lambda **kw: make_stage(tmp_path, **kw)


def make_stage(tmp_path, *, libraries=("ProjectManagerHandler",), manifests=()):
    bin_dir = tmp_path / "stage" / "common" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "IarServiceLauncher").write_text("#!/bin/sh\n")
    (bin_dir / "CSpyServer2").write_text("#!/bin/sh\n")
    for name in libraries:
        (bin_dir / f"lib{name}.so").write_bytes(b"")
    for name in manifests:
        (bin_dir / name).write_text("{}")
    return bin_dir


@pytest.fixture()
def fake():
    """(client, rpc): a Client answering RPCs from a script. See iar_cspy.testing."""
    client, rpc = fake_client()
    yield client, rpc
    client.close()


@pytest.fixture()
def session():
    """(client, rpc) like ``fake``, with a started debug session."""
    client, rpc = fake_client(started=True)
    yield client, rpc
    client.close()
