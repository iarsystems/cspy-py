from __future__ import annotations

import json
import os

import pytest

from iar_cspy import Backend, Config, CSpyError
from iar_cspy import ide_services
from iar_cspy._process import PORT_PATTERN
from iar_cspy.testing import fake_client


# --------------------------------------------------------------------------
# Service selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ["projectmanager", "options"]),
        (None, ["projectmanager", "options"]),
        ([], ["projectmanager", "options"]),
        # options depends on projectmanager, which is pulled in ahead of it.
        ("options", ["projectmanager", "options"]),
        ("OPTIONS, projectmanager", ["projectmanager", "options"]),
        ("options;options", ["projectmanager", "options"]),
        (["projectmanager"], ["projectmanager"]),
    ],
)
def test_normalize_service_keys(raw, expected):
    assert ide_services.normalize_service_keys(raw) == expected


def test_options_depends_on_projectmanager():
    # OptionsServiceHandler reaches the project manager by linkage, so hosting
    # options without projectmanager crashes the host process on CreateSession.
    assert ide_services.service_dependencies("options") == ("projectmanager",)
    assert ide_services.service_dependencies("projectmanager") == ()


def test_normalize_service_keys_detects_dependency_cycles(monkeypatch):
    monkeypatch.setitem(ide_services.IDE_SERVICES["projectmanager"], "depends_on", ("options",))
    with pytest.raises(CSpyError, match="Circular IDE service dependency"):
        ide_services.normalize_service_keys("options")


def test_normalize_service_keys_rejects_unknown():
    with pytest.raises(CSpyError, match="Unknown IDE service"):
        ide_services.normalize_service_keys("nosuchservice")


def test_registry_names_match_idl_constants():
    # These are the SERVICE_ID/PROJECTMANAGER_ID constants the services
    # register themselves under, not the manifest 'name' fields.
    assert ide_services.IDE_SERVICES["projectmanager"]["registry_name"] == "com.iar.thrift.service.projectmanager"
    assert ide_services.IDE_SERVICES["options"]["registry_name"] == "com.iar.optionsservice"


# --------------------------------------------------------------------------
# Locating the service libraries
# --------------------------------------------------------------------------


def test_service_bin_dir_prefers_explicit_setting(tmp_path, fake_stage):
    bin_dir = fake_stage()
    explicit = tmp_path / "elsewhere"
    explicit.mkdir()
    cfg = Config(launcher_executable=bin_dir / "IarServiceLauncher", service_bin_dir=explicit)
    assert ide_services.service_bin_dir(cfg) == explicit


def test_service_bin_dir_falls_back_to_launcher_then_cspyserver(fake_stage):
    bin_dir = fake_stage()
    assert ide_services.service_bin_dir(Config(launcher_executable=bin_dir / "IarServiceLauncher")) == bin_dir
    assert ide_services.service_bin_dir(Config(cspy_executable=bin_dir / "CSpyServer2")) == bin_dir


def test_service_bin_dir_without_any_hint_raises():
    with pytest.raises(CSpyError, match="--iar-path"):
        ide_services.service_bin_dir(Config())


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


def test_resolve_manifest_prefers_stock_manifest(fake_stage):
    bin_dir = fake_stage(manifests=("projectmanager.json",))
    cfg = Config(launcher_executable=bin_dir / "IarServiceLauncher")
    assert ide_services.resolve_manifest(cfg, "projectmanager") == bin_dir / "projectmanager.json"


def test_resolve_manifest_generates_one_with_relative_library(fake_stage):
    # OptionsService ships libOptionsService.so but no OptionsService.json,
    # so the manifest has to be generated.
    bin_dir = fake_stage(libraries=("OptionsService",))
    cfg = Config(launcher_executable=bin_dir / "IarServiceLauncher")

    manifest = ide_services.resolve_manifest(cfg, "options")
    assert manifest.parent != bin_dir

    entry = json.loads(manifest.read_text())["services"][0]
    assert entry["name"] == "com.iar.OptionsService"
    assert entry["startupEntryPoint"] == "OptionsServiceStart"
    assert entry["shutdownEntryPoint"] == "OptionsServiceStop"
    assert entry["spawnNewProcess"] is False
    assert entry["registerInLauncher"] is False

    # The ServiceManager prepends the manifest's own directory to libraryName
    # unconditionally, so an absolute path here would resolve to nonsense.
    assert not os.path.isabs(entry["libraryName"])
    resolved = (manifest.parent / entry["libraryName"]).resolve()
    assert resolved == (bin_dir / "OptionsService").resolve()
    assert resolved.with_name("lib" + resolved.name + ".so").exists()


def test_resolve_manifest_without_library_or_manifest_raises(fake_stage):
    bin_dir = fake_stage(libraries=())
    cfg = Config(launcher_executable=bin_dir / "IarServiceLauncher")
    with pytest.raises(CSpyError, match="neither the manifest"):
        ide_services.resolve_manifest(cfg, "options")


def test_render_manifest_is_relative_from_a_sibling_directory(tmp_path):
    bin_dir = tmp_path / "a" / "common" / "bin"
    manifest_dir = tmp_path / "b"
    bin_dir.mkdir(parents=True)
    manifest_dir.mkdir()

    rendered = json.loads(
        ide_services.render_manifest(ide_services.IDE_SERVICES["projectmanager"], bin_dir, manifest_dir)
    )
    library = rendered["services"][0]["libraryName"]
    assert library.startswith("..")
    assert (manifest_dir / library).resolve() == (bin_dir / "ProjectManagerHandler").resolve()


# --------------------------------------------------------------------------
# Backend: CSpyServer2 arguments and launcher
# --------------------------------------------------------------------------


def test_cspy_args_default_to_standalone():
    assert Backend(Config()).cspy_args(None) == ["-standalone", "-sockets"]


def test_cspy_args_join_launcher_registry():
    # With an IarServiceLauncher, CSpyServer2 must attach to the launcher's
    # registry instead of starting a second one.
    assert Backend(Config()).cspy_args(4711) == ["-sockets", "-registry", "4711"]


def test_cspy_args_join_overrides_configured_args():
    assert "-standalone" not in Backend(Config(cspy_args=("-standalone", "-sockets"))).cspy_args(5)


def test_standalone_backend_never_starts_processes():
    backend = Backend.standalone(51926)
    assert backend.registry_endpoint() == ("127.0.0.1", 51926)
    assert backend.status() == {"mode": "standalone"}
    backend.stop_debugger()  # a no-op outside managed mode


def test_standalone_backend_without_registry_has_no_endpoint():
    assert Backend(Config(mode="standalone")).registry_endpoint() is None


def test_managed_backend_without_executables_explains_iar_path():
    with pytest.raises(CSpyError, match="iar_path"):
        Backend(Config()).registry_endpoint()


def test_managed_backend_missing_launcher_file(tmp_path):
    backend = Backend(Config(launcher_executable=tmp_path / "nope" / "IarServiceLauncher"))
    with pytest.raises(CSpyError, match="not found"):
        backend.registry_endpoint()


def test_registry_banner_pattern_matches_launcher_output():
    line = "Service registry running on local socket on port: 41821"
    assert PORT_PATTERN.search(line).group(1) == "41821"


# --------------------------------------------------------------------------
# IdeServices.ensure
# --------------------------------------------------------------------------


@pytest.fixture()
def ide(fake_stage):
    """A fake client whose backend can host IDE services, and the manifests it loaded."""
    bin_dir = fake_stage(libraries=("ProjectManagerHandler", "OptionsService"), manifests=("projectmanager.json",))
    client, rpc = fake_client(
        Config(mode="standalone", registry_port=4711, launcher_executable=bin_dir / "IarServiceLauncher")
    )
    rpc.on("servicemanager", "startServicesFromJsonManifest", None)
    return client, rpc, bin_dir


def loaded(rpc) -> list[str]:
    return [args[0] for args in rpc.args("servicemanager", "startServicesFromJsonManifest")]


def test_ensure_reports_already_registered(ide):
    client, rpc, _ = ide
    client.registry.set(["com.iar.thrift.service.projectmanager"])

    assert client.ide_services.ensure("projectmanager")["status"] == "already-registered"
    assert loaded(rpc) == []


def test_ensure_caches_presence(ide):
    client, _, _ = ide
    client.registry.set(["com.iar.thrift.service.projectmanager"])

    assert client.ide_services.ensure("projectmanager")["status"] == "already-registered"
    assert client.ide_services.ensure("projectmanager")["status"] == "cached"
    # force= re-queries the registry rather than trusting the cache.
    assert client.ide_services.ensure("projectmanager", force=True)["status"] == "already-registered"


def test_ensure_cache_is_dropped_when_the_launcher_restarts(ide):
    client, _, _ = ide
    client.registry.set(["com.iar.thrift.service.projectmanager"])
    assert client.ide_services.ensure("projectmanager")["status"] == "already-registered"
    client.backend.launcher_generation += 1
    assert client.ide_services.ensure("projectmanager")["status"] == "already-registered"


def test_ensure_starts_missing_service(ide):
    client, rpc, bin_dir = ide
    client.registry.set(["debugger", "com.iar.thrift.service.manager"])

    result = client.ide_services.ensure("projectmanager")
    assert result["status"] == "started"
    assert result["manifest"] == str(bin_dir / "projectmanager.json")
    assert loaded(rpc) == [str(bin_dir / "projectmanager.json")]
    assert client.backend.loaded_manifests == {"projectmanager": str(bin_dir / "projectmanager.json")}


def test_ensure_starts_dependency_first(ide):
    client, rpc, bin_dir = ide
    client.registry.set(["com.iar.thrift.service.manager"])

    result = client.ide_services.ensure("options")
    assert result["status"] == "started"
    assert [d["service"] for d in result["dependencies"]] == ["projectmanager"]
    # ProjectManager's manifest is loaded before the OptionsService one.
    assert loaded(rpc)[0] == str(bin_dir / "projectmanager.json")
    assert loaded(rpc)[1].endswith("OptionsService.json")


def test_ensure_uses_a_long_timeout_for_loading_manifests(ide):
    client, rpc, _ = ide
    client.registry.set(["com.iar.thrift.service.manager"])
    client.ide_services.ensure("projectmanager")
    # Loading ProjectManagerHandler pulls in the whole legacy project manager.
    assert rpc.calls[-1][3] == {"timeout_ms": client.config.launcher_start_timeout_ms}


def test_ensure_without_service_manager_explains_cspyserver_limitation(ide):
    client, rpc, _ = ide
    client.registry.set(["debugger", "breakpoints"])

    with pytest.raises(CSpyError) as excinfo:
        client.ide_services.ensure("options")

    message = str(excinfo.value)
    assert "CSpyServer2 alone cannot host IDE services" in message
    assert "--iar-path" in message
    # The registry snapshot is included so the caller can see what it did get.
    assert "debugger" in message
    assert loaded(rpc) == []


def test_ensure_without_registry_explains_options():
    client, _ = fake_client(Config(mode="standalone"))
    with pytest.raises(CSpyError, match="no service registry"):
        client.ide_services.ensure("options")


@pytest.mark.parametrize("enabled", [False, True])
def test_auto_ensure_respects_opt_out(monkeypatch, enabled):
    client, _ = fake_client(Config(mode="standalone", registry_port=4711, auto_ide_services=enabled))
    called: list[str] = []
    monkeypatch.setattr(client.ide_services, "ensure", lambda key, **kw: called.append(key))
    client.ide_services.auto_ensure("options")
    assert called == (["options"] if enabled else [])


def test_ensure_all_reports_per_service_errors(ide):
    client, _, _ = ide
    client.registry.set(["debugger"])

    results = client.ide_services.ensure_all()
    assert [r["service"] for r in results] == ["projectmanager", "options"]
    assert all(r["status"] == "error" for r in results)


def test_status_reports_registry_and_services(ide):
    client, _, bin_dir = ide
    client.registry.set(["com.iar.thrift.service.manager", "com.iar.thrift.service.projectmanager"])

    status = client.ide_services.status()
    assert status["cspy_mode"] == "standalone"
    assert status["registry_port"] == 4711
    assert status["has_service_manager"] is True
    assert status["services"]["projectmanager"]["registered"] is True
    assert status["services"]["options"]["registered"] is False
    assert status["services"]["options"]["depends_on"] == ["projectmanager"]
    assert status["service_bin_dir"] == str(bin_dir)
