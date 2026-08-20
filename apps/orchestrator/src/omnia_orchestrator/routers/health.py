"""GET /health — service liveness + dependency probes."""

from __future__ import annotations

from fastapi import APIRouter

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.release import normalize_release_sha

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
    }
