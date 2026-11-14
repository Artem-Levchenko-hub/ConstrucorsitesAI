"""Soft windows: arithmetic survives restart; proof authority never does."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.services.generation.recovery_contract import (
    RecoveryClock,
    RecoveryContractError,
    RecoveryState,
    RecoveryStateV3,
)

if TYPE_CHECKING:
    from yleum_api.services.generation.recovery_execution import ExecutionDriver

_INTERVAL: ContextVar[tuple[UUID, float] | None] = ContextVar(
    "recovery_active_interval", default=None
)


def start_interval(clock: RecoveryClock) -> None:
    _INTERVAL.set((clock.interval_id, time.monotonic()))


def interval_elapsed(clock: RecoveryClock) -> int:
    anchor = _INTERVAL.get()
    if anchor is None or anchor[0] != clock.interval_id:
        raise RecoveryContractError("active interval has no process clock origin")
    return max(clock.interval_charged_ms, int(max(0, time.monotonic() - anchor[1]) * 1000))


def contract_deadline(
    continuation: ExecutionDriver | None, fallback: datetime | None
) -> str | None:
    """Keep immutable loop identity separate from the current V3 work window."""
    state = getattr(continuation, "state", None)
    deadline = (
        state.execution.budget.absolute_deadline if isinstance(state, RecoveryStateV3) else fallback
    )
    return deadline.isoformat() if deadline is not None else None


def next_dependency_check(run_id: UUID, *, failures: int, now: datetime) -> datetime:
    if type(failures) is not int or failures < 1 or now.utcoffset() is None:
        raise RecoveryContractError("invalid dependency schedule")
    base = (15, 30, 60, 120, 300)[min(failures - 1, 4)]
    fraction = (
        int.from_bytes(hashlib.sha256(f"{run_id}:{failures}".encode()).digest()[:2], "big") / 65535
    )
    return now + timedelta(seconds=base * (1 + fraction / 10))


def account_active_work(state: RecoveryState, *, elapsed_ms: int) -> RecoveryState:
    if type(elapsed_ms) is not int or elapsed_ms < 0 or not isinstance(state, RecoveryStateV3):
        raise RecoveryContractError("active work requires a V3 nonnegative interval")
    counters = dict(state.stage_active_work_ms)
    counters[state.clock.stage] = counters.get(state.clock.stage, 0) + elapsed_ms
    return state.model_copy(
        update={
            "active_work_ms": state.active_work_ms + elapsed_ms,
            "stage_active_work_ms": counters,
            "clock": state.clock.model_copy(
                update={
                    "segment_used_ms": state.clock.segment_used_ms + elapsed_ms,
                }
            ),
        }
    )


def charge_interval(
    state: RecoveryStateV3, *, interval_id: UUID, elapsed_ms: int
) -> RecoveryStateV3:
    if type(elapsed_ms) is not int or elapsed_ms < 0 or interval_id != state.clock.interval_id:
        raise RecoveryContractError("work interval ownership changed")
    if elapsed_ms < state.clock.interval_charged_ms:
        raise RecoveryContractError("work interval cannot move backwards")
    delta = elapsed_ms - state.clock.interval_charged_ms
    updated = account_active_work(state, elapsed_ms=delta)
    assert isinstance(updated, RecoveryStateV3)
    return updated.model_copy(
        update={
            "clock": updated.clock.model_copy(
                update={
                    "interval_charged_ms": elapsed_ms,
                }
            )
        }
    )


def resume_clock(state: RecoveryStateV3, *, now: datetime) -> RecoveryStateV3:
    if now.utcoffset() is None:
        raise RecoveryContractError("work clock resume requires absolute time")
    clock = state.clock
    # Open means the old process never proved stopped/charged its last interval.
    used = (
        max(clock.segment_used_ms, clock.window_ms)
        if clock.interval_open
        else clock.segment_used_ms
    )
    return state.model_copy(
        update={
            "clock": clock.model_copy(
                update={
                    "segment_used_ms": used,
                    "interval_id": uuid4(),
                    "interval_open": True,
                    "interval_charged_ms": 0,
                    "segment_deadline": now
                    + timedelta(milliseconds=max(0, clock.window_ms - used)),
                }
            )
        }
    )


def initial_clock(seconds: int, *, now: datetime | None = None) -> RecoveryClock:
    now = now or datetime.now(UTC)
    return RecoveryClock(
        window_ms=seconds * 1000,
        segment_deadline=now + timedelta(seconds=seconds),
        interval_id=uuid4(),
    )


async def update_owned_clock(
    driver: ExecutionDriver,
    transform: Callable[[AsyncSession, RecoveryStateV3], Awaitable[RecoveryStateV3]],
    *,
    stopped: bool = False,
) -> None:
    """One owner/CAS transaction for time and grants; an unknown ACK never grants work."""
    from yleum_api.services.generation.recovery_execution import (
        RECOVERY_KEY,
        _require_session_owner,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_store import _locked_owner
    from yleum_api.services.generation_deployment_drain import require_generation_admission

    previous = driver.state
    if not isinstance(previous, RecoveryStateV3):
        return
    await _require_session_owner(previous.identity.run_id)
    async with driver.factory() as session:
        await require_generation_admission(session)
        run, _ = await _locked_owner(
            session,
            run_id=previous.identity.run_id,
            project_id=previous.identity.project_id,
            user_id=previous.identity.user_id,
        )
        current = recovery_state(run)
        if not isinstance(current, RecoveryStateV3) or current.continuation_claim is None:
            raise RecoveryContractError("clock execution owner changed")
        same_released_owner = stopped and (
            current.identity == previous.identity
            and current.clock == previous.clock
            and current.continuation_claim == previous.continuation_claim
            and current.attempt_seq == previous.attempt_seq
            and current.execution.physical_state == "released"
            and current.execution.budget == previous.execution.budget
            and current.context_ref == previous.context_ref
            and current.binding == previous.binding
            and current.active_work_ms == previous.active_work_ms
            and current.stage_active_work_ms == previous.stage_active_work_ms
        )
        if current != previous and not same_released_owner:
            raise RecoveryContractError("clock execution owner changed")
        updated = await transform(session, current)
        await _require_session_owner(run.id)
        updated = updated.model_copy(update={"state_revision": current.state_revision + 1})
        run.agent_state = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
        await session.commit()
    driver.state = updated


async def _current_compiler_progress(session: AsyncSession, state: RecoveryStateV3) -> bool:
    """Verify a source-changing RED to GREEN from this window's command journal."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.models.project_cell import (
        ProjectCellActivityLease,
        ProjectCellProof,
        ProjectCellProofResult,
    )
    from yleum_api.services.generation.native_strategy import compiler_failure
    from yleum_api.services.generation.recovery_requirements import load_compiler_witness
    from yleum_api.services.generation.source_error_classification import (
        source_check_is_repairable,
        source_diagnostic_has_infrastructure_error,
    )
    from yleum_api.services.generation_deadline import (
        is_restoration_adaptation,
        source_edit_deadline,
    )
    from yleum_api.services.project_cell_proofs import ProofDimension, proof_identity_from_model

    clock, binding = state.clock, state.binding
    if clock.compiler_granted or clock.stage == "proof" or binding is None:
        return False
    window_start = clock.segment_deadline - timedelta(milliseconds=clock.window_ms)
    now = datetime.now(UTC)
    if now < source_edit_deadline(clock.segment_deadline, started_at=window_start):
        return False
    run = await session.get(GenerationRun, state.identity.run_id)
    project = await session.get(Project, state.identity.project_id)
    if run is None or project is None or project.template != "max_miniapp" or (
        is_restoration_adaptation(run)
    ):
        return False

    proof, result, lease = ProjectCellProof, ProjectCellProofResult, ProjectCellActivityLease
    workspace_id = UUID(binding.workspace_id)
    journal = (
        select(proof, result, lease)
        .join(result, result.proof_id == proof.id)
        .join(lease, lease.operation_id == result.operation_id)
        .where(
            proof.workspace_id == workspace_id, result.workspace_id == workspace_id,
            lease.workspace_id == workspace_id, proof.generation_run_id == state.identity.run_id,
            lease.generation_run_id == state.identity.run_id,
            proof.fencing_epoch == binding.fencing_epoch,
            lease.fencing_epoch == proof.fencing_epoch,
            lease.proof_key == proof.proof_key,
            lease.kind == "command", lease.phase == "fast_check",
            result.dimension == "fast_check", lease.finished_at.is_not(None),
            lease.finished_at >= lease.started_at,
            proof.schema_data_digest == binding.schema_sha256,
            proof.base_image_digest == binding.environment_sha256,
            proof.toolchain_digest == binding.environment_sha256,
        )
    )
    green = (await session.execute(journal.where(
        proof.workspace_revision == binding.source_sha256,
        result.outcome == "green", lease.state == "completed",
        lease.started_at >= window_start, lease.finished_at <= now,
    ).order_by(lease.finished_at.desc()).limit(1))).first()
    if green is None:
        return False

    checked = green[0]
    earlier = (await session.execute(journal.where(
        proof.workspace_revision != binding.source_sha256,
        proof.dependency_digest == checked.dependency_digest,
        proof.cell_manifest_digest == checked.cell_manifest_digest,
        proof.resource_profile_version == checked.resource_profile_version,
        proof.build_config_digest == checked.build_config_digest,
        result.outcome == "red", lease.state == "failed",
        lease.started_at >= window_start, lease.finished_at < green[2].started_at,
    ).order_by(lease.finished_at.desc()).limit(1))).first()
    if earlier is None:
        return False
    historical = proof_identity_from_model(earlier[0])
    red = earlier[1]
    if (
        historical.proof_key != earlier[0].proof_key
        or red.dimension_key != historical.dimension_key(ProofDimension.FAST_CHECK)
        or red.detail_digest != hashlib.sha256(red.redacted_detail.encode()).hexdigest()
        # The coordinator records this RED when the command changes the
        # candidate identity, even if the failure was unrelated to compilation.
        or red.redacted_detail.startswith("command changed the frozen proof identity")
        or source_diagnostic_has_infrastructure_error(red.redacted_detail)
        or not (
            compiler_failure(red.redacted_detail) is not None
            or source_check_is_repairable(red.dimension, red.outcome, red.redacted_detail)
        )
    ):
        return False

    # Historical evidence cannot certify the current workspace. The normal
    # current-result loader revalidates the live source, environment and fence.
    current = await load_compiler_witness(
        session, run_id=state.identity.run_id, receipt_id=green[1].id, binding=binding,
    )
    return (
        current.passed and current.dimension == "fast_check"
        and current.comparison_sha256 == replace(historical, workspace_revision="0" * 64).proof_key
    )


async def grant_window(driver: ExecutionDriver, *, repair_receipt_id: UUID | None = None) -> bool:
    """Consume each independently verified window grant at most once."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services import max_behavior_proof as behavior
    from yleum_api.services.generation.recovery_requirements import (
        load_compiler_witness,
        load_requirement_witness,
    )

    granted = False

    async def transform(session: AsyncSession, state: RecoveryStateV3) -> RecoveryStateV3:
        nonlocal granted
        clock = state.clock
        if state.binding is None:
            return state
        ids = clock.consumed_requirement_ids
        stage = clock.stage
        repair = clock.repair_granted
        compiler = clock.compiler_granted
        if repair_receipt_id is not None:
            if repair:
                return state
            witness = await load_compiler_witness(
                session,
                run_id=state.identity.run_id,
                receipt_id=repair_receipt_id,
                binding=state.binding,
            )
            if witness.passed:
                raise RecoveryContractError("repair entry requires an owned terminal RED")
            stage, repair = "repair", True
        else:
            requirements = [
                r for r in state.requirements if r.required and r.check_kind == "named_behavior"
            ]
            named_pending = any(r.id not in ids for r in requirements)
            run = await session.get(GenerationRun, state.identity.run_id) if named_pending else None
            if run is not None and behavior.RECEIPT_KEY in run.agent_state:
                raw = run.agent_state[behavior.RECEIPT_KEY]
                if not isinstance(raw, dict) or type(raw.get("receipt_digest")) is not str:
                    raise RecoveryContractError("semantic receipt invalid")
                try:
                    for requirement in requirements:
                        await load_requirement_witness(
                            session,
                            run_id=state.identity.run_id,
                            requirement_id=requirement.id,
                            receipt_id="named_behavior:" + raw["receipt_digest"],
                            binding=state.binding,
                        )
                except behavior.BehaviorProofError as exc:
                    raise RecoveryContractError("semantic receipt unavailable or invalid") from exc
                ids = tuple(sorted(set(ids) | {r.id for r in requirements}))
                # A fully covered supported request proceeds to verification. The
                # grant must not create a gratuitous new model turn.
                stage = (
                    "edit"
                    if any(r.required and r.check_kind == "manual" for r in state.requirements)
                    else "proof"
                )
            elif await _current_compiler_progress(session, state):
                compiler = True
            elif named_pending:
                raise RecoveryContractError("semantic receipt unavailable")
            else:
                return state
        granted = True
        return state.model_copy(
            update={
                "clock": clock.model_copy(
                    update={
                        "stage": stage,
                        "segment_seq": clock.segment_seq + 1,
                        "segment_used_ms": 0,
                        "segment_deadline": datetime.now(UTC)
                        + timedelta(milliseconds=clock.window_ms),
                        "repair_granted": repair,
                        "compiler_granted": compiler,
                        "consumed_requirement_ids": ids,
                    }
                )
            }
        )

    await update_owned_clock(driver, transform)
    return granted
