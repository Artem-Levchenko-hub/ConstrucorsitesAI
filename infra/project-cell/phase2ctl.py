#!/usr/bin/env python3
"""Fail-closed data and policy primitives for Project Cell Phase 2.

This program intentionally performs no package installation, systemd mutation,
or Kubernetes apply.  The shell entrypoints call it before every mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

K3S_VERSION = "v1.36.4+k3s1"
POD_CIDR = ipaddress.ip_network("10.42.0.0/16")
SERVICE_CIDR = ipaddress.ip_network("10.43.0.0/16")
RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
TRUSTED_HELLO_IMAGE = (
    "registry.k8s.io/e2e-test-images/agnhost:2.53@"
    "sha256:99c6b4bb4a1e1df3f0b3752168c89358794d02258ebebc26bf21c29399011a85"
)
PINNED_POLICY = {
    "K3S_VERSION": K3S_VERSION,
    "K3S_AMD64_SHA256": "835873f37245fc615f547a2fe2af9402a347875f13fa64a1f136de644955ea3f",
    "K3S_SHA256SUM_AMD64_SHA256": "db1dbdc92f0cb5ccd361348a113f4dff82b1f9194175e5993efc37224a04ba4d",
    "K3S_INSTALL_SH_SHA256": "46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad",
    "K3S_INSTALL_SH_URL": "https://raw.githubusercontent.com/k3s-io/k3s/v1.36.4%2Bk3s1/install.sh",
    "K3S_SHA256SUM_AMD64_URL": "https://github.com/k3s-io/k3s/releases/download/v1.36.4%2Bk3s1/sha256sum-amd64.txt",
    "TRUSTED_HELLO_IMAGE": TRUSTED_HELLO_IMAGE,
    "KATA_NEXT_GATE_VERSION": "4.1.0",
    "KATA_NEXT_GATE_AMD64_SHA256": "3dc6b69c4acb787b967b04b64599a20d02a8beb1a8eaab3084110df9d0b08c96",
}
MIN_AVAILABLE_MEMORY = 6 * 1024**3
MIN_FREE_DISK = 60 * 1024**3
REQUIRED_EVIDENCE = {
    "active-operations.json",
    "backup.json",
    "capacity.json",
    "cni.json",
    "dns.json",
    "docker-networks.json",
    "docker-state.json",
    "filesystem-state.json",
    "firewall-v4.rules",
    "firewall-v6.rules",
    "listeners.txt",
    "modules.txt",
    "nftables.rules",
    "package-versions.txt",
    "production-health.json",
    "routes.json",
    "restore-contract.json",
    "runtime-versions.txt",
    "sysctls.txt",
    "systemd-state.json",
}
METADATA_FILES = {"manifest.json", "bundle-id.txt"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRUSTED_HELLO_MANIFEST_SHA256 = "0a3e8298ab813b5e4826520da06007c534302637fdbf8b5cc1a550f58f73eaff"


class GateError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise GateError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_regular_nofollow(path: Path, label: str = "file") -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open {label} without following symlinks: {path}: {exc}")
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        fail(f"file changed type while opening: {path}")
    with os.fdopen(descriptor, "rb") as source:
        payload = source.read()
    return payload, info


def sha256_file(path: Path) -> str:
    payload, _ = read_regular_nofollow(path)
    return sha256_bytes(payload)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_json(path: Path) -> Any:
    try:
        payload, _ = read_regular_nofollow(path, "JSON input")
        return json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON {path}: {exc}")


def require_canonical_json(path: Path, payload: Any, label: str) -> None:
    if read_regular_nofollow(path, label)[0] != canonical_json(payload):
        fail(f"{label} must be canonical JSON")


def require_regular(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        fail(f"{label} is unavailable: {path}: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file: {path}")
    if os.environ.get("PHASE2_REQUIRE_ROOT_OWNERSHIP") == "1" and (info.st_uid != 0 or info.st_gid != 0):
        fail(f"{label} must be owned by root:root: {path}")
    return info


def require_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        fail(f"{label} is unavailable: {path}: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{label} must be a non-symlink directory: {path}")
    if os.environ.get("PHASE2_REQUIRE_ROOT_OWNERSHIP") == "1" and (info.st_uid != 0 or info.st_gid != 0):
        fail(f"{label} must be owned by root:root: {path}")
    enforce_modes = not (
        os.name == "nt" and os.environ.get("PHASE2_TEST_ALLOW_WINDOWS_ACL") == "1"
    )
    if enforce_modes and stat.S_IMODE(info.st_mode) & 0o077:
        fail(f"{label} exposes group/world permissions: {path}")
    return info


def require_protected_file(path: Path, label: str, *, readonly: bool = False) -> None:
    info = require_regular(path, label)
    mode = stat.S_IMODE(info.st_mode)
    enforce_modes = not (
        os.name == "nt" and os.environ.get("PHASE2_TEST_ALLOW_WINDOWS_ACL") == "1"
    )
    if enforce_modes and mode & 0o077:
        fail(f"{label} exposes group/world permissions: {path}")
    if enforce_modes and readonly and mode & 0o222:
        fail(f"{label} must be read-only: {path}")


def atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() or path.is_symlink():
        fail(f"refusing to overwrite output: {path}")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def bundle_paths(bundle: Path) -> list[Path]:
    evidence = bundle / "evidence"
    require_directory(evidence, "evidence directory")
    discovered: list[Path] = []
    for path in sorted(bundle.rglob("*")):
        if path.parent == bundle and path.name in METADATA_FILES:
            continue
        if path.is_dir() and not path.is_symlink():
            require_directory(path, "bundle directory")
            continue
        require_regular(path, "bundle entry")
        discovered.append(path)
    names = {
        path.relative_to(evidence).as_posix()
        for path in discovered
        if path.is_relative_to(evidence)
    }
    missing = REQUIRED_EVIDENCE - names
    if missing:
        fail(f"required evidence is missing: {', '.join(sorted(missing))}")
    return discovered


def make_entries(bundle: Path, *, force_readonly: bool) -> list[dict[str, Any]]:
    paths = bundle_paths(bundle)
    if force_readonly:
        for path in paths:
            os.chmod(path, 0o400)
    entries: list[dict[str, Any]] = []
    for path in paths:
        require_protected_file(path, "bundle entry", readonly=True)
        payload, info = read_regular_nofollow(path, "bundle entry")
        entries.append(
            {
                "path": path.relative_to(bundle).as_posix(),
                "sha256": sha256_bytes(payload),
                "size": info.st_size,
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "uid": info.st_uid,
                "gid": info.st_gid,
                "type": "file",
            }
        )
    return entries


def bundle_identity(hostname: str, entries: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json({"format": 1, "hostname": hostname, "entries": entries}))


def command_manifest(args: argparse.Namespace) -> None:
    bundle = Path(args.bundle)
    require_directory(bundle, "bundle")
    if any((bundle / name).exists() or (bundle / name).is_symlink() for name in METADATA_FILES):
        fail("bundle metadata already exists")
    if not args.hostname or any(character.isspace() for character in args.hostname):
        fail("hostname must be a non-empty token")
    entries = make_entries(bundle, force_readonly=True)
    bundle_id = bundle_identity(args.hostname, entries)
    manifest = {
        "format": 1,
        "hostname": args.hostname,
        "bundle_id": bundle_id,
        "entries": entries,
    }
    atomic_write(bundle / "manifest.json", canonical_json(manifest), 0o400)
    atomic_write(bundle / "bundle-id.txt", f"{bundle_id}\n".encode(), 0o400)
    print(bundle_id)


def verify_bundle(bundle_value: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_value)
    require_directory(bundle, "bundle")
    manifest_path = bundle / "manifest.json"
    bundle_id_path = bundle / "bundle-id.txt"
    require_protected_file(manifest_path, "bundle manifest", readonly=True)
    require_protected_file(bundle_id_path, "bundle id", readonly=True)
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "hostname", "bundle_id", "entries"}:
        fail("bundle manifest has an unexpected schema")
    if manifest["format"] != 1 or not isinstance(manifest["hostname"], str):
        fail("bundle manifest format or hostname is invalid")
    if not isinstance(manifest["entries"], list):
        fail("bundle manifest entries are invalid")
    actual_entries = make_entries(bundle, force_readonly=False)
    if manifest["entries"] != actual_entries:
        fail("bundle evidence bytes, paths, modes, or sizes do not match the manifest")
    evidence = bundle / "evidence"
    firewall_payloads = [
        (evidence / "firewall-v4.rules").read_text(encoding="utf-8", errors="replace"),
        (evidence / "firewall-v6.rules").read_text(encoding="utf-8", errors="replace"),
        (evidence / "nftables.rules").read_text(encoding="utf-8", errors="replace"),
    ]
    if any(value.startswith("capture-failed:") for value in firewall_payloads):
        fail("firewall evidence contains a failed capture")
    if all(value.startswith("absent:") for value in firewall_payloads):
        fail("bundle has no restorable firewall backend")
    restore_contract = load_json(evidence / "restore-contract.json")
    if not isinstance(restore_contract, dict) or restore_contract.get("format") != 1 or not isinstance(restore_contract.get("entries"), list):
        fail("restore contract schema is invalid")
    restore_entries = restore_contract["entries"]
    seen: set[str] = set()
    for entry in restore_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].startswith("/"):
            fail("restore contract entry path/schema is invalid")
        if entry["path"] in seen or ".." in Path(entry["path"]).parts:
            fail("restore contract contains duplicate/traversal paths")
        seen.add(entry["path"])
        kind = entry.get("type")
        expected = {"path", "type"} if kind == "absent" else {"path", "type", "uid", "gid", "mode"}
        if kind == "file": expected |= {"size", "sha256"}
        elif kind == "symlink": expected |= {"target"}
        elif kind not in {"absent", "directory"}:
            fail("restore contract entry type is invalid")
        if set(entry) != expected:
            fail("restore contract entry fields are invalid")
        if kind == "file" and not SHA256_RE.fullmatch(str(entry.get("sha256", ""))):
            fail("restore contract file digest is invalid")
    required_cni_roots = {"/etc/cni", "/opt/cni", "/var/lib/cni"}
    if not required_cni_roots.issubset(seen):
        fail("restore contract does not cover every CNI root")

    systemd_state = load_json(evidence / "systemd-state.json")
    required_units = {
        "docker.service", "nginx.service", "omnia-orchestrator.service", "k3s.service"
    }
    active_states = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
    enabled_states = {
        "enabled", "enabled-runtime", "disabled", "static", "masked", "masked-runtime",
        "indirect", "generated", "transient", "alias", "linked", "linked-runtime",
    }
    if not isinstance(systemd_state, dict) or set(systemd_state) != required_units:
        fail("systemd baseline schema is invalid")
    for unit, state_value in systemd_state.items():
        if (
            not isinstance(state_value, dict)
            or set(state_value) != {"active", "enabled"}
            or state_value["active"] not in active_states
            or state_value["enabled"] not in enabled_states
        ):
            fail(f"systemd baseline state is unknown: {unit}")

    sysctl_lines = read_regular_nofollow(evidence / "sysctls.txt", "sysctl evidence")[0].decode(
        "utf-8", errors="strict"
    ).splitlines()
    if not sysctl_lines or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]+ = .*", line) for line in sysctl_lines
    ):
        fail("sysctl evidence is empty, failed, or malformed")
    module_lines = read_regular_nofollow(evidence / "modules.txt", "module evidence")[0].decode(
        "ascii", errors="strict"
    ).splitlines()
    if not module_lines or any(not re.fullmatch(r"[A-Za-z0-9_]+", line) for line in module_lines):
        fail("module evidence is empty, failed, or malformed")
    tar_path = bundle / "restore" / "host-files.tar"
    require_protected_file(tar_path, "restore archive", readonly=True)
    contract_by_path = {entry["path"]: entry for entry in restore_entries}
    try:
        with tarfile.open(tar_path, "r:") as archive:
            for member in archive.getmembers():
                absolute = "/" + member.name.rstrip("/")
                contract = contract_by_path.get(absolute)
                if contract is None or contract["type"] == "absent":
                    fail(f"restore archive member is outside the normalized contract: {member.name}")
                if member.uid != contract["uid"] or member.gid != contract["gid"] or f"{member.mode:04o}" != contract["mode"]:
                    fail(f"restore archive metadata differs from contract: {member.name}")
                if member.isfile():
                    extracted = archive.extractfile(member)
                    if extracted is None or sha256_bytes(extracted.read()) != contract.get("sha256"):
                        fail(f"restore archive bytes differ from contract: {member.name}")
                elif member.issym():
                    if contract["type"] != "symlink" or member.linkname != contract.get("target"):
                        fail(f"restore archive symlink differs from contract: {member.name}")
                elif member.isdir():
                    if contract["type"] != "directory":
                        fail(f"restore archive directory differs from contract: {member.name}")
                else:
                    fail(f"restore archive contains unsupported type: {member.name}")
    except (tarfile.TarError, OSError) as exc:
        fail(f"restore archive is invalid: {exc}")
    expected_id = bundle_identity(manifest["hostname"], actual_entries)
    try:
        recorded_id = read_regular_nofollow(bundle_id_path, "bundle id")[0].decode("ascii").strip()
    except UnicodeDecodeError:
        fail("bundle id is not ASCII")
    if manifest["bundle_id"] != expected_id or recorded_id != expected_id:
        fail("bundle identity does not match its evidence")
    return manifest


def command_verify_bundle(args: argparse.Namespace) -> None:
    print(verify_bundle(args.bundle)["bundle_id"])


def parse_recent_timestamp(value: Any, *, max_age: timedelta = timedelta(hours=24)) -> str:
    if not isinstance(value, str):
        fail("attestation verified_at is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("attestation verified_at is invalid")
    if parsed.tzinfo is None:
        fail("attestation verified_at must include a timezone")
    now = datetime.now(UTC)
    parsed = parsed.astimezone(UTC)
    if parsed > now + timedelta(minutes=5) or parsed < now - max_age:
        fail("attestation is stale or from the future")
    return parsed.isoformat().replace("+00:00", "Z")


def verify_signature(attestation: Path, signature: Path, public_key: Path) -> None:
    require_protected_file(attestation, "attestation", readonly=True)
    require_protected_file(signature, "attestation signature", readonly=True)
    require_protected_file(public_key, "attestation public key", readonly=True)
    completed = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(public_key),
            "-signature",
            str(signature),
            str(attestation),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0 or "Verified OK" not in completed.stdout:
        fail("attestation detached signature verification failed")


def load_trust_policy(path_value: str | Path, public_key: Path) -> tuple[dict[str, Any], Path]:
    path = Path(path_value)
    require_protected_file(path, "Phase 2 trust policy", readonly=True)
    if not (os.name == "nt" and os.environ.get("PHASE2_TEST_ALLOW_WINDOWS_ACL") == "1"):
        if stat.S_IMODE(path.lstat().st_mode) != 0o400:
            fail("Phase 2 trust policy must be mode 0400")
    policy = load_json(path)
    required = {
        "format",
        "production_hostname",
        "verifier_id",
        "verifier_public_key_sha256",
        "recovery_certificate_sha256",
        "allowed_offhost_locations",
    }
    if not isinstance(policy, dict) or set(policy) != required or policy.get("format") != 1:
        fail("Phase 2 trust policy schema is invalid")
    if policy.get("verifier_public_key_sha256") != sha256_file(public_key):
        fail("verifier public key does not match the pinned trust policy fingerprint")
    locations = policy.get("allowed_offhost_locations")
    if (
        not isinstance(policy.get("production_hostname"), str)
        or not policy["production_hostname"]
        or not isinstance(policy.get("verifier_id"), str)
        or not policy["verifier_id"]
        or not SHA256_RE.fullmatch(str(policy.get("recovery_certificate_sha256", "")))
    ):
        fail("Phase 2 trust policy identity/fingerprint fields are invalid")
    if not isinstance(locations, list) or not locations or not all(
        isinstance(item, str) and item for item in locations
    ):
        fail("trust policy off-host location allowlist is invalid")
    return policy, path


def command_verify_recipient(args: argparse.Namespace) -> None:
    certificate = Path(args.certificate)
    require_protected_file(certificate, "recovery recipient certificate")
    payload = certificate.read_bytes()
    if re.search(br"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----", payload):
        fail("recovery recipient PEM must not contain a private key")
    policy_path = Path(args.trust_policy)
    require_protected_file(policy_path, "Phase 2 trust policy", readonly=True)
    if not (os.name == "nt" and os.environ.get("PHASE2_TEST_ALLOW_WINDOWS_ACL") == "1"):
        if stat.S_IMODE(policy_path.lstat().st_mode) != 0o400:
            fail("Phase 2 trust policy must be mode 0400")
    policy = load_json(policy_path)
    expected_policy_keys = {
        "format", "production_hostname", "verifier_id", "verifier_public_key_sha256",
        "recovery_certificate_sha256", "allowed_offhost_locations",
    }
    if (
        not isinstance(policy, dict)
        or set(policy) != expected_policy_keys
        or policy.get("format") != 1
        or policy.get("recovery_certificate_sha256") != sha256_file(certificate)
    ):
        fail("recovery certificate does not match the pinned trust-policy fingerprint")
    completed = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        fail("recovery recipient is not a valid X.509 certificate")
    print(sha256_file(certificate))


def validate_attestation_payload(
    attestation: Any,
    *,
    kind: str,
    bundle: dict[str, Any],
    policy: dict[str, Any],
    ciphertext: Path | None,
) -> str:
    common = {"kind", "bundle_id", "production_hostname", "verifier_id", "verified_at"}
    expected = common | (
        {"ciphertext_sha256", "location", "checksum", "decrypt", "manifest", "restore_test"}
        if kind == "offhost"
        else {"console_login", "rescue_boot"}
    )
    if not isinstance(attestation, dict) or set(attestation) != expected or attestation.get("kind") != kind:
        fail("attestation kind does not match the requested gate")
    if attestation.get("bundle_id") != bundle["bundle_id"]:
        fail("attestation is for a different bundle")
    if (
        attestation.get("production_hostname") != bundle["hostname"]
        or attestation.get("production_hostname") != policy["production_hostname"]
    ):
        fail("attestation production hostname does not match the bundle")
    verifier_id = attestation.get("verifier_id")
    if verifier_id != policy["verifier_id"] or verifier_id == bundle["hostname"]:
        fail("attestation verifier identity does not match the independent trust anchor")
    verified_at = parse_recent_timestamp(attestation.get("verified_at"))
    if kind == "offhost":
        if ciphertext is None:
            fail("off-host verification requires the encrypted ciphertext")
        require_regular(ciphertext, "encrypted rollback bundle")
        digest = sha256_file(ciphertext)
        if attestation.get("ciphertext_sha256") != digest:
            fail("off-host attestation ciphertext checksum does not match")
        location = attestation.get("location")
        if not isinstance(location, str):
            fail("off-host location is missing")
        split = urlsplit(location)
        if split.scheme.lower() not in {"s3", "gs", "https"} or not split.hostname:
            fail("off-host location must not be a local path")
        hostname = split.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            fail("off-host location resolves to a local identity")
        try:
            remote_ip = ipaddress.ip_address(hostname)
        except ValueError:
            remote_ip = None
        if remote_ip is not None and not remote_ip.is_global:
            fail("off-host location must not use a loopback/private/link-local address")
        if not any(location.startswith(prefix) for prefix in policy["allowed_offhost_locations"]):
            fail("off-host location is outside the pinned trust-policy allowlist")
        for field in ("checksum", "decrypt", "manifest", "restore_test"):
            if attestation.get(field) != "passed":
                fail(f"off-host attestation gate did not pass: {field}")
    elif kind == "provider_rescue":
        for field in ("console_login", "rescue_boot"):
            if attestation.get(field) != "passed":
                fail(f"provider rescue gate did not pass: {field}")
    else:
        fail("unsupported attestation kind")
    return verified_at


def command_verify_attestation(args: argparse.Namespace) -> None:
    bundle = verify_bundle(args.bundle)
    attestation_path = Path(args.attestation)
    signature_path = Path(args.signature)
    public_key_path = Path(args.public_key)
    policy, trust_path = load_trust_policy(args.trust_policy, public_key_path)
    verify_signature(attestation_path, signature_path, public_key_path)
    attestation = load_json(attestation_path)
    require_canonical_json(attestation_path, attestation, "signed attestation")
    ciphertext = Path(args.ciphertext) if args.ciphertext else None
    validate_attestation_payload(
        attestation,
        kind=args.kind,
        bundle=bundle,
        policy=policy,
        ciphertext=ciphertext,
    )
    marker = {
        "kind": args.kind,
        "bundle_id": bundle["bundle_id"],
        "attestation_sha256": sha256_file(attestation_path),
        "signature_sha256": sha256_file(signature_path),
        "sources": {
            "attestation": str(attestation_path),
            "signature": str(signature_path),
            "public_key": str(public_key_path),
            "ciphertext": str(ciphertext) if ciphertext else None,
        },
        "public_key_sha256": sha256_file(public_key_path),
        "trust_policy": str(trust_path),
        "trust_policy_sha256": sha256_file(trust_path),
    }
    atomic_write(Path(args.output), canonical_json(marker), 0o400)
    print(marker["bundle_id"])


def validate_marker(
    bundle_value: str | Path,
    marker_value: str | Path,
    kind: str,
    expected_trust_policy: str | None = None,
) -> dict[str, Any]:
    bundle = verify_bundle(bundle_value)
    marker_path = Path(marker_value)
    require_protected_file(marker_path, f"{kind} verified marker", readonly=True)
    marker = load_json(marker_path)
    expected_marker_keys = {
        "kind", "bundle_id", "attestation_sha256", "signature_sha256", "sources",
        "public_key_sha256", "trust_policy", "trust_policy_sha256",
    }
    if (
        not isinstance(marker, dict)
        or set(marker) != expected_marker_keys
        or marker.get("kind") != kind
    ):
        fail(f"{kind} marker has the wrong kind")
    if marker.get("bundle_id") != bundle["bundle_id"]:
        fail(f"{kind} marker is for a different bundle")
    if not SHA256_RE.fullmatch(str(marker.get("attestation_sha256", ""))):
        fail(f"{kind} marker attestation checksum is invalid")
    if not SHA256_RE.fullmatch(str(marker.get("signature_sha256", ""))):
        fail(f"{kind} marker signature checksum is invalid")
    sources = marker.get("sources")
    if not isinstance(sources, dict):
        fail(f"{kind} marker does not retain signed source artifacts")
    required = {"attestation", "signature", "public_key", "ciphertext"}
    if set(sources) != required:
        fail(f"{kind} marker source schema is invalid")
    paths: dict[str, Path | None] = {}
    for name, value in sources.items():
        if value is None and name == "ciphertext" and kind == "provider_rescue":
            paths[name] = None
            continue
        if not isinstance(value, str):
            fail(f"{kind} marker source path is invalid: {name}")
        path = Path(value)
        require_regular(path, f"{kind} source {name}")
        paths[name] = path
    assert paths["attestation"] and paths["signature"] and paths["public_key"]
    if sha256_file(paths["attestation"]) != marker["attestation_sha256"]:
        fail(f"{kind} attestation bytes changed after verification")
    if sha256_file(paths["signature"]) != marker["signature_sha256"]:
        fail(f"{kind} signature bytes changed after verification")
    if sha256_file(paths["public_key"]) != marker.get("public_key_sha256"):
        fail(f"{kind} public key bytes changed after verification")
    trust_value = marker.get("trust_policy")
    if not isinstance(trust_value, str):
        fail(f"{kind} marker does not retain its trust anchor")
    trust_path = Path(trust_value)
    if expected_trust_policy is not None and trust_path.absolute() != Path(expected_trust_policy).absolute():
        fail(f"{kind} marker uses an unpinned trust-policy path")
    if sha256_file(trust_path) != marker.get("trust_policy_sha256"):
        fail(f"{kind} trust policy changed after verification")
    policy, _ = load_trust_policy(trust_path, paths["public_key"])
    verify_signature(paths["attestation"], paths["signature"], paths["public_key"])
    attestation = load_json(paths["attestation"])
    require_canonical_json(paths["attestation"], attestation, "signed attestation")
    validate_attestation_payload(
        attestation,
        kind=kind,
        bundle=bundle,
        policy=policy,
        ciphertext=paths["ciphertext"],
    )
    return marker


def command_validate_marker(args: argparse.Namespace) -> None:
    print(
        validate_marker(
            args.bundle,
            args.marker,
            args.kind,
            args.expected_trust_policy,
        )["bundle_id"]
    )


def validate_network_inputs(
    bind_address_value: str,
    admin_cidr_value: str,
    host_addresses: list[str],
    networks: list[str],
) -> ipaddress.IPv4Address:
    try:
        bind = ipaddress.ip_address(bind_address_value)
        admin = ipaddress.ip_network(admin_cidr_value, strict=True)
    except ValueError as exc:
        fail(f"invalid bind address or administration CIDR: {exc}")
    if not isinstance(bind, ipaddress.IPv4Address) or not isinstance(admin, ipaddress.IPv4Network):
        fail("Phase 2 pilot accepts an IPv4 private administration address only")
    if not any(bind in network for network in RFC1918_NETWORKS):
        fail("K3s bind address must be an RFC1918 address")
    if not any(admin.subnet_of(network) for network in RFC1918_NETWORKS) or bind not in admin:
        fail("K3s bind address is outside the approved private administration CIDR")
    if bind_address_value not in host_addresses:
        fail("K3s bind address is not present on the host")
    if admin.overlaps(POD_CIDR) or admin.overlaps(SERVICE_CIDR):
        fail("administration CIDR overlaps the K3s pod/service CIDR")
    if POD_CIDR.overlaps(SERVICE_CIDR):
        fail("pod and service CIDRs overlap")
    for value in networks:
        if value == "default":
            value = "0.0.0.0/0"
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            fail(f"invalid observed network {value}: {exc}")
        if network.prefixlen == 0:
            continue
        if POD_CIDR.overlaps(network) or SERVICE_CIDR.overlaps(network):
            fail(f"K3s CIDR overlaps observed network: {network}")
    return bind


def command_install_preflight(args: argparse.Namespace) -> None:
    gate = load_json(Path(args.gate))
    if not isinstance(gate, dict):
        fail("install gate must be a JSON object")
    checks = {
        "available_memory_bytes": (int, lambda value: value >= MIN_AVAILABLE_MEMORY),
        "free_disk_bytes": (int, lambda value: value >= MIN_FREE_DISK),
        "swap_used_bytes": (int, lambda value: value == 0),
        "active_operations": (int, lambda value: value == 0),
    }
    for name, (expected_type, predicate) in checks.items():
        value = gate.get(name)
        if type(value) is not expected_type or not predicate(value):
            fail(f"install gate failed: {name}")
    for name in ("production_health", "backup", "restorable_firewall"):
        if gate.get(name) != "passed":
            fail(f"install gate failed: {name}")
    expected_lock = "not-held-read-only" if args.read_only else "held"
    if gate.get("maintenance_lock") != expected_lock or gate.get("deadman") != "armed":
        fail("install gate requires the maintenance lock and armed dead-man")
    parse_recent_timestamp(gate.get("active_operations_verified_at"), max_age=timedelta(minutes=5))
    parse_recent_timestamp(gate.get("backup_verified_at"), max_age=timedelta(hours=24))
    if gate.get("listener_6443") != "absent" or gate.get("baseline_k3s_state") != "absent":
        fail("install gate requires absent baseline K3s state and listener 6443")
    if gate.get("background_quiescence") != "passed":
        fail("background/worker quiescence is not proven")
    expected_revision = gate.get("expected_revision")
    if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        fail("expected server revision is invalid")
    if gate.get("server_revision") != expected_revision:
        fail("server revision does not match the expected revision")
    installed = gate.get("k3s_installed_version")
    if installed not in (None, K3S_VERSION):
        fail("an unexpected K3s version is already installed")
    host_addresses = gate.get("host_addresses")
    docker_networks = gate.get("docker_networks")
    host_routes = gate.get("host_routes")
    if not all(isinstance(value, list) and all(isinstance(item, str) for item in value) for value in (host_addresses, docker_networks, host_routes)):
        fail("install gate network evidence is invalid")
    validate_network_inputs(
        args.bind_address,
        args.admin_cidr,
        host_addresses,
        docker_networks + host_routes,
    )
    print("install preflight passed")


def command_render_config(args: argparse.Namespace) -> None:
    bind = validate_network_inputs(
        args.bind_address,
        args.admin_cidr,
        args.host_address,
        args.network,
    )
    payload = f'''# Generated by infra/project-cell/phase2ctl.py; do not hand-edit.
write-kubeconfig-mode: "0600"
bind-address: "{bind}"
advertise-address: "{bind}"
node-ip: "{bind}"
tls-san:
  - "{bind}"
tls-san-security: true
cluster-cidr: "{POD_CIDR}"
service-cidr: "{SERVICE_CIDR}"
cluster-dns: "10.43.0.10"
flannel-backend: "vxlan"
secrets-encryption: true
disable:
  - traefik
  - servicelb
'''.encode()
    output = Path(args.output).resolve()
    atomic_write(output, payload, 0o600)
    print(output)


def command_validate_hello(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    if sha256_file(manifest_path) != TRUSTED_HELLO_MANIFEST_SHA256:
        fail("trusted hello manifest bytes differ from the compiled allowlist")
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("apiVersion") != "v1" or manifest.get("kind") != "List":
        fail("trusted hello must be one Kubernetes List")
    items = manifest.get("items")
    if not isinstance(items, list) or len(items) != 3:
        fail("trusted hello must contain exactly three resources")
    kinds = {item.get("kind"): item for item in items if isinstance(item, dict)}
    if set(kinds) != {"Namespace", "Deployment", "Service"}:
        fail("trusted hello may contain only Namespace, Deployment, and Service")
    namespace = kinds["Namespace"]
    if namespace.get("metadata", {}).get("name") != "omnia-project-cell-system":
        fail("trusted hello namespace is invalid")
    labels = namespace.get("metadata", {}).get("labels", {})
    expected_labels = {
        "pod-security.kubernetes.io/enforce": "restricted",
        "pod-security.kubernetes.io/enforce-version": "v1.36",
        "pod-security.kubernetes.io/audit": "restricted",
        "pod-security.kubernetes.io/warn": "restricted",
    }
    if labels != expected_labels:
        fail("trusted hello namespace Pod Security labels are not exact")
    deployment = kinds["Deployment"]
    service = kinds["Service"]
    if deployment.get("metadata", {}).get("namespace") != "omnia-project-cell-system":
        fail("trusted hello Deployment is outside the trusted namespace")
    if service.get("metadata", {}).get("namespace") != "omnia-project-cell-system":
        fail("trusted hello Service is outside the trusted namespace")
    service_spec = service.get("spec", {})
    if service_spec.get("type", "ClusterIP") != "ClusterIP" or "externalIPs" in service_spec:
        fail("trusted hello Service must be ClusterIP-only")
    pod_spec = deployment.get("spec", {}).get("template", {}).get("spec", {})
    if pod_spec.get("automountServiceAccountToken") is not False:
        fail("trusted hello must not mount a service-account token")
    if pod_spec.get("hostNetwork") or pod_spec.get("hostPID") or pod_spec.get("hostIPC"):
        fail("trusted hello may not share host namespaces")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        fail("trusted hello must have exactly one container")
    container = containers[0]
    if container.get("image") != TRUSTED_HELLO_IMAGE:
        fail("trusted hello image is not the approved digest")
    if container.get("args") != ["netexec", "--http-port=8080"]:
        fail("trusted hello command is not the approved netexec server")
    security = container.get("securityContext", {})
    required_security = {
        "allowPrivilegeEscalation": False,
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
    }
    if any(security.get(key) != value for key, value in required_security.items()):
        fail("trusted hello container security context is incomplete")
    if security.get("capabilities", {}).get("drop") != ["ALL"]:
        fail("trusted hello must drop all Linux capabilities")
    if pod_spec.get("securityContext", {}).get("seccompProfile", {}).get("type") != "RuntimeDefault":
        fail("trusted hello must use RuntimeDefault seccomp")
    resources = container.get("resources", {})
    if not isinstance(resources.get("requests"), dict) or not isinstance(resources.get("limits"), dict):
        fail("trusted hello must set requests and limits")
    print("trusted hello manifest passed")


def command_compare(args: argparse.Namespace) -> None:
    before = Path(args.before)
    after = Path(args.after)
    require_regular(before, "before snapshot")
    require_regular(after, "after snapshot")
    before_bytes = before.read_bytes()
    after_bytes = after.read_bytes()
    if before_bytes != after_bytes:
        detail = "snapshot bytes differ"
        try:
            before_json = json.loads(before_bytes)
            after_json = json.loads(after_bytes)
            if isinstance(before_json, dict) and isinstance(after_json, dict):
                keys = sorted(key for key in set(before_json) | set(after_json) if before_json.get(key) != after_json.get(key))
                detail = f"snapshot bytes differ in: {', '.join(keys)}"
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        fail(detail)
    print(sha256_bytes(before_bytes))


def command_snapshot(args: argparse.Namespace) -> None:
    source = Path(args.input_dir)
    require_directory(source, "snapshot input")
    entries: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        require_regular(path, "snapshot input entry")
        entries[path.relative_to(source).as_posix()] = sha256_file(path)
    atomic_write(Path(args.output).resolve(), canonical_json(entries), 0o600)


INSTALLED_PATHS = (
    "/usr/local/bin/k3s",
    "/usr/local/bin/k3s-killall.sh",
    "/usr/local/bin/k3s-uninstall.sh",
    "/etc/rancher/k3s/config.yaml",
    "/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-project-cell-reserves.conf",
    "/etc/systemd/system/k3s.service",
)


def installed_entries() -> list[dict[str, Any]]:
    entries = []
    for value in INSTALLED_PATHS:
        test_root = os.environ.get("PHASE2_TEST_INSTALLED_ROOT") if os.environ.get("PHASE2_TEST_MODE") == "1" else None
        path = Path(test_root) / value.lstrip("/") if test_root else Path(value)
        info = require_regular(path, "installed Phase 2 artifact")
        test_mode = os.environ.get("PHASE2_TEST_MODE") == "1"
        owner_bad = (info.st_uid != 0 or info.st_gid != 0) and not test_mode
        if owner_bad or (stat.S_IMODE(info.st_mode) & 0o022 and not test_mode):
            fail(f"installed Phase 2 artifact owner/mode is unsafe: {path}")
        entries.append(
            {
                "path": value,
                "type": "file",
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "size": info.st_size,
                "sha256": sha256_file(path),
            }
        )
    return entries


def command_installed_manifest(args: argparse.Namespace) -> None:
    bundle = verify_bundle(args.bundle)
    payload = {
        "format": 1,
        "bundle_id": bundle["bundle_id"],
        "k3s_version": K3S_VERSION,
        "entries": installed_entries(),
    }
    atomic_write(Path(args.output), canonical_json(payload), 0o400)
    print(sha256_bytes(canonical_json(payload)))


def command_verify_installed(args: argparse.Namespace) -> None:
    path = Path(args.manifest)
    require_protected_file(path, "installed-state manifest", readonly=True)
    payload = load_json(path)
    if not isinstance(payload, dict) or set(payload) != {"format", "bundle_id", "k3s_version", "entries"}:
        fail("installed-state manifest schema is invalid")
    if payload.get("format") != 1 or payload.get("k3s_version") != K3S_VERSION:
        fail("installed-state manifest policy is invalid")
    if payload.get("entries") != installed_entries():
        fail("installed Phase 2 artifact bytes/owner/mode differ from the manifest")
    print(sha256_file(path))


def command_validate_postflight(args: argparse.Namespace) -> None:
    fail("unsigned postflight validation is disabled; use verify-postflight with signed evidence")


def validate_postflight_semantics(
    *,
    armed: Any,
    postflight: Any,
    policy: dict[str, Any],
    evidence: dict[str, tuple[Path, str]],
) -> dict[str, dict[str, str]]:
    expected_keys = {
        "kind", "bundle_id", "production_hostname", "verifier_id", "verified_at",
        "started_at", "finished_at", "k3s_version",
        "trusted_hello_evidence_sha256", "production_health_evidence_sha256",
        "rollback_evidence_sha256", "deadman_rehearsal", "rollback_byte_compare",
    }
    if not isinstance(postflight, dict) or set(postflight) != expected_keys:
        fail("signed postflight schema is invalid")
    if not isinstance(armed, dict) or not isinstance(armed.get("bundle_id"), str):
        fail("armed state schema is invalid")
    if postflight.get("kind") != "postflight" or postflight.get("bundle_id") != armed.get("bundle_id"):
        fail("signed postflight does not match the armed bundle")
    if (
        postflight.get("production_hostname") != armed.get("production_hostname")
        or postflight.get("production_hostname") != policy["production_hostname"]
        or postflight.get("verifier_id") != policy["verifier_id"]
    ):
        fail("signed postflight identity does not match the trust anchor")
    verified_at = datetime.fromisoformat(
        parse_recent_timestamp(postflight.get("verified_at")).replace("Z", "+00:00")
    )
    try:
        started = datetime.fromisoformat(str(postflight["started_at"]).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(postflight["finished_at"]).replace("Z", "+00:00"))
    except ValueError:
        fail("postflight evidence timestamps are invalid")
    if (
        started.tzinfo is None
        or finished.tzinfo is None
        or (finished - started).total_seconds() < 900
    ):
        fail("postflight evidence window must prove at least 900 real seconds")
    started = started.astimezone(UTC)
    finished = finished.astimezone(UTC)
    if finished > datetime.now(UTC) + timedelta(minutes=5) or finished < datetime.now(UTC) - timedelta(hours=24):
        fail("postflight finished_at is stale or in the future")
    if verified_at < finished or verified_at > finished + timedelta(minutes=5):
        fail("postflight verification timestamp is inconsistent with the signed evidence window")
    if postflight.get("k3s_version") != K3S_VERSION:
        fail("postflight K3s version is invalid")
    if postflight.get("deadman_rehearsal") != "passed" or postflight.get("rollback_byte_compare") != "passed":
        fail("postflight rollback/dead-man evidence is not passed")
    sources: dict[str, dict[str, str]] = {}
    for name, (path, field) in evidence.items():
        require_protected_file(path, name, readonly=True)
        digest = sha256_file(path)
        if digest != postflight.get(field):
            fail(f"postflight evidence hash mismatch: {name}")
        evidence_payload = load_json(path)
        if not isinstance(evidence_payload, dict) or evidence_payload.get("status") != "passed":
            fail(f"postflight evidence is not passed: {name}")
        sources[name] = {"path": str(path), "sha256": digest}
    return sources


def command_verify_postflight(args: argparse.Namespace) -> None:
    armed_path = Path(args.armed)
    require_protected_file(armed_path, "armed state")
    armed = load_json(armed_path)
    postflight_path = Path(args.postflight)
    signature_path = Path(args.signature)
    public_key_path = Path(args.public_key)
    policy, trust_path = load_trust_policy(args.trust_policy, public_key_path)
    verify_signature(postflight_path, signature_path, public_key_path)
    postflight = load_json(postflight_path)
    require_canonical_json(postflight_path, postflight, "signed postflight")
    evidence = {
        "hello_result": (Path(args.hello_result), "trusted_hello_evidence_sha256"),
        "health_result": (Path(args.health_result), "production_health_evidence_sha256"),
        "rollback_result": (Path(args.rollback_result), "rollback_evidence_sha256"),
    }
    sources = validate_postflight_semantics(
        armed=armed,
        postflight=postflight,
        policy=policy,
        evidence=evidence,
    )
    marker = {
        "format": 1,
        "kind": "postflight",
        "bundle_id": armed["bundle_id"],
        "postflight": {"path": str(postflight_path), "sha256": sha256_file(postflight_path)},
        "signature": {"path": str(signature_path), "sha256": sha256_file(signature_path)},
        "public_key": {"path": str(public_key_path), "sha256": sha256_file(public_key_path)},
        "trust_policy": {"path": str(trust_path), "sha256": sha256_file(trust_path)},
        "evidence": sources,
    }
    atomic_write(Path(args.output), canonical_json(marker), 0o400)
    print(armed["bundle_id"])


def command_validate_postflight_marker(args: argparse.Namespace) -> None:
    armed_path = Path(args.armed)
    require_protected_file(armed_path, "armed state")
    armed = load_json(armed_path)
    marker_path = Path(args.marker)
    require_protected_file(marker_path, "postflight marker", readonly=True)
    marker = load_json(marker_path)
    expected_marker_keys = {
        "format", "kind", "bundle_id", "postflight", "signature",
        "public_key", "trust_policy", "evidence",
    }
    if (
        not isinstance(marker, dict)
        or set(marker) != expected_marker_keys
        or marker.get("format") != 1
        or marker.get("kind") != "postflight"
        or marker.get("bundle_id") != armed.get("bundle_id")
    ):
        fail("postflight marker does not match armed state")
    trust = marker.get("trust_policy", {})
    if Path(str(trust.get("path", ""))).absolute() != Path(args.expected_trust_policy).absolute():
        fail("postflight marker uses an unpinned trust policy")
    for group in ("postflight", "signature", "public_key", "trust_policy"):
        item = marker.get(group)
        if not isinstance(item, dict) or sha256_file(Path(str(item.get("path", "")))) != item.get("sha256"):
            fail(f"postflight marker source changed: {group}")
    evidence = marker.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != {"hello_result", "health_result", "rollback_result"}:
        fail("postflight marker evidence schema is invalid")
    evidence_paths: dict[str, tuple[Path, str]] = {}
    evidence_fields = {
        "hello_result": "trusted_hello_evidence_sha256",
        "health_result": "production_health_evidence_sha256",
        "rollback_result": "rollback_evidence_sha256",
    }
    for name, item in evidence.items():
        if not isinstance(item, dict) or sha256_file(Path(str(item.get("path", "")))) != item.get("sha256"):
            fail(f"postflight evidence changed: {name}")
        evidence_paths[name] = (Path(str(item["path"])), evidence_fields[name])
    verify_signature(
        Path(marker["postflight"]["path"]),
        Path(marker["signature"]["path"]),
        Path(marker["public_key"]["path"]),
    )
    policy, _ = load_trust_policy(
        marker["trust_policy"]["path"], Path(marker["public_key"]["path"])
    )
    signed_postflight = load_json(Path(marker["postflight"]["path"]))
    require_canonical_json(
        Path(marker["postflight"]["path"]), signed_postflight, "signed postflight"
    )
    validate_postflight_semantics(
        armed=armed,
        postflight=signed_postflight,
        policy=policy,
        evidence=evidence_paths,
    )
    print(armed["bundle_id"])


def load_versions(path: Path) -> dict[str, str]:
    require_regular(path, "versions file")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail("versions file contains an invalid line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z0-9_]+", key) or not value:
            fail("versions file contains an invalid entry")
        values[key] = value
    if values != PINNED_POLICY:
        fail("versions file does not exactly match the compiled Phase 2 supply-chain policy")
    return values


def command_policy_value(args: argparse.Namespace) -> None:
    try:
        print(PINNED_POLICY[args.name])
    except KeyError:
        fail("unknown supply-chain policy name")


def command_verify_checkout(args: argparse.Namespace) -> None:
    repo = Path(args.repo)
    require_directory(repo, "checkout root")
    expected = args.expected_revision
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        fail("expected checkout revision must be a full SHA")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if head != expected:
        fail("checkout HEAD differs from the expected revision")
    paths = [
        "infra/project-cell/phase2ctl.py",
        "infra/project-cell/install-isolated-k3s.sh",
        "infra/project-cell/deadman-control.sh",
        "infra/project-cell/rollback-phase2.sh",
        "infra/project-cell/capture-host-evidence.sh",
        "infra/project-cell/seal-rollback-bundle.sh",
        "infra/project-cell/smoke-trusted-hello.sh",
        "infra/project-cell/verify-rollback.sh",
        "infra/project-cell/versions.env",
        "infra/project-cell/config/10-project-cell-reserves.conf",
        "infra/project-cell/manifests/trusted-hello.yaml",
        "infra/project-cell/systemd/omnia-project-cell-deadman.service",
        "infra/project-cell/systemd/omnia-project-cell-deadman.timer",
    ]
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--", *paths],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    if status.strip():
        fail("Phase 2 executable/config artifacts are dirty or untracked")
    for relative in paths:
        worktree = repo / relative
        require_regular(worktree, "checked-out Phase 2 artifact")
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{expected}:{relative}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if blob.returncode != 0 or sha256_bytes(blob.stdout) != sha256_file(worktree):
            fail(f"Phase 2 artifact does not match expected commit: {relative}")
    print(expected)


def command_self_check(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parent
    load_versions(root / "versions.env")
    reserves = root / "config" / "10-project-cell-reserves.conf"
    require_regular(reserves, "kubelet reservations")
    reserve_text = reserves.read_text(encoding="utf-8")
    for fragment in ("memory: 4Gi", "memory: 1Gi", "memory.available: 1Gi", "podPidsLimit: 4096"):
        if fragment not in reserve_text:
            fail(f"kubelet reservation contract is missing: {fragment}")
    command_validate_hello(argparse.Namespace(manifest=str(root / "manifests" / "trusted-hello.yaml")))
    print("phase2 self-check passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--bundle", required=True)
    manifest.add_argument("--hostname", required=True)
    manifest.set_defaults(handler=command_manifest)

    verify = subparsers.add_parser("verify-bundle")
    verify.add_argument("--bundle", required=True)
    verify.set_defaults(handler=command_verify_bundle)

    attestation = subparsers.add_parser("verify-attestation")
    attestation.add_argument("--kind", choices=("offhost", "provider_rescue"), required=True)
    attestation.add_argument("--bundle", required=True)
    attestation.add_argument("--attestation", required=True)
    attestation.add_argument("--signature", required=True)
    attestation.add_argument("--public-key", required=True)
    attestation.add_argument("--trust-policy", required=True)
    attestation.add_argument("--ciphertext")
    attestation.add_argument("--output", required=True)
    attestation.set_defaults(handler=command_verify_attestation)

    marker = subparsers.add_parser("validate-marker")
    marker.add_argument("--kind", choices=("offhost", "provider_rescue"), required=True)
    marker.add_argument("--bundle", required=True)
    marker.add_argument("--marker", required=True)
    marker.add_argument("--expected-trust-policy")
    marker.set_defaults(handler=command_validate_marker)

    recipient = subparsers.add_parser("verify-recipient")
    recipient.add_argument("--certificate", required=True)
    recipient.add_argument("--trust-policy", required=True)
    recipient.set_defaults(handler=command_verify_recipient)

    preflight = subparsers.add_parser("install-preflight")
    preflight.add_argument("--gate", required=True)
    preflight.add_argument("--bind-address", required=True)
    preflight.add_argument("--admin-cidr", required=True)
    preflight.add_argument("--read-only", action="store_true")
    preflight.set_defaults(handler=command_install_preflight)

    render = subparsers.add_parser("render-config")
    render.add_argument("--bind-address", required=True)
    render.add_argument("--admin-cidr", required=True)
    render.add_argument("--host-address", action="append", default=[], required=True)
    render.add_argument("--network", action="append", default=[])
    render.add_argument("--output", required=True)
    render.set_defaults(handler=command_render_config)

    hello = subparsers.add_parser("validate-hello")
    hello.add_argument("--manifest", required=True)
    hello.set_defaults(handler=command_validate_hello)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    compare.set_defaults(handler=command_compare)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--input-dir", required=True)
    snapshot.add_argument("--output", required=True)
    snapshot.set_defaults(handler=command_snapshot)

    postflight = subparsers.add_parser("validate-postflight")
    postflight.add_argument("--armed", required=True)
    postflight.add_argument("--postflight", required=True)
    postflight.set_defaults(handler=command_validate_postflight)

    verified_postflight = subparsers.add_parser("verify-postflight")
    verified_postflight.add_argument("--armed", required=True)
    verified_postflight.add_argument("--postflight", required=True)
    verified_postflight.add_argument("--signature", required=True)
    verified_postflight.add_argument("--public-key", required=True)
    verified_postflight.add_argument("--trust-policy", required=True)
    verified_postflight.add_argument("--hello-result", required=True)
    verified_postflight.add_argument("--health-result", required=True)
    verified_postflight.add_argument("--rollback-result", required=True)
    verified_postflight.add_argument("--output", required=True)
    verified_postflight.set_defaults(handler=command_verify_postflight)

    postflight_marker = subparsers.add_parser("validate-postflight-marker")
    postflight_marker.add_argument("--armed", required=True)
    postflight_marker.add_argument("--marker", required=True)
    postflight_marker.add_argument("--expected-trust-policy", required=True)
    postflight_marker.set_defaults(handler=command_validate_postflight_marker)

    self_check = subparsers.add_parser("self-check")
    self_check.set_defaults(handler=command_self_check)

    policy = subparsers.add_parser("policy-value")
    policy.add_argument("--name", required=True)
    policy.set_defaults(handler=command_policy_value)

    checkout = subparsers.add_parser("verify-checkout")
    checkout.add_argument("--repo", required=True)
    checkout.add_argument("--expected-revision", required=True)
    checkout.set_defaults(handler=command_verify_checkout)

    installed = subparsers.add_parser("installed-manifest")
    installed.add_argument("--bundle", required=True)
    installed.add_argument("--output", required=True)
    installed.set_defaults(handler=command_installed_manifest)

    verify_installed = subparsers.add_parser("verify-installed")
    verify_installed.add_argument("--manifest", required=True)
    verify_installed.set_defaults(handler=command_verify_installed)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.handler(args)
    except GateError as exc:
        print(f"phase2 gate failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
