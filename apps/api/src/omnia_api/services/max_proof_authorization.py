"""Short-lived authorization for side-effect-free MAX browser proof.

The generated application is untrusted.  A plain proof/idempotency header can
therefore never enable the managed integration sandbox: model-written code can
forge that header.  This module signs the exact project, source revision,
capability and stable idempotency key with an API-only secret.  The locked MAX
proxy forwards the opaque token to the platform, where it is validated before
any sandbox response is returned.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from uuid import UUID

from omnia_api.core.config import get_settings

_DOMAIN = b"omnia:max-proof-authorization:v1\0"
_TOKEN_PREFIX = "v1."
_SIGNATURE_CHARS = 43
_MAX_TTL_SECONDS = 15 * 60
_TOKEN_RE = re.compile(r"^v1\.[A-Za-z0-9_-]{32,512}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class MaxProofAuthorization:
    project_id: UUID
    proof_key: str
    source_digest: str
    capability_id: str
    issued_at: int
    expires_at: int


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        (value + padding).encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


def _secret() -> bytes:
    return get_settings().jwt_secret.get_secret_value().encode("utf-8")


def _canonical_project_id(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def issue_max_proof_authorization(
    project_id: UUID | str,
    *,
    proof_key: str,
    source_digest: str,
    capability_id: str,
    ttl_seconds: int = _MAX_TTL_SECONDS,
) -> str:
    """Issue one bounded opaque token; callers must never expose its contents."""

    canonical_project_id = _canonical_project_id(project_id)
    if not _DIGEST_RE.fullmatch(proof_key):
        raise ValueError("proof_key must be a lowercase SHA-256 digest")
    if not _DIGEST_RE.fullmatch(source_digest):
        raise ValueError("source_digest must be a lowercase SHA-256 digest")
    if not _CAPABILITY_RE.fullmatch(capability_id):
        raise ValueError("capability_id is invalid")
    if not 30 <= ttl_seconds <= _MAX_TTL_SECONDS:
        raise ValueError("proof authorization ttl is invalid")

    issued_at = int(time.time())
    claims = {
        "c": capability_id,
        "e": issued_at + ttl_seconds,
        "i": issued_at,
        "k": proof_key,
        "p": str(canonical_project_id),
        "s": source_digest,
    }
    payload = json.dumps(
        claims,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    signature = hmac.new(_secret(), _DOMAIN + payload, hashlib.sha256).digest()
    token = _TOKEN_PREFIX + _b64encode(payload) + _b64encode(signature)
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("proof authorization token exceeds its transport contract")
    return token


def validate_max_proof_authorization(
    token: str,
    project_id: UUID | str,
    *,
    proof_key: str,
) -> MaxProofAuthorization | None:
    """Validate a token against the exact route project and stable proof key."""

    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    if not _DIGEST_RE.fullmatch(proof_key):
        return None
    try:
        canonical_project_id = _canonical_project_id(project_id)
        body = token.removeprefix(_TOKEN_PREFIX)
        payload_segment = body[:-_SIGNATURE_CHARS]
        signature_segment = body[-_SIGNATURE_CHARS:]
        payload = _b64decode(payload_segment)
        supplied_signature = _b64decode(signature_segment)
        expected_signature = hmac.new(_secret(), _DOMAIN + payload, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        raw = json.loads(payload)
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or set(raw) != {"c", "e", "i", "k", "p", "s"}:
        return None

    capability_id = raw.get("c")
    expires_at = raw.get("e")
    issued_at = raw.get("i")
    token_proof_key = raw.get("k")
    token_project_id = raw.get("p")
    source_digest = raw.get("s")
    if (
        not isinstance(capability_id, str)
        or not _CAPABILITY_RE.fullmatch(capability_id)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(token_proof_key, str)
        or not _DIGEST_RE.fullmatch(token_proof_key)
        or not isinstance(token_project_id, str)
        or not isinstance(source_digest, str)
        or not _DIGEST_RE.fullmatch(source_digest)
    ):
        return None
    try:
        parsed_project_id = UUID(token_project_id)
    except ValueError:
        return None
    now = int(time.time())
    if (
        parsed_project_id != canonical_project_id
        or not hmac.compare_digest(token_proof_key, proof_key)
        or issued_at > now + 30
        or expires_at <= now
        or expires_at <= issued_at
        or expires_at - issued_at > _MAX_TTL_SECONDS
    ):
        return None
    return MaxProofAuthorization(
        project_id=parsed_project_id,
        proof_key=token_proof_key,
        source_digest=source_digest,
        capability_id=capability_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )


__all__ = [
    "MaxProofAuthorization",
    "issue_max_proof_authorization",
    "validate_max_proof_authorization",
]
