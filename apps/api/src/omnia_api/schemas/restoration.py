"""Owner-facing restoration state and strictly validated controller evidence."""

import hashlib
import json
import math
import re
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
    "adapting",
    "applying",
    "completed",
    "cancelled",
    "failed",
    "reconciling",
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_ACTIVATION_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_ACTIVATION_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def canonical_activation_digest(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


JsonScalar = str | int | float | bool | None


class ActivationBusinessWitness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entity: str = Field(min_length=1, max_length=63)
    id_column: str = Field(min_length=1, max_length=63)
    owner_column: str = Field(min_length=1, max_length=63)
    value_column: str = Field(min_length=1, max_length=63)
    create_values: dict[str, JsonScalar] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def bounded_identifiers_and_values(self) -> Self:
        names = (
            self.entity,
            self.id_column,
            self.owner_column,
            self.value_column,
            *self.create_values,
        )
        if any(_ACTIVATION_TABLE_RE.fullmatch(value) is None for value in names):
            raise ValueError("invalid activation business witness identifier")
        if len(json.dumps(self.create_values, ensure_ascii=False).encode("utf-8")) > 4096:
            raise ValueError("activation business witness payload is too large")
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in self.create_values.values()
        ):
            raise ValueError("activation business witness payload is not finite")
        return self


class ActivationProbePointers(BaseModel):
    """Fixed probe response locations; arbitrary controller JSON pointers are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    items: Literal["/items"] = "/items"
    item: Literal["/item"] = "/item"
    item_id: Literal["/item/id"] = "/item/id"
    owner_id: Literal["/item/ownerId"] = "/item/ownerId"
    entity: Literal["/item/entity"] = "/item/entity"
    marker: Literal["/item/marker"] = "/item/marker"
    phase: Literal["/item/phase"] = "/item/phase"
    contract_digest: Literal["/probeContractDigest"] = "/probeContractDigest"


class ActivationBusinessProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    endpoint: str = Field(min_length=1, max_length=240)
    data_contract_digest: Sha256
    witnesses: tuple[ActivationBusinessWitness, ...] = Field(min_length=1, max_length=32)
    max_payload_bytes: int = Field(ge=256, le=8192, strict=True)
    pointers: ActivationProbePointers = Field(default_factory=ActivationProbePointers)
    contract_digest: Sha256

    @model_validator(mode="after")
    def valid_contract(self) -> Self:
        if (
            not self.endpoint.startswith("/api/")
            or self.endpoint.startswith(("/api/omnia/", "/api/max/"))
            or self.endpoint in {"/api/omnia", "/api/max"}
            or any(token in self.endpoint for token in ("?", "#", "%", "\\", "//"))
            or ".." in self.endpoint.split("/")
            or self.endpoint.endswith("/")
            or any(
                re.fullmatch(r"[A-Za-z0-9_-]+", segment) is None
                for segment in self.endpoint.split("/")[1:]
            )
        ):
            raise ValueError("activation business probe endpoint must be target-owned")
        if len({item.entity for item in self.witnesses}) != len(self.witnesses):
            raise ValueError("duplicate activation business probe entity")
        payload = self.model_dump(mode="json", exclude={"contract_digest"})
        if canonical_activation_digest(payload) != self.contract_digest:
            raise ValueError("activation business probe digest mismatch")
        return self


class RestorationAdaptationActivationOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0, strict=True)
    candidate_workspace_revision: Sha256
    proof_attempt: int = Field(gt=0, strict=True)
    proof_digest: Sha256


class RestorationAdaptationActivationOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["offered"] = "offered"
    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    activation_id: UUID
    expected_source_fencing_epoch: int = Field(gt=0, strict=True)
    target_fencing_epoch: int = Field(gt=0, strict=True)
    source_workspace_revision: Sha256
    source_code_volume: str = Field(min_length=1, max_length=255)
    live_database_volume: str = Field(min_length=1, max_length=255)
    live_database_identity_digest: Sha256
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0, strict=True)
    candidate_workspace_revision: Sha256
    candidate_artifact_digest: Sha256
    candidate_source_manifest_digest: Sha256
    candidate_files_digest: Sha256
    archive_digest: Sha256
    business_probe: ActivationBusinessProbe
    probe_contract_digest: Sha256
    probe_rehearsal_digest: Sha256
    probe_rehearsal_database_digest: Sha256
    proof_attempt: int = Field(gt=0, strict=True)
    proof_digest: Sha256
    offer_digest: Sha256

    @model_validator(mode="after")
    def valid_binding(self) -> Self:
        if self.target_fencing_epoch <= self.expected_source_fencing_epoch:
            raise ValueError("target fencing epoch must advance source fence")
        if self.candidate_workspace_id == self.workspace_id:
            raise ValueError("candidate workspace must be isolated")
        if any(
            _ACTIVATION_VOLUME_RE.fullmatch(value) is None
            for value in (self.source_code_volume, self.live_database_volume)
        ):
            raise ValueError("invalid activation volume identity")
        if self.probe_contract_digest != self.business_probe.contract_digest:
            raise ValueError("activation probe digest mismatch")
        payload = self.model_dump(mode="json", exclude={"offer_digest"})
        if canonical_activation_digest(payload) != self.offer_digest:
            raise ValueError("activation offer digest mismatch")
        return self


class RestorationAdaptationActivationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    offer: RestorationAdaptationActivationOffer
    planned_commit_sha: GitSha

    def activation_binding_payload(self) -> dict[str, object]:
        offer = self.offer
        return {
            "operation_id": str(offer.operation_id),
            "generation_run_id": str(offer.generation_run_id),
            "project_id": str(offer.project_id),
            "owner_id": str(offer.owner_id),
            "source_workspace_id": str(offer.workspace_id),
            "expected_source_fencing_epoch": offer.expected_source_fencing_epoch,
            "target_fencing_epoch": offer.target_fencing_epoch,
            "source_workspace_revision": offer.source_workspace_revision,
            "source_code_volume": offer.source_code_volume,
            "live_database_volume": offer.live_database_volume,
            "live_database_identity_digest": offer.live_database_identity_digest,
            "candidate_workspace_id": str(offer.candidate_workspace_id),
            "candidate_fencing_epoch": offer.candidate_fencing_epoch,
            "candidate_workspace_revision": offer.candidate_workspace_revision,
            "proof_digest": offer.proof_digest,
            "candidate_artifact_digest": offer.candidate_artifact_digest,
            "candidate_source_manifest_digest": offer.candidate_source_manifest_digest,
            "candidate_code_digest": offer.archive_digest,
            "business_probe": offer.business_probe.model_dump(mode="json"),
            "probe_rehearsal_digest": offer.probe_rehearsal_digest,
            "probe_rehearsal_database_digest": offer.probe_rehearsal_database_digest,
            "planned_commit_sha": self.planned_commit_sha,
            "activation_id": str(offer.activation_id),
        }

    def activation_digest(self) -> str:
        """Digest the immutable effect binding, excluding the digest field itself."""
        return canonical_activation_digest(self.activation_binding_payload())

    def digest(self) -> str:
        """Hash the complete command envelope independently from the effect binding."""
        return canonical_activation_digest(self)


class ActivationPreparedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_id: UUID
    fencing_epoch: int = Field(gt=0, strict=True)
    code_volume: str = Field(min_length=1, max_length=255)
    code_digest: Sha256
    database_volume: str = Field(min_length=1, max_length=255)
    database_identity_digest: Sha256


ActivationState = Literal[
    "intent",
    "target_prepared",
    "writers_stopping",
    "cancelling",
    "target_writers_admitted",
    "activated",
    "cancelled",
]


class RestorationAdaptationActivationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: ActivationState
    effects_admitted: bool = Field(strict=True)
    offer: RestorationAdaptationActivationOffer
    planned_commit_sha: GitSha
    activation_digest: Sha256
    fencing_epoch: int = Field(gt=0, strict=True)
    source_volume_identity: ActivationPreparedTarget
    target_volume_identity: ActivationPreparedTarget
    health_digest: Sha256 | None = None
    receipt_digest: Sha256

    @model_validator(mode="after")
    def valid_receipt(self) -> Self:
        admitted = self.state in {"target_writers_admitted", "activated"}
        if self.effects_admitted is not admitted:
            raise ValueError("activation state does not match admitted effects")
        if (self.state == "activated") != (self.health_digest is not None):
            raise ValueError("activation health proof does not match state")
        command = RestorationAdaptationActivationCommand(
            offer=self.offer, planned_commit_sha=self.planned_commit_sha
        )
        if self.activation_digest != command.activation_digest():
            raise ValueError("activation digest mismatch")
        expected_source = ActivationPreparedTarget(
            workspace_id=self.offer.workspace_id,
            fencing_epoch=self.offer.expected_source_fencing_epoch,
            code_volume=self.offer.source_code_volume,
            code_digest=self.offer.source_workspace_revision,
            database_volume=self.offer.live_database_volume,
            database_identity_digest=self.offer.live_database_identity_digest,
        )
        if self.source_volume_identity != expected_source:
            raise ValueError("activation source identity mismatch")
        if (
            self.target_volume_identity.workspace_id != self.offer.workspace_id
            or self.target_volume_identity.fencing_epoch != self.offer.target_fencing_epoch
            or self.target_volume_identity.code_digest != self.offer.archive_digest
            or self.target_volume_identity.database_volume != self.offer.live_database_volume
            or self.target_volume_identity.database_identity_digest
            != self.offer.live_database_identity_digest
            or self.fencing_epoch != self.offer.target_fencing_epoch
        ):
            raise ValueError("activation target identity mismatch")
        receipt = {
            "state": self.state,
            "effects_admitted": self.effects_admitted,
            "operation_id": str(self.offer.operation_id),
            "generation_run_id": str(self.offer.generation_run_id),
            "project_id": str(self.offer.project_id),
            "owner_id": str(self.offer.owner_id),
            "activation_id": str(self.offer.activation_id),
            "activation_digest": self.activation_digest,
            "proof_digest": self.offer.proof_digest,
            "fencing_epoch": self.fencing_epoch,
            "source_volume_identity": self.source_volume_identity.model_dump(mode="json"),
            "target_volume_identity": self.target_volume_identity.model_dump(mode="json"),
            "health_digest": self.health_digest,
        }
        if canonical_activation_digest(receipt) != self.receipt_digest:
            raise ValueError("activation receipt digest mismatch")
        return self


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
