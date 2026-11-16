"""Committed scheduler handoffs; a Python exception never creates authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt
from sqlalchemy import exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    CompilerWitness,
    ContinuationClaim,
    ExecutionCursor,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryIdentity,
    RecoveryState,
    RecoveryStateV2,
    RecoveryStateV3,
    Sha256,
    decode_recovery,
)
from yleum_api.services.generation_deployment_drain import (
    require_generation_admission,
    try_continuation_admission,
)

if TYPE_CHECKING:
    from yleum_api.services.generation.expired_native_finalization import RetiredFinalizationPlan
    from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
    from yleum_api.services.generation.recovery_reconciliation import ProviderEffectCursor
    from yleum_api.services.orchestrator_client import ProjectCellEnvironmentRecoveryProof

# Keep the original container key so old strict readers see an unsupported
# version, rather than ignoring a new sibling namespace and replaying dispatch.
RECOVERY_KEY = "recovery_v1"
PROVIDER_REFUSAL_REDRIVE_KEY = "provider_refusal_redrive_v1"
EXPIRED_PROVIDER_ATTACHMENT_KEY = "expired_provider_attachment_v1"
_EXPIRED_PROVIDER_SCOPE_ENV = "GENERATION_EXPIRED_PROVIDER_REATTACH_SCOPE"
log = structlog.get_logger(__name__)


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


async def _require_session_owner(run_id: UUID, *, compatibility: bool = True) -> None:
    from yleum_api.services.generation.recovery_readers import require_recovery_readers

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
    if compatibility:
        await require_recovery_readers()


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


async def _response_wait_attempt(
    session: AsyncSession, state: RecoveryStateV2,
) -> GenerationProviderAttempt | None:
    from yleum_api.services.generation.recovery_reconciliation import provider_root_effect_id

    execution, owner = state.execution, state.identity
    if execution.substep != "response_wait":
        return None
    root = provider_root_effect_id(owner.run_id, execution.logical_step_seq)
    if execution.provider_effect_id != root or execution.pending_effect_id != root:
        return None
    effect, attempt = GenerationRecoveryEffect, GenerationProviderAttempt
    matches = await session.scalars(
        select(attempt).join(effect, effect.provider_attempt_id == attempt.attempt_id).where(
            effect.kind == "provider", effect.logical_step_seq == execution.logical_step_seq,
            effect.run_id == owner.run_id, effect.project_id == owner.project_id,
            effect.user_id == owner.user_id, effect.input_digest == attempt.request_sha256,
            attempt.run_id == owner.run_id, attempt.project_id == owner.project_id,
            attempt.user_id == owner.user_id, attempt.prompt_hash == owner.prompt_hash,
        ).order_by(effect.effect_ordinal.desc()).limit(1)
    )
    return matches.one_or_none()


class NativeProviderRedrive(Exception):
    """Resume with a new logical step after the one scheduled connection redrive."""


def _preconnect_redrive_attempt(run: GenerationRun) -> UUID | None:
    data = run.agent_state
    if not isinstance(data, dict) or PROVIDER_REFUSAL_REDRIVE_KEY not in data:
        return None
    receipt = data[PROVIDER_REFUSAL_REDRIVE_KEY]
    if (
        type(receipt) is not dict or set(receipt) != {"version", "attempt_id"}
        or type(receipt["version"]) is not int or receipt["version"] != 1
        or type(receipt["attempt_id"]) is not str
    ):
        raise RecoveryContractError("invalid provider refusal redrive receipt")
    try:
        attempt = UUID(receipt["attempt_id"])
    except ValueError as exc:
        raise RecoveryContractError("invalid provider refusal redrive attempt") from exc
    if str(attempt) != receipt["attempt_id"] or attempt.int == 0:
        raise RecoveryContractError("invalid provider refusal redrive attempt")
    return attempt


async def _closed_native_preconnect_chain(
    session: AsyncSession, state: RecoveryStateV3,
) -> GenerationProviderIdentity | None:
    from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

    attempt = await _response_wait_attempt(session, state)
    if attempt is None:
        return None
    # These rows are the sole no-dispatch evidence. A generic 5xx, a balance
    # refusal or even a confirmed 429 must not masquerade as a connect failure.
    exact = await session.scalar(text("""
        WITH RECURSIVE chain AS (
            SELECT a.*,0 AS depth FROM generation_provider_attempts a
            WHERE a.attempt_id=:leaf
            UNION ALL
            SELECT p.*,c.depth+1 FROM chain c
            JOIN generation_provider_attempts p ON p.attempt_id=c.parent_attempt_id
            WHERE c.depth<8 AND p.run_id=:run AND p.project_id=:project
                  AND p.user_id=:owner AND p.prompt_hash=:prompt AND p.request_sha256=:digest
        )
        SELECT bool_or(c.parent_attempt_id IS NULL)
            AND count(DISTINCT call.provider_scope)=2
            AND bool_and(COALESCE(
                c.phase='closed' AND c.terminal_reason='preconnect'
                AND call.provider_scope IN ('llmgw','aitunnel')
                AND call.route='/v1/messages' AND call.requested_model='claude-sonnet-5.5'
                AND call.status='failed' AND call.finished_at IS NOT NULL
                AND call.run_id=:run AND call.project_id=:project AND call.user_id=:owner
                AND call.receipt_hash IS NOT NULL AND call.usage_id IS NULL
                AND call.tokens_in IS NULL AND call.tokens_out IS NULL
                AND call.cache_read_tokens IS NULL AND call.cache_write_tokens IS NULL
                AND call.calculated_cost_rub IS NULL AND call.provider_cost_rub IS NULL
                AND call.provider_cost_usd IS NULL AND call.cost_provenance IS NULL
                AND response.state='complete' AND NOT response.reconciliation_hold
                AND response.reconciled_at IS NOT NULL
                AND response.manifest_key=c.response_manifest_key
                AND response.response_sha256=c.response_sha256
                AND effect.kind='provider' AND effect.logical_step_seq=:step
                AND effect.run_id=:run AND effect.project_id=:project AND effect.user_id=:owner
                AND effect.input_digest=:digest
                AND ((c.parent_attempt_id IS NOT NULL AND effect.effect_ordinal>0)
                    OR (effect.effect_id=:root AND effect.effect_ordinal=0)), FALSE))
        FROM chain c
        LEFT JOIN provider_calls call ON call.id=c.provider_call_id
        LEFT JOIN generation_responses response ON response.attempt_id=c.attempt_id
        LEFT JOIN generation_recovery_effects effect ON effect.provider_attempt_id=c.attempt_id
    """), {
        "leaf": attempt.attempt_id, "run": attempt.run_id, "project": attempt.project_id,
        "owner": attempt.user_id, "prompt": attempt.prompt_hash, "digest": attempt.request_sha256,
        "step": state.execution.logical_step_seq, "root": state.execution.provider_effect_id,
    })
    if exact is not True:
        return None
    return GenerationProviderIdentity(
        attempt.run_id, attempt.project_id, attempt.user_id, attempt.attempt_id,
        attempt.prompt_hash, attempt.request_sha256,
    )


async def _armed_preconnect_wait(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2,
) -> bool:
    # A release-only worker can stop the physical writer after the clock owner
    # dies. The drain observes that stop; resume_clock still charges an open interval.
    if (
        not isinstance(state, RecoveryStateV3)
        or state.phase != "waiting_dependency" or state.next_check_at is None
        or state.clock.wait_dependency is not None
        or (
            state.identity.run_id, state.identity.project_id, state.identity.user_id,
            state.identity.prompt_hash,
        ) != (run.id, run.project_id, run.user_id, run.prompt_hash)
    ):
        return False
    armed = _preconnect_redrive_attempt(run)
    if armed is None:
        return False
    closed = await _closed_native_preconnect_chain(session, state)
    return closed is not None and closed.attempt_id == armed


async def _retained_completion_attempt(
    session: AsyncSession, state: RecoveryStateV2,
) -> GenerationProviderAttempt | None:
    attempt = await _response_wait_attempt(session, state)
    if (
        attempt is None or attempt.phase != "closed" or attempt.terminal_reason != "completed"
        or attempt.provider_call_id is None or not attempt.response_manifest_key
        or not attempt.response_sha256
    ):
        return None
    stored = await session.scalar(text(
        "SELECT state FROM generation_responses WHERE attempt_id=:attempt "
        "AND manifest_key=:manifest AND response_sha256=:sha"
    ), {
        "attempt": attempt.attempt_id, "manifest": attempt.response_manifest_key,
        "sha": attempt.response_sha256,
    })
    return attempt if stored == "complete" else None


async def _has_retained_completion(
    factory: async_sessionmaker[AsyncSession], state: RecoveryStateV2,
) -> bool:
    async with factory() as session:
        return await _retained_completion_attempt(session, state) is not None


async def _is_retained_content_filter(
    factory: async_sessionmaker[AsyncSession], state: RecoveryStateV3,
    *, frame_kind: Literal["native", "legacy"],
) -> bool:
    from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
    from yleum_api.services.llm_client import require_recovered_response

    async with factory() as session:
        attempt = await _retained_completion_attempt(session, state)
        if attempt is None:
            return False
        identity = GenerationProviderIdentity(
            attempt.run_id, attempt.project_id, attempt.user_id, attempt.attempt_id,
            attempt.prompt_hash, attempt.request_sha256,
        )
    retained = await require_recovered_response(identity)
    if frame_kind == "native":
        return (
            retained.get("type") == "message"
            and retained.get("provider_finish_reason") == "content_filter"
            and "choices" not in retained
        )
    choices = retained.get("choices")
    return (
        isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict)
        and choices[0].get("finish_reason") == "content_filter"
    )


async def _defer_open_response(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV3, now: datetime,
) -> bool:
    from yleum_api.services.generation.recovery_clock import next_dependency_check

    attempt = await _response_wait_attempt(session, state)
    if attempt is None or attempt.phase not in {"dispatching", "indeterminate"}:
        return False
    # An open receipt cannot become usable through cell acquisition. A later
    # poll rereads the same logical step without reopening the active clock.
    failures = state.clock.dependency_failures + 1
    deferred = state.model_copy(update={
        "phase": "waiting_dependency",
        "next_check_at": next_dependency_check(run.id, failures=failures, now=now),
        "state_revision": state.state_revision + 1,
        "clock": state.clock.model_copy(update={"dependency_failures": failures}),
    })
    await _require_session_owner(run.id)
    run.agent_state = {**run.agent_state, RECOVERY_KEY: deferred.model_dump(mode="json")}
    await session.flush()
    return True


class ExpiredProviderReattachScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    run_id: UUID
    attempt_id: UUID
    state_revision: NonNegativeInt
    context_sha256: Sha256
    source_sha256: Sha256
    clock_sha256: Sha256


def expired_provider_reattach_scope() -> ExpiredProviderReattachScope | None:
    """An absent or invalid private worker scope never enables recovery."""
    from yleum_api.services.generation.expired_provider_authorization import (
        read_released_expired_provider_scope,
    )

    raw = os.environ.get(_EXPIRED_PROVIDER_SCOPE_ENV) or read_released_expired_provider_scope()
    if not raw or len(raw) > 4096:
        return None
    try:
        value = _context_json(raw.encode())
        if type(value.get("version")) is not int:
            return None
        scope = ExpiredProviderReattachScope.model_validate_json(raw)
        if any(str(getattr(scope, key)) != value[key] or not getattr(scope, key).int
               for key in ("run_id", "attempt_id")):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return scope


class ExpiredProviderAttachment(BaseModel):
    """Frozen authority of the parked run before claiming its next lease cycle."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    run_id: UUID
    attempt_id: UUID
    state_revision: NonNegativeInt
    attempt_seq: NonNegativeInt
    logical_step_seq: PositiveInt
    binding: RecoveryBinding
    context_ref: CheckpointRef
    checkpoint: CheckpointRef
    release_operation_id: UUID
    release_fencing_epoch: PositiveInt
    lease_cycle_seq: NonNegativeInt
    clock_sha256: Sha256

    def matches_scope(self, scope: ExpiredProviderReattachScope) -> bool:
        return (
            self.run_id == scope.run_id and self.attempt_id == scope.attempt_id
            and self.state_revision == scope.state_revision
            and self.context_ref.sha256 == scope.context_sha256
            and self.binding.source_sha256 == scope.source_sha256
            and self.clock_sha256 == scope.clock_sha256
        )

    def matches_claim(self, state: RecoveryStateV2) -> bool:
        from yleum_api.services.generation.expired_provider_drain import clock_sha256
        from yleum_api.services.generation.recovery_reconciliation import provider_root_effect_id

        execution = state.execution
        claim = state.continuation_claim
        delta = state.attempt_seq - self.attempt_seq
        root = provider_root_effect_id(self.run_id, self.logical_step_seq)
        return (
            isinstance(state, RecoveryStateV3) and state.identity.run_id == self.run_id
            and state.phase == "working" and state.next_check_at is None
            and state.binding == self.binding and state.checkpoint == self.checkpoint
            and state.context_ref == execution.execution_context_ref == self.context_ref
            and execution.stage == "finalization" and execution.substep == "response_wait"
            and execution.physical_state == "released"
            and execution.response_sha256 is None and execution.next_tool_index == 0
            and execution.budget.repair_calls == 1 and not state.unresolved_operation_ids
            and execution.logical_step_seq == self.logical_step_seq
            and execution.provider_effect_id == execution.pending_effect_id == root
            and execution.release_operation_id == self.release_operation_id
            and execution.control_fencing_epoch == self.release_fencing_epoch
            and str(execution.control_workspace_id) == self.binding.workspace_id
            and self.release_fencing_epoch == self.binding.fencing_epoch + 1
            and execution.lease_cycle_seq == self.lease_cycle_seq + 1
            and claim is not None and claim.run_id == self.run_id
            and claim.attempt_seq == state.attempt_seq
            and delta > 0 and state.state_revision - self.state_revision == delta
            and clock_sha256(state) == self.clock_sha256
        )


def load_expired_provider_attachment(run: GenerationRun) -> ExpiredProviderAttachment | None:
    if EXPIRED_PROVIDER_ATTACHMENT_KEY not in run.agent_state:
        return None
    raw = run.agent_state[EXPIRED_PROVIDER_ATTACHMENT_KEY]
    try:
        if type(raw) is not dict or type(raw.get("version")) is not int:
            raise ValueError("invalid attachment shape")
        attached = ExpiredProviderAttachment.model_validate_json(json.dumps(raw, allow_nan=False))
        for field in ("run_id", "attempt_id", "release_operation_id"):
            value = getattr(attached, field)
            if not value.int or str(value) != raw[field]:
                raise ValueError("invalid attachment identity")
        if attached.run_id != run.id:
            raise ValueError("attachment belongs to another run")
        return attached
    except (KeyError, TypeError, ValueError):
        raise RecoveryContractError("invalid expired provider attachment") from None


async def _attachment_release_matches(
    session: AsyncSession, attached: ExpiredProviderAttachment, *,
    workspace_id: UUID, historical_binding: RecoveryBinding,
) -> bool:
    from yleum_api.services.project_cells import ProjectCellValidationError, _stored_request_payload

    release = await session.get(ProjectCellOperation, attached.release_operation_id)
    if release is None or not _owned_release_receipt(
        release, run_id=attached.run_id, workspace_id=workspace_id,
        fence=attached.release_fencing_epoch,
    ):
        return False
    try:
        request = _stored_request_payload(release)
    except (ProjectCellValidationError, TypeError, ValueError):
        return False
    if request == {"require_environment_proof": {
        "source_sha256": attached.binding.source_sha256,
        "environment_sha256": attached.binding.environment_sha256,
    }}:
        return True
    # The verified two-row frame only allows the same environment. A changed
    # environment across two fences here requires both historical role proofs
    # and the intervening owned release, already checked by the caller.
    # The executor still has to attest the legacy release physically at E11.
    return (
        request == {}
        and historical_binding.fencing_epoch + 2 == attached.binding.fencing_epoch
        and historical_binding.environment_sha256 != attached.binding.environment_sha256
    )


async def _attachment_workspace_available(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV3,
    attached: ExpiredProviderAttachment, *, retry: bool, historical_binding: RecoveryBinding,
) -> bool:
    from yleum_api.services import project_cell_executor

    workspace_id = state.execution.control_workspace_id
    workspace = await session.get(
        ProjectCellWorkspace, workspace_id, with_for_update=True, populate_existing=True,
    )
    if (
        workspace is None or workspace.project_id != run.project_id
        or workspace.owner_id != run.user_id or workspace.deleted_at is not None
    ):
        return False
    if not await _attachment_release_matches(
        session, attached, workspace_id=workspace.id, historical_binding=historical_binding,
    ):
        return False
    if await session.scalar(select(or_(
        exists().where(
            ProjectCellOperation.workspace_id == workspace.id,
            ProjectCellOperation.status.in_(("pending", "waiting_capacity", "running",
                                            "indeterminate")),
        ),
        exists().where(
            ProjectCellActivityLease.workspace_id == workspace.id,
            ProjectCellActivityLease.state == "active",
        ),
    ))) is not False:
        return False
    if (
        workspace.generation_run_id is None
        and workspace.fencing_epoch == attached.release_fencing_epoch
        and workspace.state in {"ready", "stopped"}
    ):
        return not await session.scalar(select(exists().where(
            ProjectCellOperation.workspace_id == workspace.id,
            ProjectCellOperation.generation_run_id == run.id,
            ProjectCellOperation.kind == "ensure",
            ProjectCellOperation.idempotency_key.endswith(
                f":cycle:{attached.lease_cycle_seq + 1}", autoescape=True,
            ),
        )))
    # The completed Ensure reader is a separately delivered, GET-first kernel.
    # Until it is present a progressed workspace always stays fenced.
    completed_ensure = getattr(project_cell_executor, "verified_completed_attachment_ensure", None)
    return (
        retry and workspace.generation_run_id == run.id
        and workspace.fencing_epoch == attached.release_fencing_epoch + 1
        and workspace.state in {"ready", "provisioning"}
        and callable(completed_ensure)
        and await completed_ensure(
            session, run, state, workspace=workspace,
            expected_cycle=attached.lease_cycle_seq + 1,
        ) is not None
    )


async def _eligible_expired_provider_attachment(
    session: AsyncSession, run: GenerationRun, project: Project, state: RecoveryStateV2,
    attached: ExpiredProviderAttachment | None,
) -> ExpiredProviderAttachment | None:
    from yleum_api.services.generation.expired_provider_drain import (
        _other_provider_attempts_stopped,
        clock_sha256,
    )
    from yleum_api.services.generation.provider_attempts import (
        _expired_provider_frame,
        _matches_expiry_receipt,
    )
    from yleum_api.services.generation_deadline import is_restoration_adaptation

    scope = expired_provider_reattach_scope()
    execution, binding = state.execution, state.binding
    if (
        scope is None or run.id != scope.run_id or not isinstance(state, RecoveryStateV3)
        or run.execution_backend != "worker" or run.execution_started_at is None
        or not run.started_at or run.response_mode not in {"build", "edit"}
        or project.template != "max_miniapp" or is_restoration_adaptation(run)
        or binding is None or binding.source_sha256 != scope.source_sha256
        or state.context_ref is None or state.context_ref != execution.execution_context_ref
        or state.context_ref.sha256 != scope.context_sha256 or state.checkpoint is None
        or state.unresolved_operation_ids or not state.environment_rebind_supported
        or state.clock.stage != "repair" or not state.clock.repair_granted
        or state.clock.wait_dependency is not None or not state.clock.interval_open
        or clock_sha256(state) != scope.clock_sha256 or execution.budget.repair_calls != 1
        or execution.stage != "finalization" or execution.substep != "response_wait"
        or execution.physical_state != "released" or execution.response_sha256 is not None
        or execution.next_tool_index != 0 or execution.control_workspace_id is None
        or binding.workspace_id != str(execution.control_workspace_id)
        or execution.control_fencing_epoch != binding.fencing_epoch + 1
        or execution.release_operation_id is None
        or await session.scalar(text("SELECT clock_timestamp()")) < state.clock.segment_deadline
    ):
        return None
    retry = attached is not None
    if attached is None:
        if (state.phase != "needs_input" or state.next_check_at is not None
                or state.state_revision != scope.state_revision):
            return None
        attached = ExpiredProviderAttachment(
            version=1, run_id=run.id, attempt_id=scope.attempt_id,
            state_revision=state.state_revision, attempt_seq=state.attempt_seq,
            logical_step_seq=execution.logical_step_seq, binding=binding,
            context_ref=state.context_ref, checkpoint=state.checkpoint,
            release_operation_id=execution.release_operation_id,
            release_fencing_epoch=execution.control_fencing_epoch,
            lease_cycle_seq=execution.lease_cycle_seq, clock_sha256=scope.clock_sha256,
        )
    elif not attached.matches_scope(scope) or not attached.matches_claim(state):
        return None
    attempt = await _response_wait_attempt(session, state)
    if attempt is None or attempt.attempt_id != attached.attempt_id:
        return None
    frame = await _expired_provider_frame(
        session, attempt, current_binding=binding, max_attempt_seq=attached.attempt_seq,
    )
    if (
        frame is None or frame.root.effect_id != execution.provider_effect_id
        or not _matches_expiry_receipt(attempt, frame)
        or not await _other_provider_attempts_stopped(session, run, attached.attempt_id)
        or not await _attachment_workspace_available(
            session, run, state, attached, retry=retry, historical_binding=frame.source,
        )
    ):
        return None
    return attached


def _retired_compiler_cursor(state: RecoveryStateV2) -> bool:
    cursor = state.execution
    return (
        cursor.stage == "finalization" and cursor.physical_state == "active"
        and cursor.substep in {"checkpoint_ready", "proof_wait"}
    )


def _matches_retired_compiler_context(
    state: RecoveryStateV3, attached: ExpiredProviderAttachment, context: dict[str, Any],
) -> bool:
    from yleum_api.services.generation.recovery_pipeline import (
        _RETIRED_NATIVE_REPAIR_KEY,
        _RETIRED_NATIVE_REPAIR_STOP,
        PendingRole,
        SourceRepairEntry,
        _RetiredNativeRepair,
    )
    from yleum_api.services.generation.recovery_reconciliation import ToolEffectCursor

    cursor, claim = state.execution, state.continuation_claim
    if (
        cursor.budget.repair_calls != 1 or context.get("active_frame") is not None
        or context.get("active_tool") is not None
    ):
        return False
    if "native_outer" in context or claim is None:
        return False
    try:
        completed = context["completed"]
        if "retired_native_finalization_v1" in completed:
            return False
        raw = completed[_RETIRED_NATIVE_REPAIR_KEY]
        if type(raw) is not dict or type(raw.get("version")) is not int:
            return False
        receipt = _RetiredNativeRepair.model_validate_json(json.dumps(raw, allow_nan=False))
        repair = SourceRepairEntry.model_validate_json(
            json.dumps(completed["source_repair"], allow_nan=False),
        )
        if (
            receipt.attempt_id != attached.attempt_id or receipt.binding != state.binding
            or receipt.logical_step_seq != attached.logical_step_seq
            or receipt.checked_at.utcoffset() is None or repair.status != "complete"
            or repair.result is None or repair.result.get("done") is not False
            or repair.result.get("files") != {}
            or repair.result.get("stop_reason") != _RETIRED_NATIVE_REPAIR_STOP
        ):
            return False
        if cursor.substep == "checkpoint_ready":
            return cursor.pending_effect_id is None and "pending_role" not in completed
        role = PendingRole.model_validate_json(
            json.dumps(completed["pending_role"], allow_nan=False),
        )
        return (
            role.operation_id.int != 0
            and role.role in {"bootstrap", "fast_check"}
            and role.return_substep == "checkpoint_ready"
            and cursor.logical_step_seq > attached.logical_step_seq
            and cursor.pending_effect_id == ToolEffectCursor(
                claim, state.state_revision, cursor.logical_step_seq, f"role:{role.operation_id}",
            ).root_effect_id
        )
    except (KeyError, TypeError, ValueError):
        return False


async def _read_retired_attachment_context(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2,
    attachment: ExpiredProviderAttachment,
) -> dict[str, Any] | None:
    """Reprove the original sealed attempt and read the current retained context."""
    from yleum_api.services.generation.expired_provider_drain import (
        _other_provider_attempts_stopped,
    )
    from yleum_api.services.generation.provider_attempts import (
        _expired_provider_frame,
        _matches_expiry_receipt,
    )
    from yleum_api.services.generation_deadline import is_restoration_adaptation

    cursor, binding, claim = state.execution, state.binding, state.continuation_claim
    scope = expired_provider_reattach_scope()
    claim_delta = state.attempt_seq - attachment.attempt_seq
    step_delta = cursor.logical_step_seq - attachment.logical_step_seq
    if (
        scope is None or not attachment.matches_scope(scope)
        or not isinstance(state, RecoveryStateV3) or not _retired_compiler_cursor(state)
        or run.id != attachment.run_id or run.status != "running"
        or run.execution_backend != "worker" or not run.execution_started_at or not run.started_at
        or run.response_mode not in {"build", "edit"} or is_restoration_adaptation(run)
        or (state.identity.run_id, state.identity.project_id, state.identity.user_id)
        != (run.id, run.project_id, run.user_id)
        or state.phase not in {"working", "waiting_dependency"}
        or (state.phase == "waiting_dependency") != (state.next_check_at is not None)
        or claim is None or claim.run_id != run.id or claim.attempt_seq != state.attempt_seq
        or claim_delta <= 0 or state.state_revision - attachment.state_revision <= claim_delta
        or state.unresolved_operation_ids or cursor.provider_effect_id is not None
        or cursor.response_sha256 is not None or cursor.next_tool_index != 0
        or cursor.release_operation_id is not None or cursor.budget.repair_calls not in {1, 2}
        or cursor.lease_cycle_seq != attachment.lease_cycle_seq + 1
        or not 0 <= step_delta <= 2
        or state.clock.stage != "repair" or not state.clock.repair_granted
        or state.clock.wait_dependency is not None or not state.clock.interval_open
        or binding is None or cursor.control_workspace_id is None
        or str(cursor.control_workspace_id) != attachment.binding.workspace_id
        or cursor.control_fencing_epoch != attachment.release_fencing_epoch + 1
        or attachment.release_fencing_epoch != attachment.binding.fencing_epoch + 1
        or binding != attachment.binding.model_copy(update={
            "fencing_epoch": cursor.control_fencing_epoch,
            "environment_sha256": binding.environment_sha256,
        })
        or state.context_ref is None or state.context_ref != cursor.execution_context_ref
        or state.context_ref == attachment.context_ref or state.checkpoint is None
    ):
        return None
    attempt = await session.get(GenerationProviderAttempt, attachment.attempt_id)
    if (
        attempt is None or (
            attempt.run_id, attempt.project_id, attempt.user_id, attempt.prompt_hash,
        ) != (run.id, run.project_id, run.user_id, run.prompt_hash)
    ):
        return None
    frame = await _expired_provider_frame(
        session, attempt, current_binding=attachment.binding,
        max_attempt_seq=attachment.attempt_seq,
    )
    if (
        frame is None or frame.root.logical_step_seq != attachment.logical_step_seq
        or not _matches_expiry_receipt(attempt, frame)
        or not await _other_provider_attempts_stopped(session, run, attachment.attempt_id)
        or not await _attachment_release_matches(
            session, attachment, workspace_id=cursor.control_workspace_id,
            historical_binding=frame.source,
        )
    ):
        return None
    try:
        return await load_execution_context(state.identity, state.context_ref, binding=binding)
    except RecoveryContractError:
        return None


async def _retired_pre_wire_effects(
    session: AsyncSession, state: RecoveryStateV3, attachment: ExpiredProviderAttachment,
    witness: CompilerWitness, *, retired_at: datetime, window_started_at: datetime,
) -> bool:
    from dataclasses import replace

    from yleum_api.models.project_cell import ProjectCellProof, ProjectCellProofResult
    from yleum_api.services.generation.provider_attempts import _closed_role_binding
    from yleum_api.services.project_cell_proofs import proof_identity_from_model

    rows = (await session.scalars(
        select(GenerationRecoveryEffect).where(
            GenerationRecoveryEffect.run_id == state.identity.run_id,
            GenerationRecoveryEffect.logical_step_seq > attachment.logical_step_seq,
        ).order_by(GenerationRecoveryEffect.logical_step_seq).limit(3),
    )).all()
    if not 1 <= len(rows) <= 2:
        return False
    terminal = rows[-1]
    if (
        terminal.logical_step_seq != state.execution.logical_step_seq
        or terminal.operation_id != witness.operation_id
    ):
        return False
    for row in rows:
        compiler = row is terminal
        if (
            (row.project_id, row.user_id) != (state.identity.project_id, state.identity.user_id)
            or row.effect_ordinal != 0
            or not attachment.attempt_seq < row.attempt_seq <= state.attempt_seq
            or (not compiler and row.logical_step_seq != attachment.logical_step_seq + 1)
        ):
            return False
        # These commands are standalone role frames. Each row owns its logical
        # sequence and has no provider parent. Use the same owned role verifier.
        if await _closed_role_binding(
            session, row, row, attachment.binding,
            "fast_check" if compiler else "prepare",
            "fast_check" if compiler else "bootstrap",
        ) != state.binding:
            return False
    result = await session.get(ProjectCellProofResult, witness.receipt_id)
    if result is None or result.operation_id != witness.operation_id:
        return False
    proof = await session.get(ProjectCellProof, result.proof_id)
    activity = await session.get(ProjectCellActivityLease, witness.operation_id)
    if (
        proof is None or activity is None or activity.finished_at is None
        or result.dimension != "fast_check" or result.outcome != "green"
        or proof.proof_key != activity.proof_key
        or not retired_at <= activity.started_at <= activity.finished_at <= window_started_at
    ):
        return False
    physical = proof_identity_from_model(proof)
    return (
        replace(physical, workspace_revision="0" * 64).proof_key == witness.comparison_sha256
    )


def _matches_targeted_source_entry(
    raw: object, *, feedback: str, source_sha256: str, deadline: datetime | None = None,
) -> bool:
    from yleum_api.services.generation.recovery_pipeline import SourceRepairEntry
    from yleum_api.services.promotion_permit import workspace_revision_digest

    try:
        if type(raw) is not dict or type(raw.get("version")) is not int:
            return False
        entry = SourceRepairEntry.model_validate_json(json.dumps(raw, allow_nan=False))
        return (
            entry.targeted and (entry.status == "complete") == (entry.result is not None)
            and entry.edit_deadline is not None
            and (deadline is None or (entry.status == "active" and entry.edit_deadline <= deadline))
            and f"FINAL SOURCE CHECK FEEDBACK:\n{feedback}" in entry.task
            and workspace_revision_digest(entry.baseline) == source_sha256
        )
    except (TypeError, ValueError):
        return False


async def _validate_retired_pre_wire_context(
    session: AsyncSession, state: RecoveryStateV3, attachment: ExpiredProviderAttachment,
    context: dict[str, Any],
) -> bool:
    from yleum_api.services.generation.expired_native_finalization import (
        _KEY,
        _REPAIR_SECONDS,
        RetiredFinalizationPlan,
    )
    from yleum_api.services.generation.recovery_pipeline import (
        _RETIRED_NATIVE_REPAIR_KEY,
        FinalizationProgress,
        _RetiredNativeRepair,
    )
    from yleum_api.services.max_management_generation import (
        PARKED_THROW_DIAGNOSTIC_PREFIX,
        _editable_library_source,
    )

    cursor, clock, identity = state.execution, state.clock, state.identity
    if (
        cursor.substep != "checkpoint_ready" or cursor.pending_effect_id is not None
        or cursor.logical_step_seq <= attachment.logical_step_seq
        or context.get("active_frame") is not None or context.get("active_tool") is not None
        or "native_outer" in context
    ):
        return False
    try:
        completed = context["completed"]
        if "pending_role" in completed:
            return False
        raw = completed[_RETIRED_NATIVE_REPAIR_KEY]
        if type(raw) is not dict or type(raw.get("version")) is not int:
            return False
        retired = _RetiredNativeRepair.model_validate_json(json.dumps(raw, allow_nan=False))
        plan = RetiredFinalizationPlan.model_validate_json(
            json.dumps(completed[_KEY], allow_nan=False),
        )
        progress = FinalizationProgress.model_validate_json(
            json.dumps(completed["finalization_progress"], allow_nan=False),
        )
    except (KeyError, TypeError, ValueError):
        return False
    target, feedback, witness = plan.repair_path, plan.feedback, plan.proof
    if (
        retired.binding != state.binding or retired.logical_step_seq != attachment.logical_step_seq
        or retired.attempt_id != plan.attempt_id or plan.attempt_id != attachment.attempt_id
        or retired.checked_at.utcoffset() is None or plan.segment_seq != clock.segment_seq
        or not witness.passed or witness.dimension != "fast_check"
        or witness.binding != state.binding
        or (witness.run_id, witness.project_id, witness.user_id, witness.prompt_hash)
        != (identity.run_id, identity.project_id, identity.user_id, identity.prompt_hash)
    ):
        return False
    now = await session.scalar(text("SELECT clock_timestamp()"))
    if now is None:
        return False
    if target is None:
        return (
            cursor.budget.repair_calls == 1 and "source_repair" not in completed
            and feedback is None and plan.repair_deadline is None and plan.maximum_repairs == 0
            and progress.pending_detail is None and not progress.pending_targeted
            and not progress.pending_fast_check and not progress.no_progress
            and await _retired_pre_wire_effects(
                session, state, attachment, witness, retired_at=retired.checked_at,
                window_started_at=now,
            )
        )
    if (
        plan.segment_seq <= 0 or not _editable_library_source(target)
        or clock.window_ms != _REPAIR_SECONDS * 1000 or clock.segment_used_ms >= clock.window_ms
        or plan.repair_deadline != clock.segment_deadline
        or not feedback or not progress.feedback or progress.feedback[-1] != feedback
        or plan.maximum_repairs != len(progress.feedback) or progress.pending_detail != feedback
        or progress.pending_targeted or not progress.pending_fast_check or progress.no_progress
        or re.match(
            re.escape(PARKED_THROW_DIAGNOSTIC_PREFIX)
            + rf" Source location: {re.escape(target)}:[1-9][0-9]*:[1-9][0-9]*;"
            r" AST call_expression; reason unproven_json_stringify; fingerprint [a-f0-9]{16}\.",
            feedback,
        ) is None
    ):
        return False
    if cursor.budget.repair_calls == 1:
        if "source_repair" in completed:
            return False
    elif not _matches_targeted_source_entry(
        completed.get("source_repair"), feedback=feedback,
        source_sha256=witness.binding.source_sha256, deadline=clock.segment_deadline,
    ):
        return False
    window_started_at = clock.segment_deadline - timedelta(seconds=_REPAIR_SECONDS)
    return (
        window_started_at <= now < clock.segment_deadline
        and await _retired_pre_wire_effects(
            session, state, attachment, witness, retired_at=retired.checked_at,
            window_started_at=window_started_at,
        )
    )


async def validate_retired_active_attachment(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2,
    attachment: ExpiredProviderAttachment,
) -> bool:
    """Accept only the exact retired compiler or already-granted pre-wire cursor."""
    context = await _read_retired_attachment_context(session, run, state, attachment)
    if not isinstance(state, RecoveryStateV3) or context is None:
        return False
    completed = context.get("completed")
    if isinstance(completed, dict) and "retired_native_finalization_v1" in completed:
        return await _validate_retired_pre_wire_context(session, state, attachment, context)
    return _matches_retired_compiler_context(state, attachment, context)


async def retired_attachment_ordinary_handoff(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV3,
    attachment: ExpiredProviderAttachment,
) -> RetiredFinalizationPlan | None:
    """Return the authenticated predecessor plan for an ordinary continuation."""
    from yleum_api.services.generation.expired_native_finalization import (
        _KEY,
        RetiredFinalizationPlan,
    )
    from yleum_api.services.generation.provider_attempts import expired_reservation_closed
    from yleum_api.services.generation.recovery_pipeline import (
        _RETIRED_NATIVE_REPAIR_KEY,
        PendingRole,
        _RetiredNativeRepair,
    )
    from yleum_api.services.generation.recovery_reconciliation import ToolEffectCursor
    from yleum_api.services.max_management_generation import _editable_library_source

    cursor, binding, owner = state.execution, state.binding, state.identity
    workspace_id = cursor.control_workspace_id
    if (
        run.id != attachment.run_id or run.status != "running"
        or state.phase not in {
            "working", "waiting_dependency", "reconciling", "repairing", "verifying",
        }
        or (owner.run_id, owner.project_id, owner.user_id, owner.prompt_hash)
        != (run.id, run.project_id, run.user_id, run.prompt_hash)
        or state.continuation_claim is None or state.continuation_claim.run_id != run.id
        or state.continuation_claim.attempt_seq != state.attempt_seq
        or cursor.stage != "finalization" or cursor.budget.repair_calls not in {1, 2}
        or cursor.physical_state != "released" or cursor.release_operation_id is None
        or cursor.release_operation_id == attachment.release_operation_id
        or cursor.lease_cycle_seq < attachment.lease_cycle_seq + 1
        or state.context_ref is None or cursor.execution_context_ref != state.context_ref
        or workspace_id is None or binding is None or binding.workspace_id != str(workspace_id)
        or binding.workspace_id != attachment.binding.workspace_id
        or binding.fencing_epoch <= attachment.release_fencing_epoch
        or cursor.control_fencing_epoch not in (binding.fencing_epoch, binding.fencing_epoch + 1)
    ):
        return None
    release = await session.get(ProjectCellOperation, cursor.release_operation_id)
    if not _owned_release_receipt(
        release, run_id=run.id, workspace_id=workspace_id, fence=binding.fencing_epoch + 1,
    ):
        return None
    old_attempt = await session.get(GenerationProviderAttempt, attachment.attempt_id)
    if (
        old_attempt is None or (
            old_attempt.run_id, old_attempt.project_id, old_attempt.user_id,
            old_attempt.prompt_hash,
        ) != (run.id, run.project_id, run.user_id, run.prompt_hash)
        or not await expired_reservation_closed(session, old_attempt)
    ):
        return None
    try:
        context = await load_execution_context(owner, state.context_ref, binding=binding)
        completed = context["completed"]
        raw = completed[_RETIRED_NATIVE_REPAIR_KEY]
        if type(raw) is not dict or type(raw.get("version")) is not int:
            return None
        retired = _RetiredNativeRepair.model_validate_json(json.dumps(raw, allow_nan=False))
        plan = RetiredFinalizationPlan.model_validate_json(
            json.dumps(completed[_KEY], allow_nan=False),
        )
    except (KeyError, TypeError, ValueError):
        return None
    witness = plan.proof
    if (
        retired.attempt_id != attachment.attempt_id or plan.attempt_id != attachment.attempt_id
        or retired.logical_step_seq != attachment.logical_step_seq
        or not witness.passed or witness.dimension != "fast_check"
        or (witness.run_id, witness.project_id, witness.user_id, witness.prompt_hash)
        != (run.id, run.project_id, run.user_id, run.prompt_hash)
        or witness.binding != retired.binding
        or retired.binding != attachment.binding.model_copy(update={
            "fencing_epoch": attachment.release_fencing_epoch + 1,
            "environment_sha256": retired.binding.environment_sha256,
        })
        or state.clock.segment_seq < plan.segment_seq
    ):
        return None
    if plan.repair_path is None:
        if (
            cursor.budget.repair_calls != 1 or plan.feedback is not None
            or plan.repair_deadline is not None or plan.maximum_repairs != 0
            or plan.segment_seq != state.clock.segment_seq
            or cursor.provider_effect_id is not None
            or binding.source_sha256 != witness.binding.source_sha256
            or context.get("active_frame") is not None or context.get("active_tool") is not None
            or "native_outer" in context or "source_repair" in completed
        ):
            return None
        role = completed.get("pending_role")
        if cursor.substep == "checkpoint_ready":
            return plan if role is None and cursor.pending_effect_id is None else None
        if cursor.substep != "proof_wait" or role is None:
            return None
        try:
            pending = PendingRole.model_validate_json(json.dumps(role, allow_nan=False))
        except (TypeError, ValueError):
            return None
        role_effect = ToolEffectCursor(
            state.continuation_claim, state.state_revision, cursor.logical_step_seq,
            f"role:{pending.operation_id}", reconcile_only=True,
        )
        return plan if (
            pending.return_substep == "checkpoint_ready"
            and cursor.pending_effect_id == role_effect.root_effect_id
        ) else None
    if (
        cursor.budget.repair_calls != 2 or not _editable_library_source(plan.repair_path)
        or not plan.feedback or plan.segment_seq <= 0
        or plan.repair_deadline is None or plan.repair_deadline.utcoffset() != timedelta(0)
        or (state.clock.segment_seq == plan.segment_seq
            and state.clock.segment_deadline > plan.repair_deadline)
    ):
        return None
    # After the entry is committed, a cold worker follows the current cursor.
    # In response_wait, only an existing, owned new reservation can be read.
    if cursor.substep == "response_wait":
        attempt = await _response_wait_attempt(session, state)
        if (
            cursor.logical_step_seq > attachment.logical_step_seq
            and attempt is not None and attempt.attempt_id != attachment.attempt_id
        ):
            return plan
        return None
    if _matches_targeted_source_entry(
        completed.get("source_repair"),
        feedback=plan.feedback, source_sha256=witness.binding.source_sha256,
    ):
        return plan
    # Finalization consumes the entry after it records the new result. Require
    # that this run really has a subsequent owned provider intent before allowing
    # an ordinary proof/publication continuation with no remaining source entry.
    effect, provider_attempt = GenerationRecoveryEffect, GenerationProviderAttempt
    newer_attempt = await session.scalar(select(exists().where(
        effect.run_id == run.id, effect.project_id == run.project_id,
        effect.user_id == run.user_id, effect.kind == "provider",
        effect.logical_step_seq > attachment.logical_step_seq,
        effect.logical_step_seq <= cursor.logical_step_seq,
        effect.provider_attempt_id == provider_attempt.attempt_id,
        effect.input_digest == provider_attempt.request_sha256,
        provider_attempt.run_id == run.id, provider_attempt.project_id == run.project_id,
        provider_attempt.user_id == run.user_id, provider_attempt.prompt_hash == run.prompt_hash,
        provider_attempt.attempt_id != attachment.attempt_id,
    )))
    return plan if newer_attempt is True else None


async def _retired_compiler_ensure_available(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2,
    attachment: ExpiredProviderAttachment,
) -> bool:
    from yleum_api.services import project_cell_executor

    verify = getattr(project_cell_executor, "verified_completed_retired_compiler_ensure", None)
    if not callable(verify):
        return False
    workspace = await session.get(
        ProjectCellWorkspace, state.execution.control_workspace_id,
        with_for_update=True, populate_existing=True,
    )
    return workspace is not None and await verify(
        session, run, state, workspace=workspace, attachment=attachment,
    ) is not None


async def _validate_attachment_context(state: RecoveryStateV2) -> None:
    from yleum_api.services.generation.recovery_pipeline import SourceRepairEntry

    ref = state.context_ref
    if ref is None:
        raise RecoveryContractError("expired provider attachment has no executable context")
    context = await load_execution_context(state.identity, ref, binding=state.binding)
    frame = context.get("active_frame")
    try:
        repair = SourceRepairEntry.model_validate_json(json.dumps(
            context["completed"]["source_repair"], allow_nan=False,
        ))
    except (KeyError, TypeError, ValueError):
        raise RecoveryContractError("expired provider attachment has no active repair") from None
    if (
        type(frame) is not dict or set(frame) != {"version", "kind", "value"}
        or type(frame["version"]) is not int or frame["version"] != 1
        or frame["kind"] != "native" or type(frame["value"]) is not dict
        or context.get("active_tool") is not None or repair.status != "active"
        or repair.result is not None
    ):
        raise RecoveryContractError("expired provider attachment has no native repair frame")


async def claim_continuation(
    session: AsyncSession,
    *,
    run_id: UUID,
    expected_attempt: int,
    worker_id: str,
    now: datetime,
) -> ContinuationClaim | None:
    from yleum_api.services.generation.recovery_store import (
        _baseline_sha,
        _locked_owner,
        _validate_retained_checkpoint,
    )

    await _require_session_owner(run_id)
    if not await try_continuation_admission(session):
        return None
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
        or (isinstance(state, RecoveryStateV3) and state.clock.wait_dependency is not None)
        or (
            state.phase in {"waiting_dependency", "reconciling"}
            and (state.next_check_at is None or state.next_check_at > now)
        )
    ):
        return None
    attached = None
    existing_attachment = None
    retired_plan = None
    has_attachment = EXPIRED_PROVIDER_ATTACHMENT_KEY in run.agent_state
    retired_compiler = has_attachment and _retired_compiler_cursor(state)
    ordinary_handoff = has_attachment and (
        state.execution.stage == "finalization" and state.execution.physical_state == "released"
        and (
            state.execution.budget.repair_calls == 2
            or (
                state.execution.budget.repair_calls == 1
                and state.execution.substep in {"checkpoint_ready", "proof_wait"}
            )
        )
    )
    if state.execution.substep == "response_wait" or retired_compiler or ordinary_handoff:
        try:
            existing_attachment = load_expired_provider_attachment(run)
        except RecoveryContractError:
            return None
        if retired_compiler:
            if (
                existing_attachment is None or project.template != "max_miniapp"
                or not await _retired_compiler_ensure_available(
                    session, run, state, existing_attachment,
                )
            ):
                return None
            attached = existing_attachment
        elif ordinary_handoff:
            if existing_attachment is None or not isinstance(state, RecoveryStateV3):
                return None
            # A normally yielded run may still be waiting. Its claimed working
            # phase is granted below after exact normal release verification.
            retired_plan = await retired_attachment_ordinary_handoff(
                session, run, state, existing_attachment,
            )
            if retired_plan is None:
                return None
        elif state.phase == "needs_input" or (
            existing_attachment is not None
            and state.execution.logical_step_seq == existing_attachment.logical_step_seq
        ):
            attached = await _eligible_expired_provider_attachment(
                session, run, project, state, existing_attachment,
            )
            if attached is None:
                return None
    parked_repair = state.phase == "needs_input" and attached is None
    if parked_repair:
        from yleum_api.services.generation.parked_source_recovery import (
            PARKED_SOURCE_REDRIVE_KEY,
            parked_source_repair,
        )

        if not await parked_source_repair(session, run, state):
            return None
    elif attached is None and state.phase not in {
        "waiting_dependency", "reconciling", "working", "repairing", "verifying",
    }:
        return None
    if (attached is None and isinstance(state, RecoveryStateV3)
            and await _defer_open_response(session, run, state, now)):
        return None
    from yleum_api.services.generation.agent_publication import publication_committed
    published = await publication_committed(session, run=run, project=project, state=state)
    if attached is not None and published:
        return None
    if not published:
        baseline_sha = await _baseline_sha(session, run=run, project=project, state=state)
        if attached is not None:
            await _validate_retained_checkpoint(run, state, baseline_sha=baseline_sha)
            if not retired_compiler:
                await _validate_attachment_context(state)
    # A process can die before its WAIT acknowledgement. Working is resumable
    # only after the same physical quiescence proof below, never lease expiry.
    initial = state.execution.physical_state == "no_effects"
    if attached is None:
        quiescent = (
            await _initial_quiescent(session, run_id)
            if initial
            else await _released_quiescent(session, run, state)
        )
        if not quiescent:
            return None
    if isinstance(state, RecoveryStateV3):
        from yleum_api.services.generation.recovery_clock import resume_clock, start_interval
        if attached is None:
            max_deadline = (
                retired_plan.repair_deadline
                if retired_plan is not None and state.clock.stage == "repair"
                and state.clock.segment_seq == retired_plan.segment_seq else None
            )
            state = resume_clock(state, now=now, max_deadline=max_deadline)
        start_interval(state.clock, resume_existing=attached is not None)
    await _require_session_owner(run_id)
    execution = (
        state.execution
        if initial or (attached is not None and existing_attachment is not None)
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
    persisted = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
    if parked_repair:
        persisted[PARKED_SOURCE_REDRIVE_KEY] = state.state_revision
    if attached is not None:
        persisted[EXPIRED_PROVIDER_ATTACHMENT_KEY] = attached.model_dump(mode="json")
    run.agent_state = persisted
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
            from yleum_api.services.generation.completion_release import has_completion_release

            if has_completion_release(run):
                return "preserve"  # Release-only intent must never replay/fail the completed body.
            if not isinstance(run.agent_state, dict):
                return "preserve"
            state = recovery_state(run)
            return "preserve" if isinstance(state, RecoveryStateV2) else "legacy"
    except Exception:
        return "preserve"


async def require_initial_execution_owner(run_id: UUID) -> None:
    settings = get_settings()
    if not (
        settings.generation_provider_attempts_enabled
        and settings.generation_response_retention_enabled
    ):
        raise RecoveryContractError("executable recovery dependencies are disabled")
    await _require_session_owner(run_id)


async def _initial_cursor(
    session: AsyncSession, run: GenerationRun, ensure_operation_id: UUID | None,
) -> ExecutionCursor:
    if ensure_operation_id is None:
        return ExecutionCursor(
            stage="bootstrap", substep="runtime_prepare", physical_state="no_effects",
        )

    from yleum_api.services.orchestrator_client import (
        OrchestratorUnavailable,
        ProjectCellResourceResponse,
    )

    operation = await session.get(ProjectCellOperation, ensure_operation_id)
    if (
        operation is None or operation.generation_run_id != run.id or operation.kind != "ensure"
        or operation.status != "completed" or operation.finished_at is None
        or operation.fencing_epoch is None or not operation.result_payload
    ):
        raise RecoveryContractError("initial runtime ensure receipt is missing")
    try:
        receipt = ProjectCellResourceResponse.from_json(operation.result_payload)
    except OrchestratorUnavailable as exc:
        raise RecoveryContractError("initial runtime ensure receipt is invalid") from exc
    workspace = await session.get(
        ProjectCellWorkspace, operation.workspace_id, with_for_update=True, populate_existing=True,
    )
    if (
        workspace is None or workspace.owner_id != run.user_id
        or workspace.project_id != run.project_id
        or workspace.generation_run_id != run.id or workspace.deleted_at is not None
        or workspace.state != "ready" or workspace.fencing_epoch != operation.fencing_epoch
        or receipt.workspace_id != workspace.id or receipt.fencing_epoch != workspace.fencing_epoch
        or receipt.state != "resources_ready" or receipt.provider_ref is None
        or receipt.provider_ref != workspace.provider_ref
        or not all((
            receipt.has_workspace, receipt.has_agent_home, receipt.has_postgres, receipt.has_redis,
        ))
    ):
        raise RecoveryContractError("initial runtime lease does not match its ensure receipt")
    return ExecutionCursor(
        stage="bootstrap", substep="runtime_prepare", physical_state="active",
        control_workspace_id=workspace.id, control_fencing_epoch=workspace.fencing_epoch,
    )


async def initialize_execution(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    context: Callable[[], dict[str, Any]],
    ensure_operation_id: UUID | None = None,
) -> RecoveryStateV2 | None:
    """Persist the original executable entry before model-facing preparation.

    This creates V2, or explicit opt-in V3. An existing record must enter through
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
        await require_initial_execution_owner(run_id)
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
        clock_fields: dict[str, Any] = {}
        state_type: type[RecoveryStateV2] = RecoveryStateV2
        from yleum_api.services.generation_deadline import is_restoration_adaptation
        if (
            getattr(settings, "generation_recovery_soft_windows_enabled", False)
            and not is_restoration_adaptation(run)
        ):
            from yleum_api.services.generation.recovery_clock import initial_clock, start_interval
            state_type = RecoveryStateV3
            clock_fields["clock"] = initial_clock(settings.max_generation_deadline_seconds)
            start_interval(clock_fields["clock"])
        state = state_type(
            identity=identity,
            continuation_claim=claim,
            requirements=frozen_requirements(run),
            execution=await _initial_cursor(session, run, ensure_operation_id),
            **clock_fields,
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
        try:
            await session.commit()
        except Exception:
            if ensure_operation_id is None:
                raise
            await session.rollback()
            # A lost acknowledgement is safe only when this exact entry and
            # its still-owned physical lease can both be read back.
            await require_initial_execution_owner(run_id)
            async with factory() as observed:
                current, _ = await _locked_owner(
                    observed, run_id=run_id, project_id=state.identity.project_id,
                    user_id=state.identity.user_id,
                )
                if recovery_state(current) != state:
                    raise
                cursor = await _initial_cursor(observed, current, ensure_operation_id)
                if cursor != state.execution.model_copy(update={"execution_context_ref": None}):
                    raise
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
    phase: Literal["waiting_dependency", "needs_input"] = "waiting_dependency",
    refusal_redrive: GenerationProviderIdentity | None = None,
) -> RecoveryStateV2:
    """Retain the current committed cursor after an uncertain local write.

    No context upload or local data replacement: a lost acknowledgement may
    already have advanced both context and arithmetic in the same transaction.
    """
    from yleum_api.services.generation.recovery_store import _locked_owner

    await _require_session_owner(previous.identity.run_id, compatibility=False)
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
        if refusal_redrive is not None:
            if (
                phase != "waiting_dependency" or not isinstance(current, RecoveryStateV3)
                or current != previous or current.phase != "working"
                or current.clock.wait_dependency is not None
                or _preconnect_redrive_attempt(run) is not None
                or await _closed_native_preconnect_chain(session, current) != refusal_redrive
            ):
                raise RecoveryContractError("connection refusal redrive lost its exact owner")
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
        await _require_session_owner(run.id, compatibility=False)
        if current.phase == phase and execution == current.execution:
            return current  # Confirmed SQL read, not an inferred commit acknowledgement.
        wait_at = datetime.now(UTC) if phase == "waiting_dependency" else None
        clock_fields = {}
        if isinstance(current, RecoveryStateV3) and wait_at is not None:
            from yleum_api.services.generation.recovery_clock import next_dependency_check
            failures = current.clock.dependency_failures + 1
            wait_at = next_dependency_check(run.id, failures=failures, now=wait_at)
            if refusal_redrive is not None:
                wait_at = max(wait_at, datetime.now(UTC) + timedelta(seconds=60))
            clock_fields["clock"] = current.clock.model_copy(
                update={"dependency_failures": failures},
            )
        updated = current.model_copy(update={
            "phase": phase, "next_check_at": wait_at,
            "state_revision": current.state_revision + 1, "execution": execution,
            **clock_fields,
        })
        saved = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
        if refusal_redrive is not None:
            saved[PROVIDER_REFUSAL_REDRIVE_KEY] = {
                "version": 1, "attempt_id": str(refusal_redrive.attempt_id),
            }
        run.agent_state = saved
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
                    "repair_max_tokens",
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
    before_fresh_provider: Callable[[], Awaitable[None]] | None = None
    prepare_environment: Callable[[str, str], Awaitable[None]] | None = None
    prove_environment_recovery: (
        Callable[[UUID], Awaitable[ProjectCellEnvironmentRecoveryProof | None]] | None
    ) = None
    _reconcile_response: bool = field(init=False)
    _tool_position: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._reconcile_response = self.state.execution.substep in {
            "response_wait",
            "response_validated",
            "tool_ready",
            "tool_wait",
        }

    async def account_work(self, *, stopped: bool = False) -> None:
        if not isinstance(self.state, RecoveryStateV3):
            return
        from yleum_api.services.generation.recovery_clock import (
            charge_interval,
            interval_elapsed,
            update_owned_clock,
        )
        elapsed = interval_elapsed(self.state.clock)

        interval_id = self.state.clock.interval_id

        async def charge(session: AsyncSession, state: RecoveryStateV3) -> RecoveryStateV3:
            updated = charge_interval(state, interval_id=interval_id, elapsed_ms=elapsed)
            if stopped:
                run = await session.get(GenerationRun, state.identity.run_id)
                if run is None:
                    raise RecoveryContractError("clock owner disappeared")
                quiescent = (await _initial_quiescent(session, run.id)
                    if state.execution.physical_state == "no_effects"
                    else await _released_quiescent(session, run, state))
                from yleum_api.services.generation.recovery_quiescence import _provider_stopped
                if not quiescent or not await _provider_stopped(session, run):
                    raise RecoveryContractError("clock cannot pause an active physical writer")
                updated = updated.model_copy(update={
                    "clock": updated.clock.model_copy(update={"interval_open": False}),
                })
            return updated

        await update_owned_clock(self, charge, stopped=stopped)

    def soft_edit_deadline(self, fallback: datetime | None) -> datetime | None:
        if not isinstance(self.state, RecoveryStateV3):
            return fallback
        clock = self.state.clock
        if clock.stage == "proof":
            return datetime.now(UTC)
        from yleum_api.services.generation_deadline import source_edit_deadline
        return source_edit_deadline(
            clock.segment_deadline,
            started_at=clock.segment_deadline - timedelta(milliseconds=clock.window_ms),
        )

    async def soft_boundary(self, *, exhausted: bool = False) -> None:
        if not isinstance(self.state, RecoveryStateV3):
            return
        from yleum_api.services.generation.recovery_clock import grant_window, update_owned_clock
        await self.account_work()
        if not exhausted and datetime.now(UTC) < self.state.clock.segment_deadline:
            return
        try:
            if await grant_window(self):
                await self.wait()
        except RecoveryContractError:
            await self.wait()
        async def blocked(_session: AsyncSession, state: RecoveryStateV3) -> RecoveryStateV3:
            return state.model_copy(update={"phase": "needs_input", "next_check_at": None})
        await update_owned_clock(self, blocked)
        raise GenerationYielded(run_id=self.state.identity.run_id, checkpoint=self.state.checkpoint,
            attempt_seq=self.state.attempt_seq, phase="needs_input")

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
        expected_binding: RecoveryBinding | None = None,
    ) -> None:
        from yleum_api.services.generation.recovery_store import save_recovery_checkpoint

        binding, files = await self.snapshot()
        if expected_binding is not None and binding != expected_binding:
            raise RecoveryContractError("candidate changed after its release environment proof")
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

    async def wait(
        self, *, needs_input: bool = False,
        refusal_redrive: GenerationProviderIdentity | None = None,
        checkpoint_uncertain: bool = False,
    ) -> None:
        # The dependency itself may be unavailable. Retain the last confirmed
        # source/context and active/unknown physical status; no stopped inference.
        if not checkpoint_uncertain:
            await self.account_work()
        self.state = await commit_current_wait(
            self.factory, self.state, runtime_control=self.runtime_control,
            phase="needs_input" if needs_input else "waiting_dependency",
            refusal_redrive=refusal_redrive,
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

        await self.account_work()
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

    async def _confirm_content_filter(
        self, error: Exception, *, frame_kind: Literal["native", "legacy"],
    ) -> None:
        from yleum_api.services.llm_client import LLMError

        if (
            isinstance(self.state, RecoveryStateV3)
            and isinstance(error, LLMError) and error.code == "provider_output_filtered"
            and error.retry_safe is False
            and await _is_retained_content_filter(self.factory, self.state, frame_kind=frame_kind)
        ):
            # A paid, authenticated refusal can hand off the existing draft
            # for checks. Its body is never used as source.
            error.provider_charge_ambiguous = False
            error.finish_reason = "content_filter"

    async def _invoke_provider(
        self, cursor: ProviderEffectCursor, invoke: Callable[[], Awaitable[Any]],
        *, frame_kind: Literal["native", "legacy"],
    ) -> Any:
        from yleum_api.services.generation.recovery_diagnostic import (
            consume_diagnostic_response_timeout,
        )
        from yleum_api.services.generation.recovery_reconciliation import (
            ProviderEffectCursor,
            provider_effect_scope,
        )
        from yleum_api.services.llm_client import LLMError

        try:
            with provider_effect_scope(cursor):
                response = await invoke()
            if not cursor.reconcile_only and isinstance(self.state, RecoveryStateV3):
                diagnostic = await consume_diagnostic_response_timeout(self.factory, self.state)
                if diagnostic == "release":
                    await self.wait()
                if diagnostic == "replay":
                    raise TimeoutError("QA timeout after response retention")
            elif cursor.reconcile_only and not cursor.dispatch.resume_authorized:
                log.info("generation.provider_response_reconciled",
                         run_id=str(cursor.claim.run_id), logical_step_seq=cursor.logical_step_seq)
            return response
        except Exception as error:
            await self._confirm_content_filter(error, frame_kind=frame_kind)
            timeout_types = (TimeoutError, httpx.ReadTimeout)
            interrupted = (
                isinstance(error, timeout_types)
                or isinstance(error.__cause__, timeout_types)
                or (
                    isinstance(error, LLMError) and error.code == "transport"
                    and error.provider_charge_ambiguous is True and error.retry_safe is False
                )
            )
            if (
                cursor.reconcile_only or not isinstance(self.state, RecoveryStateV3)
                or not interrupted
                or not await _has_retained_completion(self.factory, self.state)
            ):
                raise
        # The first request has a closed, retained result. The usual signed
        # reader validates it again before returning bytes to the agent.
        replay = ProviderEffectCursor(
            cursor.claim, self.state.state_revision, cursor.logical_step_seq,
            reconcile_only=True, retained_only=True, outer_attempt=cursor.outer_attempt,
            outer_allowance=cursor.outer_allowance, call_deadline=cursor.call_deadline,
        )
        try:
            with provider_effect_scope(replay):
                response = await invoke()
        except LLMError as error:
            await self._confirm_content_filter(error, frame_kind=frame_kind)
            raise
        log.info(
            "generation.retained_provider_response_reused",
            run_id=str(replay.claim.run_id), logical_step_seq=replay.logical_step_seq,
        )
        return response

    async def _redrive_confirmed_preconnect(
        self, frame: dict[str, Any], *, reconcile: bool,
    ) -> None:
        from yleum_api.services.agent_output_budget import is_sonnet55_model
        from yleum_api.services.generation.recovery_store import _locked_owner
        from yleum_api.services.llm_client import (
            LLMError,
            ProviderChainExhausted,
            require_recovered_response,
        )

        state = self.state
        request = frame["value"].get("request")
        if (
            not isinstance(state, RecoveryStateV3) or frame["kind"] != "native"
            or not isinstance(request, dict) or not isinstance(request.get("model"), str)
            or not is_sonnet55_model(request["model"])
        ):
            return
        owner = state.identity
        await _require_session_owner(owner.run_id)
        async with self.factory() as session:
            run, _ = await _locked_owner(
                session, run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
            )
            if recovery_state(run) != state:
                raise RecoveryContractError("provider refusal redrive execution changed")
            armed = _preconnect_redrive_attempt(run)
            identity = await _closed_native_preconnect_chain(session, state)
        if identity is None or (armed is not None and armed != identity.attempt_id):
            return
        # Bind the exhausted envelope to the SQL leaf on both warm and cold
        # paths. Cold reconciliation may have switched to a proven child.
        try:
            await require_recovered_response(identity)
        except ProviderChainExhausted:
            pass
        except LLMError:
            return
        else:
            return
        if armed is None:
            await self.wait(refusal_redrive=identity)
        elif reconcile:
            # Keep the marker for the run. Only the original cursor can use it;
            # saving a different logical step makes the marker spent.
            self._reconcile_response = False
            raise NativeProviderRedrive()

    async def provider(
        self,
        frame: dict[str, Any],
        invoke: Callable[[], Awaitable[Any]],
    ) -> Any:
        from yleum_api.services.generation.recovery_reconciliation import (
            ProviderEffectCursor,
        )
        from yleum_api.services.llm_client import (
            LLMError,
            LLMOutputTruncated,
            ProviderChainExhausted,
        )

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
        if not reconcile:
            await self.soft_boundary()
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
            admission = self.before_fresh_provider
            if admission is not None:
                # Reconcile the retained paid response first. Only a genuinely
                # new request can initialize the current fenced platform core.
                await admission()
                # A durable role can renew the claim, advance the revision and
                # consume the remaining clock. Re-read all dispatch authority.
                await self.soft_boundary()
                claim = self.state.continuation_claim
                if claim is None:
                    raise RecoveryContractError("provider admission lost its current claim")
                outer_attempt, outer_allowance = _frame_retry(
                    frame, self.context.get("active_frame"), reconcile=False,
                )
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
            if admission is not None:
                self.before_fresh_provider = None
        try:
            response = await self._invoke_provider(cursor, invoke, frame_kind=frame["kind"])
        except LLMOutputTruncated:
            self._reconcile_response = False
            raise
        except LLMError as exc:
            if isinstance(exc, ProviderChainExhausted):
                await self._redrive_confirmed_preconnect(frame, reconcile=reconcile)
            if (
                isinstance(self.state, RecoveryStateV3)
                and exc.provider_refusal_category in {"access", "funds"}
            ):
                from yleum_api.services.generation.recovery_clock import update_owned_clock

                reason = "credentials" if exc.provider_refusal_category == "access" else "funds"

                async def dependency(
                    _session: AsyncSession, state: RecoveryStateV3,
                ) -> RecoveryStateV3:
                    return state.model_copy(update={"clock": state.clock.model_copy(update={
                        "wait_dependency": reason,
                    })})
                await update_owned_clock(self, dependency)
                await self.wait()
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
