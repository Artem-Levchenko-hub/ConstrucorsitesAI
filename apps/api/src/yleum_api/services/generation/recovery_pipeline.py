"""Typed preparation data and fresh runtime bindings for the durable pipeline."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.design_plugin import DesignContract
from yleum_api.services.generation.contracts import AgentPromptPlan, StackPrompt
from yleum_api.services.generation.handoff_advice import normalize_handoff
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import (
    CompilerWitness,
    IncompleteResponseBudget,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryStateV2,
    RecoveryStateV3,
)
from yleum_api.services.generation.recovery_execution import ExecutionDriver
from yleum_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorMachineInventoryUnavailable,
    OrchestratorUnavailable,
    ProjectCellEnvironmentRecoveryProof,
)
from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle
from yleum_api.services.promotion_permit import workspace_revision_digest

if TYPE_CHECKING:
    from yleum_api.services.generation.expired_native_finalization import RetiredFinalizationPlan
    from yleum_api.services.generation.recovery_execution import GenerationYielded
    from yleum_api.services.generation.recovery_reconciliation import ToolEffectCursor

log = get_logger(__name__)


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
    isolated_test_paths: tuple[str, ...] | None = None

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported bootstrap preparation")
        return value


_BOOTSTRAP_STAGES = frozenset({
    "stack_prepare", "prompt_prepare", "starter_apply", "starter_isolate",
    "adjacent_apply", "font_apply",
})


def can_isolate_bootstrap_starter(driver: ExecutionDriver) -> bool:
    completed = driver.context["completed"]
    results = completed.get("bootstrap_results", {})
    pending = completed.get("bootstrap_pending")
    # An older execution may already be applying the draft or fonts. Its
    # starter proof preceded those effects, so keep its existing sequence.
    return not any(
        name in results or pending == {"version": 1, "name": name}
        for name in ("adjacent_apply", "font_apply")
    )


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
    if name in {"starter_isolate", "adjacent_apply"} and (encode or value is True):
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
    substep = (
        "starter_apply" if name in {"starter_isolate", "adjacent_apply", "font_apply"} else name
    )
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
                if name == "starter_isolate":
                    # This stage owns exactly one frozen write. A prior intent
                    # is replayed from its receipt. With no intent, no POST
                    # could have been sent before the worker stopped.
                    cursor.reconcile_only = False
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
    expected_binding: RecoveryBinding | None = None,
) -> None:
    completed = dict(driver.context["completed"])
    completed.pop("pending_role")
    # Source checkpoint follows exact terminal receipt/current physical validation.
    execution = driver.state.execution.model_copy(
        update={
            "substep": pending.return_substep,
            "pending_effect_id": (
                driver.state.execution.provider_effect_id
                if pending.return_substep in _PROVIDER_FRAME_STEPS else None
            ),
        }
    )
    context = {**driver.context, "completed": completed}
    if expected_binding is None:
        await driver.save(execution, context=context)
    else:
        await driver.save(execution, context=context, expected_binding=expected_binding)
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
        completed = driver.context["completed"]
        preparation = completed.get("bootstrap_preparation")
        starter_proof = (
            driver.state.execution.stage == "bootstrap"
            and pending.return_substep == "starter_apply"
            and pending.role in {"bootstrap", "fast_check"}
            and isinstance(preparation, dict)
            and isinstance(preparation.get("isolated_test_paths"), list)
            and "starter_apply" in completed.get("bootstrap_results", {})
            and completed.get("bootstrap_pending") is None
        )
        with tool_effect_scope(cursor):
            has_intent = await require_command_reconciliation(
                driver.factory,
                run_id=driver.state.identity.run_id,
                project_id=driver.state.identity.project_id,
                user_id=driver.state.identity.user_id,
                undispatched_operation_id=pending.operation_id if starter_proof else None,
            )
            if has_intent:
                # Discard historical observations. The next check needs a current proof.
                await handle.run_role(ProjectCellCommandRole(pending.role), pending.operation_id)
        # Without a physical intent the controller could not have received a
        # POST. Resume starter verification from its saved substep in that case.
        await _finish_role_cursor(driver, pending, cold=True)
    except Exception:
        await driver.wait()


def _result_data(result: AgentResult) -> dict[str, Any]:
    # Process-local proof summaries cannot survive a lease/process boundary.
    # Keep the full transcript and arithmetic; finalization obtains fresh proofs.
    payload = asdict(replace(result, proof_checkpoint=None))
    if result.handoff is None:
        payload.pop("handoff")
    return payload


def _result_from_data(raw: Any) -> AgentResult:
    if type(raw) is not dict or raw.get("proof_checkpoint") is not None:
        raise RecoveryContractError("serialized agent proof authority is unsupported")
    # Old completion records predate optional author advice. All other fields remain exact.
    return cast(AgentResult, _record(AgentResult, {
        **raw, "handoff": normalize_handoff(raw.get("handoff")),
    }))


@dataclass(frozen=True)
class PreparedAgentEntry:
    classification: tuple[str, bool, bool, bool]
    design_contract: DesignContract | None
    stack: StackPrompt
    prompt_plan: AgentPromptPlan
    seed_files: dict[str, str]
    initial_result: AgentResult | None
    edit_deadline: datetime | None
    restored_files: dict[str, str] | None = None

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
            **({"restored_files": self.restored_files} if self.restored_files is not None else {}),
        }

    @classmethod
    def from_json(cls, raw: Any) -> PreparedAgentEntry:
        if (
            type(raw) is not dict
            or (set(raw) - {"restored_files"})
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
        restored = raw.get("restored_files")
        if (
            type(turn) is not list
            or len(turn) != 4
            or type(turn[0]) is not str
            or any(type(value) is not bool for value in turn[1:])
            or type(files) is not dict
            or any(type(path) is not str or type(value) is not str for path, value in files.items())
            or (
                restored is not None and (
                    type(restored) is not dict or any(
                        type(path) is not str or type(value) is not str
                        for path, value in restored.items()
                    )
                )
            )
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
            restored,
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
    repair_model: Literal["kimi-k3.1-pro"] | None = None

    def to_json(self) -> dict[str, Any]:
        # Preserve the existing receipt shape on ordinary runs.
        excluded = {"repair_model"} if self.repair_model is None else None
        return self.model_dump(mode="json", exclude=excluded)

    @field_validator("edit_deadline")
    @classmethod
    def absolute_clock(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("repair deadline requires an absolute clock")
        return value


_NATIVE_REPAIR_LAST_REJECTION = "native_repair_last_rejection"
_NATIVE_REPAIR_KEY = re.compile(r"[0-9a-f]{64}")


def _native_repair_position(
    driver: ExecutionDriver, *, contract: str, step: int,
) -> dict[str, int | str] | None:
    context = getattr(driver, "context", None)
    if not isinstance(context, dict):
        return None
    completed = context.get("completed")
    repair = completed.get("source_repair") if isinstance(completed, dict) else None
    outer = context.get("native_outer")
    if type(repair) is not dict or repair.get("status") != "active" or type(outer) is not dict:
        return None
    segment = outer.get("segment")
    repair_call = driver.state.execution.budget.repair_calls
    if type(segment) is not int or segment < 1 or repair_call < 1:
        return None
    return {
        "version": 1, "contract": contract, "segment": segment, "step": step,
        "repair_call": repair_call,
    }


def restore_native_repair_rejection(
    driver: ExecutionDriver, *, contract: str, step: int,
) -> str | None:
    """Read advisory history for the current authenticated frame."""
    position = _native_repair_position(driver, contract=contract, step=step)
    if position is None:
        return None
    raw = driver.context["completed"].get(_NATIVE_REPAIR_LAST_REJECTION)
    if type(raw) is not dict or set(raw) != {*position, "key"}:
        return None
    if any(
        type(raw[name]) is not type(value) or raw[name] != value
        for name, value in position.items()
    ):
        return None
    key = raw["key"]
    return key if type(key) is str and _NATIVE_REPAIR_KEY.fullmatch(key) else None


def stage_native_repair_rejection(
    driver: ExecutionDriver, *, contract: str, step: int, key: str | None,
) -> None:
    position = _native_repair_position(driver, contract=contract, step=step)
    if position is None:
        return
    existing = driver.context["completed"]
    if key is None and _NATIVE_REPAIR_LAST_REJECTION not in existing:
        return
    completed = dict(existing)
    if key is None:
        completed.pop(_NATIVE_REPAIR_LAST_REJECTION)
    else:
        completed[_NATIVE_REPAIR_LAST_REJECTION] = {"key": key, **position}
    # The next normal provider checkpoint saves this sibling with the exact frame.
    driver.context = {**driver.context, "completed": completed}


class FinalizationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    source_history: tuple[str, ...]
    feedback: tuple[str, ...] = ()
    pending_detail: str | None = None
    pending_targeted: bool = False
    pending_fast_check: bool = Field(default=False, exclude_if=lambda value: value is False)
    no_progress_passes: Annotated[int, Field(ge=0)] = Field(
        default=0, exclude_if=lambda value: value == 0,
    )
    interrupted_product_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )
    completed_repair_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )
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
    repair_model: Literal["kimi-k3.1-pro"] | None = None,
) -> SourceRepairEntry:
    from yleum_api.services.generation.expired_native_finalization import (
        _TRANSIENT_CHECKPOINT_ERRORS,
        retired_finalization_plan,
    )

    if (entry := pending_source_repair(driver)) is not None:
        return entry
    entry = SourceRepairEntry(
        task=task,
        baseline=baseline,
        edit_deadline=edit_deadline,
        targeted=targeted,
        response_budget=response_budget,
        repair_model=repair_model,
    )
    budget = driver.state.execution.budget
    completed = {
        **driver.context["completed"],
        "source_repair": entry.to_json(),
    }
    completed.pop(_NATIVE_REPAIR_LAST_REJECTION, None)
    try:
        await driver.save(
            driver.state.execution.model_copy(
                update={
                    "budget": budget.model_copy(update={"repair_calls": budget.repair_calls + 1}),
                }
            ),
            context={**driver.context, "completed": completed},
        )
    except _TRANSIENT_CHECKPOINT_ERRORS:
        if retired_finalization_plan(driver) is None:
            raise
        await driver.wait(checkpoint_uncertain=True)
        raise AssertionError("committed wait must leave retired repair entry") from None
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
    await driver.save(
        driver.state.execution.model_copy(
            update={
                "substep": "checkpoint_ready"
                if driver.state.execution.stage == "finalization"
                else "agent_entry",
                "pending_effect_id": None,
            }
        ),
        context=_completed_source_repair_context(driver, entry, result, response_budget),
    )


def _completed_source_repair_context(
    driver: ExecutionDriver, entry: SourceRepairEntry, result: AgentResult,
    response_budget: IncompleteResponseBudget,
) -> dict[str, Any]:
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
            "source_repair": saved.to_json(),
            "response_budget": response_budget.model_dump(mode="json"),
        },
    }
    context.pop("native_outer", None)
    context["completed"].pop(_NATIVE_REPAIR_LAST_REJECTION, None)
    return context


_RETIRED_NATIVE_REPAIR_KEY = "retired_expired_native_repair"
_RETIRED_NATIVE_REPAIR_STOP = "expired_provider_request_retired"
_EXPIRED_REPAIR_PROOF_KEY = "closed_expired_repair_proof_only"
_EXPIRED_CATALOG_REPAIR_PROOF_KEY = "closed_expired_catalog_repair_proof_only"
_RETIRED_REPAIR_COORDINATOR_REQUIRED = (
    "an expired attempt requires an enabled retired repair coordinator"
)


class _RetiredNativeRepair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    attempt_id: UUID
    logical_step_seq: Annotated[int, Field(gt=0)]
    binding: RecoveryBinding
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class RetiredNativeRepairCheck:
    compiler: CompilerWitness
    source_gap: str | None
    missing_catalog_hook: bool = False


async def retire_expired_native_source_repair(
    driver: ExecutionDriver, *, released: RecoveryStateV3, armed_attempt_id: UUID | None = None,
    proof_only: bool = False,
) -> None:
    """Atomically rebind the held checkpoint and retire its sealed unused wire."""
    from datetime import UTC

    from sqlalchemy import func, select

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.project_cell import ProjectCellOperation
    from yleum_api.services.generation.expired_native_finalization import (
        _owns_retired_repair_entry,
        retired_finalization_plan,
    )
    from yleum_api.services.generation.expired_provider_recovery import resumable_expired_repair
    from yleum_api.services.generation.provider_attempts import (
        _expired_provider_frame,
        _matches_expiry_receipt,
        expired_reservation_closed,
    )
    from yleum_api.services.generation.recovery_execution import (
        _owned_release_receipt,
        _require_session_owner,
        _response_wait_attempt,
        load_execution_context,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_quiescence import _provider_stopped
    from yleum_api.services.generation.recovery_store import _locked_owner
    from yleum_api.services.generation_deadline import is_restoration_adaptation
    from yleum_api.services.project_cells import _stored_request_payload

    original = released.execution
    old = released.binding
    release_id = original.release_operation_id
    catalog = proof_only and original.budget.repair_calls == 3
    seal_key = (
        _EXPIRED_CATALOG_REPAIR_PROOF_KEY if catalog
        else _EXPIRED_REPAIR_PROOF_KEY if proof_only else _RETIRED_NATIVE_REPAIR_KEY
    )
    if (
        driver.state != released or old is None or released.phase != "working"
        or released.continuation_claim is None
        or original.stage != "finalization" or original.substep != "response_wait"
        or original.physical_state != "released" or release_id is None
        or original.execution_context_ref is None or released.checkpoint is None
        or original.execution_context_ref != released.context_ref
        or original.next_tool_index != 0 or original.response_sha256 is not None
        or original.budget.repair_calls not in ({1, 2, 3} if proof_only else {1})
        or released.clock.stage != "repair" or not released.clock.repair_granted
        or released.unresolved_operation_ids or not released.environment_rebind_supported
        or str(original.control_workspace_id) != old.workspace_id
        or original.control_fencing_epoch != old.fencing_epoch + 1
        or driver.runtime_control != (original.control_workspace_id, old.fencing_epoch + 2)
    ):
        raise RecoveryContractError("expired repair retirement lost its exact released cursor")
    owner = released.identity
    await _require_session_owner(owner.run_id)
    assert original.control_workspace_id is not None and original.control_fencing_epoch is not None
    old_context = await load_execution_context(
        owner, original.execution_context_ref, binding=old,
    )
    frame = driver.restore_frame("native")
    entry = pending_source_repair(driver)
    if (
        old_context != driver.context or frame is None or entry is None or entry.status != "active"
        or driver.context.get("active_tool") is not None
        or seal_key in driver.context["completed"]
        or (not catalog and _EXPIRED_REPAIR_PROOF_KEY in driver.context["completed"])
    ):
        raise RecoveryContractError("expired repair retirement has no matching active native frame")
    raw_progress = driver.context["completed"].get("finalization_progress")
    progress = FinalizationProgress.model_validate_json(json.dumps(raw_progress, allow_nan=False))
    if progress.pending_detail is None or progress.no_progress:
        raise RecoveryContractError("expired repair retirement has no pending source check")
    if catalog:
        plan = retired_finalization_plan(driver)
        preceding = _RetiredNativeRepair.model_validate_json(json.dumps(
            old_context["completed"][_EXPIRED_REPAIR_PROOF_KEY], allow_nan=False,
        ))
        if (
            plan is None or plan.catalog_gap_key is None or plan.attempt_id != preceding.attempt_id
            or original.logical_step_seq <= preceding.logical_step_seq
            or plan.feedback != progress.pending_detail
            or not _owns_retired_repair_entry(entry, plan)
        ):
            raise RecoveryContractError("expired catalog repair has no exact bounded entry")
    budget = IncompleteResponseBudget.model_validate(frame.get("response_budget"))
    if budget.total < entry.response_budget.total:
        raise RecoveryContractError("expired repair retirement would refund response accounting")

    async with driver.factory() as session:
        run, project = await _locked_owner(
            session, run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
        )
        if (
            recovery_state(run) != released
            or project.template != "max_miniapp" or is_restoration_adaptation(run)
            or run.execution_backend != "worker" or run.response_mode not in {"build", "edit"}
            or (catalog and not resumable_expired_repair(run, released))
        ):
            raise RecoveryContractError("expired repair retirement lost its active run owner")
        release = await session.get(ProjectCellOperation, release_id)
        if (
            release is None or not _owned_release_receipt(
                release, run_id=owner.run_id, workspace_id=original.control_workspace_id,
                fence=original.control_fencing_epoch,
            )
        ):
            raise RecoveryContractError("expired repair retirement lost its terminal release")
        request = _stored_request_payload(release)
        strict_request = {"require_environment_proof": {
            "source_sha256": old.source_sha256, "environment_sha256": old.environment_sha256,
        }}
        if (proof_only or request) and request != strict_request:
            raise RecoveryContractError("expired repair release request conflicts")
        attempt = await _response_wait_attempt(session, released)
        if attempt is None or original.provider_effect_id is None:
            raise RecoveryContractError("expired repair has no owning no-dispatch receipt")
        root = await session.get(GenerationRecoveryEffect, original.provider_effect_id)
        if root is None or type(root.pre_binding) is not dict or (
            armed_attempt_id is not None and attempt.attempt_id != armed_attempt_id
        ):
            raise RecoveryContractError("expired repair has no owning no-dispatch receipt")
        if proof_only:
            frame_receipt = await _expired_provider_frame(
                session, attempt, max_attempt_seq=released.attempt_seq,
            )
            matches = (
                frame_receipt is not None and _matches_expiry_receipt(attempt, frame_receipt)
                and frame_receipt.root.effect_id == root.effect_id
                and frame_receipt.source.workspace_id == old.workspace_id
                and frame_receipt.source.source_sha256 == old.source_sha256
                and frame_receipt.source.schema_sha256 == old.schema_sha256
                and frame_receipt.source.fencing_epoch <= old.fencing_epoch
            )
        else:
            matches = await expired_reservation_closed(
                session, attempt, held_binding=old, max_attempt_seq=released.attempt_seq,
            )
        if not matches or not await _provider_stopped(session, run):
            raise RecoveryContractError("expired repair has no owning no-dispatch receipt")
        historical = root.pre_binding.get("source") != old.model_dump(mode="json")
        checked_at = await session.scalar(select(func.clock_timestamp()))
        assert checked_at is not None

    if armed_attempt_id is None:
        current, _ = await _read_released_environment(driver.snapshot, hold_lease=True)
    else:
        current = await _observe_released_candidate(driver, old, hold_lease=True)
    if current != old.model_copy(update={
        "fencing_epoch": old.fencing_epoch + 2, "environment_sha256": current.environment_sha256,
    }):
        raise RecoveryContractError("retired repair source, schema or lease changed")
    if historical or not request or old.environment_sha256 != current.environment_sha256:
        prove = driver.prove_environment_recovery
        if prove is None:
            raise RecoveryContractError("retired repair has no environment recovery reader")
        proof = await _read_released_environment(
            lambda: prove(release_id), hold_lease=True,
        )
        if not _release_environment_matches(
            driver, proof, release_id, original.control_fencing_epoch,
            old, current,
        ):
            raise RecoveryContractError("retired repair environment is not confirmed")

    retired = _RetiredNativeRepair(
        version=1, attempt_id=attempt.attempt_id, logical_step_seq=original.logical_step_seq,
        binding=current, checked_at=checked_at.astimezone(UTC),
    )
    result = AgentResult(
        done=False, summary="The expired provider request was never dispatched.",
        files={}, steps=0, stop_reason=_RETIRED_NATIVE_REPAIR_STOP,
        needs_finalization=proof_only,
    )
    context = _completed_source_repair_context(driver, entry, result, budget)
    context["completed"][seal_key] = retired.model_dump(mode="json")
    # A cold process must see either the original held E9 frame or the retired
    # frame at the new fence. The ordinary rebind would consume the old pointer.
    await driver.save(
        original.model_copy(update={
            "substep": "checkpoint_ready", "pending_effect_id": None, "provider_effect_id": None,
        }),
        context=context, expected_binding=current,
    )
    driver._reconcile_response = False


def _same_retired_candidate(current: RecoveryBinding, sealed: RecoveryBinding) -> bool:
    return (
        current.workspace_id == sealed.workspace_id
        and current.source_sha256 == sealed.source_sha256
        and current.schema_sha256 == sealed.schema_sha256
        and (current == sealed or current.fencing_epoch > sealed.fencing_epoch)
    )


def expired_repair_proof_only(driver: ExecutionDriver | None) -> bool:
    if driver is None:
        return False
    completed = driver.context["completed"]
    record = completed.get(_EXPIRED_REPAIR_PROOF_KEY)
    if record is None:
        return False
    from yleum_api.services.generation.expired_native_finalization import retired_finalization_plan

    plan = retired_finalization_plan(driver)
    state = driver.state
    if plan is not None and plan.catalog_gap_key is not None:
        exhausted = completed.get(_EXPIRED_CATALOG_REPAIR_PROOF_KEY)
        if exhausted is None:
            return False
        preceding = _RetiredNativeRepair.model_validate_json(json.dumps(record, allow_nan=False))
        catalog = _RetiredNativeRepair.model_validate_json(json.dumps(exhausted, allow_nan=False))
        if (
            state.execution.budget.repair_calls != 3
            or catalog.logical_step_seq <= preceding.logical_step_seq
            or catalog.attempt_id == preceding.attempt_id
        ):
            raise RecoveryContractError("expired catalog repair lost its consumed scope")
        record = exhausted
    receipt = _RetiredNativeRepair.model_validate_json(json.dumps(record, allow_nan=False))
    if (
        not isinstance(state, RecoveryStateV3) or state.binding is None
        or state.execution.stage != "finalization"
        or state.execution.logical_step_seq < receipt.logical_step_seq
        or not _same_retired_candidate(state.binding, receipt.binding)
    ):
        raise RecoveryContractError("expired repair no longer owns the verified source")
    return True


async def _closed_expired_repair_attempt(
    driver: ExecutionDriver, state: RecoveryStateV3,
) -> UUID | None:
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.expired_provider_recovery import resumable_expired_repair
    from yleum_api.services.generation.provider_attempts import expired_reservation_closed
    from yleum_api.services.generation.recovery_execution import _response_wait_attempt

    async with driver.factory() as session:
        run = await session.get(GenerationRun, state.identity.run_id)
        if run is None or not resumable_expired_repair(run, state):
            return None
        attempt = await _response_wait_attempt(session, state)
        if attempt is not None and await expired_reservation_closed(session, attempt):
            return attempt.attempt_id
    return None


async def revalidate_retired_source_repair(
    driver: ExecutionDriver, *, compiler_receipt_id: UUID, proof_only: bool = False,
) -> RetiredNativeRepairCheck:
    """Read an owned current check and source diagnostic, without granting model work."""
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.project_cell import ProjectCellActivityLease
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        load_execution_context,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_reconciliation import ToolEffectCursor
    from yleum_api.services.generation.recovery_requirements import load_compiler_witness
    from yleum_api.services.generation.recovery_store import _locked_owner
    from yleum_api.services.generation.recovery_tool_receipts import CommandBinding, digest
    from yleum_api.services.generation_runs import load_generation_dispatch
    from yleum_api.services.max_generation_contract import max_source_completion_gap
    from yleum_api.services.orchestrator_client import ProjectCellCommandReceipt

    seal_key = _EXPIRED_REPAIR_PROOF_KEY if proof_only else _RETIRED_NATIVE_REPAIR_KEY
    raw = driver.context["completed"].get(seal_key)
    if raw is None:
        raise RecoveryContractError("retired repair receipt is unavailable")
    receipt = _RetiredNativeRepair.model_validate_json(json.dumps(raw, allow_nan=False))
    state = driver.state
    entry = pending_source_repair(driver)
    if (
        not isinstance(state, RecoveryStateV3) or state.continuation_claim is None
        or state.phase != "working" or state.binding is None
        or (
            not _same_retired_candidate(state.binding, receipt.binding)
            if proof_only else state.binding != receipt.binding
        )
        or (proof_only and (
            state.execution.physical_state != "active"
            or driver.runtime_control != (
                state.execution.control_workspace_id, state.execution.control_fencing_epoch,
            )
            or str(state.execution.control_workspace_id) != state.binding.workspace_id
            or state.execution.control_fencing_epoch != state.binding.fencing_epoch
        ))
        or state.execution.stage != "finalization" or state.execution.substep != "checkpoint_ready"
        or state.execution.logical_step_seq <= receipt.logical_step_seq
        or state.execution.pending_effect_id is not None
        or state.execution.provider_effect_id is not None
        or state.context_ref is None or entry is None or entry.status != "complete"
        or entry.result is None
        or entry.result.get("stop_reason") != _RETIRED_NATIVE_REPAIR_STOP
    ):
        raise RecoveryContractError("retired repair check lost its exact cursor")
    owner = state.identity
    await _require_session_owner(owner.run_id)
    persisted_context = await load_execution_context(
        owner, state.context_ref, binding=state.binding,
    )
    if persisted_context != driver.context:
        raise RecoveryContractError("retired repair verification context changed")
    binding, files = await driver.snapshot()
    if binding != state.binding or workspace_revision_digest(files) != binding.source_sha256:
        raise RecoveryContractError("retired repair source changed before verification")
    async with driver.factory() as session:
        run, _ = await _locked_owner(
            session, run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
        )
        if recovery_state(run) != state:
            raise RecoveryContractError("retired repair check lost its active claim")
        compiler = await load_compiler_witness(
            session, run_id=owner.run_id, receipt_id=compiler_receipt_id, binding=binding,
        )
        operation = await session.get(ProjectCellActivityLease, compiler.operation_id)
        if (
            compiler.dimension != "fast_check" or operation is None
            or operation.finished_at is None or operation.finished_at < operation.started_at
            or operation.started_at < receipt.checked_at
        ):
            raise RecoveryContractError("retired repair requires a fresh compiler check")
        cursor = ToolEffectCursor(
            state.continuation_claim, state.state_revision, state.execution.logical_step_seq,
            f"role:{compiler.operation_id}",
        )
        effect = await session.get(
            GenerationRecoveryEffect, uuid5(cursor.root_effect_id, "physical:0"),
        )
        if (
            effect is None or effect.kind != "tool" or effect.payload_version != 1
            or effect.state != "confirmed" or effect.operation_id != compiler.operation_id
            or effect.parent_effect_id is not None or effect.provider_attempt_id is not None
            or effect.tool_call_id != cursor.tool_call_id or effect.attempt_seq > state.attempt_seq
            or (effect.run_id, effect.project_id, effect.user_id, effect.logical_step_seq)
            != (owner.run_id, owner.project_id, owner.user_id, cursor.logical_step_seq)
            or type(effect.receipt_ref) is not dict
            or effect.receipt_sha256 != digest(effect.receipt_ref)
        ):
            raise RecoveryContractError("retired repair compiler command binding conflicts")
        command = CommandBinding.model_validate_json(
            json.dumps(effect.pre_binding, allow_nan=False),
        )
        ack = ProjectCellCommandReceipt.from_json(effect.receipt_ref)
        if (
            command.context_binding != binding or command.workspace_id != operation.workspace_id
            or command.fencing_epoch != binding.fencing_epoch or command.activity is None
            or command.activity.operation_id != operation.operation_id
            or command.activity.proof_key != operation.proof_key
            or command.activity.phase != "fast_check"
            or (ack.workspace_id, ack.project_id, ack.owner_id, ack.generation_run_id)
            != (operation.workspace_id, owner.project_id, owner.user_id, owner.run_id)
            or ack.operation_id != compiler.operation_id
            or ack.dispatch_fencing_epoch != binding.fencing_epoch
            or ack.request_sha256 != command.request_sha256
            or {command.workspace_revision, ack.before_revision, ack.after_revision}
            != {binding.source_sha256}
            or effect.post_binding != {
                "workspace_revision": binding.source_sha256,
                "physical_volume_identity": ack.physical_volume_identity,
            }
        ):
            raise RecoveryContractError("retired repair compiler command binding conflicts")
        prompt = load_generation_dispatch(run).prompt_text
    gap = max_source_completion_gap(prompt, files, portable=True)
    page = files.get("src/app/page.tsx")
    missing_catalog_hook = (
        proof_only and page is not None and re.search(r"\buseYleumCatalog\b", page) is None
    )
    if missing_catalog_hook and page is not None:
        gap = _retired_catalog_gap(prompt, files, page, gap)
    return RetiredNativeRepairCheck(
        compiler=compiler, source_gap=gap, missing_catalog_hook=missing_catalog_hook,
    )


def _retired_catalog_gap(
    prompt: str, files: dict[str, str], page: str, gap: str | None,
) -> str | None:
    from yleum_api.services.max_generation_contract import max_source_completion_gap
    from yleum_api.services.max_management_generation import (
        ManagedSourceGap,
        ManagedSourceObligation,
    )

    location = re.search(
        r" Source location: src/app/page\.tsx:([1-9][0-9]*):1; "
        r"AST import_statement; reason missing_checkout_stylesheet;",
        str(gap),
    )
    if not isinstance(gap, ManagedSourceGap) or gap.obligation is not None or location is None:
        return gap
    lines = page.splitlines(keepends=True)
    row = int(location.group(1)) - 1
    if row >= len(lines):
        return gap
    statement = re.fullmatch(
        r"""import[ \t]+(['"])\./([A-Za-z0-9][A-Za-z0-9_.-]*\.css)\1[ \t]*;?[ \t]*[\r\n]*""",
        lines[row],
    )
    if statement is None:
        return gap
    css = "src/app/" + statement.group(2)
    if any(path == css or path.startswith((css + ".", css + "/")) for path in files):
        return gap
    # Determine whether this exact page repair can also satisfy the admitted catalog.
    # The real candidate and its publication proof still run with the actual source.
    candidate = {**files, "src/app/page.tsx": "".join(lines[:row] + lines[row + 1:])}
    latent = max_source_completion_gap(prompt, candidate, portable=True)
    if (
        isinstance(latent, ManagedSourceGap)
        and latent.obligation is ManagedSourceObligation.CATALOG_RENDER
    ):
        return latent.with_diagnostic(
            f"{latent} Remove the missing relative stylesheet import at page.tsx:{row + 1} "
            "or select an existing local stylesheet; do not fabricate styling dependencies.",
        )
    return gap


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
        prepare_environment=getattr(handle, "prepare_environment", None),
        prove_environment_recovery=getattr(handle, "prove_environment_recovery", None),
    )


async def _require_reacquired_lease(driver: ExecutionDriver) -> int:
    from yleum_api.models.project_cell import ProjectCellOperation
    from yleum_api.services.generation.recovery_execution import _owned_release_receipt

    state = driver.state
    execution = state.execution
    workspace_id, fence = driver.runtime_control or (None, None)
    saved_fence = execution.control_fencing_epoch
    if (
        execution.physical_state != "released" or execution.lease_cycle_seq == 0
        or execution.release_operation_id is None or workspace_id is None
        or workspace_id != execution.control_workspace_id
        or saved_fence is None or fence is None or fence <= saved_fence
    ):
        raise RecoveryContractError("candidate has no newer lease after release")
    async with driver.factory() as session:
        release = await session.get(ProjectCellOperation, execution.release_operation_id)
        release_fence = release.fencing_epoch if release is not None else None
        # An interrupted release can retain its pre-dispatch fence. Claiming
        # the old release advanced it once; the new lease must exceed the receipt.
        if (
            release_fence is None or saved_fence not in (release_fence, release_fence - 1)
            or fence <= release_fence
            or not _owned_release_receipt(
                release, run_id=state.identity.run_id, workspace_id=workspace_id,
                fence=release_fence,
            )
        ):
            raise RecoveryContractError("candidate has no owned release receipt")
    return release_fence


async def _prepare_owned_environment(
    driver: ExecutionDriver, error: OrchestratorMachineInventoryUnavailable,
) -> None:
    from yleum_api.services.generation.recovery_execution import _require_session_owner

    prepare = driver.prepare_environment
    if prepare is None or error.reason not in {"stopped", "missing", "prepare_pending"}:
        raise error
    # The controller startup journal binds this request to the current admission,
    # source and fence. Its result cannot substitute for a proof read.
    await _require_session_owner(driver.state.identity.run_id)
    await prepare(error.workspace_revision, error.manifest_digest)


async def _bind_initial_candidate(driver: ExecutionDriver) -> None:

    state = driver.state
    execution = state.execution
    workspace_id, fence = driver.runtime_control or (None, None)
    if (
        execution.stage != "bootstrap" or execution.substep != "runtime_prepare"
        or execution.logical_step_seq != 0 or execution.pending_effect_id is not None
        or execution.provider_effect_id is not None or state.checkpoint is not None
        or workspace_id is None or fence is None
    ):
        raise RecoveryContractError("initial candidate has no owned bootstrap lease")
    saved_control = (execution.control_workspace_id, execution.control_fencing_epoch)
    if execution.physical_state == "released":
        await _require_reacquired_lease(driver)
    elif execution.physical_state != "no_effects" and (
        execution.physical_state != "active" or (workspace_id, fence) != saved_control
    ):
        raise RecoveryContractError("admitted Project Cell lease changed before binding")

    try:
        await driver.save(execution, context=driver.context)
    except OrchestratorMachineInventoryUnavailable as error:
        await _prepare_owned_environment(driver, error)
        await driver.save(execution, context=driver.context)


class _UnprovedReleaseEnvironment(RecoveryContractError):
    pass


_ENVIRONMENT_NEEDS_PREPARE = frozenset({"stopped", "missing", "prepare_pending"})


def _can_rebind_release_environment(driver: ExecutionDriver) -> bool:
    return (
        isinstance(driver.state, RecoveryStateV3)
        and driver.state.environment_rebind_supported
        and callable(getattr(driver, "prove_environment_recovery", None))
    )


def _transient_environment_reader(error: Exception) -> bool:
    if isinstance(error, OrchestratorMachineInventoryUnavailable):
        return error.reason in _ENVIRONMENT_NEEDS_PREPARE or error.reason == "unavailable"
    return isinstance(error, OrchestratorUnavailable) or (
        isinstance(error, OrchestratorBadRequest)
        and error.status_code == 409 and error.upstream_code == "container_not_running"
    )


async def _read_released_environment[ReadResult](
    read: Callable[[], Awaitable[ReadResult]], *, hold_lease: bool, allow_prepare: bool = False,
) -> ReadResult:
    if not hold_lease:
        return await read()
    for read_attempt in range(3):
        try:
            return await read()
        except (OrchestratorUnavailable, OrchestratorBadRequest) as error:
            if (allow_prepare and isinstance(error, OrchestratorMachineInventoryUnavailable)
                    and error.reason in _ENVIRONMENT_NEEDS_PREPARE):
                raise
            # A new lease would replace the release that attests the original checkpoint.
            if read_attempt == 2 or not _transient_environment_reader(error):
                raise _UnprovedReleaseEnvironment(
                    "generation environment reader unavailable"
                ) from error
            await asyncio.sleep(read_attempt + 1)
    raise AssertionError("the last environment read must return or raise")


def _release_environment_matches(
    driver: ExecutionDriver, proof: ProjectCellEnvironmentRecoveryProof | None,
    release_id: UUID, release_fence: int, old: RecoveryBinding, current: RecoveryBinding,
) -> bool:
    owner = driver.state.identity
    return (
        proof is not None and str(proof.workspace_id) == old.workspace_id
        and proof.project_id == owner.project_id and proof.owner_id == owner.user_id
        and proof.generation_run_id == owner.run_id and proof.release_operation_id == release_id
        and proof.previous_fencing_epoch == old.fencing_epoch
        and proof.release_fencing_epoch == release_fence == old.fencing_epoch + 1
        and proof.fencing_epoch == current.fencing_epoch == release_fence + 1
        and driver.runtime_control == (proof.workspace_id, proof.fencing_epoch)
        and proof.source_sha256 == current.source_sha256
        and proof.previous_environment_sha256 == old.environment_sha256
        and proof.environment_sha256 == current.environment_sha256
    )


async def _require_rebound_environment(
    driver: ExecutionDriver, old: RecoveryBinding, current: RecoveryBinding,
) -> None:
    release_fence = await _require_reacquired_lease(driver)
    release_id = driver.state.execution.release_operation_id
    prove = driver.prove_environment_recovery
    if release_id is None or prove is None:
        raise _UnprovedReleaseEnvironment("generation release environment reader is unavailable")
    proof = await _read_released_environment(lambda: prove(release_id), hold_lease=True)
    if not _release_environment_matches(driver, proof, release_id, release_fence, old, current):
        raise _UnprovedReleaseEnvironment("generation release environment cannot be verified")


async def _rebind_release_environment(
    driver: ExecutionDriver, old: RecoveryBinding, current: RecoveryBinding,
) -> bool:
    """Accept a new full environment identity only from the owning release and startup."""
    state = driver.state
    execution = state.execution
    if (
        not _can_rebind_release_environment(driver)
        or old.source_sha256 != current.source_sha256
        or old.schema_sha256 != current.schema_sha256
    ):
        return False
    await _require_rebound_environment(driver, old, current)
    # The orchestrator reads inside save recheck the current source and binding.
    await _read_released_environment(
        lambda: driver.save(execution, context=driver.context, expected_binding=current),
        hold_lease=True,
    )
    log.info("generation.release_environment_rebound", run_id=str(state.identity.run_id),
             previous_fence=old.fencing_epoch, fencing_epoch=current.fencing_epoch)
    return True


async def _rebind_undispatched_catalog_compiler_role(
    driver: ExecutionDriver, plan: RetiredFinalizationPlan,
    old: RecoveryBinding, current: RecoveryBinding,
) -> bool:
    from sqlalchemy import or_, select

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellOperation
    from yleum_api.services.generation.expired_native_finalization import _owns_retired_repair_entry
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_quiescence import _provider_stopped
    from yleum_api.services.generation.recovery_reconciliation import ToolEffectCursor
    from yleum_api.services.generation.recovery_store import _locked_owner
    from yleum_api.services.generation_deadline import is_restoration_adaptation
    from yleum_api.services.project_cells import _stored_request_payload

    state, context = driver.state, driver.context
    if (
        not isinstance(state, RecoveryStateV3) or state.clock.stage != "repair"
        or state.phase != "working" or state.continuation_claim is None
        or state.unresolved_operation_ids
        or state.execution.stage != "finalization" or state.execution.substep != "proof_wait"
        or state.execution.physical_state != "released"
        or state.execution.release_operation_id is None
        or state.execution.provider_effect_id is not None or state.context_ref is None
        or state.context_ref != state.execution.execution_context_ref
        or context.get("active_frame") is not None or context.get("active_tool") is not None
        or "native_outer" in context or "legacy_outer" in context
        or not _same_retired_candidate(current, old)
    ):
        return False

    pending = pending_role(driver)
    if (
        pending is None or pending.operation_id.int == 0
        or pending.role not in {"bootstrap", "fast_check"}
        or pending.return_substep != "checkpoint_ready"
    ):
        return False
    cursor = ToolEffectCursor(
        state.continuation_claim, state.state_revision, state.execution.logical_step_seq,
        f"role:{pending.operation_id}", reconcile_only=True,
    )
    if state.execution.pending_effect_id != cursor.root_effect_id:
        return False

    calls, entry = state.execution.budget.repair_calls, pending_source_repair(driver)
    if plan.catalog_gap_key is None:
        if (
            calls != 2 or entry is None or entry.status != "complete"
            or not expired_repair_proof_only(driver)
        ):
            return False
    elif (
        not _same_retired_candidate(old, plan.proof.binding)
        or not ((calls == 2 and entry is None) or (
            calls == 3 and entry is not None and entry.status == "active"
            and _owns_retired_repair_entry(entry, plan)
        ))
    ):
        return False

    owner = state.identity
    await _require_session_owner(owner.run_id)
    async with driver.factory() as session:
        run, project = await _locked_owner(
            session, run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
        )
        if (
            recovery_state(run) != state or run.prompt_hash != owner.prompt_hash
            or project.template != "max_miniapp" or is_restoration_adaptation(run)
            or project.current_snapshot_id != owner.accepted_snapshot_id
            or run.execution_backend != "worker" or run.response_mode not in {"build", "edit"}
        ):
            return False
        release = await session.get(ProjectCellOperation, state.execution.release_operation_id)
        if release is None or _stored_request_payload(release) != {"require_environment_proof": {
            "source_sha256": old.source_sha256, "environment_sha256": old.environment_sha256,
        }}:
            return False

        effects = GenerationRecoveryEffect
        physical_id = uuid5(cursor.root_effect_id, "physical:0")
        if await session.scalar(select(effects.effect_id).where(or_(
            effects.effect_id == physical_id,
            effects.operation_id == pending.operation_id,
            (effects.run_id == owner.run_id)
            & (effects.logical_step_seq >= cursor.logical_step_seq),
        )).limit(1)) is not None:
            # Issued or conflicting effects stay with the normal receipt reader.
            return False
        activity = ProjectCellActivityLease
        if await session.scalar(select(activity.operation_id).where(or_(
            activity.operation_id == pending.operation_id,
            (
                (activity.workspace_id == state.execution.control_workspace_id)
                | (activity.generation_run_id == owner.run_id)
            ) & (
                activity.finished_at.is_(None) | (activity.fencing_epoch >= current.fencing_epoch)
            ),
        )).limit(1)) is not None or not await _provider_stopped(session, run):
            return False
        if await session.get(ProjectCellOperation, pending.operation_id) is not None:
            return False

    # The run advisory lock excludes new command intent until the checkpoint CAS.
    await _require_rebound_environment(driver, old, current)
    await _read_released_environment(
        lambda: _finish_role_cursor(driver, pending, cold=True, expected_binding=current),
        hold_lease=True,
    )
    return True


async def _observe_released_candidate(
    driver: ExecutionDriver, original: RecoveryBinding, *, hold_lease: bool,
) -> RecoveryBinding:
    try:
        current, _ = await _read_released_environment(
            driver.snapshot, hold_lease=hold_lease, allow_prepare=True,
        )
    except OrchestratorMachineInventoryUnavailable as error:
        if (
            driver.prepare_environment is None
            or original.workspace_id != str(driver.state.execution.control_workspace_id)
            or error.workspace_revision != original.source_sha256
        ):
            raise
        await _require_reacquired_lease(driver)
        try:
            await _prepare_owned_environment(driver, error)
        except OrchestratorUnavailable:
            if not hold_lease:
                raise
            # Observe the same lease after a lost prepare response.
        current, _ = await _read_released_environment(driver.snapshot, hold_lease=hold_lease)
    return current


async def _armed_retirement_attempt(driver: ExecutionDriver, state: RecoveryStateV3) -> UUID | None:
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        load_expired_provider_attachment,
        recovery_state,
    )

    await _require_session_owner(state.identity.run_id)
    async with driver.factory() as session:
        run = await session.get(GenerationRun, state.identity.run_id)
        if run is None:
            raise RecoveryContractError("expired repair run is unavailable")
        attachment = load_expired_provider_attachment(run)
        if attachment is None:
            return None
        if (
            run.status != "running" or recovery_state(run) != state
            or not attachment.matches_claim(state)
        ):
            raise RecoveryContractError("expired provider attachment lost its claimed cursor")
        return attachment.attempt_id


def _pending_retirement_compiler(
    driver: ExecutionDriver, *, phase: Literal["working", "waiting_dependency"] = "working",
) -> bool:
    completed = driver.context.get("completed", {})
    if type(completed) is not dict or _RETIRED_NATIVE_REPAIR_KEY not in completed:
        return False
    receipt = _RetiredNativeRepair.model_validate_json(
        json.dumps(completed[_RETIRED_NATIVE_REPAIR_KEY], allow_nan=False),
    )
    state = driver.state
    cursor = state.execution
    repair = pending_source_repair(driver)
    if (
        not isinstance(state, RecoveryStateV3) or state.binding != receipt.binding
        or state.phase != phase or state.continuation_claim is None
        or state.context_ref is None or cursor.execution_context_ref != state.context_ref
        or cursor.physical_state != "active" or cursor.stage != "finalization"
        or str(cursor.control_workspace_id) != receipt.binding.workspace_id
        or cursor.control_fencing_epoch != receipt.binding.fencing_epoch
        or driver.runtime_control != (cursor.control_workspace_id, cursor.control_fencing_epoch)
        or cursor.logical_step_seq < receipt.logical_step_seq
        or cursor.provider_effect_id is not None
        or cursor.substep not in {"checkpoint_ready", "proof_wait"}
        or driver.context.get("active_frame") is not None
        or repair is None or repair.status != "complete" or repair.result is None
        or repair.result.get("stop_reason") != _RETIRED_NATIVE_REPAIR_STOP
    ):
        raise RecoveryContractError("retired repair lost its compiler checkpoint")
    if cursor.substep == "checkpoint_ready":
        if cursor.pending_effect_id is not None:
            raise RecoveryContractError("retired repair still has a pending effect")
    else:
        from yleum_api.services.generation.recovery_reconciliation import ToolEffectCursor

        role = pending_role(driver)
        if (
            role is None or role.role not in {"bootstrap", "fast_check"}
            or role.return_substep != "checkpoint_ready"
        ):
            raise RecoveryContractError("retired repair lost its compiler role")
        owned = ToolEffectCursor(
            state.continuation_claim, state.state_revision, cursor.logical_step_seq,
            f"role:{role.operation_id}",
        )
        if cursor.pending_effect_id != owned.root_effect_id:
            raise RecoveryContractError("retired repair compiler role has lost its effect")
    return True


def retain_retired_attachment_on_wait(driver: ExecutionDriver, handoff: GenerationYielded) -> bool:
    from yleum_api.services.generation.expired_native_finalization import retired_finalization_plan

    state = getattr(driver, "state", None)
    if (
        not isinstance(state, RecoveryStateV3) or state.phase != "waiting_dependency"
        or state.identity.run_id != handoff.run_id or state.attempt_seq != handoff.attempt_seq
        or state.checkpoint != handoff.checkpoint or handoff.phase != state.phase
        or state.next_check_at is None
    ):
        return False
    plan = retired_finalization_plan(driver)
    if plan is None:
        return _pending_retirement_compiler(driver, phase="waiting_dependency")
    cursor = state.execution
    return (
        plan.catalog_gap_key is None
        and plan.repair_path is not None and state.binding == plan.proof.binding
        and cursor.stage == "finalization" and cursor.substep == "checkpoint_ready"
        and cursor.physical_state == "active" and cursor.budget.repair_calls in {1, 2}
        and cursor.pending_effect_id is None and cursor.provider_effect_id is None
        and driver.runtime_control == (cursor.control_workspace_id, cursor.control_fencing_epoch)
        and driver.context.get("active_frame") is None and driver.context.get("active_tool") is None
    )


async def pending_retired_compiler_role(
    driver: ExecutionDriver,
) -> tuple[PendingRole, ToolEffectCursor] | None:
    """Keep the original role cursor while reading its receipt or proving no intent."""
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        tool_effect_scope,
    )
    from yleum_api.services.generation.recovery_tool_receipts import require_command_reconciliation

    if not _pending_retirement_compiler(driver):
        raise RecoveryContractError("retired repair has no owning compiler checkpoint")
    pending = pending_role(driver)
    state = driver.state
    if pending is None:
        return None
    claim = state.continuation_claim
    assert claim is not None
    role = ToolEffectCursor(
        claim, state.state_revision, state.execution.logical_step_seq,
        f"role:{pending.operation_id}", reconcile_only=True,
    )
    owner = state.identity
    with tool_effect_scope(role):
        role.reconcile_only = await require_command_reconciliation(
            driver.factory, run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
            undispatched_operation_id=pending.operation_id,
        )
    return pending, role


async def rebind_continuation(
    driver: ExecutionDriver, *, allow_expired_retirement: bool = False,
) -> Literal[True] | None:
    """Reprove the retained tree under the new lease before any new logical effect.

    A source write whose terminal ACK was lost is reconciled by its own receipt
    first. Never checkpoint its old tree or accept a merely equal subset of files.
    True hands the candidate only to the retired repair compiler coordinator.
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
    from yleum_api.services.generation.expired_native_finalization import (
        restore_retired_finalization_cursor,
        retired_finalization_plan,
    )

    retired_plan = retired_finalization_plan(driver)
    if retired_plan is not None:
        if not allow_expired_retirement:
            raise RecoveryContractError(_RETIRED_REPAIR_COORDINATOR_REQUIRED)
        phase = await restore_retired_finalization_cursor(driver)
        if phase == "pre_entry":
            return True
        if phase is not None:
            return None
    elif _pending_retirement_compiler(driver):
        if not allow_expired_retirement:
            raise RecoveryContractError(_RETIRED_REPAIR_COORDINATOR_REQUIRED)
        return True
    if state.binding is None:
        await _bind_initial_candidate(driver)
        return None
    if state.execution.physical_state != "released":
        return None
    cursor = state.execution
    if (
        isinstance(state, RecoveryStateV3)
        and cursor.stage == "finalization" and cursor.substep == "response_wait"
        and cursor.budget.repair_calls == 1 and state.clock.repair_granted
    ):
        attempt_id = await _armed_retirement_attempt(driver, state)
        if attempt_id is not None:
            if not allow_expired_retirement:
                raise RecoveryContractError(_RETIRED_REPAIR_COORDINATOR_REQUIRED)
            # Generic wait after E11 would drop the E10 release pointer, so this
            # scoped transition reports failure without advancing its cursor.
            await retire_expired_native_source_repair(
                driver, released=state, armed_attempt_id=attempt_id,
            )
            return True
    if allow_expired_retirement and isinstance(state, RecoveryStateV3):
        closed_attempt = await _closed_expired_repair_attempt(driver, state)
        if closed_attempt is not None:
            await retire_expired_native_source_repair(
                driver, released=state, armed_attempt_id=closed_attempt, proof_only=True,
            )
            return None
    old = state.binding
    hold_lease = _can_rebind_release_environment(driver)
    try:
        current = await _observe_released_candidate(driver, old, hold_lease=hold_lease)
        if (
            current.workspace_id != old.workspace_id
            or current.fencing_epoch <= old.fencing_epoch
            or driver.runtime_control is None
            or driver.runtime_control[0] != state.execution.control_workspace_id
        ):
            raise RecoveryContractError("resumed candidate identity conflicts")
        if retired_plan is not None and await _rebind_undispatched_catalog_compiler_role(
            driver, retired_plan, old, current,
        ):
            return None
        same_environment = (
            current.schema_sha256 == old.schema_sha256
            and current.environment_sha256 == old.environment_sha256
        )
        if current.source_sha256 == old.source_sha256 and same_environment:
            await driver.save(state.execution, context=driver.context)
            return None
        if hold_lease and (
            current.source_sha256 != old.source_sha256 or current.schema_sha256 != old.schema_sha256
        ):
            raise _UnprovedReleaseEnvironment("released candidate source or schema changed")
        if await _rebind_release_environment(driver, old, current):
            return None
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
    except _UnprovedReleaseEnvironment as error:
        log.warning("generation.release_environment_unproved", run_id=str(state.identity.run_id),
                    reason=str(error))
        await driver.wait(needs_input=True)
    except Exception:
        await driver.wait()
    return None
