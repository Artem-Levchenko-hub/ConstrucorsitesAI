"""Safe owner-visible migration commands and metadata; no SQL or row values."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from yleum_orchestrator.schemas.cell_publication import CellDeployRequest

MigrationState = Literal[
    "preparing", "prepared", "applying", "reconciling", "applied", "failed", "stale", "blocked"
]


class MigrationOutcomeProof(BaseModel):
    """Private IO proof; the public status never exposes transaction identities."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["preflight_no_dispatch", "xid_aborted", "xid_committed_verified"]
    xid: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")

    @model_validator(mode="after")
    def exact_transaction(self) -> Self:
        if (self.kind == "preflight_no_dispatch") != (self.xid is None):
            raise ValueError("migration outcome requires its exact transaction")
        return self


class MigrationField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=63)
    type: str = Field(min_length=1, max_length=128)


class PublicationMigrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    project_id: UUID
    snapshot_id: UUID
    state: MigrationState
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: list[MigrationField] = Field(default_factory=list, max_length=128)
    backup_verified: bool = False
    error_code: str | None = None
    expires_at: datetime | None = None
    effect_possible: bool = False


class MigrationPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication: CellDeployRequest

    @model_validator(mode="after")
    def no_configuration(self) -> Self:
        if self.publication.runtime_env or self.publication.business_config:
            raise ValueError("migration accepts only publication evidence")
        return self


class PublicationMigrationPrepare(MigrationPublicationRequest):
    idempotency_key: UUID


class PublicationMigrationConfirm(MigrationPublicationRequest):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    explicit_intent: StrictBool


class PublicationMigrationLatest(BaseModel):
    migration: PublicationMigrationStatus | None
