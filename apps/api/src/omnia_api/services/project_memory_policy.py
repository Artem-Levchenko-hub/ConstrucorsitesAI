from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.services.project_memory import render_project_memory_context

log = structlog.get_logger(__name__)


def project_memory_enabled(*, global_enabled: bool, canary_users: str, user_id: UUID) -> bool:
    if global_enabled:
        return True
    enabled = False
    invalid_entry_count = 0
    for raw in canary_users.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            if UUID(candidate) == user_id:
                enabled = True
        except ValueError:
            invalid_entry_count += 1
    if invalid_entry_count:
        log.warning(
            "project_memory.invalid_canary_allowlist",
            invalid_entry_count=invalid_entry_count,
        )
    return enabled


async def load_project_memory_context(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> str:
    settings = get_settings()
    if not project_memory_enabled(
        global_enabled=settings.use_project_memory,
        canary_users=settings.project_memory_canary_users,
        user_id=user_id,
    ):
        return ""
    return await render_project_memory_context(session, project_id)
