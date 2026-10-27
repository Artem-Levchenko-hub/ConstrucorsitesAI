"""Owner-visible migration proof; never SQL, storage locations or business rows."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class MigrationField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    type: str = Field(pattern=r"^(?:pg_catalog\.)?[a-z][a-z0-9_]{0,62}$")


class MigrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    project_id: UUID
    snapshot_id: UUID
    state: Literal["preparing", "prepared", "applying", "reconciling", "applied",
                   "failed", "stale", "blocked"]
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: list[MigrationField] = Field(default_factory=list, max_length=64)
    backup_verified: StrictBool = False
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    expires_at: datetime | None = None
    effect_possible: StrictBool = False


class MigrationPreparation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID


class MigrationConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    explicit_intent: StrictBool
