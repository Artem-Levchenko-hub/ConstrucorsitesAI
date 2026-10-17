"""Read-only gateway health advisory, separate from every paid dispatch path."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import httpx

from yleum_api.core.config import get_settings
from yleum_api.services.generation.provider_health_auth import (
    HEADER,
    Dependency,
    mint_health_request,
    unknown_snapshot,
    verify_health_response,
)
from yleum_api.services.generation.provider_health_auth import (
    ProviderHealthAdvisory as ProviderHealthAdvisory,
)


async def free_health_for_run(
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    dependency: Dependency,
) -> ProviderHealthAdvisory:
    identity = {
        "run_id": str(run_id),
        "project_id": str(project_id),
        "user_id": str(user_id),
        "dependency": dependency,
    }
    nonce = str(uuid4())
    try:
        settings = get_settings()
        token = mint_health_request(identity, nonce)
        async with (
            asyncio.timeout(6),
            httpx.AsyncClient(
                timeout=httpx.Timeout(5, connect=2), follow_redirects=False
            ) as client,
        ):
            response = await client.get(
                f"{settings.llm_gateway_url.rstrip('/')}/_internal/provider-health/{run_id}",
                headers={HEADER: token},
            )
        # Final verification follows connection cleanup: that await cannot renew stale evidence.
        if response.status_code != 200 or len(response.content) > 8192:
            raise ValueError
        import json

        from yleum_api.services.generation.response_auth import _pairs

        value = json.loads(response.content, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or set(value) != {"assertion"}:
            raise ValueError
        return verify_health_response(value["assertion"], identity, nonce)
    except Exception:
        # No raw HTTP/provider/auth data leaves this collaborator, including diagnostic errors.
        return unknown_snapshot(None, "unavailable")
