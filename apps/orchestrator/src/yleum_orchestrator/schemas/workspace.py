"""Internal Project Cell workspace schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from yleum_orchestrator.core.workspace_provider import ProjectCellLeaseObservation


class WorkspaceAgentPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int = Field(strict=True, gt=0)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    profile_version: str = Field(min_length=1)
    operation_id: UUID
    fencing_epoch: int = Field(gt=0)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    kind: Literal["wake", "pause", "stop", "destroy", "restore", "reconcile", "release"]
    checkpoint_ref: str | None = None
    operation_id: UUID
    fencing_epoch: int = Field(gt=0)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    operation_id: UUID
    fencing_epoch: int = Field(gt=0)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceCapabilityResponse(BaseModel):
    project_id: UUID
    provider: Literal["disabled", "docker_owner_canary"]
    enabled: bool
    ready: bool
    state: Literal["disabled", "unsupported", "ready"]
    detail: str


class WorkspaceResourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    state: Literal[
        "resources_ready",
        "resources_paused",
        "retained",
        "partial",
        "degraded",
        "conflict",
    ]
    provider_ref: str | None
    fencing_epoch: int | None
    checkpoint_ref: str | None
    has_workspace: bool
    has_agent_home: bool
    has_postgres: bool
    has_redis: bool
    has_draft_runtime: bool = False
    draft_state: Literal["running", "stopped", "failed"] | None = None
    preview_url: str | None = None
    lease_observation: ProjectCellLeaseObservation | None = None

    @model_serializer(mode="wrap")
    def serialize_resource(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload: dict[str, Any] = handler(self)
        if self.lease_observation is None:
            payload.pop("lease_observation", None)
        return payload


class WorkspaceAgentBootstrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: dict[str, str]
    seeded_from_project: bool
    generation_run_id: UUID
    fencing_epoch: int
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: dict[str, object] = Field(default_factory=dict)


class WorkspaceAgentBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)


class WorkspaceAgentWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, str]
    deletes: list[str] = Field(default_factory=list)
    operation_id: UUID | None = None


class WorkspaceAgentWriteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    physical_volume: str = Field(min_length=1)
    physical_volume_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    written: int = Field(ge=0)
    deleted: int = Field(ge=0)


class WorkspaceAgentWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    written: int
    deleted: int
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt: WorkspaceAgentWriteReceipt | None = None


class WorkspaceAgentWriteStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    state: Literal["pending", "terminal"]
    receipt: WorkspaceAgentWriteReceipt | None = None


class WorkspaceDraftFilesResponse(BaseModel):
    """The owner's draft as it is on disk, read outside any generation lease."""

    model_config = ConfigDict(extra="forbid")

    files: dict[str, str]
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceDraftResetRequest(BaseModel):
    """Owner-initiated rewrite of draft files (discarding unsaved edits)."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, str]
    deletes: list[str] = Field(default_factory=list)


class WorkspaceDraftApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, str] = Field(default_factory=dict)
    deletes: list[str] = Field(default_factory=list)


class WorkspaceDraftApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["draft_running", "draft_failed"]
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_url: str
    package_exit_code: int | None = None
    package_stderr_tail: str = ""
    migration_exit_code: int | None = None
    migration_stderr_tail: str = ""
    runtime_log_tail: str = ""


class WorkspaceDraftPreviewSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)


class WorkspaceOwnerPreviewSessionRequest(BaseModel):
    """API-attested owner access, never an agent execution lease."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    owner_id: UUID


class WorkspaceDraftPreviewSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    state: Literal["draft_running"]
    preview_url: str
    bootstrap_url: str
    expires_at: str


class WorkspaceOwnerBusinessConfigRequest(WorkspaceOwnerPreviewSessionRequest):
    version: int = Field(ge=0, strict=True)
    config: dict[str, object]
    generation_run_id: UUID | None = None
    fencing_epoch: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def generation_seed_authority(self) -> Self:
        if (self.generation_run_id is None) != (self.fencing_epoch is None):
            raise ValueError("generation seed requires a complete active lease")
        if self.generation_run_id is None and self.version == 0:
            raise ValueError("owner configuration version must be positive")
        if self.version == 0 and self.config.get("content", []) != []:
            raise ValueError("default generation configuration must have empty content")
        return self


class WorkspaceAgentExecRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_receipt: bool = False

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cmd: str = Field(min_length=1)
    timeout_seconds: int = Field(default=180, ge=1, le=900)
    operation_id: UUID = Field(default_factory=uuid4)
    task_role: (
        Literal[
            "bootstrap",
            "fast_check",
            "full_build",
            "restore_runtime",
            "build",
            "test",
        ]
        | None
    ) = None


class WorkspaceIdentityDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_data_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cell_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceAgentExecResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    exit_code: int
    detail: str
    timed_out: bool = False
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: UUID
    before_identity: WorkspaceIdentityDigest
    after_identity: WorkspaceIdentityDigest
    environment_mutated: bool
    project_migration_receipt: dict[str, Any] | None = None
    compiled_asset_receipt: dict[str, Any] | None = None


class WorkspaceAgentCommandBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    dispatch_fencing_epoch: int = Field(gt=0)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_volume: str = Field(min_length=1)
    physical_volume_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceAgentCommandReceipt(WorkspaceAgentCommandBinding):
    after_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceAgentTrackedExecResponse(WorkspaceAgentExecResponse):
    command_receipt: WorkspaceAgentCommandReceipt


class WorkspaceAgentCommandStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    state: Literal["unknown", "pending", "terminal"]
    receipt: WorkspaceAgentCommandReceipt | None = None
    terminal_response: WorkspaceAgentExecResponse | None = None


class WorkspaceAgentOperationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    state: Literal["running", "completed", "failed", "timed_out", "cancelled"]
    phase: str
    started_at: datetime
    deadline_at: datetime
    heartbeat_at: datetime
    log_bytes: int = Field(ge=0)
    terminal_response: WorkspaceAgentExecResponse | None = None
