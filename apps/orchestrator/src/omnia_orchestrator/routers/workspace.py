"""Dark internal Project Cell workspace surface."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Response

from omnia_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellFenceRejected,
    CellIdentityConflict,
    CellIndeterminateOperation,
    CellResourceError,
    CellResourceNames,
    CellRestoreFailed,
    LifecycleMutation,
    WorkspaceLockTimeout,
    WorkspaceLockUnavailable,
    identity_labels,
)
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.internal_auth import verify_internal_token
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.stack_registry import get_stack
from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    WorkspaceProviderUnavailable,
    WorkspaceResourceStatus,
    WorkspaceSpec,
)
from omnia_orchestrator.routers.runtime import (
    _EXEC_DENY,
    _MAX_PREVIEW_BOOTSTRAP_PATH,
    _MAX_PREVIEW_BOOTSTRAP_TTL,
    _collect_workspace_text_files,
    _command_exposes_environment,
    _max_preview_bootstrap_signature,
    _project_workspace_dir,
    _redact_exec_output,
    _safe_app_path,
    _sandbox_name_is_secret,
    _workspace_revision,
)
from omnia_orchestrator.schemas.workspace import (
    WorkspaceAgentBootstrapRequest,
    WorkspaceAgentBootstrapResponse,
    WorkspaceAgentExecRequest,
    WorkspaceAgentExecResponse,
    WorkspaceAgentWriteRequest,
    WorkspaceAgentWriteResponse,
    WorkspaceCapabilityResponse,
    WorkspaceControlRequest,
    WorkspaceDraftApplyRequest,
    WorkspaceDraftApplyResponse,
    WorkspaceDraftPreviewSessionRequest,
    WorkspaceDraftPreviewSessionResponse,
    WorkspaceEnsureRequest,
    WorkspaceObserveRequest,
    WorkspaceOwnerPreviewSessionRequest,
    WorkspaceResourceResponse,
)
from omnia_orchestrator.services import nginx_writer
from omnia_orchestrator.services.cell_draft_support import (
    signed_preview_session_url,
    trusted_template_source,
)
from omnia_orchestrator.services.cell_state import CellWorkspaceState
from omnia_orchestrator.services.docker_cell_resources import DockerCellResourceManager
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider

router = APIRouter(prefix="/internal", tags=["workspace"])
_MAX_AGENT_FILES = 5_000
_MAX_AGENT_FILE_BYTES = 2 * 1024 * 1024
_MAX_AGENT_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_AGENT_EXEC_OUTPUT = 24_000
_MAX_DRAFT_LOG_TAIL = 8_000
_PROJECT_CELL_EXEC_IMAGE = get_stack("max-miniapp-nextjs").image_tag
_PROJECT_CELL_TEMPLATE_DIR = get_stack("max-miniapp-nextjs").template_dir


@router.get(
    "/projects/{project_id}/workspace/capabilities",
    response_model=WorkspaceCapabilityResponse,
)
async def workspace_capabilities(
    project_id: UUID,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceCapabilityResponse:
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    status = await provider.status(project_id)
    return WorkspaceCapabilityResponse(
        project_id=status.project_id,
        provider=status.provider,
        enabled=status.enabled,
        ready=status.ready,
        state=status.state,
        detail=status.detail,
    )


@router.post("/workspaces/ensure", response_model=WorkspaceResourceResponse)
async def ensure_workspace(
    request: WorkspaceEnsureRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceResourceResponse:
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    mutation = LifecycleMutation(
        request.operation_id,
        request.fencing_epoch,
        request.request_digest,
    )
    try:
        handle = await provider.ensure(
            WorkspaceSpec(
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                owner_id=request.owner_id,
                profile_version=request.profile_version,
                generation_run_id=request.generation_run_id,
            ),
            mutation,
        )
    except CellCapacityUnavailable as exc:
        settings = get_settings()
        raise OrchestratorError(
            code="capacity_wait",
            message=exc.reason,
            status_code=429,
            details={
                "operation_id": str(mutation.operation_id),
                "fencing_epoch": mutation.fencing_epoch,
                "request_digest": mutation.request_digest,
                "effect_applied": False,
                "reason": exc.reason,
                "retry_after_seconds": settings.cell_capacity_retry_after_seconds,
            },
        ) from exc
    except (CellFenceRejected, CellIdentityConflict, CellIndeterminateOperation) as exc:
        _raise_pre_effect_conflict(str(exc), mutation)
    except (WorkspaceProviderUnavailable, WorkspaceLockTimeout, WorkspaceLockUnavailable) as exc:
        raise OrchestratorError(
            code="docker_unavailable",
            message=str(exc),
            status_code=503,
        ) from exc
    except (CellRestoreFailed, CellResourceError) as exc:
        raise OrchestratorError(
            code="container_failure",
            message=str(exc),
            status_code=500,
        ) from exc
    manager = _maybe_docker_resource_manager(provider)
    if manager is not None:
        await _sync_lifecycle_draft_preview(manager, request.workspace_id, mutation)
    return WorkspaceResourceResponse(
        workspace_id=handle.workspace_id,
        state="resources_ready",
        provider_ref=handle.provider_ref,
        fencing_epoch=request.fencing_epoch,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )


@router.post(
    "/workspaces/{workspace_id}/control",
    response_model=WorkspaceResourceResponse,
)
async def control_workspace(
    workspace_id: UUID,
    request: WorkspaceControlRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceResourceResponse:
    verify_internal_token(x_internal_token)
    _require_matching_workspace_id(workspace_id, request.workspace_id)
    provider = build_workspace_provider(get_settings())
    mutation = LifecycleMutation(
        request.operation_id,
        request.fencing_epoch,
        request.request_digest,
    )
    try:
        status = await provider.execute_control(
            workspace_id,
            ControlAction(kind=request.kind, checkpoint_ref=request.checkpoint_ref),
            mutation,
        )
    except (CellFenceRejected, CellIdentityConflict, CellIndeterminateOperation) as exc:
        _raise_pre_effect_conflict(str(exc), mutation)
    except (WorkspaceProviderUnavailable, WorkspaceLockTimeout, WorkspaceLockUnavailable) as exc:
        raise OrchestratorError(
            code="docker_unavailable",
            message=str(exc),
            status_code=503,
        ) from exc
    except (CellRestoreFailed, CellResourceError) as exc:
        raise OrchestratorError(
            code="container_failure",
            message=str(exc),
            status_code=500,
        ) from exc
    if request.kind in {"destroy", "wake"}:
        manager = _maybe_docker_resource_manager(provider)
        if manager is not None:
            await _sync_lifecycle_draft_preview(
                manager, workspace_id, mutation, remove=request.kind == "destroy",
            )
    return await _resource_response(status, provider=provider)


@router.post(
    "/workspaces/{workspace_id}/resources/observe",
    response_model=WorkspaceResourceResponse,
)
async def observe_workspace_resources(
    workspace_id: UUID,
    request: WorkspaceObserveRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceResourceResponse:
    verify_internal_token(x_internal_token)
    _require_matching_workspace_id(workspace_id, request.workspace_id)
    provider = build_workspace_provider(get_settings())
    mutation = LifecycleMutation(
        request.operation_id,
        request.fencing_epoch,
        request.request_digest,
    )
    try:
        status = await provider.observe_resources(workspace_id, mutation)
    except (CellFenceRejected, CellIdentityConflict, CellIndeterminateOperation) as exc:
        _raise_pre_effect_conflict(str(exc), mutation)
    except (WorkspaceProviderUnavailable, WorkspaceLockTimeout, WorkspaceLockUnavailable) as exc:
        raise OrchestratorError(
            code="docker_unavailable",
            message=str(exc),
            status_code=503,
        ) from exc
    except (CellRestoreFailed, CellResourceError) as exc:
        raise OrchestratorError(
            code="container_failure",
            message=str(exc),
            status_code=500,
        ) from exc
    return await _resource_response(status, provider=provider)


@router.get(
    "/workspaces/{workspace_id}/resources",
    response_model=WorkspaceResourceResponse,
)
async def get_workspace_resources(
    workspace_id: UUID,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceResourceResponse:
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    try:
        status = await provider.inspect_resources(workspace_id)
    except WorkspaceProviderUnavailable as exc:
        raise OrchestratorError(
            code="docker_unavailable",
            message=str(exc),
            status_code=503,
        ) from exc
    return await _resource_response(status, provider=provider)


@router.post(
    "/workspaces/{workspace_id}/agent/bootstrap",
    response_model=WorkspaceAgentBootstrapResponse,
    response_model_exclude_defaults=True,
)
async def bootstrap_workspace_agent(
    workspace_id: UUID,
    request: WorkspaceAgentBootstrapRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceAgentBootstrapResponse:
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, volume_name = await _workspace_volume_identity(manager, workspace_id)
        generation_run_id, fencing_epoch = _require_generation_lease_match(
            state,
            generation_run_id=request.generation_run_id,
            fencing_epoch=request.fencing_epoch,
        )
        files, seeded_from_project = await _ensure_seed_workspace_files(
            manager,
            state,
            volume_name,
        )
        workspace_revision = _workspace_revision(files)
    return WorkspaceAgentBootstrapResponse(
        files=files,
        seeded_from_project=seeded_from_project,
        generation_run_id=generation_run_id,
        fencing_epoch=fencing_epoch,
        workspace_revision=workspace_revision,
        capabilities=(manager.machine_runtime.capabilities() if manager.machine_runtime else {}),
    )


@router.post(
    "/workspaces/{workspace_id}/agent/write-files",
    response_model=WorkspaceAgentWriteResponse,
)
async def write_workspace_agent_files(
    workspace_id: UUID,
    request: WorkspaceAgentWriteRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceAgentWriteResponse:
    verify_internal_token(x_internal_token)
    writes = _normalize_agent_write_files(request.files)
    deletes = _normalize_agent_delete_paths(request.deletes, writes)
    _require_agent_patch_budget(writes, deletes)
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, volume_name = await _workspace_volume_identity(manager, workspace_id)
        _require_generation_lease_match(
            state,
            generation_run_id=request.generation_run_id,
            fencing_epoch=request.fencing_epoch,
        )
        current_files = await _read_agent_workspace_files(manager, volume_name)
        current_revision = _workspace_revision(current_files)
        desired_files = _apply_agent_workspace_patch(current_files, writes, deletes)
        _require_agent_workspace_budget(desired_files)
        desired_revision = _workspace_revision(desired_files)
        if current_revision != request.expected_revision:
            if current_revision == desired_revision:
                return WorkspaceAgentWriteResponse(
                    written=0,
                    deleted=0,
                    workspace_revision=current_revision,
                )
            _raise_agent_stale_conflict(
                expected_revision=request.expected_revision,
                current_revision=current_revision,
            )
        writes_to_apply = {
            path: content.encode("utf-8")
            for path, content in writes.items()
            if current_files.get(path) != content
        }
        deletes_to_apply = tuple(
            path for path in deletes if path in current_files and path not in writes
        )
        await _prepare_portable_write(manager, state, desired_files)
        if deletes_to_apply:
            await manager.docker.delete_volume_paths(volume_name, deletes_to_apply)
        if writes_to_apply:
            await manager.docker.write_volume_files(volume_name, writes_to_apply)
        updated_files = await _read_agent_workspace_files(manager, volume_name)
        return WorkspaceAgentWriteResponse(
            written=len(writes_to_apply),
            deleted=len(deletes_to_apply),
            workspace_revision=_workspace_revision(updated_files),
        )


@router.post(
    "/workspaces/{workspace_id}/agent/exec",
    response_model=WorkspaceAgentExecResponse,
)
async def exec_workspace_agent_command(
    workspace_id: UUID,
    request: WorkspaceAgentExecRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceAgentExecResponse:
    verify_internal_token(x_internal_token)
    low = request.cmd.strip()
    if not low:
        raise OrchestratorError(
            code="validation_failed",
            message="empty cmd",
            status_code=400,
        )
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, volume_name = await _workspace_volume_identity(manager, workspace_id)
        _require_generation_lease_match(
            state,
            generation_run_id=request.generation_run_id,
            fencing_epoch=request.fencing_epoch,
        )
        current_files = await _read_agent_workspace_files(manager, volume_name)
        current_revision = _workspace_revision(current_files)
        if current_revision != request.expected_revision:
            _raise_agent_stale_conflict(
                expected_revision=request.expected_revision,
                current_revision=current_revision,
            )
        manifest = await _prepare_portable_write(manager, state, current_files)
        if manifest is not None:
            if manager.machine_runtime is None:
                raise OrchestratorError(
                    code="docker_unavailable",
                    message="portable machine provider is unavailable",
                    status_code=503,
                )
            try:
                result = await manager.machine_runtime.execute(state, manifest, request)
            except (CellResourceError, ValueError) as exc:
                raise OrchestratorError(
                    code="container_failure", message=str(exc), status_code=409
                ) from exc
            updated_files = await _read_agent_workspace_files(manager, volume_name)
            return WorkspaceAgentExecResponse(
                ok=result.exit_code == 0 and not result.timed_out,
                exit_code=result.exit_code,
                detail=_redact_exec_output(result.output)[:_MAX_AGENT_EXEC_OUTPUT],
                timed_out=result.timed_out,
                workspace_revision=_workspace_revision(updated_files),
            )
        if any(bad in low for bad in _EXEC_DENY):
            return WorkspaceAgentExecResponse(
                ok=False,
                exit_code=126,
                detail="command blocked by safety denylist",
                workspace_revision=current_revision,
            )
        if _command_exposes_environment(low):
            return WorkspaceAgentExecResponse(
                ok=False,
                exit_code=126,
                detail="command blocked: environment and secret enumeration is not allowed",
                workspace_revision=current_revision,
            )
        exec_spec = _workspace_exec_spec(state)
        names = exec_spec.resource_names
        credentials = manager.credential_store.load_or_create(workspace_id)
        had_draft_runtime = await manager.inspect_draft_runtime(workspace_id) is not None
        restore_failure: CellResourceError | OrchestratorError | None = None
        try:
            if had_draft_runtime:
                await manager.docker.remove_container(names.draft_container_name())
            try:
                result = await manager.docker.run_workspace_command(
                    workspace_volume_name=volume_name,
                    agent_home_volume_name=names.agent_home_volume,
                    labels=identity_labels(exec_spec.spec, "agent-exec"),
                    image=_PROJECT_CELL_EXEC_IMAGE,
                    command=low,
                    internal_network_name=names.internal_network,
                    egress_network_name=names.egress_network,
                    environment=_workspace_agent_exec_env(
                        postgres_container=names.postgres_container,
                        redis_container=names.redis_container,
                        postgres_password=credentials.postgres_password,
                    ),
                    timeout_seconds=request.timeout_seconds,
                )
            finally:
                if had_draft_runtime:
                    try:
                        await manager.ensure_draft_runtime(workspace_id)
                        await _publish_draft_preview(manager, workspace_id)
                    except (CellResourceError, OrchestratorError) as exc:
                        restore_failure = exc
        except CellResourceError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=str(exc),
                status_code=500,
            ) from exc
        updated_files = await _read_agent_workspace_files(manager, volume_name)
    detail = _redact_exec_output(result.output)[:_MAX_AGENT_EXEC_OUTPUT]
    ok = result.exit_code == 0 and result.timed_out is False
    if restore_failure is not None:
        ok = False
        restart_detail = _bounded_redacted_text(str(restore_failure))
        detail = (
            f"{detail}\n\n" if detail else ""
        ) + "Command effects were saved, but the draft runtime restart failed: " + restart_detail
    return WorkspaceAgentExecResponse(
        ok=ok,
        exit_code=result.exit_code,
        detail=detail or ("ok" if ok else "non-zero exit"),
        timed_out=result.timed_out,
        workspace_revision=_workspace_revision(updated_files),
    )


@router.post(
    "/workspaces/{workspace_id}/draft/apply",
    response_model=WorkspaceDraftApplyResponse,
)
async def apply_workspace_draft(
    workspace_id: UUID,
    request: WorkspaceDraftApplyRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceDraftApplyResponse:
    verify_internal_token(x_internal_token)
    writes = _normalize_agent_write_files(request.files)
    deletes = _normalize_agent_delete_paths(request.deletes, writes)
    _require_agent_patch_budget(writes, deletes)
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, volume_name = await _workspace_volume_identity(manager, workspace_id)
        _require_generation_lease_match(
            state,
            generation_run_id=request.generation_run_id,
            fencing_epoch=request.fencing_epoch,
        )
        current_files, _seeded_from_project = await _ensure_seed_workspace_files(
            manager,
            state,
            volume_name,
        )
        current_revision = _workspace_revision(current_files)
        desired_files = _apply_agent_workspace_patch(current_files, writes, deletes)
        _require_agent_workspace_budget(desired_files)
        desired_revision = _workspace_revision(desired_files)
        if (
            current_revision != request.expected_revision
            and desired_revision != current_revision
        ):
            _raise_agent_stale_conflict(
                expected_revision=request.expected_revision,
                current_revision=current_revision,
            )
        manifest = await _prepare_portable_write(manager, state, desired_files)
        if current_revision == request.expected_revision:
            deletes_to_apply = tuple(
                path for path in deletes if path in current_files and path not in writes
            )
            writes_to_apply = {
                path: content.encode("utf-8")
                for path, content in writes.items()
                if current_files.get(path) != content
            }
            if deletes_to_apply:
                await manager.docker.delete_volume_paths(volume_name, deletes_to_apply)
            if writes_to_apply:
                await manager.docker.write_volume_files(volume_name, writes_to_apply)
        if manifest is not None:
            if manager.machine_runtime is None:
                raise OrchestratorError(
                    code="docker_unavailable",
                    message="portable machine provider is unavailable",
                    status_code=503,
                )
            try:
                result = await manager.machine_runtime.apply(state, manifest, request)
                preview_url = (
                    await _publish_draft_preview(manager, workspace_id)
                    if result.exit_code == 0
                    else _draft_preview_url(workspace_id)
                )
            except (CellResourceError, ValueError) as exc:
                raise OrchestratorError(
                    code="container_failure", message=str(exc), status_code=409
                ) from exc
            updated_files = await _read_agent_workspace_files(manager, volume_name)
            return WorkspaceDraftApplyResponse(
                state="draft_running" if result.exit_code == 0 else "draft_failed",
                workspace_revision=_workspace_revision(updated_files),
                preview_url=preview_url,
                package_exit_code=result.exit_code,
                package_stderr_tail=_bounded_redacted_text(result.output),
                migration_exit_code=result.exit_code,
            )
        exec_spec = _workspace_exec_spec(state)
        names = exec_spec.resource_names
        credentials = manager.credential_store.load_or_create(workspace_id)
        await manager.docker.remove_container(names.draft_container_name())
        try:
            migration = await manager.docker.run_workspace_command(
                workspace_volume_name=volume_name,
                agent_home_volume_name=names.agent_home_volume,
                labels=identity_labels(exec_spec.spec, "agent-exec"),
                image=_PROJECT_CELL_EXEC_IMAGE,
                command="node scripts/apply-migrations.mjs",
                internal_network_name=names.internal_network,
                egress_network_name=names.egress_network,
                environment=_workspace_agent_exec_env(
                    postgres_container=names.postgres_container,
                    redis_container=names.redis_container,
                    postgres_password=credentials.postgres_password,
                ),
                timeout_seconds=60,
            )
            if migration.exit_code == 0 and migration.timed_out is False:
                await manager.ensure_draft_runtime(workspace_id)
        except CellResourceError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=str(exc),
                status_code=500,
            ) from exc
        migration_ok = migration.exit_code == 0 and migration.timed_out is False
        preview_url = (
            await _publish_draft_preview(manager, workspace_id)
            if migration_ok
            else _draft_preview_url(workspace_id)
        )
        updated_files = await _read_agent_workspace_files(manager, volume_name)
        runtime_log_tail = await _draft_runtime_log_tail(manager, workspace_id)
    return WorkspaceDraftApplyResponse(
        state="draft_running" if migration_ok else "draft_failed",
        workspace_revision=_workspace_revision(updated_files),
        preview_url=preview_url,
        migration_exit_code=migration.exit_code,
        migration_stderr_tail=_bounded_redacted_text(migration.output),
        runtime_log_tail=runtime_log_tail,
    )


@router.post(
    "/workspaces/{workspace_id}/draft/preview-session",
    response_model=WorkspaceDraftPreviewSessionResponse,
)
async def create_workspace_draft_preview_session(
    workspace_id: UUID,
    request: WorkspaceDraftPreviewSessionRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceDraftPreviewSessionResponse:
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, _volume_name = await _workspace_volume_identity(manager, workspace_id)
        _require_generation_lease_match(
            state,
            generation_run_id=request.generation_run_id,
            fencing_epoch=request.fencing_epoch,
        )
        return await _draft_preview_session(manager, state)


@router.post(
    "/workspaces/{workspace_id}/draft/owner-preview-session",
    response_model=WorkspaceDraftPreviewSessionResponse,
)
async def create_workspace_owner_preview_session(
    workspace_id: UUID,
    request: WorkspaceOwnerPreviewSessionRequest,
    response: Response,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceDraftPreviewSessionResponse:
    """View the retained app after release without reviving agent write access."""
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, _volume_name = await _workspace_volume_identity(manager, workspace_id)
        if state.project_id != request.project_id or state.owner_id != request.owner_id:
            raise OrchestratorError(
                code="conflict", message="workspace owner identity mismatch", status_code=409,
            )
        response.headers["Cache-Control"] = "no-store"
        return await _draft_preview_session(manager, state)


async def _draft_preview_session(
    manager: DockerCellResourceManager,
    state: CellWorkspaceState,
) -> WorkspaceDraftPreviewSessionResponse:
    """Caller holds the operation lock and has verified viewing authority."""
    workspace_id = state.workspace_id
    exec_spec = _workspace_exec_spec(state)
    if _portable_active(manager, workspace_id):
        _require_portable_runtime(manager)
        preview = manager.machine_runtime.preview(state)
        if preview is None or preview[0] != "running":
            raise OrchestratorError(
                code="conflict", message="draft runtime is not running", status_code=409
            )
        auth_secret = manager.machine_runtime.secret(workspace_id)
    else:
        draft = await manager.inspect_draft_runtime(workspace_id)
        if draft is None or draft.state != "running":
            raise OrchestratorError(
                code="conflict",
                message="draft runtime is not running",
                status_code=409,
            )
        try:
            manager._verify_draft_container_record(draft, state)
        except CellIdentityConflict as exc:
            raise OrchestratorError(code="conflict", message=str(exc), status_code=409) from exc
        auth_secret = manager._draft_auth_secret(
            manager.credential_store.load_or_create(workspace_id).postgres_password
        )
    preview_url = _draft_preview_url(workspace_id)
    if not preview_url.startswith("https://"):
        raise OrchestratorError(
            code="container_failure",
            message="draft preview requires an HTTPS development origin", status_code=503,
        )
    expires_at = datetime.now(UTC) + _MAX_PREVIEW_BOOTSTRAP_TTL
    expires = int(expires_at.timestamp())
    signature = _max_preview_bootstrap_signature(
        auth_secret,
        str(exec_spec.spec.project_id),
        expires,
    )
    bootstrap_url = signed_preview_session_url(
        preview_url, _MAX_PREVIEW_BOOTSTRAP_PATH, expires=expires, signature=signature,
    )
    return WorkspaceDraftPreviewSessionResponse(
        workspace_id=workspace_id,
        state="draft_running",
        preview_url=preview_url,
        bootstrap_url=bootstrap_url,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
    )


@router.post(
    "/workspaces/{workspace_id}/draft/owner-start",
    response_model=WorkspaceDraftPreviewSessionResponse,
)
async def start_workspace_owner_preview(
    workspace_id: UUID,
    request: WorkspaceOwnerPreviewSessionRequest,
    response: Response,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceDraftPreviewSessionResponse:
    """Restart only the retained draft; never seed files or acquire an agent lease."""
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    manager = _require_docker_resource_manager(provider)
    async with manager.operation_lock.hold(workspace_id):
        state, _volume_name = await _workspace_volume_identity(manager, workspace_id)
        if state.project_id != request.project_id or state.owner_id != request.owner_id:
            raise OrchestratorError(
                code="conflict", message="workspace owner identity mismatch", status_code=409,
            )
        _workspace_exec_spec(state)
        if state.phase != "completed" or state.active_generation_run_id is not None:
            raise OrchestratorError(
                code="conflict", message="workspace operation is still active", status_code=409,
            )
        if _portable_active(manager, workspace_id):
            _require_portable_runtime(manager)
            await manager.machine_runtime.resume_preview(state)
            await _publish_draft_preview(manager, workspace_id)
            response.headers["Cache-Control"] = "no-store"
            return await _draft_preview_session(manager, state)
        draft = await manager.inspect_draft_runtime(workspace_id)
        if draft is None:
            raise OrchestratorError(
                code="conflict", message="retained draft runtime is missing", status_code=409,
            )
        try:
            manager._verify_draft_container_record(draft, state)
            if draft.state != "running":
                await manager.docker.start_container(draft.name)
            await _publish_draft_preview(manager, workspace_id)
        except CellResourceError as exc:
            raise OrchestratorError(
                code="conflict", message="retained draft runtime could not start", status_code=409,
            ) from exc
        response.headers["Cache-Control"] = "no-store"
        return await _draft_preview_session(manager, state)


async def _resource_response(
    status: WorkspaceResourceStatus,
    *,
    provider: object | None = None,
) -> WorkspaceResourceResponse:
    has_draft_runtime = False
    draft_state: Literal["running", "stopped", "failed"] | None = None
    preview_url: str | None = None
    manager = _maybe_docker_resource_manager(provider)
    if manager is not None:
        state = manager.state_store.load(status.workspace_id)
        if state is not None and state.resource_names is not None:
            draft = await manager.docker.get_container(state.resource_names.draft_container_name())
            if draft is not None:
                has_draft_runtime = True
                preview_url = _draft_preview_url(status.workspace_id)
                try:
                    manager._verify_draft_container_record(draft, state)
                    draft_state = _draft_state_name(draft.state)
                except CellIdentityConflict:
                    draft_state = "failed"
            if _portable_active(manager, status.workspace_id):
                portable = (
                    manager.machine_runtime.preview(state) if manager.machine_runtime else None
                )
                has_draft_runtime = True
                draft_state = _draft_state_name(portable[0]) if portable else "stopped"
                preview_url = _draft_preview_url(status.workspace_id)
    return WorkspaceResourceResponse(
        workspace_id=status.workspace_id,
        state=status.state,
        provider_ref=status.provider_ref,
        fencing_epoch=status.fencing_epoch,
        checkpoint_ref=status.checkpoint_ref,
        has_workspace=status.has_workspace,
        has_agent_home=status.has_agent_home,
        has_postgres=status.has_postgres,
        has_redis=status.has_redis,
        has_draft_runtime=has_draft_runtime,
        draft_state=draft_state,
        preview_url=preview_url,
    )


def _maybe_docker_resource_manager(provider: object | None) -> DockerCellResourceManager | None:
    if (
        isinstance(provider, DockerOwnerCanaryProvider)
        and provider.resource_manager is not None
    ):
        return provider.resource_manager
    return None


def _portable_active(manager: DockerCellResourceManager, workspace_id: UUID) -> bool:
    # Trusted controller state is sticky. Guest deletion of .omnia/cell.json or
    # disabling the provider must never expose the credentialed legacy executor.
    from omnia_orchestrator.services.machine_identity import is_portable_workspace

    return is_portable_workspace(manager.state_store.root, workspace_id) or bool(
        manager.machine_runtime
        and getattr(manager.machine_runtime, "exists", lambda _: False)(workspace_id)
    )


def _require_portable_runtime(manager: DockerCellResourceManager) -> None:
    if manager.machine_runtime is None:
        raise OrchestratorError(
            code="container_failure",
            message="portable machine provider is unavailable",
            status_code=503,
        )


async def _prepare_portable_write(
    manager: DockerCellResourceManager, state: CellWorkspaceState, files: dict[str, str]
) -> MachineManifest | None:
    """Caller holds lease/source lock. Retire credentialed HMR BEFORE guest writes."""
    from omnia_orchestrator.services.machine_identity import machine_identity_root
    from omnia_orchestrator.services.project_machine import write_controller_json

    manifest = _machine_manifest(files, manager=manager, workspace_id=state.workspace_id)
    if manifest is None:
        return None
    _require_portable_runtime(manager)
    draft = await manager.inspect_draft_runtime(state.workspace_id)
    if draft is not None:
        manager._verify_draft_container_record(draft, state)
    marker = machine_identity_root(manager.state_store.root, state.workspace_id) / "portable.json"
    if not marker.exists():
        write_controller_json(
            marker,
            {
                "workspace_id": str(state.workspace_id),
                "project_id": str(state.project_id),
                "kind": "portable",
            },
        )
    if draft is not None:
        await manager.docker.remove_container(draft.name)
        await nginx_writer.unpublish(_draft_preview_host(state.workspace_id))
    return manifest


def _draft_preview_host(workspace_id: UUID) -> str:
    return nginx_writer.dev_host(_draft_preview_slug(workspace_id))


def _draft_preview_slug(workspace_id: UUID) -> str:
    return CellResourceNames.for_workspace(workspace_id).draft_preview_slug()


def _draft_preview_url(workspace_id: UUID) -> str:
    return nginx_writer.dev_url(_draft_preview_slug(workspace_id))


def _draft_state_name(raw_state: str) -> Literal["running", "stopped", "failed"]:
    if raw_state == "running":
        return "running"
    if raw_state in {"created", "paused", "exited"}:
        return "stopped"
    return "failed"


def _bounded_redacted_text(text: str) -> str:
    text = re.sub(r"([?&](?:signature|token)=)[^&\s]+", r"\1[REDACTED]", text)
    return _redact_exec_output(text)[:_MAX_DRAFT_LOG_TAIL]


async def _draft_runtime_log_tail(
    manager: DockerCellResourceManager,
    workspace_id: UUID,
) -> str:
    if _portable_active(manager, workspace_id):
        _require_portable_runtime(manager)
        state = manager.state_store.load(workspace_id)
        return _bounded_redacted_text(await manager.machine_runtime.logs(state))
    draft = await manager.inspect_draft_runtime(workspace_id)
    if draft is None:
        return ""
    return _bounded_redacted_text(
        await manager.docker.read_container_logs(draft.name, tail=200)
    )


async def _sync_lifecycle_draft_preview(
    manager: DockerCellResourceManager,
    workspace_id: UUID,
    mutation: LifecycleMutation,
    *,
    remove: bool = False,
) -> None:
    # Lifecycle providers release this lock before returning. Reacquire it and
    # recheck the operation so an older response cannot overwrite newer ingress.
    async with manager.operation_lock.hold(workspace_id):
        state = manager.state_store.load(workspace_id)
        if (
            state is None
            or state.fencing_epoch != mutation.fencing_epoch
            or state.last_operation_id != mutation.operation_id
        ):
            return
        if remove:
            await nginx_writer.unpublish(_draft_preview_host(workspace_id))
        elif _portable_active(manager, workspace_id):
            _require_portable_runtime(manager)
            preview = manager.machine_runtime.preview(state)
            if preview is None or preview[0] != "running":
                await nginx_writer.unpublish(_draft_preview_host(workspace_id))
            else:
                await _publish_draft_preview(manager, workspace_id)
        elif await manager.inspect_draft_runtime(workspace_id) is not None:
            await _publish_draft_preview(manager, workspace_id)


async def _publish_draft_preview(
    manager: DockerCellResourceManager,
    workspace_id: UUID,
) -> str:
    """Publish while the caller holds the workspace operation lock."""
    state = manager.state_store.load(workspace_id)
    if state is not None and _portable_active(manager, workspace_id):
        _require_portable_runtime(manager)
        preview = manager.machine_runtime.preview(state)
        if preview is None or preview[0] != "running" or not preview[1]:
            raise OrchestratorError(
                code="container_failure",
                message="portable draft gateway is not running",
                status_code=503,
            )
        host = _draft_preview_host(workspace_id)
        await nginx_writer.publish_http(host, 3000, upstream_host=preview[1], private_cell=True)
        if not await nginx_writer.ensure_tls(
            host, 3000, upstream_host=preview[1], private_cell=True
        ):
            await nginx_writer.unpublish(host)
            raise OrchestratorError(
                code="container_failure",
                message="draft preview TLS provisioning failed",
                status_code=503,
            )
        return _draft_preview_url(workspace_id)
    draft = await manager.inspect_draft_runtime(workspace_id)
    if state is None or state.resource_names is None or draft is None or draft.state != "running":
        raise OrchestratorError(
            code="container_failure", message="draft runtime is not running", status_code=503,
        )
    manager._verify_draft_container_record(draft, state)
    # Docker 29 does not activate published ports on internal-only networks.
    # The host can reach the bridge IP without granting the cell external egress.
    upstream_host = draft.network_ipv4.get(state.resource_names.internal_network)
    if not upstream_host:
        raise OrchestratorError(
            code="container_failure",
            message="draft runtime has no internal address",
            status_code=503,
        )
    host = _draft_preview_host(workspace_id)
    await nginx_writer.publish_http(host, 3000, upstream_host=upstream_host, private_cell=True)
    if await nginx_writer.ensure_tls(
        host, 3000, upstream_host=upstream_host, private_cell=True,
    ) is False:
        await nginx_writer.unpublish(host)
        raise OrchestratorError(
            code="container_failure",
            message="draft preview TLS provisioning failed",
            status_code=503,
        )
    preview_url = _draft_preview_url(workspace_id)
    if not preview_url.startswith("https://"):
        raise OrchestratorError(
            code="container_failure",
            message="draft preview requires an HTTPS development origin",
            status_code=503,
        )
    return preview_url


async def _ensure_seed_workspace_files(
    manager: DockerCellResourceManager,
    state: CellWorkspaceState,
    volume_name: str,
) -> tuple[dict[str, str], bool]:
    files = await _read_agent_workspace_files(manager, volume_name)
    if files:
        return files, False
    if await manager.docker.read_volume_files(volume_name):
        raise OrchestratorError(
            code="validation_failed",
            message="workspace contains unsupported binary or oversized files; refusing to reseed",
            status_code=409,
        )
    seeded_from_project = False
    seeded_files: dict[str, str] = {}
    if state.project_id is not None:
        project_root = _project_workspace_dir(str(state.project_id))
        if await asyncio.to_thread(project_root.is_dir):
            seeded_files, _dropped = await _collect_project_workspace_files(project_root)
            seeded_from_project = bool(seeded_files)
    if not seeded_files or manager.machine_runtime is not None:
        template_root = trusted_template_source(_PROJECT_CELL_TEMPLATE_DIR)
        template_files, _dropped = await asyncio.to_thread(
            _collect_workspace_text_files,
            template_root,
        )
        pristine = not seeded_files or seeded_files == template_files
        if not seeded_files:
            seeded_files = template_files
            seeded_from_project = False
        if manager.machine_runtime is not None and pristine:
            from omnia_orchestrator.services.machine_defaults import next_machine_seed

            seeded_files = next_machine_seed(seeded_files)
    if ".omnia/cell.json" in seeded_files:
        await _prepare_portable_write(manager, state, seeded_files)
    await manager.docker.clear_volume(volume_name)
    if seeded_files:
        await manager.docker.write_volume_files(
            volume_name,
            {path: content.encode("utf-8") for path, content in seeded_files.items()},
        )
    return seeded_files, seeded_from_project


def _raise_pre_effect_conflict(message: str, mutation: LifecycleMutation) -> None:
    raise OrchestratorError(
        code="conflict",
        message=message,
        status_code=409,
        details={
            "operation_id": str(mutation.operation_id),
            "fencing_epoch": mutation.fencing_epoch,
            "request_digest": mutation.request_digest,
            "effect_applied": False,
        },
    )


def _require_matching_workspace_id(path_workspace_id: UUID, body_workspace_id: UUID) -> None:
    if path_workspace_id != body_workspace_id:
        raise OrchestratorError(
            code="validation_failed",
            message="workspace_id in path and body must match",
            status_code=400,
        )


def _require_docker_resource_manager(provider: object) -> DockerCellResourceManager:
    if (
        not isinstance(provider, DockerOwnerCanaryProvider)
        or provider.resource_manager is None
    ):
        raise OrchestratorError(
            code="docker_unavailable",
            message="docker owner canary agent workspace is unavailable",
            status_code=503,
        )
    return provider.resource_manager


class _WorkspaceExecSpec:
    def __init__(self, spec: WorkspaceSpec, resource_names: CellResourceNames) -> None:
        self.spec = spec
        self.resource_names = resource_names


def _machine_manifest(
    files: dict[str, str], *, manager: DockerCellResourceManager, workspace_id: UUID
) -> MachineManifest | None:
    try:
        manifest = MachineManifest.from_files(files)
    except ValueError as exc:
        raise OrchestratorError(
            code="validation_failed", message=f"invalid machine manifest: {exc}", status_code=400
        ) from exc
    if manifest is None and _portable_active(manager, workspace_id):
        raise OrchestratorError(
            code="conflict",
            message="portable workspace manifest is missing; legacy downgrade is forbidden",
            status_code=409,
        )
    return manifest


def _workspace_agent_exec_env(
    *,
    postgres_container: str,
    redis_container: str,
    postgres_password: str,
) -> dict[str, str]:
    database_url = (
        f"postgresql://postgres:{postgres_password}@{postgres_container}:5432/postgres"
    )
    return {
        "HOME": "/root",
        "CI": "1",
        "NODE_ENV": "development",
        # The persistent agent home masks /root's image cache. MAX's pinned
        # package manager is bundled at this path and needs no public egress.
        "COREPACK_HOME": "/home/node/.cache/node/corepack",
        "COREPACK_ENABLE_NETWORK": "0",
        "DATABASE_URL": database_url,
        "PGHOST": postgres_container,
        "PGPORT": "5432",
        "PGUSER": "postgres",
        "PGPASSWORD": postgres_password,
        "PGDATABASE": "postgres",
        "REDIS_URL": f"redis://{redis_container}:6379/0",
    }


def _workspace_exec_spec(state: CellWorkspaceState) -> _WorkspaceExecSpec:
    if (
        state.project_id is None
        or state.owner_id is None
        or state.resource_names is None
    ):
        raise OrchestratorError(
            code="not_found",
            message="workspace identity is incomplete",
            status_code=404,
        )
    if state.bundle_state != "resources_ready":
        raise OrchestratorError(
            code="conflict",
            message="workspace resources are not ready",
            status_code=409,
        )
    return _WorkspaceExecSpec(
        WorkspaceSpec(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            profile_version=state.profile_version,
            generation_run_id=state.active_generation_run_id,
        ),
        state.resource_names,
    )


def _require_active_generation_lease(state: CellWorkspaceState) -> tuple[UUID, int]:
    if state.bundle_state != "resources_ready":
        raise OrchestratorError(
            code="conflict",
            message="workspace resources are not ready",
            status_code=409,
        )
    if (
        state.active_generation_run_id is None
        or state.active_generation_fencing_epoch is None
    ):
        raise OrchestratorError(
            code="conflict",
            message="workspace generation lease is not active",
            status_code=409,
        )
    return state.active_generation_run_id, state.active_generation_fencing_epoch


def _require_generation_lease_match(
    state: CellWorkspaceState,
    *,
    generation_run_id: UUID,
    fencing_epoch: int,
) -> tuple[UUID, int]:
    active_generation_run_id, active_fencing_epoch = _require_active_generation_lease(state)
    if (
        generation_run_id != active_generation_run_id
        or fencing_epoch != active_fencing_epoch
    ):
        raise OrchestratorError(
            code="conflict",
            message="workspace generation lease mismatch",
            status_code=409,
            details={
                "effect_applied": False,
                "generation_run_id": str(active_generation_run_id),
                "fencing_epoch": active_fencing_epoch,
            },
        )
    return active_generation_run_id, active_fencing_epoch


def _raise_agent_stale_conflict(
    *,
    expected_revision: str,
    current_revision: str,
) -> None:
    raise OrchestratorError(
        code="conflict",
        message="workspace changed",
        status_code=409,
        details={
            "effect_applied": False,
            "expected_revision": expected_revision,
            "current_revision": current_revision,
        },
    )


async def _workspace_volume_identity(
    manager: DockerCellResourceManager,
    workspace_id: UUID,
) -> tuple[CellWorkspaceState, str]:
    state = manager.state_store.load(workspace_id)
    if (
        state is None
        or state.resource_names is None
        or state.project_id is None
        or state.owner_id is None
    ):
        raise OrchestratorError(
            code="not_found",
            message="workspace state not found",
            status_code=404,
        )
    volume_name = state.resource_names.workspace_volume
    volume = await manager.docker.get_volume(volume_name)
    if volume is None:
        raise OrchestratorError(
            code="not_found",
            message="workspace volume not found",
            status_code=404,
        )
    expected = identity_labels(
        WorkspaceSpec(
            workspace_id=workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            profile_version=state.profile_version,
        ),
        "workspace",
    )
    if volume.name != volume_name or any(
        volume.labels.get(key) != value for key, value in expected.items()
    ):
        raise OrchestratorError(
            code="conflict",
            message="workspace volume identity mismatch",
            status_code=409,
        )
    return state, volume_name


async def _collect_project_workspace_files(project_root: Path) -> tuple[dict[str, str], set[str]]:
    import asyncio

    if not await asyncio.to_thread(project_root.is_dir):
        raise OrchestratorError(
            code="not_found",
            message="project workspace not found",
            status_code=404,
        )
    return await asyncio.to_thread(_collect_workspace_text_files, project_root)


async def _read_agent_workspace_files(
    manager: DockerCellResourceManager,
    volume_name: str,
) -> dict[str, str]:
    # Portable machines retain package/build caches in the workspace volume.
    # Agent revisions cover source only; archiving node_modules/.next here can
    # exceed the bounded control-plane payload after a successful build.
    raw_files = await manager.docker.read_workspace_source_files(volume_name)
    files: dict[str, str] = {}
    total_bytes = 0
    for raw_path, payload in sorted(raw_files.items()):
        safe_path = _safe_app_path(raw_path)
        if _sandbox_name_is_secret(Path(safe_path).name):
            continue
        if len(files) >= _MAX_AGENT_FILES:
            raise OrchestratorError(
                code="validation_failed",
                message="agent workspace exceeds file budget",
                status_code=413,
            )
        if len(payload) > _MAX_AGENT_FILE_BYTES:
            continue
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        total_bytes += len(payload)
        if total_bytes > _MAX_AGENT_TOTAL_BYTES:
            raise OrchestratorError(
                code="validation_failed",
                message="agent workspace exceeds payload budget",
                status_code=413,
            )
        files[safe_path] = content
    return files


def _normalize_agent_write_files(files: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_path, content in files.items():
        safe_path = _safe_app_path(raw_path)
        _require_agent_non_secret_path(safe_path)
        normalized[safe_path] = content
    return normalized


def _normalize_agent_delete_paths(
    deletes: list[str],
    writes: dict[str, str],
) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw_path in deletes:
        safe_path = _safe_app_path(raw_path)
        _require_agent_non_secret_path(safe_path)
        normalized.add(safe_path)
    normalized.difference_update(writes)
    return tuple(sorted(normalized))


def _require_agent_non_secret_path(path: str) -> None:
    if _sandbox_name_is_secret(Path(path).name):
        raise OrchestratorError(
            code="validation_failed",
            message=f"secret file is not allowed in the agent workspace: {path}",
            status_code=403,
        )


def _apply_agent_workspace_patch(
    current_files: dict[str, str],
    writes: dict[str, str],
    deletes: tuple[str, ...],
) -> dict[str, str]:
    desired_files = dict(current_files)
    for path in deletes:
        desired_files.pop(path, None)
    desired_files.update(writes)
    return desired_files


def _require_agent_patch_budget(
    files: dict[str, str],
    deletes: tuple[str, ...],
) -> None:
    if len(files) + len(deletes) > _MAX_AGENT_FILES:
        raise OrchestratorError(
            code="validation_failed",
            message="agent workspace exceeds file budget",
            status_code=413,
        )
    _require_agent_workspace_budget(files)


def _require_agent_workspace_budget(files: dict[str, str]) -> None:
    if len(files) > _MAX_AGENT_FILES:
        raise OrchestratorError(
            code="validation_failed",
            message="agent workspace exceeds file budget",
            status_code=413,
        )
    total_bytes = 0
    for content in files.values():
        payload = content.encode("utf-8")
        if len(payload) > _MAX_AGENT_FILE_BYTES:
            raise OrchestratorError(
                code="validation_failed",
                message="agent workspace file exceeds size budget",
                status_code=413,
            )
        total_bytes += len(payload)
        if total_bytes > _MAX_AGENT_TOTAL_BYTES:
            raise OrchestratorError(
                code="validation_failed",
                message="agent workspace exceeds payload budget",
                status_code=413,
            )
