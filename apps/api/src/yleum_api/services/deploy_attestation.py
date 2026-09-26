"""Fail-closed release proof for production deploys."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.attestation import Attestation
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services.attestation import (
    ATTESTATION_VERSION,
    verify_digest,
)
from yleum_api.services.project_cell_access import decide_project_cell_selection


@dataclass(frozen=True)
class DeployProof:
    passed: bool
    reason: str
    commit_sha: str | None = None
    digest: str | None = None


def _digest_is_valid(attestation: Attestation) -> bool:
    if not attestation.issued_at or not attestation.stack:
        return False
    return verify_digest(
        {
            "version": ATTESTATION_VERSION,
            "project_id": str(attestation.project_id),
            "stack": attestation.stack,
            "commit_sha": attestation.commit_sha,
            "created_at": attestation.issued_at,
            "overall_passed": attestation.overall_passed,
            "gates": attestation.gates,
            "digest": attestation.digest,
        }
    )


async def resolve_deploy_proof(
    session: AsyncSession,
    project: Project,
    requested_sha: str | None,
) -> DeployProof:
    """Resolve proof for the exact code that the orchestrator will deploy."""
    if project.template == "max_miniapp":
        workspace_id = await session.scalar(
            select(ProjectCellWorkspace.id).where(
                ProjectCellWorkspace.project_id == project.id,
            )
        )
        owner = await session.get(User, project.owner_id)
        if workspace_id is not None or (
            owner is not None and decide_project_cell_selection(
                owner, project_cell_enabled=project.project_cell_enabled is True,
            ).enabled
        ):
            return DeployProof(False, "project_cell_publish_unavailable")
    target_sha = requested_sha
    if target_sha is None:
        if project.current_snapshot_id is None:
            return DeployProof(False, "snapshot_missing")
        snapshot = await session.get(Snapshot, project.current_snapshot_id)
        if snapshot is None or snapshot.project_id != project.id:
            return DeployProof(False, "snapshot_missing")
        target_sha = snapshot.commit_sha

    attestation = (
        (
            await session.execute(
                select(Attestation)
                .where(
                    Attestation.project_id == project.id,
                    Attestation.commit_sha == target_sha,
                )
                .order_by(Attestation.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if attestation is None:
        return DeployProof(False, "attestation_missing", commit_sha=target_sha)
    if not _digest_is_valid(attestation):
        return DeployProof(
            False,
            "digest_invalid",
            commit_sha=target_sha,
            digest=attestation.digest,
        )
    if not attestation.overall_passed:
        return DeployProof(
            False,
            "gates_failed",
            commit_sha=target_sha,
            digest=attestation.digest,
        )
    return DeployProof(
        True,
        "proven",
        commit_sha=target_sha,
        digest=attestation.digest,
    )


async def ensure_current_release_proof(
    session: AsyncSession,
    project: Project,
) -> DeployProof:
    """Вернуть текущее состояние доказательства выкатки для приложения MAX.

    Раньше эта функция умела выпустить доказательство сама: поднять старый
    контейнер, залить в него снимок и прогнать проверки. Для проекта в ячейке
    такой путь не работал никогда — `resolve_deploy_proof` отвечает
    `project_cell_publish_unavailable` ещё до него, потому что настоящее
    доказательство выпускает служба ячейки при публикации.
    """
    return await resolve_deploy_proof(session, project, None)
