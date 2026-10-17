"""Durable linkage between the SQL control lease and its physical adaptation candidate.

The controller owns physical receipts. This module retains authenticated replies,
never reconstructs them from a checkpoint or repeats an uncertain preparation.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect as Effect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.services import orchestrator_client as client
from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryContractError,
    RecoveryStateV2,
)
from yleum_api.services.generation.recovery_execution import _owned_release_receipt, recovery_state
from yleum_api.services.generation.recovery_reconciliation import (
    _lock_effect_namespace,
    _next_effect_ordinal,
)
from yleum_api.services.orchestrator_hosts import bind_candidate_to_source_host

Factory = async_sessionmaker[AsyncSession]
Workspace = client.RestorationAdaptationWorkspace
Receipt = client.RestorationAdaptationRecoveryReceipt


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    source_workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: UUID
    base_draft_snapshot_id: UUID
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    adaptation_bundle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _wire(value: object) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _id(run_id: UUID, action: str, cycle: int = 0) -> UUID:
    return uuid5(run_id, f"restoration-adaptation/v1/{action}/{cycle}")


def _state(run: GenerationRun) -> RecoveryStateV2:
    value = recovery_state(run)
    if not isinstance(value, RecoveryStateV2):
        raise RecoveryContractError("adaptation requires executable recovery")
    return value


def _original_scope(run: GenerationRun, request: Prepare) -> None:
    saved = (run.agent_state or {}).get("restoration_adaptation")
    expected = {
        "operation_id": str(request.operation_id), "adaptation_run_id": str(run.id),
        "source_snapshot_id": str(request.source_snapshot_id),
        "base_draft_snapshot_id": str(request.base_draft_snapshot_id),
        "source_commit_sha": request.source_commit_sha,
        "sha256": request.adaptation_bundle_digest,
    }
    if (not isinstance(saved, dict) or any(saved.get(k) != v for k, v in expected.items())
        or request.project_id != run.project_id or request.owner_id != run.user_id
        or request.generation_run_id != run.id):
        raise RecoveryContractError("adaptation original ownership conflicts")


async def _owner(
    session: AsyncSession, request: Prepare, fence: int, *, terminal: bool = False,
    released: bool = False, idle: bool = False, stopping: bool = False,
) -> tuple[GenerationRun, RecoveryStateV2, ProjectCellWorkspace]:
    project = await session.get(Project, request.project_id, with_for_update=True,
                                populate_existing=True)
    run = await session.get(GenerationRun, request.generation_run_id, with_for_update=True,
                            populate_existing=True)
    statuses = {"completed", "failed", "cancelled"} if terminal else {"running"}
    if stopping:
        statuses = {"cancel_requested", "cancelled", "failed"}
    if (project is None or project.owner_id != request.owner_id or run is None
        or run.status not in statuses):
        raise RecoveryContractError("adaptation current run ownership is unavailable")
    _original_scope(run, request)
    state = _state(run)
    workspace = await session.get(ProjectCellWorkspace, request.workspace_id,
                                  with_for_update=True, populate_existing=True)
    if (workspace is None or workspace.project_id != run.project_id
        or workspace.owner_id != run.user_id or workspace.deleted_at is not None
        or workspace.state != "ready" or workspace.fencing_epoch != fence
        or workspace.generation_run_id != (None if released else run.id)):
        raise RecoveryContractError("adaptation SQL control lease conflicts")
    if idle and await session.scalar(select(ProjectCellActivityLease.operation_id).where(
        ProjectCellActivityLease.workspace_id == workspace.id,
        ProjectCellActivityLease.state == "active",
    ).limit(1)) is not None:
        raise RecoveryContractError("adaptation still has an active physical operation")
    return run, state, workspace


def _check_effect(row: Effect, run: GenerationRun, action: str) -> dict[str, Any]:
    pre = row.pre_binding
    if (row.payload_version != 1 or row.kind != "bootstrap" or row.run_id != run.id
        or row.project_id != run.project_id or row.user_id != run.user_id
        or type(pre) is not dict or set(pre) != {"protocol", "action", "request"}
        or pre["protocol"] != "adaptation/v1" or pre["action"] != action
        or type(pre["request"]) is not dict or _digest(pre) != row.input_digest):
        raise RecoveryContractError("adaptation durable effect conflicts")
    return pre["request"]


async def _reserve(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2, action: str,
    cycle: int, command: dict[str, Any],
) -> tuple[Effect, bool]:
    await _lock_effect_namespace(session, run.id, 0)
    effect_id = _id(run.id, action, cycle)
    row = await session.get(Effect, effect_id, with_for_update=True, populate_existing=True)
    if row is not None:
        if _check_effect(row, run, action) != command:
            raise RecoveryContractError("adaptation transition envelope changed")
        return row, False
    claim = state.continuation_claim
    if claim is None or claim.run_id != run.id or claim.attempt_seq != state.attempt_seq:
        raise RecoveryContractError("adaptation continuation claim is unavailable")
    row = Effect(
        effect_id=effect_id, run_id=run.id, project_id=run.project_id, user_id=run.user_id,
        logical_step_seq=0, effect_ordinal=await _next_effect_ordinal(session, run.id, 0),
        kind="bootstrap", state="inflight", attempt_seq=state.attempt_seq,
        claim_token=claim.token, operation_id=effect_id,
        pre_binding={"protocol": "adaptation/v1", "action": action, "request": command},
        input_digest="0" * 64,
    )
    row.input_digest = _digest(row.pre_binding)
    session.add(row)
    await session.flush()
    return row, True


def _confirmed(row: Effect) -> dict[str, Any]:
    value = row.receipt_ref
    if (row.state != "confirmed" or type(value) is not dict
        or row.receipt_sha256 != _digest(value)):
        raise RecoveryContractError("adaptation acknowledgement is unknown")
    return value


def _ack(request: Prepare, raw: dict[str, Any]) -> Workspace:
    workspace = Workspace.from_json(raw)
    if (workspace.source_workspace_id != request.workspace_id
        or workspace.candidate_workspace_id == request.workspace_id
        or workspace.operation_id != request.operation_id
        or workspace.project_id != request.project_id or workspace.owner_id != request.owner_id
        or workspace.generation_run_id != request.generation_run_id
        or workspace.request_digest is None):
        raise RecoveryContractError("adaptation preparation acknowledgement conflicts")
    return workspace


async def _prepared(session: AsyncSession, run: GenerationRun) -> tuple[Prepare, Workspace]:
    row = await session.get(Effect, _id(run.id, "prepare"), populate_existing=True)
    if row is None:
        raise RecoveryContractError("adaptation preparation was not durably acknowledged")
    request = Prepare.model_validate_json(json.dumps(_check_effect(row, run, "prepare")))
    _original_scope(run, request)
    return request, _ack(request, _confirmed(row))


def _identity(workspace: Workspace, action: str, cycle: int) -> dict[str, Any]:
    return client.RestorationAdaptationRecoveryIdentity(
        transition_id=_id(workspace.generation_run_id, action, cycle),
        workspace_id=workspace.source_workspace_id,
        generation_run_id=workspace.generation_run_id, operation_id=workspace.operation_id,
        project_id=workspace.project_id, owner_id=workspace.owner_id,
        candidate_workspace_id=workspace.candidate_workspace_id,
        request_digest=workspace.request_digest,  # type: ignore[arg-type]
    ).model_dump(mode="json")


def _receipt(
    raw: dict[str, Any], request: Prepare, workspace: Workspace, action: str, cycle: int,
    command: dict[str, Any],
) -> Receipt:
    receipt = Receipt.model_validate_json(json.dumps(raw))
    identity = _identity(workspace, action, cycle)
    if (any(raw.get(k) != v for k, v in identity.items())
        or receipt.original_control_fencing_epoch != request.fencing_epoch
        or receipt.original_candidate_fencing_epoch != workspace.candidate_fencing_epoch
        or receipt.cycle != cycle
        or receipt.current_control_fencing_epoch != command.get(
            "control_fencing_epoch", command.get("new_control_fencing_epoch"))
        or (action == "hold" and receipt.current_candidate_fencing_epoch
            != command["candidate_fencing_epoch"])
        or receipt.state not in ({"held", "hold_pending"} if action == "hold"
                                  else {"resumable", "rebinding"})):
        raise RecoveryContractError("adaptation physical receipt binding conflicts")
    if receipt.state in {"held", "resumable"} and receipt.terminal_receipt_digest != _digest(
        receipt.model_dump(mode="json", exclude={"terminal_receipt_digest"})
    ):
        raise RecoveryContractError("adaptation terminal receipt digest conflicts")
    return receipt


async def _transition(
    factory: Factory, request: Prepare, original: Workspace, *, action: Literal["hold", "rebind"],
    cycle: int, command: dict[str, Any], fence: int, stopping: bool = False,
) -> Receipt:
    async with factory() as session:
        run, state, _ = await _owner(session, request, fence, idle=True, stopping=stopping)
        _, fresh = await _reserve(session, run, state, action, cycle, command)
        token, attempt = state.continuation_claim, state.attempt_seq
        await session.commit()
    # A persisted intent is never a retry permission. Even an ACK lost before
    # SQL persistence is resolved through the controller's read-only status.
    result = await client.project_cell_restoration_recovery(
        action if fresh else "status", command if fresh else _identity(original, action, cycle),
    )
    result = _receipt(result.model_dump(mode="json"), request, original, action, cycle, command)
    async with factory() as session:
        run, current, _ = await _owner(session, request, fence, idle=True, stopping=stopping)
        if current.continuation_claim != token or current.attempt_seq != attempt:
            raise RecoveryContractError("adaptation continuation changed during physical work")
        persisted = await session.get(Effect, _id(run.id, action, cycle), with_for_update=True)
        if persisted is None or _check_effect(persisted, run, action) != command:
            raise RecoveryContractError("adaptation transition disappeared")
        persisted.receipt_ref = result.model_dump(mode="json")
        persisted.receipt_sha256 = _digest(persisted.receipt_ref)
        persisted.state = "confirmed" if result.state in {"held", "resumable"} else "unknown"
        persisted.revision += 1
        await session.commit()
    return result


async def _cycle_workspace(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2,
    request: Prepare, original: Workspace, fence: int,
) -> Workspace:
    cycle = state.execution.lease_cycle_seq
    if cycle == 0:
        if fence != request.fencing_epoch:
            raise RecoveryContractError("adaptation original control fence changed")
        return original
    row = await session.get(Effect, _id(run.id, "rebind", cycle), populate_existing=True)
    if row is None:
        raise RecoveryContractError("adaptation current cycle has no acknowledgement")
    command = _check_effect(row, run, "rebind")
    receipt = _receipt(_confirmed(row), request, original, "rebind", cycle, command)
    if receipt.state != "resumable" or receipt.current_control_fencing_epoch != fence:
        raise RecoveryContractError("adaptation current cycle is not resumable")
    return replace(original, candidate_fencing_epoch=receipt.current_candidate_fencing_epoch)


async def prepare_or_resume(
    factory: Factory, request: Prepare, prepare: Callable[..., Awaitable[Workspace]],
) -> Workspace:
    async with factory() as session:
        run, state, _ = await _owner(session, request, request.fencing_epoch)
        existing = await session.get(Effect, _id(run.id, "prepare"))
        cycle = state.execution.lease_cycle_seq
        if existing is None:
            if cycle != 0:
                raise RecoveryContractError("cold adaptation preparation is missing")
            _, fresh = await _reserve(session, run, state, "prepare", 0,
                                         request.model_dump(mode="json"))
            token, attempt = state.continuation_claim, state.attempt_seq
            await session.commit()
        else:
            original_request, original = await _prepared(session, run)
            if request.model_dump(exclude={"fencing_epoch"}) != original_request.model_dump(
                exclude={"fencing_epoch"},
            ):
                raise RecoveryContractError("adaptation original preparation changed")
            if cycle == 0:
                result = await _cycle_workspace(session, run, state, original_request,
                                                original, request.fencing_epoch)
                await bind_candidate_to_source_host(result.candidate_workspace_id,
                                                    request.workspace_id)
                return result
            hold_row = await session.get(Effect, _id(run.id, "hold", cycle - 1))
            if hold_row is None:
                raise RecoveryContractError("adaptation previous hold is missing")
            hold_command = _check_effect(hold_row, run, "hold")
            rebound = await session.get(Effect, _id(run.id, "rebind", cycle))
            if rebound is None:
                # Before the first rebind, this hold must still be the current
                # physical transition. Its historical SQL copy is insufficient.
                await session.rollback()
                held = await client.project_cell_restoration_recovery(
                    "status", _identity(original, "hold", cycle - 1),
                )
                held = _receipt(held.model_dump(mode="json"), original_request, original,
                                "hold", cycle - 1, hold_command)
            else:
                # Once rebind was dispatched, the controller rejects historical
                # hold reads. The stored hold only reconstructs the immutable
                # request; _transition reads the CURRENT rebind status below.
                held = _receipt(_confirmed(hold_row), original_request, original,
                                "hold", cycle - 1, hold_command)
                await session.rollback()
            if held.state != "held":
                raise RecoveryContractError("adaptation hold remains uncertain")
            command = _identity(original, "rebind", cycle) | {
                "expected_cycle": cycle - 1,
                "expected_hold_receipt_digest": held.terminal_receipt_digest,
                "new_control_fencing_epoch": request.fencing_epoch,
            }
            receipt = await _transition(factory, original_request, original, action="rebind",
                                         cycle=cycle, command=command, fence=request.fencing_epoch)
            if receipt.state != "resumable":
                raise RecoveryContractError("adaptation restoration remains uncertain")
            result = replace(
                original, candidate_fencing_epoch=receipt.current_candidate_fencing_epoch,
            )
            await bind_candidate_to_source_host(result.candidate_workspace_id, request.workspace_id)
            return result
    if not fresh:
        raise RecoveryContractError("adaptation original preparation remains uncertain")
    kwargs = request.model_dump(exclude={"workspace_id"})
    result = await prepare(request.workspace_id, **kwargs)
    raw = _wire(asdict(result)) | {"state": "ready"}
    result = _ack(request, raw)
    async with factory() as session:
        run, state, _ = await _owner(session, request, request.fencing_epoch)
        if state.continuation_claim != token or state.attempt_seq != attempt:
            raise RecoveryContractError("adaptation ownership changed after preparation")
        persisted = await session.get(Effect, _id(run.id, "prepare"), with_for_update=True)
        if (persisted is None or _check_effect(persisted, run, "prepare")
            != request.model_dump(mode="json")):
            raise RecoveryContractError("adaptation preparation intent changed")
        persisted.receipt_ref, persisted.receipt_sha256 = raw, _digest(raw)
        persisted.state = "confirmed"
        persisted.revision += 1
        await session.commit()
    return result


async def validate_candidate_binding(
    session: AsyncSession, run: GenerationRun, binding: RecoveryBinding,
) -> None:
    state = _state(run)
    request, original = await _prepared(session, run)
    control = await session.get(ProjectCellWorkspace, request.workspace_id,
                                with_for_update=True, populate_existing=True)
    if (control is None or control.generation_run_id != run.id
        or control.project_id != run.project_id or control.owner_id != run.user_id
        or control.state != "ready" or control.deleted_at is not None):
        raise RecoveryContractError("adaptation current SQL control is unavailable")
    current = await _cycle_workspace(session, run, state, request, original, control.fencing_epoch)
    if (str(current.candidate_workspace_id) != binding.workspace_id
        or current.candidate_fencing_epoch != binding.fencing_epoch):
        raise RecoveryContractError("adaptation physical candidate binding conflicts")


async def _released_owner(
    session: AsyncSession, request: Prepare, cycle: int,
) -> tuple[GenerationRun, RecoveryStateV2, int]:
    latest = await session.get(GenerationRun, request.generation_run_id)
    if latest is None:
        raise RecoveryContractError("adaptation run disappeared")
    hint = _state(latest)
    fence = hint.execution.control_fencing_epoch
    if fence is None:
        raise RecoveryContractError("terminal adaptation control fence is unavailable")
    run, state, _ = await _owner(session, request, fence, terminal=True, released=True, idle=True)
    execution = state.execution
    if (execution.physical_state != "released" or execution.control_fencing_epoch != fence
        or execution.control_workspace_id != request.workspace_id
        or execution.lease_cycle_seq != cycle or execution.release_operation_id is None):
        raise RecoveryContractError("terminal adaptation SQL lease is not released")
    operation = await session.get(ProjectCellOperation, execution.release_operation_id)
    if not _owned_release_receipt(
        operation, run_id=run.id, workspace_id=request.workspace_id, fence=fence,
    ):
        raise RecoveryContractError("terminal adaptation control release is unproven")
    return run, state, fence


async def release_candidate(
    factory: Factory, workspace: Workspace, control_fence: int,
    release: Callable[[int, bool], Awaitable[None]], cleanup: Callable[..., Awaitable[None]],
) -> None:
    """Retain on every uncertain outcome; terminal cleanup is a separate durable effect."""
    async with factory() as session:
        run = await session.get(GenerationRun, workspace.generation_run_id)
        if run is None:
            raise RecoveryContractError("adaptation run disappeared")
        request, original = await _prepared(session, run)
        state = _state(run)
        cycle = state.execution.lease_cycle_seq
        terminal = run.status in {"completed", "failed", "cancelled"}
        stopping = run.status in {"cancel_requested", "cancelled", "failed"}
        if not terminal and not stopping and (run.status != "running" or state.phase not in {
            "waiting_dependency", "reconciling", "needs_input",
        }):
            raise RecoveryContractError("adaptation release is not a confirmed yield")
        current = await _cycle_workspace(session, run, state, request, original, control_fence)
        if workspace != current:
            raise RecoveryContractError("adaptation handle belongs to a different lease cycle")
        hold_command = _identity(original, "hold", cycle) | {
            "expected_cycle": cycle, "control_fencing_epoch": control_fence,
            "candidate_fencing_epoch": workspace.candidate_fencing_epoch,
        }
        control = await session.get(ProjectCellWorkspace, request.workspace_id)
        already_released = control is not None and control.generation_run_id is None
        if terminal and already_released:
            run, _, _ = await _released_owner(session, request, cycle)
            cleaned = await session.get(Effect, _id(run.id, "cleanup", cycle))
            if cleaned is not None:
                command = _check_effect(cleaned, run, "cleanup")
                if (set(command) != {"workspace", "recovery_cycle", "recovery_receipt_digest"}
                    or command["workspace"] != _wire(asdict(workspace))
                    or command["recovery_cycle"] != cycle
                    or _confirmed(cleaned) != {"cleaned": True}):
                    raise RecoveryContractError("adaptation cleanup acknowledgement conflicts")
                # No physical status exists after confirmed destruction. This
                # idempotent no-op cannot authorize another delete or a writer.
                return
        await session.rollback()
        if not already_released:
            receipt = await _transition(
                factory, request, original, action="hold", cycle=cycle,
                command=hold_command, fence=control_fence, stopping=stopping,
            )
            if receipt.state != "held":
                raise RecoveryContractError("adaptation hold remains uncertain")
            await release(control_fence, stopping)
        if not terminal:
            return
    # Cleanup consumes a current terminal run and an acknowledged stop/hold.
    # An uncertain deletion is never redispatched.
    held = await client.project_cell_restoration_recovery(
        "status", _identity(original, "hold", cycle),
    )
    held = _receipt(held.model_dump(mode="json"), request, original, "hold", cycle, hold_command)
    if held.state != "held":
        raise RecoveryContractError("terminal adaptation has no confirmed physical hold")
    async with factory() as session:
        run, state, released_fence = await _released_owner(session, request, cycle)
        command = {"workspace": _wire(asdict(workspace)), "recovery_cycle": cycle,
                   "recovery_receipt_digest": held.terminal_receipt_digest}
        row, fresh = await _reserve(session, run, state, "cleanup", cycle, command)
        if not fresh:
            _confirmed(row)
            return
        await session.commit()
    await cleanup(workspace, recovery_cycle=cycle,
                  recovery_receipt_digest=held.terminal_receipt_digest)
    async with factory() as session:
        run, state, final_fence = await _released_owner(session, request, cycle)
        if final_fence != released_fence:
            raise RecoveryContractError("adaptation cleanup cycle changed")
        persisted = await session.get(Effect, _id(run.id, "cleanup", cycle), with_for_update=True)
        if persisted is None or _check_effect(persisted, run, "cleanup") != command:
            raise RecoveryContractError("adaptation cleanup intent changed")
        persisted.receipt_ref = {"cleaned": True}
        persisted.receipt_sha256 = _digest(persisted.receipt_ref)
        persisted.state = "confirmed"
        persisted.revision += 1
        await session.commit()


async def cleanup_terminal_adaptation(factory: Factory, run_id: UUID) -> None:
    """Post-terminal reconciliation; never resumes source/model/tool execution.

    The caller commits cancellation first. Unknown physical outcomes keep their
    exact records and are reported to the caller; they never grant a retry.
    """
    from yleum_api.services import project_cell_executor as executor
    from yleum_api.services.restorations import adaptation_activation_holds_generation_lease

    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        if (run is None or not isinstance(recovery_state(run), RecoveryStateV2)
            or "restoration_adaptation" not in (run.agent_state or {})):
            return
        if run.status not in {"cancelled", "failed"}:
            raise RecoveryContractError("adaptation cleanup requires committed termination")
        state = _state(run)
        request, original = await _prepared(session, run)
        cycle = state.execution.lease_cycle_seq
        fence = request.fencing_epoch
        if cycle:
            row = await session.get(Effect, _id(run.id, "rebind", cycle))
            if row is None:
                raise RecoveryContractError("adaptation terminal cycle has no acknowledgement")
            command = _check_effect(row, run, "rebind")
            receipt = _receipt(_confirmed(row), request, original, "rebind", cycle, command)
            fence = receipt.current_control_fencing_epoch
        current = await _cycle_workspace(session, run, state, request, original, fence)
        control = await session.get(ProjectCellWorkspace, request.workspace_id)
        profile = (control.provider_metadata or {}).get("profile_version") if control else None
        if type(profile) is not str or profile not in {
            "docker-owner-cell-resources-v1", "docker-owner-cell-resources-v2",
        }:
            raise RecoveryContractError("adaptation terminal control profile is unavailable")
        profile_version = str(profile)
    if await adaptation_activation_holds_generation_lease(factory, run_id):
        return  # The already-issued activation remains owned by its reconciler.

    async def release_control(expected_fence: int, stopping: bool) -> None:
        await executor._release_generation_lease(
            session_factory=factory, workspace_id=request.workspace_id,
            generation_run_id=run_id, profile_version=profile_version,
            expected_fencing_epoch=expected_fence, adaptation_stopping=stopping,
        )

    await release_candidate(
        factory, current, fence, release_control,
        executor.project_cell_cleanup_restoration_adaptation,
    )
