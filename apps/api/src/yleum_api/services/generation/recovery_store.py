"""Private recovery checkpoints; no source writes, publication or execution hooks."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation import source_checkpoint
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryObservation,
    RecoveryState,
    RecoveryStateV2,
    RecoveryStateV3,
    decode_recovery,
)
from yleum_api.services.generation_runs import load_generation_dispatch, prompt_hash
from yleum_api.services.orchestrator_client import project_cell_agent_identity
from yleum_api.services.promotion_permit import workspace_revision_digest


async def _locked_owner(
    session: AsyncSession, *, run_id: UUID, project_id: UUID, user_id: UUID,
) -> tuple[GenerationRun, Project]:
    # Match the project -> run -> workspace ordering of existing lifecycle paths.
    project = await session.get(Project, project_id, with_for_update=True, populate_existing=True)
    run = await session.get(GenerationRun, run_id, with_for_update=True, populate_existing=True)
    if (
        project is None or project.owner_id != user_id or run is None
        or run.project_id != project_id or run.user_id != user_id or run.status != "running"
    ):
        raise RecoveryContractError("recovery run ownership is not active")
    return run, project


async def _validate_binding(
    session: AsyncSession, *, run: GenerationRun, binding: RecoveryBinding,
) -> None:
    try:
        workspace_id = UUID(binding.workspace_id)
    except ValueError as exc:
        raise RecoveryContractError("invalid recovery workspace identity") from exc
    if str(workspace_id) != binding.workspace_id:
        raise RecoveryContractError("noncanonical recovery workspace identity")
    workspace = await session.get(
        ProjectCellWorkspace, workspace_id, with_for_update=True, populate_existing=True,
    )
    if workspace is None:
        from yleum_api.services.generation.recovery_adaptation import validate_candidate_binding

        await validate_candidate_binding(session, run, binding)
    elif (
        workspace.project_id != run.project_id
        or workspace.owner_id != run.user_id or workspace.generation_run_id != run.id
        or workspace.fencing_epoch != binding.fencing_epoch
        or workspace.state != "ready" or workspace.deleted_at is not None
    ):
        raise RecoveryContractError("recovery workspace ownership or fence conflict")
    # DB ownership and digest syntax are insufficient: read current facts using
    # the server's fenced endpoint. An unavailable reader fails closed.
    current = await project_cell_agent_identity(
        workspace_id, generation_run_id=run.id, fencing_epoch=binding.fencing_epoch,
    )
    if (
        current.workspace_revision != binding.source_sha256
        or current.schema_data_digest != binding.schema_sha256
        or current.environment_digest != binding.environment_sha256
    ):
        raise RecoveryContractError("recovery candidate source/schema/environment conflict")


def _previous_state(run: GenerationRun) -> RecoveryState | None:
    root = run.agent_state
    if type(root) is not dict:
        raise RecoveryContractError("corrupt generation state")
    if "recovery_v1" not in root:
        return None
    state = decode_recovery(root["recovery_v1"])
    if state is None:
        raise RecoveryContractError("present recovery namespace is corrupt")
    return state


def _validate_previous_shadow(run: GenerationRun) -> None:
    if "recovery_shadow_v1" not in run.agent_state:
        return
    raw = run.agent_state["recovery_shadow_v1"]
    if (
        type(raw) is not dict or set(raw) != {"version", "observation"}
        or type(raw["version"]) is not int or raw["version"] != 1
    ):
        raise RecoveryContractError("unsupported or corrupt recovery shadow")
    try:
        RecoveryObservation.model_validate_json(json.dumps(raw["observation"], allow_nan=False))
    except (ValidationError, TypeError, ValueError) as exc:
        raise RecoveryContractError("corrupt recovery shadow observation") from exc


def _validate_revision(
    previous: RecoveryState | None, state: RecoveryState, *, expected_attempt: int,
    expected_revision: int,
) -> None:
    if (
        type(expected_attempt) is not int or type(expected_revision) is not int
        or expected_attempt < 0 or expected_revision < 0
        or state.attempt_seq != expected_attempt or state.state_revision != expected_revision
    ):
        raise RecoveryContractError("recovery attempt/revision conflict")
    if previous is None:
        if expected_attempt != 0 or expected_revision != 0 or state.checkpoint is not None:
            raise RecoveryContractError("recovery initialization requires initial attempt/revision")
    elif (
        previous.version != state.version
        or previous.attempt_seq != expected_attempt or previous.state_revision != expected_revision
        or previous.identity != state.identity or previous.requirements != state.requirements
        or previous.checkpoint != state.checkpoint
    ):
        raise RecoveryContractError("recovery revision or immutable scope conflict")
    if isinstance(previous, RecoveryStateV3) and (
        not isinstance(state, RecoveryStateV3) or state.clock != previous.clock
        or state.active_work_ms != previous.active_work_ms
        or state.stage_active_work_ms != previous.stage_active_work_ms
    ):
        raise RecoveryContractError("checkpoint cannot grant or refund work time")


def _require_writer_enabled(project_id: UUID) -> None:
    settings = get_settings()
    if settings.generation_recovery_mode == "on":
        return
    allowed = {item.strip() for item in settings.generation_recovery_canary_project_ids.split(",")}
    if settings.generation_recovery_mode == "canary" and str(project_id) in allowed:
        return
    raise RecoveryContractError("active recovery checkpoint writes are disabled")


async def _baseline_sha(
    session: AsyncSession, *, run: GenerationRun, project: Project, state: RecoveryState,
) -> str | None:
    identity = state.identity
    dispatch = load_generation_dispatch(run)
    from yleum_api.services.reference_images import request_identity

    if (
        identity.prompt_hash != run.prompt_hash
        or prompt_hash(request_identity(dispatch.prompt_text, dispatch.reference_images or [],
                                        dispatch.font_selections or []))
        != run.prompt_hash
        or dispatch.current_snapshot_id != identity.accepted_snapshot_id
        or project.current_snapshot_id != identity.accepted_snapshot_id
    ):
        raise RecoveryContractError("recovery original prompt/baseline conflict")
    baseline_sha: str | None = None
    # All facts consulted here stay locked until the writer commits its private
    # reference. Scope queries before locking to avoid taking foreign-project
    # locks from an invalid identity. The project lock already serializes this
    # project's current/predecessor run ordering with other recovery writers.
    if identity.accepted_snapshot_id is not None:
        snapshot = await session.scalar(
            select(Snapshot).where(
                Snapshot.id == identity.accepted_snapshot_id,
                Snapshot.project_id == project.id,
            ).execution_options(populate_existing=True).with_for_update()
        )
        if snapshot is None:
            raise RecoveryContractError("recovery baseline ownership mismatch")
        baseline_sha = snapshot.commit_sha
    if identity.supersedes_run_id is not None:
        parent = await session.scalar(
            select(GenerationRun).where(
                GenerationRun.id == identity.supersedes_run_id,
                GenerationRun.id != run.id,
                GenerationRun.project_id == project.id,
                GenerationRun.user_id == run.user_id,
            ).execution_options(populate_existing=True).with_for_update()
        )
        if parent is None or parent.status not in {"completed", "failed", "cancelled"}:
            raise RecoveryContractError("recovery superseded run ownership conflict")
    return baseline_sha


async def _validate_retained_checkpoint(
    run: GenerationRun, previous: RecoveryState | None, *, baseline_sha: str | None,
) -> None:
    if previous is None or previous.checkpoint is None:
        return
    ref = previous.checkpoint
    data = await source_checkpoint.load_checkpoint_object(
        run.project_id, run.id, object_key=ref.object_key, digest=ref.sha256,
    )
    if data is None or source_checkpoint.decode_checkpoint(
        data, project_id=run.project_id, user_id=run.user_id, run_id=run.id,
        baseline_sha=baseline_sha,
    ) is None:
        raise RecoveryContractError("retained checkpoint is unavailable or baseline changed")


async def save_recovery_checkpoint(
    session: AsyncSession, *, run_id: UUID, expected_attempt: int, expected_revision: int,
    state: RecoveryState, files: Mapping[str, str], deleted_paths: Sequence[str],
) -> CheckpointRef:
    """Commit a private immutable reference under row locks and revision CAS.

    ``files`` is the full candidate tree, not an incremental patch or the filtered
    private object. Candidate evolution is allowed only with matching current
    fenced facts; original run identity, baseline and requirements never change.
    The caller supplies a clean dedicated session; this function owns its commit.
    """
    # model_copy/model_construct bypass Pydantic validation; revalidate the wire
    # shape at this persistence boundary as well as on ordinary reads.
    validated = decode_recovery(state.model_dump(mode="json"))
    if validated is None:
        raise RecoveryContractError("missing recovery contract")
    state = validated
    if isinstance(state, RecoveryStateV2):
        from yleum_api.services.generation.recovery_execution import _require_session_owner
        from yleum_api.services.generation_deployment_drain import require_generation_admission

        await _require_session_owner(run_id)
        await require_generation_admission(session)
    else:
        _require_writer_enabled(state.identity.project_id)
    try:
        with session.no_autoflush:
            identity = state.identity
            if identity.run_id != run_id:
                raise RecoveryContractError("recovery run identity mismatch")
            run, project = await _locked_owner(
                session, run_id=run_id, project_id=identity.project_id, user_id=identity.user_id,
            )
            previous = _previous_state(run)
            if isinstance(state, RecoveryStateV2):
                from yleum_api.services.generation.recovery_execution import execution_enabled

                if previous is None:
                    if not execution_enabled(run.project_id):
                        raise RecoveryContractError("new executable recovery writes are disabled")
                elif (
                    not isinstance(previous, RecoveryStateV2)
                    or previous.continuation_claim is None
                    or previous.continuation_claim != state.continuation_claim
                    or previous.phase not in {"working", "repairing", "verifying"}
                ):
                    raise RecoveryContractError("checkpoint continuation claim conflicts")
            _validate_revision(previous, state, expected_attempt=expected_attempt,
                               expected_revision=expected_revision)
            baseline_sha = await _baseline_sha(session, run=run, project=project, state=state)
            await _validate_retained_checkpoint(run, previous, baseline_sha=baseline_sha)
            binding, context = state.binding, state.context_ref
            if binding is None or context is None:
                raise RecoveryContractError("checkpoint requires candidate and durable context")
            # Validate source types/paths/budgets before hashing or external calls.
            candidate = dict(files)
            selected = source_checkpoint.checkpoint_sources(candidate)
            data = source_checkpoint.encode_checkpoint(
                project_id=project.id, user_id=run.user_id, run_id=run.id,
                baseline_sha=baseline_sha, files=selected, deleted_paths=list(deleted_paths),
            )
            if workspace_revision_digest(candidate) != binding.source_sha256:
                raise RecoveryContractError("recovery supplied source does not match candidate")
            context_data = await source_checkpoint.load_checkpoint_object(
                project.id, run.id, object_key=context.object_key, digest=context.sha256,
            )
            if context_data is None:
                raise RecoveryContractError("recovery context is not durably retained")
            await _validate_binding(session, run=run, binding=binding)
            object_key, digest = await source_checkpoint.store_checkpoint_object(
                project.id, run.id, data,
            )
            # A physical candidate may change even while database rows are locked.
            # Do not install a reference if it drifted during private upload.
            await _validate_binding(session, run=run, binding=binding)
            ref = CheckpointRef(object_key=object_key, sha256=digest)
            next_raw = state.model_dump(mode="json") | {
                "checkpoint": ref.model_dump(mode="json"),
                "state_revision": expected_revision + 1,
            }
            persisted = decode_recovery(next_raw)
            assert persisted is not None
            run.agent_state = {**run.agent_state, "recovery_v1": persisted.model_dump(mode="json")}
        await session.commit()
        return ref
    except BaseException:
        # Uploaded objects can be orphaned, never removed here: a commit outcome
        # may be uncertain, and the only retained source must survive failures.
        await session.rollback()
        raise


async def save_recovery_shadow(
    session: AsyncSession, *, run_id: UUID, observation: RecoveryObservation,
) -> bool:
    """Bounded latest observation only; never creates/updates an active contract.

    No automatic caller is installed in Task 2. This seam makes shadow storage
    available without changing dispatch, editing, finalization or paid calls.
    """
    if get_settings().generation_recovery_mode != "shadow":
        return False
    # Do not treat an observation as proof authority, even if it has a witness.
    observation = RecoveryObservation.model_validate_json(observation.model_dump_json())
    try:
        with session.no_autoflush:
            target = await session.get(GenerationRun, run_id)
            if target is None:
                raise RecoveryContractError("shadow run is missing")
            run, _project = await _locked_owner(
                session, run_id=run_id, project_id=target.project_id, user_id=target.user_id,
            )
            _previous_state(run)  # Unknown/corrupt active JSON blocks all recovery writes.
            _validate_previous_shadow(run)
            await _validate_binding(session, run=run, binding=observation.binding)
            run.agent_state = {**run.agent_state, "recovery_shadow_v1": {
                "version": 1, "observation": observation.model_dump(mode="json"),
            }}
        await session.commit()
        return True
    except BaseException:
        await session.rollback()
        raise
