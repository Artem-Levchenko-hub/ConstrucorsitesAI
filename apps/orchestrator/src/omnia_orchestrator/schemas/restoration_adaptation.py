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


class RestorationAdaptationCleanup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    generation_run_id: UUID
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0)
    proof_digest: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


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
    # латинские слова: фразы правил именно такие, а значениям из чужой базы в
    # такое поле не пролезть.
    reason_detail: str | None = Field(
        default=None, max_length=120, pattern=r"^[a-z][a-z0-9 ]*$"
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
