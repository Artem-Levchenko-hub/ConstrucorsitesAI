"""HTTP client to the V2 orchestrator (`apps/orchestrator` on :8003).

apps/api is a thin authenticated proxy: it owns the JWT cookie and the
ownership check, then forwards the request to orchestrator with a shared
X-Internal-Token header. Errors from orchestrator are translated into our
ApiError taxonomy so the public response shape stays consistent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import httpx
import structlog

from omnia_api.core.config import get_settings
from omnia_api.core.errors import ApiError

log = structlog.get_logger(__name__)

_REQUEST_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
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
class ProjectCellAgentWriteResponse:
    written: int
    deleted: int
    workspace_revision: str

    def __post_init__(self) -> None:
        if self.written < 0 or self.deleted < 0:
            raise ValueError("written and deleted must be zero or positive")
        _validate_workspace_revision(self.workspace_revision)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellAgentWriteResponse:
        expected = {"written", "deleted", "workspace_revision"}
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
            )
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell write response"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProjectCellAgentExecResponse:
    ok: bool
    exit_code: int
    detail: str
    timed_out: bool
    workspace_revision: str

    def __post_init__(self) -> None:
        _validate_workspace_revision(self.workspace_revision)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ProjectCellAgentExecResponse:
        expected = {"ok", "exit_code", "detail", "timed_out", "workspace_revision"}
        unexpected = set(payload) - expected
        if unexpected:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell exec response"
            )
        ok = payload.get("ok")
        exit_code = payload.get("exit_code")
        detail = payload.get("detail")
        timed_out = payload.get("timed_out")
        workspace_revision = payload.get("workspace_revision")
        if (
            type(ok) is not bool
            or type(exit_code) is not int
            or type(detail) is not str
            or type(timed_out) is not bool
            or type(workspace_revision) is not str
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
            )
        except ValueError as exc:
            raise OrchestratorUnavailable(
                "Orchestrator returned an invalid Project Cell exec response"
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
    suffix = get_settings().project_cell_preview_host_suffix
    expected_host = f"cell-{session.workspace_id.hex[:12]}-dev.{suffix}"
    if (
        preview.scheme != "https"
        or preview.hostname != expected_host
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


class HttpProjectCellOrchestratorClient:
    async def ensure(
        self,
        request: EnsureProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        try:
            payload = await _request(
                "POST",
                "/internal/workspaces/ensure",
                json=request.to_wire_json(),
            )
        except OrchestratorBadRequest as exc:
            if exc.status_code != 429:
                raise
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
        return ProjectCellResourceResponse.from_json(payload)

    async def control(
        self,
        request: ControlProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        timeout = 930.0 if request.kind in {"pause", "stop", "destroy", "restore"} else 30.0
        payload = await _request(
            "POST",
            f"/internal/workspaces/{request.workspace_id}/control",
            json=request.to_wire_json(),
            timeout=timeout,
        )
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


async def _request_raw(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109 - outbound request deadline
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

    url = f"{settings.orchestrator_url.rstrip('/')}{path}"
    headers = {"X-Internal-Token": token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=json, params=params, headers=headers)
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
        raise OrchestratorUnavailable(
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
) -> dict[str, Any]:
    payload = await _request_raw(method, path, json=json, params=params, timeout=timeout)
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


async def project_cell_agent_write_files(
    workspace_id: UUID,
    *,
    generation_run_id: UUID | None,
    fencing_epoch: int,
    expected_revision: str,
    files: dict[str, str],
    deletes: Sequence[str] = (),
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
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/agent/write-files",
        json={
            "generation_run_id": (
                str(generation_run_id) if generation_run_id is not None else None
            ),
            "fencing_epoch": fencing_epoch,
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
) -> ProjectCellAgentExecResponse:
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("cmd must be a non-empty string")
    if timeout_seconds <= 0 or timeout_seconds > 900:
        raise ValueError("timeout_seconds must be between 1 and 900")
    _validate_fencing_epoch(fencing_epoch)
    _validate_workspace_revision(expected_revision)
    if task_role not in (None, "bootstrap", "build", "test"):
        raise ValueError("invalid portable task role")
    payload = await _request(
        "POST",
        f"/internal/workspaces/{workspace_id}/agent/exec",
        json={
            "generation_run_id": (
                str(generation_run_id) if generation_run_id is not None else None
            ),
            "fencing_epoch": fencing_epoch,
            "expected_revision": expected_revision,
            "cmd": cmd,
            "timeout_seconds": timeout_seconds,
            **({"task_role": task_role} if task_role is not None else {}),
            **({"operation_id": str(operation_id)} if operation_id is not None else {}),
        },
        timeout=float(timeout_seconds + 30),
    )
    return ProjectCellAgentExecResponse.from_json(payload)


async def create_max_preview_session(project_id: UUID) -> dict[str, Any]:
    """POST a short-lived, signed bootstrap session for a MAX preview."""
    return await _request("POST", f"/internal/projects/{project_id}/max-preview-session")


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
        timeout=120.0,
    )
    response = ProjectCellPreviewSession.from_json(payload)
    if response.workspace_id != workspace_id:
        raise OrchestratorUnavailable("Orchestrator returned a different cell preview")
    return response


async def wake(project_id: UUID) -> dict[str, Any]:
    """POST /internal/projects/wake — start (or unpause) a previously provisioned project."""
    return await _request("POST", "/internal/projects/wake", json={"project_id": str(project_id)})


async def provision(
    *,
    project_id: UUID,
    slug: str,
    template: str,
    tier: str = "free",
    initial_env: dict[str, str] | None = None,
    timeout: float = 1320.0,  # noqa: ASYNC109 - cold rebuild + dependency sync can take minutes
) -> dict[str, Any]:
    """POST /internal/projects/provision — first-time scaffold + start.

    A stale template image is rebuilt during the first cold start. Production
    builds regularly exceed the generic 30-second orchestrator deadline, while
    provisioning continues successfully in the background. Keep this request
    alive long enough for the real result so the UI never reports a false
    preview failure for a container that is still starting.
    """
    payload: dict[str, Any] = {
        "project_id": str(project_id),
        "slug": slug,
        "template": template,
        "tier": tier,
    }
    if initial_env:
        payload["initial_env"] = initial_env
    return await _request(
        "POST",
        "/internal/projects/provision",
        json=payload,
        timeout=timeout,
    )


async def stop(project_id: UUID, *, pause: bool = True) -> dict[str, Any]:
    """POST /internal/projects/stop — pause or full stop of dev container."""
    return await _request(
        "POST",
        "/internal/projects/stop",
        json={"project_id": str(project_id), "pause": pause},
    )


async def set_keep_alive(project_id: UUID, *, enabled: bool) -> dict[str, Any]:
    """Persist whether the project's dev runtime may be auto-hibernated."""
    return await _request(
        "POST",
        "/internal/projects/keep-alive",
        json={"project_id": str(project_id), "enabled": enabled},
    )


async def deploy(
    project_id: UUID,
    *,
    commit_sha: str | None = None,
    target: dict[str, Any] | None = None,
    domains: list[str] | None = None,
    runtime_env: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """POST /internal/projects/deploy — build prod image + swap traffic.

    `target` (BYO-VPS) несёт расшифрованные SSH-креды чужого VPS: когда задан,
    оркестратор разворачивает образ на машине пользователя, а не у нас.
    `domains` — подключённые домены проекта: агент поднимет для них edge (авто-
    HTTPS) на машине пользователя.
    """
    payload: dict[str, Any] = {"project_id": str(project_id)}
    if commit_sha:
        payload["commit_sha"] = commit_sha
    if target:
        payload["target"] = target
    if domains:
        payload["domains"] = domains
    if runtime_env:
        payload["runtime_env"] = runtime_env
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    return await _request("POST", "/internal/projects/deploy", json=payload)


async def verify_deploy_target(target: dict[str, Any]) -> dict[str, Any]:
    """POST /internal/deploy-targets/verify — SSH-коннект к чужому VPS + проверка docker.

    `target` несёт РАСШИФРОВАННЫЕ креды (host/port/user/auth_type/secret) — канал
    за X-Internal-Token, на одной машине это localhost. Возвращает
    `{ok, detail, docker_ok, docker_version, host_key}`.
    """
    return await _request("POST", "/internal/deploy-targets/verify", json=target, timeout=45.0)


async def teardown_remote_project(project_id: UUID, target: dict[str, Any]) -> dict[str, Any]:
    return await _request(
        "POST",
        "/internal/deploy-targets/teardown",
        json={"project_id": str(project_id), **target},
        timeout=150.0,
    )


async def get_remote_logs(
    project_id: UUID, target: dict[str, Any], *, tail: int = 200
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/internal/deploy-targets/logs",
        params={"tail": tail},
        json={"project_id": str(project_id), **target},
        timeout=45.0,
    )


async def sync_remote_routes(
    project_id: UUID,
    slug: str,
    target: dict[str, Any],
    domains: list[str],
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/internal/deploy-targets/routes",
        json={
            "project_id": str(project_id),
            "slug": slug,
            "domains": domains,
            **target,
        },
        timeout=60.0,
    )


async def publish_custom_domain(payload: dict[str, Any]) -> dict[str, Any]:
    """POST /internal/domains/publish — nginx-vhost для чужого host + выпуск SSL.

    payload: {host, project_id, slug}. Оркестратор пишет vhost host → контейнер
    проекта и выпускает Let's Encrypt (HTTP-01). Возвращает
    `{ok, cert_status, detail}`. Таймаут высокий — acme может идти долго.
    """
    return await _request("POST", "/internal/domains/publish", json=payload, timeout=120.0)


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


async def cancel_deploy(project_id: UUID) -> dict[str, Any]:
    return await _request("POST", f"/internal/projects/{project_id}/deploy/cancel")


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


async def get_logs(project_id: UUID, *, tail: int = 200, kind: str = "dev") -> dict[str, Any]:
    """GET /internal/projects/<uuid>/logs — tail container stdout+stderr.

    Returns `{"project_id", "container_name", "tail", "logs": "<text>"}`.
    `logs` is a single UTF-8 string with newline-separated lines.
    """
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/logs",
        params={"tail": tail, "kind": kind},
    )


async def compile_status(project_id: UUID, *, slug: str | None = None) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/compile-status — does the dev build fail?

    Returns `{"project_id", "ok": bool, "error": str|None, "file": str|None}`.
    `ok=True` when the Next.js dev server is compiling cleanly (or has no
    outstanding error). Used right after a hot-reload to surface a compile
    failure as a chat card. Fail-soft on the orchestrator side: a missing
    container returns `ok=True`, never a 404.
    """
    params = {"slug": slug} if slug else None
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/compile-status",
        params=params,
    )


async def runtime_status(
    project_id: UUID, *, slug: str | None = None, path: str = "/"
) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/runtime-status — does the running app 5xx?

    Returns `{"project_id", "ok": bool, "status_code": int|None, "error": str|None,
    "file": str|None}`. `ok=False` only when the rendered route returns 5xx — a
    compile-clean app that still crashes on render. Used right after a build,
    after the compile probe comes back clean. Fail-soft on the orchestrator side:
    a missing/paused container returns `ok=True`, never a 404.
    """
    params: dict[str, str] = {"path": path}
    if slug:
        params["slug"] = slug
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/runtime-status",
        params=params,
    )


async def read_container_file(project_id: UUID, slug: str, path: str) -> str | None:
    """GET /internal/projects/{id}/read-file — read a whitelisted fixed file
    (e.g. ``src/app/globals.css``) straight from the running dev container.

    The project git repo only tracks AI-generated files; the template's fixed
    files live solely in the container image. Returns the file content, or
    ``None`` if it isn't present / the container is down (caller falls back).
    """
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/read-file",
        params={"slug": slug, "path": path},
    )
    if not resp.get("found"):
        return None
    content = resp.get("content")
    return content if isinstance(content, str) else None


# ── Agentic builder tools (Phase 0) ─────────────────────────────────────────
# Thin wrappers the api-side agent loop (services/agent_builder.py) calls to act
# on the live dev container. Each maps to a /agent/* orchestrator endpoint.


async def agent_read_file(project_id: UUID, slug: str, path: str) -> str | None:
    """Read ANY file under /app from the dev container; None if missing/down."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/read-file",
        params={"slug": slug, "path": path},
    )
    if not resp.get("found"):
        return None
    content = resp.get("content")
    return content if isinstance(content, str) else None


async def agent_list_dir(project_id: UUID, slug: str, path: str = ".") -> str:
    """List a directory under /app; returns the ls output (or an error line)."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/list-dir",
        params={"slug": slug, "path": path},
    )
    detail = resp.get("detail")
    return detail if isinstance(detail, str) else ""


async def agent_grep(project_id: UUID, slug: str, *, pattern: str, path: str = "src") -> str:
    """Recursive text search under /app; returns matches (or '(no matches)')."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/grep",
        params={"slug": slug, "pattern": pattern, "path": path},
    )
    detail = resp.get("detail")
    return detail if isinstance(detail, str) else ""


async def agent_build(project_id: UUID, slug: str) -> dict[str, Any]:
    """Run the container typecheck; returns {ok: bool, detail/error: str}."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/agent/build",
        params={"slug": slug},
        timeout=600.0,
    )


async def agent_exec(project_id: UUID, slug: str, cmd: str) -> dict[str, Any]:
    """Run a shell command in the dev container (agent `bash` tool)."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/agent/exec",
        params={"slug": slug, "cmd": cmd},
        timeout=210.0,
    )


async def agent_exec_sandbox(project_id: UUID, slug: str, cmd: str) -> dict[str, Any]:
    """Run a shell command in the isolated project sandbox and return a diff."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/agent/exec-sandbox",
        json={"slug": slug, "cmd": cmd},
        timeout=1500.0,
    )


async def agent_sandbox_capabilities(project_id: UUID, slug: str) -> dict[str, Any]:
    """Return the orchestrator's fail-closed attestation for the shell lane."""
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/sandbox-capabilities",
        params={"slug": slug},
    )


async def warm_routes(project_id: UUID, slug: str) -> dict[str, Any]:
    """POST /internal/projects/{id}/warm — force-compile the dev app's static
    routes so a demo opens WARM pages instead of eating a cold Turbopack compile
    per click. Best-effort: called fire-and-forget after a successful build."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/warm",
        params={"slug": slug},
    )


async def hot_reload(
    project_id: UUID,
    slug: str,
    files: dict[str, str],
    *,
    base_workspace_revision: str | None = None,
    empty_files: Sequence[str] = (),
) -> dict[str, Any]:
    """POST /internal/projects/hot-reload — write AI-generated files into the
    dev container; orchestrator additionally runs `drizzle-kit push` if the
    diff touches `src/lib/db/schema.ts` or `src/lib/db/migrations/*`.

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


async def build_exe(
    name: str,
    files: dict[str, str],
    pyinstaller_args: list[str],
    installer_nsi: str,
    requirements: str | None,
) -> dict[str, Any]:
    """POST /build-exe — package a Python project into a Windows .exe + NSIS
    Setup installer.

    The orchestrator side spawns an ``omnia-exe-builder`` sidecar container
    that runs PyInstaller + NSIS and returns the artefacts as base-64 blobs.
    A full build typically takes 60–300s, so we override the default 30s
    socket timeout with 360s. Returns ``{"ok": bool, "log": str,
    "setup_b64": str, "exe_b64": str | null}``.
    """
    return await _request(
        "POST",
        "/build-exe",
        json={
            "name": name,
            "files": files,
            "pyinstaller_args": pyinstaller_args,
            "installer_nsi": installer_nsi,
            "requirements": requirements,
        },
        timeout=360.0,
    )
