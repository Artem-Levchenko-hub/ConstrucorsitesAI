from __future__ import annotations

import asyncio
import json
import posixpath
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.agent_builder import Action, Executor
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    promote_generation_after_admission,
)
from omnia_api.services.orchestrator_client import (
    HttpProjectCellOrchestratorClient,
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellAgentOperationStatus,
    ProjectCellPreviewSession,
    ProjectCellWorkspaceIdentity,
    project_cell_agent_bootstrap,
    project_cell_agent_exec,
    project_cell_agent_identity,
    project_cell_agent_operation_status,
    project_cell_agent_write_files,
    project_cell_apply_draft,
    project_cell_create_preview_session,
)
from omnia_api.services.project_cell_capacity import (
    release_one_stale_generation_lease,
    signal_capacity_admitted,
    wait_for_capacity,
)
from omnia_api.services.project_cell_control import inspect_project_cell_control
from omnia_api.services.project_cell_lifecycle import execute_cell_operation
from omnia_api.services.project_cell_proofs import ProofDimension, ProofIdentity
from omnia_api.services.project_cells import (
    ProjectCellBusy,
    ProjectCellNotFound,
    ProjectCellOwnershipError,
    ProjectCellStateConflict,
    ProjectCellValidationError,
    get_or_create_workspace,
    reserve_cell_operation,
    resolve_workspace_profile,
)

_PROJECT_CELL_PROFILE_V1 = "docker-owner-cell-resources-v1"
_PROJECT_CELL_PROFILE_V2 = "docker-owner-cell-resources-v2"
_PROJECT_CELL_BUILD_TIMEOUT_SECONDS = 600
_PROJECT_CELL_SHELL_TIMEOUT_SECONDS = 300
_PROJECT_CELL_DEPENDENCY_METADATA_STRING_FIELDS = ("packageManager",)
_PROJECT_CELL_DEPENDENCY_METADATA_OBJECT_FIELDS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
    "peerDependenciesMeta",
    "dependenciesMeta",
    "overrides",
    "resolutions",
    "pnpm",
)
_PROJECT_CELL_DEPENDENCY_METADATA_FIELDS = (
    _PROJECT_CELL_DEPENDENCY_METADATA_STRING_FIELDS
    + _PROJECT_CELL_DEPENDENCY_METADATA_OBJECT_FIELDS
)


def _sort_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sort_jsonish(item) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, list):
        return [_sort_jsonish(item) for item in value]
    return value


def _normalize_project_cell_dependency_metadata(
    package_json_text: str,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        raw = json.loads(package_json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} package.json is invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} package.json must contain a JSON object")
    normalized: dict[str, Any] = {}
    for field in _PROJECT_CELL_DEPENDENCY_METADATA_STRING_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"{label} package.json {field} must be a string")
        normalized[field] = value
    for field in _PROJECT_CELL_DEPENDENCY_METADATA_OBJECT_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{label} package.json {field} must be an object")
        normalized[field] = _sort_jsonish(value)
    return normalized


def _project_cell_dependency_reuse_error(
    *,
    workspace_package_json: str,
    bundled_package_json: str,
    workspace_lockfile: str | None,
    bundled_lockfile: str | None,
) -> str | None:
    workspace_metadata = _normalize_project_cell_dependency_metadata(
        workspace_package_json,
        label="workspace",
    )
    bundled_metadata = _normalize_project_cell_dependency_metadata(
        bundled_package_json,
        label="bundled image",
    )
    if workspace_metadata != bundled_metadata:
        return (
            "Project Cell build blocked: dependency metadata differs from bundled "
            "image package.json; update dependencies outside the owner-only cell path."
        )
    if (workspace_lockfile is None) != (bundled_lockfile is None):
        return (
            "Project Cell build blocked: pnpm-lock.yaml presence differs from bundled "
            "image; cannot safely reuse bundled node_modules."
        )
    if workspace_lockfile is not None and workspace_lockfile != bundled_lockfile:
        return (
            "Project Cell build blocked: pnpm-lock.yaml differs from bundled image; "
            "cannot safely reuse bundled node_modules."
        )
    return None


def _build_project_cell_build_cmd() -> str:
    dependency_fields_json = json.dumps(list(_PROJECT_CELL_DEPENDENCY_METADATA_FIELDS))
    return "\n".join(
        [
            "set -eu",
            "if [ ! -f package.json ]; then",
            "  echo 'package.json not found' >&2",
            "  exit 1",
            "fi",
            "if [ ! -f /app/package.json ]; then",
            (
                "  echo 'bundled image package.json not found; "
                "cannot validate Project Cell dependencies' >&2"
            ),
            "  exit 1",
            "fi",
            "if [ ! -e /app/node_modules ]; then",
            (
                "  echo 'bundled image node_modules not found; "
                "cannot reuse Project Cell dependencies' >&2"
            ),
            "  exit 1",
            "fi",
            "node <<'NODE'",
            "const fs = require('fs');",
            f"const fields = {dependency_fields_json};",
            "const stringFields = new Set(['packageManager']);",
            "function readText(path) {",
            "  try {",
            "    return fs.readFileSync(path, 'utf8');",
            "  } catch (error) {",
            "    if (error && error.code === 'ENOENT') return null;",
            "    throw error;",
            "  }",
            "}",
            "function parsePackage(label, text) {",
            "  if (text === null) throw new Error(`${label} package.json not found`);",
            "  let data;",
            "  try {",
            "    data = JSON.parse(text);",
            "  } catch (_error) {",
            "    throw new Error(`${label} package.json is invalid JSON`);",
            "  }",
            "  if (!data || Array.isArray(data) || typeof data !== 'object') {",
            "    throw new Error(`${label} package.json must contain a JSON object`);",
            "  }",
            "  return data;",
            "}",
            "function sortValue(value) {",
            "  if (Array.isArray(value)) return value.map(sortValue);",
            "  if (value && typeof value === 'object') {",
            "    const out = {};",
            "    for (const key of Object.keys(value).sort()) out[key] = sortValue(value[key]);",
            "    return out;",
            "  }",
            "  return value;",
            "}",
            "function normalize(label, data) {",
            "  const out = {};",
            "  for (const field of fields) {",
            "    if (!Object.prototype.hasOwnProperty.call(data, field)) continue;",
            "    const value = data[field];",
            "    if (value == null) continue;",
            "    if (stringFields.has(field)) {",
            "      if (typeof value !== 'string') {",
            "        throw new Error(`${label} package.json ${field} must be a string`);",
            "      }",
            "      out[field] = value;",
            "      continue;",
            "    }",
            "    if (Array.isArray(value) || typeof value !== 'object') {",
            "      throw new Error(`${label} package.json ${field} must be an object`);",
            "    }",
            "    out[field] = sortValue(value);",
            "  }",
            "  return out;",
            "}",
            (
                "const workspaceMetadata = normalize("
                "'workspace', parsePackage('workspace', readText('package.json')));"
            ),
            (
                "const bundledMetadata = normalize("
                "'bundled image', parsePackage('bundled image', readText('/app/package.json')));"
            ),
            "if (JSON.stringify(workspaceMetadata) !== JSON.stringify(bundledMetadata)) {",
            (
                "  throw new Error('Project Cell build blocked: dependency metadata "
                "differs from bundled image package.json; update dependencies outside "
                "the owner-only cell path.');"
            ),
            "}",
            "const workspaceLock = readText('pnpm-lock.yaml');",
            "const bundledLock = readText('/app/pnpm-lock.yaml');",
            "if ((workspaceLock === null) !== (bundledLock === null)) {",
            (
                "  throw new Error('Project Cell build blocked: pnpm-lock.yaml "
                "presence differs from bundled image; cannot safely reuse bundled "
                "node_modules.');"
            ),
            "}",
            "if (workspaceLock !== null && workspaceLock !== bundledLock) {",
            (
                "  throw new Error('Project Cell build blocked: pnpm-lock.yaml "
                "differs from bundled image; cannot safely reuse bundled "
                "node_modules.');"
            ),
            "}",
            "NODE",
            # Cells are migration-owned, just like their draft runtime. Schema
            # reconciliation would treat the migration ledger/indexes as drift.
            "node scripts/apply-migrations.mjs",
            "rm -rf -- .next/types/app .next/types/validator.ts || true",
            "if grep -q '\"typecheck\"' package.json; then",
            "  pnpm typecheck",
            "else",
            "  ./node_modules/.bin/tsc --noEmit -p ./tsconfig.json",
            "fi",
        ]
    )


_PROJECT_CELL_BUILD_CMD = _build_project_cell_build_cmd()
_MAX_READ_CHARS = 16_000
_MAX_GREP_MATCHES = 200
_MAX_GREP_CHARS = 16_000


class ProjectCellExecutorUnavailable(RuntimeError):
    """Selected owner-only Project Cell path could not be prepared safely."""


class ProjectCellCommandRole(StrEnum):
    BOOTSTRAP = "bootstrap"
    FAST_CHECK = "fast_check"
    FULL_BUILD = "full_build"


@dataclass(frozen=True, slots=True)
class ProjectCellCommandObservation:
    operation_id: UUID
    role: ProjectCellCommandRole | None
    ok: bool
    timed_out: bool
    redacted_detail: str
    before: ProofIdentity
    after: ProofIdentity
    invalidated_dimensions: frozenset[ProofDimension]


def invalidated_dimensions(
    before: ProofIdentity,
    after: ProofIdentity,
) -> frozenset[ProofDimension]:
    if (
        before.fencing_epoch != after.fencing_epoch
        or before.cell_manifest_digest != after.cell_manifest_digest
        or before.base_image_digest != after.base_image_digest
        or before.toolchain_digest != after.toolchain_digest
        or before.resource_profile_version != after.resource_profile_version
    ):
        return frozenset(ProofDimension)
    invalid: set[ProofDimension] = set()
    if before.dependency_digest != after.dependency_digest:
        invalid.update(ProofDimension)
    if before.workspace_revision != after.workspace_revision:
        invalid.update(
            {
                ProofDimension.FAST_CHECK,
                ProofDimension.FULL_BUILD,
                ProofDimension.RUNTIME,
                ProofDimension.RELEASE,
            }
        )
    if before.schema_data_digest != after.schema_data_digest:
        invalid.update({ProofDimension.RUNTIME, ProofDimension.RELEASE})
    if before.build_config_digest != after.build_config_digest:
        invalid.update(
            {
                ProofDimension.FAST_CHECK,
                ProofDimension.FULL_BUILD,
                ProofDimension.RUNTIME,
                ProofDimension.RELEASE,
            }
        )
    return frozenset(invalid)


def portable_selected(capabilities: dict[str, object], files: dict[str, str]) -> bool:
    # Capability comes from the selected trusted provider; a source file alone
    # must never weaken legacy checks or advertise unavailable execution.
    return capabilities.get("portable_machine") is True and ".omnia/cell.json" in files


@dataclass(frozen=True, slots=True)
class ProjectCellExecutorHandle:
    execute: Executor
    sync_preview: Callable[[], Awaitable[ProjectCellPreviewSyncResult]]
    snapshot_files: Callable[[], Awaitable[dict[str, str]]]
    stage_patch: Callable[[dict[str, str], tuple[str, ...]], Awaitable[None]]
    stage_files: Callable[[dict[str, str]], Awaitable[None]]
    apply_external_files: Callable[[dict[str, str]], Awaitable[None]]
    export_files: Callable[[], Awaitable[dict[str, str]]]
    workspace_id: UUID
    create_preview_session: Callable[[], Awaitable[ProjectCellPreviewSession]]
    release: Callable[[], Awaitable[None]]
    current_identity: Callable[[], Awaitable[ProofIdentity]] | None = None
    run_role: (
        Callable[[ProjectCellCommandRole, UUID], Awaitable[ProjectCellCommandObservation]] | None
    ) = None
    runtime_probe: Callable[[str], Awaitable[Any]] | None = None
    operation_status: Callable[[UUID], Awaitable[ProjectCellAgentOperationStatus]] | None = None
    capabilities: dict[str, object] = dataclass_field(default_factory=dict)
    is_portable: Callable[[], bool] = lambda: False


@dataclass(frozen=True, slots=True)
class ProjectCellPreviewSyncResult:
    generated_files: dict[str, str]
    failure: str | None


async def _release_generation_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    generation_run_id: UUID,
    profile_version: str,
) -> None:
    async with session_factory() as session:
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise ProjectCellExecutorUnavailable("Project Cell workspace disappeared")
        if workspace.generation_run_id is None:
            return
        if workspace.generation_run_id != generation_run_id:
            raise ProjectCellExecutorUnavailable("Project Cell generation lease changed")
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace_id,
            generation_run_id=generation_run_id,
            kind="release",
            idempotency_key=(f"generation:{generation_run_id}:release:{profile_version}"),
            request={},
        )
        await session.commit()
    outcome = await execute_cell_operation(
        session_factory,
        operation.id,
        HttpProjectCellOrchestratorClient(),
    )
    response = outcome.response
    if (
        outcome.status != "completed"
        or response is None
        or response.workspace_id != workspace_id
        or response.fencing_epoch is None
        or response.state != "resources_ready"
    ):
        raise ProjectCellExecutorUnavailable(f"Project Cell release failed: {outcome.status}")
    async with session_factory() as session:
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise ProjectCellExecutorUnavailable("Project Cell workspace disappeared")
        if (
            workspace.generation_run_id == generation_run_id
            and workspace.fencing_epoch == response.fencing_epoch
        ):
            workspace.generation_run_id = None
            workspace.updated_at = datetime.now(UTC)
            await session.commit()


async def maybe_create_project_cell_executor(
    *,
    project_id: UUID,
    project_slug: str,
    project_template: str,
    user_id: UUID,
    generation_run_id: UUID,
    legacy_execute: Executor,
    vision_context: str = "",
    agent_emit: Callable[[dict[str, object]], Awaitable[None]] | None = None,
    capacity_dispatch_token: UUID | None = None,
) -> ProjectCellExecutorHandle | None:
    if project_template != "max_miniapp" or not project_slug:
        return None

    profile_version = (
        _PROJECT_CELL_PROFILE_V2
        if get_settings().use_cell_resource_profile_v2
        else _PROJECT_CELL_PROFILE_V1
    )
    session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        user = await session.get(User, user_id)
        run = await session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == generation_run_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if run is not None:
            await session.refresh(run)
        if project is None or user is None or run is None:
            return None
        readiness = await inspect_project_cell_control(user, project_id)
        if not readiness.selected:
            existing_cell_id = await session.scalar(
                select(ProjectCellWorkspace.id).where(
                    ProjectCellWorkspace.project_id == project_id,
                )
            )
            if existing_cell_id is not None:
                raise ProjectCellExecutorUnavailable(
                    "Project already belongs to a Project Cell; legacy execution is disabled"
                )
            return None
        if not readiness.ready:
            raise ProjectCellExecutorUnavailable(
                f"Project Cell selected but not ready: {readiness.reason}"
            )
        try:
            workspace, _ = await get_or_create_workspace(
                session,
                project=project,
                user=user,
                run=run,
            )
            if workspace.generation_run_id not in {None, run.id}:
                previous_run = await session.get(GenerationRun, workspace.generation_run_id)
                if previous_run is None or previous_run.status in ACTIVE_GENERATION_STATUSES:
                    raise ProjectCellBusy("Project Cell still belongs to an active generation")
                prior_ensure_effect = await session.scalar(
                    select(ProjectCellOperation.id)
                    .where(
                        ProjectCellOperation.workspace_id == workspace.id,
                        ProjectCellOperation.kind == "ensure",
                        ProjectCellOperation.status.not_in(("failed", "cancelled")),
                    )
                    .limit(1)
                )
                if prior_ensure_effect is not None:
                    # Preserve the old terminal run's identity until its resources
                    # are reconciled and the physical lease is released.
                    await session.commit()
                    for _attempt in range(3):
                        if await release_one_stale_generation_lease(
                            session_factory,
                            requesting_run_id=run.id,
                            client=HttpProjectCellOrchestratorClient(),
                            workspace_id=workspace.id,
                        ):
                            break
                    await session.refresh(workspace)
                    if workspace.generation_run_id is not None:
                        remaining_effect = await session.scalar(
                            select(ProjectCellOperation.id)
                            .where(
                                ProjectCellOperation.workspace_id == workspace.id,
                                ProjectCellOperation.generation_run_id == previous_run.id,
                                ProjectCellOperation.kind == "ensure",
                                ProjectCellOperation.status.not_in(("failed", "cancelled")),
                            )
                            .limit(1)
                        )
                        if remaining_effect is not None:
                            raise ProjectCellStateConflict(
                                "Previous Project Cell ensure needs reconciliation"
                            )
                    await session.refresh(run)
            if run.status == "cancel_requested" or run.status not in ACTIVE_GENERATION_STATUSES:
                raise ProjectCellStateConflict("Generation is no longer active")
            workspace.generation_run_id = run.id
            profile_version = await resolve_workspace_profile(session, workspace, profile_version)
            operation, _ = await reserve_cell_operation(
                session,
                workspace_id=workspace.id,
                generation_run_id=run.id,
                kind="ensure",
                idempotency_key=f"generation:{run.id}:ensure:{profile_version}",
                request={"profile_version": profile_version},
            )
            await session.commit()
        except (
            ProjectCellBusy,
            ProjectCellNotFound,
            ProjectCellOwnershipError,
            ProjectCellStateConflict,
            ProjectCellValidationError,
        ) as exc:
            raise ProjectCellExecutorUnavailable(str(exc)) from exc
        workspace_id = workspace.id

    async def _discard_progress(_payload: dict[str, object]) -> None:
        return None

    outcome = await wait_for_capacity(
        session_factory,
        run_id=generation_run_id,
        operation_id=operation.id,
        client=HttpProjectCellOrchestratorClient(),
        emit=agent_emit or _discard_progress,
        dispatch_token=capacity_dispatch_token,
        initial_attempt=lambda: execute_cell_operation(
            session_factory,
            operation.id,
            HttpProjectCellOrchestratorClient(),
        ),
    )
    response = outcome.response
    if (
        outcome.status != "completed"
        or response is None
        or response.workspace_id != workspace_id
        or response.state != "resources_ready"
        or response.provider_ref is None
        or response.fencing_epoch is None
        or not all(
            (
                response.has_workspace,
                response.has_agent_home,
                response.has_postgres,
                response.has_redis,
            )
        )
    ):
        resource_state = response.state if response is not None else "no_response"
        raise ProjectCellExecutorUnavailable(
            f"Project Cell ensure failed: operation={outcome.status}, resources={resource_state}"
        )
    admission = await promote_generation_after_admission(
        session_factory,
        run_id=generation_run_id,
        dispatch_token=capacity_dispatch_token,
    )
    if admission != "admitted":
        if admission == "cancelled":
            release_task = asyncio.create_task(
                _release_generation_lease(
                    session_factory=session_factory,
                    workspace_id=workspace_id,
                    generation_run_id=generation_run_id,
                    profile_version=profile_version,
                )
            )
            try:
                await asyncio.shield(release_task)
            except asyncio.CancelledError:
                await release_task
                raise
            raise ProjectCellExecutorUnavailable("generation cancelled during admission")
        raise ProjectCellExecutorUnavailable(f"Project Cell admission ownership lost: {admission}")
    # Resource readiness is proven by ensure, not by the later agent bootstrap.
    # Persist it now so a failed bootstrap remains reclaimable.
    await _mark_workspace_ready(
        session_factory=session_factory,
        workspace_id=workspace_id,
        generation_run_id=generation_run_id,
        provider_ref=response.provider_ref,
        fencing_epoch=response.fencing_epoch,
    )
    signal_capacity_admitted(generation_run_id)
    try:
        snapshot = await project_cell_agent_bootstrap(
            workspace_id,
            generation_run_id=generation_run_id,
            fencing_epoch=response.fencing_epoch,
        )
    except (OrchestratorUnavailable, OrchestratorBadRequest) as exc:
        raise ProjectCellExecutorUnavailable(exc.message) from exc
    if (
        snapshot.generation_run_id != generation_run_id
        or snapshot.fencing_epoch != response.fencing_epoch
    ):
        raise ProjectCellExecutorUnavailable("Project Cell active lease does not match the run")
    workspace_files = {_normalize_path(path): content for path, content in snapshot.files.items()}
    leased_run_id = generation_run_id
    fencing_epoch = response.fencing_epoch
    workspace_revision = snapshot.workspace_revision
    baseline_files = dict(workspace_files)
    synced_files = dict(workspace_files)
    dirty = False
    runtime_log_tail = ""
    preview_synced = False
    capabilities = dict(snapshot.capabilities)
    last_identity: ProofIdentity | None = None

    def _is_portable() -> bool:
        return portable_selected(capabilities, workspace_files)

    def _proof_identity(identity: ProjectCellWorkspaceIdentity) -> ProofIdentity:
        return ProofIdentity(
            workspace_id=workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
            workspace_revision=identity.workspace_revision,
            dependency_digest=identity.dependency_digest,
            schema_data_digest=identity.schema_data_digest,
            cell_manifest_digest=identity.cell_manifest_digest,
            base_image_digest=identity.environment_digest,
            toolchain_digest=identity.environment_digest,
            resource_profile_version=profile_version,
            build_config_digest=identity.build_config_digest,
        )

    def _command_observation(
        result: Any,
        role: ProjectCellCommandRole | None,
    ) -> ProjectCellCommandObservation:
        if (
            result.operation_id is None
            or result.before_identity is None
            or result.after_identity is None
        ):
            raise ProjectCellExecutorUnavailable(
                "Project Cell orchestrator omitted identity-aware command evidence"
            )
        before = _proof_identity(result.before_identity)
        after = _proof_identity(result.after_identity)
        return ProjectCellCommandObservation(
            operation_id=result.operation_id,
            role=role,
            ok=result.ok,
            timed_out=result.timed_out,
            redacted_detail=result.detail,
            before=before,
            after=after,
            invalidated_dimensions=invalidated_dimensions(before, after),
        )

    async def _current_identity() -> ProofIdentity:
        nonlocal last_identity
        if not _is_portable():
            raise ProjectCellExecutorUnavailable("portable Project Cell identity is unavailable")
        identity = await project_cell_agent_identity(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
        )
        last_identity = _proof_identity(identity)
        return last_identity

    async def _run_role(
        role: ProjectCellCommandRole,
        operation_id: UUID,
    ) -> ProjectCellCommandObservation:
        nonlocal dirty, last_identity, preview_synced, synced_files, workspace_revision
        timeout = {
            ProjectCellCommandRole.BOOTSTRAP: 900,
            ProjectCellCommandRole.FAST_CHECK: 480,
            ProjectCellCommandRole.FULL_BUILD: 900,
        }[role]
        result = await project_cell_agent_exec(
            workspace_id,
            f"omnia:{role.value}",
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
            expected_revision=workspace_revision,
            timeout_seconds=timeout,
            task_role=role.value,
            operation_id=operation_id,
        )
        workspace_revision = result.workspace_revision
        observation = _command_observation(result, role)
        last_identity = observation.after
        if observation.invalidated_dimensions:
            preview_synced = False
        await _refresh_workspace_from_cell()
        if role is ProjectCellCommandRole.FULL_BUILD and observation.ok:
            synced_files = dict(workspace_files)
            dirty = False
            preview_synced = True
        return observation

    async def _operation_status(operation_id: UUID) -> ProjectCellAgentOperationStatus:
        return await project_cell_agent_operation_status(workspace_id, operation_id)

    async def _runtime_probe(proof_key: str) -> Any:
        from omnia_api.services.max_runtime_probe import probe_max_cell_runtime

        preview = await project_cell_create_preview_session(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
        )
        return await probe_max_cell_runtime(
            preview,
            portable_project_id=project_id,
            expected_epoch=fencing_epoch,
            proof_key=proof_key,
        )

    async def _persist_files(
        *,
        writes: dict[str, str] | None = None,
        deletes: tuple[str, ...] = (),
    ) -> None:
        nonlocal workspace_revision
        normalized_writes = _normalize_files(writes or {})
        normalized_deletes = _normalize_delete_paths(deletes)
        if set(normalized_writes).intersection(normalized_deletes):
            raise ValueError("the same path cannot be written and deleted")
        if not normalized_writes and not normalized_deletes:
            return
        response = await project_cell_agent_write_files(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
            expected_revision=workspace_revision,
            files=normalized_writes,
            deletes=normalized_deletes,
        )
        workspace_revision = response.workspace_revision

    async def _stage_patch(writes: dict[str, str], deletes: tuple[str, ...] = ()) -> None:
        nonlocal dirty
        normalized_writes = _normalize_files(writes)
        normalized_deletes = _normalize_delete_paths(deletes)
        if set(normalized_writes).intersection(normalized_deletes):
            raise ValueError("the same path cannot be written and deleted")
        await _persist_files(writes=normalized_writes, deletes=normalized_deletes)
        _apply_to_local_state(workspace_files, writes=normalized_writes, deletes=normalized_deletes)
        dirty = bool(_diff_files(synced_files, workspace_files))

    async def _apply_external_files(files: dict[str, str]) -> None:
        nonlocal synced_files, dirty, preview_synced
        await _stage_files(files)
        synced_files = dict(workspace_files)
        dirty = False
        preview_synced = False

    async def _stage_files(files: dict[str, str]) -> None:
        normalized = _normalize_files(files)
        writes, deletes = _split_external_files(normalized)
        await _stage_patch(writes, deletes)

    async def _refresh_workspace_from_cell() -> dict[str, str]:
        nonlocal dirty, fencing_epoch, workspace_revision
        refreshed = await project_cell_agent_bootstrap(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
        )
        if refreshed.generation_run_id != leased_run_id:
            raise ProjectCellExecutorUnavailable("Project Cell active lease changed mid-run")
        if refreshed.fencing_epoch != fencing_epoch:
            raise ProjectCellExecutorUnavailable("Project Cell fencing epoch changed mid-run")
        normalized = _normalize_files(refreshed.files)
        changed = _diff_files(workspace_files, normalized)
        workspace_files.clear()
        workspace_files.update(normalized)
        fencing_epoch = refreshed.fencing_epoch
        workspace_revision = refreshed.workspace_revision
        dirty = bool(_diff_files(synced_files, workspace_files))
        return changed

    async def _export_files() -> dict[str, str]:
        return _diff_files(baseline_files, workspace_files)

    async def _snapshot_files() -> dict[str, str]:
        return dict(workspace_files)

    async def _release() -> None:
        await _release_generation_lease(
            session_factory=session_factory,
            workspace_id=workspace_id,
            generation_run_id=leased_run_id,
            profile_version=profile_version,
        )

    async def _sync_preview() -> ProjectCellPreviewSyncResult:
        nonlocal synced_files, dirty, workspace_revision, runtime_log_tail, preview_synced
        # A healthy, already-synced draft must survive read-only checks. Applying
        # even an empty patch recreates the container and cold-compiles every route.
        if not dirty and preview_synced:
            try:
                await project_cell_create_preview_session(
                    workspace_id,
                    generation_run_id=leased_run_id,
                    fencing_epoch=fencing_epoch,
                )
            except OrchestratorBadRequest as exc:
                # A stopped draft needs the normal fenced apply/recovery path.
                if exc.status_code != 409 or exc.message != "draft runtime is not running":
                    raise
            else:
                return ProjectCellPreviewSyncResult(generated_files={}, failure=None)
        if _is_portable() and get_settings().use_max_finalization_coordinator:
            if dirty:
                return ProjectCellPreviewSyncResult(
                    generated_files={},
                    failure=None,
                )
            try:
                await project_cell_create_preview_session(
                    workspace_id,
                    generation_run_id=leased_run_id,
                    fencing_epoch=fencing_epoch,
                )
            except OrchestratorBadRequest as exc:
                return ProjectCellPreviewSyncResult(
                    generated_files={},
                    failure=f"preview reconciliation failed: {exc.message}",
                )
            preview_synced = True
            return ProjectCellPreviewSyncResult(generated_files={}, failure=None)
        preview_synced = False
        diff = _diff_files(synced_files, workspace_files)
        draft = await project_cell_apply_draft(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
            expected_revision=workspace_revision,
            files={path: value for path, value in diff.items() if path in workspace_files},
            deletes=tuple(path for path in diff if path not in workspace_files),
        )
        workspace_revision = draft.workspace_revision
        runtime_log_tail = draft.runtime_log_tail
        # Install/migration steps may generate files even on failure.
        generated_files = await _refresh_workspace_from_cell()
        failure = _sync_failure_detail(
            {
                "package_exit_code": draft.package_exit_code,
                "package_stderr_tail": draft.package_stderr_tail,
                "migration_exit_code": draft.migration_exit_code,
                "migration_stderr_tail": draft.migration_stderr_tail,
            }
        )
        if failure is not None:
            return ProjectCellPreviewSyncResult(
                generated_files=generated_files,
                failure=failure,
            )
        synced_files = dict(workspace_files)
        dirty = False
        preview_synced = True
        return ProjectCellPreviewSyncResult(
            generated_files=generated_files,
            failure=None,
        )

    async def _create_preview_session() -> ProjectCellPreviewSession:
        sync = await _sync_preview()
        if sync.failure:
            raise ProjectCellExecutorUnavailable(sync.failure)
        return await project_cell_create_preview_session(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
        )

    async def _execute_action(action: Action) -> dict[str, Any]:
        nonlocal dirty, workspace_revision
        try:
            if action.name == "list_dir":
                return {
                    "ok": True,
                    "detail": _list_dir(workspace_files, action.path or "."),
                }
            if action.name == "read_file":
                path = _normalize_path(action.path)
                content = workspace_files.get(path)
                if content is None:
                    return {"ok": False, "error": f"not found: {path}"}
                return {"ok": True, "content": _truncate(content, _MAX_READ_CHARS)}
            if action.name == "grep":
                pattern = str(action.args.get("pattern", ""))
                path = str(action.path or "src")
                return {"ok": True, "detail": _grep(workspace_files, pattern=pattern, path=path)}
            if action.name == "write_file":
                content = action.args.get("content")
                if not isinstance(content, str) or not action.path:
                    return {"ok": False, "error": "write_file needs path + content"}
                path = _normalize_path(action.path)
                await _persist_files(writes={path: content})
                _apply_to_local_state(workspace_files, writes={path: content})
                dirty = True
                return {
                    "ok": True,
                    "content": content,
                    "detail": f"wrote {path} ({len(content)} bytes)",
                }
            if action.name == "edit_file":
                search = action.args.get("search")
                replace = action.args.get("replace")
                if not action.path or not isinstance(search, str) or replace is None:
                    return {"ok": False, "error": "edit_file needs path, search, replace"}
                path = _normalize_path(action.path)
                current = workspace_files.get(path)
                if current is None:
                    return {"ok": False, "error": f"not found: {path}"}
                if search not in current:
                    return {
                        "ok": False,
                        "error": (
                            "search text not found exactly; read the file and copy it byte-for-byte"
                        ),
                    }
                if current.count(search) > 1:
                    return {
                        "ok": False,
                        "error": "search text is not unique; add surrounding lines",
                    }
                new_content = current.replace(search, str(replace), 1)
                await _persist_files(writes={path: new_content})
                _apply_to_local_state(workspace_files, writes={path: new_content})
                dirty = True
                return {
                    "ok": True,
                    "content": new_content,
                    "detail": f"patched {path}",
                }
            if action.name == "build":
                portable = _is_portable()
                if portable and get_settings().use_max_finalization_coordinator:
                    observation = await _run_role(ProjectCellCommandRole.FAST_CHECK, uuid4())
                    return {
                        "ok": observation.ok,
                        "detail": observation.redacted_detail
                        or ("ok" if observation.ok else "non-zero exit"),
                        "environment_mutated": bool(observation.invalidated_dimensions),
                        "invalidated_dimensions": sorted(
                            item.value for item in observation.invalidated_dimensions
                        ),
                    }
                result = await project_cell_agent_exec(
                    workspace_id,
                    "omnia:build" if portable else _PROJECT_CELL_BUILD_CMD,
                    generation_run_id=leased_run_id,
                    fencing_epoch=fencing_epoch,
                    expected_revision=workspace_revision,
                    timeout_seconds=_PROJECT_CELL_BUILD_TIMEOUT_SECONDS,
                    task_role="build" if portable else None,
                    operation_id=uuid4() if portable else None,
                )
                workspace_revision = result.workspace_revision
                await _refresh_workspace_from_cell()
                detail = result.detail
                return {
                    "ok": result.ok,
                    "detail": detail or ("ok" if result.ok else "non-zero exit"),
                }
            if action.name == "bash":
                cmd = str(action.args.get("cmd") or "").strip()
                if not cmd:
                    return {"ok": False, "error": "bash needs a non-empty cmd string"}
                result = await project_cell_agent_exec(
                    workspace_id,
                    cmd,
                    generation_run_id=leased_run_id,
                    fencing_epoch=fencing_epoch,
                    expected_revision=workspace_revision,
                    timeout_seconds=_PROJECT_CELL_SHELL_TIMEOUT_SECONDS,
                    operation_id=uuid4() if _is_portable() else None,
                )
                workspace_revision = result.workspace_revision
                changed_files = await _refresh_workspace_from_cell()
                detail = result.detail or "(no output)"
                if result.timed_out:
                    detail += "\n\nCommand timed out; partial workspace changes were synced back."
                response: dict[str, Any] = {
                    "ok": result.ok,
                    "detail": detail,
                }
                if _is_portable() and result.before_identity and result.after_identity:
                    observation = _command_observation(result, None)
                    response["environment_mutated"] = bool(observation.invalidated_dimensions)
                    response["invalidated_dimensions"] = sorted(
                        item.value for item in observation.invalidated_dimensions
                    )
                    response["mutation"] = {
                        "source_changed": (
                            observation.before.workspace_revision
                            != observation.after.workspace_revision
                        ),
                        "dependency_changed": (
                            observation.before.dependency_digest
                            != observation.after.dependency_digest
                        ),
                        "schema_data_changed": (
                            observation.before.schema_data_digest
                            != observation.after.schema_data_digest
                        ),
                        "manifest_changed": (
                            observation.before.cell_manifest_digest
                            != observation.after.cell_manifest_digest
                        ),
                        "environment_changed": (
                            observation.before.toolchain_digest
                            != observation.after.toolchain_digest
                        ),
                        "build_config_changed": (
                            observation.before.build_config_digest
                            != observation.after.build_config_digest
                        ),
                    }
                if changed_files:
                    response["files"] = changed_files
                return response
            if action.name in {
                "read_logs",
                "runtime_check",
                "probe",
                "verify_isolation",
            }:
                if _is_portable() and get_settings().use_max_finalization_coordinator and dirty:
                    return {
                        "ok": False,
                        "error": "runtime proof is reserved until the green full build",
                    }
                sync_result = await _sync_preview()
                if sync_result.failure is not None:
                    return {
                        "ok": False,
                        "error": sync_result.failure,
                        **(
                            {"files": sync_result.generated_files}
                            if sync_result.generated_files
                            else {}
                        ),
                    }
                if action.name == "read_logs":
                    runtime_result: dict[str, Any] = {
                        "ok": True,
                        "detail": runtime_log_tail.strip() or "(no logs yet)",
                    }
                elif action.name == "verify_isolation":
                    runtime_result = {
                        "ok": False,
                        "error": "Cell MAX isolation requires two signed tenant identities; "
                        "legacy email-auth isolation is not applicable.",
                    }
                else:
                    preview = await project_cell_create_preview_session(
                        workspace_id,
                        generation_run_id=leased_run_id,
                        fencing_epoch=fencing_epoch,
                    )
                    if action.name == "runtime_check":
                        from omnia_api.services.max_runtime_probe import probe_max_cell_runtime
                        from omnia_api.services.max_runtime_routes import (
                            resolve_max_runtime_probe_paths,
                        )

                        runtime_path = str(action.args.get("path") or "/")
                        fallback_paths: tuple[str, ...] = ()
                        proof_kwargs: dict[str, Any] = {}
                        if _is_portable():
                            runtime_path, fallback_paths = resolve_max_runtime_probe_paths(
                                workspace_files,
                                requested_path=runtime_path,
                            )
                            proof_kwargs = {
                                "portable_project_id": project_id,
                                "expected_epoch": fencing_epoch,
                            }
                            if fallback_paths:
                                proof_kwargs["fallback_paths"] = fallback_paths
                        proof = await probe_max_cell_runtime(
                            preview,
                            path=runtime_path,
                            **proof_kwargs,
                        )
                        runtime_result = {"ok": proof.ok, "detail": proof.detail}
                    else:
                        from omnia_api.services import agent_probe

                        runtime_result = await agent_probe.run_probe(
                            project_id,
                            method=str(action.args.get("method") or "GET"),
                            path=action.path or "/",
                            body=action.args.get("body"),
                            cell_preview=preview,
                        )
                if sync_result.generated_files:
                    merged = dict(runtime_result.get("files") or {})
                    merged.update(sync_result.generated_files)
                    runtime_result["files"] = merged
                return runtime_result
            if action.name in {"docs", "provider_docs", "generate_media"}:
                return await legacy_execute(action)
            return {"ok": False, "error": f"unknown cell action {action.name}"}
        except OrchestratorUnavailable as exc:
            return {
                "ok": False,
                "error": f"infra: {exc.message}",
                "infra_dead": True,
            }
        except OrchestratorBadRequest as exc:
            infra_dead = "container_not_running" in str(exc.details or "")
            return {
                "ok": False,
                "error": f"{'infra: ' if infra_dead else ''}{exc.message}",
                **({"infra_dead": True} if infra_dead else {}),
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _execute(action: Action) -> dict[str, Any]:
        nonlocal preview_synced
        result = await _execute_action(action)
        if result.get("environment_mutated") is True:
            preview_synced = False
        return result

    return ProjectCellExecutorHandle(
        execute=_execute,
        sync_preview=_sync_preview,
        snapshot_files=_snapshot_files,
        stage_patch=_stage_patch,
        stage_files=_stage_files,
        apply_external_files=_apply_external_files,
        export_files=_export_files,
        workspace_id=workspace_id,
        create_preview_session=_create_preview_session,
        release=_release,
        current_identity=_current_identity,
        run_role=_run_role,
        runtime_probe=_runtime_probe,
        operation_status=_operation_status,
        capabilities=capabilities,
        is_portable=_is_portable,
    )


async def _mark_workspace_ready(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    generation_run_id: UUID,
    provider_ref: str | None,
    fencing_epoch: int,
) -> None:
    async with session_factory() as session:
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise ProjectCellExecutorUnavailable("Project Cell workspace disappeared")
        run = await session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == generation_run_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            workspace.generation_run_id != generation_run_id
            or workspace.fencing_epoch != fencing_epoch
            or run is None
            or run.status not in {"pending", "queued_for_capacity", "running"}
        ):
            raise ProjectCellExecutorUnavailable("Project Cell lease changed before activation")
        workspace.state = "ready"
        workspace.provider_ref = provider_ref
        workspace.ready_at = datetime.now(UTC)
        workspace.last_error = None
        await session.commit()


def _apply_to_local_state(
    state: dict[str, str],
    *,
    writes: dict[str, str],
    deletes: tuple[str, ...] = (),
) -> None:
    for path, content in writes.items():
        state[path] = content
    for path in deletes:
        state.pop(path, None)


def _normalize_files(files: dict[str, str]) -> dict[str, str]:
    return {_normalize_path(path): content for path, content in files.items()}


def _normalize_delete_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_path(path) for path in paths}))


def _split_external_files(files: dict[str, str]) -> tuple[dict[str, str], tuple[str, ...]]:
    writes: dict[str, str] = {}
    deletes: list[str] = []
    for path, content in files.items():
        if content == "":
            deletes.append(path)
            continue
        writes[path] = content
    return writes, tuple(sorted(set(deletes)))


def _normalize_path(path: str) -> str:
    candidate = PurePosixPath(str(path).replace("\\", "/"))
    normalized = posixpath.normpath(candidate.as_posix()).lstrip("/")
    normalized_path = PurePosixPath(normalized)
    if (
        normalized in {"", "."}
        or candidate.is_absolute()
        or normalized_path.is_absolute()
        or ".." in normalized_path.parts
    ):
        raise ValueError(f"unsafe path: {path!r}")
    return normalized_path.as_posix()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n…[truncated {len(value) - limit} chars]"


def _list_dir(files: dict[str, str], path: str) -> str:
    prefix = _normalize_dir_prefix(path)
    entries: set[str] = set()
    for file_path in files:
        if prefix:
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
        else:
            remainder = file_path
        if not remainder:
            continue
        head, _sep, tail = remainder.partition("/")
        if not head:
            continue
        entries.add(f"{head}/" if tail else head)
    return "\n".join(sorted(entries)) or "(empty)"


def _grep(files: dict[str, str], *, pattern: str, path: str) -> str:
    prefix = _normalize_dir_prefix(path)
    if not pattern:
        return "(empty pattern)"
    try:
        matcher = re.compile(pattern)
    except re.error:
        matcher = re.compile(re.escape(pattern))
    lines: list[str] = []
    total_chars = 0
    for file_path, content in sorted(files.items()):
        if prefix and not file_path.startswith(prefix):
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            if matcher.search(line) is None:
                continue
            entry = f"{file_path}:{line_no}:{line}"
            lines.append(entry)
            total_chars += len(entry) + 1
            if len(lines) >= _MAX_GREP_MATCHES or total_chars >= _MAX_GREP_CHARS:
                return "\n".join(lines)
    return "\n".join(lines) if lines else "(no matches)"


def _normalize_dir_prefix(path: str) -> str:
    if path in {"", "."}:
        return ""
    normalized = _normalize_path(path)
    return normalized.rstrip("/") + "/"


def _diff_files(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    changed = {path: content for path, content in after.items() if before.get(path) != content}
    for path in before.keys() - after.keys():
        changed[path] = ""
    return changed


def _sync_failure_detail(hot: dict[str, Any]) -> str | None:
    failures = [
        (
            name,
            hot.get(name),
            hot.get(name.replace("_exit_code", "_stderr_tail"), ""),
        )
        for name in ("package_exit_code", "migration_exit_code")
        if hot.get(name) not in (None, "0", 0, "n/a")
    ]
    if not failures:
        return None
    detail = "; ".join(f"{name}={code}: {stderr}" for name, code, stderr in failures)
    return f"runtime apply failed during Project Cell sync: {detail}"
