"""Typed preparation data and fresh runtime bindings for the durable pipeline."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.design_plugin import DesignContract
from yleum_api.services.generation.contracts import AgentPromptPlan, StackPrompt
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import (
    IncompleteResponseBudget,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryStateV2,
)
from yleum_api.services.generation.recovery_execution import ExecutionDriver
from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle
from yleum_api.services.promotion_permit import workspace_revision_digest


def _record(cls: Any, raw: Any) -> Any:
    keys = set(cls._fields if hasattr(cls, "_fields") else cls.__dataclass_fields__)
    if type(raw) is not dict or set(raw) != keys:
        raise RecoveryContractError("prepared pipeline record has an unknown shape")
    payload = list(raw[key] for key in cls._fields) if hasattr(cls, "_fields") else raw
    return TypeAdapter(cls).validate_json(json.dumps(payload, allow_nan=False), strict=True)


class BootstrapPreparation(BaseModel):
    """Original resolved request data; no actor credentials or execution proof."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    classification: tuple[str, bool, bool, bool]
    design_contract: DesignContract | None = None
    adjacent_files: dict[str, str] | None = None
    adjacent_deleted_paths: tuple[str, ...] = ()

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported bootstrap preparation")
        return value


_BOOTSTRAP_STAGES = frozenset({
    "stack_prepare", "prompt_prepare", "starter_apply", "adjacent_apply", "font_apply",
})


def _bootstrap_output(name: str, value: Any, *, encode: bool) -> Any:
    """Serialize only the existing typed preparation result, never capabilities."""
    if name == "stack_prepare":
        return value._asdict() if encode else _record(StackPrompt, value)
    if name == 'font_apply':
        if type(value) is not dict or any(
            type(k) is not str or type(v) is not str for k, v in value.items()
        ):
            raise RecoveryContractError('invalid font source preparation')
        return value
    if name == "prompt_prepare":
        if encode:
            plan, build_plan = value
            if build_plan is not None:
                raise RecoveryContractError("unsupported bootstrap build-plan authority")
            return {"plan": asdict(plan), "build_plan": None}
        if type(value) is not dict or set(value) != {"plan", "build_plan"}:
            raise RecoveryContractError("invalid bootstrap prompt result")
        if value["build_plan"] is not None:
            raise RecoveryContractError("unsupported bootstrap build plan")
        return _record(AgentPromptPlan, value["plan"]), None
    if name == "starter_apply":
        if encode:
            files, result = value
            return {"files": files, "result": _result_data(result) if result is not None else None}
        if (type(value) is not dict or set(value) != {"files", "result"}
            or type(value["files"]) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in value["files"].items())):
            raise RecoveryContractError("invalid bootstrap starter result")
        return value["files"], (
            _result_from_data(value["result"]) if value["result"] is not None else None
        )
    if name == "adjacent_apply" and (encode or value is True):
        return True if encode else None
    raise RecoveryContractError("unknown bootstrap preparation result")


async def run_bootstrap_stage(
    driver: ExecutionDriver | None, name: str, invoke: Callable[[], Awaitable[Any]],
    *, handle: ProjectCellExecutorHandle | None,
) -> Any:
    """Resume a partial source preparation through its owning per-write receipts."""
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        tool_effect_scope,
    )

    if driver is None:
        return await invoke()
    if name not in _BOOTSTRAP_STAGES or driver.state.execution.stage != "bootstrap":
        raise RecoveryContractError("bootstrap stage does not belong to this cursor")
    completed = driver.context["completed"]
    results = completed.get("bootstrap_results", {})
    if type(results) is not dict or set(results) - _BOOTSTRAP_STAGES:
        raise RecoveryContractError("unknown bootstrap result inventory")
    if name in results:
        return _bootstrap_output(name, results[name], encode=False)
    pending = completed.get("bootstrap_pending")
    expected_pending = {"version": 1, "name": name}
    claim = driver.state.continuation_claim
    if claim is None or (pending is not None and pending != expected_pending):
        raise RecoveryContractError("bootstrap pending stage conflicts")
    cold = pending is not None
    cursor = ToolEffectCursor(
        claim, driver.state.state_revision,
        driver.state.execution.logical_step_seq + (0 if cold else 1),
        f"bootstrap:{name}", reconcile_only=cold,
    )
    substep = "starter_apply" if name in {"adjacent_apply", 'font_apply'} else name
    if cold:
        if (driver.state.execution.substep != substep
            or driver.state.execution.pending_effect_id != cursor.root_effect_id):
            raise RecoveryContractError("bootstrap logical effect conflicts")
    else:
        await driver.save(driver.state.execution.model_copy(update={
            "substep": substep, "logical_step_seq": cursor.logical_step_seq,
            "pending_effect_id": cursor.root_effect_id,
        }), context={**driver.context, "completed": {
            **completed, "bootstrap_pending": expected_pending,
        }})
        cursor.state_revision = driver.state.state_revision
    try:
        from yleum_api.services.generation.recovery_tool_receipts import (
            reconcile_preparation_receipts,
            require_preparation_receipts,
        )
        with tool_effect_scope(cursor):
            if cold:
                if handle is None:
                    raise RecoveryContractError("bootstrap has no current executor")
                await reconcile_preparation_receipts(
                    driver.factory, identity=driver.state.identity, handle=handle,
                )
            value = await invoke()
            await require_preparation_receipts(driver.factory, identity=driver.state.identity)
        encoded = _bootstrap_output(name, value, encode=True)
        await driver.save(driver.state.execution.model_copy(update={"pending_effect_id": None}),
            context={**driver.context, "completed": {
                **driver.context["completed"], "bootstrap_pending": None,
                "bootstrap_results": {**results, name: encoded},
            }})
        return value
    except Exception:
        await driver.wait()
        raise AssertionError("committed bootstrap wait must leave invocation") from None


class PendingRole(BaseModel):
    """A logical command target, never a proof or a restored execution lease."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    operation_id: UUID
    role: Literal["bootstrap", "fast_check", "full_build", "restore_runtime"]
    return_substep: str

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: Any) -> Any:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported pending command version")
        return value


_PROVIDER_FRAME_STEPS = frozenset({
    "response_wait", "response_validated", "tool_ready", "tool_wait",
})


def pending_role(driver: ExecutionDriver) -> PendingRole | None:
    completed = driver.context["completed"]
    if "pending_role" not in completed:
        return None
    return PendingRole.model_validate_json(json.dumps(completed["pending_role"]))


async def _finish_role_cursor(
    driver: ExecutionDriver,
    pending: PendingRole,
    *,
    cold: bool = False,
) -> None:
    completed = dict(driver.context["completed"])
    completed.pop("pending_role")
    # Source checkpoint follows exact terminal receipt/current physical validation.
    await driver.save(
        driver.state.execution.model_copy(
            update={
                "substep": pending.return_substep,
                "pending_effect_id": (
                    driver.state.execution.provider_effect_id
                    if pending.return_substep in _PROVIDER_FRAME_STEPS else None
                ),
            }
        ),
        context={**driver.context, "completed": completed},
    )
    if cold:
        driver._reconcile_response = pending.return_substep in _PROVIDER_FRAME_STEPS


async def finish_recorded_role(driver: ExecutionDriver, operation_id: UUID) -> None:
    pending = pending_role(driver)
    if pending is not None:
        if pending.operation_id != operation_id:
            raise RecoveryContractError("recorded role operation conflicts")
        await _finish_role_cursor(driver, pending)


async def run_durable_role[T](
    driver: ExecutionDriver,
    *,
    role: str,
    operation_id: UUID,
    invoke: Callable[[], Awaitable[T]],
) -> T:
    from yleum_api.services.generation.recovery_reconciliation import (
        _TOOL_EFFECT,
        ProviderEffectCursor,
        ToolEffectCursor,
        tool_effect_scope,
    )

    if _TOOL_EFFECT.get() is not None:
        return await invoke()  # The model's tool frame already owns this logical step.
    if pending_role(driver) is not None:
        raise RecoveryContractError("pending command must be reconciled before fresh proof")
    claim = driver.state.continuation_claim
    if claim is None:
        raise RecoveryContractError("command has no current continuation claim")
    pending = PendingRole.model_validate(
        {
            "operation_id": operation_id,
            "role": role,
            "return_substep": driver.state.execution.substep,
        }
    )
    execution = driver.state.execution
    shares_provider_step = pending.return_substep in _PROVIDER_FRAME_STEPS
    if shares_provider_step and execution.provider_effect_id != ProviderEffectCursor(
        claim, driver.state.state_revision, execution.logical_step_seq,
    ).root_effect_id:
        raise RecoveryContractError("autonomous role conflicts with the retained provider cursor")
    # An early compiler check belongs to the still-retained model frame. Its
    # distinct role operation owns its effect key; it must not move that frame
    # onto another provider sequence. Standalone roles keep advancing normally.
    cursor = ToolEffectCursor(
        claim,
        driver.state.state_revision + 1,
        execution.logical_step_seq + (0 if shares_provider_step else 1),
        f"role:{operation_id}",
    )
    await driver.save(
        driver.state.execution.model_copy(
            update={
                "logical_step_seq": cursor.logical_step_seq,
                "substep": "proof_wait",
                "pending_effect_id": cursor.root_effect_id,
            }
        ),
        context={
            **driver.context,
            "completed": {
                **driver.context["completed"],
                "pending_role": pending.model_dump(mode="json"),
            },
        },
    )
    cursor.state_revision = driver.state.state_revision
    try:
        with tool_effect_scope(cursor):
            result = await invoke()
        # The coordinator clears this only after its owning SQL proof receipt.
        # A crash between physical completion and that commit still resumes GET-first.
        return result
    except Exception:
        await driver.wait()
        raise AssertionError("committed wait must leave role") from None


async def reconcile_pending_role(
    driver: ExecutionDriver, handle: ProjectCellExecutorHandle
) -> None:
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        tool_effect_scope,
    )
    from yleum_api.services.generation.recovery_tool_receipts import require_command_reconciliation
    from yleum_api.services.project_cell_executor import ProjectCellCommandRole

    pending = pending_role(driver)
    if pending is None:
        return
    try:
        claim = driver.state.continuation_claim
        if claim is None or handle.run_role is None:
            raise RecoveryContractError("pending command has no current executor")
        cursor = ToolEffectCursor(
            claim,
            driver.state.state_revision,
            driver.state.execution.logical_step_seq,
            f"role:{pending.operation_id}",
            reconcile_only=True,
        )
        if (
            driver.state.execution.substep != "proof_wait"
            or driver.state.execution.pending_effect_id != cursor.root_effect_id
        ):
            raise RecoveryContractError("pending command logical cursor conflicts")
        with tool_effect_scope(cursor):
            await require_command_reconciliation(
                driver.factory,
                run_id=driver.state.identity.run_id,
                project_id=driver.state.identity.project_id,
                user_id=driver.state.identity.user_id,
            )
            # Historical observation is discarded. Normal finalization obtains a
            # new current-fence proof; this read never restores proof authority.
            await handle.run_role(ProjectCellCommandRole(pending.role), pending.operation_id)
        await _finish_role_cursor(driver, pending, cold=True)
    except Exception:
        await driver.wait()


def _result_data(result: AgentResult) -> dict[str, Any]:
    # Process-local proof summaries cannot survive a lease/process boundary.
    # Keep the full transcript and arithmetic; finalization obtains fresh proofs.
    return asdict(replace(result, proof_checkpoint=None))


def _result_from_data(raw: Any) -> AgentResult:
    if type(raw) is not dict or raw.get("proof_checkpoint") is not None:
        raise RecoveryContractError("serialized agent proof authority is unsupported")
    return cast(AgentResult, _record(AgentResult, raw))


@dataclass(frozen=True)
class PreparedAgentEntry:
    classification: tuple[str, bool, bool, bool]
    design_contract: DesignContract | None
    stack: StackPrompt
    prompt_plan: AgentPromptPlan
    seed_files: dict[str, str]
    initial_result: AgentResult | None
    edit_deadline: datetime | None

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "classification": list(self.classification),
            "design_contract": asdict(self.design_contract) if self.design_contract else None,
            "stack": self.stack._asdict(),
            "prompt_plan": asdict(self.prompt_plan),
            "build_plan": None,
            "seed_files": self.seed_files,
            "initial_result": _result_data(self.initial_result) if self.initial_result else None,
            "edit_deadline": self.edit_deadline.isoformat() if self.edit_deadline else None,
        }

    @classmethod
    def from_json(cls, raw: Any) -> PreparedAgentEntry:
        if (
            type(raw) is not dict
            or set(raw)
            != {
                "version",
                "classification",
                "design_contract",
                "stack",
                "prompt_plan",
                "build_plan",
                "seed_files",
                "initial_result",
                "edit_deadline",
            }
            or type(raw["version"]) is not int
            or raw["version"] != 1
            or raw["build_plan"] is not None
        ):
            raise RecoveryContractError("unsupported prepared agent entry")
        turn = raw["classification"]
        files = raw["seed_files"]
        if (
            type(turn) is not list
            or len(turn) != 4
            or type(turn[0]) is not str
            or any(type(value) is not bool for value in turn[1:])
            or type(files) is not dict
            or any(type(path) is not str or type(value) is not str for path, value in files.items())
        ):
            raise RecoveryContractError("invalid prepared agent inputs")
        deadline = None
        if raw["edit_deadline"] is not None:
            if type(raw["edit_deadline"]) is not str:
                raise RecoveryContractError("invalid original edit deadline")
            deadline = datetime.fromisoformat(raw["edit_deadline"])
            if deadline.utcoffset() is None:
                raise RecoveryContractError("original edit deadline requires an absolute clock")
        return cls(
            (turn[0], turn[1], turn[2], turn[3]),
            _record(DesignContract, raw["design_contract"])
            if raw["design_contract"] is not None
            else None,
            _record(StackPrompt, raw["stack"]),
            _record(AgentPromptPlan, raw["prompt_plan"]),
            files,
            _result_from_data(raw["initial_result"]) if raw["initial_result"] is not None else None,
            deadline,
        )


@dataclass(frozen=True)
class CompletedAgentTurn:
    result: AgentResult
    provider_failure: str | None
    response_budget: IncompleteResponseBudget

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "result": _result_data(self.result),
            "provider_failure": self.provider_failure,
            "response_budget": self.response_budget.model_dump(mode="json"),
        }

    @classmethod
    def from_json(cls, raw: Any) -> CompletedAgentTurn:
        if (
            type(raw) is not dict
            or set(raw) != {"version", "result", "provider_failure", "response_budget"}
            or type(raw["version"]) is not int
            or raw["version"] != 1
            or (raw["provider_failure"] is not None and type(raw["provider_failure"]) is not str)
        ):
            raise RecoveryContractError("unsupported completed agent turn")
        return cls(
            _result_from_data(raw["result"]),
            raw["provider_failure"],
            IncompleteResponseBudget.model_validate_json(json.dumps(raw["response_budget"])),
        )


async def complete_agent_turn(
    driver: ExecutionDriver,
    result: AgentResult,
    failure: str | None,
    budget: IncompleteResponseBudget,
) -> None:
    completed = CompletedAgentTurn(result, failure, budget)
    context = {
        **driver.context,
        "active_frame": None,
        "active_tool": None,
        "completed": {**driver.context["completed"], "agent_turn": completed.to_json()},
    }
    # The model transcript and arithmetic are data. A later proof callback must
    # obtain its own current receipt; no collector/issuer is restored here.
    await driver.save(
        driver.state.execution.model_copy(
            update={
                "stage": "finalization",
                "substep": "checkpoint_ready",
                "pending_effect_id": None,
            }
        ),
        context=context,
    )


@dataclass(frozen=True)
class FinalizationInputs:
    """Completed pipeline data, never a current acceptance/proof verdict."""

    result: AgentResult
    files: dict[str, str]
    accumulated: str
    verification_failed: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "result": _result_data(self.result),
            "files": self.files,
            "accumulated": self.accumulated,
            "verification_failed": self.verification_failed,
        }

    @classmethod
    def from_json(cls, raw: Any) -> FinalizationInputs:
        if (
            type(raw) is not dict
            or set(raw)
            != {
                "version",
                "result",
                "files",
                "accumulated",
                "verification_failed",
            }
            or type(raw["version"]) is not int
            or raw["version"] != 1
            or type(raw["files"]) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in raw["files"].items())
            or type(raw["accumulated"]) is not str
            or type(raw["verification_failed"]) is not bool
        ):
            raise RecoveryContractError("unsupported finalization inputs")
        return cls(
            _result_from_data(raw["result"]),
            raw["files"],
            raw["accumulated"],
            raw["verification_failed"],
        )


async def save_finalization_inputs(driver: ExecutionDriver, value: FinalizationInputs) -> None:
    await driver.save(
        driver.state.execution,
        context={
            **driver.context,
            "completed": {
                **driver.context["completed"],
                "finalization_inputs": value.to_json(),
            },
        },
    )


class SourceRepairEntry(BaseModel):
    """Original model inputs and arithmetic; no source/proof permission survives."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    status: Literal["active", "complete"] = "active"
    task: str
    baseline: dict[str, str]
    edit_deadline: datetime | None
    targeted: bool
    response_budget: IncompleteResponseBudget
    result: dict[str, Any] | None = None

    @field_validator("edit_deadline")
    @classmethod
    def absolute_clock(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("repair deadline requires an absolute clock")
        return value


class FinalizationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    source_history: tuple[str, ...]
    feedback: tuple[str, ...] = ()
    pending_detail: str | None = None
    pending_targeted: bool = False
    no_progress: bool = False

    @field_validator("source_history")
    @classmethod
    def source_states(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from yleum_api.services.generation.continuation import SourceNoveltyTracker

        SourceNoveltyTracker.from_source_history(value)
        return value


async def save_finalization_progress(
    driver: ExecutionDriver,
    progress: FinalizationProgress,
    *,
    consume_repair: bool = False,
) -> None:
    completed = {
        **driver.context["completed"],
        "finalization_progress": progress.model_dump(mode="json"),
    }
    if consume_repair:
        repair = pending_source_repair(driver)
        if repair is None or repair.status != "complete":
            raise RecoveryContractError("source repair has no completed result")
        completed.pop("source_repair")
    await driver.save(driver.state.execution, context={**driver.context, "completed": completed})


def pending_source_repair(driver: ExecutionDriver) -> SourceRepairEntry | None:
    raw = driver.context["completed"].get("source_repair")
    if raw is None:
        return None
    entry = SourceRepairEntry.model_validate_json(json.dumps(raw, allow_nan=False))
    if (entry.status == "complete") != (entry.result is not None):
        raise RecoveryContractError("source repair completion is incomplete")
    if entry.result is not None:
        _result_from_data(entry.result)
    return entry


async def prepare_source_repair(
    driver: ExecutionDriver,
    *,
    task: str,
    baseline: dict[str, str],
    edit_deadline: datetime | None,
    targeted: bool,
    response_budget: IncompleteResponseBudget,
) -> SourceRepairEntry:
    if (entry := pending_source_repair(driver)) is not None:
        return entry
    entry = SourceRepairEntry(
        task=task,
        baseline=baseline,
        edit_deadline=edit_deadline,
        targeted=targeted,
        response_budget=response_budget,
    )
    budget = driver.state.execution.budget
    await driver.save(
        driver.state.execution.model_copy(
            update={
                "budget": budget.model_copy(update={"repair_calls": budget.repair_calls + 1}),
            }
        ),
        context={
            **driver.context,
            "completed": {
                **driver.context["completed"],
                "source_repair": entry.model_dump(mode="json"),
            },
        },
    )
    return entry


async def finish_source_repair(
    driver: ExecutionDriver,
    result: AgentResult,
    response_budget: IncompleteResponseBudget,
) -> None:
    entry = pending_source_repair(driver)
    if entry is None or entry.status != "active":
        raise RecoveryContractError("source repair invocation is no longer active")
    if response_budget.total < entry.response_budget.total:
        raise RecoveryContractError("source repair response accounting was refunded")
    saved = entry.model_copy(
        update={
            "status": "complete",
            "result": _result_data(result),
            "response_budget": response_budget,
        }
    )
    context = {
        **driver.context,
        "active_frame": None,
        "active_tool": None,
        "completed": {
            **driver.context["completed"],
            "source_repair": saved.model_dump(mode="json"),
            "response_budget": response_budget.model_dump(mode="json"),
        },
    }
    context.pop("native_outer", None)
    await driver.save(
        driver.state.execution.model_copy(
            update={
                "substep": "checkpoint_ready"
                if driver.state.execution.stage == "finalization"
                else "agent_entry",
                "pending_effect_id": None,
            }
        ),
        context=context,
    )


def attach_driver(
    factory: async_sessionmaker[AsyncSession],
    state: RecoveryStateV2,
    context: dict[str, Any],
    handle: ProjectCellExecutorHandle | None,
) -> ExecutionDriver:
    if (
        handle is None
        or not callable(getattr(handle, "current_identity", None))
        or not callable(getattr(handle, "refresh_snapshot_files", None))
        or not callable(getattr(handle, "is_portable", None))
        or not handle.is_portable()
    ):
        raise RecoveryContractError("durable continuation needs its owned portable runtime")

    assert handle is not None and handle.current_identity is not None
    assert handle.refresh_snapshot_files is not None
    identity_reader, source_reader = handle.current_identity, handle.refresh_snapshot_files

    async def snapshot() -> tuple[RecoveryBinding, dict[str, str]]:
        before = await identity_reader()
        files = dict(await source_reader())
        after = await identity_reader()
        if (
            before != after
            or after.workspace_id != handle.workspace_id
            or after.generation_run_id != state.identity.run_id
            or workspace_revision_digest(files) != after.workspace_revision
        ):
            raise RecoveryContractError("candidate changed during source checkpoint observation")
        return recovery_binding(after), files

    return ExecutionDriver(
        factory,
        state,
        context,
        snapshot,
        runtime_control=(handle.control_workspace_id, handle.control_fencing_epoch),
    )


async def rebind_continuation(driver: ExecutionDriver) -> None:
    """Reprove the retained tree under the new lease before any new logical effect.

    A source write whose terminal ACK was lost is reconciled by its own receipt
    first. Never checkpoint its old tree or accept a merely equal subset of files.
    """
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_store import _locked_owner, _validate_binding
    from yleum_api.services.generation.recovery_tool_receipts import (
        CommandBinding,
        SourceWriteBinding,
    )
    from yleum_api.services.generation_deployment_drain import require_generation_admission

    state = driver.state
    if state.execution.physical_state != "released":
        return
    old = state.binding
    try:
        current, _ = await driver.snapshot()
        if (
            old is None
            or current.workspace_id != old.workspace_id
            or current.fencing_epoch <= old.fencing_epoch
            or driver.runtime_control is None
            or driver.runtime_control[0] != state.execution.control_workspace_id
        ):
            raise RecoveryContractError("resumed candidate identity conflicts")
        same_environment = (
            current.schema_sha256 == old.schema_sha256
            and current.environment_sha256 == old.environment_sha256
        )
        if current.source_sha256 == old.source_sha256 and same_environment:
            await driver.save(state.execution, context=driver.context)
            return
        # No source mutation is licensed by a pending row. This only allows the
        # owning read-only receipt path to establish the full post-state later.
        execution = state.execution
        if (
            execution.substep not in {
                "tool_wait", "proof_wait", "stack_prepare", "prompt_prepare", "starter_apply",
            }
            or execution.pending_effect_id is None
        ):
            raise RecoveryContractError("resumed source changed without a pending write")
        async with driver.factory() as session:
            await require_generation_admission(session)
            run, _ = await _locked_owner(
                session,
                run_id=state.identity.run_id,
                project_id=state.identity.project_id,
                user_id=state.identity.user_id,
            )
            if recovery_state(run) != state:
                raise RecoveryContractError("resumed source claim changed")
            await _require_session_owner(run.id)
            await _validate_binding(session, run=run, binding=current)
            row = await session.get(
                GenerationRecoveryEffect, uuid5(execution.pending_effect_id, "physical:0")
            )
            if (
                row is None
                or row.run_id != run.id
                or row.project_id != run.project_id
                or row.user_id != run.user_id
                or row.kind != "tool"
                or row.payload_version != 1
                or row.logical_step_seq != execution.logical_step_seq
                or row.state not in {"inflight", "confirmed", "unknown"}
                or type(row.pre_binding) is not dict
            ):
                raise RecoveryContractError("resumed source has no owning write intent")
            assert row.pre_binding is not None
            bound: CommandBinding | SourceWriteBinding
            if row.pre_binding.get("kind") == "command_v1":
                bound = CommandBinding.model_validate_json(json.dumps(row.pre_binding), strict=True)
            else:
                if not same_environment:
                    raise RecoveryContractError("source write cannot reconcile environment drift")
                bound = SourceWriteBinding.model_validate_json(
                    json.dumps(row.pre_binding), strict=True
                )
            expected_operation = (
                bound.activity.operation_id
                if isinstance(bound, CommandBinding) and bound.activity is not None
                else uuid5(row.effect_id, "controller-operation-v1")
            )
            if row.operation_id != expected_operation:
                raise RecoveryContractError("resumed command operation conflicts")
            if bound.context_binding != old or str(bound.workspace_id) != old.workspace_id:
                raise RecoveryContractError("resumed source intent conflicts with retained tree")
            await session.commit()
    except Exception:
        await driver.wait()
