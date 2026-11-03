"""Authenticated read-only inventory for controller build retention."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.internal_auth import verify_internal_token
from yleum_orchestrator.services.build_retention_inventory import collect_roots

router = APIRouter(prefix="/internal/build-retention", tags=["build-retention"])


@router.get("/roots")
async def build_retention_roots(
    x_internal_token: Annotated[str | None, Header()] = None,
    nonce: Annotated[
        str | None, Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ] = None,
) -> JSONResponse:
    verify_internal_token(x_internal_token)
    try:
        root = Path(get_settings().cell_state_path).parent
        roots = await asyncio.to_thread(collect_roots, root)
    except Exception:
        # Neither state payloads nor filesystem/Docker exception details are public.
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "internal_error", "message": "build inventory unavailable"}},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        content={
            "version": 1,
            "archive_names": sorted(roots.archive_names),
            "image_ids": sorted(roots.image_ids),
            "image_refs": sorted(roots.image_refs),
            "busy_workspaces": sorted(roots.busy_workspaces),
            "active_operations": roots.active_operations,
            "observed_at": roots.observed_at,
            "generation": roots.generation,
            "nonce": nonce,
        },
        headers={"Cache-Control": "no-store"},
    )
