"""Firmware tests with the iar_cspy pytest plugin.

    pytest examples/pytest --cspy-iar-path /opt/iar/ewarm

Without a --cspy-* option (or IAR_INSTALL_PATH) the tests are skipped.
"""

from pathlib import Path

import pytest

from iar_cspy import LaunchConfig

pytest_plugins = ["iar_cspy.pytest_plugin"]


@pytest.fixture
def cspy_launch() -> LaunchConfig:
    """The firmware under test; overrides the plugin's --cspy-launch fixture."""
    return LaunchConfig.from_file(Path(__file__).resolve().parents[1] / "firmware" / "launch.json")
