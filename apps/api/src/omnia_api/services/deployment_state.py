from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.models.project import Project

ACTIVE_DEPLOY_PHASES = frozenset({"queued", "building", "pushing", "swapping", "cancelling"})


def deployment_is_active(payload: Mapping[str, Any]) -> bool:
    phase = payload.get("phase")
    if phase != "queued":
        return phase in ACTIVE_DEPLOY_PHASES
    # The orchestrator uses an empty queued response as the "never deployed"
    # sentinel. A real queued journal row has a run id or start timestamp.
    return bool(payload.get("run_id") or payload.get("started_at"))


async def current_snapshot_id_fresh(project_id: UUID) -> UUID | None:
    """Read the canonical pointer on a new connection after ambiguous COMMIT."""
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project_id)
        return project.current_snapshot_id if project is not None else None


__all__ = [
    "ACTIVE_DEPLOY_PHASES",
    "current_snapshot_id_fresh",
    "deployment_is_active",
]
