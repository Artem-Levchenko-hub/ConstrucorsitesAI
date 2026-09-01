"""Immutable Project Cell resource contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

_IMAGE_DIGEST_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_REQUEST_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")


class CellResourceSettings(Protocol):
    workspace_provider: str
    docker_owner_canary_enabled: bool
    cell_profile_version: str
    cell_postgres_image: str
    cell_redis_image: str
    cell_backup_image: str
    cell_max_active_bundles: int
    cell_bundle_cpu_cores: float
    cell_bundle_memory_bytes: int
    cell_host_cpu_reserve_cores: float
    cell_host_memory_reserve_bytes: int
    cell_required_free_disk_bytes: int
    cell_host_disk_reserve_bytes: int
    cell_required_free_inodes: int
    cell_host_inode_reserve: int
    cell_state_path: str


class WorkspaceIdentitySpec(Protocol):
    @property
    def workspace_id(self) -> UUID: ...

    @property
    def project_id(self) -> UUID: ...

    @property
    def owner_id(self) -> UUID: ...

    @property
    def profile_version(self) -> str: ...


class CellResourceError(RuntimeError):
    """Base resource-management failure."""


class CellIdentityConflict(CellResourceError):
    """A named Docker resource exists with mismatched identity labels."""


class CellIndeterminateOperation(CellResourceError):
    """A previous operation ended in an indeterminate state."""


class CellFenceRejected(CellResourceError):
    """A fencing epoch was stale or already consumed."""


class WorkspaceLockUnavailable(CellResourceError):
    """The workspace file lock could not be established safely."""


class WorkspaceLockTimeout(CellResourceError):
    """The workspace file lock was not acquired before the deadline."""


class CellRestoreFailed(CellResourceError):
    """Checkpoint restore failed; rollback may already have run."""


@dataclass(frozen=True, slots=True)
class CellResourceProfile:
    profile_version: str
    postgres_image: str
    redis_image: str
    backup_image: str
    max_active_bundles: int
    bundle_cpu_cores: float
    bundle_memory_bytes: int
    host_cpu_reserve_cores: float
    host_memory_reserve_bytes: int
    required_free_disk_bytes: int
    host_disk_reserve_bytes: int
    required_free_inodes: int
    host_inode_reserve: int
    state_path: str

    @classmethod
    def from_settings(cls, settings: CellResourceSettings) -> CellResourceProfile:
        profile = cls(
            profile_version=str(settings.cell_profile_version),
            postgres_image=str(settings.cell_postgres_image),
            redis_image=str(settings.cell_redis_image),
            backup_image=str(settings.cell_backup_image),
            max_active_bundles=int(settings.cell_max_active_bundles),
            bundle_cpu_cores=float(settings.cell_bundle_cpu_cores),
            bundle_memory_bytes=int(settings.cell_bundle_memory_bytes),
            host_cpu_reserve_cores=float(settings.cell_host_cpu_reserve_cores),
            host_memory_reserve_bytes=int(settings.cell_host_memory_reserve_bytes),
            required_free_disk_bytes=int(settings.cell_required_free_disk_bytes),
            host_disk_reserve_bytes=int(settings.cell_host_disk_reserve_bytes),
            required_free_inodes=int(settings.cell_required_free_inodes),
            host_inode_reserve=int(settings.cell_host_inode_reserve),
            state_path=str(settings.cell_state_path),
        )
        provider_selected = (
            settings.workspace_provider == "docker_owner_canary"
            and settings.docker_owner_canary_enabled is True
        )
        if provider_selected:
            images = {
                "cell_postgres_image": profile.postgres_image,
                "cell_redis_image": profile.redis_image,
                "cell_backup_image": profile.backup_image,
            }
            invalid = {
                key: value
                for key, value in images.items()
                if not value or _IMAGE_DIGEST_RE.fullmatch(value) is None
            }
            if invalid:
                values = ", ".join(f"{key}={value!r}" for key, value in sorted(invalid.items()))
                raise ValueError(f"project cell images must be digest pinned: {values}")
        return profile


@dataclass(frozen=True, slots=True)
class CellResourceNames:
    workspace_id: UUID
    namespace: Literal["prod", "test"]
    internal_network: str
    egress_network: str
    workspace_volume: str
    agent_home_volume: str
    postgres_volume: str
    redis_volume: str
    checkpoint_volume: str
    postgres_container: str
    redis_container: str

    @classmethod
    def for_workspace(
        cls,
        workspace_id: UUID,
        *,
        namespace: Literal["prod", "test"] = "prod",
    ) -> CellResourceNames:
        prefix = "omnia-cell" if namespace == "prod" else "omnia-cell-test"
        stem = f"{prefix}-{workspace_id.hex}"
        return cls(
            workspace_id=workspace_id,
            namespace=namespace,
            internal_network=f"{stem}-internal",
            egress_network=f"{stem}-egress",
            workspace_volume=f"{stem}-workspace",
            agent_home_volume=f"{stem}-agent-home",
            postgres_volume=f"{stem}-postgres",
            redis_volume=f"{stem}-redis",
            checkpoint_volume=f"{stem}-checkpoints",
            postgres_container=f"{stem}-postgres",
            redis_container=f"{stem}-redis",
        )

    @property
    def retained_volumes(self) -> tuple[str, str, str, str, str]:
        return (
            self.workspace_volume,
            self.agent_home_volume,
            self.postgres_volume,
            self.redis_volume,
            self.checkpoint_volume,
        )

    @property
    def networks(self) -> tuple[str, str]:
        return (self.internal_network, self.egress_network)

    def helper_container_name(self, kind: str, operation_id: UUID) -> str:
        return f"{self._stem}-{kind}-{operation_id.hex[:12]}"

    def secret_staging_volume_name(self, operation_id: UUID, purpose: str) -> str:
        return f"{self._stem}-secret-{purpose}-{operation_id.hex[:12]}"

    def checkpoint_directory(self, checkpoint_ref: str) -> str:
        validate_checkpoint_ref(checkpoint_ref)
        return checkpoint_ref

    @property
    def _stem(self) -> str:
        return self.postgres_container.rsplit("-postgres", 1)[0]


def identity_labels(spec: WorkspaceIdentitySpec, resource_kind: str) -> dict[str, str]:
    return {
        "omnia.managed": "true",
        "omnia.project_cell": "true",
        "omnia.workspace_id": str(spec.workspace_id),
        "omnia.project_id": str(spec.project_id),
        "omnia.owner_id": str(spec.owner_id),
        "omnia.provider": "docker_owner_canary",
        "omnia.profile_version": str(spec.profile_version),
        "omnia.resource_kind": resource_kind,
    }


@dataclass(frozen=True, slots=True)
class LifecycleMutation:
    operation_id: UUID
    fencing_epoch: int
    request_digest: str

    def __post_init__(self) -> None:
        if self.fencing_epoch <= 0:
            raise ValueError("fencing_epoch must be positive")
        if _REQUEST_DIGEST_RE.fullmatch(self.request_digest) is None:
            raise ValueError("request_digest must be a lowercase sha256 hex digest")


@dataclass(frozen=True, slots=True)
class FilesystemCapacityEvidence:
    path: str
    free_bytes: int
    free_inodes: int


@dataclass(frozen=True, slots=True)
class DockerDaemonIdentity:
    id: str
    name: str
    docker_root_dir: str
    operating_system: str


@dataclass(frozen=True, slots=True)
class HostCapacitySnapshot:
    cpu_count: int
    load_1m: float
    memory_available_bytes: int
    disk_free_bytes: int
    disk_free_inodes: int
    active_bundle_count: int
    disk_path: str
    filesystem_evidence: tuple[FilesystemCapacityEvidence, ...] = ()
    daemon: DockerDaemonIdentity | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    reason: str


def validate_checkpoint_ref(checkpoint_ref: str) -> str:
    if _CHECKPOINT_REF_RE.fullmatch(checkpoint_ref) is None:
        raise ValueError("checkpoint_ref must match ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
    return checkpoint_ref


def is_digest_pinned_image(image: str) -> bool:
    return _IMAGE_DIGEST_RE.fullmatch(image) is not None


def state_root_from_path(state_path: str) -> Path:
    return Path(state_path).expanduser().resolve().parent
