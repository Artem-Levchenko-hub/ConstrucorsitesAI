"""HTTP client to the V2 orchestrator (`apps/orchestrator` on :8003).

apps/api is a thin authenticated proxy: it owns the JWT cookie and the
ownership check, then forwards the request to orchestrator with a shared
X-Internal-Token header. Errors from orchestrator are translated into our
ApiError taxonomy so the public response shape stays consistent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
from asyncio import sleep
from asyncio import timeout as asyncio_timeout
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid5

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.schemas.restoration import (
    RestorationAdaptationActivationCommand,
    RestorationAdaptationActivationOffer,
    RestorationAdaptationActivationOfferRequest,
    RestorationAdaptationActivationStatus,
)

log = structlog.get_logger(__name__)

_REQUEST_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
# Имя нарушенного правила приходит от оркестратора: фраза разработчика,
# только строчные латинские слова. Всё остальное — повод отвергнуть ответ.
_PROBE_RULE_NAME = re.compile(r"^[a-z][a-z0-9_; ]{0,399}$")
_CHECKPOINT_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
_PROJECT_CELL_CONTROL_KINDS = frozenset(
    {"wake", "pause", "stop", "destroy", "restore", "reconcile", "release"}
)
_PROJECT_CELL_CAPACITY_REASONS = frozenset(
    {
        "insufficient_cpu",
        "insufficient_memory",
        "insufficient_disk",
        "insufficient_inodes",
        "daemon_filesystem_unverifiable",
    }
)


class OrchestratorUnavailable(ApiError):
    """Orchestrator is offline or returns a network/5xx error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="orchestrator_unavailable",
            message=message,
            status_code=503,
            details=details,
        )


class OrchestratorReaderInventoryUnavailable(OrchestratorUnavailable):
    """A confirmed reader guard denial; earlier lifecycle effects may exist."""


class OrchestratorMachineInventoryUnavailable(OrchestratorUnavailable):
    """Typed missing process inventory; it carries no successful proof identity."""

    def __init__(self, reason: str, workspace_revision: str, manifest_digest: str) -> None:
        self.reason = reason
        self.workspace_revision = workspace_revision
        self.manifest_digest = manifest_digest
        super().__init__("Portable machine environment inventory is unavailable")


class OrchestratorBadRequest(ApiError):
    """Orchestrator rejected the request (4xx) — pass the reason through."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
        upstream_code: str | None = None,
    ) -> None:
        self.upstream_code = upstream_code
        super().__init__(
            code="orchestrator_rejected",
            message=message,
            status_code=status_code,
            details=details,
        )


def _validate_request_digest(request_digest: str) -> None:
    if not _REQUEST_DIGEST_RE.fullmatch(request_digest):
        raise ValueError("request_digest must be a 64-character lowercase hex string")


def _validate_workspace_revision(workspace_revision: str) -> None:
    if not _REQUEST_DIGEST_RE.fullmatch(workspace_revision):
        raise ValueError("workspace_revision must be a 64-character lowercase hex string")


def _validate_fencing_epoch(fencing_epoch: int) -> None:
    if fencing_epoch <= 0:
        raise ValueError("fencing_epoch must be positive")


def _validate_checkpoint_ref(checkpoint_ref: str | None) -> None:
    if checkpoint_ref is not None and _CHECKPOINT_REF_RE.fullmatch(checkpoint_ref) is None:
        raise ValueError("checkpoint_ref must match ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")


def _parse_orchestrator_4xx(
    payload: object,
) -> tuple[str, str | None, dict[str, Any] | None]:
    if type(payload) is not dict:
        return "unknown", None, {"detail": payload}

    body = cast(dict[str, Any], payload)
    error = body.get("error")
    if type(error) is dict:
        error_body = cast(dict[str, Any], error)
        raw_message = error_body.get("message")
        message = raw_message if type(raw_message) is str else "unknown"
        raw_code = error_body.get("code")
        code = raw_code if type(raw_code) is str else None
        raw_details = error_body.get("details")
        if type(raw_details) is dict:
            return message, code, cast(dict[str, Any], raw_details)

        fallback: dict[str, Any] = {}
        if type(raw_code) is str:
            fallback["code"] = raw_code
        if raw_details is not None:
            fallback["details"] = raw_details
        return message, code, fallback or None

    raw_detail = body.get("detail")
    message = raw_detail if type(raw_detail) is str else "unknown"
    return message, None, body


@dataclass(frozen=True, slots=True)
class EnsureProjectCellResourcesRequest:
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    profile_version: str
    operation_id: UUID
    fencing_epoch: int
    request_digest: str

    def __post_init__(self) -> None:
        if not self.profile_version:
            raise ValueError("profile_version must be non-empty")
        _validate_fencing_epoch(self.fencing_epoch)
        _validate_request_digest(self.request_digest)

    def to_wire_json(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "project_id": str(self.project_id),
            "owner_id": str(self.owner_id),
            "generation_run_id": str(self.generation_run_id),
            "profile_version": self.profile_version,
            "operation_id": str(self.operation_id),
            "fencing_epoch": self.fencing_epoch,
            "request_digest": self.request_digest,
        }


@dataclass(frozen=True, slots=True)
class ControlProjectCellResourcesRequest:
    workspace_id: UUID
    kind: str
    checkpoint_ref: str | None
    operation_id: UUID
    fencing_epoch: int
    request_digest: str

    def __post_init__(self) -> None:
        if self.kind not in _PROJECT_CELL_CONTROL_KINDS:
            raise ValueError(f"unsupported Project Cell control kind {self.kind!r}")
        _validate_checkpoint_ref(self.checkpoint_ref)
        _validate_fencing_epoch(self.fencing_epoch)
        _validate_request_digest(self.request_digest)
        if self.kind in {"pause", "stop", "restore"} and self.checkpoint_ref is None:
            raise ValueError(f"checkpoint_ref is required for {self.kind!r}")
        if (
            self.kind in {"wake", "destroy", "reconcile", "release"}
            and self.checkpoint_ref is not None
        ):
            raise ValueError(f"checkpoint_ref is forbidden for {self.kind!r}")

    def to_wire_json(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "kind": self.kind,
            "checkpoint_ref": self.checkpoint_ref,
            "operation_id": str(self.operation_id),
            "fencing_epoch": self.fencing_epoch,
            "request_digest": self.request_digest,
        }


@dataclass(frozen=True, slots=True)
class ObserveProjectCellResourcesRequest:
    workspace_id: UUID
    operation_id: UUID
    fencing_epoch: int
    request_digest: str

    def __post_init__(self) -> None:
        _validate_fencing_epoch(self.fencing_epoch)
        _validate_request_digest(self.request_digest)

    def to_wire_json(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "operation_id": str(self.operation_id),
            "fencing_epoch": self.fencing_epoch,
            "request_digest": self.request_digest,
        }


class ProjectCellLeaseObservation(BaseModel):
    """Authenticated, fenced observation; missing evidence never means idle."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    operation_id: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fencing_epoch: int = Field(gt=0)
    active_generation_run_id: UUID | None
    active_generation_fencing_epoch: int | None = Field(gt=0)
    quiescent: bool

    @model_validator(mode="after")
    def coherent_lease(self) -> ProjectCellLeaseObservation:
        if (self.active_generation_run_id is None) != (
            self.active_generation_fencing_epoch is None
        ) or (self.quiescent and self.active_generation_run_id is not None):
            raise ValueError("incoherent lease observation")
        return self


@dataclass(frozen=True, slots=True)
class ProjectCellResourceResponse:
    workspace_id: UUID
    state: str
    provider_ref: str | None
    fencing_epoch: int | None
    checkpoint_ref: str | None
    has_workspace: bool
    has_agent_home: bool
    has_postgres: bool
    has_redis: bool
    has_draft_runtime: bool = False
    draft_state: str | None = None
    preview_url: str | None = None
    lease_observation: ProjectCellLeaseObservation | None = None

    def __post_init__(self) -> None:
        if not self.state:
            raise ValueError("state must be non-empty")
        if self.provider_ref == "":
            raise ValueError("provider_ref must be non-empty when provided")
        _validate_checkpoint_ref(self.checkpoint_ref)
        if self.fencing_epoch is not None and self.fencing_epoch < 0:
            raise ValueError("fencing_epoch must be zero or positive when provided")

    def to_wire_json(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "state": self.state,
            "provider_ref": self.provider_ref,
            "fencing_epoch": self.fencing_epoch,
            "checkpoint_ref": self.checkpoint_ref,
            "has_workspace": self.has_workspace,
            "has_agent_home": self.has_agent_home,
            "has_postgres": self.has_postgres,
            "has_redis": self.has_redis,
            "has_draft_runtime": self.has_draft_runtime,
            "draft_state": self.draft_state,
            "preview_url": self.preview_url,
            **(
                {"lease_observation": self.lease_observation.model_dump(mode="json")}
                if self.lease_observation is not None
                else {}
            ),
        }

    @classmethod
    def from_json(
        cls,
        payload: dict[str, object],
        *,
        allow_extra: bool = False,
    ) -> ProjectCellResourceResponse:
        expected = {
            "workspace_id",
            "state",
            "provider_ref",
            "fencing_epoch",
            "checkpoint_ref",
            "has_workspace",
            "has_agent_home",
            "has_postgres",
            "has_redis",
            "has_draft_runtime",
            "draft_state",
            "preview_url",
            "lease_observation",
        }
        if not allow_extra:
            unexpected = set(payload) - expected
            if unexpected:
                raise OrchestratorUnavailable(
                    "Orchestrator returned an invalid Project Cell resource object"
                )

        workspace_id = payload.get("workspace_id")
        state = payload.get("state")
        provider_ref = payload.get("provider_ref")
        fencing_epoch = payload.get("fencing_epoch")
        checkpoint_ref = payload.get("checkpoint_ref")
        has_workspace = payload.get("has_workspace")
        has_agent_home = payload.get("has_agent_home")
        has_postgres = payload.get("has_postgres")
        has_redis = payload.get("has_redis")
        has_draft_runtime = payload.get("has_draft_runtime", False)
        draft_state = payload.get("draft_state")
        preview_url = payload.get("preview_url")
        invalid_shape = (
            type(workspace_id) is not str
            or type(state) is not str
            or (provider_ref is not None and type(provider_ref) is not str)
            or (fencing_epoch is not None and type(fencing_epoch) is not int)
            or (checkpoint_ref is not None and type(checkpoint_ref) is not str)
            or type(has_workspace) is not bool
            or type(has_agent_home) is not bool
            or type(has_postgres) is not bool
            or type(has_redis) is not bool
            or type(has_draft_runtime) is not bool
            or (
                draft_state is not None
                and (
                    type(draft_state) is not str
                    or draft_state not in {"running", "stopped", "failed"}
                )
            )
            or (preview_url is not None and type(preview_url) is not str)
        )
        if invalid_shape:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell resource object"
            )
        workspace_id = cast(str, workspace_id)
        state = cast(str, state)
        provider_ref = cast(str | None, provider_ref)
        fencing_epoch = cast(int | None, fencing_epoch)
        checkpoint_ref = cast(str | None, checkpoint_ref)
        has_workspace = cast(bool, has_workspace)
        has_agent_home = cast(bool, has_agent_home)
        has_postgres = cast(bool, has_postgres)
        has_redis = cast(bool, has_redis)

        try:
            return cls(
                workspace_id=UUID(workspace_id),
                state=state,
                provider_ref=provider_ref,
                fencing_epoch=fencing_epoch,
                checkpoint_ref=checkpoint_ref,
                has_workspace=has_workspace,
                has_agent_home=has_agent_home,
                has_postgres=has_postgres,
                has_redis=has_redis,
                has_draft_runtime=cast(bool, has_draft_runtime),
                draft_state=cast(str | None, draft_state),
                preview_url=cast(str | None, preview_url),
                lease_observation=(
                    ProjectCellLeaseObservation.model_validate_json(
                        json.dumps(payload["lease_observation"])
                    )
                    if payload.get("lease_observation") is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell resource object"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProjectCellAgentWorkspaceSnapshot:
    files: dict[str, str]
    seeded_from_project: bool
    generation_run_id: UUID | None
    fencing_epoch: int
    workspace_revision: str
    capabilities: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellAgentWorkspaceSnapshot:
        expected = {
            "files",
            "seeded_from_project",
            "generation_run_id",
            "fencing_epoch",
            "workspace_revision",
            "capabilities",
        }
        unexpected = set(payload) - expected
        if unexpected:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell workspace snapshot"
            )
        raw_files = payload.get("files")
        seeded_from_project = payload.get("seeded_from_project")
        generation_run_id = payload.get("generation_run_id")
        fencing_epoch = payload.get("fencing_epoch")
        workspace_revision = payload.get("workspace_revision")
        capabilities = payload.get("capabilities", {})
        if (
            type(raw_files) is not dict
            or type(seeded_from_project) is not bool
            or (generation_run_id is not None and type(generation_run_id) is not str)
            or type(fencing_epoch) is not int
            or type(workspace_revision) is not str
            or type(capabilities) is not dict
            or (
                "portable_machine" in capabilities
                and type(capabilities["portable_machine"]) is not bool
            )
        ):
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell workspace snapshot"
            )
        files: dict[str, str] = {}
        for raw_path, raw_content in raw_files.items():
            if type(raw_path) is not str or type(raw_content) is not str:
                raise OrchestratorUnavailable(
                    "Orchestrator returned an invalid Project Cell workspace snapshot"
                )
            files[raw_path] = raw_content
        try:
            _validate_fencing_epoch(fencing_epoch)
            _validate_workspace_revision(workspace_revision)
            return cls(
                files=files,
                seeded_from_project=seeded_from_project,
                generation_run_id=(
                    UUID(generation_run_id) if generation_run_id is not None else None
                ),
                fencing_epoch=fencing_epoch,
                workspace_revision=workspace_revision,
                capabilities=dict(capabilities),
            )
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell workspace snapshot"
            ) from exc


@dataclass(frozen=True, slots=True)
class RestorationAdaptationWorkspace:
    source_workspace_id: UUID
    candidate_workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    candidate_fencing_epoch: int
    source_database_digest: str
    proof_digest: str
    capabilities: dict[str, object]
    request_digest: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> RestorationAdaptationWorkspace:
        expected = {
            "state",
            "source_workspace_id",
            "candidate_workspace_id",
            "operation_id",
            "project_id",
            "owner_id",
            "generation_run_id",
            "candidate_fencing_epoch",
            "source_database_digest",
            "proof_digest",
            "capabilities",
        }
        capabilities = payload.get("capabilities")
        digests = (payload.get("source_database_digest"), payload.get("proof_digest"))
        if (
            set(payload) not in (expected, expected | {"request_digest"})
            or ("request_digest" in payload and (
                type(payload["request_digest"]) is not str
                or _REQUEST_DIGEST_RE.fullmatch(payload["request_digest"]) is None
            ))
            or payload.get("state") != "ready"
            or type(payload.get("candidate_fencing_epoch")) is not int
            or cast(int, payload["candidate_fencing_epoch"]) < 1
            or type(capabilities) is not dict
            or any(
                type(value) is not str or _REQUEST_DIGEST_RE.fullmatch(value) is None
                for value in digests
            )
            or capabilities
            != {
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
            }
        ):
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid restoration adaptation workspace"
            )
        try:
            return cls(
                source_workspace_id=UUID(cast(str, payload["source_workspace_id"])),
                candidate_workspace_id=UUID(cast(str, payload["candidate_workspace_id"])),
                operation_id=UUID(cast(str, payload["operation_id"])),
                project_id=UUID(cast(str, payload["project_id"])),
                owner_id=UUID(cast(str, payload["owner_id"])),
                generation_run_id=UUID(cast(str, payload["generation_run_id"])),
                candidate_fencing_epoch=cast(int, payload["candidate_fencing_epoch"]),
                source_database_digest=cast(str, payload["source_database_digest"]),
                proof_digest=cast(str, payload["proof_digest"]),
                capabilities=cast(dict[str, object], capabilities),
                request_digest=cast(str | None, payload.get("request_digest")),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid restoration adaptation workspace"
            ) from exc


class RestorationAdaptationRecoveryIdentity(BaseModel):
    """Immutable original preparation identity; transition IDs are separate effect IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: int = 1
    transition_id: UUID
    workspace_id: UUID
    generation_run_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    candidate_workspace_id: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def distinct_transition(self) -> RestorationAdaptationRecoveryIdentity:
        if self.version != 1:
            raise ValueError("unsupported adaptation recovery version")
        if self.transition_id == self.operation_id:
            raise ValueError("recovery transition must differ from preparation operation")
        return self


class RestorationAdaptationRecoveryHold(RestorationAdaptationRecoveryIdentity):
    expected_cycle: int = Field(ge=0)
    control_fencing_epoch: int = Field(gt=0)
    candidate_fencing_epoch: int = Field(gt=0)


class RestorationAdaptationRecoveryRebind(RestorationAdaptationRecoveryIdentity):
    expected_cycle: int = Field(ge=0)
    expected_hold_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    new_control_fencing_epoch: int = Field(gt=0)


class RestorationAdaptationRecoveryReceipt(RestorationAdaptationRecoveryIdentity):
    state: Literal["hold_pending", "held", "rebinding", "resumable"]
    cycle: int = Field(ge=0)
    original_control_fencing_epoch: int = Field(gt=0)
    current_control_fencing_epoch: int = Field(gt=0)
    original_candidate_fencing_epoch: int = Field(gt=0)
    current_candidate_fencing_epoch: int = Field(gt=0)
    barrier_operation_id: UUID
    barrier_receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_ref_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    physical_incarnation_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    physical_proof_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    restore_operation_id: UUID | None = None
    terminal_receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

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


async def project_cell_restoration_recovery(
    action: Literal["hold", "status", "rebind"], command: dict[str, Any],
) -> RestorationAdaptationRecoveryReceipt:
    """Authenticated transition/read; callers still recheck SQL ownership after await."""
    models: dict[str, type[RestorationAdaptationRecoveryIdentity]] = {
        "hold": RestorationAdaptationRecoveryHold,
        "status": RestorationAdaptationRecoveryIdentity,
        "rebind": RestorationAdaptationRecoveryRebind,
    }
    try:
        request_type = models[action]
        request = request_type.model_validate_json(json.dumps(command, allow_nan=False))
    except (KeyError, ValueError, TypeError):
        raise OrchestratorUnavailable("Invalid adaptation recovery request") from None
    payload = await _request(
        "POST",
        f"/internal/workspaces/{request.workspace_id}/restoration-adaptations/"
        f"{request.generation_run_id}/recovery/{action}",
        json=request.model_dump(mode="json"), timeout=120.0,
    )
    try:
        receipt = RestorationAdaptationRecoveryReceipt.model_validate_json(
            json.dumps(payload, allow_nan=False),
        )
        if any(getattr(receipt, key) != getattr(request, key)
               for key in RestorationAdaptationRecoveryIdentity.model_fields):
            raise ValueError("foreign recovery envelope")
        if isinstance(request, RestorationAdaptationRecoveryHold) and (
            receipt.state not in {"hold_pending", "held"}
            or receipt.cycle != request.expected_cycle
            or receipt.current_control_fencing_epoch != request.control_fencing_epoch
            or receipt.current_candidate_fencing_epoch != request.candidate_fencing_epoch
        ):
            raise ValueError("hold cycle conflicts")
        if isinstance(request, RestorationAdaptationRecoveryRebind) and (
            receipt.state not in {"rebinding", "resumable"}
            or receipt.cycle != request.expected_cycle + 1
            or receipt.current_control_fencing_epoch != request.new_control_fencing_epoch
        ):
            raise ValueError("rebind cycle conflicts")
        if receipt.state in {"held", "resumable"}:
            digest = hashlib.sha256(json.dumps(
                receipt.model_dump(mode="json", exclude={"terminal_receipt_digest"}),
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if receipt.terminal_receipt_digest != digest:
                raise ValueError("terminal recovery receipt digest conflicts")
        elif receipt.terminal_receipt_digest is not None:
            raise ValueError("pending recovery has no terminal receipt")
        return receipt
    except (ValueError, TypeError, AttributeError):
        raise OrchestratorUnavailable("Invalid adaptation recovery receipt") from None


@dataclass(frozen=True, slots=True)
class RestorationAdaptationProof:
    state: str
    reason_code: str | None
    source_workspace_id: UUID
    candidate_workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    candidate_fencing_epoch: int
    proof_attempt: int
    source_workspace_revision: str
    candidate_workspace_revision: str
    candidate_proof_key: str
    candidate_artifact_digest: str
    source_database_digest: str
    candidate_database_digest: str
    source_schema_digest: str
    candidate_schema_digest: str
    source_business_digest: str
    candidate_business_digest: str
    source_technical_digest: str
    candidate_technical_digest: str
    probe_contract_digest: str
    probe_rehearsal_digest: str | None
    probe_rehearsal_database_digest: str | None
    candidate_source_manifest_digest: str
    proof_digest: str
    capabilities: dict[str, object]
    # Нарушенное правило, когда код причины слишком общий (манифест проверки).
    # Со значением по умолчанию и в конце: ответ без него остаётся годным, и
    # всё, что собирает доказательство иначе, о поле знать не обязано.
    reason_detail: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> RestorationAdaptationProof:
        expected = {
            "state",
            "reason_code",
            "source_workspace_id",
            "candidate_workspace_id",
            "operation_id",
            "project_id",
            "owner_id",
            "generation_run_id",
            "candidate_fencing_epoch",
            "proof_attempt",
            "source_workspace_revision",
            "candidate_workspace_revision",
            "candidate_proof_key",
            "candidate_artifact_digest",
            "source_database_digest",
            "candidate_database_digest",
            "source_schema_digest",
            "candidate_schema_digest",
            "source_business_digest",
            "candidate_business_digest",
            "source_technical_digest",
            "candidate_technical_digest",
            "probe_contract_digest",
            "probe_rehearsal_digest",
            "probe_rehearsal_database_digest",
            "candidate_source_manifest_digest",
            "proof_digest",
            "capabilities",
        }
        state = payload.get("state")
        reason = payload.get("reason_code")
        digests = tuple(
            payload.get(name)
            for name in (
                "source_workspace_revision",
                "candidate_workspace_revision",
                "candidate_proof_key",
                "candidate_artifact_digest",
                "source_database_digest",
                "candidate_database_digest",
                "source_schema_digest",
                "candidate_schema_digest",
                "source_business_digest",
                "candidate_business_digest",
                "source_technical_digest",
                "candidate_technical_digest",
                "probe_contract_digest",
                "candidate_source_manifest_digest",
                "proof_digest",
            )
        )
        valid_reasons = {
            "candidate_schema_changed",
            "candidate_business_data_changed",
            "candidate_technical_data_changed",
            "source_code_changed",
            "source_database_changed",
            "probe_rehearsal_failed",
            # Три разные беды раньше приходили под одним кодом, и по нему нельзя
            # было отличить «приложение не объявило проверку» от «проверка
            # выполнилась и не прошла» и от «копия изменилась под нами».
            # Принимаем новые коды ДО того, как оркестратор начнёт их слать.
            "probe_manifest_invalid",
            "candidate_changed_during_rehearsal",
            # Репетиция проверки состоит из шести шагов, и раньше любой из них
            # падал под одним словом — чинить просили вслепую. Теперь шаг назван;
            # приём здесь снова идёт впереди отправки из оркестратора.
            "probe_readiness_failed",
            "probe_owner_read_failed",
            "probe_owner_mutation_failed",
            "probe_owner_reload_failed",
            "probe_cross_owner_denial_failed",
            "probe_unauthenticated_denial_failed",
        }
        base_capabilities = {
            "portable_machine": True,
            "database_admin": "isolated_copy",
            "restoration_adaptation_database_copy_v1": True,
        }
        expected_capabilities = (
            {**base_capabilities, "restoration_adaptation_proof_v1": True}
            if state == "proof_ready"
            else base_capabilities
        )
        detail = payload.get("reason_detail")
        if (
            set(payload) - {"reason_detail"} != expected
            or state not in {"proof_ready", "migration_required", "source_changed"}
            or (state == "proof_ready") != (reason is None)
            or (reason is not None and reason not in valid_reasons)
            # Деталь — фраза разработчика, а не данные: в такое узкое поле
            # значению из чужой базы не пролезть. Форму проверяем здесь,
            # потому что приём идёт раньше, чем оркестратор начнёт слать.
            or (detail is not None and not _PROBE_RULE_NAME.fullmatch(str(detail)))
            or (detail is not None and reason is None)
            or type(payload.get("candidate_fencing_epoch")) is not int
            or cast(int, payload["candidate_fencing_epoch"]) < 1
            or type(payload.get("proof_attempt")) is not int
            or cast(int, payload["proof_attempt"]) < 1
            or payload.get("capabilities") != expected_capabilities
            or (
                state == "proof_ready"
                and (
                    payload.get("probe_rehearsal_digest") is None
                    or payload.get("probe_rehearsal_database_digest") is None
                )
            )
            or any(
                value is not None
                and (type(value) is not str or _REQUEST_DIGEST_RE.fullmatch(value) is None)
                for value in (
                    payload.get("probe_rehearsal_digest"),
                    payload.get("probe_rehearsal_database_digest"),
                )
            )
            or any(
                type(value) is not str or _REQUEST_DIGEST_RE.fullmatch(value) is None
                for value in digests
            )
        ):
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid restoration adaptation proof"
            )
        try:
            return cls(
                state=state,
                reason_code=reason,
                reason_detail=cast("str | None", detail),
                source_workspace_id=UUID(cast(str, payload["source_workspace_id"])),
                candidate_workspace_id=UUID(cast(str, payload["candidate_workspace_id"])),
                operation_id=UUID(cast(str, payload["operation_id"])),
                project_id=UUID(cast(str, payload["project_id"])),
                owner_id=UUID(cast(str, payload["owner_id"])),
                generation_run_id=UUID(cast(str, payload["generation_run_id"])),
                candidate_fencing_epoch=cast(int, payload["candidate_fencing_epoch"]),
                proof_attempt=cast(int, payload["proof_attempt"]),
                **{
                    name: cast(str, payload[name])
                    for name in (
                        "source_workspace_revision",
                        "candidate_workspace_revision",
                        "candidate_proof_key",
                        "candidate_artifact_digest",
                        "source_database_digest",
                        "candidate_database_digest",
                        "source_schema_digest",
                        "candidate_schema_digest",
                        "source_business_digest",
                        "candidate_business_digest",
                        "source_technical_digest",
                        "candidate_technical_digest",
                        "probe_contract_digest",
                        "candidate_source_manifest_digest",
                        "proof_digest",
                    )
                },
                probe_rehearsal_digest=cast(str | None, payload["probe_rehearsal_digest"]),
                probe_rehearsal_database_digest=cast(
                    str | None, payload["probe_rehearsal_database_digest"]
                ),
                capabilities=cast(dict[str, object], payload["capabilities"]),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid restoration adaptation proof"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProjectCellSourceWriteReceipt:
    version: int
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int
    physical_volume: str
    physical_volume_identity: str
    request_sha256: str
    before_revision: str
    after_revision: str
    written: int
    deleted: int

    @classmethod
    def from_json(cls, payload: object) -> ProjectCellSourceWriteReceipt:
        try:
            if type(payload) is not dict or set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("invalid receipt shape")
            values = dict(payload)
            for key in ("version", "fencing_epoch", "written", "deleted"):
                if type(values[key]) is not int:
                    raise ValueError("invalid receipt integer")
            if values["version"] != 1 or min(values["written"], values["deleted"]) < 0:
                raise ValueError("invalid receipt version or counts")
            _validate_fencing_epoch(values["fencing_epoch"])
            for key in (
                "operation_id", "workspace_id", "project_id", "owner_id", "generation_run_id",
            ):
                if type(values[key]) is not str:
                    raise ValueError("invalid receipt identity")
                values[key] = UUID(values[key])
            for key in (
                "request_sha256", "before_revision", "after_revision", "physical_volume_identity",
            ):
                _validate_workspace_revision(values[key])
            if type(values["physical_volume"]) is not str or not values["physical_volume"].strip():
                raise ValueError("invalid receipt volume")
            return cls(**values)
        except (ValueError, TypeError, AttributeError) as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid source write receipt"
            ) from exc

    def require_identity(
        self, workspace_id: UUID, generation_run_id: UUID, operation_id: UUID,
    ) -> None:
        if (self.workspace_id, self.generation_run_id, self.operation_id) != (
            workspace_id, generation_run_id, operation_id,
        ):
            raise OrchestratorUnavailable("Orchestrator source write receipt identity mismatch")


@dataclass(frozen=True, slots=True)
class ProjectCellSourceWriteStatus:
    operation_id: UUID
    state: Literal["pending", "terminal"]
    receipt: ProjectCellSourceWriteReceipt | None


@dataclass(frozen=True, slots=True)
class ProjectCellAgentWriteResponse:
    written: int
    deleted: int
    workspace_revision: str
    receipt: ProjectCellSourceWriteReceipt | None = None

    def __post_init__(self) -> None:
        if self.written < 0 or self.deleted < 0:
            raise ValueError("written and deleted must be zero or positive")
        _validate_workspace_revision(self.workspace_revision)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellAgentWriteResponse:
        expected = {"written", "deleted", "workspace_revision", "receipt"}
        unexpected = set(payload) - expected
        if unexpected:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell write response"
            )
        written = payload.get("written")
        deleted = payload.get("deleted")
        workspace_revision = payload.get("workspace_revision")
        if (
            type(written) is not int
            or type(deleted) is not int
            or type(workspace_revision) is not str
        ):
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell write response"
            )
        try:
            return cls(
                written=written,
                deleted=deleted,
                workspace_revision=workspace_revision,
                receipt=(ProjectCellSourceWriteReceipt.from_json(payload["receipt"])
                         if "receipt" in payload else None),
            )
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell write response"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProjectCellWorkspaceIdentity:
    workspace_revision: str
    dependency_digest: str
    schema_data_digest: str
    cell_manifest_digest: str
    environment_digest: str
    build_config_digest: str

    def __post_init__(self) -> None:
        for value in (
            self.workspace_revision,
            self.dependency_digest,
            self.schema_data_digest,
            self.cell_manifest_digest,
            self.environment_digest,
            self.build_config_digest,
        ):
            _validate_workspace_revision(value)

    @classmethod
    def from_json(cls, payload: object) -> ProjectCellWorkspaceIdentity:
        expected = {
            "workspace_revision",
            "dependency_digest",
            "schema_data_digest",
            "cell_manifest_digest",
            "environment_digest",
            "build_config_digest",
        }
        if type(payload) is not dict or set(payload) != expected:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell workspace identity"
            )
        values = cast(dict[str, object], payload)
        if any(type(values[name]) is not str for name in expected):
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell workspace identity"
            )
        try:
            return cls(**cast(dict[str, str], values))
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell workspace identity"
            ) from exc


def command_wire_digest(value: object) -> str:
    """The controller's versioned ASCII-escaped canonical command envelope."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectCellCommandReceipt:
    version: int
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    dispatch_fencing_epoch: int
    physical_volume: str
    physical_volume_identity: str
    request_sha256: str
    before_revision: str
    after_revision: str
    response_sha256: str

    @classmethod
    def from_json(cls, payload: object) -> ProjectCellCommandReceipt:
        try:
            if type(payload) is not dict or set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("invalid command receipt shape")
            values = dict(payload)
            if type(values["version"]) is not int or values["version"] != 1:
                raise ValueError("invalid command receipt version")
            if type(values["dispatch_fencing_epoch"]) is not int:
                raise ValueError("invalid command receipt fence")
            _validate_fencing_epoch(values["dispatch_fencing_epoch"])
            for key in ("operation_id", "workspace_id", "project_id", "owner_id",
                        "generation_run_id"):
                if type(values[key]) is not str:
                    raise ValueError("invalid command receipt identity")
                values[key] = UUID(values[key])
            for key in ("request_sha256", "response_sha256", "before_revision",
                        "after_revision", "physical_volume_identity"):
                _validate_workspace_revision(values[key])
            if type(values["physical_volume"]) is not str or not values["physical_volume"].strip():
                raise ValueError("invalid command receipt volume")
            return cls(**values)
        except (ValueError, TypeError, AttributeError) as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid command receipt"
            ) from exc

    def require_identity(self, workspace_id: UUID, run_id: UUID, operation_id: UUID) -> None:
        if (self.workspace_id, self.generation_run_id, self.operation_id) != (
            workspace_id, run_id, operation_id,
        ):
            raise OrchestratorUnavailable("Orchestrator command receipt identity mismatch")

    def require_response(self, response: ProjectCellAgentExecResponse) -> None:
        if (response.operation_id != self.operation_id or response.before_identity is None
            or response.after_identity is None
            or response.before_identity.workspace_revision != self.before_revision
            or response.after_identity.workspace_revision != self.after_revision
            or command_wire_digest(response.command_wire_json()) != self.response_sha256):
            raise OrchestratorUnavailable("Orchestrator command receipt response mismatch")


@dataclass(frozen=True, slots=True)
class ProjectCellAgentExecResponse:
    ok: bool
    exit_code: int
    detail: str
    timed_out: bool
    workspace_revision: str
    operation_id: UUID | None = None
    before_identity: ProjectCellWorkspaceIdentity | None = None
    after_identity: ProjectCellWorkspaceIdentity | None = None
    environment_mutated: bool = False
    project_migration_receipt: dict[str, object] | None = None
    compiled_asset_receipt: dict[str, object] | None = None
    command_receipt: ProjectCellCommandReceipt | None = None

    def command_wire_json(self) -> dict[str, Any]:
        values = asdict(self)
        values.pop("command_receipt")
        values["operation_id"] = str(self.operation_id) if self.operation_id else None
        return values

    def __post_init__(self) -> None:
        _validate_workspace_revision(self.workspace_revision)
        evidence = (self.operation_id, self.before_identity, self.after_identity)
        if any(item is not None for item in evidence) and not all(
            item is not None for item in evidence
        ):
            raise ValueError("incomplete Project Cell command evidence")
        if self.after_identity is not None:
            if self.workspace_revision != self.after_identity.workspace_revision:
                raise ValueError("workspace revision does not match command evidence")
            if self.environment_mutated != (self.before_identity != self.after_identity):
                raise ValueError("mutation flag does not match command evidence")
        if self.command_receipt is not None:
            self.command_receipt.require_response(self)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellAgentExecResponse:
        legacy = {"ok", "exit_code", "detail", "timed_out", "workspace_revision"}
        transport = legacy | {
            "operation_id",
            "before_identity",
            "after_identity",
            "environment_mutated",
        }
        is_transport = bool(set(payload) - legacy)
        expected = transport if is_transport else legacy
        if "project_migration_receipt" in payload:
            expected = expected | {"project_migration_receipt"}
        if "compiled_asset_receipt" in payload:
            expected = expected | {"compiled_asset_receipt"}
        if "command_receipt" in payload:
            expected = expected | {"command_receipt"}
        unexpected = set(payload) - expected
        if unexpected or set(payload) != expected:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell exec response"
            )
        ok = payload.get("ok")
        exit_code = payload.get("exit_code")
        detail = payload.get("detail")
        timed_out = payload.get("timed_out")
        workspace_revision = payload.get("workspace_revision")
        operation_id = payload.get("operation_id")
        before_identity = payload.get("before_identity")
        after_identity = payload.get("after_identity")
        environment_mutated = payload.get("environment_mutated", False)
        receipt = payload.get("project_migration_receipt")
        compiled = payload.get("compiled_asset_receipt")
        if (
            type(ok) is not bool
            or type(exit_code) is not int
            or type(detail) is not str
            or type(timed_out) is not bool
            or type(workspace_revision) is not str
            or (is_transport and type(operation_id) is not str)
            or (is_transport and type(environment_mutated) is not bool)
            or (receipt is not None and type(receipt) is not dict)
            or (compiled is not None and type(compiled) is not dict)
        ):
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell exec response"
            )
        try:
            return cls(
                ok=ok,
                exit_code=exit_code,
                detail=detail,
                timed_out=timed_out,
                workspace_revision=workspace_revision,
                operation_id=(UUID(operation_id) if type(operation_id) is str else None),
                before_identity=(
                    ProjectCellWorkspaceIdentity.from_json(before_identity)
                    if is_transport
                    else None
                ),
                after_identity=(
                    ProjectCellWorkspaceIdentity.from_json(after_identity) if is_transport else None
                ),
                environment_mutated=cast(bool, environment_mutated),
                project_migration_receipt=cast(dict[str, object] | None, receipt),
                compiled_asset_receipt=cast(dict[str, object] | None, compiled),
                command_receipt=(ProjectCellCommandReceipt.from_json(payload["command_receipt"])
                                 if "command_receipt" in payload else None),
            )
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell exec response"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProjectCellCommandStatus:
    operation_id: UUID
    state: Literal["unknown", "pending", "terminal"]
    receipt: ProjectCellCommandReceipt | None
    terminal_response: ProjectCellAgentExecResponse | None


@dataclass(frozen=True, slots=True)
class ProjectCellAgentOperationStatus:
    operation_id: UUID
    state: str
    phase: str
    started_at: datetime
    deadline_at: datetime
    heartbeat_at: datetime
    log_bytes: int
    terminal_response: ProjectCellAgentExecResponse | None

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellAgentOperationStatus:
        expected = {
            "operation_id",
            "state",
            "phase",
            "started_at",
            "deadline_at",
            "heartbeat_at",
            "log_bytes",
            "terminal_response",
        }
        if set(payload) != expected:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell operation status"
            )
        try:
            state = payload["state"]
            if state not in {"running", "completed", "failed", "timed_out", "cancelled"}:
                raise ValueError("invalid operation state")
            log_bytes = payload["log_bytes"]
            if type(log_bytes) is not int or log_bytes < 0:
                raise ValueError("invalid operation log size")
            terminal = payload["terminal_response"]
            if terminal is not None and type(terminal) is not dict:
                raise ValueError("invalid terminal response")
            if any(
                type(payload[name]) is not str
                for name in (
                    "operation_id",
                    "phase",
                    "started_at",
                    "deadline_at",
                    "heartbeat_at",
                )
            ):
                raise ValueError("invalid operation status fields")
            return cls(
                operation_id=UUID(cast(str, payload["operation_id"])),
                state=state,
                phase=cast(str, payload["phase"]),
                started_at=datetime.fromisoformat(cast(str, payload["started_at"])),
                deadline_at=datetime.fromisoformat(cast(str, payload["deadline_at"])),
                heartbeat_at=datetime.fromisoformat(cast(str, payload["heartbeat_at"])),
                log_bytes=log_bytes,
                terminal_response=(
                    ProjectCellAgentExecResponse.from_json(cast(dict[str, object], terminal))
                    if terminal is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell operation status"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProjectCellDraftApplyResponse:
    workspace_revision: str
    preview_url: str
    package_exit_code: int | None = None
    package_stderr_tail: str = ""
    migration_exit_code: int | None = None
    migration_stderr_tail: str = ""
    runtime_log_tail: str = ""

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ProjectCellDraftApplyResponse:
        expected = {
            "state",
            "workspace_revision",
            "preview_url",
            "package_exit_code",
            "package_stderr_tail",
            "migration_exit_code",
            "migration_stderr_tail",
            "runtime_log_tail",
        }
        try:
            state = payload.get("state")
            if set(payload) - expected or state not in {"draft_running", "draft_failed"}:
                raise ValueError("invalid draft state")
            for key in ("workspace_revision", "preview_url"):
                if type(payload.get(key)) is not str or not payload[key]:
                    raise ValueError("missing draft identity")
            for key in ("package_exit_code", "migration_exit_code"):
                if payload.get(key) is not None and type(payload[key]) is not int:
                    raise ValueError("invalid exit code")
            migration_exit_code = payload.get("migration_exit_code")
            if (state == "draft_running" and migration_exit_code not in {None, 0}) or (
                state == "draft_failed" and migration_exit_code in {None, 0}
            ):
                raise ValueError("draft state does not match migration result")
            for key in ("package_stderr_tail", "migration_stderr_tail", "runtime_log_tail"):
                if type(payload.get(key, "")) is not str:
                    raise ValueError("invalid draft logs")
            _validate_workspace_revision(payload["workspace_revision"])
            return cls(**{key: value for key, value in payload.items() if key != "state"})
        except (TypeError, ValueError) as exc:
            raise OrchestratorUnavailable("Orchestrator returned an invalid cell draft") from exc


@dataclass(frozen=True, slots=True)
class ProjectCellPreviewSession:
    workspace_id: UUID
    preview_url: str
    bootstrap_url: str
    expires_at: str

    def __post_init__(self) -> None:
        _validate_project_cell_preview_session(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ProjectCellPreviewSession:
        expected = {"workspace_id", "state", "preview_url", "bootstrap_url", "expires_at"}
        try:
            if set(payload) != expected or payload.get("state") != "draft_running":
                raise ValueError("invalid preview state")
            if any(type(payload[key]) is not str for key in expected):
                raise ValueError("invalid preview fields")
            return cls(
                workspace_id=UUID(payload["workspace_id"]),
                preview_url=payload["preview_url"],
                bootstrap_url=payload["bootstrap_url"],
                expires_at=payload["expires_at"],
            )
        except (TypeError, ValueError) as exc:
            # Never include the signed URL in diagnostics.
            raise OrchestratorUnavailable("Orchestrator returned an invalid cell preview") from exc


def _validate_project_cell_preview_session(session: ProjectCellPreviewSession) -> None:
    preview = urlsplit(session.preview_url)
    bootstrap = urlsplit(session.bootstrap_url)
    query = parse_qsl(bootstrap.query, keep_blank_values=True)
    from yleum_api.services.orchestrator_hosts import registry

    expected_hosts = {
        f"cell-{session.workspace_id.hex[:12]}-dev.{suffix}"
        for suffix in registry().preview_suffixes()
    }
    if (
        preview.scheme != "https"
        or preview.hostname not in expected_hosts
        or preview.username is not None
        or preview.password is not None
        or preview.port is not None
        or preview.path not in {"", "/"}
        or preview.query
        or preview.fragment
        or (bootstrap.scheme, bootstrap.netloc) != (preview.scheme, preview.netloc)
        or bootstrap.path != "/api/omnia/preview-session"
        or bootstrap.fragment
        or [key for key, _ in query] != ["expires", "signature"]
        or not query[0][1].isdigit()
        or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", query[1][1]) is None
        or datetime.fromisoformat(session.expires_at).tzinfo is None
    ):
        raise ValueError("invalid Project Cell preview session")


@dataclass(frozen=True, slots=True)
class ProjectCellPreEffectRejection:
    operation_id: UUID
    fencing_epoch: int
    request_digest: str
    effect_applied: bool

    def __post_init__(self) -> None:
        _validate_fencing_epoch(self.fencing_epoch)
        _validate_request_digest(self.request_digest)
        if self.effect_applied is not False:
            raise ValueError("effect_applied must be false for a pre-effect rejection")

    @classmethod
    def from_json(cls, payload: object) -> ProjectCellPreEffectRejection:
        if type(payload) is not dict:
            raise ValueError("rejection payload must be an object")
        value = cast(dict[str, object], payload)
        expected = {"operation_id", "fencing_epoch", "request_digest", "effect_applied"}
        if set(value) != expected:
            raise ValueError("rejection payload keys are invalid")
        operation_id = value.get("operation_id")
        fencing_epoch = value.get("fencing_epoch")
        request_digest = value.get("request_digest")
        effect_applied = value.get("effect_applied")
        if (
            type(operation_id) is not str
            or type(fencing_epoch) is not int
            or type(request_digest) is not str
            or type(effect_applied) is not bool
        ):
            raise ValueError("rejection payload field types are invalid")
        return cls(
            operation_id=UUID(operation_id),
            fencing_epoch=fencing_epoch,
            request_digest=request_digest,
            effect_applied=effect_applied,
        )


@dataclass(frozen=True, slots=True)
class ProjectCellCapacityRejection:
    operation_id: UUID
    fencing_epoch: int
    request_digest: str
    effect_applied: Literal[False]
    reason: str
    retry_after_seconds: int

    def __post_init__(self) -> None:
        _validate_fencing_epoch(self.fencing_epoch)
        _validate_request_digest(self.request_digest)
        if self.effect_applied is not False:
            raise ValueError("effect_applied must be false for capacity waiting")
        if self.reason not in _PROJECT_CELL_CAPACITY_REASONS:
            raise ValueError("capacity reason is not allowlisted")
        if not 1 <= self.retry_after_seconds <= 10:
            raise ValueError("retry_after_seconds must be between 1 and 10")

    @classmethod
    def from_json(cls, payload: object) -> ProjectCellCapacityRejection:
        if type(payload) is not dict:
            raise ValueError("capacity payload must be an object")
        value = cast(dict[str, object], payload)
        expected = {
            "operation_id",
            "fencing_epoch",
            "request_digest",
            "effect_applied",
            "reason",
            "retry_after_seconds",
        }
        if set(value) != expected:
            raise ValueError("capacity payload keys are invalid")
        if (
            type(value.get("operation_id")) is not str
            or type(value.get("fencing_epoch")) is not int
            or type(value.get("request_digest")) is not str
            or type(value.get("effect_applied")) is not bool
            or type(value.get("reason")) is not str
            or type(value.get("retry_after_seconds")) is not int
        ):
            raise ValueError("capacity payload field types are invalid")
        return cls(
            operation_id=UUID(cast(str, value["operation_id"])),
            fencing_epoch=cast(int, value["fencing_epoch"]),
            request_digest=cast(str, value["request_digest"]),
            effect_applied=cast(Literal[False], value["effect_applied"]),
            reason=cast(str, value["reason"]),
            retry_after_seconds=cast(int, value["retry_after_seconds"]),
        )


class ProjectCellCapacityWait(RuntimeError):
    def __init__(self, rejection: ProjectCellCapacityRejection) -> None:
        self.rejection = rejection
        super().__init__(rejection.reason)


class ProjectCellOrchestratorClient(Protocol):
    async def ensure(
        self,
        request: EnsureProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse: ...

    async def control(
        self,
        request: ControlProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse: ...

    async def observe_resources(
        self,
        request: ObserveProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse: ...

    async def agent_operation_status(
        self,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> ProjectCellAgentOperationStatus: ...


def _raise_capacity_wait_if_any(exc: OrchestratorBadRequest, request: Any) -> None:
    """Превратить ответ «подожди ёмкости» в ожидание, а не в отказ.

    Разбор общий для создания ячейки и для её пробуждения намеренно: пока он был
    только у создания, пробуждение считало тот же ответ отказом операции. Ответ
    про чужую операцию не принимается за свой — иначе ожиданием можно было бы
    подменить результат другого запроса.
    """
    if exc.status_code != 429:
        return
    if exc.upstream_code != "capacity_wait":
        raise OrchestratorUnavailable(
            "Orchestrator returned an invalid capacity-wait response"
        ) from exc
    try:
        rejection = ProjectCellCapacityRejection.from_json(exc.details)
    except (TypeError, ValueError) as parse_error:
        raise OrchestratorUnavailable(
            "Orchestrator returned an invalid capacity-wait response"
        ) from parse_error
    if (
        rejection.operation_id != request.operation_id
        or rejection.fencing_epoch != request.fencing_epoch
        or rejection.request_digest != request.request_digest
    ):
        raise OrchestratorUnavailable(
            "Orchestrator returned a mismatched capacity-wait response"
        ) from exc
    raise ProjectCellCapacityWait(rejection) from exc


class HttpProjectCellOrchestratorClient:
    async def ensure(
        self,
        request: EnsureProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        from yleum_api.services.orchestrator_hosts import host_for_workspace

        try:
            payload = await _request(
                "POST",
                "/internal/workspaces/ensure",
                json=request.to_wire_json(),
                # A new lease may halt/checkpoint the previous portable machine.
                # Match control release; the outer capacity deadline and worker
                # cancellation still bound the wait independently.
                timeout=930.0,
                host=await host_for_workspace(request.workspace_id),
            )
        except OrchestratorBadRequest as exc:
            _raise_capacity_wait_if_any(exc, request)
            raise
        return ProjectCellResourceResponse.from_json(payload)

    async def control(
        self,
        request: ControlProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        # Portable release halts/checkpoints the guest just like pause.
        timeout = (
            930.0
            if request.kind in {"pause", "stop", "destroy", "restore", "release"}
            else 300.0
            if request.kind == "wake"
            else 30.0
        )
        try:
            payload = await _request(
                "POST",
                f"/internal/workspaces/{request.workspace_id}/control",
                json=request.to_wire_json(),
                timeout=timeout,
            )
        except OrchestratorBadRequest as exc:
            _raise_capacity_wait_if_any(exc, request)
            raise
        return ProjectCellResourceResponse.from_json(payload)

    async def observe_resources(
        self,
        request: ObserveProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        payload = await _request(
            "POST",
            f"/internal/workspaces/{request.workspace_id}/resources/observe",
            json=request.to_wire_json(),
        )
        return ProjectCellResourceResponse.from_json(payload)

    async def agent_operation_status(
        self,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> ProjectCellAgentOperationStatus:
        return await project_cell_agent_operation_status(workspace_id, operation_id)


async def _base_url(path: str, host: str | None) -> str:
    """The orchestrator that owns this call: an explicit host, else the host bound
    to the workspace/project named in the path, else the default (single-host
    deployments never look anything up)."""
    from yleum_api.services.orchestrator_hosts import (
        OrchestratorHostError,
        host_for_path,
        registry,
    )

    try:
        name = host if host is not None else await host_for_path(path)
        return registry().get(name).url
    except OrchestratorHostError as exc:
        raise OrchestratorUnavailable(str(exc)) from exc


_FONT_INPUT_LIMIT = 8 * 1024 * 1024
_FONT_RESPONSE_LIMIT = 2 * 1024 * 1024
_FONT_FACE_LIMIT = 1024 * 1024
_FONT_FACE_KEYS = frozenset({
    "family", "data", "weight", "style", "unicode_range", "license_text",
    "has_cyrillic", "has_ruble",
})


def _font_unavailable() -> OrchestratorUnavailable:
    return OrchestratorUnavailable("Проверка шрифта временно недоступна. Повторите загрузку.")


def _validated_font_face(value: object) -> dict[str, Any]:
    """Validate the bounded wire envelope without parsing font tables in the API."""
    if not isinstance(value, dict) or set(value) != _FONT_FACE_KEYS:
        raise ValueError("font response shape")
    family, license_text = value["family"], value["license_text"]
    if (
        not isinstance(family, str) or not 1 <= len(family) <= 100 or not family.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in family)
        or not isinstance(license_text, str) or len(license_text) > 30000
        or any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127
               for char in license_text)
        or value["style"] not in ("normal", "italic")
        or type(value["has_cyrillic"]) is not bool or type(value["has_ruble"]) is not bool
    ):
        raise ValueError("font metadata")
    weight = value["weight"]
    if type(weight) is int:
        if not 1 <= weight <= 1000:
            raise ValueError("font weight")
    elif isinstance(weight, str) and len(weight) <= 32 and re.fullmatch(
        r"[0-9]+(?:\.[0-9]+)? [0-9]+(?:\.[0-9]+)?", weight,
    ):
        low, high = map(float, weight.split())
        if not 1 <= low <= high <= 1000:
            raise ValueError("font weight range")
    else:
        raise ValueError("font weight type")
    ranges = value["unicode_range"]
    if not isinstance(ranges, str) or not 1 <= len(ranges) <= 30000:
        raise ValueError("font coverage length")
    previous = -1
    for token in ranges.split(","):
        match = re.fullmatch(r"U\+([0-9A-F]{1,6})(?:-([0-9A-F]{1,6}))?", token)
        if match is None:
            raise ValueError("font coverage syntax")
        first, last = int(match[1], 16), int(match[2] or match[1], 16)
        if not previous < first <= last <= 0x10FFFF:
            raise ValueError("font coverage range")
        previous = last
    encoded = value["data"]
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 4 * ((_FONT_FACE_LIMIT + 2) // 3):
        raise ValueError("font data length")
    raw = base64.b64decode(encoded, validate=True)
    if (
        not 48 <= len(raw) <= _FONT_FACE_LIMIT
        or base64.b64encode(raw).decode("ascii") != encoded
    ):
        raise ValueError("font data encoding")
    (signature, flavor, length, tables, reserved, expanded, compressed, _major, _minor,
     meta_offset, meta_length, meta_original, private_offset, private_length) = struct.unpack_from(
        ">4s4sIHHIIHHIIIII", raw,
    )
    if (
        signature != b"wOF2" or flavor not in (b"\0\1\0\0", b"OTTO")
        or length != len(raw) or not 1 <= tables <= 100 or reserved != 0
        or not 1 <= expanded <= 16 * 1024 * 1024 or not 1 <= compressed <= len(raw) - 48
    ):
        raise ValueError("font header")
    for offset, size in ((meta_offset, meta_length), (private_offset, private_length)):
        if (offset == 0 and size != 0) or (
            offset != 0 and not 48 <= offset < offset + size <= length
        ):
            raise ValueError("font auxiliary block")
    if (meta_offset == 0) != (meta_original == 0) or meta_original > 16 * 1024 * 1024:
        raise ValueError("font metadata block")
    return value


async def validate_font(raw: bytes) -> dict[str, Any]:
    """Send one binary face to Core; never retry or run a local parser fallback."""
    if not 0 < len(raw) <= _FONT_INPUT_LIMIT:
        raise ApiError("validation_failed", "Допустимы TTF, OTF, WOFF и WOFF2 до 8 МБ", 422)
    settings = get_settings()
    token = (
        settings.orchestrator_internal_token.get_secret_value()
        if settings.orchestrator_internal_token else ""
    )
    if not token:
        raise _font_unavailable()
    path = "/internal/font-validation"
    try:
        async with asyncio_timeout(30.0):
            url = f"{await _base_url(path, 'core')}{path}"
            async with httpx.AsyncClient(
                timeout=30.0, trust_env=False, follow_redirects=False,
            ) as client, client.stream(
                "POST", url, content=raw,
                headers={"X-Internal-Token": token, "Content-Type": "application/octet-stream",
                         "Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response:
                if response.status_code == 422:
                    raise ApiError(
                        "validation_failed",
                        "Шрифт повреждён, не поддерживается или превышает лимит обработки. "
                        "Загрузите другой TTF, OTF, WOFF или WOFF2.", 422,
                    )
                if response.status_code != 200:
                    raise _font_unavailable()
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    != "application/json"
                    or response.headers.get("content-encoding", "identity").lower() != "identity"
                ):
                    raise ValueError("font response encoding")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isascii() or not length.isdecimal()
                    or int(length) > _FONT_RESPONSE_LIMIT
                ):
                    raise ValueError("font response length")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(body) + len(chunk) > _FONT_RESPONSE_LIMIT:
                        raise ValueError("font response budget")
                    body.extend(chunk)
                if length is not None and len(body) != int(length):
                    raise ValueError("font response truncated")
                return _validated_font_face(json.loads(body))
    except (
        OrchestratorUnavailable, httpx.HTTPError, TimeoutError,
        ValueError, TypeError, RecursionError,
    ):
        raise _font_unavailable() from None


_READER_RETRY_SECONDS = 30.0
_READER_RETRY_ATTEMPTS = 31
_READER_RETRY_MESSAGES = frozenset({
    "reader readiness is unavailable",
    "reader inventory is incompatible or stale",
    "reader epoch changed",
    "inventory changed during validation",
})


def _replayable_reader_request(
    method: str, path: str, payload: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> bool:
    """Only fenced reads and revision-CAS source writes are replayable.

    A fresh HTTP request reacquires the controller lock and checks the current
    generation lease, epoch and revision. Commands and lifecycle effects must
    keep their own reconciliation protocol; a 503 is not proof of no effects.
    """
    route = re.fullmatch(
        r"/internal/workspaces/([^/]+)/agent/(identity|snapshot|write-files)", path,
    )
    if route is None:
        return False
    if route[2] in {"identity", "snapshot"}:
        envelope = params if method == "GET" else None
    else:
        envelope = payload if method == "POST" else None
        if envelope is None or not _REQUEST_DIGEST_RE.fullmatch(
            str(envelope.get("expected_revision", ""))
        ):
            return False
    if envelope is None or type(envelope.get("fencing_epoch")) is not int:
        return False
    run_id = envelope.get("generation_run_id")
    if not isinstance(run_id, str):
        return False
    try:
        return bool(
            str(UUID(route[1])) == route[1]
            and envelope["fencing_epoch"] > 0
            and str(UUID(run_id)) == run_id
        )
    except ValueError:
        return False


def _reader_inventory_denied(response: httpx.Response) -> bool:
    if response.status_code != 503:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    error = payload.get("error") if isinstance(payload, dict) else None
    return (
        isinstance(error, dict)
        and error.get("code") == "workspace_busy"
        and isinstance(error.get("message"), str)
        and error["message"] in _READER_RETRY_MESSAGES
    )


async def _request_with_reader_retries(
    client: httpx.AsyncClient, method: str, url: str, *, path: str,
    payload: dict[str, Any] | None, params: dict[str, Any] | None,
    headers: dict[str, str], request_timeout: float,
) -> httpx.Response:
    replayable = _replayable_reader_request(method, path, payload, params)
    # Preserve the exact CAS/lease envelope even if a caller changes its objects
    # while the first request or the readiness wait is suspended.
    if replayable:
        payload, params = deepcopy(payload), deepcopy(params)
    deadline = monotonic() + request_timeout
    retry_deadline: float | None = None
    for attempt in range(_READER_RETRY_ATTEMPTS):
        options: dict[str, Any] = {}
        if attempt:
            options["timeout"] = max(0.001, deadline - monotonic())
        response = await client.request(
            method, url, json=payload, params=params, headers=headers, **options,
        )
        if not replayable or not _reader_inventory_denied(response):
            return response
        now = monotonic()
        if retry_deadline is None:
            retry_deadline = min(deadline, now + _READER_RETRY_SECONDS)
        if attempt == _READER_RETRY_ATTEMPTS - 1 or now + 1 >= retry_deadline:
            return response
        await sleep(1)
        if monotonic() >= retry_deadline:
            return response
    raise AssertionError("bounded reader retry did not return a response")


async def _request_raw(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109 - outbound request deadline
    host: str | None = None,
) -> Any:
    """Internal call to orchestrator. Returns parsed JSON body or raises ApiError.

    Note: orchestrator routes are all under `/internal/...` and require the
    `X-Internal-Token` header. The shared secret comes from settings — same
    string sits in /opt/omnia/apps/orchestrator/.env on prod.

    `timeout` defaults to 30s for normal requests. Long-running jobs (e.g.
    /build-exe which invokes PyInstaller + NSIS) should pass a higher value —
    see `build_exe()` which uses 360s.
    """
    settings = get_settings()
    token = (
        settings.orchestrator_internal_token.get_secret_value()
        if settings.orchestrator_internal_token
        else ""
    )
    if not token:
        # Not configured — fail fast, don't silently 200 with bogus data.
        raise OrchestratorUnavailable(
            "Orchestrator token is not configured (set ORCHESTRATOR_INTERNAL_TOKEN)."
        )

    url = f"{await _base_url(path, host)}{path}"
    headers = {"X-Internal-Token": token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await _request_with_reader_retries(
                client, method, url, path=path, payload=json, params=params,
                headers=headers, request_timeout=timeout,
            )
    except httpx.RequestError as exc:
        log.exception("orchestrator.network_error", path=path, err=str(exc))
        raise OrchestratorUnavailable(f"Cannot reach orchestrator at {url}") from exc

    if resp.status_code >= 500:
        log.error(
            "orchestrator.upstream_5xx",
            path=path,
            status=resp.status_code,
            body=resp.text[:300],
        )
        unavailable = (
            OrchestratorReaderInventoryUnavailable
            if _reader_inventory_denied(resp)
            else OrchestratorUnavailable
        )
        raise unavailable(
            f"Orchestrator returned {resp.status_code}",
            details={"upstream_body": resp.text[:300]},
        )
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text[:300]}
        message, upstream_code, details = _parse_orchestrator_4xx(payload)
        log.warning(
            "orchestrator.4xx",
            path=path,
            status=resp.status_code,
            message=message,
        )
        raise OrchestratorBadRequest(
            f"Orchestrator rejected request: {message}",
            status_code=resp.status_code,
            details=details,
            upstream_code=upstream_code,
        )

    try:
        return resp.json()
    except Exception as exc:
        raise OrchestratorUnavailable(
            f"Orchestrator returned non-JSON ({resp.status_code})"
        ) from exc


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109 - outbound request deadline
    host: str | None = None,
) -> dict[str, Any]:
    payload = await _request_raw(method, path, json=json, params=params, timeout=timeout, host=host)
    if not isinstance(payload, dict):
        raise OrchestratorUnavailable("Orchestrator returned an invalid object")
    return payload


# --- Public API ---------------------------------------------------------


async def get_status(project_id: UUID) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/status — current runtime state."""
    return await _request("GET", f"/internal/projects/{project_id}/status")


async def get_project_cell_capabilities(project_id: UUID) -> dict[str, Any]:
    """Read the dark Project Cell capability status for one project."""
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/workspace/capabilities",
    )


async def project_cell_agent_bootstrap(
    workspace_id: UUID,
    *,
    generation_run_id: UUID | None,
    fencing_epoch: int,
) -> ProjectCellAgentWorkspaceSnapshot:
    _validate_fencing_epoch(fencing_epoch)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/agent/bootstrap",
        json={
            "generation_run_id": (
                str(generation_run_id) if generation_run_id is not None else None
            ),
            "fencing_epoch": fencing_epoch,
        },
    )
    return ProjectCellAgentWorkspaceSnapshot.from_json(payload)


async def project_cell_agent_snapshot(
    workspace_id: UUID, *, generation_run_id: UUID, fencing_epoch: int,
) -> ProjectCellAgentWorkspaceSnapshot:
    """Read physical source under its current lease; never seed an empty tree."""
    _validate_fencing_epoch(fencing_epoch)
    payload = await _request(
        "GET", f"/internal/workspaces/{workspace_id}/agent/snapshot",
        params={"generation_run_id": str(generation_run_id), "fencing_epoch": fencing_epoch},
    )
    snapshot = ProjectCellAgentWorkspaceSnapshot.from_json(payload)
    if (
        snapshot.generation_run_id != generation_run_id
        or snapshot.fencing_epoch != fencing_epoch or snapshot.seeded_from_project
    ):
        raise OrchestratorUnavailable("Orchestrator returned a mismatched source snapshot")
    return snapshot


async def project_cell_prepare_restoration_adaptation(
    workspace_id: UUID,
    *,
    operation_id: UUID,
    project_id: UUID,
    owner_id: UUID,
    generation_run_id: UUID,
    fencing_epoch: int,
    source_workspace_revision: str,
    source_snapshot_id: UUID,
    base_draft_snapshot_id: UUID,
    source_commit_sha: str,
    adaptation_bundle_digest: str,
) -> RestorationAdaptationWorkspace:
    _validate_fencing_epoch(fencing_epoch)
    _validate_workspace_revision(source_workspace_revision)
    if re.fullmatch(r"[0-9a-f]{40}", source_commit_sha) is None:
        raise ValueError("source_commit_sha must be a Git SHA")
    _validate_request_digest(adaptation_bundle_digest)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/restoration-adaptations/{generation_run_id}/prepare",
        json={
            "workspace_id": str(workspace_id),
            "operation_id": str(operation_id),
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            "generation_run_id": str(generation_run_id),
            "fencing_epoch": fencing_epoch,
            "source_workspace_revision": source_workspace_revision,
            "source_snapshot_id": str(source_snapshot_id),
            "base_draft_snapshot_id": str(base_draft_snapshot_id),
            "source_commit_sha": source_commit_sha,
            "adaptation_bundle_digest": adaptation_bundle_digest,
        },
        timeout=900.0,
    )
    response = RestorationAdaptationWorkspace.from_json(payload)
    if (
        response.source_workspace_id != workspace_id
        or response.operation_id != operation_id
        or response.project_id != project_id
        or response.owner_id != owner_id
        or response.generation_run_id != generation_run_id
    ):
        raise OrchestratorUnavailable(
            "Orchestrator returned a foreign restoration adaptation workspace"
        )
    # Кандидата создал тот оркестратор, которому ушёл этот запрос, и в реестре
    # ячеек его нет — он внутренняя копия, а не ячейка владельца. Привязка
    # ставится здесь, в единственном месте, где id кандидата появляется на свет:
    # любой вызывающий, который сделает это сам, рано или поздно забудет. Живой
    # отказ 23.09: запрос по кандидату ушёл на хост по умолчанию и получил
    # «workspace state not found», адаптивный откат умер на первом шаге.
    from yleum_api.services.orchestrator_hosts import bind_candidate_to_source_host

    await bind_candidate_to_source_host(response.candidate_workspace_id, workspace_id)
    return response


async def project_cell_cleanup_restoration_adaptation(
    workspace: RestorationAdaptationWorkspace,
    *,
    recovery_cycle: int | None = None,
    recovery_receipt_digest: str | None = None,
) -> None:
    if (recovery_cycle is None) != (recovery_receipt_digest is None) or (
        recovery_cycle is not None and (
            type(recovery_cycle) is not int or recovery_cycle < 0
            or type(recovery_receipt_digest) is not str
            or _REQUEST_DIGEST_RE.fullmatch(recovery_receipt_digest) is None
        )
    ):
        raise OrchestratorUnavailable("Invalid adaptation cleanup cycle binding")
    body: dict[str, object] = {
        "workspace_id": str(workspace.source_workspace_id),
        "generation_run_id": str(workspace.generation_run_id),
        "candidate_workspace_id": str(workspace.candidate_workspace_id),
        "candidate_fencing_epoch": workspace.candidate_fencing_epoch,
        "proof_digest": workspace.proof_digest,
    }
    if recovery_cycle is not None:
        body.update(recovery_cycle=recovery_cycle, recovery_receipt_digest=recovery_receipt_digest)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace.source_workspace_id}/restoration-adaptations/"
        f"{workspace.generation_run_id}/cleanup", json=body, timeout=120.0,
    )
    if payload != {"state": "cleaned"}:
        raise OrchestratorUnavailable(
            "Orchestrator returned an invalid restoration adaptation cleanup receipt"
        )


async def project_cell_prove_restoration_adaptation(
    workspace: RestorationAdaptationWorkspace,
    *,
    candidate_workspace_revision: str,
    candidate_proof_key: str,
    candidate_artifact_digest: str,
    proof_attempt: int,
) -> RestorationAdaptationProof:
    if type(proof_attempt) is not int or proof_attempt < 1:
        raise ValueError("proof_attempt must be a positive integer")
    for value in (
        candidate_workspace_revision,
        candidate_proof_key,
        candidate_artifact_digest,
    ):
        _validate_request_digest(value)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace.source_workspace_id}/restoration-adaptations/"
        f"{workspace.generation_run_id}/proof",
        json={
            "workspace_id": str(workspace.source_workspace_id),
            "operation_id": str(workspace.operation_id),
            "project_id": str(workspace.project_id),
            "owner_id": str(workspace.owner_id),
            "generation_run_id": str(workspace.generation_run_id),
            "candidate_workspace_id": str(workspace.candidate_workspace_id),
            "candidate_fencing_epoch": workspace.candidate_fencing_epoch,
            "preparation_proof_digest": workspace.proof_digest,
            "source_database_digest": workspace.source_database_digest,
            "candidate_workspace_revision": candidate_workspace_revision,
            "candidate_proof_key": candidate_proof_key,
            "candidate_artifact_digest": candidate_artifact_digest,
            "proof_attempt": proof_attempt,
        },
        timeout=900.0,
    )
    result = RestorationAdaptationProof.from_json(payload)
    if (
        result.source_workspace_id != workspace.source_workspace_id
        or result.candidate_workspace_id != workspace.candidate_workspace_id
        or result.operation_id != workspace.operation_id
        or result.project_id != workspace.project_id
        or result.owner_id != workspace.owner_id
        or result.generation_run_id != workspace.generation_run_id
        or result.candidate_fencing_epoch != workspace.candidate_fencing_epoch
        or result.candidate_workspace_revision != candidate_workspace_revision
        or result.candidate_proof_key != candidate_proof_key
        or result.candidate_artifact_digest != candidate_artifact_digest
        or result.proof_attempt != proof_attempt
    ):
        raise OrchestratorUnavailable(
            "Orchestrator returned a foreign restoration adaptation proof"
        )
    return result


async def project_cell_offer_restoration_adaptation_activation(
    request: RestorationAdaptationActivationOfferRequest,
) -> RestorationAdaptationActivationOffer:
    payload = await _request(
        "POST",
        f"/internal/workspaces/{request.workspace_id}/restoration-adaptations/"
        f"{request.generation_run_id}/activation-offer",
        json=request.model_dump(mode="json"),
        timeout=120.0,
    )
    try:
        result = RestorationAdaptationActivationOffer.model_validate_json(
            json.dumps(payload), strict=True
        )
    except (TypeError, ValueError) as exc:
        raise OrchestratorUnavailable(
            "Orchestrator returned an invalid restoration adaptation activation offer"
        ) from exc
    if (
        result.workspace_id != request.workspace_id
        or result.operation_id != request.operation_id
        or result.project_id != request.project_id
        or result.owner_id != request.owner_id
        or result.generation_run_id != request.generation_run_id
        or result.candidate_workspace_id != request.candidate_workspace_id
        or result.candidate_fencing_epoch != request.candidate_fencing_epoch
        or result.candidate_workspace_revision != request.candidate_workspace_revision
        or result.proof_attempt != request.proof_attempt
        or result.proof_digest != request.proof_digest
    ):
        raise OrchestratorUnavailable(
            "Orchestrator returned a foreign restoration adaptation activation offer"
        )
    return result


async def _project_cell_restoration_adaptation_activation_action(
    command: RestorationAdaptationActivationCommand,
    action: Literal["apply", "status", "cancel"],
) -> RestorationAdaptationActivationStatus:
    offer = command.offer
    payload = await _request(
        "POST",
        f"/internal/workspaces/{offer.workspace_id}/restoration-adaptations/"
        f"{offer.generation_run_id}/activations/{offer.activation_id}/{action}",
        json=command.model_dump(mode="json"),
        timeout=930.0 if action == "apply" else 120.0,
    )
    try:
        result = RestorationAdaptationActivationStatus.model_validate_json(
            json.dumps(payload), strict=True
        )
    except (TypeError, ValueError) as exc:
        raise OrchestratorUnavailable(
            "Orchestrator returned an invalid restoration adaptation activation receipt"
        ) from exc
    if result.offer != offer or result.planned_commit_sha != command.planned_commit_sha:
        raise OrchestratorUnavailable(
            "Orchestrator returned a foreign restoration adaptation activation receipt"
        )
    return result


async def project_cell_apply_restoration_adaptation_activation(
    command: RestorationAdaptationActivationCommand,
) -> RestorationAdaptationActivationStatus:
    return await _project_cell_restoration_adaptation_activation_action(command, "apply")


async def project_cell_status_restoration_adaptation_activation(
    command: RestorationAdaptationActivationCommand,
) -> RestorationAdaptationActivationStatus:
    return await _project_cell_restoration_adaptation_activation_action(command, "status")


async def project_cell_cancel_restoration_adaptation_activation(
    command: RestorationAdaptationActivationCommand,
) -> RestorationAdaptationActivationStatus:
    return await _project_cell_restoration_adaptation_activation_action(command, "cancel")


async def project_cell_update_restoration_adaptation_owner_status(
    *,
    workspace_id: UUID,
    operation_id: UUID,
    project_id: UUID,
    owner_id: UUID,
    generation_run_id: UUID,
    state: Literal["active", "terminal"],
) -> None:
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/restoration-adaptations/"
        f"{generation_run_id}/owner-status",
        json={
            "workspace_id": str(workspace_id),
            "operation_id": str(operation_id),
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            "generation_run_id": str(generation_run_id),
            "state": state,
        },
        timeout=30.0,
    )
    if payload != {"state": state}:
        raise OrchestratorUnavailable(
            "Orchestrator returned an invalid restoration adaptation owner status"
        )


async def project_cell_agent_write_files(
    workspace_id: UUID,
    *,
    generation_run_id: UUID | None,
    fencing_epoch: int,
    expected_revision: str,
    files: dict[str, str],
    deletes: Sequence[str] = (),
    operation_id: UUID | None = None,
) -> ProjectCellAgentWriteResponse:
    if type(files) is not dict:
        raise ValueError("files must be an object")
    for raw_path, raw_content in files.items():
        if type(raw_path) is not str or type(raw_content) is not str:
            raise ValueError("files must be a string-to-string mapping")
    normalized_deletes: list[str] = []
    for raw_path in deletes:
        if type(raw_path) is not str:
            raise ValueError("deletes must contain only strings")
        normalized_deletes.append(raw_path)
    if set(files).intersection(normalized_deletes):
        raise ValueError("the same path cannot be written and deleted")
    _validate_fencing_epoch(fencing_epoch)
    _validate_workspace_revision(expected_revision)
    body: dict[str, Any] = {
        "generation_run_id": str(generation_run_id) if generation_run_id is not None else None,
        "fencing_epoch": fencing_epoch, "expected_revision": expected_revision,
        "files": files, "deletes": normalized_deletes,
    }
    if operation_id is not None:
        if not isinstance(operation_id, UUID) or generation_run_id is None:
            raise ValueError("tracked writes require operation and generation identities")
        body["operation_id"] = str(operation_id)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/agent/write-files",
        json=body,
        # The first guarded write can resume a retained machine and its database.
        # Match the bounded cold-start allowance used by owner preview startup.
        timeout=300.0,
    )
    result = ProjectCellAgentWriteResponse.from_json(payload)
    if operation_id is not None:
        if result.receipt is None or generation_run_id is None:
            raise OrchestratorUnavailable("Tracked source write has no terminal receipt")
        result.receipt.require_identity(workspace_id, generation_run_id, operation_id)
        if (result.receipt.fencing_epoch, result.receipt.before_revision,
            result.receipt.after_revision, result.receipt.written, result.receipt.deleted) != (
            fencing_epoch, expected_revision, result.workspace_revision,
            result.written, result.deleted,
        ):
            raise OrchestratorUnavailable("Tracked source write receipt conflicts with response")
    elif result.receipt is not None:
        raise OrchestratorUnavailable("Untracked source write returned an unexpected receipt")
    return result


async def project_cell_agent_write_operation(
    workspace_id: UUID, *, operation_id: UUID, generation_run_id: UUID,
) -> ProjectCellSourceWriteStatus:
    payload = await _request(
        "GET", f"/internal/workspaces/{workspace_id}/agent/write-operations/{operation_id}",
        params={"generation_run_id": str(generation_run_id)},
    )
    if (set(payload) != {"operation_id", "state", "receipt"}
        or payload["operation_id"] != str(operation_id)
        or payload["state"] not in ("pending", "terminal")):
        raise OrchestratorUnavailable("Orchestrator returned an invalid source write status")
    receipt = (ProjectCellSourceWriteReceipt.from_json(payload["receipt"])
               if payload["receipt"] is not None else None)
    if (payload["state"] == "terminal") != (receipt is not None):
        raise OrchestratorUnavailable("Source write terminal status has no matching receipt")
    if receipt is not None:
        receipt.require_identity(workspace_id, generation_run_id, operation_id)
    return ProjectCellSourceWriteStatus(
        operation_id, cast(Literal["pending", "terminal"], payload["state"]), receipt,
    )


@dataclass(frozen=True)
class ProjectCellDraftFiles:
    """The owner's draft as stored in the cell, read outside any generation lease."""

    files: dict[str, str]
    workspace_revision: str

    def __post_init__(self) -> None:
        _validate_workspace_revision(self.workspace_revision)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellDraftFiles:
        if set(payload) != {"files", "workspace_revision"}:
            raise OrchestratorUnavailable("Orchestrator returned an invalid draft files response")
        files = payload.get("files")
        revision = payload.get("workspace_revision")
        valid_entries = type(files) is dict and all(
            type(path) is str and type(content) is str for path, content in files.items()
        )
        if not valid_entries or type(revision) is not str:
            raise OrchestratorUnavailable("Orchestrator returned an invalid draft files response")
        assert type(files) is dict
        return cls(files=dict(files), workspace_revision=revision)


async def project_cell_draft_files(workspace_id: UUID) -> ProjectCellDraftFiles:
    payload = await _request("GET", f"/internal/workspaces/{workspace_id}/draft/files")
    return ProjectCellDraftFiles.from_json(payload)


async def project_cell_draft_reset(
    workspace_id: UUID,
    *,
    expected_revision: str,
    files: dict[str, str],
    deletes: Sequence[str] = (),
) -> ProjectCellAgentWriteResponse:
    """Rewrite draft files between generations («отбросить правки»)."""
    if type(files) is not dict or any(
        type(path) is not str or type(content) is not str for path, content in files.items()
    ):
        raise ValueError("files must be a string-to-string mapping")
    normalized_deletes = [path for path in deletes]
    if any(type(path) is not str for path in normalized_deletes):
        raise ValueError("deletes must contain only strings")
    if set(files).intersection(normalized_deletes):
        raise ValueError("the same path cannot be written and deleted")
    _validate_workspace_revision(expected_revision)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/draft/reset",
        json={
            "expected_revision": expected_revision,
            "files": files,
            "deletes": normalized_deletes,
        },
    )
    return ProjectCellAgentWriteResponse.from_json(payload)


async def project_cell_agent_exec(
    workspace_id: UUID,
    cmd: str,
    *,
    generation_run_id: UUID | None,
    fencing_epoch: int,
    expected_revision: str,
    timeout_seconds: int = 180,
    task_role: str | None = None,
    operation_id: UUID | None = None,
    track_receipt: bool = False,
) -> ProjectCellAgentExecResponse:
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("cmd must be a non-empty string")
    if timeout_seconds <= 0 or timeout_seconds > 900:
        raise ValueError("timeout_seconds must be between 1 and 900")
    _validate_fencing_epoch(fencing_epoch)
    _validate_workspace_revision(expected_revision)
    if task_role not in (
        None,
        "bootstrap",
        "fast_check",
        "full_build",
        "restore_runtime",
        "build",
        "test",
    ):
        raise ValueError("invalid portable task role")
    if type(track_receipt) is not bool or (track_receipt and (
        generation_run_id is None or operation_id is None
    )):
        raise ValueError("tracked command requires an exact run and operation")
    request = {
        "generation_run_id": str(generation_run_id) if generation_run_id is not None else None,
        "fencing_epoch": fencing_epoch, "expected_revision": expected_revision,
        "cmd": cmd, "timeout_seconds": timeout_seconds,
        **({"task_role": task_role} if task_role is not None or track_receipt else {}),
        **({"operation_id": str(operation_id)} if operation_id is not None else {}),
        **({"track_receipt": True} if track_receipt else {}),
    }
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/agent/exec",
        json=request,
        timeout=float(timeout_seconds + 30),
    )
    result = ProjectCellAgentExecResponse.from_json(payload)
    if track_receipt:
        assert generation_run_id is not None and operation_id is not None
        receipt = result.command_receipt
        if receipt is None:
            raise OrchestratorUnavailable("Orchestrator omitted tracked command receipt")
        receipt.require_identity(workspace_id, generation_run_id, operation_id)
        if (receipt.dispatch_fencing_epoch != fencing_epoch
            or receipt.before_revision != expected_revision
            or receipt.request_sha256 != command_wire_digest({
                "protocol": "tracked-command/v1", "workspace_id": str(workspace_id),
                "request": request,
            })):
            raise OrchestratorUnavailable("Orchestrator command request binding mismatch")
    elif result.command_receipt is not None:
        raise OrchestratorUnavailable("Orchestrator returned an unsolicited command receipt")
    return result


async def project_cell_agent_identity(
    workspace_id: UUID,
    *,
    generation_run_id: UUID,
    fencing_epoch: int,
) -> ProjectCellWorkspaceIdentity:
    _validate_fencing_epoch(fencing_epoch)
    try:
        payload = await _request(
            "GET", f"/internal/workspaces/{workspace_id}/agent/identity",
            params={"generation_run_id": str(generation_run_id), "fencing_epoch": fencing_epoch},
        )
    except OrchestratorBadRequest as error:
        details = error.details
        if (error.status_code == 409 and error.upstream_code == "container_not_running"
                and type(details) is dict
                and set(details) == {"reason", "workspace_revision", "manifest_digest"}
                and type(details.get("reason")) is str
                and details.get("reason") in {
                    "stopped", "missing", "unavailable", "prepare_pending",
                }
                and all(isinstance(details.get(key), str)
                        and re.fullmatch(r"[0-9a-f]{64}", details[key])
                        for key in ("workspace_revision", "manifest_digest"))):
            raise OrchestratorMachineInventoryUnavailable(
                details["reason"], details["workspace_revision"], details["manifest_digest"],
            ) from error
        raise
    return ProjectCellWorkspaceIdentity.from_json(payload)


async def project_cell_prepare_environment(
    workspace_id: UUID, *, project_id: UUID, owner_id: UUID, generation_run_id: UUID,
    fencing_epoch: int, expected_revision: str, manifest_digest: str,
) -> ProjectCellWorkspaceIdentity:
    _validate_fencing_epoch(fencing_epoch)
    _validate_workspace_revision(expected_revision)
    _validate_workspace_revision(manifest_digest)
    payload = await _request(
        "POST", f"/internal/workspaces/{workspace_id}/agent/environment/prepare",
        json={"project_id": str(project_id), "owner_id": str(owner_id),
              "generation_run_id": str(generation_run_id), "fencing_epoch": fencing_epoch,
              "expected_revision": expected_revision, "manifest_digest": manifest_digest},
        timeout=120,
    )
    operation_id = uuid5(generation_run_id, f"machine-startup-prepare/v1:{fencing_epoch}")
    if (
        type(payload) is not dict
        or set(payload) != {"operation_id", "binding_sha256", "startup_incarnation", "identity"}
        or payload.get("operation_id") != str(operation_id)
        or any(
            type(payload.get(key)) is not str
            or re.fullmatch(r"[0-9a-f]{64}", payload[key]) is None
            for key in ("binding_sha256", "startup_incarnation")
        )
    ):
        raise OrchestratorUnavailable("Orchestrator returned an invalid startup preparation")
    identity = ProjectCellWorkspaceIdentity.from_json(payload["identity"])
    if (identity.workspace_revision, identity.cell_manifest_digest) != (
        expected_revision, manifest_digest,
    ):
        raise OrchestratorUnavailable("Orchestrator startup source identity changed")
    return identity


async def project_cell_agent_command_operation(
    workspace_id: UUID, *, operation_id: UUID, generation_run_id: UUID, fencing_epoch: int,
) -> ProjectCellCommandStatus:
    _validate_fencing_epoch(fencing_epoch)
    payload = await _request(
        "GET", f"/internal/workspaces/{workspace_id}/agent/command-operations/{operation_id}",
        params={"generation_run_id": str(generation_run_id), "fencing_epoch": fencing_epoch},
    )
    if (type(payload) is not dict or set(payload) != {
        "operation_id", "state", "receipt", "terminal_response",
    } or payload["operation_id"] != str(operation_id)
        or payload["state"] not in {"unknown", "pending", "terminal"}):
        raise OrchestratorUnavailable("Orchestrator returned an invalid command status")
    state = cast(Literal["unknown", "pending", "terminal"], payload["state"])
    if state != "terminal":
        if payload["receipt"] is not None or payload["terminal_response"] is not None:
            raise OrchestratorUnavailable("Nonterminal command has terminal evidence")
        return ProjectCellCommandStatus(operation_id, state, None, None)
    receipt = ProjectCellCommandReceipt.from_json(payload["receipt"])
    raw = payload["terminal_response"]
    if type(raw) is not dict or "command_receipt" in raw:
        raise OrchestratorUnavailable("Orchestrator omitted the terminal command response")
    response = ProjectCellAgentExecResponse.from_json(raw)
    receipt.require_identity(workspace_id, generation_run_id, operation_id)
    receipt.require_response(response)
    return ProjectCellCommandStatus(operation_id, state, receipt, response)


async def project_cell_agent_operation_status(
    workspace_id: UUID,
    operation_id: UUID,
) -> ProjectCellAgentOperationStatus:
    payload = await _request(
        "GET",
        f"/internal/workspaces/{workspace_id}/agent/operations/{operation_id}",
    )
    return ProjectCellAgentOperationStatus.from_json(payload)


async def project_cell_apply_draft(
    workspace_id: UUID,
    *,
    generation_run_id: UUID,
    fencing_epoch: int,
    expected_revision: str,
    files: dict[str, str],
    deletes: Sequence[str] = (),
) -> ProjectCellDraftApplyResponse:
    _validate_fencing_epoch(fencing_epoch)
    _validate_workspace_revision(expected_revision)
    if any(type(k) is not str or type(v) is not str for k, v in files.items()):
        raise ValueError("files must be a string-to-string mapping")
    if any(type(path) is not str for path in deletes) or set(files).intersection(deletes):
        raise ValueError("invalid draft deletes")
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/draft/apply",
        json={
            "generation_run_id": str(generation_run_id),
            "fencing_epoch": fencing_epoch,
            "expected_revision": expected_revision,
            "files": files,
            "deletes": list(deletes),
        },
        # Server machine apply has one 900s aggregate budget including build,
        # capture and readiness. Keep a transport/cleanup margin outside it.
        timeout=930.0,
    )
    return ProjectCellDraftApplyResponse.from_json(payload)


async def project_cell_create_preview_session(
    workspace_id: UUID,
    *,
    generation_run_id: UUID,
    fencing_epoch: int,
) -> ProjectCellPreviewSession:
    _validate_fencing_epoch(fencing_epoch)
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/draft/preview-session",
        json={"generation_run_id": str(generation_run_id), "fencing_epoch": fencing_epoch},
    )
    response = ProjectCellPreviewSession.from_json(payload)
    if response.workspace_id != workspace_id:
        raise OrchestratorUnavailable("Orchestrator returned a different cell preview")
    return response


async def project_cell_create_owner_preview_session(
    workspace_id: UUID,
    *,
    project_id: UUID,
    owner_id: UUID,
) -> ProjectCellPreviewSession:
    """Mint viewing access independently of the generation mutation lease."""
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/draft/owner-preview-session",
        json={"project_id": str(project_id), "owner_id": str(owner_id)},
    )
    response = ProjectCellPreviewSession.from_json(payload)
    if response.workspace_id != workspace_id:
        raise OrchestratorUnavailable("Orchestrator returned a different cell preview")
    return response


async def project_cell_start_owner_preview(
    workspace_id: UUID,
    *,
    project_id: UUID,
    owner_id: UUID,
) -> ProjectCellPreviewSession:
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/draft/owner-start",
        json={"project_id": str(project_id), "owner_id": str(owner_id)},
        # Retained portable previews restore their environment, database and
        # services before publishing TLS. The observed cold start exceeded 120s;
        # keep a bounded restoration budget instead of returning a false 503.
        timeout=300.0,
    )
    response = ProjectCellPreviewSession.from_json(payload)
    if response.workspace_id != workspace_id:
        raise OrchestratorUnavailable("Orchestrator returned a different cell preview")
    return response


async def project_cell_apply_business_config(
    workspace_id: UUID,
    *,
    project_id: UUID,
    owner_id: UUID,
    version: int,
    config: dict[str, Any],
    generation_run_id: UUID | None = None,
    fencing_epoch: int | None = None,
) -> bool:
    if (generation_run_id is None) != (fencing_epoch is None):
        raise ValueError("configuration seed requires complete generation authority")
    generation_authority = (
        {"generation_run_id": str(generation_run_id), "fencing_epoch": fencing_epoch}
        if generation_run_id is not None else {}
    )
    payload = await _request(
        "PUT",
        f"/internal/workspaces/{workspace_id}/owner-business-config",
        json={
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            "version": version,
            "config": config,
            **generation_authority,
        },
        timeout=300.0,
    )
    applied = payload.get("applied")
    if (
        payload.get("workspace_id") != str(workspace_id)
        or payload.get("version") != version
        or not isinstance(applied, bool)
    ):
        raise OrchestratorUnavailable("Orchestrator returned invalid MAX configuration status")
    return applied


async def set_keep_alive(project_id: UUID, *, enabled: bool) -> dict[str, Any]:
    """Persist whether the project's dev runtime may be auto-hibernated."""
    return await _request(
        "POST",
        "/internal/projects/keep-alive",
        json={"project_id": str(project_id), "enabled": enabled},
    )


async def publish_project_cell(project_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit durable public release; private payload is never logged."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/cell-deploy",
        json=payload,
        timeout=30.0,
    )


async def configure_published_cell(project_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """Update only controller-owned public MAX configuration, not generated code."""
    return await _request(
        "PUT",
        f"/internal/projects/{project_id}/cell-deploy/config",
        json=payload,
        timeout=120.0,
    )


async def published_cell_config_status(
    project_id: UUID, payload: dict[str, Any],
) -> dict[str, Any]:
    return await _request(
        "POST", f"/internal/projects/{project_id}/cell-deploy/config/status",
        json=payload, timeout=6.0,
    )


async def project_cell_business_config_status(
    workspace_id: UUID, payload: dict[str, Any],
) -> dict[str, Any]:
    return await _request(
        "POST", f"/internal/workspaces/{workspace_id}/owner-business-config/status",
        json=payload, timeout=6.0,
    )


async def get_publication_migration(project_id: UUID, owner_id: UUID) -> dict[str, Any]:
    return await _request(
        "GET", f"/internal/projects/{project_id}/publication-migrations",
        params={"owner_id": str(owner_id)},
    )


async def prepare_publication_migration(
    project_id: UUID, payload: dict[str, Any],
) -> dict[str, Any]:
    return await _request(
        "POST", f"/internal/projects/{project_id}/publication-migrations", json=payload,
    )


async def confirm_publication_migration(
    project_id: UUID, operation_id: UUID, payload: dict[str, Any],
) -> dict[str, Any]:
    return await _request(
        "POST", f"/internal/projects/{project_id}/publication-migrations/{operation_id}/confirm",
        json=payload,
    )


async def get_deploy(project_id: UUID) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/deploy — last-known deploy record.

    Returns the orchestrator's `DeployResponse` shape:
    `{project_id, phase, prod_url, image_tag, started_at, finished_at, error}`.
    `phase` is one of `queued | building | swapping | done | failed`. For a
    project that has never been deployed the orchestrator returns
    `phase=queued` with no prod_url.
    """
    return await _request("GET", f"/internal/projects/{project_id}/deploy")


async def get_deploy_history(project_id: UUID) -> list[dict[str, Any]]:
    result = await _request_raw("GET", f"/internal/projects/{project_id}/deploy/history")
    if not isinstance(result, list):
        raise OrchestratorUnavailable("Orchestrator returned invalid deploy history")
    return cast(list[dict[str, Any]], result)


async def destroy(project_id: UUID, slug: str) -> dict[str, Any]:
    """POST /internal/projects/<uuid>/destroy?slug=<slug> — full teardown.

    Removes dev+prod containers, releases ports, archives the per-project
    Postgres schema, removes nginx vhosts. Idempotent on the orchestrator side,
    so a retry after a partial failure is safe. `slug` is required as a query
    param (orchestrator looks containers up by `omnia-dev-<slug>`)."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/destroy",
        params={"slug": slug},
    )


# ── Agentic builder tools (Phase 0) ─────────────────────────────────────────
# Thin wrappers the api-side agent loop (services/agent_builder.py) calls to act
# on the live dev container. Each maps to a /agent/* orchestrator endpoint.


async def agent_grep(project_id: UUID, slug: str, *, pattern: str, path: str = "src") -> str:
    """Recursive text search under /app; returns matches (or '(no matches)')."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/grep",
        params={"slug": slug, "pattern": pattern, "path": path},
    )
    detail = resp.get("detail")
    return detail if isinstance(detail, str) else ""


async def hot_reload(
    project_id: UUID,
    slug: str,
    files: dict[str, str],
    *,
    base_workspace_revision: str | None = None,
    empty_files: Sequence[str] = (),
) -> dict[str, Any]:
    """POST /internal/projects/hot-reload — write AI-generated files into the
    dev container. MAX runtimes apply direct ``drizzle/*.sql`` files through
    the platform-owned runner and reject a failed apply after reporting that
    source writes already landed. Other templates retain ``drizzle-kit push``
    for schema and legacy migration-directory changes.

    `slug` is required as a query param because orchestrator's container
    lookup is `omnia-dev-<slug>` (no project_id ↔ container_name registry
    yet, PoC). apps/api always has the slug at hand from its own Project row.
    """
    payload: dict[str, Any] = {"project_id": str(project_id), "files": files}
    normalized_empty_files: list[str] = []
    for raw_path in empty_files:
        if type(raw_path) is not str:
            raise ValueError("empty_files must contain only strings")
        normalized_empty_files.append(raw_path)
    if base_workspace_revision:
        payload["base_workspace_revision"] = base_workspace_revision
    if normalized_empty_files:
        payload["empty_files"] = normalized_empty_files
    return await _request(
        "POST",
        "/internal/projects/hot-reload",
        json=payload,
        params={"slug": slug},
        timeout=1800.0,
    )
