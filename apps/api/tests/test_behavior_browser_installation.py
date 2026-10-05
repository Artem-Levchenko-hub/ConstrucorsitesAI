import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from yleum_api.services import behavior_browser_installation as installation


@pytest.fixture
def owned_image(tmp_path, monkeypatch):
    root = tmp_path / "browser"
    root.mkdir()
    monkeypatch.setattr(installation, "INSTALLATION_ROOT", root)
    monkeypatch.setattr(installation, "IMAGE_OWNER_UID", os.getuid())
    executable = root / "chromium-1234" / "chrome"
    executable.parent.mkdir()
    executable.write_bytes(b"synthetic browser executable; never launched")
    executable.chmod(0o755)
    pin = hashlib.sha256(executable.read_bytes()).hexdigest()
    installation.write_manifest(str(executable), "1.60.0")
    return root, executable, pin


def test_manifest_is_produced_from_actual_bytes_and_matches_pin(owned_image):
    root, executable, pin = owned_image
    value = json.loads((root / "browser-manifest.json").read_text())
    assert value["sha256"] == pin
    assert value["bytes"] == executable.stat().st_size
    assert value["playwright_version"] == "1.60.0"
    installation.validate_installation(str(executable), pin)


@pytest.mark.parametrize("failure", ["changed", "other", "symlink", "writable", "manifest"])
def test_bad_installation_is_denied_without_path_or_pin_output(owned_image, failure):
    root, executable, pin = owned_image
    if failure == "changed":
        executable.write_bytes(b"changed")
    elif failure == "other":
        executable = root / "other"
        executable.write_bytes(b"synthetic browser executable; never launched")
        executable.chmod(0o755)
    elif failure == "symlink":
        link = root / "link"
        link.symlink_to(executable)
        executable = link
    elif failure == "writable":
        executable.chmod(0o777)
    else:
        (root / "browser-manifest.json").chmod(0o644)
        (root / "browser-manifest.json").write_text('{"version":1}')
    with pytest.raises(installation.BrowserInstallationUnavailable) as info:
        installation.validate_installation(str(executable), pin)
    assert str(executable) not in str(info.value)
    assert pin not in str(info.value)


def test_startup_disabled_does_not_read_or_select_any_manifest(monkeypatch):
    monkeypatch.setattr(installation, "validate_installation", lambda *_: pytest.fail("read"))
    installation.validate_startup({})


def test_startup_partial_configuration_is_not_silently_disabled():
    with pytest.raises(installation.BrowserInstallationUnavailable):
        installation.validate_startup({"MAX_BEHAVIOR_ADAPTER_REGISTRY": "configured"})


def test_foreign_owner_cannot_supply_image_browser(owned_image, monkeypatch):
    _, executable, pin = owned_image
    monkeypatch.setattr(installation, "IMAGE_OWNER_UID", os.getuid() + 1)
    with pytest.raises(installation.BrowserInstallationUnavailable):
        installation.validate_installation(str(executable), pin)


def test_symlink_in_installation_ancestor_is_rejected(owned_image, tmp_path, monkeypatch):
    root, executable, pin = owned_image
    alias = tmp_path / "alias"
    alias.symlink_to(root.parent, target_is_directory=True)
    monkeypatch.setattr(installation, "INSTALLATION_ROOT", alias / root.name)
    aliased_executable = alias / root.name / executable.relative_to(root)
    with pytest.raises(installation.BrowserInstallationUnavailable):
        installation.validate_installation(str(aliased_executable), pin)


def test_production_factory_requires_and_rechecks_image_manifest(owned_image, monkeypatch):
    from types import SimpleNamespace as NS

    from yleum_api.services.behavior_driver_configuration import configured_behavior_driver
    from yleum_api.services.max_behavior_proof import BehaviorProofError

    root, executable, pin = owned_image
    configuration = NS(
        env="prod",
        max_behavior_browser_executable=str(executable),
        max_behavior_browser_sha256=pin,
        max_behavior_adapter_registry=json.dumps(
            {
                "max-miniapp-nextjs": {
                    "coffee_summary": {
                        "button": "#check",
                        "form": "#form",
                        "text_input": "#name",
                        "summary": "#summary",
                    }
                }
            }
        ),
        max_behavior_vendor_asset_registry="",
    )
    assert configured_behavior_driver(NS(), configuration) is not None
    (root / "browser-manifest.json").unlink()
    with pytest.raises(BehaviorProofError, match="BEHAVIOR_CONFIGURATION_UNAVAILABLE"):
        configured_behavior_driver(NS(), configuration)


async def test_configured_production_driver_rejects_removed_manifest_before_transport(
    owned_image,
):
    from yleum_api.services.behavior_driver_configuration import configured_behavior_driver
    from yleum_api.services.max_behavior_proof import BehaviorProofError

    from .test_behavior_compilation_resolver import setup
    from .test_behavior_driver_configuration import settings

    root, executable, pin = owned_image
    configuration = settings(root)
    configuration.env = "prod"
    configuration.max_behavior_browser_executable = str(executable)
    configuration.max_behavior_browser_sha256 = pin
    request, handle, _, _, _ = setup()
    driver = configured_behavior_driver(handle, configuration)
    (root / "browser-manifest.json").unlink()
    with pytest.raises(BehaviorProofError, match="BEHAVIOR_CONFIGURATION_UNAVAILABLE"):
        await driver.collect_compiled(request)
    handle.operation_status.assert_not_awaited()


@pytest.mark.parametrize("bad_file", ["fifo", "oversized", "directory"])
def test_manifest_special_or_oversized_file_is_rejected_before_read(owned_image, bad_file):
    root, executable, pin = owned_image
    manifest = root / "browser-manifest.json"
    manifest.unlink()
    if bad_file == "fifo":
        os.mkfifo(manifest)
    elif bad_file == "oversized":
        manifest.write_bytes(b" " * 8193)
    else:
        manifest.mkdir()
    with pytest.raises(installation.BrowserInstallationUnavailable):
        installation.validate_installation(str(executable), pin)


def test_startup_checks_pin_before_exec_and_executes_original_arguments(owned_image, monkeypatch):
    _, executable, pin = owned_image
    environment = {
        "MAX_BEHAVIOR_BROWSER_EXECUTABLE": str(executable),
        "MAX_BEHAVIOR_BROWSER_SHA256": pin,
        "MAX_BEHAVIOR_ADAPTER_REGISTRY": "configured",
    }
    for key in installation.CONFIGURATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    called = []
    monkeypatch.setattr(os, "execvp", lambda command, args: called.append((command, args)))
    installation.main(["--exec", "python", "-m", "yleum_api.workers.generation"])
    assert called == [("python", ["python", "-m", "yleum_api.workers.generation"])]


def test_docker_and_compose_propagate_same_browser_installation_to_workers():
    import yaml

    repository = Path(__file__).parents[3]
    docker = (repository / "apps/api/Dockerfile").read_text()
    assert "PLAYWRIGHT_BROWSERS_PATH=/opt/omnia/playwright" in docker
    assert "--write-image-manifest" in docker
    assert '"yleum_api.services.behavior_browser_installation", "--exec"' in docker
    compose = yaml.safe_load(
        (repository / "apps/llm-gateway/deploy/full/docker-compose.yml").read_text()
    )
    for name in ["api", "generation-worker"]:
        for key in installation.CONFIGURATION_KEYS:
            assert compose["services"][name]["environment"][key] == "${" + key + ":-}"


@pytest.mark.parametrize("partial", [False, True])
def test_actual_startup_exec_and_partial_config_failure_are_bounded(partial):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in installation.CONFIGURATION_KEYS
    }
    if partial:
        environment["MAX_BEHAVIOR_ADAPTER_REGISTRY"] = "synthetic-private-config"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yleum_api.services.behavior_browser_installation",
            "--exec",
            sys.executable,
            "-c",
            "print('synthetic-child-started');raise SystemExit(23)",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == (1 if partial else 23)
    assert ("synthetic-child-started" in result.stdout) is not partial
    if partial:
        assert result.stderr.strip() == "BEHAVIOR_BROWSER_INSTALLATION_UNAVAILABLE"
        assert "synthetic-private-config" not in result.stderr
