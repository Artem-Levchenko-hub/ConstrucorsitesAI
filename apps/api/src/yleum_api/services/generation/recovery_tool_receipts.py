"""Reconcile owning controller write receipts before exposing saved observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellWorkspace
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_reconciliation as reconciliation
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryIdentity,
    RecoveryStateV2,
    Sha256,
)
from yleum_api.services.orchestrator_client import (
    ProjectCellAgentExecResponse,
    ProjectCellCommandReceipt,
    ProjectCellSourceWriteReceipt,
    command_wire_digest,
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class PhysicalIntentBinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal[1] = 1
    workspace_id: UUID
    fencing_epoch: PositiveInt
    workspace_revision: Sha256
    request_sha256: Sha256
    context_ref: CheckpointRef
    context_binding: RecoveryBinding | None

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("source write version must be an integer")
        return value


class SourceWriteBinding(PhysicalIntentBinding):
    kind: Literal["source_write"] = "source_write"


class SourceWriteContext(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    entry: Literal["result"] = "result"
    kind: Literal["source_write_v1"] = "source_write_v1"
    input_digest: Sha256
    files: dict[str, str]
    deletes: list[str]
    observation: dict[str, Any] | None
    expected_written: NonNegativeInt
    expected_deleted: NonNegativeInt

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("source write schema must be an integer")
        return value


@dataclass(frozen=True)
class SourceWriteOperation:
    effect_id: UUID
    operation_id: UUID
    identity: RecoveryIdentity
    binding: SourceWriteBinding
    context: SourceWriteContext
    existing: bool


def request_digest(
    *,
    workspace_id: UUID,
    run_id: UUID,
    fencing_epoch: int,
    revision: str,
    files: dict[str, str],
    deletes: list[str],
) -> str:
    return digest(
        {
            "protocol": "source-write/v1",
            "workspace_id": str(workspace_id),
            "generation_run_id": str(run_id),
            "fencing_epoch": fencing_epoch,
            "expected_revision": revision,
            "files": {
                path: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for path, content in files.items()
            },
            "deletes": sorted(deletes),
        }
    )


async def _owned(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
) -> (
    tuple[RecoveryStateV2, reconciliation.ToolEffectCursor, GenerationRecoveryEffect | None] | None
):
    owned = await reconciliation.require_tool_owner(
        session,
        run_id=run_id,
        project_id=project_id,
        user_id=user_id,
    )
    if owned is None:
        return None
    state, cursor = owned
    effect_id = uuid5(cursor.root_effect_id, f"physical:{cursor.ordinal}")
    row = await session.get(GenerationRecoveryEffect, effect_id)
    if row is not None and (
        row.run_id != run_id
        or row.project_id != project_id
        or row.user_id != user_id
        or row.kind != "tool"
        or row.payload_version != 1
        or row.logical_step_seq != cursor.logical_step_seq
        or row.tool_call_id != cursor.tool_call_id
        or row.state not in {"inflight", "confirmed", "unknown"}
    ):
        raise RecoveryContractError("source write logical effect conflicts")
    if row is not None:
        expected_operation = uuid5(effect_id, "controller-operation-v1")
        if isinstance(row.pre_binding, dict) and row.pre_binding.get("kind") == "command_v1":
            bound = CommandBinding.model_validate_json(json.dumps(row.pre_binding))
            if bound.activity is not None:
                await _require_command_activity(session, state, bound, new=False)
                expected_operation = bound.activity.operation_id
        if row.operation_id != expected_operation:
            raise RecoveryContractError("physical operation binding conflicts")
    return state, cursor, row


async def load_source_write(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    input_digest: str,
) -> SourceWriteOperation | None:
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None or owned[2] is None:
            return None
        state, _, row = owned
        assert row is not None
        if row.input_digest != input_digest:
            raise RecoveryContractError("source write action changed")
        binding = SourceWriteBinding.model_validate_json(json.dumps(row.pre_binding), strict=True)
        effect_id, operation_id, identity = row.effect_id, row.operation_id, state.identity
    raw = await execution.load_execution_context(
        identity,
        binding.context_ref,
        binding=binding.context_binding,
    )
    context = SourceWriteContext.model_validate(raw, strict=True)
    if context.input_digest != input_digest or binding.request_sha256 != request_digest(
        workspace_id=binding.workspace_id,
        run_id=run_id,
        fencing_epoch=binding.fencing_epoch,
        revision=binding.workspace_revision,
        files=context.files,
        deletes=context.deletes,
    ):
        raise RecoveryContractError("source write private context conflicts")
    assert operation_id is not None
    return SourceWriteOperation(effect_id, operation_id, identity, binding, context, True)


async def require_source_write_reconciliation(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
) -> None:
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None or owned[2] is None:
            raise RecoveryContractError("cold source operation has no owning receipt target")
        SourceWriteBinding.model_validate_json(json.dumps(owned[2].pre_binding))


async def prepare_source_write(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    input_digest: str,
    workspace_id: UUID,
    fencing_epoch: int,
    revision: str,
    files: dict[str, str],
    deletes: tuple[str, ...],
    observation: dict[str, Any] | None,
    before_files: dict[str, str],
) -> SourceWriteOperation | None:
    previous = await load_source_write(
        factory,
        run_id=run_id,
        project_id=project_id,
        user_id=user_id,
        input_digest=input_digest,
    )
    if previous is not None:
        return previous  # Status only; a previous intent never licenses POST.
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None:
            return None
        state, cursor, row = owned
        if row is not None:
            raise RecoveryContractError("source write reservation raced")
        if cursor.reconcile_only:
            raise RecoveryContractError("cold source write has no owning intent")
        expected_identity, expected_claim = state.identity, state.continuation_claim
        expected_revision, ordinal = state.state_revision, cursor.ordinal
        context_binding = state.binding
    context = SourceWriteContext(
        input_digest=input_digest,
        files=files,
        deletes=list(deletes),
        observation=observation,
        expected_written=sum(before_files.get(path) != value for path, value in files.items()),
        expected_deleted=sum(path in before_files for path in deletes),
    )
    ref = await execution.store_execution_context(
        expected_identity,
        context.model_dump(mode="json"),
        binding=context_binding,
    )
    binding = SourceWriteBinding(
        workspace_id=workspace_id,
        fencing_epoch=fencing_epoch,
        workspace_revision=revision,
        request_sha256=request_digest(
            workspace_id=workspace_id,
            run_id=run_id,
            fencing_epoch=fencing_epoch,
            revision=revision,
            files=files,
            deletes=list(deletes),
        ),
        context_ref=ref,
        context_binding=context_binding,
    )
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None:
            raise RecoveryContractError("source write ownership disappeared")
        state, cursor, row = owned
        if (
            row is not None
            or cursor.ordinal != ordinal
            or state.identity != expected_identity
            or state.continuation_claim != expected_claim
            or state.state_revision != expected_revision
        ):
            raise RecoveryContractError("source write reservation changed during context upload")
        effect_id = uuid5(cursor.root_effect_id, f"physical:{ordinal}")
        operation_id = uuid5(effect_id, "controller-operation-v1")
        session.add(
            GenerationRecoveryEffect(
                effect_id=effect_id,
                operation_id=operation_id,
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                logical_step_seq=cursor.logical_step_seq,
                effect_ordinal=await reconciliation._next_effect_ordinal(
                    session, run_id, cursor.logical_step_seq
                ),
                kind="tool",
                input_digest=input_digest,
                state="inflight",
                attempt_seq=cursor.claim.attempt_seq,
                claim_token=cursor.claim.token,
                tool_call_id=cursor.tool_call_id,
                pre_binding=binding.model_dump(mode="json"),
            )
        )
        await session.commit()
    return SourceWriteOperation(effect_id, operation_id, expected_identity, binding, context, False)


async def accept_source_write(
    factory: async_sessionmaker[AsyncSession],
    operation: SourceWriteOperation,
    receipt: ProjectCellSourceWriteReceipt | None,
    current_source: Callable[[], Awaitable[tuple[str, dict[str, str]]]],
) -> dict[str, Any] | None:
    """A receipt and current full post-state are both needed, before checkpoint restore."""
    bound, identity, context = operation.binding, operation.identity, operation.context
    if (
        receipt is None
        or receipt.operation_id != operation.operation_id
        or receipt.workspace_id != bound.workspace_id
        or receipt.project_id != identity.project_id
        or receipt.owner_id != identity.user_id
        or receipt.generation_run_id != identity.run_id
        or receipt.fencing_epoch != bound.fencing_epoch
        or receipt.before_revision != bound.workspace_revision
        or receipt.request_sha256 != bound.request_sha256
        or receipt.written != context.expected_written
        or receipt.deleted != context.expected_deleted
    ):
        raise RecoveryContractError("source write terminal receipt conflicts")
    encoded = {
        key: str(value) if isinstance(value, UUID) else value
        for key, value in asdict(receipt).items()
    }
    # Re-parse even direct server doubles; opaque incarnation must be a full hash.
    ProjectCellSourceWriteReceipt.from_json(encoded)
    revision, files = await current_source()
    if (
        revision != receipt.after_revision
        or any(files.get(path) != value for path, value in context.files.items())
        or any(path in files for path in context.deletes)
    ):
        raise RecoveryContractError("source write current post-state conflicts")
    async with factory() as session:
        owned = await _owned(
            session,
            run_id=identity.run_id,
            project_id=identity.project_id,
            user_id=identity.user_id,
        )
        if owned is None:
            raise RecoveryContractError("source write terminal owner disappeared")
        state, cursor, row = owned
        if (
            state.identity != identity
            or row is None
            or row.effect_id != operation.effect_id
            or row.input_digest != context.input_digest
            or row.pre_binding != bound.model_dump(mode="json")
        ):
            raise RecoveryContractError("source write terminal effect changed")
        receipt_hash = digest(encoded)
        if row.receipt_ref is not None and (
            row.receipt_ref != encoded or row.receipt_sha256 != receipt_hash
        ):
            raise RecoveryContractError("source write terminal receipt changed")
        row.state, row.receipt_ref, row.receipt_sha256 = "confirmed", encoded, receipt_hash
        row.post_binding = {
            "workspace_revision": revision,
            "physical_volume_identity": receipt.physical_volume_identity,
        }
        row.revision += 1
        await session.commit()  # Uncertain ACK does not expose the observation.
        cursor.ordinal += 1
    return context.observation


class CommandActivityBinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    operation_id: UUID
    proof_key: Sha256
    phase: str


class CommandBinding(PhysicalIntentBinding):
    kind: Literal["command_v1"] = "command_v1"
    activity: CommandActivityBinding | None = None


async def _require_command_activity(
    session: AsyncSession,
    state: RecoveryStateV2,
    bound: CommandBinding,
    *,
    new: bool,
) -> None:
    activity = bound.activity
    if activity is None:
        return
    row = await session.get(ProjectCellActivityLease, activity.operation_id)
    workspace = await session.get(ProjectCellWorkspace, bound.workspace_id)
    if (
        row is None
        or workspace is None
        or workspace.project_id != state.identity.project_id
        or workspace.owner_id != state.identity.user_id
        or workspace.generation_run_id != state.identity.run_id
        or row.workspace_id != bound.workspace_id
        or row.generation_run_id != state.identity.run_id
        or row.fencing_epoch != bound.fencing_epoch
        or row.kind != "command"
        or row.proof_key != activity.proof_key
        or row.phase != activity.phase
        or row.state == "cancelled"
        or (new and (row.state != "active" or row.deadline_at <= datetime.now(UTC)))
    ):
        raise RecoveryContractError("command owning activity conflicts")


class CommandContext(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    entry: Literal["result"] = "result"
    kind: Literal["command_v1"] = "command_v1"
    before_files: dict[str, str]
    cmd: str
    timeout_seconds: PositiveInt
    task_role: str | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema(cls, value: Any) -> Any:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported command context version")
        return value


@dataclass(frozen=True)
class CommandOperation:
    effect_id: UUID
    operation_id: UUID
    identity: RecoveryIdentity
    binding: CommandBinding
    context: CommandContext
    input_digest: str
    existing: bool


def _command_request_hash(
    bound: CommandBinding, operation_id: UUID, run_id: UUID, context: CommandContext
) -> str:
    return command_wire_digest(
        {
            "protocol": "tracked-command/v1",
            "workspace_id": str(bound.workspace_id),
            "request": {
                "generation_run_id": str(run_id),
                "fencing_epoch": bound.fencing_epoch,
                "expected_revision": bound.workspace_revision,
                "cmd": context.cmd,
                "timeout_seconds": context.timeout_seconds,
                "task_role": context.task_role,
                "operation_id": str(operation_id),
                "track_receipt": True,
            },
        }
    )


async def require_command_reconciliation(
    factory: async_sessionmaker[AsyncSession], *, run_id: UUID, project_id: UUID, user_id: UUID
) -> None:
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None or owned[2] is None:
            raise RecoveryContractError("cold command has no owning intent")
        CommandBinding.model_validate_json(json.dumps(owned[2].pre_binding))


async def command_role_target(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    allow_missing: bool = False,
) -> tuple[UUID, str] | None:
    cursor = reconciliation._TOOL_EFFECT.get()
    if cursor is None or not cursor.reconcile_only:
        return None
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None or owned[2] is None:
            if allow_missing:
                return None
            raise RecoveryContractError("cold role command has no owning intent")
        state, _, row = owned
        assert row is not None
        binding = CommandBinding.model_validate_json(json.dumps(row.pre_binding))
        if binding.activity is None:
            raise RecoveryContractError("cold command has no owning role activity")
    raw = await execution.load_execution_context(
        state.identity,
        binding.context_ref,
        binding=binding.context_binding,
    )
    context = CommandContext.model_validate(raw, strict=True)
    role = context.task_role
    phases = {
        "bootstrap": "prepare",
        "fast_check": "fast_check",
        "full_build": "final_build",
        "restore_runtime": "final_build",
    }
    if (
        role not in phases
        or phases[role] != binding.activity.phase
        or _command_request_hash(binding, binding.activity.operation_id, run_id, context)
        != binding.request_sha256
    ):
        raise RecoveryContractError("cold command role context conflicts")
    return binding.activity.operation_id, role


async def prepare_command(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    fencing_epoch: int,
    revision: str,
    cmd: str,
    timeout_seconds: int,
    task_role: str | None,
    before_files: dict[str, str],
    activity_operation_id: UUID | None = None,
    activity_proof_key: str | None = None,
) -> CommandOperation | None:
    semantic = {"cmd": cmd, "timeout_seconds": timeout_seconds, "task_role": task_role}
    input_digest = digest(semantic)
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None:
            return None
        state, cursor, row = owned
        identity, claim, version, ordinal = (
            state.identity,
            state.continuation_claim,
            state.state_revision,
            cursor.ordinal,
        )
        context_binding = state.binding
        effect_id = uuid5(cursor.root_effect_id, f"physical:{ordinal}")
        operation_id = uuid5(effect_id, "controller-operation-v1")
        previous = None
        if row is not None:
            if row.input_digest != input_digest:
                raise RecoveryContractError("command logical input changed")
            previous = CommandBinding.model_validate_json(json.dumps(row.pre_binding))
            if (
                previous.activity.operation_id if previous.activity else None
            ) != activity_operation_id:
                raise RecoveryContractError("command owning activity changed")
            assert row.operation_id is not None
            operation_id = row.operation_id
        elif cursor.reconcile_only:
            raise RecoveryContractError("cold command has no owning intent")
        activity = None
        if previous is None and activity_operation_id is not None:
            lease = await session.get(ProjectCellActivityLease, activity_operation_id)
            phase = {
                "bootstrap": "prepare",
                "fast_check": "fast_check",
                "full_build": "final_build",
                "restore_runtime": "final_build",
            }.get(task_role or "")
            if (
                lease is None
                or lease.proof_key is None
                or phase is None
                or lease.phase != phase
                or lease.proof_key != activity_proof_key
            ):
                raise RecoveryContractError("command owning role is unavailable")
            activity = CommandActivityBinding(
                operation_id=activity_operation_id,
                proof_key=lease.proof_key,
                phase=phase,
            )
            operation_id = activity_operation_id
    if previous is not None:
        raw = await execution.load_execution_context(
            identity, previous.context_ref, binding=previous.context_binding
        )
        context = CommandContext.model_validate(raw, strict=True)
        if (
            previous.workspace_id != workspace_id
            or {key: getattr(context, key) for key in semantic} != semantic
            or _command_request_hash(previous, operation_id, run_id, context)
            != previous.request_sha256
        ):
            raise RecoveryContractError("command immutable request changed")
        return CommandOperation(
            effect_id, operation_id, identity, previous, context, input_digest, True
        )
    context = CommandContext(
        before_files=dict(before_files),
        cmd=cmd,
        timeout_seconds=timeout_seconds,
        task_role=task_role,
    )
    ref = await execution.store_execution_context(
        identity, context.model_dump(mode="json"), binding=context_binding
    )
    bound = CommandBinding(
        workspace_id=workspace_id,
        fencing_epoch=fencing_epoch,
        workspace_revision=revision,
        request_sha256="0" * 64,
        context_ref=ref,
        context_binding=context_binding,
        activity=activity,
    )
    bound = bound.model_copy(
        update={
            "request_sha256": _command_request_hash(
                bound,
                operation_id,
                run_id,
                context,
            )
        }
    )
    async with factory() as session:
        owned = await _owned(session, run_id=run_id, project_id=project_id, user_id=user_id)
        if owned is None:
            raise RecoveryContractError("command owner disappeared")
        state, cursor, row = owned
        if (
            row is not None
            or cursor.ordinal != ordinal
            or state.identity != identity
            or state.continuation_claim != claim
            or state.state_revision != version
        ):
            raise RecoveryContractError("command reservation changed during context upload")
        await _require_command_activity(session, state, bound, new=True)
        session.add(
            GenerationRecoveryEffect(
                effect_id=effect_id,
                operation_id=operation_id,
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                logical_step_seq=cursor.logical_step_seq,
                effect_ordinal=await reconciliation._next_effect_ordinal(
                    session, run_id, cursor.logical_step_seq
                ),
                kind="tool",
                input_digest=input_digest,
                state="inflight",
                attempt_seq=cursor.claim.attempt_seq,
                claim_token=cursor.claim.token,
                tool_call_id=cursor.tool_call_id,
                pre_binding=bound.model_dump(mode="json"),
            )
        )
        await session.commit()
    return CommandOperation(effect_id, operation_id, identity, bound, context, input_digest, False)


async def accept_command(
    factory: async_sessionmaker[AsyncSession],
    operation: CommandOperation,
    receipt: ProjectCellCommandReceipt | None,
    response: ProjectCellAgentExecResponse | None,
    current_source: Callable[[], Awaitable[tuple[str, dict[str, str]]]],
) -> dict[str, str]:
    bound, identity = operation.binding, operation.identity
    if (
        receipt is None
        or response is None
        or receipt.operation_id != operation.operation_id
        or receipt.workspace_id != bound.workspace_id
        or receipt.project_id != identity.project_id
        or receipt.owner_id != identity.user_id
        or receipt.generation_run_id != identity.run_id
        or receipt.dispatch_fencing_epoch != bound.fencing_epoch
        or receipt.before_revision != bound.workspace_revision
        or receipt.request_sha256 != bound.request_sha256
    ):
        raise RecoveryContractError("command terminal receipt conflicts")
    encoded = {
        key: str(value) if isinstance(value, UUID) else value
        for key, value in asdict(receipt).items()
    }
    ProjectCellCommandReceipt.from_json(encoded).require_response(response)
    revision, files = await current_source()
    if revision != receipt.after_revision:
        raise RecoveryContractError("command current post-state conflicts")
    async with factory() as session:
        owned = await _owned(
            session,
            run_id=identity.run_id,
            project_id=identity.project_id,
            user_id=identity.user_id,
        )
        if owned is None:
            raise RecoveryContractError("command terminal owner disappeared")
        state, cursor, row = owned
        if (
            state.identity != identity
            or row is None
            or row.effect_id != operation.effect_id
            or row.input_digest != operation.input_digest
            or row.pre_binding != bound.model_dump(mode="json")
        ):
            raise RecoveryContractError("command terminal effect changed")
        receipt_hash = digest(encoded)
        if row.receipt_ref is not None and (
            row.receipt_ref != encoded or row.receipt_sha256 != receipt_hash
        ):
            raise RecoveryContractError("command terminal receipt changed")
        row.state, row.receipt_ref, row.receipt_sha256 = "confirmed", encoded, receipt_hash
        row.post_binding = {
            "workspace_revision": revision,
            "physical_volume_identity": receipt.physical_volume_identity,
        }
        if operation.existing and bound.activity is not None:
            from yleum_api.services.project_cell_activity import ActivityState, finish_activity

            lease = await session.get(ProjectCellActivityLease, bound.activity.operation_id)
            assert lease is not None  # _owned revalidated the immutable activity above.
            await finish_activity(
                session,
                operation_id=lease.operation_id,
                state=(
                    ActivityState.TIMED_OUT
                    if response.timed_out
                    else ActivityState.COMPLETED
                    if response.ok
                    else ActivityState.FAILED
                ),
                finished_at=max(datetime.now(UTC), lease.heartbeat_at),
                diagnostic=response.detail,
            )
        row.revision += 1
        await session.commit()
        cursor.ordinal += 1
    before = operation.context.before_files
    return {
        path: files.get(path, "")
        for path in set(before) | set(files)
        if before.get(path) != files.get(path)
    }


class ToolResultBinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    kind: Literal["tool_result_v1"] = "tool_result_v1"
    index: NonNegativeInt
    context_binding: RecoveryBinding | None


async def _result_owner(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    input_digest: str,
    index: int,
) -> tuple[RecoveryStateV2, reconciliation.ToolEffectCursor, GenerationRecoveryEffect | None]:
    owned = await reconciliation.require_tool_owner(
        session,
        run_id=run_id,
        project_id=project_id,
        user_id=user_id,
        consumed_index=index,
    )
    if owned is None:
        raise RecoveryContractError("logical tool result requires executable recovery")
    state, cursor = owned
    row = await session.get(GenerationRecoveryEffect, uuid5(cursor.root_effect_id, "result-v1"))
    if row is not None:
        if (
            row.run_id != run_id
            or row.project_id != project_id
            or row.user_id != user_id
            or row.kind != "tool"
            or row.payload_version != 1
            or row.state != "confirmed"
            or row.logical_step_seq != cursor.logical_step_seq
            or row.tool_call_id != cursor.tool_call_id
            or row.operation_id is not None
            or row.provider_attempt_id is not None
            or row.input_digest != input_digest
            or row.checkpoint_ref is None
        ):
            raise RecoveryContractError("logical tool result conflicts")
        binding = ToolResultBinding.model_validate_json(json.dumps(row.pre_binding))
        if binding.index != index:
            raise RecoveryContractError("logical tool ordering conflicts")
    return state, cursor, row


async def load_tool_result(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    input_digest: str,
    index: int,
) -> dict[str, Any] | None:
    async with factory() as session:
        state, _, row = await _result_owner(
            session,
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            input_digest=input_digest,
            index=index,
        )
        if row is None:
            return None
        binding = ToolResultBinding.model_validate_json(json.dumps(row.pre_binding))
        ref = CheckpointRef.model_validate_json(json.dumps(row.checkpoint_ref))
        identity = state.identity
    value = await execution.load_execution_context(identity, ref, binding=binding.context_binding)
    if (
        set(value) != {"schema_version", "entry", "kind", "input_digest", "observation"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["entry"] != "result"
        or value["kind"] != "tool_result_v1"
        or value["input_digest"] != input_digest
        or type(value["observation"]) is not dict
    ):
        raise RecoveryContractError("logical tool result context conflicts")
    # Object I/O is not ownership authority: recheck after the awaited read.
    async with factory() as session:
        _, _, current = await _result_owner(
            session,
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            input_digest=input_digest,
            index=index,
        )
        if current is None or current.checkpoint_ref != ref.model_dump(mode="json"):
            raise RecoveryContractError("logical tool result changed during read")
    return value["observation"]


async def _require_terminal_physical(
    session: AsyncSession, cursor: reconciliation.ToolEffectCursor, run_id: UUID
) -> None:
    rows = (
        await session.scalars(
            select(GenerationRecoveryEffect).where(
                GenerationRecoveryEffect.run_id == run_id,
                GenerationRecoveryEffect.logical_step_seq == cursor.logical_step_seq,
                GenerationRecoveryEffect.tool_call_id == cursor.tool_call_id,
                GenerationRecoveryEffect.operation_id.is_not(None),
            )
        )
    ).all()
    if any(row.state != "confirmed" or row.receipt_ref is None for row in rows):
        raise RecoveryContractError("tool physical outcome is unresolved")


async def save_tool_result(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    input_digest: str,
    index: int,
    observation: dict[str, Any],
) -> None:
    async with factory() as session:
        state, cursor, row = await _result_owner(
            session,
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            input_digest=input_digest,
            index=index,
        )
        if row is not None:
            existing = True
        else:
            existing = False
            if state.execution.pending_effect_id != cursor.root_effect_id:
                raise RecoveryContractError("tool result has no current execution intent")
            await _require_terminal_physical(session, cursor, run_id)
        identity, context_binding = state.identity, state.binding
    if existing:
        if (
            await load_tool_result(
                factory,
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                input_digest=input_digest,
                index=index,
            )
            != observation
        ):
            raise RecoveryContractError("logical tool result cannot be rewritten")
        return
    ref = await execution.store_execution_context(
        identity,
        {
            "schema_version": 1,
            "entry": "result",
            "kind": "tool_result_v1",
            "input_digest": input_digest,
            "observation": observation,
        },
        binding=context_binding,
    )
    async with factory() as session:
        current, cursor, row = await _result_owner(
            session,
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            input_digest=input_digest,
            index=index,
        )
        if row is not None or current != state:
            raise RecoveryContractError("logical tool result reservation changed")
        await _require_terminal_physical(session, cursor, run_id)
        session.add(
            GenerationRecoveryEffect(
                effect_id=uuid5(cursor.root_effect_id, "result-v1"),
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                logical_step_seq=cursor.logical_step_seq,
                effect_ordinal=await reconciliation._next_effect_ordinal(
                    session, run_id, cursor.logical_step_seq
                ),
                kind="tool",
                input_digest=input_digest,
                state="confirmed",
                attempt_seq=cursor.claim.attempt_seq,
                claim_token=cursor.claim.token,
                tool_call_id=cursor.tool_call_id,
                pre_binding=ToolResultBinding(
                    index=index, context_binding=context_binding
                ).model_dump(mode="json"),
                checkpoint_ref=ref.model_dump(mode="json"),
            )
        )
        await session.commit()


async def reconcile_preparation_receipts(
    factory: async_sessionmaker[AsyncSession], *, identity: RecoveryIdentity, handle: Any,
) -> None:
    """Check original effects before conditional bootstrap reads skip old writes.

    The executor consumes each typed owning receipt under the current lease.
    This never restores an old source checkpoint or grants another physical POST.
    """
    cursor = reconciliation._TOOL_EFFECT.get()
    if cursor is None or not cursor.reconcile_only or cursor.ordinal:
        raise RecoveryContractError("preparation reconciliation has no cold cursor")
    while True:
        async with factory() as session:
            owned = await _owned(session, run_id=identity.run_id,
                                 project_id=identity.project_id, user_id=identity.user_id)
            if owned is None:
                raise RecoveryContractError("preparation receipt owner disappeared")
            row = owned[2]
            if row is None:
                break
            kind, input_digest = (row.pre_binding or {}).get("kind"), row.input_digest
        ordinal = cursor.ordinal
        if kind == "command_v1":
            target = await command_role_target(
                factory, run_id=identity.run_id, project_id=identity.project_id,
                user_id=identity.user_id,
            )
            if target is None or handle.run_role is None:
                raise RecoveryContractError("preparation command has no typed role")
            from yleum_api.services.project_cell_executor import ProjectCellCommandRole
            operation_id, role = target
            await handle.run_role(ProjectCellCommandRole(role), operation_id)
        else:
            operation = await load_source_write(
                factory, run_id=identity.run_id, project_id=identity.project_id,
                user_id=identity.user_id, input_digest=input_digest,
            )
            if operation is None or operation.context.observation is not None:
                raise RecoveryContractError("preparation write context conflicts")
            await handle.stage_patch(operation.context.files, tuple(operation.context.deletes))
        if cursor.ordinal != ordinal + 1:
            raise RecoveryContractError("preparation effect was not reconciled")
    cursor.ordinal = 0  # Re-evaluation may skip an already-current managed source patch.


async def require_preparation_receipts(
    factory: async_sessionmaker[AsyncSession], *, identity: RecoveryIdentity,
) -> None:
    """Inner legacy fail-soft code cannot consume an unresolved physical effect."""
    from sqlalchemy import select

    async with factory() as session:
        owned = await _owned(session, run_id=identity.run_id,
                             project_id=identity.project_id, user_id=identity.user_id)
        if owned is None:
            raise RecoveryContractError("preparation receipt owner disappeared")
        cursor = owned[1]
        rows = await session.scalars(select(GenerationRecoveryEffect).where(
            GenerationRecoveryEffect.run_id == identity.run_id,
            GenerationRecoveryEffect.logical_step_seq == cursor.logical_step_seq,
            GenerationRecoveryEffect.tool_call_id == cursor.tool_call_id,
        ))
        for row in rows:
            if (row.kind != "tool" or row.state != "confirmed" or row.receipt_ref is None
                or row.receipt_sha256 != digest(row.receipt_ref)):
                raise RecoveryContractError("preparation physical receipt remains unresolved")
