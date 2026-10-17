"""Internal contracts for an operation-bound adaptive-restoration workspace."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256 = str


class RestorationAdaptationPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    source_workspace_revision: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: UUID
    base_draft_snapshot_id: UUID
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    adaptation_bundle_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


class RestorationAdaptationWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ready"] = "ready"
    source_workspace_id: UUID
    candidate_workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    candidate_fencing_epoch: int = Field(gt=0)
    source_database_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    proof_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: dict[str, object]
    request_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RestorationAdaptationCleanup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    generation_run_id: UUID
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0)
    proof_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_cycle: int | None = Field(default=None, ge=0)
    recovery_receipt_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def recovery_pair(self) -> RestorationAdaptationCleanup:
        if (self.recovery_cycle is None) != (self.recovery_receipt_digest is None):
            raise ValueError("recovery cleanup requires both cycle and receipt")
        return self


class RestorationAdaptationProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0)
    preparation_proof_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    source_database_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_workspace_revision: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_proof_key: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_artifact_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    proof_attempt: int = Field(gt=0)

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


class RestorationAdaptationProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["proof_ready", "migration_required", "source_changed"]
    reason_code: Literal[
        "candidate_schema_changed",
        "candidate_business_data_changed",
        "candidate_technical_data_changed",
        "probe_rehearsal_failed",
        "probe_readiness_failed",
        "probe_owner_read_failed",
        "probe_owner_mutation_failed",
        "probe_owner_reload_failed",
        "probe_cross_owner_denial_failed",
        "probe_unauthenticated_denial_failed",
        "probe_manifest_invalid",
        "candidate_changed_during_rehearsal",
        "source_code_changed",
        "source_database_changed",
    ] | None = None
    # Какое именно правило нарушено, когда причина сама по себе слишком общая.
    # Годность манифеста проверки решается четырнадцатью правилами, и без имени
    # правила починка идёт вслепую. Поле нарочно узкое — только строчные
    # латинские слова, имена колонок и точка с запятой между претензиями: фразы
    # правил именно такие, а значениям из
    # чужой базы в такое поле не пролезть — имя колонки это схема, а не данные,
    # и оно и так уже звучит в отчёте для владельца.
    reason_detail: str | None = Field(
        default=None, max_length=400, pattern=r"^[a-z][a-z0-9_; ]*$"
    )
    source_workspace_id: UUID
    candidate_workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    candidate_fencing_epoch: int = Field(gt=0)
    proof_attempt: int = Field(gt=0)
    source_workspace_revision: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_workspace_revision: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_proof_key: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_artifact_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_source_manifest_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    probe_contract_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    probe_rehearsal_digest: Sha256 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    probe_rehearsal_database_digest: Sha256 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_database_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_database_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    source_schema_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_schema_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    source_business_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_business_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    source_technical_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_technical_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    proof_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: dict[str, object]

    @model_validator(mode="after")
    def state_matches_reason(self) -> RestorationAdaptationProof:
        if (self.state == "proof_ready") != (self.reason_code is None):
            raise ValueError("proof result reason does not match state")
        if self.state == "proof_ready" and (
            self.probe_rehearsal_digest is None
            or self.probe_rehearsal_database_digest is None
        ):
            raise ValueError("proof-ready result requires a successful probe rehearsal")
        return self


class RestorationAdaptationOwnerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    state: Literal["active", "terminal"]


class RestorationAdaptationRecoveryIdentity(BaseModel):
    """Immutable original preparation identity; transition IDs are separate effect IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    transition_id: UUID
    workspace_id: UUID
    generation_run_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    candidate_workspace_id: UUID
    request_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def distinct_transition(self) -> RestorationAdaptationRecoveryIdentity:
        if self.transition_id == self.operation_id:
            raise ValueError("recovery transition must differ from preparation operation")
        return self


class RestorationAdaptationRecoveryHold(RestorationAdaptationRecoveryIdentity):
    expected_cycle: int = Field(ge=0)
    control_fencing_epoch: int = Field(gt=0)
    candidate_fencing_epoch: int = Field(gt=0)


class RestorationAdaptationRecoveryRebind(RestorationAdaptationRecoveryIdentity):
    expected_cycle: int = Field(ge=0)
    expected_hold_receipt_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    new_control_fencing_epoch: int = Field(gt=0)


class RestorationAdaptationRecoveryReceipt(RestorationAdaptationRecoveryIdentity):
    state: Literal["hold_pending", "held", "rebinding", "resumable"]
    cycle: int = Field(ge=0)
    original_control_fencing_epoch: int = Field(gt=0)
    current_control_fencing_epoch: int = Field(gt=0)
    original_candidate_fencing_epoch: int = Field(gt=0)
    current_candidate_fencing_epoch: int = Field(gt=0)
    barrier_operation_id: UUID
    barrier_receipt_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_ref_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    physical_incarnation_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    physical_proof_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    restore_operation_id: UUID | None = None
    terminal_receipt_digest: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def terminal_requires_physical_proof(self) -> RestorationAdaptationRecoveryReceipt:
        if self.state in {"held", "resumable"} and any(value is None for value in (
            self.barrier_receipt_digest, self.checkpoint_ref_digest,
            self.physical_incarnation_digest, self.physical_proof_digest,
            self.terminal_receipt_digest,
        )):
            raise ValueError("terminal recovery receipt requires complete physical proof")
        if self.state == "resumable" and self.restore_operation_id is None:
            raise ValueError("resumable recovery receipt requires confirmed restore")
        return self
