"""Owner-facing restoration state and strictly validated controller evidence."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from omnia_api.schemas.snapshot import SnapshotPublic

RestoreState = Literal[
    "preparing",
    "checking",
    "ready",
    "needs_changes",
    "applying",
    "completed",
    "cancelled",
    "failed",
    "reconciling",
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RuntimeSourceBindingV2(BaseModel):
    """Secret-free controller observation binding a candidate to its live source."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[2]
    serving_route_digest: Sha256
    serving_release_digest: Sha256
    controller_resource_digest: Sha256
    controller_incarnation_digest: Sha256
    controller_generation_digest: Sha256
    provider_digest: Sha256
    source_artifact_digest: Sha256
    database_identity_digest: Sha256
    database_schema_digest: Sha256
    database_role_binding_digest: Sha256
    database_system_identifier: str | None = Field(default=None, max_length=128)
    database_export_digest: Sha256
    source_business_inventory_digest: Sha256
    candidate_business_inventory_digest: Sha256
    source_technical_inventory_digest: Sha256
    candidate_technical_inventory_digest: Sha256
    candidate_artifact_digest: Sha256

    def digest(self) -> str:
        wire = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(wire, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class RuntimeSourceBindingV3(BaseModel):
    """V2 live identity plus the verified database handling strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[3]
    serving_route_digest: Sha256
    serving_release_digest: Sha256
    controller_resource_digest: Sha256
    controller_incarnation_digest: Sha256
    controller_generation_digest: Sha256
    provider_digest: Sha256
    source_artifact_digest: Sha256
    database_identity_digest: Sha256
    database_schema_digest: Sha256
    database_role_binding_digest: Sha256
    database_system_identifier: str | None = Field(default=None, max_length=128)
    database_export_digest: Sha256
    source_business_inventory_digest: Sha256
    candidate_business_inventory_digest: Sha256
    source_technical_inventory_digest: Sha256
    candidate_technical_inventory_digest: Sha256
    candidate_artifact_digest: Sha256
    database_strategy: Literal["preserve_current", "replace_verified_empty"]
    witness_digest: Sha256 | None = None
    target_database_artifact_digest: Sha256 | None = None

    @model_validator(mode="after")
    def require_strategy_evidence(self) -> Self:
        evidence = (self.witness_digest, self.target_database_artifact_digest)
        if self.database_strategy == "replace_verified_empty" and None in evidence:
            raise ValueError("replace_verified_empty requires both evidence digests")
        if self.database_strategy == "preserve_current" and any(evidence):
            raise ValueError("preserve_current must not carry replacement evidence")
        return self

    def digest(self) -> str:
        wire = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(wire, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


RuntimeSourceBinding = Annotated[
    RuntimeSourceBindingV2 | RuntimeSourceBindingV3,
    Field(discriminator="version"),
]


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_version_id: UUID
    expected_draft_snapshot_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=128)
    # Legacy clients omit this and keep the reviewed/manual apply step. The MAX
    # one-click flow explicitly opts into the durable server-owned continuation.
    execution_policy: Literal["manual", "automatic_when_safe"] = "manual"


class RestoreApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_revision: int = Field(ge=1, strict=True)
    expected_draft_snapshot_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=128)


class _ReportPart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InventoryObject(_ReportPart):
    """Counts only; a report never carries row contents."""

    object: str = Field(max_length=300)
    kind: Literal["table", "partitioned_table", "view", "materialized_view", "foreign_table",
                  "large_objects"]
    classification: Literal["business", "technical", "derived", "unknown"]
    presence: Literal["empty", "present", "unknown"]
    row_count: int | None = Field(default=None, ge=0)
    count_kind: Literal["exact", "estimate", "not_measured"]
    diagnostic: str | None = Field(default=None, max_length=120)


class InventoryReport(_ReportPart):
    presence: Literal["empty", "present", "unknown"]
    coverage: Literal["complete", "partial", "unavailable"]
    schema_analysis: Literal["complete", "partial", "unavailable"]
    objects: list[InventoryObject] = Field(default_factory=list, max_length=500)
    observed_on: Literal["source", "candidate_copy"]


class CompatibilityCheck(_ReportPart):
    code: str = Field(max_length=80)
    status: Literal["compatible", "incompatible", "unknown", "not_applicable"]
    severity: Literal["blocking", "warning", "info"]
    operation: str = Field(max_length=200)
    object: str = Field(max_length=300)
    evidence: Literal["structural_rule", "observed_catalog", "source_scan"]
    explanation: str = Field(max_length=600)
    resolution: str | None = Field(default=None, max_length=600)


class Capability(_ReportPart):
    method: str = Field(max_length=10)
    path: str = Field(max_length=300)


class CapabilityDiff(_ReportPart):
    lost: list[Capability] = Field(default_factory=list, max_length=500)
    restored: list[Capability] = Field(default_factory=list, max_length=500)


class RestoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, strict=True)
    mode: Literal["exact", "adapted"]
    # Informational observation at preparation, never permission to reset data.
    database_state: Literal["empty", "present", "unknown"] = "unknown"
    changes: list[str] = Field(default_factory=list)
    retained_data: list[str] = Field(default_factory=list)
    unavailable_features: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    # Format 2 (structured evidence). Absent on older reports: that means
    # "not measured", never "no data" and never "compatible".
    format: Literal[1, 2] = 1
    inventory: InventoryReport | None = None
    checks: list[CompatibilityCheck] = Field(default_factory=list, max_length=1000)
    capabilities: CapabilityDiff | None = None

    @model_validator(mode="after")
    def consistent_presence(self) -> Self:
        if self.inventory is not None and self.inventory.presence != self.database_state:
            raise ValueError("database_state must match the structured inventory")
        return self


class RestoreOperation(BaseModel):
    id: UUID
    project_id: UUID
    target: Literal["draft"] = "draft"
    source_version_id: UUID
    source_snapshot_id: UUID
    base_draft_snapshot_id: UUID
    state: RestoreState
    phase: str
    updated_at: datetime
    revision: int
    candidate_id: UUID | None
    execution_policy: Literal["manual", "automatic_when_safe"]
    selected_branch: Literal["exact", "adaptive"] | None
    adaptation_run_id: UUID | None
    report: RestoreReport | None
    can_apply: bool
    can_cancel: bool
    applied_version: UUID | None
    applied_snapshot_id: UUID | None
    applied_snapshot: SnapshotPublic | None = None
    error: str | None


class RestorationsPage(BaseModel):
    items: list[RestoreOperation]
    enabled: bool


class RuntimeObserved(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: UUID
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    applied: Literal[True]
    fencing_epoch: int = Field(ge=0, strict=True)
    binding_digest: Sha256 | None = None
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("applied", mode="before")
    @classmethod
    def exact_applied_flag(cls, value: object) -> object:
        if value is not True:
            raise ValueError("runtime activation flag must be true")
        return value


class RuntimeRecoveryObserved(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: UUID
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    applied: Literal[False]
    safe_to_release: Literal[True]
    fencing_epoch: int = Field(ge=0, strict=True)
    binding_digest: Sha256 | None = None
    rejected_before_effect: Literal[True] | None = None
    superseded_before_effect: Literal[True] | None = None
    retained_source_fencing_epoch: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("applied", "safe_to_release", mode="before")
    @classmethod
    def exact_recovery_flags(cls, value: object, info: ValidationInfo) -> object:
        expected = info.field_name == "safe_to_release"
        if value is not expected:
            raise ValueError("runtime recovery flags must be exact booleans")
        return value

    @field_validator("rejected_before_effect", "superseded_before_effect", mode="before")
    @classmethod
    def exact_optional_proof_flags(cls, value: object) -> object:
        if value is not None and value is not True:
            raise ValueError("runtime recovery proof flags must be exact booleans")
        return value

    @model_validator(mode="after")
    def require_complete_pre_effect_rejection(self) -> Self:
        if self.superseded_before_effect is True and (
            self.rejected_before_effect is not None
            or self.retained_source_fencing_epoch is not None
        ):
            raise ValueError("superseded receipt cannot assert a retained source fence")
        if self.superseded_before_effect is None and (
            (self.rejected_before_effect is None)
            != (self.retained_source_fencing_epoch is None)
        ):
            raise ValueError("pre-effect rejection receipt must be complete")
        if (
            self.retained_source_fencing_epoch is not None
            and self.retained_source_fencing_epoch >= self.fencing_epoch
        ):
            raise ValueError("retained source fence must precede the rejected fence")
        return self


class RuntimeRestoration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    state: RestoreState
    phase: str
    revision: int = Field(ge=1, strict=True)
    candidate_id: UUID | None = None
    report: RestoreReport | None = None
    error: str | None = None
    can_apply: bool = Field(strict=True)
    can_cancel: bool = Field(strict=True)
    observed: RuntimeObserved | RuntimeRecoveryObserved | None = None
    binding: RuntimeSourceBinding | None = None
    binding_digest: Sha256 | None = None

    @model_validator(mode="after")
    def require_observed_completion(self) -> Self:
        if (self.binding is None) != (self.binding_digest is None):
            raise ValueError("runtime source binding must be complete")
        if self.binding is not None and self.binding.digest() != self.binding_digest:
            raise ValueError("runtime source binding digest mismatch")
        if (self.state == "completed") != isinstance(self.observed, RuntimeObserved):
            raise ValueError("only observed runtime activation may complete restoration")
        if isinstance(self.observed, RuntimeRecoveryObserved) and self.state != "failed":
            raise ValueError("verified runtime recovery requires failed state")
        if self.state == "ready" and (
            self.report is None or self.candidate_id is None or self.report.blockers
        ):
            raise ValueError("ready restoration requires a candidate and an unblocked report")
        return self
