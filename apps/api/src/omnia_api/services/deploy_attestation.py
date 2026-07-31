"""Fail-closed release proof for production deploys."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import Settings
from omnia_api.models.attestation import Attestation
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services.attestation import ATTESTATION_VERSION, verify_digest


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
