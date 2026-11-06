"""The standard argparse CLI for Config: flag -> field mapping, and the mode side effect."""

from __future__ import annotations

import argparse

import pytest

from iar_cspy.cli import add_connection_arguments, config_from_args, parse_config


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_connection_arguments(parser)
    return parser.parse_args(argv)


def test_nothing_given_is_an_unchanged_config_from_env(monkeypatch):
    monkeypatch.delenv("IAR_INSTALL_PATH", raising=False)
    monkeypatch.delenv("THRIFT_CSPYSERVER_MODE", raising=False)

    config = config_from_args(parse())

    assert config.mode == "managed"
    assert config.iar_path is None
    assert config.registry_uri is None
    assert config.auto_discovery_enabled is False


def test_iar_path(tmp_path, monkeypatch):
    monkeypatch.delenv("THRIFT_CSPYSERVER_MODE", raising=False)
    (tmp_path / "common" / "bin").mkdir(parents=True)

    config = config_from_args(parse("--iar-path", str(tmp_path)))

    assert config.iar_path == tmp_path.resolve()
    assert config.mode == "managed"


def test_cspyserver2_maps_to_cspy_executable(tmp_path):
    exe = tmp_path / "CSpyServer2"
    exe.touch()

    config = config_from_args(parse("--cspyserver2", str(exe)))

    assert config.cspy_executable == exe.resolve()


def test_cspyserver2_args_are_shell_split():
    config = config_from_args(parse("--cspyserver2-args", "-standalone -sockets --foo=bar"))
    assert config.cspy_args == ("-standalone", "-sockets", "--foo=bar")


def test_service_launcher_maps_to_launcher_executable(tmp_path):
    exe = tmp_path / "IarServiceLauncher"
    exe.touch()

    config = config_from_args(parse("--service-launcher", str(exe)))

    assert config.launcher_executable == exe.resolve()


def test_ide_services_is_comma_separated_and_normalized():
    config = config_from_args(parse("--ide-services", " ProjectManager, Options "))
    assert config.ide_services == ("projectmanager", "options")


def test_no_ide_services_disables_host_ide_services():
    config = config_from_args(parse("--no-ide-services"))
    assert config.host_ide_services is False


def test_ide_services_and_no_ide_services_are_mutually_exclusive():
    parser = argparse.ArgumentParser()
    add_connection_arguments(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--ide-services", "projectmanager", "--no-ide-services"])


@pytest.mark.parametrize(
    "argv",
    [
        ("--registry-uri", "thrift://127.0.0.1:1"),
        ("--registry-host", "10.0.0.1", "--registry-port", "51926"),
        ("--registry-port", "51926"),
        ("--auto-discovery",),
        ("--auto-discovery-dir", "/tmp/whatever"),
        ("--workspace", "/path/to/project.eww"),
    ],
)
def test_any_standalone_flag_forces_standalone_mode(argv):
    config = config_from_args(parse(*argv))
    assert config.mode == "standalone"


def test_registry_uri_given_directly():
    config = config_from_args(parse("--registry-uri", "thrift://127.0.0.1:51926"))
    assert config.registry_uri == "thrift://127.0.0.1:51926"


def test_registry_host_and_port_are_joined():
    config = config_from_args(parse("--registry-host", "10.0.0.1", "--registry-port", "51926"))
    assert config.registry_uri == "thrift://10.0.0.1:51926"


def test_registry_port_alone_defaults_host_to_loopback():
    config = config_from_args(parse("--registry-port", "51926"))
    assert config.registry_uri == "thrift://127.0.0.1:51926"


def test_registry_uri_and_registry_port_together_is_rejected():
    with pytest.raises(ValueError):
        config_from_args(parse("--registry-uri", "thrift://1:2", "--registry-port", "3"))


def test_registry_host_without_port_is_rejected():
    with pytest.raises(ValueError):
        config_from_args(parse("--registry-host", "10.0.0.1"))


def test_registry_service_maps_to_registry_service_name():
    config = config_from_args(parse("--registry-port", "1", "--registry-service", "my.debugger"))
    assert config.registry_service_name == "my.debugger"


def test_workspace_maps_to_auto_discovery_workspace():
    config = config_from_args(parse("--workspace", "/path/to/project.eww"))
    assert config.auto_discovery_workspace == "/path/to/project.eww"


def test_auto_discovery_flag_enables_it():
    config = config_from_args(parse("--auto-discovery"))
    assert config.auto_discovery_enabled is True


def test_auto_discovery_dir(tmp_path):
    config = config_from_args(parse("--auto-discovery-dir", str(tmp_path)))
    assert config.auto_discovery_dir == tmp_path.resolve()


def test_auto_discovery_require_alive_defaults_to_true_when_unset():
    config = config_from_args(parse("--auto-discovery"))
    assert config.auto_discovery_require_alive is True


def test_auto_discovery_require_alive_flag():
    config = config_from_args(parse("--auto-discovery", "--auto-discovery-require-alive"))
    assert config.auto_discovery_require_alive is True


def test_auto_discovery_allow_dead_flag():
    config = config_from_args(parse("--auto-discovery", "--auto-discovery-allow-dead"))
    assert config.auto_discovery_require_alive is False


def test_require_alive_and_allow_dead_are_mutually_exclusive():
    parser = argparse.ArgumentParser()
    add_connection_arguments(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--auto-discovery-require-alive", "--auto-discovery-allow-dead"])


def test_parse_config_is_a_one_shot_convenience(monkeypatch):
    monkeypatch.delenv("IAR_INSTALL_PATH", raising=False)
    config = parse_config(["--registry-port", "4711"])
    assert config.registry_uri == "thrift://127.0.0.1:4711"
