from __future__ import annotations

import asyncio
from collections.abc import Iterable
from uuid import UUID

from fastapi import status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.errors import ApiError
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.schemas.project import is_fullstack, orchestrator_template
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.max_project_kit import (
    default_max_project_config,
    max_history_product_files,
    max_project_config_from_files,
    render_max_history_files,
)


def mark_runtime_sync_required(project: Project, paths: Iterable[str]) -> None:
    """Persist the exact source paths whose live representation may be stale."""

    merged = {
        path
        for path in [*(getattr(project, "runtime_sync_paths", None) or []), *paths]
        if isinstance(path, str) and path and len(path) <= 500
    }
    project.runtime_sync_required = True
    project.runtime_sync_paths = sorted(merged)


async def reconcile_locked_runtime(
    session: AsyncSession,
    project: Project,
    *,
    ensure_running: bool,
    full_tree: bool = False,
) -> bool:
    """Reconcile one row-locked project; clear the guard only after exact sync.

    The caller owns the transaction-scoped project advisory lock. Returning
    ``False`` means a stopped runtime was deliberately left guarded for the next
    start; an exception keeps the durable guard unchanged.
    """

    if not getattr(project, "runtime_sync_required", False):
        return True
    if not is_fullstack(project.template):
        project.runtime_sync_required = False
        project.runtime_sync_paths = []
        await session.flush()
        return True
    if project.current_snapshot_id is None:
        raise ApiError(
            "deployment_state_unavailable",
            "Каноническая версия проекта недоступна; запуск временно заблокирован.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    paths = {
        path
        for path in (getattr(project, "runtime_sync_paths", None) or [])
        if isinstance(path, str) and path and len(path) <= 500
    }
    if not paths:
        raise ApiError(
            "deployment_state_unavailable",
            "Не удалось определить файлы для безопасной синхронизации runtime.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if ensure_running:
        template = orchestrator_template(project.template)
        if template is None:
            raise ApiError(
                "deployment_state_unavailable",
                "Runtime проекта нельзя безопасно запустить.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        await orchestrator_client.provision(
            project_id=project.id,
            slug=project.slug,
            template=template,
        )
        full_tree = True
    else:
        runtime = await orchestrator_client.get_status(project.id)
        if runtime.get("state") != "running":
            return False

    snapshot = await session.get(Snapshot, project.current_snapshot_id)
    if snapshot is None:
        raise ApiError(
            "deployment_state_unavailable",
            "Канонический снимок проекта недоступен.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    canonical = await asyncio.to_thread(repo_svc.read_files, project.id, snapshot.commit_sha)
    if full_tree and project.template == "max_miniapp":
        # MAX snapshots deliberately keep product files and may also contain
        # per-project Studio output. Rebuild the live tree from today's trusted
        # managed kit plus the current business config and snapshot product.
        # Comparing the raw product-only snapshot against every live path would
        # delete package.json/bridge/routes; dropping all locked files would in
        # turn lose max-config and the project-bound preview route.
        record = await session.get(MaxProjectConfig, project.id)
        snapshot_config = max_project_config_from_files(canonical)
        config = (
            MaxProjectConfigPayload.model_validate(record.config)
            if record is not None
            else snapshot_config or default_max_project_config(project.name)
        )
        canonical = render_max_history_files(canonical, config, project.id)
    if full_tree:
        live_paths = await orchestrator_client.agent_list_source_files(project.id, project.slug)
        if project.template == "max_miniapp":
            live_paths = list(max_history_product_files(dict.fromkeys(live_paths, "")))
        patch = {
            **{path: "" for path in live_paths if path not in canonical},
            **canonical,
        }
    else:
        patch = {path: canonical.get(path, "") for path in paths}
    await orchestrator_client.hot_reload_exact(project.id, project.slug, patch)
    project.runtime_sync_required = False
    project.runtime_sync_paths = []
    await session.flush()
    return True


async def reconcile_project_runtime(
    project_id: UUID,
    *,
    ensure_running: bool,
) -> bool:
    """Own-session durable reconciliation used after canonical COMMIT."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
            {"project_id": str(project_id)},
        )
        project = await session.get(Project, project_id, with_for_update=True)
        if project is None:
            return False
        synced = await reconcile_locked_runtime(
            session,
            project,
            ensure_running=ensure_running,
        )
        await session.commit()
        return synced


__all__ = [
    "mark_runtime_sync_required",
    "reconcile_locked_runtime",
    "reconcile_project_runtime",
]
