"""Owner-facing restoration state and strictly validated controller evidence."""

from datetime import datetime
from typing import Literal, Self
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


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_version_id: UUID
    expected_draft_snapshot_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=128)


class RestoreApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_revision: int = Field(ge=1, strict=True)
    expected_draft_snapshot_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=128)


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

    @field_validator("applied", "safe_to_release", mode="before")
    @classmethod
    def exact_recovery_flags(cls, value: object, info: ValidationInfo) -> object:
        expected = info.field_name == "safe_to_release"
        if value is not expected:
            raise ValueError("runtime recovery flags must be exact booleans")
        return value


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

    @model_validator(mode="after")
    def require_observed_completion(self) -> Self:
        if (self.state == "completed") != isinstance(self.observed, RuntimeObserved):
            raise ValueError("only observed runtime activation may complete restoration")
        if isinstance(self.observed, RuntimeRecoveryObserved) and self.state != "failed":
            raise ValueError("verified runtime recovery requires failed state")
        if self.state == "ready" and (
            self.report is None or self.candidate_id is None or self.report.blockers
        ):
            raise ValueError("ready restoration requires a candidate and an unblocked report")
        return self
