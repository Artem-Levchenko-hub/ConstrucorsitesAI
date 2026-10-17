"""Authenticated free advisory reads; no completion or provider-attempt admission."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from yleum_gateway.core.db import get_pool
from yleum_gateway.core.provider_health_auth import (
    HEADER,
    sign_health_response,
    verify_health_request,
)
from yleum_gateway.core.redis import get_redis
from yleum_gateway.services.provider_health import ProviderHealth, current_scope

router = APIRouter(prefix="/_internal/provider-health", tags=["internal"])


async def current_owner(identity: dict[str, str]) -> bool:
    # MVCC read only, no generation execution slot/lease/attempt, no PG lock held during health.
    async with get_pool().acquire() as conn, conn.transaction(readonly=True):
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM generation_runs r JOIN projects p ON p.id=r.project_id "
                "WHERE r.id=$1 AND r.project_id=$2 AND r.user_id=$3 AND p.owner_id=$3 "
                "AND r.status='running')",
                UUID(identity["run_id"]),
                UUID(identity["project_id"]),
                UUID(identity["user_id"]),
            )
        )


@router.get("/{run_id}")
async def provider_health(run_id: UUID, request: Request) -> dict[str, str]:
    tokens = request.headers.getlist(HEADER)
    try:
        if len(tokens) != 1:
            raise ValueError
        identity, nonce = verify_health_request(tokens[0])
        if identity["run_id"] != str(run_id) or not await current_owner(identity):
            raise ValueError
    except Exception:
        raise HTTPException(403, "provider health authentication denied") from None
    try:
        result = await ProviderHealth(get_redis()).check()
        if not await current_owner(identity):
            raise ValueError
        # Recheck after SQL/Redis cleanup; expired request/current credential never inherits old cache.
        verify_health_request(tokens[0])
        if result.credential_scope != current_scope():
            raise ValueError
        from time import time

        if not result.observed_at <= time() < result.expires_at:
            raise ValueError
        return {"assertion": sign_health_response(result, identity, nonce)}
    except Exception:
        raise HTTPException(503, "provider health unavailable") from None
