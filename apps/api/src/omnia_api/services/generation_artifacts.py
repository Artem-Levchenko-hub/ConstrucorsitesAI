"""Snapshot and run linkage inside a caller-owned generation transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services.project_memory import record_run_artifacts


async def create_generation_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    run_id: UUID,
    parent_snapshot_id: UUID | None,
    commit_sha: str,
    prompt_text: str,
    model_id: str,
    changed_files: list[str],
) -> tuple[Snapshot, Project | None]:
    """Stage links without committing; return the project for caller runtime work."""
    snapshot = Snapshot(
        project_id=project_id,
        commit_sha=commit_sha,
        prompt_text=prompt_text,
        model_id=model_id,
        parent_id=parent_snapshot_id,
    )
    session.add(snapshot)
    await session.flush()
    project = await session.get(Project, project_id)
    if project is not None:
        project.current_snapshot_id = snapshot.id
    memory_run = await session.get(GenerationRun, run_id)
    if memory_run is not None:
        record_run_artifacts(
            memory_run,
            snapshot_id=snapshot.id,
            commit_sha=commit_sha,
            changed_files=changed_files,
        )
    return snapshot, project
