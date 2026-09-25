"""Repository-wide test setup: the live tests default to the bundled firmware."""

from __future__ import annotations

from pathlib import Path

import pytest

from iar_cspy import LaunchConfig

BUNDLED_LAUNCH = Path(__file__).resolve().parent / "examples" / "firmware" / "launch.json"


@pytest.fixture
def cspy_launch(pytestconfig) -> LaunchConfig:
    """--cspy-launch, or the bundled Cortex-M3 simulator firmware."""
    return LaunchConfig.from_file(pytestconfig.getoption("--cspy-launch") or BUNDLED_LAUNCH)
