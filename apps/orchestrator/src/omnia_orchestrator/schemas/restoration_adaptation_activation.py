"""Immutable contracts for activating a proof-ready adaptation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


JsonScalar = str | int | float | bool | None


def _digest_payload(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ActivationBusinessWitness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str = Field(min_length=1, max_length=63)
    id_column: str = Field(min_length=1, max_length=63)
    owner_column: str = Field(min_length=1, max_length=63)
    value_column: str = Field(min_length=1, max_length=63)
    create_values: dict[str, JsonScalar] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def bounded_identifiers_and_values(self) -> Self:
        for value in (
            self.entity,
            self.id_column,
            self.owner_column,
            self.value_column,
            *self.create_values,
        ):
            if _TABLE_RE.fullmatch(value) is None:
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
    """Fixed response envelope; arbitrary JSON pointers are never trusted."""

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
    """Normalized target-owned probe contract bound to declared business tables."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    endpoint: str = Field(min_length=1, max_length=240)
    data_contract_digest: Sha256
    witnesses: tuple[ActivationBusinessWitness, ...] = Field(min_length=1, max_length=32)
    max_payload_bytes: int = Field(ge=1024, le=8192, strict=True)
    pointers: ActivationProbePointers = Field(default_factory=ActivationProbePointers)
    contract_digest: Sha256

    @model_validator(mode="after")
    def target_owned_endpoint(self) -> ActivationBusinessProbe:
        if (
            not self.endpoint.startswith("/api/")
            or self.endpoint.startswith(("/api/omnia/", "/api/max/"))
            or self.endpoint in {"/api/omnia", "/api/max"}
            or "?" in self.endpoint
            or "#" in self.endpoint
            or "%" in self.endpoint
            or "\\" in self.endpoint
            or "//" in self.endpoint
            or ".." in self.endpoint.split("/")
            or self.endpoint.endswith("/")
            or any(
                re.fullmatch(r"[A-Za-z0-9_-]+", segment) is None
                for segment in self.endpoint.split("/")[1:]
            )
        ):
            raise ValueError("activation business probe endpoint must be target-owned")
        if len({witness.entity for witness in self.witnesses}) != len(self.witnesses):
            raise ValueError("duplicate activation business probe entity")
        payload = self.model_dump(mode="json", exclude={"contract_digest"})
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.contract_digest != expected:
            raise ValueError("activation business probe digest mismatch")
        return self


class RestorationAdaptationActivationOfferRequest(BaseModel):
    """Exact identity of the controller-owned proof selected by the API."""

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

    def digest(self) -> str:
        return _digest_payload(self)


class RestorationAdaptationActivationOffer(BaseModel):
    """Stable observed binding pinned to one proof-ready candidate."""

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

    def binding_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"offer_digest"})

    @model_validator(mode="after")
    def valid_binding(self) -> Self:
        if self.target_fencing_epoch <= self.expected_source_fencing_epoch:
            raise ValueError("target fencing epoch must advance the source fence")
        if self.candidate_workspace_id == self.workspace_id:
            raise ValueError("candidate workspace must be isolated from the source")
        for value in (self.source_code_volume, self.live_database_volume):
            if _VOLUME_RE.fullmatch(value) is None:
                raise ValueError("invalid restoration adaptation volume identity")
        if self.probe_contract_digest != self.business_probe.contract_digest:
            raise ValueError("restoration activation probe digest mismatch")
        if _digest_payload(self.binding_payload()) != self.offer_digest:
            raise ValueError("restoration activation offer digest mismatch")
        return self


class RestorationAdaptationActivationCommand(BaseModel):
    """Full immutable offer echo plus the Git revision prepared by the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offer: RestorationAdaptationActivationOffer
    planned_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")

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
        return _digest_payload(self.activation_binding_payload())

    def digest(self) -> str:
        return _digest_payload(self)


class RestorationAdaptationActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    generation_run_id: UUID
    project_id: UUID
    owner_id: UUID
    source_workspace_id: UUID
    expected_source_fencing_epoch: int = Field(gt=0, strict=True)
    target_fencing_epoch: int = Field(gt=0, strict=True)
    source_workspace_revision: Sha256
    source_code_volume: str = Field(min_length=1, max_length=255)
    live_database_volume: str = Field(min_length=1, max_length=255)
    live_database_identity_digest: Sha256
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0, strict=True)
    candidate_workspace_revision: Sha256
    proof_digest: Sha256
    candidate_artifact_digest: Sha256
    candidate_source_manifest_digest: Sha256
    candidate_code_digest: Sha256
    business_probe: ActivationBusinessProbe
    probe_rehearsal_digest: Sha256
    probe_rehearsal_database_digest: Sha256
    planned_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    activation_id: UUID
    activation_digest: Sha256

    def activation_binding_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"activation_digest"})

    @model_validator(mode="after")
    def valid_fence_volume_and_digest(self) -> RestorationAdaptationActivationRequest:
        if self.target_fencing_epoch <= self.expected_source_fencing_epoch:
            raise ValueError("target fencing epoch must advance the source fence")
        if self.candidate_workspace_id == self.source_workspace_id:
            raise ValueError("candidate workspace must be isolated from the source")
        for value in (
            self.source_code_volume,
            self.live_database_volume,
        ):
            if _VOLUME_RE.fullmatch(value) is None:
                raise ValueError("invalid restoration adaptation volume identity")
        if _digest_payload(self.activation_binding_payload()) != self.activation_digest:
            raise ValueError("restoration activation binding digest mismatch")
        return self

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


class ActivationObservedIdentity(BaseModel):
    """Fresh read-only observation made before any activation effect."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_workspace_id: UUID
    source_fencing_epoch: int = Field(gt=0, strict=True)
    source_workspace_revision: Sha256
    source_code_volume: str
    live_database_volume: str
    live_database_identity_digest: Sha256
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int = Field(gt=0, strict=True)
    candidate_workspace_revision: Sha256
    proof_digest: Sha256
    candidate_artifact_digest: Sha256
    candidate_source_manifest_digest: Sha256
    candidate_code_digest: Sha256
    business_probe: ActivationBusinessProbe
    probe_rehearsal_digest: Sha256
    probe_rehearsal_database_digest: Sha256
    planned_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    activation_id: UUID
    activation_digest: Sha256

    @classmethod
    def from_request(
        cls, request: RestorationAdaptationActivationRequest
    ) -> ActivationObservedIdentity:
        return cls(
            source_workspace_id=request.source_workspace_id,
            source_fencing_epoch=request.expected_source_fencing_epoch,
            source_workspace_revision=request.source_workspace_revision,
            source_code_volume=request.source_code_volume,
            live_database_volume=request.live_database_volume,
            live_database_identity_digest=request.live_database_identity_digest,
            candidate_workspace_id=request.candidate_workspace_id,
            candidate_fencing_epoch=request.candidate_fencing_epoch,
            candidate_workspace_revision=request.candidate_workspace_revision,
            proof_digest=request.proof_digest,
            candidate_artifact_digest=request.candidate_artifact_digest,
            candidate_source_manifest_digest=request.candidate_source_manifest_digest,
            candidate_code_digest=request.candidate_code_digest,
            business_probe=request.business_probe,
            probe_rehearsal_digest=request.probe_rehearsal_digest,
            probe_rehearsal_database_digest=request.probe_rehearsal_database_digest,
            planned_commit_sha=request.planned_commit_sha,
            activation_id=request.activation_id,
            activation_digest=request.activation_digest,
        )


class ActivationPreparedTarget(BaseModel):
    """The code-only target; its database identity must remain the live one."""

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


class RestorationAdaptationActivationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ActivationState
    effects_admitted: bool
    operation_id: UUID
    generation_run_id: UUID
    project_id: UUID
    owner_id: UUID
    activation_id: UUID
    activation_digest: Sha256
    proof_digest: Sha256
    fencing_epoch: int = Field(gt=0, strict=True)
    source_volume_identity: ActivationPreparedTarget
    target_volume_identity: ActivationPreparedTarget
    health_digest: Sha256 | None = None
    receipt_digest: Sha256 | None = None

    @model_validator(mode="after")
    def state_matches_effects(self) -> RestorationAdaptationActivationReceipt:
        admitted = self.state in {"target_writers_admitted", "activated"}
        if self.effects_admitted is not admitted:
            raise ValueError("activation state does not match admitted effects")
        if (self.state == "activated") != (self.health_digest is not None):
            raise ValueError("activation health proof does not match state")
        return self


class RestorationAdaptationActivationStatus(BaseModel):
    """Engine phase together with the full immutable activation binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ActivationState
    effects_admitted: bool
    offer: RestorationAdaptationActivationOffer
    planned_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    activation_digest: Sha256
    fencing_epoch: int = Field(gt=0, strict=True)
    source_volume_identity: ActivationPreparedTarget
    target_volume_identity: ActivationPreparedTarget
    health_digest: Sha256 | None = None
    receipt_digest: Sha256

    @model_validator(mode="after")
    def state_matches_effects(self) -> Self:
        admitted = self.state in {"target_writers_admitted", "activated"}
        if self.effects_admitted is not admitted:
            raise ValueError("activation state does not match admitted effects")
        if (self.state == "activated") != (self.health_digest is not None):
            raise ValueError("activation health proof does not match state")
        command = RestorationAdaptationActivationCommand(
            offer=self.offer,
            planned_commit_sha=self.planned_commit_sha,
        )
        if self.activation_digest != command.activation_digest():
            raise ValueError("restoration activation digest mismatch")
        expected_source = ActivationPreparedTarget(
            workspace_id=self.offer.workspace_id,
            fencing_epoch=self.offer.expected_source_fencing_epoch,
            code_volume=self.offer.source_code_volume,
            code_digest=self.offer.source_workspace_revision,
            database_volume=self.offer.live_database_volume,
            database_identity_digest=self.offer.live_database_identity_digest,
        )
        if self.source_volume_identity != expected_source:
            raise ValueError("restoration activation source identity mismatch")
        if (
            self.target_volume_identity.workspace_id != self.offer.workspace_id
            or self.target_volume_identity.fencing_epoch != self.offer.target_fencing_epoch
            or self.target_volume_identity.code_digest != self.offer.archive_digest
            or self.target_volume_identity.database_volume != self.offer.live_database_volume
            or self.target_volume_identity.database_identity_digest
            != self.offer.live_database_identity_digest
            or self.fencing_epoch != self.offer.target_fencing_epoch
        ):
            raise ValueError("restoration activation target identity mismatch")
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
        if _digest_payload(receipt) != self.receipt_digest:
            raise ValueError("restoration activation receipt digest mismatch")
        return self


class RestorationAdaptationActivationRecoveryFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    activation_id: str
    error_code: Literal["invalid_journal", "identity_conflict", "recovery_failed"]
    retryable: bool


class RestorationAdaptationActivationRecoveryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    successes: tuple[RestorationAdaptationActivationReceipt, ...]
    failures: tuple[RestorationAdaptationActivationRecoveryFailure, ...]
