"""Minimal HS256 bearer verification for trusted Project Cell runner traffic."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from omnia_gateway.core.config import Settings, get_settings
from omnia_gateway.core.redis import get_redis

_FIXED_ALGORITHM = "HS256"
_RUNNER_JTI_KEY_PREFIX = "omnia:project-cell:runner-jti:"


class RunnerAuthError(ValueError):
    """Public-safe runner auth failure."""


class RunnerAuthConfigError(RuntimeError):
    """Server-side runner auth misconfiguration."""


class RunnerReplayError(RunnerAuthError):
    """Single-use runner token was already consumed."""


class RunnerReplayUnavailableError(RuntimeError):
    """Replay fence could not be checked safely."""


@dataclass(frozen=True, slots=True)
class RunnerClaims:
    jti: str
    project_id: UUID
    run_id: UUID
    session_id: UUID
    workspace_id: UUID
    fencing_epoch: int
    cancel_epoch: int
    nbf: int
    exp: int


class RedisReplayFence(Protocol):
    def set(
        self,
        name: str,
        value: bytes,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> Awaitable[bool | str | bytes | None]: ...


def verify_runner_bearer_header(
    authorization: str | None,
    *,
    settings: Settings | None = None,
    now: int | None = None,
) -> RunnerClaims:
    active_settings = settings or get_settings()
    secret_value = active_settings.runner_auth_secret
    secret = secret_value.get_secret_value().strip() if secret_value is not None else ""
    if not secret:
        raise RunnerAuthConfigError("project-cell runner auth is not configured")
    issuer = (active_settings.runner_auth_issuer or "").strip()
    if not issuer:
        raise RunnerAuthConfigError("project-cell runner auth issuer is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise RunnerAuthError("missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise RunnerAuthError("missing bearer token")
    return verify_runner_token(token, settings=active_settings, now=now)


def verify_runner_token(
    token: str,
    *,
    settings: Settings | None = None,
    now: int | None = None,
) -> RunnerClaims:
    active_settings = settings or get_settings()
    header_segment, payload_segment, signature_segment = _split_token(token)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    header = _decode_segment(header_segment, "header")
    payload = _decode_segment(payload_segment, "payload")

    algorithm = header.get("alg")
    if algorithm != _FIXED_ALGORITHM:
        raise RunnerAuthError("unsupported jwt algorithm")

    secret_value = active_settings.runner_auth_secret
    secret = secret_value.get_secret_value().encode("utf-8") if secret_value is not None else b""
    expected_signature = _b64url_encode(hmac.new(secret, signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected_signature, signature_segment):
        raise RunnerAuthError("invalid bearer token")

    audience = payload.get("aud")
    if audience != active_settings.runner_auth_audience:
        raise RunnerAuthError("invalid token audience")
    issuer = payload.get("iss")
    if issuer != active_settings.runner_auth_issuer:
        raise RunnerAuthError("invalid token issuer")

    current_time = int(time.time()) if now is None else now
    nbf = _require_int(payload, "nbf")
    exp = _require_int(payload, "exp")
    if exp <= nbf:
        raise RunnerAuthError("invalid token lifetime")
    if exp - nbf > active_settings.runner_auth_max_ttl_seconds:
        raise RunnerAuthError("token ttl exceeds server limit")
    if current_time < nbf:
        raise RunnerAuthError("token not active yet")
    if current_time >= exp:
        raise RunnerAuthError("token expired")

    fencing_epoch = _require_int(payload, "fencing_epoch")
    if fencing_epoch <= 0:
        raise RunnerAuthError("fencing_epoch must be positive")

    cancel_epoch = _require_int(payload, "cancel_epoch")
    if cancel_epoch < 0:
        raise RunnerAuthError("cancel_epoch must be non-negative")

    return RunnerClaims(
        jti=_require_uuid_string(payload, "jti"),
        project_id=_require_uuid(payload, "project_id"),
        run_id=_require_uuid(payload, "run_id"),
        session_id=_require_uuid(payload, "session_id"),
        workspace_id=_require_uuid(payload, "workspace_id"),
        fencing_epoch=fencing_epoch,
        cancel_epoch=cancel_epoch,
        nbf=nbf,
        exp=exp,
    )


def validate_runner_metadata(raw_metadata: Any, claims: RunnerClaims) -> dict[str, Any]:
    if not isinstance(raw_metadata, dict):
        raise RunnerAuthError("metadata is required")
    metadata = dict(raw_metadata)
    message_id = metadata.get("message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        raise RunnerAuthError("metadata message_id is required")
    expected: dict[str, object] = {
        "project_id": str(claims.project_id),
        "run_id": str(claims.run_id),
        "session_id": str(claims.session_id),
        "workspace_id": str(claims.workspace_id),
        "fencing_epoch": claims.fencing_epoch,
        "cancel_epoch": claims.cancel_epoch,
        "message_id": claims.jti,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise RunnerAuthError(f"metadata {key} does not match bearer token")
    return metadata


async def consume_runner_jti(
    claims: RunnerClaims,
    *,
    now: int | None = None,
    redis_client: RedisReplayFence | None = None,
) -> None:
    ttl_seconds = _runner_jti_ttl_seconds(claims, now=now)
    key = f"{_RUNNER_JTI_KEY_PREFIX}{claims.jti}"
    client = get_redis() if redis_client is None else redis_client
    try:
        consumed = await client.set(key, b"1", ex=ttl_seconds, nx=True)
    except Exception as exc:
        raise RunnerReplayUnavailableError("runner replay fence unavailable") from exc
    if not consumed:
        raise RunnerReplayError("runner token already used")


def _split_token(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise RunnerAuthError("malformed bearer token")
    return parts[0], parts[1], parts[2]


def _decode_segment(segment: str, label: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(_pad_b64(segment))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise RunnerAuthError(f"malformed jwt {label}") from exc
    if not isinstance(value, dict):
        raise RunnerAuthError(f"malformed jwt {label}")
    return value


def _pad_b64(segment: str) -> bytes:
    return (segment + "=" * (-len(segment) % 4)).encode("ascii")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _runner_jti_ttl_seconds(claims: RunnerClaims, *, now: int | None = None) -> int:
    current_time = int(time.time()) if now is None else now
    return max(1, claims.exp - current_time)


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise RunnerAuthError(f"token {key} must be an integer")
    return int(value)


def _require_uuid(payload: dict[str, Any], key: str) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RunnerAuthError(f"token {key} must be a uuid")
    try:
        return UUID(value)
    except ValueError as exc:
        raise RunnerAuthError(f"token {key} must be a uuid") from exc


def _require_uuid_string(payload: dict[str, Any], key: str) -> str:
    return str(_require_uuid(payload, key))
