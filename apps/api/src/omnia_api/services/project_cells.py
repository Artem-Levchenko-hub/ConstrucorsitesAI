from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User

ALLOWED_OPERATION_KINDS = frozenset(
    {
        "ensure", "wake", "pause", "stop", "destroy", "status", "restore", "reconcile",
        "release",
    }
)
ACTIVE_OPERATION_STATUSES = ("pending", "waiting_capacity", "running")
TERMINAL_OPERATION_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "indeterminate"}
)
MAX_STORED_PAYLOAD_BYTES = 64 * 1024
REDACTED_VALUE = "[REDACTED]"

_UNSAFE_COMPACT_KEY_PARTS = frozenset(
    {
        "password",
        "secret",
        "credential",
        "authorization",
        "cookie",
        "dsn",
        "databaseurl",
        "connectionstring",
        "apikey",
        "privatekey",
        "token",
        "rawenvironment",
        "command",
        "commandstream",
        "commandoutput",
    }
)
_UNSAFE_COMPACT_EXACT_KEYS = frozenset(
    {
        "env",
        "envvar",
        "envvars",
        "environment",
        "environmentvariables",
        "processenv",
        "rawenv",
        "rawenvironment",
        "rawenvironmentvariables",
        "stdout",
        "stderr",
        "commandoutput",
        "commandstream",
    }
)


class ProjectCellError(Exception):
    """Base class for Project Cell domain failures."""


class ProjectCellValidationError(ProjectCellError):
    """Caller supplied an invalid key, kind, or payload."""


class ProjectCellIdempotencyConflict(ProjectCellError):
    """An idempotency key was reused for a different canonical request."""


class ProjectCellBusy(ProjectCellError):
    """A workspace already has an active operation."""


class ProjectCellNotFound(ProjectCellError):
    """A requested durable Project Cell record does not exist."""


class ProjectCellOwnershipError(ProjectCellError):
    """Project, workspace, and generation-run ownership do not agree."""


class ProjectCellStateConflict(ProjectCellError):
    """A lifecycle transition is not valid from the current state."""


@dataclass(frozen=True, slots=True)
class ClaimedCellOperation:
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID | None
    kind: str
    request: dict[str, object]
    request_digest: str
    fencing_epoch: int


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", key).lower()


def _is_unsafe_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return normalized in _UNSAFE_COMPACT_EXACT_KEYS or any(
        part in normalized for part in _UNSAFE_COMPACT_KEY_PARTS
    )


def _validate_json_native(value: object) -> None:
    if type(value) is dict:
        for key, nested in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise ProjectCellValidationError("payload object keys must be strings")
            _validate_json_native(nested)
        return
    if type(value) is list:
        for nested in cast(list[object], value):
            _validate_json_native(nested)
        return
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    raise ProjectCellValidationError("payload must contain only JSON-native values")


def _canonical_payload(value: dict[str, object]) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProjectCellValidationError("payload must be JSON-serializable") from exc


def _decoded_payload(canonical: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(canonical))


def _ensure_stored_size(canonical: str) -> None:
    if len(canonical.encode("utf-8")) > MAX_STORED_PAYLOAD_BYTES:
        raise ProjectCellValidationError("stored payload exceeds 64 KiB")


def _reject_unsafe_request_keys(value: object) -> None:
    if type(value) is dict:
        for key, nested in cast(dict[str, object], value).items():
            if _is_unsafe_key(key):
                raise ProjectCellValidationError(
                    f"request payload key {key!r} is not safe to store"
                )
            _reject_unsafe_request_keys(nested)
        return
    if type(value) is list:
        for nested in cast(list[object], value):
            _reject_unsafe_request_keys(nested)


def _redact_unsafe_result_keys(value: object) -> object:
    if type(value) is dict:
        redacted: dict[str, object] = {}
        for key, nested in cast(dict[str, object], value).items():
            redacted[key] = (
                REDACTED_VALUE if _is_unsafe_key(key) else _redact_unsafe_result_keys(nested)
            )
        return redacted
    if type(value) is list:
        return [_redact_unsafe_result_keys(nested) for nested in cast(list[object], value)]
    return value


def _prepare_request(value: dict[str, object]) -> tuple[dict[str, object], str]:
    if type(value) is not dict:
        raise ProjectCellValidationError("request payload must be a JSON object")
    _validate_json_native(value)
    _reject_unsafe_request_keys(value)
    canonical = _canonical_payload(value)
    _ensure_stored_size(canonical)
    return _decoded_payload(canonical), hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_operation_envelope(
    workspace_id: UUID,
    generation_run_id: UUID | None,
    kind: str,
    request: dict[str, object],
) -> tuple[dict[str, object], str]:
    request_payload, _ = _prepare_request(request)
    envelope: dict[str, object] = {
        "workspace_id": str(workspace_id),
        "generation_run_id": str(generation_run_id) if generation_run_id is not None else None,
        "kind": kind,
        "request": request_payload,
    }
    canonical = _canonical_payload(envelope)
    _ensure_stored_size(canonical)
    return _decoded_payload(canonical), hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_result(value: dict[str, object]) -> dict[str, object]:
    if type(value) is not dict:
        raise ProjectCellValidationError("result payload must be a JSON object")
    _validate_json_native(value)
    _canonical_payload(value)
    redacted = cast(dict[str, object], _redact_unsafe_result_keys(value))
    canonical = _canonical_payload(redacted)
    _ensure_stored_size(canonical)
    return _decoded_payload(canonical)


def _hashed_error(error: str) -> str:
    return f"provider_error:{hashlib.sha256(error.encode('utf-8')).hexdigest()}"


async def _advisory_lock(session: AsyncSession, workspace_key: UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:workspace_key))"),
        {"workspace_key": str(workspace_key)},
    )


def _validate_workspace_ownership(project: Project, user: User, run: GenerationRun) -> None:
    if project.owner_id != user.id:
        raise ProjectCellOwnershipError("authenticated user does not own the project")
    if run.project_id != project.id or run.user_id != user.id:
        raise ProjectCellOwnershipError("generation run does not belong to the project owner")


def _stored_request_payload(operation: ProjectCellOperation) -> dict[str, object]:
    payload = operation.request_payload
    if type(payload) is not dict:
        raise ProjectCellValidationError("stored operation request payload must be a JSON object")
    expected_keys = {"workspace_id", "generation_run_id", "kind", "request"}
    if set(payload) != expected_keys:
        raise ProjectCellValidationError(
            "stored operation request payload is not a canonical envelope"
        )

    raw_workspace_id = payload.get("workspace_id")
    raw_generation_run_id = payload.get("generation_run_id")
    raw_kind = payload.get("kind")
    request = payload.get("request")
    if (
        type(raw_workspace_id) is not str
        or (raw_generation_run_id is not None and type(raw_generation_run_id) is not str)
        or type(raw_kind) is not str
        or type(request) is not dict
    ):
        raise ProjectCellValidationError("stored operation envelope has invalid field types")

    expected_generation_run_id = (
        str(operation.generation_run_id) if operation.generation_run_id is not None else None
    )
    if raw_workspace_id != str(operation.workspace_id):
        raise ProjectCellValidationError("stored operation envelope workspace does not match row")
    if raw_generation_run_id != expected_generation_run_id:
        raise ProjectCellValidationError(
            "stored operation envelope generation run does not match row"
        )
    if raw_kind != operation.kind:
        raise ProjectCellValidationError("stored operation envelope kind does not match row")

    envelope, digest = _canonical_operation_envelope(
        operation.workspace_id,
        operation.generation_run_id,
        operation.kind,
        request,
    )
    if payload != envelope:
        raise ProjectCellValidationError("stored operation envelope is not canonical")
    if operation.request_digest != digest:
        raise ProjectCellValidationError("stored operation request digest does not match envelope")

    canonical_request = envelope.get("request")
    assert type(canonical_request) is dict
    return canonical_request


async def get_or_create_workspace(
    session: AsyncSession,
    *,
    project: Project,
    user: User,
    run: GenerationRun,
) -> tuple[ProjectCellWorkspace, bool]:
    _validate_workspace_ownership(project, user, run)
    await _advisory_lock(session, project.id)

    existing = await session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.project_id == project.id)
    )
    if existing is not None:
        if existing.owner_id != user.id or existing.owner_id != project.owner_id:
            raise ProjectCellOwnershipError("existing workspace belongs to another owner")
        return existing, False

    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state="provisioning",
        generation_run_id=run.id,
    )
    session.add(workspace)
    await session.flush()
    return workspace, True


def _validate_reservation(kind: str, idempotency_key: str) -> None:
    if kind not in ALLOWED_OPERATION_KINDS:
        raise ProjectCellValidationError(f"unsupported Project Cell operation kind {kind!r}")
    if not 8 <= len(idempotency_key) <= 128:
        raise ProjectCellValidationError("idempotency key length must be between 8 and 128")


def _validate_operation_request(kind: str, request: dict[str, object]) -> None:
    if kind != "reconcile":
        return
    if set(request) != {"indeterminate_operation_id"}:
        raise ProjectCellValidationError(
            "reconcile request must contain exactly indeterminate_operation_id"
        )
    raw_operation_id = request.get("indeterminate_operation_id")
    if type(raw_operation_id) is not str:
        raise ProjectCellValidationError(
            "reconcile indeterminate_operation_id must be a UUID string"
        )
    try:
        UUID(raw_operation_id)
    except ValueError as exc:
        raise ProjectCellValidationError(
            "reconcile indeterminate_operation_id must be a UUID string"
        ) from exc


async def reserve_cell_operation(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    generation_run_id: UUID | None,
    kind: str,
    idempotency_key: str,
    request: dict[str, object],
) -> tuple[ProjectCellOperation, bool]:
    _validate_reservation(kind, idempotency_key)
    _validate_operation_request(kind, request)
    await _advisory_lock(session, workspace_id)

    workspace = await session.get(ProjectCellWorkspace, workspace_id)
    if workspace is None:
        raise ProjectCellNotFound("Project Cell workspace was not found")

    if generation_run_id is not None:
        generation_run = await session.get(GenerationRun, generation_run_id)
        if generation_run is None:
            raise ProjectCellNotFound("generation run was not found")
        if (
            generation_run.project_id != workspace.project_id
            or generation_run.user_id != workspace.owner_id
        ):
            raise ProjectCellOwnershipError("generation run does not belong to the workspace owner")

    request_payload, digest = _canonical_operation_envelope(
        workspace_id,
        generation_run_id,
        kind,
        request,
    )

    existing = await session.scalar(
        select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_digest != digest:
            raise ProjectCellIdempotencyConflict(
                "idempotency key was already used for a different request"
            )
        return existing, True

    active = await session.scalar(
        select(ProjectCellOperation)
        .where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
        )
        .order_by(ProjectCellOperation.created_at.desc())
        .limit(1)
    )
    if active is not None:
        raise ProjectCellBusy("Project Cell workspace already has an active operation")

    operation = ProjectCellOperation(
        workspace_id=workspace_id,
        generation_run_id=generation_run_id,
        idempotency_key=idempotency_key,
        request_digest=digest,
        kind=kind,
        status="pending",
        request_payload=request_payload,
    )
    session.add(operation)
    await session.flush()
    return operation, False


async def _locked_operation(session: AsyncSession, operation_id: UUID) -> ProjectCellOperation:
    operation = await session.scalar(
        select(ProjectCellOperation)
        .where(ProjectCellOperation.id == operation_id)
        .with_for_update()
    )
    if operation is None:
        raise ProjectCellNotFound("Project Cell operation was not found")
    return operation


async def claim_cell_operation(
    session: AsyncSession,
    operation_id: UUID,
) -> ProjectCellOperation:
    operation = await _locked_operation(session, operation_id)
    if operation.status not in {"pending", "waiting_capacity"}:
        raise ProjectCellStateConflict(f"cannot claim operation in state {operation.status!r}")
    operation.status = "running"
    operation.attempt_count += 1
    operation.capacity_reason = None
    operation.next_attempt_at = None
    operation.started_at = datetime.now(UTC)
    await session.flush()
    return operation


async def complete_cell_operation(
    session: AsyncSession,
    operation_id: UUID,
    result: dict[str, object],
) -> None:
    operation = await _locked_operation(session, operation_id)
    if operation.status != "running":
        raise ProjectCellStateConflict(f"cannot complete operation in state {operation.status!r}")
    result_payload = _prepare_result(result)
    operation.status = "completed"
    operation.result_payload = result_payload
    operation.finished_at = datetime.now(UTC)
    await session.flush()


async def fail_cell_operation(
    session: AsyncSession,
    operation_id: UUID,
    error: str,
) -> None:
    operation = await _locked_operation(session, operation_id)
    if operation.status != "running":
        raise ProjectCellStateConflict(f"cannot fail operation in state {operation.status!r}")
    operation.status = "failed"
    operation.error = _hashed_error(error)
    operation.finished_at = datetime.now(UTC)
    await session.flush()


async def mark_cell_operation_indeterminate(
    session: AsyncSession,
    operation_id: UUID,
    error: str,
) -> None:
    operation = await _locked_operation(session, operation_id)
    if operation.status == "indeterminate":
        return
    if operation.status != "running":
        raise ProjectCellStateConflict(
            f"cannot mark operation indeterminate in state {operation.status!r}"
        )
    operation.status = "indeterminate"
    operation.error = _hashed_error(error)
    operation.finished_at = datetime.now(UTC)
    await session.flush()


async def park_cell_operation_for_capacity(
    session: AsyncSession,
    operation_id: UUID,
    *,
    reason: str,
    retry_after_seconds: int,
) -> None:
    if reason not in {
        "insufficient_cpu",
        "insufficient_memory",
        "insufficient_disk",
        "insufficient_inodes",
        "daemon_filesystem_unverifiable",
    }:
        raise ProjectCellValidationError("unsupported capacity reason")
    if not 1 <= retry_after_seconds <= 10:
        raise ProjectCellValidationError("retry_after_seconds must be between 1 and 10")
    operation = await _locked_operation(session, operation_id)
    if operation.status != "running":
        raise ProjectCellStateConflict(
            f"cannot park operation in state {operation.status!r}"
        )
    operation.status = "waiting_capacity"
    operation.capacity_reason = reason
    operation.next_attempt_at = datetime.now(UTC) + timedelta(seconds=retry_after_seconds)
    operation.error = None
    operation.finished_at = None
    await session.flush()


async def claim_cell_operation_committed(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
) -> ClaimedCellOperation:
    async with session_factory() as session:
        operation = await session.get(ProjectCellOperation, operation_id)
        if operation is None:
            raise ProjectCellNotFound("Project Cell operation was not found")

        await _advisory_lock(session, operation.workspace_id)
        locked_operation = await _locked_operation(session, operation_id)
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == locked_operation.workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise ProjectCellNotFound("Project Cell workspace was not found")
        if locked_operation.status not in {"pending", "waiting_capacity"}:
            raise ProjectCellStateConflict(
                f"cannot claim operation in state {locked_operation.status!r}"
            )
        request = _stored_request_payload(locked_operation)

        workspace.fencing_epoch += 1
        workspace.version += 1
        locked_operation.fencing_epoch = workspace.fencing_epoch
        locked_operation.status = "running"
        locked_operation.attempt_count += 1
        locked_operation.capacity_reason = None
        locked_operation.next_attempt_at = None
        locked_operation.started_at = datetime.now(UTC)
        await session.flush()

        claimed = ClaimedCellOperation(
            operation_id=locked_operation.id,
            workspace_id=locked_operation.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            generation_run_id=locked_operation.generation_run_id,
            kind=locked_operation.kind,
            request=request,
            request_digest=locked_operation.request_digest,
            fencing_epoch=workspace.fencing_epoch,
        )
        await session.commit()
        return claimed


async def _recover_interrupted_cell_operations(session: AsyncSession) -> int:
    operations = list(
        (
            await session.execute(
                select(ProjectCellOperation)
                .where(ProjectCellOperation.status == "running")
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for operation in operations:
        operation.status = "indeterminate"
        operation.error = _hashed_error("interrupted_before_outcome_recorded")
        operation.finished_at = now
    await session.flush()
    return len(operations)


async def recover_interrupted_cell_operations(
    session: AsyncSession | None = None,
) -> int:
    """Finalise cell effects whose API coroutine cannot survive a restart."""

    if session is not None:
        return await _recover_interrupted_cell_operations(session)

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        recovered = await _recover_interrupted_cell_operations(own_session)
        await own_session.commit()
        return recovered
