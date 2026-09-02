"""Durable per-workspace Project Cell state and credential storage."""

from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from omnia_orchestrator.core.cell_resources import CellResourceNames, LifecycleMutation
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_WORKSPACE_STATE_KEYS = frozenset(
    {
        "workspace_id",
        "project_id",
        "owner_id",
        "profile_version",
        "phase",
        "bundle_state",
        "fencing_epoch",
        "active_generation_run_id",
        "active_generation_fencing_epoch",
        "last_operation_id",
        "provider_ref",
        "resource_names",
        "operations",
    }
)
_RESOURCE_NAME_KEYS = frozenset(
    {
        "workspace_id",
        "namespace",
        "internal_network",
        "egress_network",
        "workspace_volume",
        "agent_home_volume",
        "postgres_volume",
        "redis_volume",
        "checkpoint_volume",
        "postgres_container",
        "redis_container",
    }
)
_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "kind",
        "status",
        "phase",
        "request_digest",
        "fencing_epoch",
        "generation_run_id",
        "checkpoint_ref",
        "provider_ref",
        "bundle_state",
        "detail",
        "expected_resources",
        "observed_resources",
    }
)


@dataclass(frozen=True, slots=True)
class CellCredentials:
    postgres_password: str


@dataclass(frozen=True, slots=True)
class CellOperationRecord:
    operation_id: UUID
    kind: str
    status: str
    phase: str
    request_digest: str
    fencing_epoch: int
    generation_run_id: UUID | None = None
    checkpoint_ref: str | None = None
    provider_ref: str | None = None
    bundle_state: str | None = None
    detail: str | None = None
    expected_resources: dict[str, str] | None = None
    observed_resources: dict[str, str] | None = None

    def matches_replay_envelope(
        self,
        *,
        kind: str,
        request_digest: str,
        fencing_epoch: int,
        checkpoint_ref: str | None,
    ) -> bool:
        return (
            self.kind == kind
            and self.request_digest == request_digest
            and self.fencing_epoch == fencing_epoch
            and self.checkpoint_ref == checkpoint_ref
        )

    def replay_completed_same_envelope(
        self,
        request_digest: str,
        fencing_epoch: int,
    ) -> bool:
        return (
            self.status == "completed"
            and self.request_digest == request_digest
            and self.fencing_epoch == fencing_epoch
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CellOperationRecord:
        _ensure_exact_keys(payload, _OPERATION_KEYS, "operation record")
        return cls(
            operation_id=_require_uuid_str(payload["operation_id"], "operation_id"),
            kind=_require_str(payload["kind"], "kind"),
            status=_require_str(payload["status"], "status"),
            phase=_require_str(payload["phase"], "phase"),
            request_digest=_require_str(payload["request_digest"], "request_digest"),
            fencing_epoch=_require_int(payload["fencing_epoch"], "fencing_epoch"),
            generation_run_id=_require_optional_uuid_str(
                payload["generation_run_id"],
                "generation_run_id",
            ),
            checkpoint_ref=_require_optional_str(payload["checkpoint_ref"], "checkpoint_ref"),
            provider_ref=_require_optional_str(payload["provider_ref"], "provider_ref"),
            bundle_state=_require_optional_str(payload["bundle_state"], "bundle_state"),
            detail=_require_optional_str(payload["detail"], "detail"),
            expected_resources=_require_optional_str_dict(
                payload["expected_resources"],
                "expected_resources",
            ),
            observed_resources=_require_optional_str_dict(
                payload["observed_resources"],
                "observed_resources",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operation_id"] = str(self.operation_id)
        payload["generation_run_id"] = (
            str(self.generation_run_id) if self.generation_run_id is not None else None
        )
        return payload


@dataclass(frozen=True, slots=True)
class CellWorkspaceState:
    workspace_id: UUID
    project_id: UUID | None
    owner_id: UUID | None
    profile_version: str
    phase: str
    bundle_state: str
    fencing_epoch: int
    active_generation_run_id: UUID | None
    active_generation_fencing_epoch: int | None
    last_operation_id: UUID | None
    provider_ref: str | None
    resource_names: CellResourceNames | None
    operations: tuple[CellOperationRecord, ...]

    def operation(self, operation_id: UUID | None) -> CellOperationRecord | None:
        if operation_id is None:
            return None
        for item in self.operations:
            if item.operation_id == operation_id:
                return item
        return None

    def matches_spec_identity(self, spec: WorkspaceSpec) -> bool:
        return (
            self.project_id == spec.project_id
            and self.owner_id == spec.owner_id
            and self.profile_version == spec.profile_version
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CellWorkspaceState:
        _ensure_exact_keys(payload, _WORKSPACE_STATE_KEYS, "workspace state")
        workspace_id = _require_uuid_str(payload["workspace_id"], "workspace_id")
        return cls(
            workspace_id=workspace_id,
            project_id=_require_optional_uuid_str(payload["project_id"], "project_id"),
            owner_id=_require_optional_uuid_str(payload["owner_id"], "owner_id"),
            profile_version=_require_str(payload["profile_version"], "profile_version"),
            phase=_require_str(payload["phase"], "phase"),
            bundle_state=_require_str(payload["bundle_state"], "bundle_state"),
            fencing_epoch=_require_int(payload["fencing_epoch"], "fencing_epoch"),
            active_generation_run_id=_require_optional_uuid_str(
                payload["active_generation_run_id"],
                "active_generation_run_id",
            ),
            active_generation_fencing_epoch=_require_optional_int(
                payload["active_generation_fencing_epoch"],
                "active_generation_fencing_epoch",
            ),
            last_operation_id=_require_optional_uuid_str(
                payload["last_operation_id"],
                "last_operation_id",
            ),
            provider_ref=_require_optional_str(payload["provider_ref"], "provider_ref"),
            resource_names=_parse_resource_names(payload["resource_names"], workspace_id),
            operations=tuple(_require_operation_records(payload["operations"])),
        )


class CellStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.root = _state_root(self.path)

    def load(self, workspace_id: UUID) -> CellWorkspaceState | None:
        file_path = self.workspace_path(workspace_id)
        if _require_regular_file_path(file_path, missing_ok=True) is None:
            return None
        payload = _read_workspace_payload_file(file_path)
        return CellWorkspaceState.from_dict(payload)

    def all_states(self) -> list[CellWorkspaceState]:
        if _lstat_path(self.root) is None:
            return []
        _ensure_secure_dir(self.root, create=False)
        states: list[CellWorkspaceState] = []
        for entry in sorted(self.root.iterdir()):
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError(f"refuse symlink path component: {entry}")
            if entry.suffix != ".json" or stat.S_ISDIR(info.st_mode):
                continue
            payload = _read_workspace_payload_file(entry)
            states.append(CellWorkspaceState.from_dict(payload))
        return states

    def operation_ids(self, workspace_id: UUID) -> list[UUID]:
        state = self.load(workspace_id)
        if state is None:
            return []
        return [item.operation_id for item in state.operations]

    def begin(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        *,
        kind: str,
        phase: str,
        resource_names: CellResourceNames,
        expected_resources: dict[str, str] | None = None,
        checkpoint_ref: str | None = None,
    ) -> CellWorkspaceState:
        workspace = self.load(spec.workspace_id)
        if workspace is not None:
            if workspace.matches_spec_identity(spec) is False:
                raise RuntimeError("workspace immutable identity mismatch")
            if (
                workspace.resource_names is not None
                and workspace.resource_names != resource_names
            ):
                raise RuntimeError("existing workspace resource_names mismatch")
        operations = list(workspace.operations if workspace is not None else ())
        operations = [item for item in operations if item.operation_id != mutation.operation_id]
        operations.append(
            CellOperationRecord(
                operation_id=mutation.operation_id,
                kind=kind,
                status="running",
                phase=phase,
                request_digest=mutation.request_digest,
                fencing_epoch=mutation.fencing_epoch,
                generation_run_id=spec.generation_run_id,
                checkpoint_ref=checkpoint_ref,
                expected_resources=expected_resources,
            )
        )
        next_state = CellWorkspaceState(
            workspace_id=spec.workspace_id,
            project_id=workspace.project_id if workspace is not None else spec.project_id,
            owner_id=workspace.owner_id if workspace is not None else spec.owner_id,
            profile_version=(
                workspace.profile_version if workspace is not None else spec.profile_version
            ),
            phase=phase,
            bundle_state="running",
            fencing_epoch=mutation.fencing_epoch,
            active_generation_run_id=(
                workspace.active_generation_run_id if workspace is not None else None
            ),
            active_generation_fencing_epoch=(
                workspace.active_generation_fencing_epoch if workspace is not None else None
            ),
            last_operation_id=mutation.operation_id,
            provider_ref=workspace.provider_ref if workspace is not None else None,
            resource_names=workspace.resource_names if workspace is not None else resource_names,
            operations=tuple(operations),
        )
        self._persist_state(next_state)
        return next_state

    def advance(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        phase: str,
        observed_resources: dict[str, str] | None = None,
        bundle_state: str | None = None,
        detail: str | None = None,
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        operations = []
        for item in workspace.operations:
            if item.operation_id == mutation.operation_id:
                operations.append(
                    CellOperationRecord(
                        operation_id=item.operation_id,
                        kind=item.kind,
                        status=item.status,
                        phase=phase,
                        request_digest=item.request_digest,
                        fencing_epoch=item.fencing_epoch,
                        generation_run_id=item.generation_run_id,
                        checkpoint_ref=item.checkpoint_ref,
                        provider_ref=item.provider_ref,
                        bundle_state=bundle_state or item.bundle_state,
                        detail=detail if detail is not None else item.detail,
                        expected_resources=item.expected_resources,
                        observed_resources=observed_resources,
                    )
                )
            else:
                operations.append(item)
        next_state = CellWorkspaceState(
            workspace_id=workspace.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            profile_version=workspace.profile_version,
            phase=phase,
            bundle_state=bundle_state or workspace.bundle_state,
            fencing_epoch=mutation.fencing_epoch,
            active_generation_run_id=workspace.active_generation_run_id,
            active_generation_fencing_epoch=workspace.active_generation_fencing_epoch,
            last_operation_id=mutation.operation_id,
            provider_ref=workspace.provider_ref,
            resource_names=workspace.resource_names,
            operations=tuple(operations),
        )
        self._persist_state(next_state)
        return next_state

    def complete(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        phase: str = "completed",
        provider_ref: str | None = None,
        bundle_state: str,
        detail: str | None = None,
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        operations = []
        completed_operation: CellOperationRecord | None = None
        for item in workspace.operations:
            if item.operation_id == mutation.operation_id:
                completed_operation = CellOperationRecord(
                    operation_id=item.operation_id,
                    kind=item.kind,
                    status="completed",
                    phase=phase,
                    request_digest=item.request_digest,
                    fencing_epoch=item.fencing_epoch,
                    generation_run_id=item.generation_run_id,
                    checkpoint_ref=item.checkpoint_ref,
                    provider_ref=provider_ref or item.provider_ref,
                    bundle_state=bundle_state,
                    detail=detail if detail is not None else item.detail,
                    expected_resources=item.expected_resources,
                    observed_resources=item.observed_resources,
                )
                operations.append(completed_operation)
            else:
                operations.append(item)
        active_generation_run_id = workspace.active_generation_run_id
        active_generation_fencing_epoch = workspace.active_generation_fencing_epoch
        if (
            completed_operation is not None
            and completed_operation.kind == "ensure"
            and completed_operation.generation_run_id is not None
        ):
            active_generation_run_id = completed_operation.generation_run_id
            active_generation_fencing_epoch = mutation.fencing_epoch
        next_state = CellWorkspaceState(
            workspace_id=workspace.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            profile_version=workspace.profile_version,
            phase=phase,
            bundle_state=bundle_state,
            fencing_epoch=mutation.fencing_epoch,
            active_generation_run_id=active_generation_run_id,
            active_generation_fencing_epoch=active_generation_fencing_epoch,
            last_operation_id=mutation.operation_id,
            provider_ref=provider_ref or workspace.provider_ref,
            resource_names=workspace.resource_names,
            operations=tuple(operations),
        )
        self._persist_state(next_state)
        return next_state

    def release_generation(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        generation_run_id: UUID | None,
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        operation = workspace.operation(mutation.operation_id)
        if (
            operation is None
            or operation.kind != "release"
            or operation.request_digest != mutation.request_digest
            or operation.fencing_epoch != mutation.fencing_epoch
            or workspace.last_operation_id != mutation.operation_id
            or workspace.fencing_epoch != mutation.fencing_epoch
        ):
            raise RuntimeError("release operation fence mismatch")
        if (
            generation_run_id is None
            or operation.generation_run_id != generation_run_id
            or workspace.active_generation_run_id != generation_run_id
            or workspace.active_generation_fencing_epoch is None
            or workspace.active_generation_fencing_epoch >= mutation.fencing_epoch
        ):
            raise RuntimeError("generation lease mismatch")
        retained_bundle_state = next(
            (
                item.bundle_state
                for item in workspace.operations
                if item.generation_run_id == generation_run_id
                and item.fencing_epoch == workspace.active_generation_fencing_epoch
                and item.bundle_state is not None
            ),
            workspace.bundle_state,
        )
        operations = tuple(
            replace(
                item,
                status="completed",
                phase="completed",
                provider_ref=workspace.provider_ref,
                bundle_state=retained_bundle_state,
            )
            if item.operation_id == mutation.operation_id
            else item
            for item in workspace.operations
        )
        next_state = replace(
            workspace,
            phase="completed",
            bundle_state=retained_bundle_state,
            active_generation_run_id=None,
            active_generation_fencing_epoch=None,
            operations=operations,
        )
        self._persist_state(next_state)
        return next_state

    def mark_failed(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        bundle_state: str,
        detail: str | None = None,
        provider_ref: str | None = None,
        phase: str = "failed",
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        operations = []
        failed_operation: CellOperationRecord | None = None
        for item in workspace.operations:
            if item.operation_id == mutation.operation_id:
                failed_operation = CellOperationRecord(
                    operation_id=item.operation_id,
                    kind=item.kind,
                    status="failed",
                    phase=phase,
                    request_digest=item.request_digest,
                    fencing_epoch=item.fencing_epoch,
                    generation_run_id=item.generation_run_id,
                    checkpoint_ref=item.checkpoint_ref,
                    provider_ref=provider_ref or item.provider_ref,
                    bundle_state=bundle_state,
                    detail=detail if detail is not None else item.detail,
                    expected_resources=item.expected_resources,
                    observed_resources=item.observed_resources,
                )
                operations.append(failed_operation)
            else:
                operations.append(item)
        if failed_operation is None:
            raise RuntimeError("workspace operation missing for failed mark")
        next_state = CellWorkspaceState(
            workspace_id=workspace.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            profile_version=workspace.profile_version,
            phase=phase,
            bundle_state=bundle_state,
            fencing_epoch=mutation.fencing_epoch,
            active_generation_run_id=workspace.active_generation_run_id,
            active_generation_fencing_epoch=workspace.active_generation_fencing_epoch,
            last_operation_id=mutation.operation_id,
            provider_ref=provider_ref or workspace.provider_ref,
            resource_names=workspace.resource_names,
            operations=tuple(operations),
        )
        self._persist_state(next_state)
        return next_state

    def set_bundle_state(
        self,
        workspace_id: UUID,
        *,
        bundle_state: str,
        phase: str | None = None,
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        next_state = CellWorkspaceState(
            workspace_id=workspace.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            profile_version=workspace.profile_version,
            phase=phase or workspace.phase,
            bundle_state=bundle_state,
            fencing_epoch=workspace.fencing_epoch,
            active_generation_run_id=workspace.active_generation_run_id,
            active_generation_fencing_epoch=workspace.active_generation_fencing_epoch,
            last_operation_id=workspace.last_operation_id,
            provider_ref=workspace.provider_ref,
            resource_names=workspace.resource_names,
            operations=workspace.operations,
        )
        self._persist_state(next_state)
        return next_state

    def mark_indeterminate(
        self,
        workspace_id: UUID,
        *,
        mutation: LifecycleMutation | None = None,
        detail: str | None = None,
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        operation_id = (
            mutation.operation_id if mutation is not None else workspace.last_operation_id
        )
        if operation_id is None:
            raise RuntimeError("workspace has no operation to mark indeterminate")
        operations = []
        for item in workspace.operations:
            if item.operation_id == operation_id:
                operations.append(
                    CellOperationRecord(
                        operation_id=item.operation_id,
                        kind=item.kind,
                        status="indeterminate",
                        phase="indeterminate",
                        request_digest=item.request_digest,
                        fencing_epoch=mutation.fencing_epoch
                        if mutation is not None
                        else item.fencing_epoch,
                        generation_run_id=item.generation_run_id,
                        checkpoint_ref=item.checkpoint_ref,
                        provider_ref=item.provider_ref,
                        bundle_state="indeterminate",
                        detail=detail if detail is not None else item.detail,
                        expected_resources=item.expected_resources,
                        observed_resources=item.observed_resources,
                    )
                )
            else:
                operations.append(item)
        next_state = CellWorkspaceState(
            workspace_id=workspace.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            profile_version=workspace.profile_version,
            phase="indeterminate",
            bundle_state="indeterminate",
            fencing_epoch=mutation.fencing_epoch
            if mutation is not None
            else workspace.fencing_epoch,
            active_generation_run_id=workspace.active_generation_run_id,
            active_generation_fencing_epoch=workspace.active_generation_fencing_epoch,
            last_operation_id=operation_id,
            provider_ref=workspace.provider_ref,
            resource_names=workspace.resource_names,
            operations=tuple(operations),
        )
        self._persist_state(next_state)
        return next_state

    def mark_degraded(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        kind: str | None = None,
        detail: str,
    ) -> CellWorkspaceState:
        workspace = self._require_state(workspace_id)
        operations = []
        operation_found = False
        for item in workspace.operations:
            if item.operation_id == mutation.operation_id:
                operation_found = True
                operations.append(
                    CellOperationRecord(
                        operation_id=item.operation_id,
                        kind=item.kind,
                        status="indeterminate",
                        phase="completed",
                        request_digest=item.request_digest,
                        fencing_epoch=item.fencing_epoch,
                        generation_run_id=item.generation_run_id,
                        checkpoint_ref=item.checkpoint_ref,
                        provider_ref=item.provider_ref,
                        bundle_state="degraded",
                        detail=detail,
                        expected_resources=item.expected_resources,
                        observed_resources=item.observed_resources,
                    )
                )
            else:
                operations.append(item)
        if operation_found is False:
            if kind is None:
                raise RuntimeError("workspace operation missing for degraded mark")
            operations.append(
                CellOperationRecord(
                    operation_id=mutation.operation_id,
                    kind=kind,
                    status="indeterminate",
                    phase="completed",
                    request_digest=mutation.request_digest,
                    fencing_epoch=mutation.fencing_epoch,
                    generation_run_id=None,
                    provider_ref=workspace.provider_ref,
                    bundle_state="degraded",
                    detail=detail,
                )
            )
        next_state = CellWorkspaceState(
            workspace_id=workspace.workspace_id,
            project_id=workspace.project_id,
            owner_id=workspace.owner_id,
            profile_version=workspace.profile_version,
            phase="completed",
            bundle_state="degraded",
            fencing_epoch=mutation.fencing_epoch,
            active_generation_run_id=workspace.active_generation_run_id,
            active_generation_fencing_epoch=workspace.active_generation_fencing_epoch,
            last_operation_id=mutation.operation_id,
            provider_ref=workspace.provider_ref,
            resource_names=workspace.resource_names,
            operations=tuple(operations),
        )
        self._persist_state(next_state)
        return next_state

    def workspace_path(self, workspace_id: UUID) -> Path:
        return self.root / f"{workspace_id}.json"

    def _persist_state(self, state: CellWorkspaceState) -> None:
        _ensure_secure_dir(self.root, create=True)
        destination = self.workspace_path(state.workspace_id)
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{state.workspace_id}.",
            dir=self.root,
            text=True,
        )
        try:
            _set_file_mode(temp_fd, _FILE_MODE)
            payload = {
                "workspace_id": str(state.workspace_id),
                "project_id": str(state.project_id) if state.project_id is not None else None,
                "owner_id": str(state.owner_id) if state.owner_id is not None else None,
                "profile_version": state.profile_version,
                "phase": state.phase,
                "bundle_state": state.bundle_state,
                "fencing_epoch": state.fencing_epoch,
                "active_generation_run_id": (
                    str(state.active_generation_run_id)
                    if state.active_generation_run_id is not None
                    else None
                ),
                "active_generation_fencing_epoch": state.active_generation_fencing_epoch,
                "last_operation_id": (
                    str(state.last_operation_id) if state.last_operation_id is not None else None
                ),
                "provider_ref": state.provider_ref,
                "resource_names": (
                    {
                        **asdict(state.resource_names),
                        "workspace_id": str(state.resource_names.workspace_id),
                    }
                    if state.resource_names is not None
                    else None
                ),
                "operations": [item.to_dict() for item in state.operations],
            }
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "workspace": payload}, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            _replace_file(Path(temp_name), destination)
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def _require_state(self, workspace_id: UUID) -> CellWorkspaceState:
        state = self.load(workspace_id)
        if state is None:
            raise RuntimeError(f"workspace state missing: {workspace_id}")
        return state


class CellCredentialStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load_or_create(self, workspace_id: UUID) -> CellCredentials:
        _ensure_secure_dir(self.root, create=True)
        path = self.root / f"{workspace_id}.json"
        if _require_regular_file_path(path, missing_ok=True) is not None:
            payload = _read_plain_json_file(path)
            password = payload.get("postgres_password")
            if not isinstance(password, str) or not password:
                raise RuntimeError("invalid credential file")
            return CellCredentials(postgres_password=password)
        password = secrets.token_urlsafe(32)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(path, flags, _FILE_MODE)
        try:
            _validate_regular_fd(fd, expected_mode=_FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"postgres_password": password}, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(self.root)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return CellCredentials(postgres_password=password)


def _state_root(path: Path) -> Path:
    if path.suffix == ".json":
        return path.parent / path.stem
    return path


def _require_uuid_str(value: object, label: str) -> UUID:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} must be UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"{label} must be UUID string") from exc


def _require_optional_uuid_str(value: object, label: str) -> UUID | None:
    if value is None:
        return None
    return _require_uuid_str(value, label)


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} must be string")
    return value


def _require_optional_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, label)


def _require_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise RuntimeError(f"{label} must be integer")
    return value


def _require_optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, label)


def _require_optional_str_dict(value: object, label: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise RuntimeError(f"{label} must be object[str, str]")
        result[key] = item
    return result


def _require_namespace(value: object) -> Literal["prod", "test"]:
    namespace = _require_str(value, "resource_names.namespace")
    if namespace not in {"prod", "test"}:
        raise RuntimeError(f"invalid namespace in state file: {namespace}")
    return cast(Literal["prod", "test"], namespace)


def _parse_resource_names(value: object, workspace_id: UUID) -> CellResourceNames | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("resource_names must be object")
    _ensure_exact_keys(value, _RESOURCE_NAME_KEYS, "resource_names")
    parsed = CellResourceNames(
        workspace_id=_require_uuid_str(value["workspace_id"], "resource_names.workspace_id"),
        namespace=_require_namespace(value["namespace"]),
        internal_network=_require_str(value["internal_network"], "resource_names.internal_network"),
        egress_network=_require_str(value["egress_network"], "resource_names.egress_network"),
        workspace_volume=_require_str(
            value["workspace_volume"],
            "resource_names.workspace_volume",
        ),
        agent_home_volume=_require_str(
            value["agent_home_volume"],
            "resource_names.agent_home_volume",
        ),
        postgres_volume=_require_str(value["postgres_volume"], "resource_names.postgres_volume"),
        redis_volume=_require_str(value["redis_volume"], "resource_names.redis_volume"),
        checkpoint_volume=_require_str(
            value["checkpoint_volume"],
            "resource_names.checkpoint_volume",
        ),
        postgres_container=_require_str(
            value["postgres_container"],
            "resource_names.postgres_container",
        ),
        redis_container=_require_str(value["redis_container"], "resource_names.redis_container"),
    )
    if parsed.workspace_id != workspace_id:
        raise RuntimeError("resource_names.workspace_id mismatch")
    expected = CellResourceNames.for_workspace(workspace_id, namespace=parsed.namespace)
    if parsed != expected:
        raise RuntimeError("resource_names must match deterministic workspace names")
    return parsed


def _require_operation_records(value: object) -> list[CellOperationRecord]:
    if not isinstance(value, list):
        raise RuntimeError("operations must be list[object]")
    records: list[CellOperationRecord] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RuntimeError(f"operations[{index}] must be object")
        records.append(CellOperationRecord.from_dict(item))
    return records


def _read_workspace_payload_file(path: Path) -> dict[str, Any]:
    payload = _read_plain_json_file(path)
    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        raise RuntimeError(f"workspace payload missing: {path}")
    return workspace


def _read_plain_json_file(path: Path) -> dict[str, Any]:
    _ensure_secure_parent(path)
    _require_regular_file_path(path, missing_ok=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(path, flags)
    try:
        _validate_regular_fd(fd, expected_mode=_FILE_MODE)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        raise RuntimeError(f"invalid JSON payload: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid JSON payload: {path}")
    return payload


def _ensure_secure_parent(path: Path) -> None:
    _ensure_secure_dir(path.parent, create=False)


def _ensure_secure_dir(path: Path, *, create: bool) -> None:
    anchor = Path(path.anchor) if path.anchor else Path(".")
    current = anchor
    for part in path.parts[len(anchor.parts) :]:
        current = current / part
        info = _lstat_path(current)
        if info is not None:
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError(f"refuse symlink path component: {current}")
            if stat.S_ISDIR(info.st_mode) is False:
                raise RuntimeError(f"path component is not directory: {current}")
            if current == path:
                _validate_dir_stat(info, current)
            else:
                _validate_ancestor_dir_stat(info, current)
            continue
        if create is False:
            raise RuntimeError(f"required directory missing: {current}")
        try:
            current.mkdir(mode=_DIR_MODE)
        except FileExistsError as exc:
            info = _lstat_path(current)
            if info is None:
                raise
            if stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode) is False:
                raise RuntimeError(f"unsafe directory race: {current}") from exc
            _validate_dir_stat(info, current)
            continue
        try:
            os.chmod(current, _DIR_MODE)
        except OSError:
            pass
        _fsync_directory(current.parent)


def _replace_file(source: Path, destination: Path) -> None:
    _ensure_secure_parent(destination)
    _require_regular_file_path(destination, missing_ok=True)
    os.replace(source, destination)
    _fsync_directory(destination.parent)


def _ensure_exact_keys(payload: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(payload)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing={missing}")
    if extra:
        details.append(f"extra={extra}")
    suffix = f": {', '.join(details)}" if details else ""
    raise RuntimeError(f"{label} keys mismatch{suffix}")


def _lstat_path(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _require_regular_file_path(path: Path, *, missing_ok: bool) -> os.stat_result | None:
    info = _lstat_path(path)
    if info is None:
        if missing_ok:
            return None
        raise RuntimeError(f"required file missing: {path}")
    if stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"refuse symlink file: {path}")
    if stat.S_ISREG(info.st_mode) is False:
        raise RuntimeError(f"path is not regular file: {path}")
    return info


def _validate_regular_fd(fd: int, *, expected_mode: int) -> None:
    info = os.fstat(fd)
    if stat.S_ISREG(info.st_mode) is False:
        raise RuntimeError("state path is not regular file")
    if getattr(info, "st_nlink", 1) != 1:
        raise RuntimeError("state path must not be hardlinked")
    if os.name != "nt":
        uid = _current_uid()
        if uid is not None and info.st_uid != uid:
            raise RuntimeError("unsafe file owner")
        if stat.S_IMODE(info.st_mode) != expected_mode:
            raise RuntimeError("unsafe file mode")


def _validate_dir_stat(info: os.stat_result, path: Path) -> None:
    if os.name != "nt":
        uid = _current_uid()
        if uid is not None and info.st_uid != uid:
            raise RuntimeError(f"unsafe directory owner: {path}")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o077:
            raise RuntimeError(f"unsafe directory mode: {path}")


def _validate_ancestor_dir_stat(info: os.stat_result, path: Path) -> None:
    if os.name == "nt":
        return
    uid = _current_uid()
    if uid is not None and info.st_uid not in {0, uid}:
        raise RuntimeError(f"unsafe directory owner: {path}")
    mode = stat.S_IMODE(info.st_mode)
    writable_by_others = bool(mode & 0o022)
    trusted_sticky_root = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
    if writable_by_others and not trusted_sticky_root:
        raise RuntimeError(f"unsafe directory mode: {path}")


def _set_file_mode(fd: int, mode: int) -> None:
    chmod = getattr(os, "fchmod", None)
    if callable(chmod):
        cast(Callable[[int, int], None], chmod)(fd, mode)


def _current_uid() -> int | None:
    getter = getattr(os, "getuid", None)
    if not callable(getter):
        return None
    return cast(Callable[[], int], getter)()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        dir_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        return
    finally:
        os.close(dir_fd)
