"""Version allocation shares the caller transaction and locks its project row."""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.snapshot import Snapshot


async def _lock(session: AsyncSession, project: Project) -> int:
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    return (
        int(
            await session.scalar(
                select(func.coalesce(func.max(ProjectVersion.number), 0)).where(
                    ProjectVersion.project_id == project.id
                )
            )
            or 0
        )
        + 1
    )


async def ensure_generation_version(
    session: AsyncSession,
    run: GenerationRun,
    project: Project,
) -> ProjectVersion:
    if run.project_id != project.id:
        raise ValueError("generation belongs to another project")
    number = await _lock(session, project)
    existing = await session.scalar(
        select(ProjectVersion).where(ProjectVersion.generation_run_id == run.id)
    )
    if existing:
        return existing
    message = await session.get(Message, run.user_message_id) if run.user_message_id else None
    assistant = (
        await session.get(Message, run.assistant_message_id) if run.assistant_message_id else None
    )
    base = (
        await session.get(Snapshot, project.current_snapshot_id)
        if project.current_snapshot_id
        else None
    )
    version = ProjectVersion(
        project_id=project.id,
        number=number,
        generation_run_id=run.id,
        source_message_id=run.user_message_id,
        base_snapshot_id=base.id if base else None,
        snapshot_id=base.id if base else None,
        commit_sha=base.commit_sha if base else None,
        prompt_text=message.content if message else "",
        model_id=assistant.model_id if assistant else None,
        status="queued",
        created_at=run.created_at,
    )
    session.add(version)
    await session.flush()
    return version


async def record_restored_version(
    session: AsyncSession,
    project: Project,
    new_snapshot: Snapshot,
    target_snapshot: Snapshot,
) -> ProjectVersion:
    if new_snapshot.project_id != project.id or target_snapshot.project_id != project.id:
        raise ValueError("snapshot belongs to another project")
    number = await _lock(session, project)
    existing = await session.scalar(
        select(ProjectVersion).where(
            ProjectVersion.project_id == project.id,
            ProjectVersion.snapshot_id == new_snapshot.id,
            ProjectVersion.generation_run_id.is_(None),
        )
    )
    if existing:
        return existing
    version = ProjectVersion(
        project_id=project.id,
        number=number,
        snapshot_id=new_snapshot.id,
        restored_from_snapshot_id=target_snapshot.id,
        commit_sha=new_snapshot.commit_sha,
        prompt_text=new_snapshot.prompt_text or "Восстановление версии",
        model_id=new_snapshot.model_id,
        status="ready",
    )
    session.add(version)
    await session.flush()
    return version


async def resolve_version(
    session: AsyncSession, version: ProjectVersion
) -> tuple[str, Snapshot | None]:
    run = (
        await session.get(GenerationRun, version.generation_run_id)
        if version.generation_run_id
        else None
    )
    status = version.status
    snapshot_id = version.snapshot_id
    if run:
        status = {
            "pending": "queued",
            "queued_for_capacity": "queued",
            "running": "running",
            "cancel_requested": "running",
            "cancelled": "cancelled",
            "failed": "failed",
            "completed": "unchanged",
        }[run.status]
        if run.status == "completed":
            assistant = (
                await session.get(Message, run.assistant_message_id)
                if run.assistant_message_id
                else None
            )
            candidate = assistant.snapshot_id if assistant else None
            if candidate is None:
                raw = (run.agent_state or {}).get("snapshot_id")
                try:
                    candidate = UUID(str(raw)) if raw else None
                except ValueError:
                    pass
            if candidate:
                final = await session.get(Snapshot, candidate)
                if final and final.project_id == version.project_id:
                    snapshot_id = final.id
                    base = (
                        await session.get(Snapshot, version.base_snapshot_id)
                        if version.base_snapshot_id
                        else None
                    )
                    if (
                        base
                        and base.project_id == version.project_id
                        and final.commit_sha == base.commit_sha
                    ):
                        status = "unchanged"
                        snapshot_id = base.id
                    else:
                        status = "ready"
    snapshot = await session.get(Snapshot, snapshot_id) if snapshot_id else None
    if snapshot and snapshot.project_id != version.project_id:
        snapshot = None
    return status, snapshot


def preview_fields(snapshot: Snapshot | None) -> tuple[str, list[dict[str, Any]]]:
    if snapshot is None:
        return "missing", []
    status = getattr(snapshot, "preview_status", "missing")
    if status != "ready":
        return status if status in {"pending", "failed"} else "missing", []
    if getattr(snapshot, "preview_commit_sha", None) != snapshot.commit_sha:
        return "missing", []
    previews = []
    for index, item in enumerate(getattr(snapshot, "preview_manifest", []) or []):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or type(item.get("width")) is not int
            or type(item.get("height")) is not int
            or item["width"] <= 0
            or item["height"] <= 0
            or not isinstance(item.get("route"), str)
        ):
            continue
        match = re.fullmatch(
            rf"snapshot-previews/{snapshot.project_id}/{snapshot.id}/([0-9a-f]{{64}})\.png",
            item["key"],
        )
        if not match:
            continue
        url = f"/api/projects/{snapshot.project_id}/snapshots/{snapshot.id}/previews/{index}"
        previews.append(
            {
                "url": url + "?v=" + match.group(1),
                "width": item["width"],
                "height": item["height"],
                "route": item["route"],
                "reconstructed": item.get("reconstructed") is True,
            }
        )
    return ("ready", previews) if previews else ("missing", [])


def version_preview_fields(
    state: str,
    snapshot: Snapshot | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Base references are recovery evidence, not output of unfinished attempts."""
    if state in {"queued", "running"}:
        return "pending", []
    if state not in {"ready", "unchanged"}:
        return "missing", []
    return preview_fields(snapshot)
