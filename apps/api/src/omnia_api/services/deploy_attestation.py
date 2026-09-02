"""Fail-closed release proof for production deploys."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import Settings
from omnia_api.models.attestation import Attestation
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.schemas.project import orchestrator_template
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.attestation import (
    ATTESTATION_VERSION,
    build_attestation,
    now_iso,
    verify_digest,
)
from omnia_api.services.project_cell_access import decide_project_cell_access
from omnia_api.services.release_proof import run_release_proof


@dataclass(frozen=True)
class DeployProof:
    passed: bool
    reason: str
    commit_sha: str | None = None
    digest: str | None = None


def blocking_required(settings: Settings) -> bool:
    """Production cannot disable the release proof through a bad env toggle."""
    return settings.env.lower() in {"prod", "production"} or settings.deploy_attestation_blocking


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
            owner is not None and decide_project_cell_access(owner).enabled
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
    """Issue a fresh proof for the current canonical snapshot when one is absent.

    MAX no-code configuration saves create real Git commits after generation.
    Re-synchronising the full snapshot before the checks binds the new attestation
    to the same tree that the launch workflow will publish.
    """
    current = await resolve_deploy_proof(session, project, None)
    if current.passed or current.reason in {"digest_invalid", "project_cell_publish_unavailable"}:
        return current
    if project.current_snapshot_id is None:
        return DeployProof(False, "snapshot_missing")
    snapshot = await session.get(Snapshot, project.current_snapshot_id)
    if snapshot is None or snapshot.project_id != project.id:
        return DeployProof(False, "snapshot_missing")

    runtime = await orchestrator_client.get_status(project.id)
    if runtime.get("state") != "running":
        return DeployProof(False, "runtime_not_running", commit_sha=snapshot.commit_sha)

    files = await asyncio.to_thread(repo_svc.read_files, project.id, snapshot.commit_sha)
    if not files:
        return DeployProof(False, "snapshot_empty", commit_sha=snapshot.commit_sha)
    await orchestrator_client.hot_reload(
        project_id=project.id,
        slug=project.slug,
        files=files,
    )

    verdict = await run_release_proof(
        project.id,
        project.slug,
        require_max_data=project.template == "max_miniapp",
    )
    issued_at = now_iso()
    stack = orchestrator_template(project.template) or project.template
    record = build_attestation(
        gates=[("release", verdict)],
        stack=stack,
        project_id=str(project.id),
        created_at=issued_at,
        commit_sha=snapshot.commit_sha,
    )
    session.add(
        Attestation(
            project_id=project.id,
            snapshot_id=snapshot.id,
            commit_sha=snapshot.commit_sha,
            stack=stack,
            issued_at=issued_at,
            overall_passed=bool(record["overall_passed"]),
            digest=str(record["digest"]),
            gates=record["gates"],
        )
    )
    await session.commit()
    await session.refresh(project)
    return await resolve_deploy_proof(session, project, None)
