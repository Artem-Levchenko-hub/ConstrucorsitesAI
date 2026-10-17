"""Versioned immutable recovery records. Decoding never repairs unknown state."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity as GenerationProviderIdentity,
)

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Nonnegative = Annotated[int, Field(ge=0)]
Nonblank = Annotated[str, Field(min_length=1, pattern=r"\S")]
Stage = Literal["edit", "repair", "proof"]


class RecoveryContractError(ValueError):
    """Unsupported, corrupt or conflicting recovery state; retain the raw value."""


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class RecoveryIdentity(_Record):
    run_id: UUID
    project_id: UUID
    user_id: UUID
    prompt_hash: Sha256
    accepted_snapshot_id: UUID | None
    supersedes_run_id: UUID | None


class RecoveryBinding(_Record):
    source_sha256: Sha256
    schema_sha256: Sha256
    environment_sha256: Sha256
    workspace_id: Nonblank
    fencing_epoch: Annotated[int, Field(gt=0)]


class CheckpointRef(_Record):
    object_key: Nonblank
    sha256: Sha256


class IncompleteResponseBudget(_Record):
    total: Nonnegative = 0
    since_progress: Nonnegative = 0

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        if self.since_progress > self.total:
            raise ValueError("incomplete response count exceeds total")
        return self


class RecoveryRequirement(_Record):
    id: Nonblank
    text: Nonblank
    origin_prompt_sha256: Sha256
    check_kind: Literal[
        "named_behavior", "http", "browser", "db_persistence", "max_identity", "manual"
    ]
    required: bool


class RequirementWitness(_Record):
    """A receipt reference, never proof authority merely because it decodes.

    The receipt loader (Task 3) must validate server ownership and candidate
    binding before constructing a trusted witness. Model output is not evidence.
    Serialization deliberately loses in-process loader authority; persisted
    references must be loaded again before checking completeness.
    """

    requirement_id: Nonblank
    receipt_id: Nonblank
    source_sha256: Sha256
    schema_sha256: Sha256
    environment_sha256: Sha256
    passed: bool


class CompilerWitness(_Record):
    """Reference to an owned terminal check; only the server loader grants authority.

    A RED excerpt has no complete error count. Only a verified transition from
    RED to GREEN can establish an error reduction with this receipt version.
    """

    receipt_id: UUID
    operation_id: UUID
    run_id: UUID
    project_id: UUID
    user_id: UUID
    prompt_hash: Sha256
    binding: RecoveryBinding
    comparison_sha256: Sha256
    dimension: Literal["fast_check", "full_build"]
    passed: bool


class RecoveryObservation(_Record):
    kind: Literal["source", "diagnostics", "proof", "tool", "truncated", "dependency"]
    binding: RecoveryBinding
    requirement_id: str | None
    action_signature: str | None
    diagnostic_signatures: tuple[str, ...]
    witness: RequirementWitness | None
    compiler: CompilerWitness | None = None


class RecoveryDirective(_Record):
    action: Literal["continue", "nudge", "narrow", "reset_context", "yield", "needs_input"]
    reason: Nonblank


class ContinuationClaim(_Record):
    run_id: UUID
    token: UUID
    attempt_seq: Nonnegative
    worker_id: Nonblank
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("continuation expiry requires timezone")
        return value


class _WorkCounters(dict[Stage, int]):
    """Keep the specified dict wire shape without mutable nested record state."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> Any:
        raise TypeError("recovery work counters are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable


class RecoveryState(_Record):
    version: Literal[1] = 1
    identity: RecoveryIdentity
    binding: RecoveryBinding | None = None
    checkpoint: CheckpointRef | None = None
    context_ref: CheckpointRef | None = None
    cursor: Literal["bootstrap", "agent", "finalization"] = "bootstrap"
    requirements: tuple[RecoveryRequirement, ...] = ()
    witnesses: tuple[RequirementWitness, ...] = ()
    attempt_seq: Nonnegative = 0
    state_revision: Nonnegative = 0
    consecutive_no_progress: Nonnegative = 0
    active_work_ms: Nonnegative = 0
    stage_active_work_ms: dict[Stage, Nonnegative] = Field(
        default_factory=dict, validate_default=True,
    )
    phase: Literal[
        "working", "repairing", "waiting_dependency", "reconciling", "verifying", "needs_input"
    ] = "working"
    mode: Literal["normal", "diagnose", "narrow", "fresh_context", "blocked"] = "normal"
    next_check_at: datetime | None = None
    unresolved_operation_ids: tuple[UUID, ...] = ()
    tried_strategies: tuple[str, ...] = ()
    # Append-only observations retain failed approaches across strategy/context
    # changes. Accepted compiler references are separate from untrusted input.
    observations: tuple[RecoveryObservation, ...] = ()
    compiler_witnesses: tuple[CompilerWitness, ...] = ()
    incomplete_response_budget: IncompleteResponseBudget = Field(
        default_factory=IncompleteResponseBudget,
    )
    continuation_claim: ContinuationClaim | None = None

    @field_validator("version", mode="before")
    @classmethod
    def require_exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported recovery version")
        return value

    @field_validator("stage_active_work_ms")
    @classmethod
    def freeze_work(cls, value: dict[Stage, int]) -> dict[Stage, int]:
        return _WorkCounters(value)

    @field_validator("next_check_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("recovery next check requires timezone")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.cursor != "bootstrap" and any(
            item is None for item in (self.binding, self.checkpoint, self.context_ref)
        ):
            raise ValueError("product work requires binding, checkpoint and durable context")
        if self.checkpoint is not None and self.binding is None:
            raise ValueError("checkpoint requires candidate binding")
        ids = [item.id for item in self.requirements]
        if len(ids) != len(set(ids)) or any(
            item.origin_prompt_sha256 != self.identity.prompt_hash for item in self.requirements
        ):
            raise ValueError("requirements must have unique IDs and original prompt scope")
        if any(witness.requirement_id not in ids for witness in self.witnesses):
            raise ValueError("witness has no recovery requirement")
        if any(
            (w.run_id, w.project_id, w.user_id, w.prompt_hash)
            != (self.identity.run_id, self.identity.project_id,
                self.identity.user_id, self.identity.prompt_hash)
            for w in self.compiler_witnesses
        ):
            raise ValueError("compiler witness belongs to another request")
        if self.continuation_claim is not None and (
            self.continuation_claim.run_id != self.identity.run_id
            or self.continuation_claim.attempt_seq != self.attempt_seq
        ):
            raise ValueError("continuation claim belongs to another run or attempt")
        return self


class ReconciliationResult(_Record):
    outcome: Literal["settled", "waiting", "conflict"]
    state: RecoveryState
    reason: Nonblank


class ExecutionBudget(_Record):
    """Arithmetic only: never a serialized collector or proof authority."""

    steps: Nonnegative = 0
    provider_attempts: Nonnegative = 0
    repair_calls: Nonnegative = 0
    absolute_deadline: datetime | None = None

    @field_validator("absolute_deadline")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("execution deadline requires timezone")
        return value


class ExecutionCursor(_Record):
    schema_version: Literal[1] = 1
    stage: Literal["bootstrap", "agent", "finalization"]
    substep: Nonblank
    logical_step_seq: Nonnegative = 0
    next_tool_index: Nonnegative = 0
    lease_cycle_seq: Nonnegative = 0
    execution_context_ref: CheckpointRef | None = None
    pending_effect_id: UUID | None = None
    provider_effect_id: UUID | None = None
    response_sha256: Sha256 | None = None
    finalization_checkpoint_id: UUID | None = None
    release_operation_id: UUID | None = None
    control_workspace_id: UUID | None = None
    control_fencing_epoch: Annotated[int, Field(gt=0)] | None = None
    physical_state: Literal["no_effects", "active", "release_pending", "released", "unknown"]
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_exact_schema(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported execution cursor schema")
        return value

    @model_validator(mode="after")
    def validate_boundary(self) -> Self:
        steps = {
            "bootstrap": {
                "runtime_prepare",
                "stack_prepare",
                "prompt_prepare",
                "starter_apply",
                "agent_entry",
                "proof_wait",
            },
            "agent": {
                "request_ready",
                "proof_wait",
                "response_wait",
                "response_validated",
                "tool_ready",
                "tool_wait",
                "response_consumed",
            },
            "finalization": {
                "checkpoint_ready",
                "proof_wait",
                "proof_resume",
                "terminal_gate",
                "publication_pending",
                "publication_committed",
                "response_wait",
                "response_validated",
                "tool_ready",
                "tool_wait",
                "response_consumed",
            },
        }
        if self.substep not in steps[self.stage]:
            raise ValueError("execution substep does not belong to its stage")
        initial = (
            self.stage == "bootstrap"
            and self.substep == "runtime_prepare"
            and self.logical_step_seq == 0
        )
        if self.physical_state == "no_effects" and (
            not initial
            or self.pending_effect_id
            or self.provider_effect_id
            or self.release_operation_id
        ):
            raise ValueError("no-effect boundary requires initial bootstrap")
        if not initial and self.execution_context_ref is None:
            raise ValueError("continuation requires durable executable context")
        if (self.control_workspace_id is None) != (self.control_fencing_epoch is None):
            raise ValueError("control binding requires both workspace and fence")
        if self.physical_state in {"release_pending", "released"} and (
            self.release_operation_id is None or self.control_workspace_id is None
        ):
            raise ValueError("physical handoff requires its owning operation and control binding")
        if (self.substep in {"publication_pending", "publication_committed"}
            and self.pending_effect_id is None):
            raise ValueError("publication requires its original logical effect")
        if (
            self.substep
            in {
                "response_wait",
                "response_validated",
                "tool_ready",
                "tool_wait",
                "response_consumed",
            }
            and self.provider_effect_id is None
        ):
            raise ValueError("response boundary requires the original provider effect")
        return self


class RecoveryStateV2(RecoveryState):
    version: Literal[2] = 2  # type: ignore[assignment]
    execution: ExecutionCursor

    @field_validator("version", mode="before")
    @classmethod
    def require_exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("unsupported execution recovery version")
        return value

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.execution.stage != self.cursor:
            raise ValueError("execution and recovery cursors conflict")
        return self


class RecoveryClock(_Record):
    """Durable arithmetic, not a serialized clock or progress authority."""

    version: Literal[1] = 1
    stage: Stage = "edit"
    segment_seq: Nonnegative = 0
    window_ms: Annotated[int, Field(gt=0)]
    segment_used_ms: Nonnegative = 0
    segment_deadline: datetime
    interval_id: UUID
    interval_open: bool = True
    interval_charged_ms: Nonnegative = 0
    repair_granted: bool = False
    consumed_requirement_ids: tuple[Nonblank, ...] = ()
    dependency_failures: Nonnegative = 0
    wait_dependency: Literal["credentials", "funds", "provider"] | None = None

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported work clock")
        return value

    @field_validator("segment_deadline")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("work clock requires an absolute deadline")
        return value

    @model_validator(mode="after")
    def unique_credit(self) -> Self:
        if len(self.consumed_requirement_ids) != len(set(self.consumed_requirement_ids)):
            raise ValueError("semantic credit cannot repeat")
        return self


class RecoveryStateV3(RecoveryStateV2):
    version: Literal[3] = 3  # type: ignore[assignment]
    clock: RecoveryClock

    @field_validator("next_check_at")
    @classmethod
    def canonical_due(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("version", mode="before")
    @classmethod
    def require_exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 3:
            raise ValueError("unsupported clock recovery version")
        return value

    @model_validator(mode="after")
    def valid_clock_scope(self) -> Self:
        required = {
            r.id for r in self.requirements if r.required and r.check_kind == "named_behavior"
        }
        if not set(self.clock.consumed_requirement_ids) <= required:
            raise ValueError("clock credit has no frozen functional requirement")
        if sum(self.stage_active_work_ms.values()) != self.active_work_ms:
            raise ValueError("work clock stage accounting conflicts")
        if self.clock.interval_charged_ms > self.active_work_ms:
            raise ValueError("work clock interval exceeds known work")
        return self


def decode_recovery(raw: object) -> RecoveryState | None:
    """Only an absent value returns None; unknown/corrupt JSON is never replaced."""
    if raw is None:
        return None
    if (
        type(raw) is not dict or type(raw.get("version")) is not int
        or raw["version"] not in {1, 2, 3}
    ):
        raise RecoveryContractError("unsupported or missing recovery version")
    try:
        # JSON mode accepts UUID/datetime/tuple wire representations while strict
        # fields still refuse coercions such as numeric strings and bool counters.
        models: dict[int, type[RecoveryState]] = {
            1: RecoveryState, 2: RecoveryStateV2, 3: RecoveryStateV3,
        }
        model = models[raw["version"]]
        return model.model_validate_json(json.dumps(raw, allow_nan=False))
    except (ValidationError, TypeError, ValueError, OverflowError) as exc:
        raise RecoveryContractError("corrupt recovery contract") from exc
