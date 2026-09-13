"""Vault is mocked; key generation, durable files and rotation use the real filesystem."""

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from omnia_orchestrator.services import app_data_keys as keys

_REAL_TMPFS_CHECK = keys._require_tmpfs


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "_require_tmpfs", lambda path: None)
    token = tmp_path / "token"
    token.write_text("private-vault-token")
    token.chmod(0o600)
    config = keys.VaultDataKeyConfig(
        "https://vault.example",
        token,
        "transit",
        "app-data",
        tmp_path / "wrapped",
        tmp_path / "runtime",
    )
    wrapped = {}
    calls = []
    failures = {}

    def vault(request):
        calls.append(request)
        assert request.headers["X-Vault-Token"] == "private-vault-token"
        if failures.get("status"):
            return httpx.Response(failures["status"], text="private-vault-token DEK")
        if "body" in failures:
            return httpx.Response(200, content=failures["body"])
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": failures.get(
                        "policy",
                        {
                            "derived": True,
                            "type": "aes256-gcm96",
                            "exportable": False,
                            "allow_plaintext_backup": False,
                        },
                    )
                },
            )
        body = json.loads(request.content)
        if "/encrypt/" in request.url.path:
            ciphertext = f"vault:v1:{uuid4().hex}"
            wrapped[ciphertext] = body
            return httpx.Response(200, json={"data": {"ciphertext": ciphertext}})
        original = wrapped[body["ciphertext"]]
        assert original["context"] == body["context"]
        return httpx.Response(200, json={"data": {"plaintext": original["plaintext"]}})

    manager = keys.AppDataKeyManager(
        config,
        client=httpx.Client(
            transport=httpx.MockTransport(vault),
            trust_env=False,
        ),
    )
    return manager, config, calls, failures


def test_missing_existing_identity_fails_closed(provider):
    manager, config, calls, _ = provider
    with pytest.raises(keys.AppDataKeyError, match="missing"):
        manager.prepare(str(uuid4()))
    assert not list(config.wrapped_root.rglob("*.json"))
    assert not calls


@pytest.mark.parametrize("status", [301, 307, 401, 403, 429, 500])
def test_http_status_never_redirects_or_leaks(provider, status):
    manager, _, calls, failures = provider
    failures["status"] = status
    with pytest.raises(keys.AppDataKeyError) as error:
        manager.prepare(str(uuid4()), allow_create=True)
    assert len(calls) == 1
    assert "private-vault-token" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [b"invalid private-vault-token", b"[]", b'{"data":null}', b"a" * (1024 * 1024 + 1)],
    ids=["non-json", "non-object", "null-data", "oversized"],
)
def test_malformed_or_oversized_vault_response_fails_closed(provider, body):
    manager, config, _, failures = provider
    failures["body"] = body
    with pytest.raises(keys.AppDataKeyError) as error:
        manager.prepare(str(uuid4()), allow_create=True)
    assert "private-vault-token" not in str(error.value)
    assert not list(config.wrapped_root.rglob("*.json"))


def test_concurrent_provision_generates_single_project_key(provider):
    manager, _, calls, _ = provider
    project = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        paths = list(executor.map(lambda _: manager.prepare(project, allow_create=True), range(4)))
    assert len(set(paths)) == 1
    assert len([r for r in calls if "/encrypt/" in r.url.path]) == 1


def test_rotation_cap_keeps_all_previous_keys(provider, monkeypatch):
    manager, config, _, _ = provider
    monkeypatch.setattr(keys, "_MAX_VERSIONS", 2)
    project = str(uuid4())
    manager.prepare(project, allow_create=True)
    manager.rotate(project)
    durable = {p: p.read_bytes() for p in config.wrapped_root.rglob("*.json")}
    with pytest.raises(keys.AppDataKeyError, match="limit"):
        manager.rotate(project)
    assert {p: p.read_bytes() for p in durable} == durable


def test_project_ring_cannot_be_moved_to_another_identity(provider):
    manager, config, _, _ = provider
    first, second = str(uuid4()), str(uuid4())
    manager.prepare(first, allow_create=True)
    manager.prepare(second, allow_create=True)
    (config.wrapped_root / f"{second}.json").write_bytes(
        (config.wrapped_root / f"{first}.json").read_bytes()
    )
    with pytest.raises(keys.AppDataKeyError, match="invalid"):
        manager.prepare(second, allow_create=True)


def test_symlink_storage_is_rejected(provider, tmp_path):
    manager, config, calls, _ = provider
    destination = tmp_path / "elsewhere"
    destination.mkdir()
    try:
        config.runtime_root.symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not grant symlink creation")
    with pytest.raises(keys.AppDataKeyError, match="symlink"):
        manager.prepare(str(uuid4()), allow_create=True)
    assert not calls


@pytest.mark.skipif(os.name != "posix", reason="Linux filesystem permissions")
def test_host_files_private_node_mount_readonly(provider):
    manager, config, _, _ = provider
    path = manager.prepare(str(uuid4()), allow_create=True)
    assert path.stat().st_mode & 0o777 == 0o444
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert config.wrapped_root.stat().st_mode & 0o777 == 0o700
    assert next(config.wrapped_root.glob("*.json")).stat().st_mode & 0o777 == 0o600


def test_provision_restart_rotation_and_project_separation(provider):
    manager, config, calls, _ = provider
    project = str(uuid4())
    first_path = manager.prepare(project, allow_create=True)
    first = json.loads(first_path.read_text())
    assert first["projectId"] == project
    assert first["activeVersion"] == "1"
    assert first["activeVersion"] in first["keys"]
    assert all(isinstance(version, str) for version in first["keys"])
    dek = base64.b64decode(first["keys"]["1"], validate=True)
    assert len(dek) == 32
    durable = b"".join(p.read_bytes() for p in config.wrapped_root.rglob("*.json"))
    assert dek not in durable
    assert first["keys"]["1"].encode() not in durable
    inode = first_path.stat().st_ino
    assert manager.prepare(project) == first_path
    assert first_path.stat().st_ino == inode
    first_path.unlink()  # Simulate loss of tmpfs on host reboot.
    assert json.loads(manager.prepare(project).read_text()) == first
    second_path = manager.rotate(project)
    second = json.loads(second_path.read_text())
    assert second_path != first_path
    assert second["activeVersion"] == "2"
    assert second["activeVersion"] in second["keys"]
    assert second["keys"]["1"] == first["keys"]["1"]
    assert second["keys"]["2"] != first["keys"]["1"]
    other = json.loads(manager.prepare(str(uuid4()), allow_create=True).read_text())
    assert other["keys"]["1"] != first["keys"]["1"]
    assert all(request.url.scheme == "https" for request in calls)


def test_vault_failure_is_sanitized_and_does_not_change_previous_ring(provider):
    manager, config, _, failures = provider
    project = str(uuid4())
    path = manager.prepare(project, allow_create=True)
    durable = {p: p.read_bytes() for p in config.wrapped_root.rglob("*.json")}
    failures["status"] = 403
    with pytest.raises(keys.AppDataKeyError) as error:
        manager.rotate(project)
    assert "private-vault-token" not in str(error.value)
    assert path.exists()
    assert {p: p.read_bytes() for p in durable} == durable


@pytest.mark.parametrize(
    "address",
    [
        "http://vault.example",
        "https://user:pass@vault.example",
        "https://vault.example/?x=y",
        "https://vault.example/#fragment",
        "https://vault.example/base",
    ],
)
def test_invalid_vault_addresses_fail(provider, address):
    _, config, _, _ = provider
    with pytest.raises(keys.AppDataKeyError):
        keys.AppDataKeyManager(
            keys.VaultDataKeyConfig(
                address,
                config.token_file,
                config.mount,
                config.key_name,
                config.wrapped_root,
                config.runtime_root,
            )
        )


def test_vault_policy_and_project_ids_fail_closed(provider):
    manager, config, calls, failures = provider
    with pytest.raises(keys.AppDataKeyError):
        manager.prepare("../../victim", allow_create=True)
    assert not calls
    failures["policy"] = {
        "derived": False,
        "type": "aes256-gcm96",
        "exportable": False,
        "allow_plaintext_backup": False,
    }
    with pytest.raises(keys.AppDataKeyError):
        manager.prepare(str(uuid4()), allow_create=True)
    assert not list(config.wrapped_root.rglob("*.json"))


def test_corrupt_established_ring_never_regenerates(provider):
    manager, config, _, _ = provider
    project = str(uuid4())
    manager.prepare(project, allow_create=True)
    ring = next(config.wrapped_root.rglob("*.json"))
    ring.write_text("{}")
    with pytest.raises(keys.AppDataKeyError):
        manager.prepare(project, allow_create=True)
    assert ring.read_text() == "{}"


def test_plaintext_disk_rejected_before_vault(provider, monkeypatch):
    manager, _, calls, _ = provider

    def reject(path: Path):
        raise keys.AppDataKeyError("Runtime key directory must use tmpfs")

    monkeypatch.setattr(keys, "_require_tmpfs", reject)
    with pytest.raises(keys.AppDataKeyError, match="tmpfs"):
        manager.prepare(str(uuid4()), allow_create=True)
    assert not calls


def test_mount_verifier_selects_deepest_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(keys.sys, "platform", "linux")
    parent = tmp_path / "tmpfs"
    runtime = parent / "keys"
    original_read = Path.read_text
    mount_text = (
        f"1 0 0:1 / {tmp_path} rw - ext4 /dev/disk rw\n2 1 0:2 / {parent} rw - tmpfs tmpfs rw\n"
    )

    def read_mounts(path, *args, **kwargs):
        if str(path).replace("\\", "/") == "/proc/self/mountinfo":
            return mount_text
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_mounts)
    _REAL_TMPFS_CHECK(runtime)
    mount_text += f"3 2 0:3 / {runtime} rw - ext4 /dev/disk rw\n"
    with pytest.raises(keys.AppDataKeyError, match="tmpfs"):
        _REAL_TMPFS_CHECK(runtime)
