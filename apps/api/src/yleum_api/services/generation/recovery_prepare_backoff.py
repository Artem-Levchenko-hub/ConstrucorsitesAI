"""Defer an owned cold prepare failure when durable evidence still proves idle."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation.provider_attempts import (
    native_transcript_attempt_closed,
    require_closed_provider_parent,
)
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
from yleum_api.services.generation.recovery_clock import (
    charge_interval,
    interval_elapsed,
    next_dependency_check,
)
from yleum_api.services.generation.recovery_contract import ContinuationClaim, RecoveryStateV3
from yleum_api.services.generation.recovery_quiescence import _provider_stopped
from yleum_api.services.generation.recovery_readers import digest
from yleum_api.services.generation.recovery_reconciliation import (
    ProviderWireReservation,
    provider_root_effect_id,
)
from yleum_api.services.generation.recovery_store import _locked_owner
from yleum_api.services.orchestrator_client import OrchestratorUnavailable
from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError
from yleum_api.services.project_cells import lock_workspace_operations

log = logging.getLogger(__name__)


PREPARE_BACKOFF_KEY = "recovery_prepare_backoff_v1"
_RETAINED_PROVIDER_REASONS = frozenset(
    {"completed", "preconnect", "rate_limit_refused", "upstream_refused"}
)


def _release_scope_matches(state: RecoveryStateV3, workspace: ProjectCellWorkspace) -> bool:
    binding = state.binding
    cursor = state.execution
    return (
        binding is not None
        and binding.workspace_id == str(workspace.id)
        and binding.fencing_epoch + 1 == workspace.fencing_epoch
        and cursor.control_workspace_id == workspace.id
        and cursor.control_fencing_epoch == workspace.fencing_epoch
        and cursor.physical_state == "released"
        and state.checkpoint is not None
        and state.context_ref is not None
        and cursor.execution_context_ref is not None
        and workspace.state == "ready"
        and state.environment_rebind_supported
    )


async def _closed_provider_history(
    session: AsyncSession,
    run: GenerationRun,
    state: RecoveryStateV3,
    started_at: datetime,
) -> bool:
    effect, attempt = GenerationRecoveryEffect, GenerationProviderAttempt
    pending = (
        await session.scalars(
            select(effect).where(
                effect.run_id == run.id,
                or_(
                    effect.attempt_seq >= state.attempt_seq,
                    effect.state.in_(("prepared", "inflight", "unknown")),
                ),
            )
        )
    ).all()
    if not pending:
        return True
    if any(
        row.attempt_seq >= state.attempt_seq
        or row.state == "unknown"
        or row.kind not in {"provider", "progress"}
        or row.payload_version != 1
        or row.operation_id is not None
        or row.tool_call_id is not None
        or (row.project_id, row.user_id) != (run.project_id, run.user_id)
        or row.logical_step_seq > state.execution.logical_step_seq
        for row in pending
    ):
        return False
    links = (
        await session.execute(
            select(effect, attempt)
            .join(attempt, attempt.attempt_id == effect.provider_attempt_id)
            .where(
                effect.run_id == run.id,
                effect.kind == "provider",
                effect.project_id == run.project_id,
                effect.user_id == run.user_id,
                effect.payload_version == 1,
                effect.state != "unknown",
                effect.attempt_seq < state.attempt_seq,
                attempt.version == 1,
                attempt.run_id == run.id,
                attempt.project_id == run.project_id,
                attempt.user_id == run.user_id,
                attempt.prompt_hash == run.prompt_hash,
                attempt.request_sha256 == effect.input_digest,
                attempt.phase == "closed",
                attempt.updated_at <= started_at,
            )
        )
    ).all()
    # The gateway installed these matching references when it closed the attempt.
    # Keep the independent receiver/quiescence check in the caller as well.
    retained_ids = set(
        await session.scalars(
            text(
                "SELECT r.attempt_id FROM generation_responses r "
                "JOIN generation_provider_attempts a ON a.attempt_id=r.attempt_id "
                "WHERE a.run_id=:run AND r.state='complete' AND NOT r.reconciliation_hold "
                "AND r.manifest_key=a.response_manifest_key AND r.response_sha256=a.response_sha256"
            ),
            {"run": run.id},
        )
    )
    providers = {}
    for row, bound in links:
        paid = (
            bound.attempt_id in retained_ids
            and bound.terminal_reason in _RETAINED_PROVIDER_REASONS
            and bound.provider_call_id is not None
        )
        if paid or await native_transcript_attempt_closed(session, bound):
            providers[row.effect_id] = (row, bound)

    proven_children: set[UUID] = set()
    for row in pending:
        link_id = row.effect_id if row.kind == "provider" else row.parent_effect_id
        if link_id is None or link_id not in providers:
            return False
        provider, bound = providers[link_id]
        root = provider_root_effect_id(run.id, provider.logical_step_seq)
        if provider.parent_effect_id is None:
            if provider.effect_id != root or bound.parent_attempt_id is not None:
                return False
        elif provider.effect_id not in proven_children:
            parent = providers.get(provider.parent_effect_id)
            if (
                parent is None
                or parent[1].attempt_id != bound.parent_attempt_id
                or parent[0].logical_step_seq != provider.logical_step_seq
                or parent[0].input_digest != provider.input_digest
                or provider.effect_id != uuid5(parent[0].effect_id, "definitive-refusal-child-v1")
            ):
                return False
            try:
                identity = GenerationProviderIdentity(
                    run.id,
                    run.project_id,
                    run.user_id,
                    bound.attempt_id,
                    run.prompt_hash,
                    bound.request_sha256,
                )
                await require_closed_provider_parent(
                    session,
                    replace(identity, attempt_id=parent[1].attempt_id),
                    identity,
                )
            except ValueError:
                return False
            proven_children.add(provider.effect_id)
        if row.kind == "provider":
            continue
        if (
            type(row.pre_binding) is not dict
            or set(row.pre_binding) != {"dispatch_v1"}
            or row.provider_attempt_id is not None
            or row.logical_step_seq != provider.logical_step_seq
            or row.input_digest != provider.input_digest
            or row.effect_ordinal <= provider.effect_ordinal
            or row.attempt_seq < provider.attempt_seq
            or (row.attempt_seq == provider.attempt_seq and row.claim_token != provider.claim_token)
        ):
            return False
        try:
            wire = ProviderWireReservation.model_validate_json(
                json.dumps(row.pre_binding["dispatch_v1"]),
                strict=True,
            )
        except (TypeError, ValueError):
            return False
        if (
            wire.root_effect_id != root
            or wire.provider_effect_id != provider.effect_id
            or wire.attempt_id != bound.attempt_id
            or wire.parent_attempt_id != bound.parent_attempt_id
            or wire.request_sha256 != bound.request_sha256
            or row.effect_id != uuid5(root, f"wire-v1:{wire.outer_attempt}:{wire.wire_attempt}")
        ):
            return False
    return True


async def _released_prepare_idle(
    session: AsyncSession,
    run: GenerationRun,
    state: RecoveryStateV3,
    workspace: ProjectCellWorkspace,
    started_at: datetime,
) -> bool:
    # The workspace and terminal release are verified by the caller. Reject
    # even a finished writer or paid call started after this prepare claim.
    operation = ProjectCellOperation
    lease = ProjectCellActivityLease
    new_or_unresolved = await session.scalar(
        select(
            or_(
                exists().where(
                    or_(
                        operation.workspace_id == workspace.id,
                        operation.generation_run_id == run.id,
                        operation.execution_run_id == run.id,
                    ),
                    or_(
                        operation.status.not_in(("completed", "failed", "cancelled")),
                        operation.started_at >= started_at,
                    ),
                ),
                exists().where(
                    or_(lease.workspace_id == workspace.id, lease.generation_run_id == run.id),
                    or_(lease.finished_at.is_(None), lease.started_at >= started_at),
                ),
                exists().where(
                    ProviderCall.run_id == run.id,
                    or_(
                        ProviderCall.status == "started",
                        ProviderCall.finished_at.is_(None),
                        ProviderCall.created_at >= started_at,
                    ),
                ),
            )
        )
    )
    return new_or_unresolved is False and await _closed_provider_history(
        session,
        run,
        state,
        started_at,
    )


async def _prepare_still_idle(
    session: AsyncSession,
    run: GenerationRun,
    state: RecoveryStateV3,
    started_at: datetime,
) -> bool:
    cursor = state.execution
    if state.unresolved_operation_ids:
        return False
    if cursor.physical_state == "no_effects":
        return await execution._initial_quiescent(session, run.id) and not await session.scalar(
            select(exists().where(ProviderCall.run_id == run.id))
        )
    if cursor.control_workspace_id is None or cursor.release_operation_id is None:
        return False
    workspace = await session.get(
        ProjectCellWorkspace,
        cursor.control_workspace_id,
        with_for_update=True,
    )
    if (
        workspace is None
        or workspace.generation_run_id is not None
        or workspace.project_id != run.project_id
        or workspace.owner_id != run.user_id
        or workspace.deleted_at is not None
        or not _release_scope_matches(state, workspace)
    ):
        return False
    release = await session.get(ProjectCellOperation, cursor.release_operation_id)
    return (
        execution._owned_release_receipt(
            release,
            run_id=run.id,
            workspace_id=workspace.id,
            fence=workspace.fencing_epoch,
        )
        and await _released_prepare_idle(
            session,
            run,
            state,
            workspace,
            started_at,
        )
        and await _provider_stopped(session, run)
    )


def _prepare_backoff_marker(state: RecoveryStateV3, started_at: str) -> dict[str, str | int]:
    return {
        "version": 1,
        "started_at": started_at,
        "wait_sha256": digest([started_at, state.model_dump(mode="json")]),
    }


async def matches_released_prepare_backoff(
    session: AsyncSession,
    run: GenerationRun,
    state: RecoveryStateV3,
    workspace: ProjectCellWorkspace,
) -> bool:
    """Verify the exact wait and newer effects. The caller owns release and receiver checks."""
    marker = run.agent_state.get(PREPARE_BACKOFF_KEY)
    claim = state.continuation_claim
    if (
        type(marker) is not dict
        or set(marker) != {"version", "started_at", "wait_sha256"}
        or type(marker["version"]) is not int
        or marker["version"] != 1
        or type(marker["started_at"]) is not str
        or len(marker["started_at"]) > 32
        or type(marker["wait_sha256"]) is not str
        or len(marker["wait_sha256"]) != 64
        or run.execution_backend != "worker"
        or claim is None
        or claim.run_id != run.id
        or claim.attempt_seq != state.attempt_seq
        or claim.worker_id != "generation-worker"
        or state.phase != "waiting_dependency"
        or state.next_check_at is None
        or state.clock.interval_open
        or state.clock.wait_dependency is not None
        or state.clock.dependency_failures < 1
        or state.unresolved_operation_ids
        or not _release_scope_matches(state, workspace)
    ):
        return False
    timestamp = marker["started_at"]
    try:
        started_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if (
        started_at.utcoffset() is None
        or started_at.astimezone(UTC).isoformat() != timestamp
        or started_at >= min(claim.expires_at, state.next_check_at)
        or marker != _prepare_backoff_marker(state, timestamp)
    ):
        return False
    return await _released_prepare_idle(session, run, state, workspace, started_at)


async def _defer_unchanged_prepare(
    factory: async_sessionmaker[AsyncSession],
    before: RecoveryStateV3,
    started_at: datetime,
) -> None:
    owner = before.identity
    await execution._require_session_owner(owner.run_id, compatibility=False)
    async with factory() as session:
        await lock_workspace_operations(
            session,
            before.execution.control_workspace_id or owner.project_id,
        )
        run, project = await _locked_owner(
            session,
            run_id=owner.run_id,
            project_id=owner.project_id,
            user_id=owner.user_id,
        )
        current = execution.recovery_state(run)
        if (
            current != before
            or project.template != "max_miniapp"
            or run.execution_backend != "worker"
            or not await _prepare_still_idle(session, run, before, started_at)
        ):
            return
        updated = charge_interval(
            before,
            interval_id=before.clock.interval_id,
            elapsed_ms=interval_elapsed(before.clock),
        )
        failures = updated.clock.dependency_failures + 1
        updated = updated.model_copy(
            update={
                "state_revision": before.state_revision + 1,
                "phase": "waiting_dependency",
                "next_check_at": next_dependency_check(
                    run.id, failures=failures, now=datetime.now(UTC)
                ),
                "clock": updated.clock.model_copy(
                    update={
                        "interval_open": False,
                        "dependency_failures": failures,
                    }
                ),
            }
        )
        await execution._require_session_owner(owner.run_id, compatibility=False)
        persisted = {
            **run.agent_state,
            execution.RECOVERY_KEY: updated.model_dump(mode="json"),
        }
        if before.execution.physical_state == "released":
            persisted[PREPARE_BACKOFF_KEY] = _prepare_backoff_marker(
                updated, started_at.isoformat()
            )
        run.agent_state = persisted
        await session.commit()


async def resume_with_prepare_backoff(
    factory: async_sessionmaker[AsyncSession],
    claim: ContinuationClaim,
    before: RecoveryStateV3 | None,
    *,
    started_at: datetime | None,
) -> None:
    try:
        await execution.resume_generation(claim)
    except (TimeoutError, OrchestratorUnavailable, ProjectCellInfrastructureError):
        if (
            before is not None
            and started_at is not None
            and before.continuation_claim == claim
            and before.phase == "working"
            and before.clock.interval_open
            and before.clock.wait_dependency is None
            and (
                before.execution.physical_state == "no_effects"
                or before.environment_rebind_supported
            )
        ):
            try:
                await _defer_unchanged_prepare(factory, before, started_at)
            except Exception as error:
                # Preserve the original outcome. A failed proof or SQL write
                # cannot authorize a replacement cursor or terminal failure.
                log.warning("Could not confirm recovery prepare backoff (%s)", type(error).__name__)
        raise
