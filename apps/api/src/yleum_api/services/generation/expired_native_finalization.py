"""Reopen one source repair only after the stopped request and current proof agree."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID, uuid4

from minio.error import S3Error
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from urllib3.exceptions import HTTPError as ObjectTransportError

from yleum_api.services.generation.recovery_clock import start_interval, update_owned_clock
from yleum_api.services.generation.recovery_contract import (
    CompilerWitness,
    RecoveryContractError,
    RecoveryStateV3,
)
from yleum_api.services.generation.recovery_execution import store_execution_context
from yleum_api.services.generation.recovery_pipeline import (
    _PROVIDER_FRAME_STEPS,
    _RETIRED_NATIVE_REPAIR_KEY,
    FinalizationProgress,
    SourceRepairEntry,
    _RetiredNativeRepair,
    pending_role,
    pending_source_repair,
    revalidate_retired_source_repair,
)
from yleum_api.services.generation.recovery_reconciliation import (
    ToolEffectCursor,
    provider_root_effect_id,
)
from yleum_api.services.generation_deadline import source_edit_deadline
from yleum_api.services.max_management_generation import _editable_library_source
from yleum_api.services.promotion_permit import workspace_revision_digest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from yleum_api.services.generation.recovery_execution import ExecutionDriver
    from yleum_api.services.max_finalization import MaxFinalizationCoordinator

_KEY = "retired_native_finalization_v1"
_REPAIR_SECONDS = 420
_TRANSIENT_CHECKPOINT_ERRORS = (
    S3Error, ObjectTransportError, DBAPIError, PoolTimeout, OSError, TimeoutError,
)


class RetiredFinalizationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    proof: CompilerWitness
    attempt_id: UUID
    repair_path: str | None
    feedback: str | None
    repair_deadline: AwareDatetime | None
    segment_seq: Annotated[int, Field(ge=0)]
    maximum_repairs: Annotated[int, Field(ge=0)]


RetiredFinalizationPhase = Literal["pre_entry", "repair", "verification"]


def retired_finalization_plan(driver: ExecutionDriver) -> RetiredFinalizationPlan | None:
    context = getattr(driver, "context", None)
    completed = context.get("completed") if isinstance(context, dict) else None
    if type(completed) is not dict or _KEY not in completed:
        return None
    try:
        raw = completed[_RETIRED_NATIVE_REPAIR_KEY]
        if type(raw) is not dict or type(raw.get("version")) is not int:
            raise ValueError("unversioned retirement")
        retired = _RetiredNativeRepair.model_validate_json(json.dumps(raw, allow_nan=False))
        plan = RetiredFinalizationPlan.model_validate_json(
            json.dumps(completed[_KEY], allow_nan=False),
        )
    except (KeyError, TypeError, ValueError):
        raise RecoveryContractError("invalid retired source repair plan") from None
    state = driver.state
    identity = state.identity
    if (
        not isinstance(state, RecoveryStateV3) or state.binding is None
        or (plan.proof.run_id, plan.proof.project_id, plan.proof.user_id, plan.proof.prompt_hash)
        != (identity.run_id, identity.project_id, identity.user_id, identity.prompt_hash)
        or state.clock.segment_seq != plan.segment_seq or plan.attempt_id != retired.attempt_id
        or not plan.proof.passed or plan.proof.dimension != "fast_check"
        or plan.proof.binding != retired.binding or retired.checked_at.utcoffset() is None
        or state.binding.workspace_id != retired.binding.workspace_id
        or state.binding.fencing_epoch < retired.binding.fencing_epoch
        or state.execution.logical_step_seq <= retired.logical_step_seq
    ):
        raise RecoveryContractError("retired source repair plan does not own the current run")
    if plan.repair_path is None:
        if plan.feedback is not None or plan.repair_deadline is not None or plan.maximum_repairs:
            raise RecoveryContractError("retired verification plan cannot grant source repair")
    elif (
        not _editable_library_source(plan.repair_path) or not plan.feedback
        or plan.repair_deadline is None or plan.maximum_repairs < 1
        or plan.repair_deadline - timedelta(seconds=_REPAIR_SECONDS) < retired.checked_at
    ):
        raise RecoveryContractError("retired source repair opportunity is invalid")
    return plan


def retired_repair_edit_deadline(
    plan: RetiredFinalizationPlan | None, fallback: datetime | None,
) -> datetime | None:
    """Cap model work at the original opportunity, including after lease renewal."""
    if plan is None or plan.repair_deadline is None:
        return fallback
    cutoff = source_edit_deadline(
        plan.repair_deadline, started_at=plan.repair_deadline - timedelta(seconds=_REPAIR_SECONDS),
    )
    return min(cutoff, fallback) if fallback is not None else cutoff


def _has_retired_role(driver: ExecutionDriver) -> bool:
    state = driver.state
    claim = state.continuation_claim
    role = pending_role(driver)
    if claim is None or role is None:
        return False
    cursor = ToolEffectCursor(
        claim, state.state_revision, state.execution.logical_step_seq, f"role:{role.operation_id}",
    )
    return state.execution.pending_effect_id == cursor.root_effect_id and role.return_substep in (
        _PROVIDER_FRAME_STEPS | {"checkpoint_ready"}
    )


def _owns_retired_repair_entry(
    entry: SourceRepairEntry, plan: RetiredFinalizationPlan,
) -> bool:
    deadline = retired_repair_edit_deadline(plan, None)
    return (
        entry.targeted and plan.repair_path in entry.baseline
        and workspace_revision_digest(entry.baseline) == plan.proof.binding.source_sha256
        and deadline is not None and entry.edit_deadline is not None
        and entry.edit_deadline <= deadline
    )


def _has_retired_tool(driver: ExecutionDriver, frame: object) -> bool:
    state = driver.state
    claim = state.continuation_claim
    if (
        claim is None or type(frame) is not dict
        or set(frame) != {"version", "index", "key", "input_digest"}
        or type(frame["version"]) is not int or frame["version"] != 1
        or type(frame["index"]) is not int or frame["index"] != state.execution.next_tool_index
        or not isinstance(frame["key"], str) or not frame["key"]
        or not isinstance(frame["input_digest"], str)
        or re.fullmatch("[a-f0-9]{64}", frame["input_digest"]) is None
    ):
        return False
    cursor = ToolEffectCursor(
        claim, state.state_revision, state.execution.logical_step_seq, frame["key"],
    )
    return state.execution.pending_effect_id == cursor.root_effect_id


def _retired_phase(
    driver: ExecutionDriver, plan: RetiredFinalizationPlan,
) -> RetiredFinalizationPhase:
    state = driver.state
    assert isinstance(state, RecoveryStateV3) and state.binding is not None
    execution = state.execution
    context = driver.context
    frame, tool = context.get("active_frame"), context.get("active_tool")
    substep = execution.substep
    progress = FinalizationProgress.model_validate_json(json.dumps(
        context["completed"]["finalization_progress"], allow_nan=False,
    ))
    entry = pending_source_repair(driver)
    calls = execution.budget.repair_calls
    ready = (
        substep == "checkpoint_ready" and execution.pending_effect_id is None
        and pending_role(driver) is None
    )
    pending_role_owned = substep == "proof_wait" and _has_retired_role(driver)
    if calls == 1 and entry is None and frame is None and tool is None:
        if (
            plan.repair_path is not None and ready and execution.provider_effect_id is None
            and state.binding == plan.proof.binding and progress.pending_detail == plan.feedback
            and state.clock.stage == "repair" and state.clock.window_ms == _REPAIR_SECONDS * 1000
        ):
            return "pre_entry"
        if (
            plan.repair_path is None and (ready or pending_role_owned)
            and progress.pending_detail is None
        ):
            return "verification"
    if calls == 2 and plan.repair_path is not None:
        if entry is not None and not _owns_retired_repair_entry(entry, plan):
            raise RecoveryContractError("retired source repair entry changed")
        if entry is not None and entry.status == "active":
            if (
                state.clock.stage != "repair" or state.clock.window_ms != _REPAIR_SECONDS * 1000
                or progress.pending_detail != plan.feedback
            ):
                raise RecoveryContractError("retired source repair lost its bounded opportunity")
            if (
                frame is None and tool is None and execution.provider_effect_id is None
                and (ready or pending_role_owned) and state.binding == plan.proof.binding
            ):
                return "repair"
            provider = provider_root_effect_id(state.identity.run_id, execution.logical_step_seq)
            if (
                type(frame) is dict and set(frame) == {"kind", "version", "value"}
                and type(frame["version"]) is int and frame["version"] == 1
                and frame["kind"] == "native" and type(frame["value"]) is dict
                and execution.provider_effect_id == provider
                and (
                    (substep == "tool_wait" and _has_retired_tool(driver, tool))
                    or (tool is None and substep in _PROVIDER_FRAME_STEPS - {"tool_wait"}
                        and execution.pending_effect_id == provider)
                    or (tool is None and pending_role_owned)
                )
            ):
                return "repair"
        elif (
            frame is None and tool is None and (ready or pending_role_owned)
            and progress.pending_detail == (plan.feedback if entry is not None else None)
            and progress.feedback and progress.feedback[-1] == plan.feedback
        ):
            return "verification"
    raise RecoveryContractError("retired finalization cannot reuse a changed cursor")


async def restore_retired_finalization_cursor(
    driver: ExecutionDriver,
) -> RetiredFinalizationPhase | None:
    plan = retired_finalization_plan(driver)
    if plan is None:
        return None
    state = driver.state
    assert isinstance(state, RecoveryStateV3)
    execution = state.execution
    if execution.physical_state in {"release_pending", "released"}:
        # The regular continuation owns the persisted release and provider cursor.
        return None
    if (
        state.binding is None or state.phase != "working" or state.continuation_claim is None
        or execution.physical_state != "active" or execution.stage != "finalization"
        or state.context_ref is None or execution.execution_context_ref != state.context_ref
        or driver.runtime_control != (
            execution.control_workspace_id, execution.control_fencing_epoch,
        )
        or str(execution.control_workspace_id) != state.binding.workspace_id
        or execution.control_fencing_epoch != state.binding.fencing_epoch
    ):
        raise RecoveryContractError("retired finalization cannot reuse a changed cursor")
    from yleum_api.services.generation.recovery_execution import _require_session_owner

    await _require_session_owner(state.identity.run_id)
    phase = _retired_phase(driver, plan)
    actual, _ = await driver.snapshot()
    if actual != state.binding:
        raise RecoveryContractError("retired finalization cannot reuse changed source")
    start_interval(state.clock, resume_existing=True)
    return phase


@dataclass(frozen=True, slots=True)
class _Target:
    path: str
    feedback: str


def _target(gap: str | None) -> _Target | None:
    if gap is None:
        return None
    from yleum_api.services.max_management_generation import (
        ManagedSourceGap,
        ManagedSourceObligation,
    )

    if (
        not isinstance(gap, ManagedSourceGap)
        or gap.obligation is not ManagedSourceObligation.JSON_STRINGIFY
        or gap.repair_path is None
    ):
        raise RecoveryContractError("current source requires a different recovery strategy")
    return _Target(gap.repair_path, str(gap))


async def prepare_retired_finalization(
    driver: ExecutionDriver, coordinator: MaxFinalizationCoordinator,
) -> RetiredFinalizationPlan:
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_requirements import load_compiler_witness
    from yleum_api.services.generation.recovery_store import _validate_binding
    from yleum_api.services.orchestrator_client import OrchestratorUnavailable
    from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError

    saved = retired_finalization_plan(driver)
    if saved is not None:
        return saved
    try:
        terminal = await coordinator.retired_fast_check()
        checked = await revalidate_retired_source_repair(driver, compiler_receipt_id=terminal.id)
    except (OrchestratorUnavailable, ProjectCellInfrastructureError, TimeoutError):
        await driver.wait()
        raise AssertionError("committed wait must leave retired compiler") from None
    state = driver.state
    entry = pending_source_repair(driver)
    previous_completed = driver.context["completed"]
    if (
        not isinstance(state, RecoveryStateV3)
        or state.binding is None
        or checked.compiler.binding != state.binding
        or not checked.compiler.passed
        or checked.compiler.dimension != "fast_check"
        or entry is None or entry.status != "complete"
        or state.execution.budget.repair_calls != 1
        or state.clock.stage != "repair" or not state.clock.repair_granted
        or datetime.now(UTC) < state.clock.segment_deadline
    ):
        raise RecoveryContractError("retired source cannot start a new repair")

    target = _target(checked.source_gap)
    raw_retired = previous_completed[_RETIRED_NATIVE_REPAIR_KEY]
    attempt = UUID(raw_retired["attempt_id"])
    progress = FinalizationProgress.model_validate_json(json.dumps(
        previous_completed["finalization_progress"],
    ))
    if target is None:
        feedback = progress.feedback
    elif progress.feedback and progress.feedback[-1] == target.feedback:
        feedback = progress.feedback
    else:
        feedback = (*progress.feedback, target.feedback)
    next_progress = progress.model_copy(update={
        "feedback": feedback,
        "pending_detail": target.feedback if target else None,
        "pending_targeted": False,
        "pending_fast_check": target is not None,
    })
    plan = RetiredFinalizationPlan(
        proof=checked.compiler, attempt_id=attempt, repair_path=target.path if target else None,
        feedback=target.feedback if target else None,
        repair_deadline=datetime.now(UTC) + timedelta(seconds=_REPAIR_SECONDS) if target else None,
        segment_seq=state.clock.segment_seq + (target is not None),
        maximum_repairs=len(feedback) if target else 0,
    )
    completed = {
        **previous_completed, "finalization_progress": next_progress.model_dump(mode="json"),
        _KEY: plan.model_dump(mode="json"),
    }
    completed.pop("source_repair")
    context = {**driver.context, "completed": completed}
    async def commit(session: AsyncSession, current: RecoveryStateV3) -> RecoveryStateV3:
        if current != state or current.binding is None:
            raise RecoveryContractError("retired source changed before granting repair")
        run = await session.get(GenerationRun, current.identity.run_id)
        if run is None:
            raise RecoveryContractError("retired generation was removed")
        await _validate_binding(session, run=run, binding=current.binding)
        witness = await load_compiler_witness(
            session, run_id=run.id, receipt_id=terminal.id, binding=current.binding,
        )
        if witness != checked.compiler:
            raise RecoveryContractError("current compiler receipt changed before granting repair")
        clock = current.clock
        if plan.repair_deadline is not None:
            clock = clock.model_copy(update={
                "segment_seq": plan.segment_seq, "window_ms": _REPAIR_SECONDS * 1000,
                "segment_used_ms": 0,
                "segment_deadline": plan.repair_deadline,
                "interval_id": uuid4(), "interval_open": True, "interval_charged_ms": 0,
            })
        return current.model_copy(update={
            "context_ref": ref, "clock": clock,
            "execution": current.execution.model_copy(update={"execution_context_ref": ref}),
        })

    try:
        ref = await store_execution_context(state.identity, context, binding=state.binding)
        await update_owned_clock(driver, commit)
    except _TRANSIENT_CHECKPOINT_ERRORS:
        # Re-read the committed cursor. A lost commit acknowledgement can have
        # advanced the checkpoint and clock together; never replace it.
        await driver.wait(checkpoint_uncertain=True)
        raise AssertionError("committed wait must leave retired plan") from None
    driver.context = context
    if target is not None:
        assert isinstance(driver.state, RecoveryStateV3)
        start_interval(driver.state.clock)
    return plan
