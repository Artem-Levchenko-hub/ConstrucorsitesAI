"""Read-only evidence for a selected expired provider reservation at deployment."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services import generation_deployment_drain as drain
from yleum_api.services.generation.expired_provider_recovery import resumable_expired_repair
from yleum_api.services.generation.provider_attempts import (
    _expired_provider_frame,
    _matches_expiry_receipt,
    expired_reservation_closed,
)
from yleum_api.services.generation.recovery_contract import RecoveryContractError, RecoveryStateV3
from yleum_api.services.generation.recovery_execution import (
    _owned_release_receipt,
    _response_wait_attempt,
    recovery_state,
)
from yleum_api.services.generation.recovery_readers import require_recovery_readers
from yleum_api.services.generation_deadline import is_restoration_adaptation
from yleum_api.services.project_cells import ProjectCellValidationError, _stored_request_payload

_OLD_HEAD = "0106_native_transcript_refusal"
_NEW_HEAD = "0107_expired_provider_reservation"
Mode = Literal["eligibility", "settled"]


@dataclass(frozen=True, slots=True)
class ExpiredProviderScope:
    release_sha: str
    run_id: UUID
    attempt_id: UUID
    state_revision: int
    context_sha256: str
    source_sha256: str
    clock_sha256: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[0-9a-f]{40}", self.release_sha)
            or not isinstance(self.run_id, UUID) or not self.run_id.int
            or not isinstance(self.attempt_id, UUID) or not self.attempt_id.int
            or type(self.state_revision) is not int or self.state_revision < 0
            or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in (
                self.context_sha256, self.source_sha256, self.clock_sha256,
            ))
        ):
            raise ValueError("expired provider release scope is invalid")


@dataclass(frozen=True, slots=True)
class ExpiredProviderReport:
    mode: Mode
    active: dict[str, int]
    eligible: bool

    @property
    def sql_drained(self) -> bool:
        return self.mode == "settled" and self.eligible and not any(self.active.values())


def clock_sha256(state: RecoveryStateV3) -> str:
    data = json.dumps(state.clock.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


async def _other_provider_attempts_stopped(
    session: AsyncSession, run: GenerationRun, selected: UUID,
) -> bool:
    from yleum_api.services.generation.provider_attempts import native_transcript_attempt_closed

    attempt = GenerationProviderAttempt
    others = (await session.scalars(select(attempt).where(
        attempt.run_id == run.id, attempt.attempt_id != selected,
    ))).all()
    for row in others:
        if (
            row.phase != "closed" or (row.project_id, row.user_id, row.prompt_hash)
            != (run.project_id, run.user_id, run.prompt_hash)
        ):
            return False
        if row.provider_call_id is None:
            if not (
                await native_transcript_attempt_closed(session, row)
                or await expired_reservation_closed(session, row)
            ):
                return False
            continue
        call = await session.get(ProviderCall, row.provider_call_id)
        if (
            call is None or call.finished_at is None or call.status == "started"
            or (call.run_id, call.project_id, call.user_id)
            != (run.id, run.project_id, run.user_id)
            or await session.scalar(
                text("SELECT state FROM generation_responses WHERE attempt_id=:attempt"),
                {"attempt": row.attempt_id},
            ) not in {"complete", "deleted"}
        ):
            return False
    return True


async def _exact_reservation(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV3,
    scope: ExpiredProviderScope, *, mode: Mode, released_repair: bool = False,
) -> bool:
    attempt = await _response_wait_attempt(session, state)
    if (
        attempt is None or state.binding is None or attempt.attempt_id != scope.attempt_id
        or (attempt.run_id, attempt.project_id, attempt.user_id, attempt.prompt_hash)
        != (run.id, run.project_id, run.user_id, run.prompt_hash)
    ):
        return False
    frame = await _expired_provider_frame(
        session, attempt, current_binding=None if released_repair else state.binding,
        max_attempt_seq=state.attempt_seq,
    )
    if frame is None or frame.root.logical_step_seq != state.execution.logical_step_seq:
        return False
    if released_repair and (
        frame.source.workspace_id != state.binding.workspace_id
        or frame.source.source_sha256 != state.binding.source_sha256
        or frame.source.schema_sha256 != state.binding.schema_sha256
        or frame.source.fencing_epoch > state.binding.fencing_epoch
        or await session.scalar(text(
            "SELECT EXISTS(SELECT 1 FROM provider_calls WHERE run_id=:run AND created_at>=:created)"
        ), {"run": run.id, "created": attempt.created_at}) is not False
    ):
        return False
    if mode == "eligibility":
        return (
            attempt.phase == "reserved" and attempt.terminal_reason is None
            and (frame.root.state, frame.wire.state) == ("prepared", "inflight")
            and frame.root.receipt_ref is None and frame.wire.receipt_ref is None
        )
    return _matches_expiry_receipt(attempt, frame)


async def _selected_run(
    session: AsyncSession, scope: ExpiredProviderScope, *, mode: Mode,
) -> bool:
    run = await session.get(GenerationRun, scope.run_id)
    if (
        run is None or run.status != "running" or run.execution_backend != "worker"
        or run.response_mode not in {"build", "edit"} or is_restoration_adaptation(run)
    ):
        return False
    state = recovery_state(run)
    if not isinstance(state, RecoveryStateV3):
        return False
    released_repair = (
        state.phase == "waiting_dependency" and state.next_check_at is not None
        and resumable_expired_repair(run, state)
    )
    parked_repair = (
        state.phase == "needs_input" and state.next_check_at is None and state.clock.interval_open
    )
    if (
        not (parked_repair or released_repair) or state.unresolved_operation_ids
        or state.state_revision != scope.state_revision or state.binding is None
        or state.binding.source_sha256 != scope.source_sha256
        or state.checkpoint is None or state.context_ref is None
        or state.context_ref.sha256 != scope.context_sha256
        or state.context_ref != state.execution.execution_context_ref
        or clock_sha256(state) != scope.clock_sha256
        or state.clock.wait_dependency is not None
        or await session.scalar(text("SELECT clock_timestamp()")) < state.clock.segment_deadline
    ):
        return False
    project = await session.get(Project, run.project_id)
    execution, binding = state.execution, state.binding
    if (
        project is None or project.owner_id != run.user_id or project.template != "max_miniapp"
        or project.current_snapshot_id != state.identity.accepted_snapshot_id
        or execution.stage != "finalization" or execution.substep != "response_wait"
        or execution.physical_state != "released" or execution.release_operation_id is None
        or execution.control_workspace_id is None
        or binding.workspace_id != str(execution.control_workspace_id)
        or execution.control_fencing_epoch != binding.fencing_epoch + 1
        or await session.scalar(text(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND classid=1195724366 AND objid=:key AND objsubid=2 AND granted)"
        ), {"key": int.from_bytes(run.id.bytes[-4:], "big")})
    ):
        return False
    workspace = await session.get(ProjectCellWorkspace, execution.control_workspace_id)
    release = await session.get(ProjectCellOperation, execution.release_operation_id)
    if released_repair:
        try:
            if release is None or _stored_request_payload(release) != {
                "require_environment_proof": {
                    "source_sha256": binding.source_sha256,
                    "environment_sha256": binding.environment_sha256,
                },
            }:
                return False
        except ProjectCellValidationError:
            return False
    return (
        workspace is not None and workspace.project_id == run.project_id
        and workspace.owner_id == run.user_id and workspace.deleted_at is None
        and workspace.generation_run_id is None
        and workspace.fencing_epoch == execution.control_fencing_epoch
        and _owned_release_receipt(
            release, run_id=run.id, workspace_id=workspace.id, fence=workspace.fencing_epoch,
        )
        and await _exact_reservation(
            session, run, state, scope, mode=mode, released_repair=released_repair,
        )
        and await _other_provider_attempts_stopped(session, run, scope.attempt_id)
    )


async def classify_expired_provider(
    session: AsyncSession, scope: ExpiredProviderScope, *, mode: Mode,
    reader_release_sha: str | None = None,
) -> ExpiredProviderReport:
    """Preserve the global drain counts until the exact Gateway receipt exists.

    The caller must independently verify ingress, incumbent Gateway and both
    controller publication/physical gates. This check cannot authorize a write.
    """
    if mode not in {"eligibility", "settled"} or (
        reader_release_sha is not None and not re.fullmatch(r"[0-9a-f]{40}", reader_release_sha)
    ):
        raise ValueError("expired provider classification mode is invalid")
    await session.execute(text("SET TRANSACTION READ ONLY"))
    if await drain._locked_drain(session, scope.release_sha) is None:
        raise ValueError("expired provider classification requires its existing drain")
    return await _classify_locked_expired_provider(
        session, scope, mode=mode, reader_release_sha=reader_release_sha,
    )


async def _classify_locked_expired_provider(
    session: AsyncSession, scope: ExpiredProviderScope, *, mode: Mode,
    reader_release_sha: str | None, captured: bool = False,
) -> ExpiredProviderReport:
    """Require the exact drain lock; only an externally verified owned retry uses captures."""
    if captured and (mode != "settled" or reader_release_sha is not None):
        raise ValueError("owned capture is for settled retry only")
    counts = await drain._active_blockers(session)
    if counts != {"generations": 1, "operations": 0, "leases": 0, "restorations": 0}:
        return ExpiredProviderReport(mode, counts, False)
    heads = (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
    expected = ([_OLD_HEAD], [_NEW_HEAD]) if mode == "eligibility" else ([_NEW_HEAD],)
    if heads not in expected:
        return ExpiredProviderReport(mode, counts, False)
    try:
        if not captured:
            await require_recovery_readers(release_sha=reader_release_sha, own=False)
        if not await _selected_run(session, scope, mode=mode):
            return ExpiredProviderReport(mode, counts, False)
        if not captured:
            await require_recovery_readers(release_sha=reader_release_sha, own=False)
    except (RecoveryContractError, KeyError, TypeError, ValueError):
        return ExpiredProviderReport(mode, counts, False)
    if mode == "settled":
        counts["generations"] -= 1
    return ExpiredProviderReport(mode, counts, True)
