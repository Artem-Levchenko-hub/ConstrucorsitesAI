"""Durable owner-only code restoration, with SQL admission and observed activation."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.restoration import ACTIVE_RESTORATION_STATES, Restoration
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.restoration import (
    RestoreApplyRequest,
    RestoreOperation,
    RestoreReport,
    RestoreRequest,
    RestoreState,
    RuntimeRestoration,
)
from omnia_api.services import repo
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.project_versions import record_restored_version, resolve_version
from omnia_api.services.restoration_runtime import RestorationRuntime


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def assert_no_active_restoration(
    session: AsyncSession,
    project_id: UUID,
    *,
    operation_id: UUID | None = None,
) -> None:
    """Caller MUST hold Project FOR UPDATE until its competing claim is durable."""
    query = select(Restoration.id).where(
        Restoration.project_id == project_id,
        Restoration.state.in_(ACTIVE_RESTORATION_STATES),
    )
    if operation_id is not None:
        query = query.where(Restoration.id != operation_id)
    if await session.scalar(query.limit(1)):
        raise ApiError("conflict", "A version restoration is already in progress", 409)


async def _owned_project(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> Project:
    # Match existing generation/preview/deletion lock order: advisory, project, workspace.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", 404)
    return project


async def lock_restoration_admission(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    *,
    operation_id: UUID | None = None,
) -> Project:
    project = await _owned_project(session, project_id, owner_id)
    await assert_no_active_restoration(session, project_id, operation_id=operation_id)
    if await session.scalar(
        select(GenerationRun.id)
        .where(
            GenerationRun.project_id == project_id,
            GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
        )
        .limit(1)
    ):
        raise ApiError("conflict", "Wait for the current generation to finish", 409)
    return project


async def _owned_operation(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> tuple[Project, Restoration]:
    project = await _owned_project(session, project_id, owner_id)
    operation = await session.scalar(
        select(Restoration)
        .where(Restoration.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None or operation.project_id != project_id or operation.owner_id != owner_id:
        raise ApiError("not_found", "restoration not found", 404)
    return project, operation


def _touch(operation: Restoration) -> None:
    operation.revision += 1
    operation.updated_at = datetime.now(UTC)


def public_operation(operation: Restoration) -> RestoreOperation:
    report = RestoreReport.model_validate(operation.report) if operation.report else None
    runtime = operation.runtime_result or {}
    return RestoreOperation(
        id=operation.id,
        project_id=operation.project_id,
        source_version_id=operation.source_version_id,
        source_snapshot_id=operation.source_snapshot_id,
        base_draft_snapshot_id=operation.base_draft_snapshot_id,
        state=cast(RestoreState, operation.state),
        phase=operation.phase,
        updated_at=operation.updated_at,
        revision=operation.revision,
        candidate_id=operation.candidate_id,
        report=report,
        can_apply=(
            operation.state == "ready"
            and report is not None
            and not report.blockers
            and runtime.get("can_apply") is True
        ),
        can_cancel=(
            operation.state in {"preparing", "checking", "ready", "needs_changes", "reconciling"}
            and operation.apply_digest is None
            and operation.phase != "cancel"
            and (not runtime or runtime.get("can_cancel") is True)
        ),
        applied_version=operation.applied_version_id,
        applied_snapshot_id=operation.applied_snapshot_id,
        error=operation.error,
    )


async def list_operations(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> list[RestoreOperation]:
    await _owned_project(session, project_id, owner_id)
    recent = (
        select(Restoration.id)
        .where(Restoration.project_id == project_id)
        .order_by(
            Restoration.created_at.desc(),
            Restoration.id.desc(),
        )
        .limit(50)
    )
    rows = (
        await session.scalars(
            select(Restoration)
            .where(
                Restoration.project_id == project_id,
                or_(Restoration.id.in_(recent), Restoration.state.in_(ACTIVE_RESTORATION_STATES)),
            )
            .order_by(Restoration.created_at.desc(), Restoration.id.desc())
        )
    ).all()
    return [public_operation(row) for row in rows]


def validate_runtime_response(request: dict[str, Any], result: RuntimeRestoration) -> None:
    for key in ("operation_id", "workspace_id", "project_id", "owner_id"):
        if str(getattr(result, key)) != request[key]:
            raise ValueError("restoration response identity mismatch")
    if result.observed is not None and (
        result.observed.source_commit_sha != request["planned_commit_sha"]
        or result.observed.fencing_epoch != request["fencing_epoch"]
        or result.observed.candidate_id != result.candidate_id
        or (
            request.get("candidate_id") is not None
            and str(result.candidate_id) != request["candidate_id"]
        )
    ):
        raise ValueError("restoration activation evidence mismatch")


async def create_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    request: RestoreRequest,
    runtime: RestorationRuntime,
    *,
    _source_attempt: int = 0,
) -> RestoreOperation:
    project = await _owned_project(session, project_id, owner_id)
    digest = _digest(
        {
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            **request.model_dump(mode="json"),
        }
    )
    existing = await session.scalar(
        select(Restoration).where(
            Restoration.project_id == project_id,
            Restoration.idempotency_key == request.idempotency_key,
        )
    )
    if existing:
        if existing.request_digest != digest:
            raise ApiError("conflict", "Restoration idempotency key was reused", 409)
        identifier = existing.id
        await session.commit()
        return await get_restoration(session, project_id, owner_id, identifier, runtime)
    project = await lock_restoration_admission(session, project_id, owner_id)
    if project.template != "max_miniapp":
        raise ApiError("conflict", "This restoration flow requires a MAX project", 409)
    if project.current_snapshot_id != request.expected_draft_snapshot_id:
        raise ApiError("conflict", "The draft changed; refresh version history", 409)
    version = await session.get(ProjectVersion, request.target_version_id)
    if version is None or version.project_id != project_id:
        raise ApiError("not_found", "version not found", 404)
    status, source = await resolve_version(session, version)
    base = await session.get(Snapshot, request.expected_draft_snapshot_id)
    if (
        status not in {"ready", "unchanged"}
        or source is None
        or base is None
        or base.project_id != project_id
    ):
        raise ApiError("conflict", "Version source is not available for restoration", 409)
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(
            ProjectCellWorkspace.project_id == project_id,
            ProjectCellWorkspace.owner_id == owner_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workspace is None or workspace.state in {"deleting", "deleted"}:
        raise ApiError("conflict", "Project environment is unavailable", 409)
    if _source_attempt >= 3:
        raise ApiError(
            "orchestrator_unavailable", "Среда ещё восстанавливается. Повторите подготовку.", 503
        )
    if await _prepare_source_environment(session, workspace):
        # Lifecycle I/O committed its own durable intent. Recheck every admission
        # condition before binding the restoration to the resulting source epoch.
        return await create_restoration(
            session,
            project_id,
            owner_id,
            request,
            runtime,
            _source_attempt=_source_attempt + 1,
        )
    await _require_idle_cell(session, workspace)
    operation = Restoration(
        id=uuid4(),
        project_id=project_id,
        owner_id=owner_id,
        workspace_id=workspace.id,
        source_version_id=version.id,
        source_snapshot_id=source.id,
        base_draft_snapshot_id=base.id,
        target_commit_sha=source.commit_sha,
        base_commit_sha=base.commit_sha,
        idempotency_key=request.idempotency_key,
        request_digest=digest,
        fencing_epoch=workspace.fencing_epoch,
        state="preparing",
        phase="prepare",
        revision=1,
        runtime_revision=0,
        request_payload={},
    )
    session.add(operation)
    await session.commit()  # Claim survives a crash before Git export or any HTTP dispatch.
    return await _prepare(session, project_id, owner_id, operation.id, runtime)


async def _prepare_source_environment(
    session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> bool:
    from omnia_api.services import orchestrator_client, project_cell_runtime
    from omnia_api.services.project_cell_capacity import release_one_stale_generation_lease

    if workspace.generation_run_id is not None:
        run = await session.get(GenerationRun, workspace.generation_run_id)
        if (
            run is None
            or run.project_id != workspace.project_id
            or run.user_id != workspace.owner_id
            or run.status in ACTIVE_GENERATION_STATUSES
        ):
            raise ApiError("conflict", "Wait for the current generation to finish", 409)
        run_id, workspace_id = run.id, workspace.id
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        await session.commit()  # The lifecycle helper needs its own workspace lock.
        released = await release_one_stale_generation_lease(
            factory,
            requesting_run_id=run_id,
            workspace_id=workspace_id,
            client=orchestrator_client.HttpProjectCellOrchestratorClient(),
            reclaim_for_repair=False,
        )
        if not released:
            raise ApiError(
                "orchestrator_unavailable",
                "Завершаем предыдущую операцию среды. Повторите подготовку.",
                503,
            )
        return True
    wake = await project_cell_runtime._unfinished_owner_wake(session, workspace.id)
    if wake is None:
        await _require_idle_cell(session, workspace)
    resources = await project_cell_runtime._get_cell_resources(workspace.id)
    if resources.state in {"resources_paused", "retained"} or wake is not None:
        await project_cell_runtime._wake_owner_workspace(session, workspace, operation=wake)
        return True
    if resources.state != "resources_ready":
        raise ApiError(
            "orchestrator_unavailable", "Среда проекта ещё не готова. Повторите подготовку.", 503
        )
    return False


async def _require_idle_cell(session: AsyncSession, workspace: ProjectCellWorkspace) -> None:
    if workspace.generation_run_id is not None or await session.scalar(
        select(ProjectCellOperation.id)
        .where(
            ProjectCellOperation.workspace_id == workspace.id,
            ProjectCellOperation.status.in_(
                ("pending", "waiting_capacity", "running", "indeterminate"),
            ),
        )
        .limit(1)
    ):
        raise ApiError("conflict", "Project environment has an active operation", 409)


async def _prepare(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state not in {"preparing", "reconciling"} or operation.phase != "prepare":
        return public_operation(operation)
    if project.current_snapshot_id != operation.base_draft_snapshot_id:
        raise ApiError("conflict", "The draft changed before restoration preparation", 409)
    if not operation.request_payload:
        try:
            prepared = repo.prepare_restore_commit(
                project_id,
                operation.target_commit_sha,
                operation.base_commit_sha,
                operation.id,
            )
        except (ValueError, RuntimeError, OSError):
            operation.state = "failed"
            operation.error = "Version source could not be prepared"
            _touch(operation)
            await session.commit()
            return public_operation(operation)
        operation.planned_commit_sha = prepared["commit_sha"]
        operation.request_payload = {
            "operation_id": str(operation.id),
            "workspace_id": str(operation.workspace_id),
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            "expected_source_head": operation.base_commit_sha,
            "target_commit_sha": operation.target_commit_sha,
            "planned_commit_sha": operation.planned_commit_sha,
            "fencing_epoch": operation.fencing_epoch,
            "files": prepared["files"],
            "current_files": prepared["current_files"],
        }
        await session.commit()
    else:
        await session.commit()
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "prepare")


async def get_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
    *,
    reconcile: bool = True,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state not in ACTIVE_RESTORATION_STATES:
        return public_operation(operation)
    response = public_operation(operation)
    pending_export = not operation.request_payload and operation.state == "preparing"
    should_poll = reconcile and operation.state in ACTIVE_RESTORATION_STATES
    await session.commit()
    if pending_export and reconcile:
        return await _prepare(session, project_id, owner_id, operation_id, runtime)
    if should_poll:
        return await _dispatch(session, project_id, owner_id, operation_id, runtime, "status")
    return response


async def apply_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    request: RestoreApplyRequest,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    digest = _digest(request.model_dump(mode="json"))
    if operation.apply_digest:
        if operation.apply_digest != digest:
            raise ApiError("conflict", "The restoration apply request changed", 409)
        completed = operation.state == "completed"
        response = public_operation(operation)
        await session.commit()
        if completed:
            return response
        return await _dispatch(session, project_id, owner_id, operation_id, runtime, "apply")
    await lock_restoration_admission(session, project_id, owner_id, operation_id=operation_id)
    if (
        request.expected_draft_snapshot_id != operation.base_draft_snapshot_id
        or project.current_snapshot_id != operation.base_draft_snapshot_id
    ):
        raise ApiError("conflict", "The draft changed; prepare restoration again", 409)
    report = public_operation(operation)
    if (
        not report.can_apply
        or report.report is None
        or report.report.revision != request.report_revision
    ):
        raise ApiError("conflict", "The restoration report is not ready or has changed", 409)
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(
            ProjectCellWorkspace.id == operation.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        workspace is None
        or workspace.owner_id != owner_id
        or workspace.project_id != project_id
        or workspace.fencing_epoch != operation.fencing_epoch
    ):
        raise ApiError("conflict", "Project environment changed; prepare restoration again", 409)
    await _require_idle_cell(session, workspace)
    operation.prior_fencing_epoch = workspace.fencing_epoch
    workspace.fencing_epoch += 1
    workspace.version += 1
    operation.fencing_epoch = workspace.fencing_epoch
    operation.apply_digest = digest
    operation.apply_idempotency_key = request.idempotency_key
    operation.state, operation.phase = "applying", "apply"
    operation.request_payload = {
        **operation.request_payload,
        "fencing_epoch": operation.fencing_epoch,
        "expected_fencing_epoch": operation.prior_fencing_epoch,
        "report_revision": request.report_revision,
        "candidate_id": str(operation.candidate_id),
    }
    _touch(operation)
    await session.commit()  # Once-only fence and planned identity precede external activation.
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "apply")


async def cancel_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state == "cancelled":
        return public_operation(operation)
    if operation.phase == "cancel" and operation.state == "reconciling":
        await session.commit()
        return await get_restoration(session, project_id, owner_id, operation_id, runtime)
    if not public_operation(operation).can_cancel:
        raise ApiError("conflict", "Restoration can no longer be cancelled", 409)
    if not operation.request_payload:
        operation.state, operation.phase = "cancelled", "cancel"
        _touch(operation)
        await session.commit()
        return public_operation(operation)
    operation.state, operation.phase = "reconciling", "cancel"
    _touch(operation)
    await session.commit()
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "cancel")


async def _dispatch(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
    action: str,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state not in ACTIVE_RESTORATION_STATES:
        return public_operation(operation)
    if action == "prepare" and (operation.apply_digest is not None or operation.phase == "cancel"):
        return public_operation(operation)
    payload = dict(operation.request_payload)
    await session.commit()  # Never hold SQL locks across runtime preparation/build checks.
    try:
        method = {
            "prepare": runtime.prepare,
            "status": runtime.status,
            "apply": runtime.apply,
            "cancel": runtime.cancel,
        }[action]
        result = await method(payload)
        validate_runtime_response(payload, result)
    except ApiError as exc:
        if action == "prepare" and exc.status_code == 422:
            # Request-model validation runs before the controller creates an operation.
            _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
            if operation.phase == "prepare" and operation.apply_digest is None:
                operation.state = "failed"
                operation.error = "Historical source does not meet restoration requirements"
                _touch(operation)
                await session.commit()
                return public_operation(operation)
        if action == "status" and exc.status_code == 404:
            _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
            intent = operation.phase
            await session.commit()
            if intent in {"prepare", "apply", "cancel"}:
                # A durable claim may precede a lost dispatch. Reuse the exact operation.
                return await _dispatch(session, project_id, owner_id, operation_id, runtime, intent)
        return await _unconfirmed(session, project_id, owner_id, operation_id)
    except (ValueError, OSError, TimeoutError):
        return await _unconfirmed(session, project_id, owner_id, operation_id)
    project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    # A delayed response to the old prepare envelope cannot overwrite an apply claim.
    if payload != operation.request_payload or result.revision < operation.runtime_revision:
        return public_operation(operation)
    if (
        result.revision == operation.runtime_revision
        and operation.runtime_result is not None
    ):
        try:
            # JSON validation retains strict flags/identity checks and supplies
            # defaults introduced after the prior durable receipt was written.
            previous = RuntimeRestoration.model_validate_json(
                json.dumps(operation.runtime_result), strict=True
            )
        except (ValueError, TypeError):
            return await _unconfirmed(session, project_id, owner_id, operation_id)
        if result != previous:
            return await _unconfirmed(session, project_id, owner_id, operation_id)
    if operation.state in {"completed", "cancelled", "failed"}:
        return public_operation(operation)
    operation.runtime_revision = result.revision
    operation.runtime_result = result.model_dump(mode="json")
    operation.candidate_id = result.candidate_id
    operation.report = result.report.model_dump(mode="json") if result.report else None
    operation.error = result.error
    if result.state == "completed":
        if operation.apply_digest is None:
            operation.state = "reconciling"
            operation.error = "Runtime activation has no owner apply claim"
        else:
            await _complete(session, project, operation)
    elif operation.apply_digest and result.state == "failed" and result.observed is None:
        # Failure alone cannot prove whether an accepted activation changed the runtime.
        operation.state = "reconciling"
    elif operation.apply_digest and result.state not in {"applying", "reconciling", "failed"}:
        # A stale pre-apply result is not evidence that an accepted apply did not execute.
        operation.state = "reconciling"
    elif operation.phase == "cancel" and result.state not in {"cancelled", "failed"}:
        operation.state = "reconciling"
    else:
        operation.state = result.state
    _touch(operation)
    await session.commit()
    return public_operation(operation)


async def _unconfirmed(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state in ACTIVE_RESTORATION_STATES:
        operation.state = "reconciling"
        operation.error = "Runtime result is not confirmed; checking the same operation"
        _touch(operation)
        await session.commit()
    return public_operation(operation)


async def _complete(session: AsyncSession, project: Project, operation: Restoration) -> None:
    snapshot_id = uuid5(operation.id, "snapshot")
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(
            ProjectCellWorkspace.id == operation.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        workspace is None
        or workspace.project_id != project.id
        or workspace.owner_id != operation.owner_id
        or workspace.fencing_epoch != operation.fencing_epoch
    ):
        operation.state = "reconciling"
        operation.error = "Runtime activation fence needs reconciliation"
        return
    if project.current_snapshot_id not in {operation.base_draft_snapshot_id, snapshot_id}:
        operation.state = "reconciling"
        operation.error = "Runtime changed but draft identity needs reconciliation"
        return
    source = await session.get(Snapshot, operation.source_snapshot_id)
    if source is None or source.project_id != project.id:
        operation.state = "reconciling"
        operation.error = "Restoration provenance needs reconciliation"
        return
    if operation.planned_commit_sha is None:
        raise ValueError("missing planned restoration commit")
    repo.activate_restore_commit(
        project.id,
        operation.planned_commit_sha,
        operation.base_commit_sha,
        operation.id,
    )
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None:
        snapshot = Snapshot(
            id=snapshot_id,
            project_id=project.id,
            commit_sha=operation.planned_commit_sha,
            parent_id=operation.base_draft_snapshot_id,
            prompt_text="Восстановление версии",
            model_id=source.model_id,
        )
        session.add(snapshot)
        await session.flush()
    elif (
        snapshot.project_id != project.id
        or snapshot.commit_sha != operation.planned_commit_sha
        or snapshot.parent_id != operation.base_draft_snapshot_id
    ):
        raise ValueError("restoration snapshot identity mismatch")
    project.current_snapshot_id = snapshot.id
    version = await record_restored_version(session, project, snapshot, source)
    operation.applied_snapshot_id, operation.applied_version_id = snapshot.id, version.id
    operation.state, operation.phase, operation.error = "completed", "complete", None
