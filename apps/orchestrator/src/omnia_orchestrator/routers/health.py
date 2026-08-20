"""GET /health — service liveness + dependency probes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict[str, str]:
    # TODO sprint A1: extend with actual docker.ping() + asyncpg.fetchval("SELECT 1")
    # probes so the api↔orchestrator handshake can fail fast on bad deploys.
    return {"status": "ok"}
