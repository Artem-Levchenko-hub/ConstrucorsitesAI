"""Release-only continuation for a successful legacy build; never another prompt."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.models.snapshot import Snapshot

COMPLETION_RELEASE_KEY = "completion_release_v1"


class GenerationReleasePending(RuntimeError):
    """The persisted release intent owns completion, without generation replay."""


class CompletionRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: int = Field(default=1, ge=1, le=1)
    run_id: UUID
    project_id: UUID
    owner_id: UUID
    assistant_message_id: UUID
    snapshot_id: UUID
    snapshot_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    workspace_id: UUID
    source_fencing_epoch: int = Field(gt=0)
    operation_id: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_version: str = Field(min_length=1, max_length=128)


def has_completion_release(run: GenerationRun) -> bool:
    return isinstance(run.agent_state, dict) and COMPLETION_RELEASE_KEY in run.agent_state


def completion_release(run: GenerationRun) -> CompletionRelease:
    try:
        value = CompletionRelease.model_validate_json(
            json.dumps(run.agent_state[COMPLETION_RELEASE_KEY]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise GenerationReleasePending("generation release intent is unreadable") from exc
    if (value.run_id != run.id or value.project_id != run.project_id
            or value.owner_id != run.user_id
            or value.assistant_message_id != run.assistant_message_id):
        raise GenerationReleasePending("generation release intent ownership changed")
    return value


async def retain_completion_release(
    session: AsyncSession, *, run: GenerationRun, workspace: ProjectCellWorkspace,
    operation: ProjectCellOperation, profile_version: str,
) -> bool:
    """Commit this intent with the reserved outbox, only for a proven finished body."""
    from yleum_api.services.generation.recovery_contract import RecoveryStateV2
    from yleum_api.services.generation.recovery_execution import recovery_state
    from yleum_api.services.generation_runs import _generation_completion_error

    if isinstance(recovery_state(run), RecoveryStateV2):
        return False  # Its versioned execution/release cycle keeps its own authority.
    if has_completion_release(run):
        marker = completion_release(run)
        if (marker.workspace_id != workspace.id or marker.operation_id != operation.id
                or marker.request_digest != operation.request_digest
                or marker.profile_version != profile_version):
            raise GenerationReleasePending("generation release outbox binding changed")
        return True
    if run.status != "running" or run.assistant_message_id is None:
        return False
    message = await session.get(Message, run.assistant_message_id, populate_existing=True)
    project = await session.get(Project, run.project_id, populate_existing=True)
    if (message is None or message.project_id != run.project_id or message.role != "assistant"
            or message.tokens_out is None or message.snapshot_id is None
            or _generation_completion_error(run, message) is not None
            or project is None or project.owner_id != run.user_id
            or project.current_snapshot_id != message.snapshot_id):
        return False
    snapshot = await session.get(Snapshot, message.snapshot_id, populate_existing=True)
    if (snapshot is None or snapshot.project_id != run.project_id
            or snapshot.preview_status != "ready"
            or workspace.project_id != run.project_id or workspace.owner_id != run.user_id
            or workspace.generation_run_id != run.id or operation.kind != "release"
            or operation.workspace_id != workspace.id or operation.generation_run_id != run.id):
        return False
    marker = CompletionRelease(
        run_id=run.id, project_id=run.project_id, owner_id=run.user_id,
        assistant_message_id=message.id, snapshot_id=snapshot.id,
        snapshot_commit=snapshot.commit_sha, workspace_id=workspace.id,
        source_fencing_epoch=workspace.fencing_epoch, operation_id=operation.id,
        request_digest=operation.request_digest, profile_version=profile_version,
    )
    run.agent_state = {
        **(run.agent_state or {}), COMPLETION_RELEASE_KEY: marker.model_dump(mode="json"),
    }
    return True


async def _bound_release(
    session: AsyncSession, run: GenerationRun, marker: CompletionRelease,
) -> tuple[ProjectCellWorkspace, ProjectCellOperation] | None:
    project = await session.get(Project, marker.project_id, populate_existing=True)
    message = await session.get(Message, marker.assistant_message_id, populate_existing=True)
    snapshot = await session.get(Snapshot, marker.snapshot_id, populate_existing=True)
    workspace = await session.get(ProjectCellWorkspace, marker.workspace_id, populate_existing=True)
    operation = await session.get(ProjectCellOperation, marker.operation_id, populate_existing=True)
    if (project is None or project.owner_id != marker.owner_id
            or project.current_snapshot_id != marker.snapshot_id
            or message is None or message.project_id != marker.project_id
            or message.role != "assistant" or message.tokens_out is None
            or message.snapshot_id != marker.snapshot_id
            or snapshot is None or snapshot.project_id != marker.project_id
            or snapshot.commit_sha != marker.snapshot_commit
            or workspace is None or workspace.project_id != marker.project_id
            or workspace.owner_id != marker.owner_id or workspace.deleted_at is not None
            or operation is None or operation.kind != "release"
            or operation.workspace_id != marker.workspace_id
            or operation.generation_run_id != run.id
            or operation.request_digest != marker.request_digest
            or operation.fencing_epoch not in (None, marker.source_fencing_epoch + 1)):
        return None
    return workspace, operation


async def generation_release_pending(session: AsyncSession, run: GenerationRun) -> bool:
    """A final message or a missing SQL lease alone never proves physical release."""
    from yleum_api.services.generation.recovery_contract import RecoveryStateV2
    from yleum_api.services.generation.recovery_execution import (
        _owned_release_receipt,
        recovery_state,
    )

    if isinstance(recovery_state(run), RecoveryStateV2):
        return False  # Preserve its existing acceptance and activation protocol.
    retained = await session.scalar(select(exists().where(
        ProjectCellWorkspace.generation_run_id == run.id,
        ProjectCellWorkspace.deleted_at.is_(None),
    )))
    if retained:
        return True  # Covers admission before cleanup even creates its intent.
    if not has_completion_release(run):
        return False
    marker = completion_release(run)
    bound = await _bound_release(session, run, marker)
    if bound is None:
        return True
    workspace, operation = bound
    fence = marker.source_fencing_epoch + 1
    if (workspace.generation_run_id is not None or workspace.fencing_epoch != fence
            or not _owned_release_receipt(
                operation, run_id=run.id, workspace_id=workspace.id, fence=fence,
            )):
        return True
    return bool(await session.scalar(select(exists().where(
        ProjectCellActivityLease.workspace_id == workspace.id,
        ProjectCellActivityLease.state == "active",
    ))))


async def _resume_interrupted_worker_release(
    factory: async_sessionmaker[AsyncSession], marker: CompletionRelease,
) -> None:
    """The acquired worker lock proves interruption, never the physical outcome."""
    from yleum_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
    from yleum_api.services.project_cell_lifecycle import replay_indeterminate_cell_operation
    from yleum_api.services.project_cells import (
        ACTIVE_OPERATION_STATUSES,
        _hashed_error,
        lock_workspace_operations,
    )

    async with factory() as session:
        await lock_workspace_operations(session, marker.workspace_id)
        await session.get(Project, marker.project_id, with_for_update=True, populate_existing=True)
        run = await session.get(
            GenerationRun, marker.run_id, with_for_update=True, populate_existing=True,
        )
        if (run is None or run.status != "running" or run.execution_backend != "worker"
                or run.execution_started_at is None or completion_release(run) != marker):
            return
        await session.get(
            ProjectCellWorkspace, marker.workspace_id, with_for_update=True, populate_existing=True,
        )
        await session.get(
            ProjectCellOperation, marker.operation_id, with_for_update=True, populate_existing=True,
        )
        bound = await _bound_release(session, run, marker)
        if bound is None:
            raise GenerationReleasePending("interrupted release ownership changed")
        workspace, operation = bound
        interrupted_error = _hashed_error("generation_executor_interrupted")
        interrupted = operation.status == "indeterminate" and operation.error == interrupted_error
        if (operation.execution_run_id != run.id
                or not (operation.status == "running" or interrupted)):
            return
        if (workspace.generation_run_id != run.id
                or workspace.fencing_epoch != marker.source_fencing_epoch + 1
                or operation.fencing_epoch != workspace.fencing_epoch
                or await session.scalar(select(exists().where(
                    ProjectCellOperation.workspace_id == workspace.id,
                    ProjectCellOperation.id != operation.id,
                    ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                ))) or await session.scalar(select(exists().where(
                    ProjectCellActivityLease.workspace_id == workspace.id,
                    ProjectCellActivityLease.state == "active",
                )))):
            raise GenerationReleasePending("interrupted release is not quiescent")
        operation.status = "indeterminate"
        operation.error = interrupted_error
        operation.finished_at = datetime.now(UTC)
        await session.commit()
    # One exact envelope replay uses the controller's durable idempotency journal.
    # A transport error remains unknown; it is never relabelled as a reader denial.
    await replay_indeterminate_cell_operation(
        factory, marker.operation_id, HttpProjectCellOrchestratorClient(),
    )


async def resume_completion_release(
    factory: async_sessionmaker[AsyncSession], run_id: UUID,
) -> bool:
    """Called only under the existing generation executor session advisory lock."""
    from yleum_api.services.generation.recovery_execution import _require_session_owner
    from yleum_api.services.generation_runs import finalize_generation_run
    from yleum_api.services.project_cell_executor import _release_generation_lease

    await _require_session_owner(run_id, compatibility=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, populate_existing=True)
        if run is None or run.status != "running":
            return False
        marker = completion_release(run)
        bound = await _bound_release(session, run, marker)
        if bound is None:
            raise GenerationReleasePending("generation release receipt binding changed")
        workspace, operation = bound
        retry_at = operation.next_attempt_at
        if retry_at is not None and retry_at > datetime.now(UTC):
            return False
        held = workspace.generation_run_id == run.id
        if workspace.generation_run_id not in (None, run.id):
            raise GenerationReleasePending("generation release lease ownership changed")
    try:
        if held:
            await _resume_interrupted_worker_release(factory, marker)
            await _release_generation_lease(
                session_factory=factory, workspace_id=marker.workspace_id,
                generation_run_id=run_id, profile_version=marker.profile_version,
                completion_ready=True,
            )
        # New Project→Run locks recheck the receipt after SQL clear committed.
        await _require_session_owner(run_id, compatibility=False)
        async with factory() as session:
            return await finalize_generation_run(run_id, session=session) == "completed"
    except GenerationReleasePending:
        async with factory() as session:
            deferred = await session.get(
                ProjectCellOperation, marker.operation_id, with_for_update=True,
                populate_existing=True,
            )
            if (deferred is not None and deferred.generation_run_id == run_id
                    and deferred.workspace_id == marker.workspace_id
                    and deferred.request_digest == marker.request_digest):
                deferred.next_attempt_at = datetime.now(UTC) + timedelta(seconds=10)
                await session.commit()
        return False
