"""Committed scheduler handoffs; a Python exception never creates authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    ContinuationClaim,
    ExecutionCursor,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryIdentity,
    RecoveryState,
    RecoveryStateV2,
    decode_recovery,
)
from yleum_api.services.generation_deployment_drain import require_generation_admission

# Keep the original container key so old strict readers see an unsupported
# version, rather than ignoring a new sibling namespace and replaying dispatch.
RECOVERY_KEY = "recovery_v1"


@dataclass(frozen=True, slots=True)
class SessionOwnership:
    run_id: UUID
    connection: AsyncConnection
    access: asyncio.Lock = field(default_factory=asyncio.Lock)


_SESSION_OWNER: ContextVar[SessionOwnership | None] = ContextVar(
    "recovery_session_owner",
    default=None,
)


@asynccontextmanager
async def session_connection_access(connection: AsyncConnection) -> AsyncIterator[None]:
    """Serialize the complete transaction on the actual lock-owning connection."""
    owner = _SESSION_OWNER.get()
    if owner is None:
        yield  # Legacy monitor callers have no concurrent recovery consumer.
        return
    if owner.connection is not connection:
        raise RecoveryContractError("continuation ownership connection conflicts")
    async with owner.access:
        yield


def execution_enabled(project_id: UUID) -> bool:
    settings = get_settings()
    if not getattr(settings, "generation_recovery_execution_enabled", False):
        return False
    if settings.generation_recovery_mode == "on":
        return True
    allowed = {
        value.strip() for value in settings.generation_recovery_canary_project_ids.split(",")
    }
    return settings.generation_recovery_mode == "canary" and str(project_id) in allowed


async def _require_session_owner(run_id: UUID) -> None:
    owner = _SESSION_OWNER.get()
    if owner is None or owner.run_id != run_id:
        raise RecoveryContractError("continuation has no session owner")
    # Exact pg session lock, not an expired timestamp or a contextvar alone.
    async with session_connection_access(owner.connection):
        held = await owner.connection.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory' AND classid=1195724366 AND objid=:key "
                "AND objsubid=2 AND granted)"
            ),
            {"key": int.from_bytes(run_id.bytes[-4:], "big")},
        )
        await owner.connection.commit()
    if not held:
        raise RecoveryContractError("continuation session lock was lost")


async def _initial_quiescent(session: AsyncSession, run_id: UUID) -> bool:
    # Initial bootstrap can omit a source artifact only if no physical/model
    # operation was ever admitted, not merely because no receipt is known.
    for model, predicate in (
        (ProjectCellOperation, ProjectCellOperation.execution_run_id == run_id),
        (ProjectCellOperation, ProjectCellOperation.generation_run_id == run_id),
        (ProjectCellActivityLease, ProjectCellActivityLease.generation_run_id == run_id),
        (GenerationProviderAttempt, GenerationProviderAttempt.run_id == run_id),
        (GenerationRecoveryEffect, GenerationRecoveryEffect.run_id == run_id),
    ):
        if await session.scalar(select(model).where(predicate).limit(1)) is not None:
            return False
    return True


def _owned_release_receipt(
    operation: ProjectCellOperation | None, *, run_id: UUID, workspace_id: UUID, fence: int,
) -> bool:
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse

    if (
        operation is None or operation.workspace_id != workspace_id
        or operation.generation_run_id != run_id or operation.kind != "release"
        or operation.status != "completed" or operation.finished_at is None
        or operation.fencing_epoch != fence or type(operation.result_payload) is not dict
    ):
        return False
    try:
        receipt = ProjectCellResourceResponse.from_json(operation.result_payload)
    except Exception:
        return False
    return (
        receipt.workspace_id == workspace_id and receipt.fencing_epoch == fence
        and receipt.state == "resources_ready"
    )


async def _released_quiescent(
    session: AsyncSession,
    run: GenerationRun,
    state: RecoveryStateV2,
) -> bool:
    """Only the controller's owned terminal release proves the writer stopped."""
    execution = state.execution
    if (
        execution.physical_state not in {"release_pending", "released"}
        or execution.release_operation_id is None
        or execution.control_workspace_id is None
    ):
        return False
    operation = await session.get(ProjectCellOperation, execution.release_operation_id)
    workspace = await session.get(
        ProjectCellWorkspace,
        execution.control_workspace_id,
        with_for_update=True,
        populate_existing=True,
    )
    if (
        operation is None
        or workspace is None
        or workspace.project_id != run.project_id
        or workspace.owner_id != run.user_id
        or workspace.deleted_at is not None
        or workspace.generation_run_id not in {None, run.id}
        or not _owned_release_receipt(
            operation, run_id=run.id, workspace_id=workspace.id, fence=workspace.fencing_epoch,
        )
    ):
        return False
    # Reconcile a crash after the terminal controller/SQL receipt but before the
    # release caller committed its local unlink. Never infer this from expiry.
    workspace.generation_run_id = None
    return True


async def claim_continuation(
    session: AsyncSession,
    *,
    run_id: UUID,
    expected_attempt: int,
    worker_id: str,
    now: datetime,
) -> ContinuationClaim | None:
    from yleum_api.services.generation.recovery_store import _baseline_sha, _locked_owner

    await _require_session_owner(run_id)
    await require_generation_admission(session)
    hint = await session.get(GenerationRun, run_id, populate_existing=True)
    if hint is None or hint.status != "running":
        return None
    run, project = await _locked_owner(
        session,
        run_id=run_id,
        project_id=hint.project_id,
        user_id=hint.user_id,
    )
    state = recovery_state(run)
    if (
        not isinstance(state, RecoveryStateV2)
        or state.attempt_seq != expected_attempt
        or state.phase not in {
            "waiting_dependency", "reconciling", "working", "repairing", "verifying",
        }
        or (
            state.phase in {"waiting_dependency", "reconciling"}
            and (state.next_check_at is None or state.next_check_at > now)
        )
    ):
        return None
    from yleum_api.services.generation.agent_publication import publication_committed
    if not await publication_committed(session, run=run, project=project, state=state):
        await _baseline_sha(session, run=run, project=project, state=state)
    # A process can die before its WAIT acknowledgement. Working is resumable
    # only after the same physical quiescence proof below, never lease expiry.
    initial = state.execution.physical_state == "no_effects"
    quiescent = (
        await _initial_quiescent(session, run_id)
        if initial
        else await _released_quiescent(session, run, state)
    )
    if not quiescent:
        return None
    execution = (
        state.execution
        if initial
        else state.execution.model_copy(
            update={
                "lease_cycle_seq": state.execution.lease_cycle_seq + 1,
                "physical_state": "released",
            }
        )
    )
    claim = ContinuationClaim(
        run_id=run_id,
        token=uuid4(),
        attempt_seq=state.attempt_seq + 1,
        worker_id=worker_id,
        expires_at=now + timedelta(seconds=60),
    )
    updated = state.model_copy(
        update={
            "attempt_seq": claim.attempt_seq,
            "execution": execution,
            "state_revision": state.state_revision + 1,
            "continuation_claim": claim,
            "phase": "working",
            "next_check_at": None,
        }
    )
    run.agent_state = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
    await session.flush()
    return claim


async def resume_generation(claim: ContinuationClaim) -> None:
    from yleum_api.services.generation.agent_pipeline import resume_agent_generation

    await _require_session_owner(claim.run_id)
    await resume_agent_generation(claim)


class GenerationYielded(BaseException):
    """Committed control transfer, never an error eligible for repair/fallback."""

    def __init__(
        self,
        *,
        run_id: UUID,
        checkpoint: CheckpointRef | None,
        attempt_seq: int,
        phase: str,
    ) -> None:
        super().__init__("generation durably yielded")
        self.run_id, self.checkpoint = run_id, checkpoint
        self.attempt_seq, self.phase = attempt_seq, phase


def has_recovery_state(run: GenerationRun) -> bool:
    return isinstance(run.agent_state, dict) and RECOVERY_KEY in run.agent_state


def recovery_state(run: GenerationRun) -> RecoveryState | None:
    if not has_recovery_state(run):
        return None
    state = decode_recovery(run.agent_state[RECOVERY_KEY])
    if state is None:
        raise RecoveryContractError("present recovery state is corrupt")
    if (
        state.identity.run_id != run.id
        or state.identity.project_id != run.project_id
        or state.identity.user_id != run.user_id
        or state.identity.prompt_hash != run.prompt_hash
    ):
        raise RecoveryContractError("recovery identity conflicts with current run")
    return state


async def recovery_failure_outcome(
    run_id: UUID,
) -> Literal["legacy", "preserve", "cancelled"]:
    """A failed read is uncertainty, never authority to fail durable effects.

    This guard grants no execution or yield authority. Valid shadow V1 keeps
    the legacy failure path; unsupported/corrupt persisted readers stay intact.
    """
    try:
        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with factory() as session:
            run = await session.get(GenerationRun, run_id, populate_existing=True)
            if run is None:
                return "preserve"
            if run.status in {"cancel_requested", "cancelled"}:
                return "cancelled"
            if not isinstance(run.agent_state, dict):
                return "preserve"
            state = recovery_state(run)
            return "preserve" if isinstance(state, RecoveryStateV2) else "legacy"
    except Exception:
        return "preserve"


async def initialize_execution(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    context: Callable[[], dict[str, Any]],
) -> RecoveryStateV2 | None:
    """Persist the original executable entry before runtime preparation.

    This creates a new V2 record only. An existing record must enter through
    its committed continuation cursor, never through the original pipeline.
    """
    from yleum_api.services.generation.recovery_requirements import frozen_requirements
    from yleum_api.services.generation.recovery_store import _baseline_sha, _locked_owner
    from yleum_api.services.generation_runs import load_generation_dispatch

    async with factory() as session:
        hint = await session.get(GenerationRun, run_id)
        if hint is None:
            raise RecoveryContractError("execution run is missing")
        if has_recovery_state(hint):
            previous = recovery_state(hint)  # Preserve unknown/corrupt records unchanged.
            if not isinstance(previous, RecoveryStateV2):
                return None
            raise RecoveryContractError("existing recovery must use its durable entry")
        if not execution_enabled(hint.project_id):
            return None
        settings = get_settings()
        if not (
            settings.generation_provider_attempts_enabled
            and settings.generation_response_retention_enabled
        ):
            raise RecoveryContractError("executable recovery dependencies are disabled")
        await _require_session_owner(run_id)
        await require_generation_admission(session)
        run, project = await _locked_owner(
            session,
            run_id=run_id,
            project_id=hint.project_id,
            user_id=hint.user_id,
        )
        if has_recovery_state(run):
            raise RecoveryContractError("execution initialization raced another writer")
        dispatch = load_generation_dispatch(run)
        identity = RecoveryIdentity(
            run_id=run.id,
            project_id=run.project_id,
            user_id=run.user_id,
            prompt_hash=run.prompt_hash,
            accepted_snapshot_id=dispatch.current_snapshot_id,
            supersedes_run_id=None,
        )
        claim = ContinuationClaim(
            run_id=run.id,
            token=uuid4(),
            attempt_seq=0,
            worker_id="initial",
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        state = RecoveryStateV2(
            identity=identity,
            continuation_claim=claim,
            requirements=frozen_requirements(run),
            execution=ExecutionCursor(
                stage="bootstrap", substep="runtime_prepare", physical_state="no_effects"
            ),
        )
        await _baseline_sha(session, run=run, project=project, state=state)
        ref = await store_execution_context(identity, context())
        state = state.model_copy(
            update={
                "context_ref": ref,
                "execution": state.execution.model_copy(update={"execution_context_ref": ref}),
            }
        )
        run.agent_state = {**run.agent_state, RECOVERY_KEY: state.model_dump(mode="json")}
        await session.commit()  # An unknown acknowledgement cannot enter runtime preparation.
        return state


async def commit_execution_cursor(
    factory: async_sessionmaker[AsyncSession],
    *,
    previous: RecoveryStateV2,
    execution: ExecutionCursor,
    context: dict[str, Any],
    phase: Literal["working", "waiting_dependency", "reconciling", "needs_input"] = "working",
) -> RecoveryStateV2:
    """Commit context/cursor together; never issue effects after an uncertain commit.

    This advances logical execution only. Source changes require the existing
    full source checkpoint writer first; historical proof/issuer objects are not
    restored or minted here. Waiting does not assert physical quiescence.
    """
    from yleum_api.services.generation.recovery_store import (
        _baseline_sha,
        _locked_owner,
        _validate_binding,
        _validate_retained_checkpoint,
    )

    old_budget, budget = previous.execution.budget, execution.budget
    if any(
        getattr(budget, name) < getattr(old_budget, name)
        for name in ("steps", "provider_attempts", "repair_calls")
    ) or (
        old_budget.absolute_deadline is not None
        and budget.absolute_deadline != old_budget.absolute_deadline
    ):
        raise RecoveryContractError("execution budget cannot be refunded or extended")
    await _require_session_owner(previous.identity.run_id)
    # Upload outside the SQL locks. Orphan objects survive an uncertain commit;
    # they cannot grant execution without the committed pointer and exact claim.
    ref = await store_execution_context(previous.identity, context, binding=previous.binding)
    next_execution = execution.model_copy(update={"execution_context_ref": ref})
    updated = decode_recovery(
        previous.model_dump(mode="json")
        | {
            "execution": next_execution.model_dump(mode="json"),
            "cursor": execution.stage,
            "context_ref": ref.model_dump(mode="json"),
            "phase": phase,
            "state_revision": previous.state_revision + 1,
            "next_check_at": (
                datetime.now(UTC).isoformat()
                if phase in {"waiting_dependency", "reconciling"}
                else None
            ),
        }
    )
    assert isinstance(updated, RecoveryStateV2)
    async with factory() as session:
        await require_generation_admission(session)
        run, project = await _locked_owner(
            session,
            run_id=previous.identity.run_id,
            project_id=previous.identity.project_id,
            user_id=previous.identity.user_id,
        )
        current = recovery_state(run)
        if (
            not isinstance(current, RecoveryStateV2)
            or current != previous
            or current.continuation_claim is None
            or current.phase not in {"working", "repairing", "verifying"}
        ):
            raise RecoveryContractError("execution cursor or continuation claim changed")
        baseline_sha = await _baseline_sha(session, run=run, project=project, state=current)
        await _validate_retained_checkpoint(run, current, baseline_sha=baseline_sha)
        if current.binding is not None and phase == "working":
            await _validate_binding(session, run=run, binding=current.binding)
        await _require_session_owner(run.id)
        run.agent_state = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
        await session.commit()
    return updated


async def commit_current_wait(
    factory: async_sessionmaker[AsyncSession], previous: RecoveryStateV2,
    *, runtime_control: tuple[UUID | None, int | None] | None = None,
) -> RecoveryStateV2:
    """Retain the current committed cursor after an uncertain local write.

    No context upload or local data replacement: a lost acknowledgement may
    already have advanced both context and arithmetic in the same transaction.
    """
    from yleum_api.services.generation.recovery_store import _locked_owner

    await _require_session_owner(previous.identity.run_id)
    async with factory() as session:
        run, _ = await _locked_owner(
            session, run_id=previous.identity.run_id,
            project_id=previous.identity.project_id, user_id=previous.identity.user_id,
        )
        current = recovery_state(run)
        if (
            not isinstance(current, RecoveryStateV2)
            or current.identity != previous.identity
            or current.continuation_claim is None
            or current.continuation_claim != previous.continuation_claim
            or current.attempt_seq != previous.attempt_seq
            or current.state_revision < previous.state_revision
            or current.execution.lease_cycle_seq != previous.execution.lease_cycle_seq
            or current.execution.control_workspace_id != previous.execution.control_workspace_id
            or current.execution.control_fencing_epoch != previous.execution.control_fencing_epoch
        ):
            raise RecoveryContractError("wait cannot adopt another execution owner")
        control_id = current.execution.control_workspace_id
        control_fence = current.execution.control_fencing_epoch
        if control_id is None and current.binding is not None:
            control_id = UUID(current.binding.workspace_id)
            control_fence = current.binding.fencing_epoch
        execution = current.execution
        if runtime_control is not None and runtime_control != (control_id, control_fence):
            runtime_id, runtime_fence = runtime_control
            if (
                execution.physical_state != "released" or runtime_id != control_id
                or type(runtime_fence) is not int or type(control_fence) is not int
                or runtime_fence <= control_fence
            ):
                raise RecoveryContractError("wait cannot adopt an unproven runtime lease")
            release = (
                await session.get(ProjectCellOperation, execution.release_operation_id)
                if execution.release_operation_id is not None else None
            )
            if control_id is None or not _owned_release_receipt(
                release, run_id=run.id, workspace_id=control_id, fence=control_fence,
            ):
                raise RecoveryContractError("wait has no owned terminal release receipt")
            # Reacquisition can precede source reproof. Retain that current physical
            # owner for cleanup, without accepting its changed source or context.
            control_fence = runtime_fence
            execution = execution.model_copy(update={
                "physical_state": "active", "control_fencing_epoch": runtime_fence,
                "release_operation_id": None,
            })
        if control_id is not None:
            workspace = await session.get(
                ProjectCellWorkspace, control_id, with_for_update=True, populate_existing=True,
            )
            if (
                workspace is None or workspace.project_id != run.project_id
                or workspace.owner_id != run.user_id or workspace.generation_run_id != run.id
                or workspace.fencing_epoch != control_fence or workspace.deleted_at is not None
                or workspace.state != "ready"
            ):
                raise RecoveryContractError("wait workspace ownership changed")
        if current.phase not in {"working", "repairing", "verifying", "waiting_dependency"}:
            raise RecoveryContractError("wait execution phase changed")
        await _require_session_owner(run.id)
        if current.phase == "waiting_dependency" and execution == current.execution:
            return current  # Confirmed SQL read, not an inferred commit acknowledgement.
        updated = current.model_copy(update={
            "phase": "waiting_dependency", "next_check_at": datetime.now(UTC),
            "state_revision": current.state_revision + 1, "execution": execution,
        })
        run.agent_state = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
        await session.commit()
        return updated


def _frame_retry(frame: dict[str, Any], previous: object, *, reconcile: bool) -> tuple[int, int]:
    """Restore loop arithmetic; the SQL receipt still decides retry permission."""
    if frame["kind"] == "native":
        return 0, 1
    value = frame["value"]
    attempt = value.get("attempt")
    if type(attempt) is not int or not 0 <= attempt < 5:
        raise RecoveryContractError("legacy provider attempt is invalid")
    if not reconcile and attempt:
        if (
            type(previous) is not dict
            or previous.get("kind") != "legacy"
            or type(previous.get("value")) is not dict
        ):
            raise RecoveryContractError("legacy retry has no prior model step")
        before = previous["value"]
        if (
            type(before.get("attempt")) is not int
            or before["attempt"] + 1 != attempt
            or any(
                before.get(key) != value.get(key)
                for key in (
                    "contract",
                    "step",
                    "active_model",
                    "call_messages",
                    "retry_started_at",
                )
            )
        ):
            raise RecoveryContractError("legacy retry changed its original model step")
    return attempt, 5


@dataclass
class ExecutionDriver:
    """Invocation-owned adapter; only SQL and owning receipts authorize effects."""

    factory: async_sessionmaker[AsyncSession]
    state: RecoveryStateV2
    context: dict[str, Any]
    snapshot: Callable[[], Awaitable[tuple[RecoveryBinding, dict[str, str]]]]
    runtime_control: tuple[UUID | None, int | None] | None = None
    _reconcile_response: bool = field(init=False)
    _tool_position: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._reconcile_response = self.state.execution.substep in {
            "response_wait",
            "response_validated",
            "tool_ready",
            "tool_wait",
        }

    def restore_frame(self, kind: str) -> dict[str, Any] | None:
        if not self._reconcile_response:
            return None
        frame = self.context.get("active_frame")
        if (
            type(frame) is not dict
            or set(frame) != {"version", "kind", "value"}
            or type(frame["version"]) is not int
            or frame["version"] != 1
            or frame["kind"] != kind
            or type(frame["value"]) is not dict
        ):
            raise RecoveryContractError("executable loop continuation frame is unavailable")
        return frame["value"]

    async def save(
        self,
        execution: ExecutionCursor,
        *,
        context: dict[str, Any],
        phase: Literal["working", "waiting_dependency", "reconciling", "needs_input"] = "working",
    ) -> None:
        from yleum_api.services.generation.recovery_store import save_recovery_checkpoint

        binding, files = await self.snapshot()
        if self.runtime_control is not None:
            workspace_id, fence = self.runtime_control
            if workspace_id is None or fence is None:
                raise RecoveryContractError("current runtime control binding is missing")
            execution = execution.model_copy(
                update={
                    "physical_state": "active",
                    "control_workspace_id": workspace_id,
                    "control_fencing_epoch": fence,
                    "release_operation_id": None,
                }
            )
        pipeline = context.get("pipeline", {})
        baseline = pipeline.get("baseline", {}) if type(pipeline) is dict else {}
        original = baseline.get("files", {}) if type(baseline) is dict else {}
        if type(original) is not dict or any(type(path) is not str for path in original):
            raise RecoveryContractError("checkpoint original file inventory is invalid")
        deleted = tuple(sorted(set(original) - set(files)))
        ref = await store_execution_context(self.state.identity, context, binding=binding)
        execution = execution.model_copy(update={"execution_context_ref": ref})
        next_state = self.state.model_copy(
            update={
                "binding": binding,
                "execution": execution,
                "cursor": execution.stage,
                "context_ref": ref,
                "phase": phase,
                "next_check_at": (
                    datetime.now(UTC) if phase in {"waiting_dependency", "reconciling"} else None
                ),
            }
        )
        # The first candidate checkpoint is established while still at bootstrap;
        # entering an agent/finalization cursor requires that retained source.
        async with self.factory() as session:
            checkpoint = await save_recovery_checkpoint(
                session,
                run_id=self.state.identity.run_id,
                expected_attempt=self.state.attempt_seq,
                expected_revision=self.state.state_revision,
                state=next_state,
                files=files,
                deleted_paths=deleted,
            )
        self.state = next_state.model_copy(
            update={
                "checkpoint": checkpoint,
                "state_revision": self.state.state_revision + 1,
            }
        )
        self.context = context

    async def wait(self) -> None:
        # The dependency itself may be unavailable. Retain the last confirmed
        # source/context and active/unknown physical status; no stopped inference.
        self.state = await commit_current_wait(
            self.factory, self.state, runtime_control=self.runtime_control,
        )
        raise GenerationYielded(
            run_id=self.state.identity.run_id,
            checkpoint=self.state.checkpoint,
            attempt_seq=self.state.attempt_seq,
            phase=self.state.phase,
        )

    async def tool(
        self, key: str, action: Any, invoke: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        try:
            return await self._tool(key, action, invoke)
        except Exception:
            await self.wait()
            raise AssertionError("committed wait must leave the invocation") from None

    async def _tool(
        self, key: str, action: Any, invoke: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        from yleum_api.services.generation import recovery_tool_receipts as receipts
        from yleum_api.services.generation.recovery_reconciliation import (
            ToolEffectCursor,
            tool_effect_scope,
        )

        claim = self.state.continuation_claim
        if claim is None or not isinstance(key, str) or not key:
            raise RecoveryContractError("tool continuation identity is missing")
        index = self._tool_position
        input_digest = receipts.digest({"name": action.name, "args": action.args})
        active = {"version": 1, "index": index, "key": key, "input_digest": input_digest}
        consumed = index < self.state.execution.next_tool_index
        cold = self.state.execution.substep == "tool_wait" and not consumed
        cursor = ToolEffectCursor(
            claim,
            self.state.state_revision,
            self.state.execution.logical_step_seq,
            key,
            reconcile_only=cold or consumed,
        )
        if not consumed:
            if cold:
                if (
                    self.context.get("active_tool") != active
                    or self.state.execution.pending_effect_id != cursor.root_effect_id
                ):
                    raise RecoveryContractError("tool continuation changed its original action")
            else:
                if index != self.state.execution.next_tool_index:
                    raise RecoveryContractError("tool continuation skipped an observation")
                context = {**self.context, "active_tool": active}
                self.state = await commit_execution_cursor(
                    self.factory,
                    previous=self.state,
                    execution=self.state.execution.model_copy(
                        update={
                            "substep": "tool_wait",
                            "pending_effect_id": cursor.root_effect_id,
                        }
                    ),
                    context=context,
                )
                self.context = context
                cursor.state_revision = self.state.state_revision
        kwargs: dict[str, Any] = dict(
            run_id=self.state.identity.run_id,
            project_id=self.state.identity.project_id,
            user_id=self.state.identity.user_id,
            input_digest=input_digest,
            index=index,
        )
        with tool_effect_scope(cursor):
            observation = await receipts.load_tool_result(self.factory, **kwargs)
            if consumed:
                if observation is None:
                    raise RecoveryContractError("consumed tool result is unavailable")
            else:
                if cold and action.name in {"write_file", "edit_file", "stage_patch"}:
                    await receipts.require_source_write_reconciliation(
                        self.factory,
                        run_id=self.state.identity.run_id,
                        project_id=self.state.identity.project_id,
                        user_id=self.state.identity.user_id,
                    )
                if cold and action.name in {"build", "bash"}:
                    await receipts.require_command_reconciliation(
                        self.factory,
                        run_id=self.state.identity.run_id,
                        project_id=self.state.identity.project_id,
                        user_id=self.state.identity.user_id,
                    )
                if observation is None:
                    if cold and action.name not in {
                        "write_file",
                        "edit_file",
                        "stage_patch",
                        "build",
                        "bash",
                    }:
                        raise RecoveryContractError("cold tool outcome is unresolved")
                    observation = await invoke()
                    await receipts.save_tool_result(self.factory, observation=observation, **kwargs)
                elif cold and action.name in {
                    "write_file",
                    "edit_file",
                    "stage_patch",
                    "build",
                    "bash",
                }:
                    # A terminal logical result may precede source checkpoint ACK.
                    # The executor reloads the owning physical receipt/current source;
                    # it must not restore the old snapshot or repeat the write.
                    if await invoke() != observation:
                        raise RecoveryContractError("source tool result changed")
        if not consumed:
            await self.save(
                self.state.execution.model_copy(
                    update={
                        "substep": "tool_ready",
                        "next_tool_index": index + 1,
                        "pending_effect_id": self.state.execution.provider_effect_id,
                    }
                ),
                context={**self.context, "active_tool": None},
            )
        self._tool_position += 1
        return observation

    async def provider(
        self,
        frame: dict[str, Any],
        invoke: Callable[[], Awaitable[Any]],
    ) -> Any:
        from yleum_api.services.generation.recovery_reconciliation import (
            ProviderEffectCursor,
            provider_effect_scope,
        )
        from yleum_api.services.llm_client import LLMError, LLMOutputTruncated

        if (
            set(frame) != {"version", "kind", "value"}
            or type(frame["version"]) is not int
            or frame["version"] != 1
            or frame["kind"] not in {"native", "legacy"}
            or type(frame["value"]) is not dict
        ):
            raise RecoveryContractError("invalid executable loop frame")
        claim = self.state.continuation_claim
        if claim is None:
            raise RecoveryContractError("provider continuation has no current claim")
        reconcile = self._reconcile_response
        outer_attempt, outer_allowance = _frame_retry(
            frame,
            self.context.get("active_frame"),
            reconcile=reconcile,
        )
        call_deadline = None
        if frame["kind"] == "legacy":
            raw_deadline = frame["value"].get("call_deadline")
            if not isinstance(raw_deadline, str):
                raise RecoveryContractError("legacy provider call clock is missing")
            call_deadline = datetime.fromisoformat(raw_deadline)
            if call_deadline.utcoffset() is None:
                raise RecoveryContractError("legacy provider call clock has no absolute basis")
        if reconcile:
            if self.context.get("active_frame") != frame:
                raise RecoveryContractError("provider continuation frame changed")
            cursor = ProviderEffectCursor(
                claim,
                self.state.state_revision,
                self.state.execution.logical_step_seq,
                True,
                outer_attempt=outer_attempt,
                outer_allowance=outer_allowance,
                call_deadline=call_deadline,
            )
        else:
            continuing = outer_attempt > 0
            seq = self.state.execution.logical_step_seq + (0 if continuing else 1)
            cursor = ProviderEffectCursor(
                claim,
                self.state.state_revision + 1,
                seq,
                outer_attempt=outer_attempt,
                outer_allowance=outer_allowance,
                call_deadline=call_deadline,
            )
            budget = self.state.execution.budget.model_copy(
                update={
                    "steps": self.state.execution.budget.steps + (0 if continuing else 1),
                }
            )
            execution = self.state.execution.model_copy(
                update={
                    "stage": (
                        "finalization" if self.state.execution.stage == "finalization" else "agent"
                    ),
                    "substep": "response_wait",
                    "logical_step_seq": seq,
                    "provider_effect_id": cursor.root_effect_id,
                    "pending_effect_id": cursor.root_effect_id,
                    "next_tool_index": 0,
                    "budget": budget,
                }
            )
            await self.save(execution, context={**self.context, "active_frame": frame})
        try:
            with provider_effect_scope(cursor):
                response = await invoke()
        except LLMOutputTruncated:
            self._reconcile_response = False
            raise
        except LLMError as exc:
            if exc.provider_charge_ambiguous is False:
                self._reconcile_response = False
                raise
            await self.wait()
            raise AssertionError("committed wait must leave the invocation") from None
        except Exception:
            await self.wait()
            raise AssertionError("committed wait must leave the invocation") from None
        self._reconcile_response = False
        self._tool_position = 0
        return response


async def committed_yield_outcome(
    yielded: GenerationYielded,
) -> Literal["waiting", "cancelled", "conflict"]:
    """Re-read committed state; cancellation wins without trusting exception fields."""
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, yielded.run_id, populate_existing=True)
        if run is None:
            return "conflict"
        if run.status in {"cancel_requested", "cancelled"}:
            return "cancelled"
        try:
            state = recovery_state(run)
        except RecoveryContractError:
            return "conflict"
        if (
            run.status != "running"
            or not isinstance(state, RecoveryStateV2)
            or state.attempt_seq != yielded.attempt_seq
            or state.phase != yielded.phase
            or state.phase not in {"waiting_dependency", "reconciling", "needs_input"}
            or state.checkpoint != yielded.checkpoint
        ):
            return "conflict"
        return "waiting"


_CONTEXT_KIND = "generation_execution_context"
_CONTEXT_CHUNK_BYTES = 1024 * 1024


def _context_json(data: bytes | bytearray) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RecoveryContractError("duplicate executable context field")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise RecoveryContractError("non-finite executable context value")

    value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
    if type(value) is not dict:
        raise RecoveryContractError("invalid executable context shape")
    return value


def _require_context_value(value: dict[str, Any]) -> None:
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("entry") not in {"pipeline", "native", "legacy", "result"}
    ):
        raise RecoveryContractError("unsupported executable context schema")


async def store_execution_context(
    identity: RecoveryIdentity,
    value: dict[str, Any],
    *,
    binding: RecoveryBinding | None = None,
    chunk_bytes: int = _CONTEXT_CHUNK_BYTES,
) -> CheckpointRef:
    """Private, content-addressed context; callers establish SQL ownership first.

    Source objects keep their existing repository limits. This distinct manifest
    chains bounded objects without a total transcript/response limit or a second
    complete serialized RAM spool. No stored field grants receipt authority.
    """
    from yleum_api.services.generation import source_checkpoint

    _require_context_value(value)
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= _CONTEXT_CHUNK_BYTES:
        raise RecoveryContractError("invalid executable context chunk configuration")
    tail = None
    count = total = 0
    digest = hashlib.sha256()
    buffer = bytearray()

    async def put(data: bytes) -> CheckpointRef:
        key, sha = await source_checkpoint.store_checkpoint_object(
            identity.project_id,
            identity.run_id,
            data,
        )
        return CheckpointRef(object_key=key, sha256=sha)

    async def flush() -> None:
        nonlocal tail, count, total
        data = bytes(buffer)
        buffer.clear()
        digest.update(data)
        total += len(data)
        part = await put(data)
        node = {
            "version": 1,
            "kind": _CONTEXT_KIND + "_chunk",
            "index": count,
            "bytes": len(data),
            "data": part.model_dump(mode="json"),
            "previous": tail.model_dump(mode="json") if tail else None,
        }
        tail = await put(json.dumps(node, sort_keys=True, separators=(",", ":")).encode())
        count += 1

    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    for piece in encoder.iterencode(value):
        encoded = piece.encode("utf-8")
        for offset in range(0, len(encoded), chunk_bytes):
            fragment = encoded[offset : offset + chunk_bytes]
            position = 0
            while position < len(fragment):
                take = min(chunk_bytes - len(buffer), len(fragment) - position)
                buffer.extend(fragment[position : position + take])
                position += take
                if len(buffer) == chunk_bytes:
                    await flush()
    if buffer:
        await flush()
    root = {
        "version": 1,
        "kind": _CONTEXT_KIND,
        "complete": True,
        "identity": identity.model_dump(mode="json"),
        "count": count,
        "binding": binding.model_dump(mode="json") if binding else None,
        "bytes": total,
        "sha256": digest.hexdigest(),
        "tail": tail.model_dump(mode="json") if tail else None,
    }
    return await put(json.dumps(root, sort_keys=True, separators=(",", ":")).encode())


async def load_execution_context(
    identity: RecoveryIdentity,
    ref: CheckpointRef,
    *,
    binding: RecoveryBinding | None = None,
) -> dict[str, Any]:
    from yleum_api.services.generation import source_checkpoint

    async def read(part: CheckpointRef) -> bytes:
        value = await source_checkpoint.load_checkpoint_object(
            identity.project_id,
            identity.run_id,
            object_key=part.object_key,
            digest=part.sha256,
        )
        if value is None:
            raise RecoveryContractError("executable context object unavailable")
        return value

    def reference(raw: object) -> CheckpointRef:
        return CheckpointRef.model_validate_json(json.dumps(raw, allow_nan=False))

    try:
        root = _context_json(await read(ref))
        if (
            set(root)
            != {
                "version",
                "kind",
                "complete",
                "identity",
                "binding",
                "count",
                "bytes",
                "sha256",
                "tail",
            }
            or type(root["version"]) is not int
            or root["version"] != 1
            or root["kind"] != _CONTEXT_KIND
            or root["complete"] is not True
            or root["identity"] != identity.model_dump(mode="json")
            or root["binding"] != (binding.model_dump(mode="json") if binding else None)
            or type(root["count"]) is not int
            or root["count"] < 1
            or type(root["bytes"]) is not int
            or root["bytes"] < 1
            or root["count"] > root["bytes"]
        ):
            raise RecoveryContractError("invalid executable context manifest")
        current = reference(root["tail"])
        chunks: list[tuple[CheckpointRef, int]] = []
        seen = {ref.object_key}
        for index in range(root["count"] - 1, -1, -1):
            if current.object_key in seen:
                raise RecoveryContractError("executable context chain cycle")
            seen.add(current.object_key)
            node = _context_json(await read(current))
            if (
                set(node) != {"version", "kind", "index", "bytes", "data", "previous"}
                or type(node["version"]) is not int
                or node["version"] != 1
                or node["kind"] != _CONTEXT_KIND + "_chunk"
                or type(node["index"]) is not int
                or node["index"] != index
                or type(node["bytes"]) is not int
                or not 1 <= node["bytes"] <= _CONTEXT_CHUNK_BYTES
                or (node["previous"] is None) != (index == 0)
            ):
                raise RecoveryContractError("invalid executable context chain")
            chunks.append((reference(node["data"]), node["bytes"]))
            if index:
                current = reference(node["previous"])
        data = bytearray()
        for part, size in reversed(chunks):
            block = await read(part)
            if len(block) != size:
                raise RecoveryContractError("executable context chunk size mismatch")
            data.extend(block)
        if len(data) != root["bytes"] or hashlib.sha256(data).hexdigest() != root["sha256"]:
            raise RecoveryContractError("executable context completion digest mismatch")
        result = _context_json(data)
        _require_context_value(result)
        return result
    except (TypeError, KeyError, ValueError, UnicodeError) as exc:
        raise RecoveryContractError("executable context is incomplete or corrupt") from exc
