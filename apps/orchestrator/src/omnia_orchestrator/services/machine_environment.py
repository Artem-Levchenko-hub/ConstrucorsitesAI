"""Controller-owned immutable image/volume artifacts; nothing is extracted on the host."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.cell_state import _ensure_secure_dir
from omnia_orchestrator.services.project_machine import machine_remaining_seconds


class EnvironmentIntegrityError(RuntimeError):
    pass


class VolumeEnvironmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    artifact_ref: str = Field(pattern=r"^[0-9a-f]{32}\.tar$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)


class MachineEnvironmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_id: UUID
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_ref: str = Field(pattern=r"^[0-9a-f]{32}\.tar$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    base_image: str
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    volumes: tuple[VolumeEnvironmentRef, ...]
    manifest: MachineManifest | None = None


class EnvironmentBackend(Protocol):
    def prepare_capture(self) -> None: ...
    def validate_restore(self, reference: MachineEnvironmentRef) -> None: ...
    def stop(self) -> None: ...
    def export_image(self) -> tuple[str, Iterable[bytes]]: ...
    def export_volume(self, name: str) -> Iterable[bytes]: ...
    def import_image(self, path: Path, image_id: str) -> None: ...
    def import_volume(self, name: str, path: Path) -> None: ...
    def begin_restore(self, reference: MachineEnvironmentRef) -> None: ...
    def finish_restore(self) -> None: ...


class MachineEnvironmentStore:
    def __init__(
        self, root: Path, workspace_id: UUID, backend: EnvironmentBackend, *, max_bytes: int
    ) -> None:
        self.root = root / str(workspace_id)
        self.workspace_id = workspace_id
        self.backend = backend
        self.max_bytes = max_bytes

    def artifact_path(self, reference: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}\.tar", reference) is None:
            raise EnvironmentIntegrityError("invalid artifact reference")
        return self.root / reference

    def _save(self, chunks: Iterable[bytes], remaining: int) -> tuple[str, str, int]:
        _ensure_secure_dir(self.root, create=True)
        reference = f"{uuid4().hex}.tar"
        path = self.artifact_path(reference)
        digest = hashlib.sha256()
        size = 0
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    machine_remaining_seconds(1)
                    size += len(chunk)
                    if size > remaining:
                        raise EnvironmentIntegrityError("environment artifact exceeds disk budget")
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if not size:
                raise EnvironmentIntegrityError("empty environment artifact")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return reference, digest.hexdigest(), size

    def capture(
        self,
        *,
        manifest_digest: str,
        base_image: str,
        volumes: tuple[str, ...],
        manifest: MachineManifest | None = None,
    ) -> MachineEnvironmentRef:
        if manifest is not None and manifest.digest() != manifest_digest:
            raise EnvironmentIntegrityError("capture manifest digest mismatch")
        self.backend.prepare_capture()
        machine_remaining_seconds(1)
        self.backend.stop()
        machine_remaining_seconds(1)
        image_id, chunks = self.backend.export_image()
        reference, digest, size = self._save(chunks, self.max_bytes)
        remaining = self.max_bytes - size
        volume_refs = []
        for name in volumes:
            machine_remaining_seconds(1)
            artifact, checksum, volume_size = self._save(
                self.backend.export_volume(name), remaining
            )
            remaining -= volume_size
            volume_refs.append(
                VolumeEnvironmentRef(
                    name=name,
                    artifact_ref=artifact,
                    sha256=checksum,
                    size=volume_size,
                )
            )
        return MachineEnvironmentRef(
            workspace_id=self.workspace_id,
            image_id=image_id,
            artifact_ref=reference,
            sha256=digest,
            size=size,
            base_image=base_image,
            manifest_digest=manifest_digest,
            volumes=tuple(volume_refs),
            manifest=manifest,
        )

    def validate(self, reference: MachineEnvironmentRef, *, manifest_digest: str) -> None:
        if reference.workspace_id != self.workspace_id:
            raise EnvironmentIntegrityError("environment workspace identity mismatch")
        if reference.manifest_digest != manifest_digest:
            raise EnvironmentIntegrityError("environment manifest digest mismatch")
        if reference.manifest is not None and reference.manifest.digest() != manifest_digest:
            raise EnvironmentIntegrityError("environment embedded manifest digest mismatch")
        _ensure_secure_dir(self.root, create=False)
        artifacts: list[MachineEnvironmentRef | VolumeEnvironmentRef] = [
            reference,
            *reference.volumes,
        ]
        if sum(item.size for item in artifacts) > self.max_bytes:
            raise EnvironmentIntegrityError("environment exceeds disk budget")
        # Check every artifact before the first import; a bad volume must not
        # result in an apparently restored image paired with incomplete data.
        for item in artifacts:
            path = self.artifact_path(item.artifact_ref)
            if path.is_symlink() or not path.is_file():
                raise EnvironmentIntegrityError("environment artifact missing or unsafe")
            if path.stat().st_size != item.size:
                raise EnvironmentIntegrityError("environment artifact size/digest mismatch")
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != item.sha256:
                raise EnvironmentIntegrityError("environment artifact digest mismatch")

    def restore(self, reference: MachineEnvironmentRef, *, manifest_digest: str) -> None:
        self.validate(reference, manifest_digest=manifest_digest)
        self.backend.begin_restore(reference)
        self.backend.stop()
        self.backend.import_image(self.artifact_path(reference.artifact_ref), reference.image_id)
        for volume in reference.volumes:
            self.backend.import_volume(volume.name, self.artifact_path(volume.artifact_ref))
        self.backend.validate_restore(reference)
        self.backend.finish_restore()
