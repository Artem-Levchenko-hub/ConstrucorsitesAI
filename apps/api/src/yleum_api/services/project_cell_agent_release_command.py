"""Verify an ordinary build's lost command ACK before replaying its release."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from urllib3.exceptions import HTTPError as ObjectTransportError

from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import ProjectCellActivityLease
from yleum_api.services import orchestrator_client
from yleum_api.services.generation import recovery_execution
from yleum_api.services.generation import recovery_tool_receipts as receipts
from yleum_api.services.generation.recovery_clock import next_dependency_check
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    RecoveryContractError,
    RecoveryState,
    RecoveryStateV2,
    RecoveryStateV3,
)
from yleum_api.services.generation.recovery_quiescence import _provider_stopped
from yleum_api.services.generation.recovery_reconciliation import (
    ToolEffectCursor,
    provider_root_effect_id,
)
from yleum_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellCommandReceipt,
    ProjectCellSourceWriteReceipt,
)
from yleum_api.services.project_cell_activity import ActivityState, finish_activity
from yleum_api.services.project_cell_proofs import MAX_BOOTSTRAP_CONTRACT_VERSION, ProofIdentity
from yleum_api.services.project_cells import _stored_request_payload
from yleum_api.services.promotion_permit import workspace_revision_digest

AGENT_DEADLINE_BUILD_TOOL_KEY = "legacy-deadline-build"
_ROLE_SPECS = (
    ("bootstrap", "prepare", MAX_BOOTSTRAP_CONTRACT_VERSION),
    ("fast_check", "fast_check", "v1"),
)


@dataclass(frozen=True)
class _CompilerSlot:
    operation_id: UUID
    effect_id: UUID
    effect_revision: int
    input_digest: str
    command: receipts.CommandBinding
    prefix: ProjectCellCommandReceipt
    profile: str
    persisted_receipt: ProjectCellCommandReceipt | None
    activity_finished: bool


def agent_build_command_cursor(state: RecoveryState | None) -> bool:
    if not isinstance(state, RecoveryStateV3):
        return False
    cursor = state.execution
    return (
        state.clock.stage == "edit"
        and state.phase in {"working", "waiting_dependency", "reconciling"}
        and state.continuation_claim is not None and not state.unresolved_operation_ids
        and state.binding is not None and state.context_ref is not None
        and state.checkpoint is not None and cursor.execution_context_ref == state.context_ref
        and cursor.stage == "agent" and cursor.substep == "tool_wait"
        and cursor.pending_effect_id == ToolEffectCursor(
            state.continuation_claim, state.state_revision, cursor.logical_step_seq,
            AGENT_DEADLINE_BUILD_TOOL_KEY, reconcile_only=True,
        ).root_effect_id
        and cursor.provider_effect_id == provider_root_effect_id(
            state.identity.run_id, cursor.logical_step_seq,
        )
    )


async def _owns_build_context(state: RecoveryStateV2) -> bool:
    assert state.context_ref is not None and state.continuation_claim is not None
    context = await recovery_execution.load_execution_context(
        state.identity, state.context_ref, binding=state.binding,
    )
    frame = context.get("active_frame")
    return (
        type(frame) is dict and set(frame) == {"version", "kind", "value"}
        and type(frame.get("version")) is int and frame["version"] == 1
        and frame["kind"] == "legacy" and type(frame["value"]) is dict
        and context.get("active_tool") == {
            "version": 1, "index": state.execution.next_tool_index,
            "key": AGENT_DEADLINE_BUILD_TOOL_KEY,
            "input_digest": receipts.digest({"name": "build", "args": {}}),
        }
    )


def _matches_command_receipt(
    receipt: ProjectCellCommandReceipt, slot: receipts.CommandBinding, state: RecoveryStateV2,
    operation_id: UUID,
) -> bool:
    owner, binding = state.identity, state.binding
    assert binding is not None
    return (
        (receipt.operation_id, receipt.workspace_id, receipt.project_id, receipt.owner_id,
         receipt.generation_run_id, receipt.dispatch_fencing_epoch)
        == (operation_id, slot.workspace_id, owner.project_id, owner.user_id,
            owner.run_id, binding.fencing_epoch)
        and receipt.request_sha256 == slot.request_sha256
        and receipt.before_revision == receipt.after_revision == binding.source_sha256
    )


def _stored_receipt(
    effect: GenerationRecoveryEffect, command: receipts.CommandBinding, state: RecoveryStateV2,
) -> ProjectCellCommandReceipt | None:
    assert effect.operation_id is not None
    if (
        effect.state != "confirmed" or type(effect.receipt_ref) is not dict
        or effect.receipt_sha256 != receipts.digest(effect.receipt_ref)
    ):
        return None
    receipt = ProjectCellCommandReceipt.from_json(effect.receipt_ref)
    if (
        not _matches_command_receipt(receipt, command, state, effect.operation_id)
        or effect.post_binding != {
            "workspace_revision": receipt.after_revision,
            "physical_volume_identity": receipt.physical_volume_identity,
        }
    ):
        return None
    return receipt


def _owns_effect(row: GenerationRecoveryEffect, state: RecoveryStateV2, key: str) -> bool:
    owner, claim = state.identity, state.continuation_claim
    assert claim is not None
    return (
        (row.project_id, row.user_id) == (owner.project_id, owner.user_id)
        and row.payload_version == 1 and row.tool_call_id == key
        and row.provider_attempt_id is None and row.parent_effect_id is None
        and (row.attempt_seq < state.attempt_seq
             or (row.attempt_seq == state.attempt_seq and row.claim_token == claim.token))
    )


def _settled_prior_legacy_action(
    rows: list[GenerationRecoveryEffect], state: RecoveryStateV2,
) -> bool:
    index = state.execution.next_tool_index
    if not rows:
        return index == 0
    if index != 1 or any(
        row.state != "confirmed" or not _owns_effect(row, state, "legacy-action") for row in rows
    ):
        return False
    owner, binding = state.identity, state.binding
    assert binding is not None
    root = uuid5(owner.run_id, f"recovery-tool-v1:{state.execution.logical_step_seq}:legacy-action")
    result = rows[-1]
    if (
        result.effect_id != uuid5(root, "result-v1") or result.operation_id is not None
        or result.checkpoint_ref is None or result.receipt_ref is not None
    ):
        return False
    CheckpointRef.model_validate(result.checkpoint_ref)
    result_binding = receipts.ToolResultBinding.model_validate_json(json.dumps(result.pre_binding))
    result_context = result_binding.context_binding
    if (
        result_binding.index != 0 or result_context is None
        or result_context.workspace_id != binding.workspace_id
        or result_context.fencing_epoch > binding.fencing_epoch
    ):
        return False
    for ordinal, row in enumerate(rows[:-1]):
        effect_id = uuid5(root, f"physical:{ordinal}")
        if (
            row.effect_id != effect_id or row.operation_id is None or row.checkpoint_ref is not None
            or type(row.pre_binding) is not dict or type(row.receipt_ref) is not dict
            or row.receipt_sha256 != receipts.digest(row.receipt_ref)
        ):
            return False
        expected_operation = uuid5(effect_id, "controller-operation-v1")
        stored: ProjectCellSourceWriteReceipt | ProjectCellCommandReceipt
        bound: receipts.PhysicalIntentBinding
        if row.pre_binding.get("kind") == "command_v1":
            command = receipts.CommandBinding.model_validate_json(json.dumps(row.pre_binding))
            bound = command
            if command.activity is not None:
                expected_operation = command.activity.operation_id
            stored = ProjectCellCommandReceipt.from_json(row.receipt_ref)
            fence = stored.dispatch_fencing_epoch
        else:
            bound = receipts.SourceWriteBinding.model_validate_json(json.dumps(row.pre_binding))
            stored = ProjectCellSourceWriteReceipt.from_json(row.receipt_ref)
            fence = stored.fencing_epoch
        if (
            (stored.operation_id, row.operation_id) != (expected_operation, expected_operation)
            or (stored.project_id, stored.owner_id, stored.generation_run_id)
            != (owner.project_id, owner.user_id, owner.run_id)
            or stored.workspace_id != bound.workspace_id
            or str(bound.workspace_id) != binding.workspace_id
            or fence != bound.fencing_epoch or fence > binding.fencing_epoch
            or stored.request_sha256 != bound.request_sha256
            or stored.before_revision != bound.workspace_revision
            or row.post_binding != {
                "workspace_revision": stored.after_revision,
                "physical_volume_identity": stored.physical_volume_identity,
            }
        ):
            return False
    return True


async def _read_slot(
    session: AsyncSession, state: RecoveryStateV2,
) -> _CompilerSlot | None:
    from yleum_api.services.project_cell_recovery_release import _locked_release

    owner, binding, cursor = state.identity, state.binding, state.execution
    assert binding is not None and cursor.pending_effect_id is not None
    assert state.continuation_claim is not None
    locked = await _locked_release(session, owner.run_id, state, reading_agent_build=True)
    if locked is None:
        return None
    run, workspace, operation = locked
    if (
        _stored_request_payload(operation) != {} or operation.status == "completed"
        or run.prompt_hash != owner.prompt_hash or run.response_mode not in {"build", "edit"}
        or (operation.next_attempt_at is not None and operation.next_attempt_at > datetime.now(UTC))
    ):
        return None
    profile = (workspace.provider_metadata or {}).get("profile_version")
    if not isinstance(profile, str) or profile not in {
        "docker-owner-cell-resources-v1", "docker-owner-cell-resources-v2",
    }:
        return None
    if (
        operation.idempotency_key
        != f"generation:{run.id}:release:{profile}:cycle:{cursor.lease_cycle_seq}"
        or await session.scalar(select(GenerationRecoveryEffect.effect_id).where(
            GenerationRecoveryEffect.run_id == owner.run_id,
            GenerationRecoveryEffect.logical_step_seq > cursor.logical_step_seq,
        ).limit(1)) is not None
    ):
        return None
    rows = list((await session.scalars(select(GenerationRecoveryEffect).where(
        GenerationRecoveryEffect.run_id == owner.run_id,
        GenerationRecoveryEffect.logical_step_seq == cursor.logical_step_seq,
        GenerationRecoveryEffect.kind == "tool",
    ).order_by(GenerationRecoveryEffect.effect_ordinal).limit(6).with_for_update())).all())
    if (
        not 2 <= len(rows) < 6 or rows[-1].effect_ordinal != rows[-2].effect_ordinal + 1
        or not _settled_prior_legacy_action(rows[:-2], state)
    ):
        return None
    prefix = None
    for ordinal, (row, spec) in enumerate(zip(rows[-2:], _ROLE_SPECS, strict=True)):
        role, phase, contract = spec
        if (
            row.effect_id != uuid5(cursor.pending_effect_id, f"physical:{ordinal}")
            or not _owns_effect(row, state, AGENT_DEADLINE_BUILD_TOOL_KEY)
            or row.operation_id is None or row.checkpoint_ref is not None
        ):
            return None
        command = receipts.CommandBinding.model_validate_json(json.dumps(row.pre_binding))
        activity = command.activity
        if (
            (command.workspace_id, command.fencing_epoch, command.workspace_revision)
            != (workspace.id, binding.fencing_epoch, binding.source_sha256)
            or command.context_binding != binding or activity is None
            or activity.operation_id != row.operation_id or activity.phase != phase
            or row.operation_id != uuid5(
                owner.run_id,
                f"command:{workspace.id}:{binding.fencing_epoch}:{activity.proof_key}:{role}:{contract}",
            )
        ):
            return None
        lease = await session.get(ProjectCellActivityLease, row.operation_id, with_for_update=True)
        if (
            lease is None
            or (lease.workspace_id, lease.generation_run_id, lease.fencing_epoch,
                lease.kind, lease.phase, lease.proof_key)
            != (workspace.id, owner.run_id, binding.fencing_epoch,
                "command", phase, activity.proof_key)
        ):
            return None
        if ordinal == 0:
            prefix = _stored_receipt(row, command, state)
            if prefix is None or lease.state != "completed" or lease.finished_at is None:
                return None
            continue
        assert prefix is not None
        retained = None
        finished = (
            lease.state in {ActivityState.COMPLETED, ActivityState.FAILED, ActivityState.TIMED_OUT}
            and lease.finished_at is not None
        )
        active = lease.state == "active" and lease.finished_at is None
        if row.state == "confirmed":
            retained = _stored_receipt(row, command, state)
            if (
                retained is None
                or (retained.physical_volume, retained.physical_volume_identity)
                != (prefix.physical_volume, prefix.physical_volume_identity)
                or not (finished or active)
            ):
                return None
        elif (
            row.state != "inflight" or row.receipt_ref is not None
            or row.receipt_sha256 is not None or row.post_binding is not None
            or not active
        ):
            return None
        if await session.scalar(select(ProjectCellActivityLease.operation_id).where(
            ProjectCellActivityLease.workspace_id == workspace.id,
            ProjectCellActivityLease.state == "active",
            ProjectCellActivityLease.operation_id != row.operation_id,
        ).limit(1)) is not None or not await _provider_stopped(session, run):
            return None
        return _CompilerSlot(
            row.operation_id, row.effect_id, row.revision, row.input_digest, command,
            prefix, profile, retained, finished,
        )
    return None


async def _read_terminal_receipt(
    state: RecoveryStateV2, slot: _CompilerSlot,
) -> tuple[ProjectCellCommandReceipt, ActivityState] | None:
    owner, binding = state.identity, state.binding
    assert binding is not None
    command = slot.command
    context = receipts.CommandContext.model_validate(
        await recovery_execution.load_execution_context(
            owner, command.context_ref, binding=command.context_binding,
        ), strict=True,
    )
    if (
        (context.cmd, context.task_role, context.timeout_seconds)
        != ("omnia:fast_check", "fast_check", 480)
        or receipts._command_request_hash(command, slot.operation_id, owner.run_id, context)
        != command.request_sha256
        or receipts.digest({
            "cmd": context.cmd, "task_role": context.task_role,
            "timeout_seconds": context.timeout_seconds,
        }) != slot.input_digest
        or workspace_revision_digest(context.before_files) != binding.source_sha256
    ):
        return None
    status = await orchestrator_client.project_cell_agent_command_operation(
        command.workspace_id, operation_id=slot.operation_id,
        generation_run_id=owner.run_id, fencing_epoch=binding.fencing_epoch,
    )
    receipt, response = status.receipt, status.terminal_response
    if (
        status.state != "terminal" or status.operation_id != slot.operation_id
        or receipt is None or response is None
        or (slot.persisted_receipt is not None and receipt != slot.persisted_receipt)
        or not _matches_command_receipt(receipt, command, state, slot.operation_id)
        or (receipt.physical_volume, receipt.physical_volume_identity)
        != (slot.prefix.physical_volume, slot.prefix.physical_volume_identity)
        or response.ok != (response.exit_code == 0 and not response.timed_out)
        or response.environment_mutated or response.before_identity != response.after_identity
    ):
        return None
    receipt.require_response(response)
    current = await orchestrator_client.project_cell_agent_identity(
        command.workspace_id, generation_run_id=owner.run_id, fencing_epoch=binding.fencing_epoch,
    )
    if (
        current != response.after_identity
        or (current.workspace_revision, current.schema_data_digest, current.environment_digest)
        != (binding.source_sha256, binding.schema_sha256, binding.environment_sha256)
    ):
        return None
    witness = ProofIdentity(
        workspace_id=command.workspace_id, generation_run_id=owner.run_id,
        fencing_epoch=binding.fencing_epoch, workspace_revision=current.workspace_revision,
        dependency_digest=current.dependency_digest, schema_data_digest=current.schema_data_digest,
        cell_manifest_digest=current.cell_manifest_digest,
        base_image_digest=current.environment_digest,
        toolchain_digest=current.environment_digest, resource_profile_version=slot.profile,
        build_config_digest=current.build_config_digest,
    )
    assert command.activity is not None
    if command.activity.proof_key != witness.proof_key:
        return None
    outcome = ActivityState.COMPLETED if response.ok else (
        ActivityState.TIMED_OUT if response.timed_out else ActivityState.FAILED
    )
    return receipt, outcome


async def confirm_agent_build_release(
    factory: async_sessionmaker[AsyncSession], run_id: UUID, state: RecoveryStateV2,
) -> bool:
    """Finish the owning command activity using its receipt without rerunning the tool."""
    from yleum_api.services.project_cell_recovery_release import _locked_release

    if (
        run_id != state.identity.run_id or not agent_build_command_cursor(state)
        or state.execution.physical_state != "release_pending"
    ):
        return False

    async def defer(session: AsyncSession) -> None:
        owned = await _locked_release(session, run_id, state, reading_agent_build=True)
        if owned is None:
            return
        _, _, operation = owned
        now = datetime.now(UTC)
        if (
            _stored_request_payload(operation) != {} or operation.status == "completed"
            or (operation.next_attempt_at is not None and operation.next_attempt_at > now)
        ):
            return
        await recovery_execution._require_session_owner(run_id)
        operation.next_attempt_at = next_dependency_check(
            run_id, failures=max(1, operation.attempt_count), now=now,
        )
        await session.commit()

    try:
        async with factory() as session:
            slot = await _read_slot(session, state)
            if slot is None:
                await defer(session)
                return False
        if not await _owns_build_context(state):
            async with factory() as session:
                await defer(session)
            return False
        if slot.activity_finished:
            return True
        try:
            terminal = await _read_terminal_receipt(state, slot)
        except (OrchestratorUnavailable, OrchestratorBadRequest, TimeoutError):
            terminal = None
        await recovery_execution._require_session_owner(run_id)
        async with factory() as session:
            if await _read_slot(session, state) != slot:
                return False
            if terminal is None:
                await defer(session)
                return False
            effect = await session.get(GenerationRecoveryEffect, slot.effect_id)
            lease = await session.get(ProjectCellActivityLease, slot.operation_id)
            assert effect is not None and lease is not None
            receipt, outcome = terminal
            if slot.persisted_receipt is None:
                encoded = {
                    field: str(value) if isinstance(value, UUID) else value
                    for field, value in asdict(receipt).items()
                }
                effect.state, effect.receipt_ref = "confirmed", encoded
                effect.receipt_sha256 = receipts.digest(encoded)
                effect.post_binding = {
                    "workspace_revision": receipt.after_revision,
                    "physical_volume_identity": receipt.physical_volume_identity,
                }
                effect.revision += 1
            await finish_activity(
                session, operation_id=slot.operation_id, state=outcome,
                finished_at=max(datetime.now(UTC), lease.heartbeat_at),
            )
            await session.commit()
            return True
    except (
        RecoveryContractError, TypeError, ValueError, OrchestratorUnavailable,
        S3Error, ObjectTransportError, OSError,
    ):
        async with factory() as session:
            await defer(session)
        return False
