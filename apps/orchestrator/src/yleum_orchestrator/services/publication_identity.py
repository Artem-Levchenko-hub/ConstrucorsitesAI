"""P04: what a publication request *wants*, separated from how it was asked.

The idempotency key answers "is this the same HTTP request?". The release
fingerprint answers "is this the same desired release?": source identity
(snapshot, commit, revision, hostname, restoration) plus the configuration that
will actually be applied. Secret values never enter the fingerprint payload —
``runtime_env`` contributes only an HMAC under a controller-private key, so a
journal reader cannot recover or compare tokens.

A fingerprint is a claim about intent, never proof of health: the caller must
still confirm what is serving before answering "already current".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from yleum_orchestrator.schemas.cell_publication import CellDeployRequest

DecisionKind = Literal["release", "already_current", "config_only"]


def fingerprint_key(root: Path) -> bytes:
    """Per-installation HMAC key, created once with owner-only permissions."""
    path = root / "fingerprint.key"
    if path.is_symlink():
        raise OSError("unsafe fingerprint key path")
    if path.exists():
        return path.read_bytes()
    root.mkdir(parents=True, exist_ok=True)
    value = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(value)
    return value


def source_identity(request: CellDeployRequest) -> str:
    payload = {
        "snapshot_id": str(request.snapshot_id),
        "commit_sha": request.commit_sha,
        "source_revision": request.source_revision,
        "slug": request.slug,
        "restoration_operation_id": (
            None
            if request.restoration_operation_id is None
            else str(request.restoration_operation_id)
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def fingerprint_payload(request: CellDeployRequest, key: bytes) -> dict[str, Any]:
    """Canonical, secret-free description of the desired release."""
    env = json.dumps(request.runtime_env, sort_keys=True).encode()
    return {
        "source_identity": source_identity(request),
        "business_config": request.business_config,
        "business_config_version": request.business_config_version,
        "runtime_env_hmac": hmac.new(key, env, "sha256").hexdigest(),
    }


def release_fingerprint(request: CellDeployRequest, key: bytes) -> str:
    payload = json.dumps(fingerprint_payload(request, key), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class PublicationDecision:
    kind: DecisionKind
    reason: str


def classify_publication(
    request: CellDeployRequest,
    active_release: dict[str, Any] | None,
    *,
    data_seeded: bool,
    key: bytes,
) -> PublicationDecision:
    """Decide from durable state only; health is verified separately."""
    if active_release is None or not data_seeded:
        return PublicationDecision("release", "no_active_release")
    recorded = active_release.get("fingerprint")
    if not recorded or not active_release.get("source_identity"):
        return PublicationDecision("release", "unproven_release")
    if recorded == release_fingerprint(request, key):
        return PublicationDecision("already_current", "same_fingerprint")
    if active_release["source_identity"] == source_identity(request):
        return PublicationDecision("config_only", "same_source_other_config")
    return PublicationDecision("release", "source_changed")
