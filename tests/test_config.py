from __future__ import annotations

import os

import pytest

from iar_cspy import Config
from iar_cspy._thrift import BUNDLED_IDL_DIR


def from_env(monkeypatch, **env) -> Config:
    for key in list(os.environ):
        if key.startswith("THRIFT_") or key == "IAR_INSTALL_PATH":
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Config.from_env()


def test_defaults(monkeypatch):
    cfg = from_env(monkeypatch)
    assert cfg.mode == "managed"
    assert cfg.launcher_executable is None
    assert cfg.service_bin_dir is None
    assert cfg.ide_services == ()
    assert cfg.auto_ide_services is True
    assert cfg.thrift_file == BUNDLED_IDL_DIR / "cspy.thrift"
    assert cfg.include_dirs == (str(BUNDLED_IDL_DIR),)
    assert Config() == cfg


def test_from_iar_path(monkeypatch, fake_stage):
    # One path for the whole toolchain; the programs come from common/bin.
    bin_dir = fake_stage()
    stage = bin_dir.parent.parent
    cfg = from_env(
        monkeypatch,
        IAR_INSTALL_PATH=str(stage),
        THRIFT_IDE_SERVICES="options, projectmanager",
        THRIFT_AUTO_IDE_SERVICES="0",
    )
    assert cfg.mode == "managed"
    assert cfg.iar_path == stage
    assert cfg.service_bin_dir == bin_dir
    assert cfg.launcher_executable == bin_dir / "IarServiceLauncher"
    assert cfg.cspy_executable == bin_dir / "CSpyServer2"
    assert cfg.ide_services == ("options", "projectmanager")
    assert cfg.auto_ide_services is False
    # The same derivation applies when constructing directly.
    assert Config(iar_path=stage).cspy_executable == bin_dir / "CSpyServer2"


def test_explicit_paths_override_the_installation(monkeypatch, tmp_path, fake_stage):
    bin_dir = fake_stage()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cfg = from_env(
        monkeypatch,
        IAR_INSTALL_PATH=str(bin_dir.parent.parent),
        THRIFT_CSPYSERVER_EXE=str(elsewhere / "CSpyServer2"),
        THRIFT_SERVICE_LAUNCHER_EXE=str(elsewhere / "IarServiceLauncher"),
    )
    assert cfg.cspy_executable == elsewhere / "CSpyServer2"
    assert cfg.launcher_executable == elsewhere / "IarServiceLauncher"


def test_explicit_missing_path_is_not_silently_dropped(monkeypatch, tmp_path):
    # An installation that does not ship a program yields None so the backend
    # can do without it, but a path given by hand must still surface as an error later.
    cfg = from_env(monkeypatch, THRIFT_CSPYSERVER_EXE=str(tmp_path / "nope" / "CSpyServer2"))
    assert cfg.cspy_executable == tmp_path / "nope" / "CSpyServer2"


def test_installation_without_a_launcher(tmp_path):
    # A compiler-only toolchain has no IarServiceLauncher; managed mode then
    # just runs CSpyServer2 on its own.
    bin_dir = tmp_path / "stage" / "common" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "CSpyServer2").write_text("#!/bin/sh\n")
    cfg = Config(iar_path=tmp_path / "stage")
    assert cfg.cspy_executable == bin_dir / "CSpyServer2"
    assert cfg.launcher_executable is None


def test_host_ide_services_opt_out(monkeypatch, fake_stage):
    bin_dir = fake_stage()
    cfg = from_env(monkeypatch, IAR_INSTALL_PATH=str(bin_dir.parent.parent), THRIFT_HOST_IDE_SERVICES="0")
    assert cfg.host_ide_services is False
    assert cfg.launcher_executable is None
    assert cfg.cspy_executable == bin_dir / "CSpyServer2"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "managed"),
        ("managed", "managed"),
        ("standalone", "standalone"),
        # Names this setting used before the modes were reduced to two.
        ("external", "standalone"),
        ("launcher", "managed"),
        ("STANDALONE", "standalone"),
        ("nonsense", "managed"),
    ],
)
def test_mode_names_and_aliases(monkeypatch, raw, expected):
    env = {"THRIFT_CSPYSERVER_MODE": raw} if raw is not None else {}
    assert from_env(monkeypatch, **env).mode == expected


def test_registry_and_timeouts_from_env(monkeypatch):
    cfg = from_env(
        monkeypatch,
        THRIFT_CSPYSERVER_MODE="standalone",
        THRIFT_REGISTRY_HOST="10.0.0.2",
        THRIFT_REGISTRY_PORT="51926",
        THRIFT_TIMEOUT_MS="1234",
        THRIFT_CSPYSERVER_ARGS="-standalone -sockets -foo",
        THRIFT_AUTO_EVENTHANDLER="off",
        THRIFT_INCLUDE_DIRS="/a;/b",
    )
    assert (cfg.registry_host, cfg.registry_port, cfg.timeout_ms) == ("10.0.0.2", 51926, 1234)
    assert cfg.cspy_args == ("-standalone", "-sockets", "-foo")
    assert cfg.auto_eventhandler is False
    assert cfg.include_dirs == ("/a", "/b", str(BUNDLED_IDL_DIR))


def test_from_env_overrides_and_unknown_fields():
    assert Config.from_env({"THRIFT_TIMEOUT_MS": "1"}, timeout_ms=5).timeout_ms == 5
    with pytest.raises(TypeError, match="bogus"):
        Config.from_env({}, bogus=1)


def test_to_dict_is_json_safe():
    import json

    json.dumps(Config().to_dict())
