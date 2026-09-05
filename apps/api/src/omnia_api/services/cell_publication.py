"""Authorize an exact accepted Cell candidate, never an owner's preview URL."""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.crypto import decrypt_strong
from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.project_cell import (
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.services import orchestrator_client, project_cell_runtime
from omnia_api.services.project_cell_proofs import (
    ProofDimension,
    proof_identity_from_model,
)


def _unproven(reason: str) -> NoReturn:
    raise ApiError(
        "deploy_not_proven",
        "Публикация не начата: проверка текущей версии не подтверждена. "
        "Работающее приложение не изменено.",
        409,
        details={"reason": reason},
    )


def validate_publication_evidence(
    *,
    project: Project,
    workspace: ProjectCellWorkspace,
    snapshot: Snapshot,
    run: GenerationRun,
    candidate: ProjectCellCandidate,
    proof: ProjectCellProof,
    results: list[ProjectCellProofResult],
) -> dict[str, Any]:
    """Pure fail-closed check, shared by submission and readiness inspection.

    A release operation may advance the workspace fence after generation; it
    must not erase the completed generation's accepted proof. The controller
    checks BOTH fences and the actual current source before sealing its clone.
    """
    if (
        workspace.project_id != project.id
        or workspace.owner_id != project.owner_id
        or workspace.provider != "docker_owner_canary"
    ):
        _unproven("workspace_identity_mismatch")
    if workspace.generation_run_id is not None or workspace.state not in {"ready", "stopped"}:
        _unproven("workspace_busy")
    if (
        snapshot.id != project.current_snapshot_id
        or snapshot.project_id != project.id
        or run.project_id != project.id
        or run.user_id != project.owner_id
        or run.status != "completed"
    ):
        _unproven("snapshot_run_mismatch")
    if (
        candidate.workspace_id != workspace.id
        or candidate.generation_run_id != run.id
        or candidate.status != "accepted"
        or candidate.cancelled
    ):
        _unproven("candidate_not_accepted")
    state = run.agent_state or {}
    finalization = state.get("max_finalization")
    finalization = finalization if isinstance(finalization, dict) else {}
    checkpoint = finalization.get("checkpoint")
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    if (
        state.get("snapshot_id") != str(snapshot.id)
        or state.get("commit_sha") != snapshot.commit_sha
        or finalization.get("outcome") != "complete"
        or checkpoint.get("phase") != "complete"
        or checkpoint.get("candidate_id") != str(candidate.id)
        or checkpoint.get("proof_key") != proof.proof_key
    ):
        _unproven("finalization_not_bound_to_snapshot")
    if (
        proof.workspace_id != workspace.id
        or proof.generation_run_id != run.id
        or proof.fencing_epoch != candidate.fencing_epoch
        or workspace.fencing_epoch < candidate.fencing_epoch
        or proof.workspace_revision != candidate.source_revision
        or proof.schema_data_digest != candidate.migration_digest
    ):
        _unproven("proof_identity_mismatch")
    try:
        identity = proof_identity_from_model(proof)
        if identity.proof_key != proof.proof_key:
            _unproven("proof_digest_invalid")
        if not candidate.build_ref.startswith("build/sha256/"):
            _unproven("build_ref_invalid")
        build_digest = candidate.build_ref.removeprefix("build/sha256/")
        for dimension in (
            ProofDimension.BOOTSTRAP,
            ProofDimension.FULL_BUILD,
            ProofDimension.RUNTIME,
            ProofDimension.RELEASE,
        ):
            key = identity.dimension_key(
                dimension,
                artifact_digest=(
                    build_digest
                    if dimension in {ProofDimension.RUNTIME, ProofDimension.RELEASE}
                    else None
                ),
            )
            matching = [
                row
                for row in results
                if row.workspace_id == workspace.id
                and row.dimension == dimension.value
                and row.dimension_key == key
            ]
            if len(matching) != 1 or matching[0].outcome != "green":
                _unproven(f"{dimension.value}_not_proven")
            expected_ref = (
                candidate.build_ref
                if dimension is ProofDimension.FULL_BUILD
                else candidate.verification_ref
                if dimension is ProofDimension.RELEASE
                else None
            )
            if expected_ref is not None and matching[0].artifact_ref != expected_ref:
                _unproven(f"{dimension.value}_artifact_mismatch")
    except ValueError:
        _unproven("proof_digest_invalid")
    return {
        "project_id": str(project.id),
        "owner_id": str(project.owner_id),
        "slug": project.slug,
        "workspace_id": str(workspace.id),
        "snapshot_id": str(snapshot.id),
        "commit_sha": snapshot.commit_sha,
        "candidate_id": str(candidate.id),
        "source_revision": candidate.source_revision,
        "fencing_epoch": workspace.fencing_epoch,
        "accepted_fencing_epoch": candidate.fencing_epoch,
        "proof_key": proof.proof_key,
        "schema_data_digest": proof.schema_data_digest,
        "build_ref": candidate.build_ref,
        "verification_ref": candidate.verification_ref,
    }


async def load_publication_evidence(
    session: AsyncSession,
    project: Project,
    workspace: ProjectCellWorkspace,
) -> dict[str, Any]:
    if await project_cell_runtime._active_generation(session, project.id) is not None:
        _unproven("generation_active")
    snapshot = (
        await session.get(Snapshot, project.current_snapshot_id)
        if project.current_snapshot_id
        else None
    )
    candidate = await session.scalar(
        select(ProjectCellCandidate).where(
            ProjectCellCandidate.workspace_id == workspace.id,
            ProjectCellCandidate.status == "accepted",
        )
    )
    if snapshot is None or candidate is None:
        _unproven("accepted_candidate_missing")
    run = await session.get(GenerationRun, candidate.generation_run_id)
    if run is None:
        _unproven("generation_missing")
    finalization = (run.agent_state or {}).get("max_finalization")
    checkpoint = finalization.get("checkpoint") if isinstance(finalization, dict) else None
    key = checkpoint.get("proof_key") if isinstance(checkpoint, dict) else None
    if not isinstance(key, str):
        _unproven("finalization_missing")
    proof = await session.scalar(
        select(ProjectCellProof).where(
            ProjectCellProof.workspace_id == workspace.id,
            ProjectCellProof.fencing_epoch == candidate.fencing_epoch,
            ProjectCellProof.proof_key == key,
        )
    )
    if proof is None:
        _unproven("proof_missing")
    results = list(
        await session.scalars(
            select(ProjectCellProofResult).where(
                ProjectCellProofResult.workspace_id == workspace.id,
            )
        )
    )
    return validate_publication_evidence(
        project=project,
        workspace=workspace,
        snapshot=snapshot,
        run=run,
        candidate=candidate,
        proof=proof,
        results=results,
    )


def integration_runtime_env(integration: MaxIntegration) -> dict[str, str]:
    # Private internal transport only; never merge into generated source/env.
    return {
        "MAX_BOT_TOKEN": decrypt_strong(integration.bot_token_enc),
        "MAX_WEBHOOK_SECRET": decrypt_strong(integration.webhook_secret_enc),
        "MAX_API_BASE_URL": "https://platform-api2.max.ru",
    }


async def submit_publication(
    session: AsyncSession,
    project: Project,
    workspace: ProjectCellWorkspace | None,
    *,
    requested_sha: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    if workspace is None:
        _unproven("workspace_missing")
    if project.template != "max_miniapp" or project.deploy_target_id is not None:
        raise ApiError("conflict", "Project Cell публикуется на изолированном хостинге Omnia", 409)
    await project_cell_runtime._try_preview_project_lock(session, project.id)
    await session.refresh(project)
    await session.refresh(workspace)
    pending_wake = await project_cell_runtime._unfinished_owner_wake(session, workspace.id)
    if pending_wake is not None:
        if (
            workspace.project_id != project.id or workspace.owner_id != project.owner_id
            or workspace.generation_run_id is not None
            or await project_cell_runtime._active_generation(session, project.id) is not None
        ):
            _unproven("workspace_busy")
        await project_cell_runtime._wake_owner_workspace(
            session, workspace, operation=pending_wake,
        )
        await project_cell_runtime._try_preview_project_lock(session, project.id)
        await session.refresh(project)
        await session.refresh(workspace)
    evidence = await load_publication_evidence(session, project, workspace)
    if requested_sha is not None and requested_sha != evidence["commit_sha"]:
        _unproven("requested_snapshot_not_current")
    resources = await project_cell_runtime._get_cell_resources(workspace.id)
    if resources.state in {"resources_paused", "retained"}:
        # Use the existing durable fenced owner lifecycle. Never wake behind
        # the API's fence or create a new generation merely to publish.
        await project_cell_runtime._wake_owner_workspace(
            session, workspace,
            operation=await project_cell_runtime._unfinished_owner_wake(session, workspace.id),
        )
        await project_cell_runtime._try_preview_project_lock(session, project.id)
        await session.refresh(project)
        await session.refresh(workspace)
        evidence = await load_publication_evidence(session, project, workspace)
        if requested_sha is not None and requested_sha != evidence["commit_sha"]:
            _unproven("requested_snapshot_not_current")
    elif resources.state != "resources_ready":
        raise ApiError("conflict", "Среда проекта ещё готовится. Повторите публикацию позже", 409)
    integration = await session.scalar(
        select(MaxIntegration).where(
            MaxIntegration.project_id == project.id,
        )
    )
    if (
        integration is None
        or integration.owner_id != project.owner_id
        or integration.status not in {"verified", "active"}
    ):
        raise ApiError("conflict", "Сначала подключите и проверьте MAX-бота", 409)
    record = await session.get(MaxProjectConfig, project.id)
    if record is None or record.owner_id != project.owner_id:
        raise ApiError("conflict", "Сначала сохраните данные приложения и политики", 409)
    config = MaxProjectConfigPayload.model_validate(record.config)
    if not (config.operator.legal_name and config.support.email and config.legal.terms_accepted):
        raise ApiError("conflict", "Заполните владельца, поддержку и подтвердите политики", 409)
    return await orchestrator_client.publish_project_cell(
        project.id,
        {
            **evidence,
            "idempotency_key": idempotency_key or uuid4().hex,
            "runtime_env": integration_runtime_env(integration),
            "business_config": config.model_dump(mode="json", exclude={"max_url_attached"}),
            "business_config_version": record.config_version,
        },
    )


async def update_public_credentials(
    project_id: UUID,
    owner_id: UUID,
    integration: MaxIntegration | None,
) -> None:
    await orchestrator_client.configure_published_cell(
        project_id,
        {
            "owner_id": str(owner_id),
            "runtime_env": integration_runtime_env(integration) if integration else {},
        },
    )
