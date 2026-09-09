"""Internal, exact-candidate publication request. Never accepts Docker options."""

from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CellDeployRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    snapshot_id: UUID
    candidate_id: UUID
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    fencing_epoch: int = Field(gt=0)
    accepted_fencing_epoch: int | None = Field(default=None, gt=0)
    restoration_operation_id: UUID | None = None
    proof_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    schema_data_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    build_ref: str | None = Field(default=None, min_length=1, max_length=2048)
    verification_ref: str | None = Field(default=None, min_length=1, max_length=2048)
    idempotency_key: str = Field(min_length=8, max_length=128)
    runtime_env: dict[str, str] = Field(default_factory=dict, repr=False)
    business_config: dict[str, Any] = Field(default_factory=dict, repr=False)
    business_config_version: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def exact_evidence_kind(self) -> Self:
        evidence = (self.proof_key, self.schema_data_digest, self.build_ref, self.verification_ref)
        if self.restoration_operation_id is None:
            if any(item is None for item in evidence):
                raise ValueError("generation publication requires complete accepted proof")
        elif self.accepted_fencing_epoch is None or any(item is not None for item in evidence):
            raise ValueError("restoration publication requires its own accepted evidence")
        return self

    @field_validator("runtime_env")
    @classmethod
    def validate_runtime_env(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"MAX_BOT_TOKEN", "MAX_WEBHOOK_SECRET", "MAX_API_BASE_URL"}
        if set(value) - allowed or any(not item or len(item) > 8192 for item in value.values()):
            raise ValueError("unsupported public runtime configuration")
        return value
