"""Credential-scoped free advisory; unsupported upstream contracts make zero calls."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.provider_health_auth import (
    ProviderHealthAdvisory,
    Status,
    sign_health_cache,
    unknown_snapshot,
    verify_health_cache,
)

PUBLISH = """
if redis.call('GET',KEYS[1]) ~= ARGV[1] or redis.call('PTTL',KEYS[1]) <= 0 then return 0 end
redis.call('SET',KEYS[2],ARGV[2],'PX',ARGV[3])
redis.call('DEL',KEYS[1])
return 1
"""
RELEASE = """
if redis.call('GET',KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL',KEYS[1])
"""


class FreeProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    credential_status: Status
    funds_status: Status


def signing_secret() -> str:
    value = get_settings().generation_response_signing_secret
    secret = value.get_secret_value() if value else ""
    if len(secret) < 32:
        raise ValueError("health authentication unavailable")
    return secret


def current_scope() -> str:
    settings = get_settings()
    # Same effective credential/base used by llmgw._key_and_url. No public raw fingerprint.
    private = {
        "provider": "llmgw",
        "key": settings.llmgw_api_key.get_secret_value() if settings.llmgw_api_key else None,
        "base": settings.llmgw_base_url.rstrip("/") + "/chat/completions",
        "organization": settings.llmgw_organization_id,
    }
    key = hmac.digest(
        signing_secret().encode(), b"yleum:provider-health:credential-scope:v1", "sha256"
    )
    return hmac.digest(
        key, json.dumps(private, sort_keys=True, separators=(",", ":")).encode(), "sha256"
    ).hex()


class ProviderHealth:
    def __init__(
        self, redis: Any, *, probe: Callable[[], Awaitable[FreeProbeResult]] | None = None
    ):
        self.redis, self.probe = redis, probe

    async def check(self) -> ProviderHealthAdvisory:
        try:
            async with asyncio.timeout(4):
                result = await self._check()
            # Includes lease-release IO: no awaited cleanup after this freshness check.
            if (
                result.credential_scope != current_scope()
                or not result.observed_at <= time.time() < result.expires_at
            ):
                return unknown_snapshot(current_scope(), "unavailable")
            return result
        except Exception:
            return unknown_snapshot(None, "unavailable")

    async def _check(self) -> ProviderHealthAdvisory:
        scope: str | None = None
        try:
            scope = current_scope()
            secret = signing_secret()
            result_key = f"provider-health:v1:{scope}:result"
            lease_key = f"provider-health:v1:{scope}:lease"
            cached = await self.redis.get(result_key)
            if cached is not None:
                result = verify_health_cache(
                    cached.decode() if isinstance(cached, bytes) else cached, scope, secret=secret
                )
                return (
                    result
                    if current_scope() == scope
                    else unknown_snapshot(current_scope(), "unavailable")
                )
            token = str(uuid4())
            if not await self.redis.set(lease_key, token, nx=True, px=5000):
                return unknown_snapshot(scope, "checking")
            try:
                # A late winner must not repeat a probe already published by the prior owner.
                cached = await self.redis.get(result_key)
                if cached is not None:
                    return verify_health_cache(
                        cached.decode() if isinstance(cached, bytes) else cached,
                        scope,
                        secret=secret,
                    )
                if self.probe is None:
                    result = unknown_snapshot(scope, "unsupported")
                else:
                    async with asyncio.timeout(2):
                        observed = await self.probe()
                    observed = FreeProbeResult.model_validate(observed)
                    seconds = int(time.time())
                    result = ProviderHealthAdvisory(
                        credential_scope=scope,
                        **observed.model_dump(),
                        observed_at=seconds,
                        expires_at=seconds + 15,
                        reason="observed",
                    )
                task = asyncio.current_task()
                if current_scope() != scope or (task is not None and task.cancelling()):
                    return unknown_snapshot(current_scope(), "unavailable")
                ttl = int((result.expires_at - time.time()) * 1000)
                if ttl <= 0:
                    return unknown_snapshot(scope, "unavailable")
                published = await self.redis.eval(
                    PUBLISH,
                    2,
                    lease_key,
                    result_key,
                    token,
                    sign_health_cache(result, secret=secret),
                    ttl,
                )
                if published != 1 or current_scope() != scope or time.time() >= result.expires_at:
                    return unknown_snapshot(current_scope(), "unavailable")
                return result
            finally:
                # Never delete a replacement owner's lease, even after cancellation/timeout.
                with suppress(Exception):
                    async with asyncio.timeout(0.25):
                        await self.redis.eval(RELEASE, 1, lease_key, token)
        except Exception:
            return unknown_snapshot(scope, "unavailable")
