"""Controller evidence that an ordinary generation resumed its own captured environment."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yleum_orchestrator.core.cell_resources import CellIdentityConflict, LifecycleMutation
from yleum_orchestrator.services.cell_state import (
    CellWorkspaceState,
    _read_plain_json_file,
    _require_regular_file_path,
)
from yleum_orchestrator.services.generation_reader_inventory import reader_dispatch
from yleum_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
)
from yleum_orchestrator.services.machine_startup import (
    EnvironmentIdentity,
    MachineInventoryUnavailable,
    StartupJournal,
    prepare_operation_id,
    require_startup_admission,
    startup_incarnation,
)
from yleum_orchestrator.services.project_machine import write_controller_json

if TYPE_CHECKING:
    from yleum_orchestrator.services.docker_machine_backend import DockerMachineBackend


_Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Epoch = Annotated[int, Field(gt=0)]
_Image = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]


class RecoveryEnvironmentUnproved(CellIdentityConflict):
    """The exact release or transition lacks trustworthy evidence."""


class RecoveryEnvironmentProof(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    version: Literal[1] = 1
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    release_operation_id: UUID
    previous_fencing_epoch: _Epoch
    release_fencing_epoch: _Epoch
    fencing_epoch: _Epoch
    source_sha256: _Sha
    manifest_sha256: _Sha
    previous_environment_sha256: _Sha
    environment_sha256: _Sha
    artifact_sha256: _Sha


@dataclass(frozen=True)
class RunningEnvironment:
    image_id: str
    identity: EnvironmentIdentity
    container_id: str
    started_at: str
    previous_image_inventory_sha256: str | None = None


class ReleaseEnvironment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    version: Literal[1] = 1
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    release_operation_id: UUID
    release_request_sha256: _Sha
    previous_fencing_epoch: _Epoch
    release_fencing_epoch: _Epoch
    source_sha256: _Sha
    manifest_sha256: _Sha
    physical_volume: str
    physical_volume_identity: _Sha
    previous_content_sha256: _Sha
    previous_incarnation: _Sha
    previous_container_id: _Sha
    previous_started_at: str
    previous_image_id: _Image
    artifact_ref_sha256: _Sha


def _owned_image(backend: DockerMachineBackend, container: Any, fence: int) -> str:
    image = container.attrs.get("Image")
    config = container.attrs.get("Config") or {}
    labels = config.get("Labels") or {}
    mounts = [
        mount for mount in (container.attrs.get("Mounts") or [])
        if mount.get("Destination") == "/workspace"
    ]
    if (
        not isinstance(image, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None
        or labels.get("omnia.fencing_epoch") != str(fence)
        or any(labels.get(key) != value for key, value in backend.labels("development").items())
        or len(mounts) != 1 or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != backend.workspace_volume
    ):
        raise RecoveryEnvironmentUnproved("physical environment identity conflicts")
    return image


def stopped_environment_matches(
    backend: DockerMachineBackend, fence: int, *,
    container_id: str, image_id: str, started_at: str,
) -> bool:
    container = backend._container()
    if container is None:
        return False
    reader_dispatch(container.reload)
    state = container.attrs.get("State") or {}
    return (
        container.id == container.attrs.get("Id") == container_id
        and state.get("Status") == "exited"
        and state.get("Running") is False and state.get("Paused") is False
        and state.get("StartedAt") == started_at
        and _owned_image(backend, container, fence) == image_id
    )


def running_environment(
    backend: DockerMachineBackend, fence: int, *, previous_image_id: str | None = None,
) -> RunningEnvironment:
    container = backend._container()
    if container is None:
        raise MachineInventoryUnavailable("missing")
    before = startup_incarnation(container)
    image = _owned_image(backend, container, fence)
    started_at = container.attrs["State"]["StartedAt"]
    if previous_image_id is None:
        identity, comparison = backend.environment_identity(), None
    else:
        identity, comparison = backend.environment_recovery_identity(previous_image_id)
    if (
        identity.incarnation != before or startup_incarnation(container) != before
        or container.attrs.get("Image") != image
    ):
        raise RecoveryEnvironmentUnproved("running environment changed during observation")
    return RunningEnvironment(image, identity, container.id, started_at, comparison)


def _record_path(root: Path, workspace_id: UUID, release_id: UUID) -> Path:
    return root / str(workspace_id) / "generation-releases" / f"{release_id}.json"


def _reference_digest(reference: MachineEnvironmentRef) -> str:
    return hashlib.sha256(json.dumps(
        reference.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def record_release(
    root: Path, state: CellWorkspaceState, mutation: LifecycleMutation, *,
    before: RunningEnvironment, source: str, volume: str, volume_identity: str,
    reference: MachineEnvironmentRef,
) -> None:
    epoch = state.active_generation_fencing_epoch
    if (
        state.project_id is None or state.owner_id is None or state.active_generation_run_id is None
        or epoch is None or mutation.fencing_epoch != epoch + 1
        or reference.workspace_id != state.workspace_id
        or volume not in {entry.name for entry in reference.volumes}
    ):
        raise RecoveryEnvironmentUnproved("release environment capture conflicts")
    path = _record_path(root, state.workspace_id, mutation.operation_id)
    record = ReleaseEnvironment(
        workspace_id=state.workspace_id, project_id=state.project_id, owner_id=state.owner_id,
        generation_run_id=state.active_generation_run_id,
        release_operation_id=mutation.operation_id, release_request_sha256=mutation.request_digest,
        previous_fencing_epoch=epoch, release_fencing_epoch=mutation.fencing_epoch,
        source_sha256=source, manifest_sha256=reference.manifest_digest,
        physical_volume=volume, physical_volume_identity=volume_identity,
        previous_content_sha256=before.identity.content_digest,
        previous_incarnation=before.identity.incarnation, previous_image_id=before.image_id,
        previous_container_id=before.container_id, previous_started_at=before.started_at,
        artifact_ref_sha256=_reference_digest(reference),
    )
    existing = _load_release(root, state.workspace_id, mutation.operation_id)
    if existing is None:
        write_controller_json(path, record.model_dump(mode="json"), durable=True)
    elif existing != record:
        raise RecoveryEnvironmentUnproved("release environment capture was replaced")


def _load_release(root: Path, workspace_id: UUID, release_id: UUID) -> ReleaseEnvironment | None:
    path = _record_path(root, workspace_id, release_id)
    try:
        info = _require_regular_file_path(path, missing_ok=True)
        if info is None:
            return None
        if info.st_size > 8192:
            raise RecoveryEnvironmentUnproved("release environment evidence is unbounded")
        return ReleaseEnvironment.model_validate_json(json.dumps(_read_plain_json_file(path)))
    except (OSError, RuntimeError, ValueError) as error:
        raise RecoveryEnvironmentUnproved("release environment evidence is invalid") from error


def replay_release_capture(
    root: Path, state: CellWorkspaceState, mutation: LifecycleMutation,
    backend: DockerMachineBackend, *, source: str, manifest: str,
    volume_identity: str,
) -> MachineEnvironmentRef | None:
    record = _load_release(root, state.workspace_id, mutation.operation_id)
    if record is None:
        return None
    previous_fence = record.previous_fencing_epoch
    if (
        (record.workspace_id, record.project_id, record.owner_id, record.generation_run_id)
        != (state.workspace_id, state.project_id, state.owner_id, state.active_generation_run_id)
        or (
            record.release_operation_id, record.release_request_sha256,
            record.release_fencing_epoch,
        ) != (mutation.operation_id, mutation.request_digest, mutation.fencing_epoch)
        or previous_fence != state.active_generation_fencing_epoch
        or mutation.fencing_epoch != previous_fence + 1
        or (record.source_sha256, record.manifest_sha256) != (source, manifest)
        or (record.physical_volume, record.physical_volume_identity)
        != (backend.workspace_volume, volume_identity)
    ):
        raise RecoveryEnvironmentUnproved("release replay binding changed")
    metadata = backend._metadata()
    reference = MachineEnvironmentRef.model_validate(metadata.get("environment_ref"))
    if (
        _reference_digest(reference) != record.artifact_ref_sha256
        or reference.workspace_id != state.workspace_id
        or metadata.get("environment_revision") != source
        or metadata.get("restored_image") != reference.image_id
        or reference.base_image != backend.base_image
        or metadata.get("restore_in_progress") or metadata.get("restore_target")
        or metadata.get("pending_image")
        or metadata.get("quiesce_state") in {"pending", "failed"}
    ):
        raise RecoveryEnvironmentUnproved("release replay artifact changed")

    def require_original_stopped() -> None:
        if not stopped_environment_matches(
            backend, previous_fence, container_id=record.previous_container_id,
            image_id=record.previous_image_id, started_at=record.previous_started_at,
        ):
            raise RecoveryEnvironmentUnproved("release replay container changed")

    require_original_stopped()
    MachineEnvironmentStore(
        root / "artifacts", state.workspace_id, backend, max_bytes=backend.disk_bytes,
    ).validate(reference, manifest_digest=manifest)
    require_original_stopped()
    return reference


def verify_release(
    root: Path, state: CellWorkspaceState, run_id: UUID, fence: int, release_id: UUID, *,
    source: str, manifest: str, volume: str, volume_identity: str,
) -> ReleaseEnvironment:
    release = state.operation(release_id)
    record = _load_release(root, state.workspace_id, release_id)
    if (
        release is None or record is None
        or (record.workspace_id, record.project_id, record.owner_id, record.generation_run_id)
        != (state.workspace_id, state.project_id, state.owner_id, run_id)
        or record.release_operation_id != release_id
        or (release.kind, release.status, release.phase, release.bundle_state)
        != ("release", "completed", "completed", "resources_ready")
        or (release.generation_run_id, release.request_digest, release.fencing_epoch)
        != (run_id, record.release_request_sha256, record.release_fencing_epoch)
        or record.release_fencing_epoch != record.previous_fencing_epoch + 1
        or fence != record.release_fencing_epoch + 1
        or release.detail != f"retained_source_fencing_epoch={record.previous_fencing_epoch}"
        or (state.active_generation_run_id, state.active_generation_fencing_epoch,
            state.fencing_epoch) != (run_id, fence, fence)
        or (source, manifest, volume, volume_identity) != (
            record.source_sha256, record.manifest_sha256,
            record.physical_volume, record.physical_volume_identity,
        )
    ):
        raise RecoveryEnvironmentUnproved("release environment evidence does not match this lease")
    return record


def finish_proof(
    root: Path, state: CellWorkspaceState, record: ReleaseEnvironment,
    reference: MachineEnvironmentRef, current: RunningEnvironment, fence: int,
) -> RecoveryEnvironmentProof:
    try:
        admission = require_startup_admission(state, record.generation_run_id, fence)
        preparation = StartupJournal(root, state.workspace_id).load(
            prepare_operation_id(record.generation_run_id, fence)
        )
    except (CellIdentityConflict, ValueError, ValidationError) as error:
        raise RecoveryEnvironmentUnproved("recovery startup identity conflicts") from error
    if preparation is None or preparation.state != "completed":
        raise MachineInventoryUnavailable("prepare_pending")
    binding = preparation.binding
    if (
        (binding.workspace_id, binding.project_id, binding.owner_id,
         binding.generation_run_id, binding.fencing_epoch)
        != (
            record.workspace_id, record.project_id, record.owner_id,
            record.generation_run_id, fence,
        )
        or (binding.admission_operation_id, binding.admission_request_sha256)
        != (admission.operation_id, admission.request_digest)
        or (binding.workspace_revision, binding.manifest_digest,
            binding.physical_volume, binding.physical_volume_identity)
        != (record.source_sha256, record.manifest_sha256,
            record.physical_volume, record.physical_volume_identity)
        or reference.workspace_id != state.workspace_id
        or _reference_digest(reference) != record.artifact_ref_sha256
        or current.image_id != reference.image_id
        or current.previous_image_inventory_sha256 != record.previous_content_sha256
        or current.identity.incarnation != preparation.incarnation
        or current.identity.incarnation == record.previous_incarnation
    ):
        raise RecoveryEnvironmentUnproved("recovery startup did not restore the released artifact")
    previous = EnvironmentIdentity(record.previous_content_sha256, record.previous_incarnation)
    return RecoveryEnvironmentProof(
        workspace_id=record.workspace_id, project_id=record.project_id, owner_id=record.owner_id,
        generation_run_id=record.generation_run_id,
        release_operation_id=record.release_operation_id,
        previous_fencing_epoch=record.previous_fencing_epoch,
        release_fencing_epoch=record.release_fencing_epoch, fencing_epoch=fence,
        source_sha256=record.source_sha256, manifest_sha256=record.manifest_sha256,
        previous_environment_sha256=previous.digest, environment_sha256=current.identity.digest,
        artifact_sha256=reference.sha256,
    )
