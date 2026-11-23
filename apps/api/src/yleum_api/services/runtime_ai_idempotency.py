"""Short-lived Redis reservations for billed MAX AITunnel text calls."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import status
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.errors import ApiError, ErrorBody
from yleum_api.schemas.integration_runtime import RuntimeAIPublic, RuntimeAIRequest

log = logging.getLogger(__name__)
_REDIS_TIMEOUT_SECONDS = 2
_RESERVATION_TTL_SECONDS = 600
_SHORT_REPLAY_TTL_SECONDS = 60
_RESERVE_SCRIPT = """
local existing = redis.call("GET", KEYS[1])
if existing then return existing end
redis.call("SET", KEYS[1], ARGV[1], "EX", ARGV[2])
return false
"""
_UPDATE_SCRIPT = """
if redis.call("GET", KEYS[1]) ~= ARGV[1] then return 0 end
if ARGV[2] == "" then
  redis.call("DEL", KEYS[1])
elseif tonumber(ARGV[3]) > 0 then
  redis.call("SET", KEYS[1], ARGV[2], "EX", ARGV[3])
else
  redis.call("SET", KEYS[1], ARGV[2], "KEEPTTL")
end
return 1
"""


class _CachedError(ErrorBody):
    status_code: int


class _RequestRecord(BaseModel):
    fingerprint: str
    outcome: RuntimeAIPublic | _CachedError | None = None

    def encrypt(self) -> str:
        # Redis snapshots can outlive a key's TTL. Keep cached answers encrypted.
        return encrypt_strong(self.model_dump_json(exclude_none=True))


@dataclass(frozen=True)
class RuntimeAIReservation:
    redis: Redis
    key: str
    pending: str
    fingerprint: str
    implicit: bool

    async def cancel_before_dispatch(self) -> None:
        """Only release a reservation if no provider request was attempted."""
        await self._update("")

    async def record_outcome(self, outcome: RuntimeAIPublic | ApiError) -> None:
        cached: RuntimeAIPublic | _CachedError
        ttl = _SHORT_REPLAY_TTL_SECONDS if self.implicit else 0
        if isinstance(outcome, ApiError):
            cached = _CachedError(
                code=outcome.code, message=outcome.message, status_code=outcome.status_code,
            )
            if outcome.code == "integration_provider_unavailable":
                ttl = 0
            elif outcome.code == "rate_limited":
                ttl = _SHORT_REPLAY_TTL_SECONDS
        else:
            cached = outcome
        record = _RequestRecord(fingerprint=self.fingerprint, outcome=cached)
        await self._update(record.encrypt(), ttl=ttl)

    async def _update(self, updated: str, *, ttl: int = 0) -> None:
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
                execute = cast(Any, self.redis.eval)
                await execute(_UPDATE_SCRIPT, 1, self.key, self.pending, updated, ttl)
        except (TimeoutError, RedisError):
            # A pending reservation still prevents replay after Redis recovers.
            log.warning("MAX runtime AI provider=aitunnel status=reservation_update_unavailable")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _request_identity(
    project_id: UUID, max_user_id: int, payload: RuntimeAIRequest,
) -> tuple[str, str]:
    fingerprint = _digest(json.dumps(
        payload.model_dump(exclude={"idempotency_key"}, exclude_unset=True),
        sort_keys=True,
        default=str,
    ))
    if payload.idempotency_key is None:
        operation = f"implicit:{fingerprint}"
    else:
        operation = f"explicit:{payload.idempotency_key}"
    # A key update must not charge a visitor again for the same operation.
    scope = _digest(f"{project_id}:{max_user_id}:{operation}")
    return f"omnia:runtime-ai:request:{scope}", fingerprint


def _storage_unavailable() -> ApiError:
    return ApiError(
        "integration_provider_unavailable",
        "Проверка ИИ-запроса временно недоступна. Попробуйте позже.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


async def has_aitunnel_request(
    redis_factory: Callable[[], Redis],
    *,
    project_id: UUID,
    max_user_id: int,
    payload: RuntimeAIRequest,
) -> bool:
    """Check for an existing AITunnel operation without reading its response."""
    key, _fingerprint = _request_identity(project_id, max_user_id, payload)
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
            return bool(await redis_factory().exists(key))
    except (TimeoutError, RedisError, RuntimeError, ValueError):
        raise _storage_unavailable() from None


async def reserve_runtime_ai(
    redis_factory: Callable[[], Redis],
    *,
    project_id: UUID,
    max_user_id: int,
    payload: RuntimeAIRequest,
) -> RuntimeAIReservation | RuntimeAIPublic:
    key, fingerprint = _request_identity(project_id, max_user_id, payload)
    implicit = payload.idempotency_key is None
    try:
        encoded = _RequestRecord(fingerprint=fingerprint).encrypt()
        async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
            redis = redis_factory()
            execute = cast(Any, redis.eval)
            # Hold ambiguous or interrupted dispatches even if the caller omitted a key.
            raw = await execute(_RESERVE_SCRIPT, 1, key, encoded, _RESERVATION_TTL_SECONDS)
        if raw is None:
            return RuntimeAIReservation(redis, key, encoded, fingerprint, implicit)
        if not isinstance(raw, str):
            raise ValueError("invalid Redis response")
        existing = _RequestRecord.model_validate_json(decrypt_strong(raw))
    except (TimeoutError, RedisError, RuntimeError, ValueError, InvalidToken):
        raise _storage_unavailable() from None
    if existing.fingerprint != fingerprint:
        raise ApiError(
            "idempotency_conflict", "Идентификатор запроса уже использовался с другими данными.",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(existing.outcome, _CachedError):
        raise ApiError(
            existing.outcome.code, existing.outcome.message, existing.outcome.status_code,
        )
    if existing.outcome is None:
        raise ApiError(
            "integration_operation_conflict", "Этот ИИ-запрос ещё выполняется.",
            status.HTTP_409_CONFLICT,
        )
    return existing.outcome
