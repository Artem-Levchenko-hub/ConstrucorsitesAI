"""Controller-owned immutable image/volume artifacts; nothing is extracted on the host."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.cell_state import _ensure_secure_dir
from omnia_orchestrator.services.project_machine import (
    machine_remaining_seconds,
    write_controller_json,
)


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
    def can_reuse_image(self, reference: MachineEnvironmentRef) -> bool: ...
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
        # P01: optional stage/byte progress sink (PublicationTrace); never required.
        self.observer: Any = None

    def _stage(self, name: str) -> None:
        if self.observer is not None:
            self.observer.stage(name)

    # -- seal markers (P08): an archive this controller hashed while writing and
    # fsync'd is immutable content-addressed data; re-reading it before import
    # only burns disk time. The marker binds the digest to the exact file
    # identity (size, mtime, inode); any later change invalidates the seal and
    # the archive is hashed in full again. Foreign or legacy archives without a
    # marker are always hashed.
    @staticmethod
    def _marker_path(path: Path) -> Path:
        return path.with_name(path.name + ".ok")

    def _seal(self, path: Path, digest: str, size: int) -> None:
        stat = path.stat()
        write_controller_json(
            self._marker_path(path),
            {"sha256": digest, "size": size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino},
        )

    def _sealed(self, path: Path, digest: str, size: int) -> bool:
        marker = self._marker_path(path)
        try:
            if marker.is_symlink() or not marker.is_file():
                return False
            recorded = json.loads(marker.read_text(encoding="utf-8"))
            stat = path.stat()
        except (OSError, ValueError):
            return False
        return bool(
            recorded.get("sha256") == digest
            and recorded.get("size") == size == stat.st_size
            and recorded.get("mtime_ns") == stat.st_mtime_ns
            and recorded.get("inode") == stat.st_ino
        )

    def _digest(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                machine_remaining_seconds(1)
                digest.update(chunk)
                if self.observer is not None:
                    self.observer.add_bytes(len(chunk))
        return digest.hexdigest()

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
                    if self.observer is not None:
                        self.observer.add_bytes(len(chunk))
                handle.flush()
                os.fsync(handle.fileno())
            if not size:
                raise EnvironmentIntegrityError("empty environment artifact")
            self._seal(path, digest.hexdigest(), size)
        except BaseException:
            path.unlink(missing_ok=True)
            self._marker_path(path).unlink(missing_ok=True)
            raise
        return reference, digest.hexdigest(), size

    def capture(
        self,
        *,
        manifest_digest: str,
        base_image: str,
        volumes: tuple[str, ...],
        manifest: MachineManifest | None = None,
        previous: MachineEnvironmentRef | None = None,
    ) -> MachineEnvironmentRef:
        if manifest is not None and manifest.digest() != manifest_digest:
            raise EnvironmentIntegrityError("capture manifest digest mismatch")
        if previous is not None and previous.workspace_id != self.workspace_id:
            raise EnvironmentIntegrityError("environment workspace identity mismatch")
        self.backend.prepare_capture()
        machine_remaining_seconds(1)
        self.backend.stop()
        machine_remaining_seconds(1)
        self._stage("capture_rootfs")
        if previous is not None and self._reusable_image(previous, base_image=base_image):
            image_id = previous.image_id
            reference, digest, size = previous.artifact_ref, previous.sha256, previous.size
        else:
            image_id, chunks = self.backend.export_image()
            reference, digest, size = self._save(chunks, self.max_bytes)
        self._stage("capture_volumes")
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

    def _reusable_image(self, previous: MachineEnvironmentRef, *, base_image: str) -> bool:
        # Only the immutable rootfs is shared. Every volume below is captured
        # anew, including source, dependencies and the stopped project database.
        if previous.base_image != base_image or not self.backend.can_reuse_image(previous):
            return False
        if previous.size > self.max_bytes:
            raise EnvironmentIntegrityError("environment artifact exceeds disk budget")
        if previous.manifest is not None and previous.manifest.digest() != previous.manifest_digest:
            return False
        try:
            _ensure_secure_dir(self.root, create=True)
            path = self.artifact_path(previous.artifact_ref)
            if path.is_symlink() or not path.is_file() or path.stat().st_size != previous.size:
                return False
            if self._sealed(path, previous.sha256, previous.size):
                if self.observer is not None:
                    self.observer.add_bytes(previous.size)
                return True
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    machine_remaining_seconds(1)
                    digest.update(chunk)
                    if self.observer is not None:
                        self.observer.add_bytes(len(chunk))
            return digest.hexdigest() == previous.sha256
        except TimeoutError:
            raise
        except OSError:
            # Missing or unreadable cached data never substitutes for a fresh
            # export. Deadline and identity failures deliberately propagate.
            return False

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
            if self._sealed(path, item.sha256, item.size):
                if self.observer is not None:
                    self.observer.add_bytes(item.size)
                continue
            if self._digest(path) != item.sha256:
                raise EnvironmentIntegrityError("environment artifact digest mismatch")

    def restore(
        self,
        reference: MachineEnvironmentRef,
        *,
        manifest_digest: str,
        preserve_volumes: frozenset[str] = frozenset(),
    ) -> None:
        self.validate(reference, manifest_digest=manifest_digest)
        self.backend.begin_restore(reference)
        self.backend.stop()
        self.backend.import_image(self.artifact_path(reference.artifact_ref), reference.image_id)
        for volume in reference.volumes:
            if volume.name in preserve_volumes:
                continue  # Live business data outranks an older checkpoint copy.
            self.backend.import_volume(volume.name, self.artifact_path(volume.artifact_ref))
        self.backend.validate_restore(reference)
        self.backend.finish_restore()
