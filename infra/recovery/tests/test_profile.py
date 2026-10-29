"""Only synthetic profiles/keys; no actual owner profile is accessed."""

import base64
import json
import os
import warnings
from pathlib import Path

import pytest
from pydantic import SecretStr

from yleum_recovery import local_unlock
from yleum_recovery.cli import main
from yleum_recovery.contracts import RecoveryProfile, Role, SecretMaterial
from yleum_recovery.local_unlock import activate_local_key, load_local_key
from yleum_recovery.profile import open_profile, resolve_profile, seal_profile
from yleum_recovery.redaction import LocalUnlockError, ProfileInvalid

SENTINEL = 'SYNTHETIC-only-秘密-пароль\n"\\\x00$!?='
KEY = bytes(range(32))


@pytest.fixture
def profile():
    return RecoveryProfile(
        name="synthetic",
        provider_adapter="test",
        provider_account="test",
        provider_group="test",
        role_selectors={role: f"role={role.value}" for role in Role},
        storage_adapter="test",
        lease_adapter="test",
        ingress_adapter="test",
        secret_material=SecretMaterial(
            export_id="synthetic-export",
            credentials={"provider-token": SecretStr(SENTINEL)},
            keys={
                "JWT-derived": SecretStr(SENTINEL + "jwt"),
                "SECRETS_ENCRYPTION_KEY": SecretStr(SENTINEL + "encryption"),
            },
            role_exports={
                role: {"complete-export": SecretStr(SENTINEL + role.value)} for role in Role
            },
        ),
    )


def test_encrypted_full_secret_roundtrip_and_fresh_nonce(profile, caplog):
    first, second = seal_profile(profile, KEY), seal_profile(profile, KEY)
    assert first != second
    assert len(base64.b64decode(json.loads(first)["nonce"])) == 12
    restored = open_profile(first, KEY)
    assert restored == profile
    assert restored.secret_material.keys["JWT-derived"].get_secret_value() == SENTINEL + "jwt"
    for role in Role:
        assert (
            restored.secret_material.role_exports[role]
            == profile.secret_material.role_exports[role]
        )
    for output in (first.decode(), repr(restored), restored.model_dump_json(), caplog.text):
        assert SENTINEL not in output
        assert "SYNTHETIC-only" not in output


@pytest.mark.parametrize("mutation", ["key", "tag", "name", "version", "nonce", "extra"])
def test_tamper_or_wrong_key_fails_safely(profile, mutation, caplog):
    envelope = json.loads(seal_profile(profile, KEY))
    key = KEY
    if mutation == "key":
        key = b"x" * 32
    elif mutation == "tag":
        encrypted = base64.b64decode(envelope["ciphertext"])
        envelope["ciphertext"] = base64.b64encode(
            encrypted[:-1] + bytes([encrypted[-1] ^ 1])
        ).decode()
    elif mutation == "name":
        envelope["profile_id"] = "other"
    elif mutation == "version":
        envelope["format_version"] = 2
    elif mutation == "nonce":
        envelope["nonce"] = base64.b64encode(b"x" * 12).decode()
    else:
        envelope["secret"] = SENTINEL
    with pytest.raises(ProfileInvalid) as error:
        open_profile(json.dumps(envelope).encode(), key)
    assert error.value.code == "PROFILE_INVALID"
    assert "SYNTHETIC-only" not in str(error.value) + repr(error.value) + caplog.text
    assert error.value.__cause__ is None


@pytest.mark.parametrize("key", [b"", b"x" * 16, b"x" * 24, b"x" * 33])
def test_only_256_bit_keys(profile, key):
    with pytest.raises(ProfileInvalid):
        seal_profile(profile, key)
    with pytest.raises(ProfileInvalid):
        open_profile(seal_profile(profile, KEY), key)


@pytest.mark.parametrize("data", [b"", b"{}", b"not json", b"[]", b"\xff"])
def test_malformed_envelopes(data):
    with pytest.raises(ProfileInvalid):
        open_profile(data, KEY)


@pytest.mark.parametrize(
    "mutation", ["payload-name", "invalid-private-material", "duplicate-field"]
)
def test_authenticated_invalid_payload_fails_safely(profile, mutation, caplog):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    envelope = json.loads(seal_profile(profile, KEY))
    encrypted = base64.b64decode(envelope.pop("ciphertext"))
    nonce = base64.b64decode(envelope["nonce"])
    aad = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    payload = json.loads(AESGCM(KEY).decrypt(nonce, encrypted, aad))
    if mutation == "payload-name":
        payload["name"] = "other"
    elif mutation == "invalid-private-material":
        payload["secret_material"]["role_exports"] = {"invalid-role": SENTINEL}
    plaintext = json.dumps(payload).encode()
    if mutation == "duplicate-field":
        plaintext = plaintext[:-1] + b', "name":"other"}'
    envelope["ciphertext"] = base64.b64encode(AESGCM(KEY).encrypt(nonce, plaintext, aad)).decode()
    with pytest.raises(ProfileInvalid) as error:
        open_profile(json.dumps(envelope).encode(), KEY)
    assert "SYNTHETIC-only" not in str(error.value) + repr(error.value) + caplog.text


@pytest.mark.parametrize(
    "name", ["../escape", "x/y", "x\\y", "C:escape", "CON", "nul", "a.", "", "x ", "UPPER"]
)
def test_unsafe_or_alias_names_fail_closed(tmp_path, name):
    with pytest.raises(ProfileInvalid):
        resolve_profile(name, tmp_path)


def test_resolve_profile(tmp_path):
    assert resolve_profile("synthetic", tmp_path) == tmp_path / "synthetic.yleum-recovery"
    with pytest.raises(ProfileInvalid):
        resolve_profile("synthetic", Path("relative"))


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
@pytest.mark.parametrize(
    "home",
    [
        r"\\untrusted\share\profiles",
        r"\\?\C:\profiles",
        r"C:\profiles.",
        r"C:\profiles ",
        r"C:\profile:stream",
    ],
)
def test_nonlocal_or_alias_home_refused_without_access(home, monkeypatch):
    def accessed(path):
        pytest.fail("Untrusted path must be refused before filesystem access")

    monkeypatch.setattr(Path, "lstat", accessed)
    with pytest.raises(ProfileInvalid):
        resolve_profile("synthetic", Path(home))


def test_hardlinked_profile_refused(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic ciphertext placeholder")
    (tmp_path / "synthetic.yleum-recovery").hardlink_to(source)
    with pytest.raises(ProfileInvalid):
        resolve_profile("synthetic", tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction proof")
def test_reparse_home_rejected(tmp_path):
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True, check=False
    )
    assert result.returncode == 0
    try:
        with pytest.raises(ProfileInvalid):
            resolve_profile("synthetic", junction)
    finally:
        junction.rmdir()


@pytest.fixture
def fake_store(monkeypatch, tmp_path):
    monkeypatch.setenv("YLEUM_RECOVERY_HOME", str(tmp_path))
    monkeypatch.setattr(local_unlock, "_require_windows", lambda: None)
    monkeypatch.setattr(local_unlock, "_restrict_acl", lambda path: None)
    monkeypatch.setattr(local_unlock, "_verify_acl", lambda path: None)
    # Independent stores simulate per-user/per-PC DPAPI keys, never the portable encryption key.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    def machine(machine_key):
        def protect(data, entropy):
            return AESGCM(machine_key).encrypt(b"0" * 12, data, entropy)

        def unprotect(data, entropy):
            return AESGCM(machine_key).decrypt(b"0" * 12, data, entropy)

        monkeypatch.setattr(local_unlock, "_dpapi_protect", protect)
        monkeypatch.setattr(local_unlock, "_dpapi_unprotect", unprotect)

    machine(b"a" * 32)
    return tmp_path, machine


def test_two_independent_local_stores_unlock_same_portable_profile(
    profile, fake_store, monkeypatch
):
    first_home, machine = fake_store
    sealed = seal_profile(profile, KEY)
    activate_local_key("synthetic", KEY)
    first_wrapper = (first_home / ".local-keys" / "synthetic.dpapi").read_bytes()
    assert KEY not in first_wrapper
    assert open_profile(sealed, load_local_key("synthetic")) == profile
    machine(b"b" * 32)
    with pytest.raises(LocalUnlockError):
        load_local_key("synthetic")  # Copying a first-PC wrapper is insufficient.
    second_home = first_home / "second-pc"
    second_home.mkdir()
    monkeypatch.setenv("YLEUM_RECOVERY_HOME", str(second_home))
    activate_local_key("synthetic", KEY)  # Owner supplies the independent recovery key.
    assert open_profile(sealed, load_local_key("synthetic")) == profile
    assert (second_home / ".local-keys" / "synthetic.dpapi").read_bytes() != first_wrapper


def test_local_key_name_binding(fake_store):
    home, _ = fake_store
    activate_local_key("synthetic", KEY)
    (home / ".local-keys" / "other.dpapi").write_bytes(
        (home / ".local-keys" / "synthetic.dpapi").read_bytes()
    )
    with pytest.raises(LocalUnlockError):
        load_local_key("other")


def test_acl_failure_prevents_persistence(fake_store, monkeypatch):
    home, _ = fake_store

    def failure(path):
        raise OSError(SENTINEL)

    monkeypatch.setattr(local_unlock, "_restrict_acl", failure)
    with pytest.raises(LocalUnlockError) as error:
        activate_local_key("synthetic", KEY)
    assert "SYNTHETIC-only" not in str(error.value)
    assert not list(home.rglob("*.dpapi"))


def test_preexisting_untrusted_key_directory_not_repaired(fake_store, monkeypatch):
    home, _ = fake_store
    directory = home / ".local-keys"
    directory.mkdir()

    def failure(path):
        if path == directory:
            raise OSError(SENTINEL)

    monkeypatch.setattr(local_unlock, "_verify_acl", failure)
    with pytest.raises(LocalUnlockError):
        activate_local_key("synthetic", KEY)
    assert not list(directory.iterdir())


def test_cleanup_error_has_only_safe_diagnostics(fake_store, monkeypatch):
    def failure(path):
        if path.name.startswith(".key-"):
            raise OSError(SENTINEL)

    def cleanup_failure(path, **kwargs):
        raise OSError(SENTINEL)

    monkeypatch.setattr(local_unlock, "_restrict_acl", failure)
    monkeypatch.setattr(Path, "unlink", cleanup_failure)
    with pytest.raises(LocalUnlockError) as error:
        activate_local_key("synthetic", KEY)
    assert "SYNTHETIC-only" not in str(error.value) + repr(error.value)


def test_unsupported_os_has_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("YLEUM_RECOVERY_HOME", str(tmp_path))
    monkeypatch.setattr(local_unlock, "_platform", lambda: "posix")
    with pytest.raises(LocalUnlockError):
        activate_local_key("synthetic", KEY)
    with pytest.raises(LocalUnlockError):
        load_local_key("synthetic")
    assert list(tmp_path.iterdir()) == []


def test_missing_home_fails_closed(monkeypatch):
    monkeypatch.delenv("YLEUM_RECOVERY_HOME", raising=False)
    with pytest.raises(LocalUnlockError):
        activate_local_key("synthetic", KEY)


def test_cli_secure_activation_and_daily_verify(profile, fake_store, monkeypatch, capsys):
    home, _ = fake_store
    resolve_profile("synthetic", home).write_bytes(seal_profile(profile, KEY))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: base64.b64encode(KEY).decode())
    assert main(["profile", "activate", "--name", "synthetic"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "profile-activated"
    assert main(["profile", "verify", "--name", "synthetic"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "profile-verified"
    assert result["profile"] == "local-profile:synthetic"
    assert SENTINEL not in json.dumps(result)


def test_cli_wrong_expected_name_not_activated(profile, fake_store, monkeypatch, capsys):
    home, _ = fake_store
    resolve_profile("other", home).write_bytes(seal_profile(profile, KEY))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: base64.b64encode(KEY).decode())
    assert main(["profile", "activate", "--name", "other"]) == 2
    output = capsys.readouterr().out
    assert "PROFILE_INVALID" in output
    assert SENTINEL not in output
    assert not (home / ".local-keys").exists()


def test_cli_verify_tamper_refused(profile, fake_store, capsys):
    home, _ = fake_store
    activate_local_key("synthetic", KEY)
    path = resolve_profile("synthetic", home)
    path.write_bytes(seal_profile(profile, b"x" * 32))
    assert main(["profile", "verify", "--name", "synthetic"]) == 2
    output = capsys.readouterr().out
    assert "PROFILE_INVALID" in output
    assert "SYNTHETIC-only" not in output


def test_cli_noninteractive_input_refused(fake_store, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["profile", "activate", "--name", "synthetic"]) == 2
    assert "LOCAL_UNLOCK_UNAVAILABLE" in capsys.readouterr().out


def test_cli_echo_fallback_refused(profile, fake_store, monkeypatch, capsys):
    import getpass

    home, _ = fake_store
    resolve_profile("synthetic", home).write_bytes(seal_profile(profile, KEY))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def fallback(prompt):
        warnings.warn("echo unavailable", getpass.GetPassWarning, stacklevel=2)
        raise AssertionError("Must not reach an echo fallback")

    monkeypatch.setattr("getpass.getpass", fallback)
    assert main(["profile", "activate", "--name", "synthetic"]) == 2
    assert "LOCAL_UNLOCK_UNAVAILABLE" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="Real Windows current-user DPAPI and ACL")
def test_real_windows_activation_current_user(profile, tmp_path, monkeypatch):
    monkeypatch.setenv("YLEUM_RECOVERY_HOME", str(tmp_path))
    activate_local_key("synthetic", KEY)
    assert load_local_key("synthetic") == KEY
    assert open_profile(seal_profile(profile, KEY), load_local_key("synthetic")) == profile
    wrapped = tmp_path / ".local-keys" / "synthetic.dpapi"
    assert KEY not in wrapped.read_bytes()
    local_unlock._verify_acl(wrapped)
    local_unlock._verify_acl(wrapped.parent)
    encrypted = wrapped.read_bytes()
    wrapped.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(LocalUnlockError):
        load_local_key("synthetic")
    activate_local_key("synthetic", KEY)
    assert load_local_key("synthetic") == KEY
    # Permissive inherited ACL is refused on loading; no repair/fallback during reads.
    import subprocess

    result = subprocess.run(
        ["icacls", str(wrapped), "/grant", "*S-1-1-0:(R)"], capture_output=True, check=False
    )
    assert result.returncode == 0
    with pytest.raises(LocalUnlockError):
        load_local_key("synthetic")
