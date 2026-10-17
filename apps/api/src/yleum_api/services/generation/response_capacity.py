"""Capacity-only admin boundary. Never return or log raw MinIO admin documents."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

_ADMIN_SLOTS = asyncio.Semaphore(4)


def capacity_values(info: object, quota: object) -> dict[str, int]:
    invalid = "generation storage capacity unavailable"
    if (
        not isinstance(quota, dict)
        or quota.get("quotatype") != "hard"
        or type(quota.get("quota")) is not int
        or quota["quota"] <= 0
        or not isinstance(info, dict)
    ):
        raise ValueError(invalid)
    servers = info.get("servers")
    # Current pinned deployment is one server/one disk. Unsupported layouts
    # cannot be summed without proving replication/erasure capacity semantics.
    if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
        raise ValueError(invalid)
    server = servers[0]
    drives = server.get("drives")
    if (
        server.get("state") != "online"
        or not isinstance(drives, list)
        or len(drives) != 1
        or not isinstance(drives[0], dict)
    ):
        raise ValueError(invalid)
    drive = drives[0]
    free, total, used = (drive.get(key) for key in ("availspace", "totalspace", "usedspace"))
    if (
        drive.get("state") != "ok"
        or type(free) is not int
        or type(total) is not int
        or type(used) is not int
        or min(free, total, used) < 0
        or total <= 0
        or free > total
        or used > total
        or free + used > total
    ):
        raise ValueError(invalid)
    return {"quota_bytes": quota["quota"], "free_bytes": free}


async def validate_capacity_identity(identity: GenerationProviderIdentity) -> None:
    async with async_sessionmaker(get_engine())() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT a.* FROM generation_provider_attempts a "
                        "JOIN generation_runs r ON r.id=a.run_id "
                        "JOIN projects p ON p.id=a.project_id "
                        "WHERE a.attempt_id=:id AND a.phase='reserved' "
                        "AND r.status='running' AND r.project_id=a.project_id "
                        "AND r.user_id=a.user_id "
                        "AND p.owner_id=a.user_id AND r.prompt_hash=a.prompt_hash"
                    ),
                    {"id": identity.attempt_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None or any(
            row[key] != getattr(identity, key)
            for key in (
                "run_id",
                "project_id",
                "user_id",
                "prompt_hash",
                "request_sha256",
                "version",
            )
        ):
            raise ValueError("generation capacity ownership conflicts")


def _admin_capacity() -> dict[str, int]:
    from minio.credentials.providers import StaticProvider
    from minio.minioadmin import MinioAdmin
    from urllib3 import PoolManager, Timeout

    settings = get_settings()
    # Existing API admin authority stays here. The gateway never receives it.
    admin = MinioAdmin(
        endpoint=settings.minio_endpoint,
        credentials=StaticProvider(
            settings.minio_access_key, settings.minio_secret_key.get_secret_value()
        ),
        secure=settings.minio_secure,
        http_client=PoolManager(timeout=Timeout(connect=2, read=3, total=5), retries=False),
    )
    quota = json.loads(admin.bucket_quota_get(settings.generation_response_s3_bucket))
    info = json.loads(admin.info())
    return capacity_values(info, quota)


async def current_capacity(identity: GenerationProviderIdentity) -> dict[str, Any]:
    settings = get_settings()
    if not settings.generation_response_retention_enabled:
        raise ValueError("generation response retention is disabled")
    epoch = settings.generation_response_storage_epoch
    budget, floor = (
        settings.generation_response_storage_budget_bytes,
        settings.generation_response_capacity_min_free_bytes,
    )
    if (
        not epoch
        or str(UUID(epoch)) != epoch
        or type(budget) is not int
        or budget <= 0
        or type(floor) is not int
        or floor < 0
    ):
        raise ValueError("generation storage provisioning is incomplete")
    await validate_capacity_identity(identity)  # Before ANY admin call.
    async with _ADMIN_SLOTS:
        task = asyncio.create_task(asyncio.to_thread(_admin_capacity))
        try:
            values = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Bound admin work retains its slot even when the caller disconnects.
            try:
                await asyncio.shield(task)
            finally:
                raise
    if values["quota_bytes"] != budget or values["free_bytes"] < floor:
        raise ValueError("generation storage capacity unavailable")
    return {
        "version": 1,
        "bucket": settings.generation_response_s3_bucket,
        "epoch": epoch,
        **values,
        "measured_at": int(datetime.now(UTC).timestamp()),
    }
