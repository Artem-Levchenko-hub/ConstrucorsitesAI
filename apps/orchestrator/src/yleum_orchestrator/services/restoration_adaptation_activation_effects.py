"""Real Project Cell effects for proof-ready adaptation activation."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import tarfile
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import UUID, uuid5

from yleum_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from yleum_orchestrator.routers.runtime import _workspace_revision
from yleum_orchestrator.routers.workspace import _read_agent_workspace_files
from yleum_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    ActivationObservedIdentity,
    ActivationPreparedTarget,
    RestorationAdaptationActivationRequest,
)
from yleum_orchestrator.services.code_restoration_engine import CodeRestorationEngine
from yleum_orchestrator.services.project_machine import machine_effect
from yleum_orchestrator.services.restoration_adaptation_probe import validate_probe_contract
from yleum_orchestrator.services.restoration_adaptation_source import (
    canonical_source_files,
    source_manifest_digest,
)
from yleum_orchestrator.services.restoration_binding import (
    canonical_digest,
    observe_live_source,
)
from yleum_orchestrator.services.restoration_catalog import catalog_contract

_MAX_RUNNABLE_EXPORT_BYTES = 640 * 1024 * 1024
_MAX_RUNNABLE_FILE_BYTES = 128 * 1024 * 1024
_MAX_RUNNABLE_FILES = 100_000
_RUNTIME_ROOTS = frozenset({".next", "node_modules"})


def live_database_volume_identity_digest(
    backend: Any,
    *,
    expected_volume: str,
) -> str:
    """Stable live-business identity shared by offer, effects, and health proof."""

    if backend.project_postgres_volume != expected_volume:
        raise CellIdentityConflict("adaptation live database volume changed")
    volume = backend._lookup(backend.client.volumes, expected_volume, "project-volume")
    attrs = volume.attrs if volume is not None else {}
    if volume is None or attrs.get("Name") != expected_volume:
        raise CellIdentityConflict("adaptation live database volume is unavailable")
    return canonical_digest(
        {
            "name": attrs.get("Name"),
            "created_at": attrs.get("CreatedAt"),
            "driver": attrs.get("Driver"),
            "scope": attrs.get("Scope"),
            "options": attrs.get("Options") or {},
            "labels": attrs.get("Labels") or {},
        }
    )


@dataclass(frozen=True, slots=True)
class ActivationHealthTarget:
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    fencing_epoch: int
    code_volume: str
    database_volume: str
    database_identity_digest: str
    business_probe: ActivationBusinessProbe


class ActivationHealthProber(Protocol):
    """API-backed signed probes. Every method returns an evidence SHA-256.

    ``verify_signed_owner_create_update_delete`` must prove create, update,
    delete, and a persistent read after the mutations. Reload is deliberately
    separate so a provider cannot satisfy both checks with one cached response.
    """

    async def verify_service_readiness(
        self, request: RestorationAdaptationActivationRequest, target: ActivationHealthTarget
    ) -> str: ...

    async def verify_signed_owner_read(
        self, request: RestorationAdaptationActivationRequest, target: ActivationHealthTarget
    ) -> str: ...

    async def verify_signed_owner_create_update_delete(
        self, request: RestorationAdaptationActivationRequest, target: ActivationHealthTarget
    ) -> str: ...

    async def verify_signed_owner_reload(
        self, request: RestorationAdaptationActivationRequest, target: ActivationHealthTarget
    ) -> str: ...

    async def verify_cross_owner_denial(
        self, request: RestorationAdaptationActivationRequest, target: ActivationHealthTarget
    ) -> str: ...

    async def verify_unauthenticated_denial(
        self, request: RestorationAdaptationActivationRequest, target: ActivationHealthTarget
    ) -> str: ...


class DockerRestorationAdaptationActivationEffects:
    """Code-only activation effects bound to real Project Cell identities."""

    def __init__(
        self,
        *,
        code_engine: CodeRestorationEngine,
        candidate_manager_factory: Callable[[Any], Any],
        health_prober: ActivationHealthProber | None,
    ) -> None:
        if health_prober is None:
            raise CellResourceError("signed activation health prober is required")
        self.code_engine = code_engine
        self.candidate_manager_factory = candidate_manager_factory
        self.health_prober = health_prober

    @asynccontextmanager
    async def hold_transition(
        self, request: RestorationAdaptationActivationRequest
    ) -> AsyncIterator[None]:
        """Serialize activation with every ordinary source lifecycle mutation."""

        manager = self.code_engine.activation_manager(request.source_workspace_id)
        async with manager.operation_lock.hold(request.source_workspace_id):
            yield

    async def observe_bound_identities(
        self, request: RestorationAdaptationActivationRequest
    ) -> ActivationObservedIdentity:
        _manager, _state, _machine, source, _source_files, _observed = await self._source(
            request, require_running=True
        )
        _candidate_manager, _candidate_state, _candidate_machine, _backend, files = (
            await self._candidate(request)
        )
        await self._require_probe_binding(request, files, source)
        return ActivationObservedIdentity.from_request(request)

    async def prepare_target_code(
        self,
        request: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
        target_code_volume: str,
    ) -> ActivationPreparedTarget:
        manager, state, _machine, source, _files, _observed = await self._source(
            request, require_running=True
        )
        if self._file_digest(sealed_artifact) != request.candidate_code_digest:
            raise CellIdentityConflict("adaptation sealed code archive changed")
        if target_code_volume.casefold() in {
            source.workspace_volume.casefold(),
            source.project_postgres_volume.casefold(),
        }:
            raise CellIdentityConflict("adaptation target code aliases a protected volume")
        if target_code_volume != f"{source.stem}-code-{request.activation_id.hex}":
            raise CellIdentityConflict("adaptation target code volume is outside machine identity")
        candidate_manager = self.candidate_manager_factory(manager)
        runnable_artifact = sealed_artifact
        runnable_digest = self._runnable_archive_digest(sealed_artifact)
        temporary = sealed_artifact.parent / f".{request.activation_id}.runnable.partial"
        temporary.unlink(missing_ok=True)
        try:
            async with candidate_manager.operation_lock.hold(
                request.candidate_workspace_id
            ):
                (
                    observed_candidate_manager,
                    _candidate_state,
                    _candidate_machine,
                    candidate,
                    candidate_files,
                ) = await self._candidate(request)
                if observed_candidate_manager.state_store is not candidate_manager.state_store:
                    raise CellIdentityConflict(
                        "adaptation candidate manager identity changed"
                    )
                if self._requires_next_runtime(candidate_files):
                    runnable_digest = await machine_effect(
                        self._write_runnable_archive,
                        candidate,
                        candidate.workspace_volume,
                        sealed_artifact,
                        temporary,
                    )
                    runnable_artifact = temporary

            target = replace(source, workspace_volume=target_code_volume)
            labels = {
                **manager._state_labels(state, "project-volume"),
                **target.labels("project-volume"),
                **target.restoration_volume_labels(
                    request.operation_id,
                    purpose="code",
                    binding_digest=request.activation_digest,
                    artifact_digest=request.candidate_code_digest,
                ),
                "omnia.restoration_runnable_digest": runnable_digest,
                "omnia.restoration_runnable_source_digest": request.candidate_code_digest,
            }
            existing = target._lookup(
                target.client.volumes, target_code_volume, "project-volume"
            )
            if existing is None:
                await manager._ensure_volume(target_code_volume, labels)
            self._require_volume_labels(target, target_code_volume, labels)
            await machine_effect(target.import_volume, target_code_volume, runnable_artifact)
        finally:
            temporary.unlink(missing_ok=True)
        target_files = await self._workspace_files(manager, target_code_volume)
        target_source_files = await self._workspace_source_files(manager, target_code_volume)
        self._require_candidate_files(request, target_files, target_source_files)
        await self._require_probe_binding(request, target_files, source)
        return ActivationPreparedTarget(
            workspace_id=request.source_workspace_id,
            fencing_epoch=request.target_fencing_epoch,
            code_volume=target_code_volume,
            code_digest=request.candidate_code_digest,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
        )

    async def verify_prepared_target(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> str:
        """Verify the bound target without consulting the disposable candidate."""

        self._require_target(request, target)
        manager, _state, _machine, source, _files, _observed = await self._source(
            request, require_running=False
        )
        volume = self._require_target_volume(request, source, target.code_volume)
        if volume is None:  # pragma: no cover - allow_missing is false above
            raise CellIdentityConflict("adaptation target code volume is missing")
        labels = (volume.attrs or {}).get("Labels") or {}
        expected_attestation = labels.get("omnia.restoration_runnable_digest")
        observed_attestation = await machine_effect(
            self._target_volume_attestation,
            source,
            target.code_volume,
        )
        if (
            not isinstance(observed_attestation, str)
            or observed_attestation != expected_attestation
        ):
            raise CellIdentityConflict(
                "adaptation prepared target runnable content changed"
            )
        target_files = await self._workspace_files(manager, target.code_volume)
        target_source_files = await self._workspace_source_files(
            manager, target.code_volume
        )
        self._require_candidate_files(request, target_files, target_source_files)
        await self._require_probe_binding(request, target_files, source)
        return observed_attestation

    @staticmethod
    def _requires_next_runtime(files: dict[str, str]) -> bool:
        try:
            package = json.loads(files.get("package.json", "{}"))
        except json.JSONDecodeError as exc:
            raise CellIdentityConflict("adaptation candidate package is invalid") from exc
        if not isinstance(package, dict):
            raise CellIdentityConflict("adaptation candidate package is invalid")
        scripts = package.get("scripts")
        dependencies = package.get("dependencies")
        return bool(
            isinstance(scripts, dict)
            and isinstance(scripts.get("start"), str)
            and scripts["start"].startswith("next start")
            and isinstance(dependencies, dict)
            and isinstance(dependencies.get("next"), str)
        )

    @staticmethod
    def _write_runnable_archive(
        candidate: Any,
        candidate_volume: str,
        sealed_source: Path,
        destination: Path,
    ) -> str:
        """Seal verified source plus bounded pre-built Next runtime artifacts."""

        captured = destination.with_suffix(".candidate")
        captured.unlink(missing_ok=True)
        total = 0
        try:
            with captured.open("xb") as handle:
                for chunk in candidate.export_volume(candidate_volume):
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise CellResourceError(
                            "adaptation candidate runtime archive is invalid"
                        )
                    total += len(chunk)
                    if total > _MAX_RUNNABLE_EXPORT_BYTES:
                        raise CellResourceError(
                            "adaptation candidate runtime archive exceeds byte limit"
                        )
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            seen: set[str] = set()
            runtime_files = 0
            runtime_bytes = 0
            found_build = False
            found_next = False
            with (
                tarfile.open(sealed_source, mode="r:*") as source,
                tarfile.open(captured, mode="r:*") as runtime,
                tarfile.open(
                    destination,
                    mode="x",
                    format=tarfile.PAX_FORMAT,
                ) as output,
            ):
                for member in source:
                    name = DockerRestorationAdaptationActivationEffects._archive_name(
                        member.name
                    )
                    if not member.isfile() or name in seen:
                        raise CellIdentityConflict(
                            "adaptation sealed source archive is invalid"
                        )
                    DockerRestorationAdaptationActivationEffects._copy_tar_member(
                        source,
                        output,
                        member,
                        name,
                    )
                    seen.add(name)

                for member in runtime:
                    raw_name = member.name.replace("\\", "/").strip("/")
                    if member.isdir() and raw_name in {"", "."}:
                        continue
                    name = DockerRestorationAdaptationActivationEffects._archive_name(
                        member.name
                    )
                    parts = PurePosixPath(name).parts
                    if (
                        not parts
                        or parts[0] not in _RUNTIME_ROOTS
                        or parts[:2] == (".next", "cache")
                    ):
                        continue
                    if name in seen:
                        raise CellIdentityConflict(
                            "adaptation runnable archive path is duplicated"
                        )
                    if not (
                        member.isfile()
                        or member.isdir()
                        or member.issym()
                        or member.islnk()
                    ):
                        raise CellIdentityConflict(
                            "adaptation runnable archive entry is unsafe"
                        )
                    runtime_files += 1
                    if runtime_files > _MAX_RUNNABLE_FILES:
                        raise CellResourceError(
                            "adaptation runnable archive exceeds file limit"
                        )
                    if member.isfile():
                        if member.size > _MAX_RUNNABLE_FILE_BYTES:
                            raise CellResourceError(
                                "adaptation runnable file exceeds byte limit"
                            )
                        runtime_bytes += member.size
                        if runtime_bytes > _MAX_RUNNABLE_EXPORT_BYTES:
                            raise CellResourceError(
                                "adaptation runnable files exceed byte limit"
                            )
                    if member.issym() or member.islnk():
                        DockerRestorationAdaptationActivationEffects._require_safe_link(
                            name,
                            member.linkname,
                            hardlink=member.islnk(),
                        )
                    DockerRestorationAdaptationActivationEffects._copy_tar_member(
                        runtime,
                        output,
                        member,
                        name,
                    )
                    seen.add(name)
                    found_build = found_build or name == ".next/BUILD_ID"
                    found_next = found_next or name == "node_modules/next" or name.startswith(
                        "node_modules/next/"
                    )
            if not found_build or not found_next:
                raise CellResourceError(
                    "adaptation candidate has no verified runnable Next artifacts"
                )
            if destination.stat().st_size > _MAX_RUNNABLE_EXPORT_BYTES:
                raise CellResourceError("adaptation runnable archive exceeds byte limit")
            with destination.open("r+b") as handle:
                os.fsync(handle.fileno())
            DockerRestorationAdaptationActivationEffects._fsync_directory(
                destination.parent
            )
            return DockerRestorationAdaptationActivationEffects._runnable_archive_digest(
                destination
            )
        finally:
            captured.unlink(missing_ok=True)

    @staticmethod
    def _runnable_archive_digest(path: Path) -> str:
        try:
            with tarfile.open(path, mode="r:*") as archive:
                return DockerRestorationAdaptationActivationEffects._runnable_tar_digest(
                    archive
                )
        except tarfile.TarError as exc:
            raise CellIdentityConflict("adaptation runnable archive is invalid") from exc

    @staticmethod
    def _target_volume_attestation(backend: Any, volume: str) -> str:
        total = 0
        with tempfile.TemporaryFile() as handle:
            for chunk in backend.export_volume(volume):
                if not isinstance(chunk, (bytes, bytearray)):
                    raise CellResourceError("adaptation target archive is invalid")
                total += len(chunk)
                if total > _MAX_RUNNABLE_EXPORT_BYTES:
                    raise CellResourceError(
                        "adaptation target archive exceeds byte limit"
                    )
                handle.write(chunk)
            handle.seek(0)
            try:
                with tarfile.open(fileobj=handle, mode="r:*") as archive:
                    return DockerRestorationAdaptationActivationEffects._runnable_tar_digest(
                        archive
                    )
            except tarfile.TarError as exc:
                raise CellIdentityConflict(
                    "adaptation target archive is invalid"
                ) from exc

    @staticmethod
    def _runnable_tar_digest(archive: tarfile.TarFile) -> str:
        entries: list[dict[str, object]] = []
        seen: set[str] = set()
        total = 0
        for member in archive:
            raw_name = member.name.replace("\\", "/").strip("/")
            if member.isdir() and raw_name in {"", "."}:
                continue
            name = DockerRestorationAdaptationActivationEffects._archive_name(
                member.name
            )
            if member.isdir():
                continue
            if name in seen:
                raise CellIdentityConflict(
                    "adaptation runnable archive path is duplicated"
                )
            seen.add(name)
            if len(seen) > _MAX_RUNNABLE_FILES:
                raise CellResourceError("adaptation runnable archive exceeds file limit")
            entry: dict[str, object] = {
                "path": name,
                "mode": member.mode & 0o777,
            }
            if member.isfile():
                if member.size > _MAX_RUNNABLE_FILE_BYTES:
                    raise CellResourceError(
                        "adaptation runnable file exceeds byte limit"
                    )
                total += member.size
                if total > _MAX_RUNNABLE_EXPORT_BYTES:
                    raise CellResourceError(
                        "adaptation runnable files exceed byte limit"
                    )
                payload = archive.extractfile(member)
                if payload is None:
                    raise CellIdentityConflict(
                        "adaptation runnable archive file is unavailable"
                    )
                digest = hashlib.sha256()
                remaining = member.size
                while remaining:
                    chunk = payload.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise CellIdentityConflict(
                            "adaptation runnable archive file is truncated"
                        )
                    remaining -= len(chunk)
                    digest.update(chunk)
                if payload.read(1):
                    raise CellIdentityConflict(
                        "adaptation runnable archive file exceeds declared size"
                    )
                entry.update(
                    type="file",
                    size=member.size,
                    sha256=digest.hexdigest(),
                )
            elif member.issym() or member.islnk():
                DockerRestorationAdaptationActivationEffects._require_safe_link(
                    name,
                    member.linkname,
                    hardlink=member.islnk(),
                )
                entry.update(
                    type="hardlink" if member.islnk() else "symlink",
                    target=member.linkname.replace("\\", "/"),
                )
            else:
                raise CellIdentityConflict(
                    "adaptation runnable archive entry is unsafe"
                )
            entries.append(entry)
        if not entries:
            raise CellIdentityConflict("adaptation runnable archive is empty")
        return canonical_digest(sorted(entries, key=lambda item: str(item["path"])))

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _archive_name(raw: str) -> str:
        if "\\" in raw:
            raise CellIdentityConflict("adaptation runnable archive path is unsafe")
        path = PurePosixPath(raw.replace("\\", "/"))
        name = path.as_posix().removeprefix("./").lstrip("/")
        parts = PurePosixPath(name).parts
        if (
            not name
            or name == "."
            or path.is_absolute()
            or ".." in parts
        ):
            raise CellIdentityConflict("adaptation runnable archive path is unsafe")
        return name

    @staticmethod
    def _require_safe_link(name: str, raw_target: str, *, hardlink: bool) -> None:
        if "\\" in raw_target:
            raise CellIdentityConflict("adaptation runnable archive link is unsafe")
        target = raw_target.replace("\\", "/")
        if not target or PurePosixPath(target).is_absolute():
            raise CellIdentityConflict("adaptation runnable archive link is unsafe")
        resolved = posixpath.normpath(
            target if hardlink else posixpath.join(posixpath.dirname(name), target)
        ).lstrip("/")
        parts = PurePosixPath(resolved).parts
        if (
            not resolved
            or resolved == "."
            or ".." in parts
            or not parts
            or parts[0] not in _RUNTIME_ROOTS
        ):
            raise CellIdentityConflict("adaptation runnable archive link is unsafe")

    @staticmethod
    def _copy_tar_member(
        source: tarfile.TarFile,
        output: tarfile.TarFile,
        member: tarfile.TarInfo,
        name: str,
    ) -> None:
        copied = tarfile.TarInfo(name)
        copied.mode = member.mode & 0o777
        copied.uid = copied.gid = 0
        copied.uname = copied.gname = ""
        copied.mtime = 0
        copied.type = member.type
        copied.linkname = member.linkname
        copied.size = member.size if member.isfile() else 0
        payload = source.extractfile(member) if member.isfile() else None
        if member.isfile() and payload is None:
            raise CellIdentityConflict("adaptation runnable archive file is unavailable")
        output.addfile(copied, payload)

    async def cleanup_candidate(
        self,
        request: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
    ) -> None:
        if self._file_digest(sealed_artifact) != request.candidate_code_digest:
            raise CellIdentityConflict("adaptation sealed code archive changed")
        source_manager = self.code_engine.activation_manager(request.source_workspace_id)
        manager = self.candidate_manager_factory(source_manager)
        state = manager.state_store.load(request.candidate_workspace_id)
        if state is None:
            return
        self._require_candidate_owner(request, state)
        release_id = self._cleanup_operation_id(request, "release")
        release_retry_id = self._cleanup_operation_id(request, "release-retry")
        if state.operation(release_retry_id) is not None:
            release_id = release_retry_id
        prior = state.operation(release_id)
        if prior is not None:
            self._require_cleanup_operation(
                prior,
                kind="release",
                request_digest=request.activation_digest,
                generation_run_id=request.generation_run_id,
            )
            if prior.status == "indeterminate":
                await self._reconcile_indeterminate_cleanup(
                    manager,
                    request,
                    operation=prior,
                    kind="release",
                )
                state = manager.state_store.load(request.candidate_workspace_id)
                if state is None:
                    return
                release_id = release_retry_id
                prior = state.operation(release_id)
        if state.active_generation_run_id is None and (
            prior is None or prior.status != "completed"
        ):
            raise CellIdentityConflict("adaptation candidate release ownership changed")
        if state.active_generation_run_id is not None or (
            prior is not None and prior.status != "completed"
        ):
            self._require_candidate_release_lease(request, state)
            mutation = LifecycleMutation(
                release_id,
                prior.fencing_epoch if prior is not None else state.fencing_epoch + 1,
                request.activation_digest,
            )
            await manager.release_generation(
                request.candidate_workspace_id,
                mutation,
                generation_run_id=request.generation_run_id,
            )
            state = manager.state_store.load(request.candidate_workspace_id)
            if state is None:
                return
            completed_release = state.operation(release_id)
            if (
                completed_release is None
                or completed_release.status != "completed"
                or state.active_generation_run_id is not None
            ):
                raise CellResourceError("adaptation candidate release was not confirmed")
        current = manager.state_store.load(request.candidate_workspace_id)
        if current is None:
            return
        destroy_id = self._cleanup_operation_id(request, "destroy")
        destroy_retry_id = self._cleanup_operation_id(request, "destroy-retry")
        if current.operation(destroy_retry_id) is not None:
            destroy_id = destroy_retry_id
        prior_destroy = current.operation(destroy_id)
        if prior_destroy is not None:
            self._require_cleanup_operation(
                prior_destroy,
                kind="destroy",
                request_digest=request.activation_digest,
                generation_run_id=None,
            )
            if prior_destroy.status == "indeterminate":
                await self._reconcile_indeterminate_cleanup(
                    manager,
                    request,
                    operation=prior_destroy,
                    kind="destroy",
                )
                destroy_id = destroy_retry_id
        async with manager.operation_lock.hold(request.candidate_workspace_id):
            current = manager.state_store.load(request.candidate_workspace_id)
            if current is None:
                return
            if current.active_generation_run_id is not None:
                raise CellIdentityConflict("adaptation candidate did not stop")
            prior = current.operation(destroy_id)
            if prior is not None:
                self._require_cleanup_operation(
                    prior,
                    kind="destroy",
                    request_digest=request.activation_digest,
                    generation_run_id=None,
                )
            mutation = LifecycleMutation(
                destroy_id,
                prior.fencing_epoch if prior is not None else current.fencing_epoch + 1,
                request.activation_digest,
            )
            await manager.destroy_compute_without_lock(
                request.candidate_workspace_id,
                mutation,
                checkpoint_ref=None,
                record_operation=True,
                capture=False,
            )
            current = manager.state_store.load(request.candidate_workspace_id)
            completed_destroy = current.operation(destroy_id) if current is not None else None
            if completed_destroy is None or completed_destroy.status != "completed":
                raise CellResourceError("adaptation candidate destroy was not confirmed")
            volumes = await manager.docker.list_workspace_volumes(
                request.candidate_workspace_id
            )
            protected = {
                request.source_code_volume.casefold(),
                request.live_database_volume.casefold(),
                self._target_code_volume(request).casefold(),
            }
            for volume in volumes:
                labels = volume.labels
                if (
                    volume.name.casefold() in protected
                    or
                    labels.get("omnia.workspace_id") != str(request.candidate_workspace_id)
                    or labels.get("omnia.project_id") != str(request.project_id)
                    or labels.get("omnia.owner_id") != str(request.owner_id)
                ):
                    raise CellIdentityConflict("adaptation candidate volume identity changed")
            for volume in volumes:
                await manager.docker.remove_volume(volume.name)

    async def _reconcile_indeterminate_cleanup(
        self,
        manager: Any,
        request: RestorationAdaptationActivationRequest,
        *,
        operation: Any,
        kind: str,
    ) -> None:
        reconcile_id = self._cleanup_operation_id(request, f"{kind}-reconcile")
        state = manager.state_store.load(request.candidate_workspace_id)
        if state is None:
            return
        prior = state.operation(reconcile_id)
        if prior is not None:
            self._require_cleanup_operation(
                prior,
                kind="reconcile",
                request_digest=request.activation_digest,
                generation_run_id=(
                    request.generation_run_id if kind == "release" else None
                ),
            )
            if prior.status == "completed":
                return
            raise CellResourceError("adaptation candidate cleanup reconcile is incomplete")
        if (
            operation.status != "indeterminate"
            or state.phase != "indeterminate"
            or state.last_operation_id != operation.operation_id
            or state.fencing_epoch != operation.fencing_epoch
        ):
            raise CellIdentityConflict("adaptation candidate cleanup ownership changed")
        mutation = LifecycleMutation(
            reconcile_id,
            state.fencing_epoch + 1,
            request.activation_digest,
        )
        await manager.reconcile(request.candidate_workspace_id, mutation)
        reconciled = manager.state_store.load(request.candidate_workspace_id)
        completed = reconciled.operation(reconcile_id) if reconciled is not None else None
        if completed is None or completed.status != "completed":
            raise CellResourceError("adaptation candidate cleanup reconcile was not confirmed")

    async def stop_source_writers(
        self, request: RestorationAdaptationActivationRequest
    ) -> None:
        manager, state, machine, backend, _files, _observed = await self._source(
            request, require_running=False
        )
        saved = machine.state()
        epoch = saved.get("epoch")
        if type(epoch) is not int or epoch != request.expected_source_fencing_epoch:
            raise CellIdentityConflict("adaptation source machine fence changed")
        await self.code_engine.activation_stop_source_application(
            manager,
            state,
            code_volume=request.source_code_volume,
            database_volume=request.live_database_volume,
            epoch=epoch,
        )
        if (
            self._require_database_volume(request, backend)
            != request.live_database_identity_digest
        ):
            raise CellIdentityConflict("adaptation live database identity changed")

    async def start_target_writers(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> None:
        self._require_target(request, target)
        manager, state, _machine, backend = await self._transition(request)
        if (
            self._require_database_volume(request, backend)
            != request.live_database_identity_digest
        ):
            raise CellIdentityConflict("adaptation live database identity changed")
        self._require_target_volume(request, backend, target.code_volume)
        await self.code_engine.activation_start_code_only_target(
            manager,
            state,
            code_volume=target.code_volume,
            database_volume=request.live_database_volume,
            epoch=request.target_fencing_epoch,
        )
        await self._target(request)

    async def start_source_writers(
        self, request: RestorationAdaptationActivationRequest
    ) -> None:
        manager, state, _machine, _backend, _files, _observed = await self._source(
            request, require_running=False
        )
        await self.code_engine.activation_restart_source(
            manager,
            state,
            code_volume=request.source_code_volume,
            database_volume=request.live_database_volume,
        )

    async def verify_source_health(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        _manager, _state, _machine, backend, files, observed = await self._source(
            request, require_running=True
        )
        if observed is None:
            raise CellIdentityConflict("adaptation source running identity is unavailable")
        return canonical_digest(
            {
                "workspace_revision": _workspace_revision(files),
                "database_identity_digest": self._require_database_volume(request, backend),
                "serving_route_digest": observed.get("serving_route_digest"),
                "source_code_volume": request.source_code_volume,
                "database_volume": request.live_database_volume,
            }
        )

    async def delete_target_code(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> None:
        self._require_target(request, target)
        _manager, _state, _machine, source, _files, _observed = await self._source(
            request, require_running=True
        )
        volume = self._require_target_volume(
            request, source, target.code_volume, allow_missing=True
        )
        if volume is None:
            return
        source._require_restoration_volume_detached(target.code_volume, purpose="code")
        await machine_effect(volume.remove)
        if source._lookup(source.client.volumes, target.code_volume, "project-volume") is not None:
            raise CellResourceError("adaptation target code removal was not confirmed")

    async def verify_service_readiness(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        target = await self._target(request)
        return await self.health_prober.verify_service_readiness(request, target)

    async def verify_signed_owner_read(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        target = await self._target(request)
        return await self.health_prober.verify_signed_owner_read(request, target)

    async def verify_signed_owner_write(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        target = await self._target(request)
        return await self.health_prober.verify_signed_owner_create_update_delete(
            request, target
        )

    async def verify_signed_owner_reload(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        target = await self._target(request)
        return await self.health_prober.verify_signed_owner_reload(request, target)

    async def verify_cross_owner_denial(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        target = await self._target(request)
        return await self.health_prober.verify_cross_owner_denial(request, target)

    async def verify_unauthenticated_denial(
        self, request: RestorationAdaptationActivationRequest
    ) -> str:
        target = await self._target(request)
        return await self.health_prober.verify_unauthenticated_denial(request, target)

    async def _source(
        self,
        request: RestorationAdaptationActivationRequest,
        *,
        require_running: bool,
    ) -> tuple[Any, Any, Any, Any, dict[str, str], dict[str, Any] | None]:
        manager = self.code_engine.activation_manager(request.source_workspace_id)
        state = manager.state_store.load(request.source_workspace_id)
        self._require_source_state(request, state)
        if manager.machine_runtime is None:
            raise CellIdentityConflict("adaptation source runtime is unavailable")
        machine, backend = manager.machine_runtime.parts(state)
        if backend.workspace_volume != request.source_code_volume:
            raise CellIdentityConflict("adaptation source code volume changed")
        self._require_state_code_volume(state, backend)
        database_identity = self._require_database_volume(request, backend)
        if database_identity != request.live_database_identity_digest:
            raise CellIdentityConflict("adaptation live database identity changed")
        files = await self._workspace_files(manager, backend.workspace_volume)
        if _workspace_revision(files) != request.source_workspace_revision:
            raise CellIdentityConflict("adaptation source workspace revision changed")
        observed = None
        if require_running:
            await self._require_running_pair(manager, state, machine, backend)
            observed, _contract = await self._observe_running(
                manager, state, machine, backend, files
            )
        return manager, state, machine, backend, files, observed

    async def _candidate(
        self, request: RestorationAdaptationActivationRequest
    ) -> tuple[Any, Any, Any, Any, dict[str, str]]:
        source_manager = self.code_engine.activation_manager(request.source_workspace_id)
        manager = self.candidate_manager_factory(source_manager)
        state = manager.state_store.load(request.candidate_workspace_id)
        self._require_candidate_state(request, state)
        if manager.machine_runtime is None:
            raise CellIdentityConflict("adaptation candidate runtime is unavailable")
        machine, backend = manager.machine_runtime.parts(state)
        self._require_state_code_volume(state, backend)
        candidate_volumes = {
            backend.workspace_volume.casefold(),
            backend.project_postgres_volume.casefold(),
        }
        protected_volumes = {
            request.source_code_volume.casefold(),
            request.live_database_volume.casefold(),
        }
        if len(candidate_volumes) != 2 or candidate_volumes & protected_volumes:
            raise CellIdentityConflict("adaptation candidate volume aliases source")
        for name in (backend.workspace_volume, backend.project_postgres_volume):
            volume = backend._lookup(
                backend.client.volumes,
                name,
                "project-volume",
            )
            if volume is None or (volume.attrs or {}).get("Name") != name:
                raise CellIdentityConflict("adaptation candidate volume is unavailable")
        files = await self._workspace_files(manager, backend.workspace_volume)
        source_files = await self._workspace_source_files(manager, backend.workspace_volume)
        self._require_candidate_files(request, files, source_files)
        return manager, state, machine, backend, files

    async def _transition(
        self, request: RestorationAdaptationActivationRequest
    ) -> tuple[Any, Any, Any, Any]:
        manager = self.code_engine.activation_manager(request.source_workspace_id)
        state = manager.state_store.load(request.source_workspace_id)
        if state is None or state.workspace_id != request.source_workspace_id:
            raise CellIdentityConflict("adaptation source state is unavailable")
        if state.project_id != request.project_id or state.owner_id != request.owner_id:
            raise CellIdentityConflict("adaptation source owner identity changed")
        if state.fencing_epoch not in {
            request.expected_source_fencing_epoch,
            request.target_fencing_epoch,
        }:
            raise CellIdentityConflict("adaptation source fence changed")
        if state.active_generation_run_id != request.generation_run_id:
            raise CellIdentityConflict("adaptation source generation ownership changed")
        if state.fencing_epoch == request.expected_source_fencing_epoch:
            if state.active_generation_fencing_epoch != request.expected_source_fencing_epoch:
                raise CellIdentityConflict("adaptation source generation fence changed")
        else:
            self.code_engine.activation_assert_target_controller(
                manager,
                state,
                epoch=request.target_fencing_epoch,
                generation_run_id=request.generation_run_id,
                allow_running=True,
            )
        if manager.machine_runtime is None:
            raise CellIdentityConflict("adaptation source runtime is unavailable")
        machine, backend = manager.machine_runtime.parts(state)
        return manager, state, machine, backend

    async def _target(
        self, request: RestorationAdaptationActivationRequest
    ) -> ActivationHealthTarget:
        manager, state, machine, backend = await self._transition(request)
        if state.fencing_epoch != request.target_fencing_epoch:
            raise CellIdentityConflict("adaptation target fence is not active")
        self.code_engine.activation_assert_target_controller(
            manager,
            state,
            epoch=request.target_fencing_epoch,
            generation_run_id=request.generation_run_id,
        )
        target_volume = self._target_code_volume(request)
        if backend.workspace_volume != target_volume:
            raise CellIdentityConflict("adaptation target code volume changed")
        if (
            self._require_database_volume(request, backend)
            != request.live_database_identity_digest
        ):
            raise CellIdentityConflict("adaptation target database identity changed")
        self._require_target_volume(request, backend, target_volume)
        files = await self._workspace_files(manager, target_volume)
        source_files = await self._workspace_source_files(manager, target_volume)
        self._require_candidate_files(request, files, source_files)
        saved = machine.state()
        epoch = saved.get("epoch")
        manifest = saved.get("manifest")
        if type(epoch) is not int or epoch != request.target_fencing_epoch:
            raise CellIdentityConflict("adaptation target machine fence changed")
        if not isinstance(manifest, dict) or not await self.code_engine.activation_running_matches(
            manager,
            state,
            backend,
            code_volume=target_volume,
            database_volume=request.live_database_volume,
            epoch=epoch,
            manifest=manifest,
        ):
            raise CellResourceError("adaptation target pair is not running")
        _observed, contract = await self._observe_running(
            manager, state, machine, backend, files
        )
        validated_probe = validate_probe_contract(files, contract)
        if validated_probe != request.business_probe:
            raise CellIdentityConflict("adaptation business probe binding changed")
        return ActivationHealthTarget(
            workspace_id=request.source_workspace_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            fencing_epoch=request.target_fencing_epoch,
            code_volume=target_volume,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
            business_probe=request.business_probe,
        )

    async def _observe_running(
        self,
        manager: Any,
        state: Any,
        machine: Any,
        backend: Any,
        files: dict[str, str],
    ) -> tuple[dict[str, Any], Any]:
        contract, blockers = await machine_effect(catalog_contract, backend)
        if blockers:
            raise CellIdentityConflict("adaptation live database contract is unavailable")
        schema = contract.model_dump(mode="json") if hasattr(contract, "model_dump") else contract
        observed = await machine_effect(
            observe_live_source,
            backend,
            machine,
            state,
            source_files={path: content.encode("utf-8") for path, content in files.items()},
            schema=schema,
        )
        if not isinstance(observed, dict):
            raise CellIdentityConflict("adaptation running identity is unavailable")
        return observed, contract

    async def _require_probe_binding(
        self,
        request: RestorationAdaptationActivationRequest,
        files: dict[str, str],
        backend: Any,
    ) -> None:
        contract, blockers = await machine_effect(catalog_contract, backend)
        if blockers:
            raise CellIdentityConflict("adaptation live database contract is unavailable")
        if validate_probe_contract(files, contract) != request.business_probe:
            raise CellIdentityConflict("adaptation business probe binding changed")

    async def _require_running_pair(
        self,
        manager: Any,
        state: Any,
        machine: Any,
        backend: Any,
    ) -> None:
        saved = machine.state()
        epoch = saved.get("epoch")
        manifest = saved.get("manifest")
        if (
            type(epoch) is not int
            or epoch <= 0
            or not isinstance(manifest, dict)
            or not await self.code_engine.activation_running_matches(
                manager,
                state,
                backend,
                code_volume=backend.workspace_volume,
                database_volume=backend.project_postgres_volume,
                epoch=epoch,
                manifest=manifest,
            )
        ):
            raise CellResourceError("adaptation source pair is not running")

    @staticmethod
    async def _workspace_files(manager: Any, volume: str) -> dict[str, str]:
        files = await _read_agent_workspace_files(manager, volume)
        source_paths = canonical_source_files(
            await manager.docker.read_workspace_source_files(volume)
        )
        files = {path: content for path, content in files.items() if path in source_paths}
        if not isinstance(files, dict) or any(
            not isinstance(path, str) or not isinstance(content, str)
            for path, content in files.items()
        ):
            raise CellIdentityConflict("adaptation workspace files are unavailable")
        return files

    @staticmethod
    async def _workspace_source_files(manager: Any, volume: str) -> dict[str, bytes]:
        return canonical_source_files(
            await manager.docker.read_workspace_source_files(volume)
        )

    @staticmethod
    def _require_source_state(
        request: RestorationAdaptationActivationRequest, state: Any
    ) -> None:
        if (
            state is None
            or state.workspace_id != request.source_workspace_id
            or state.project_id != request.project_id
            or state.owner_id != request.owner_id
            or state.fencing_epoch != request.expected_source_fencing_epoch
            or state.active_generation_run_id != request.generation_run_id
            or state.active_generation_fencing_epoch != request.expected_source_fencing_epoch
        ):
            raise CellIdentityConflict("adaptation source identity or fence changed")

    @staticmethod
    def _require_candidate_state(
        request: RestorationAdaptationActivationRequest, state: Any
    ) -> None:
        DockerRestorationAdaptationActivationEffects._require_candidate_owner(request, state)
        if (
            state.fencing_epoch != request.candidate_fencing_epoch
            or state.active_generation_run_id != request.generation_run_id
            or state.active_generation_fencing_epoch != request.candidate_fencing_epoch
        ):
            raise CellIdentityConflict("adaptation candidate identity or fence changed")

    @staticmethod
    def _require_candidate_owner(
        request: RestorationAdaptationActivationRequest, state: Any
    ) -> None:
        if (
            state is None
            or state.workspace_id != request.candidate_workspace_id
            or state.project_id != request.project_id
            or state.owner_id != request.owner_id
        ):
            raise CellIdentityConflict("adaptation candidate owner identity changed")

    @classmethod
    def _require_candidate_release_lease(
        cls,
        request: RestorationAdaptationActivationRequest,
        state: Any,
    ) -> None:
        if state.active_generation_run_id != request.generation_run_id:
            raise CellIdentityConflict("adaptation candidate lease changed")
        if state.active_generation_fencing_epoch == request.candidate_fencing_epoch:
            return
        reconcile = state.operation(cls._cleanup_operation_id(request, "release-reconcile"))
        if reconcile is not None:
            cls._require_cleanup_operation(
                reconcile,
                kind="reconcile",
                request_digest=request.activation_digest,
                generation_run_id=request.generation_run_id,
            )
        if (
            reconcile is None
            or reconcile.status != "completed"
            or reconcile.fencing_epoch != state.active_generation_fencing_epoch
        ):
            raise CellIdentityConflict("adaptation candidate lease changed")

    @staticmethod
    def _require_cleanup_operation(
        operation: Any,
        *,
        kind: str,
        request_digest: str,
        generation_run_id: UUID | None,
    ) -> None:
        if (
            getattr(operation, "kind", None) != kind
            or getattr(operation, "request_digest", None) != request_digest
            or getattr(operation, "status", None)
            not in {"running", "completed", "indeterminate"}
            or type(getattr(operation, "fencing_epoch", None)) is not int
            or getattr(operation, "generation_run_id", None) != generation_run_id
            or getattr(operation, "checkpoint_ref", None) is not None
        ):
            raise CellIdentityConflict("adaptation candidate cleanup operation changed")

    @staticmethod
    def _cleanup_operation_id(
        request: RestorationAdaptationActivationRequest,
        kind: str,
    ) -> UUID:
        return uuid5(request.activation_id, f"adaptation-candidate-{kind}")

    @staticmethod
    def _require_database_volume(
        request: RestorationAdaptationActivationRequest, backend: Any
    ) -> str:
        return live_database_volume_identity_digest(
            backend,
            expected_volume=request.live_database_volume,
        )

    @staticmethod
    def _require_state_code_volume(state: Any, backend: Any) -> None:
        resources = getattr(state, "resource_names", None)
        expected = getattr(resources, "workspace_volume", None)
        active = (
            backend._metadata().get("active_code_volume")
            if hasattr(backend, "_metadata")
            else None
        )
        if expected == backend.workspace_volume or active == backend.workspace_volume:
            return
        raise CellIdentityConflict("adaptation controller code volume identity changed")

    def _require_target_volume(
        self,
        request: RestorationAdaptationActivationRequest,
        backend: Any,
        name: str,
        *,
        allow_missing: bool = False,
    ) -> Any | None:
        labels = backend.restoration_volume_labels(
            request.operation_id,
            purpose="code",
            binding_digest=request.activation_digest,
            artifact_digest=request.candidate_code_digest,
        )
        volume = backend._lookup(backend.client.volumes, name, "project-volume")
        if volume is None:
            if allow_missing:
                return None
            raise CellIdentityConflict("adaptation target code volume is missing")
        self._require_volume_labels(backend, name, labels)
        attrs = volume.attrs or {}
        observed_labels = attrs.get("Labels") or {}
        runnable_digest = observed_labels.get("omnia.restoration_runnable_digest")
        if (
            observed_labels.get("omnia.restoration_runnable_source_digest")
            != request.candidate_code_digest
            or not isinstance(runnable_digest, str)
            or len(runnable_digest) != 64
            or any(char not in "0123456789abcdef" for char in runnable_digest)
        ):
            raise CellIdentityConflict("adaptation target runnable identity changed")
        return volume

    @staticmethod
    def _require_volume_labels(backend: Any, name: str, expected: dict[str, str]) -> Any:
        volume = backend._lookup(backend.client.volumes, name, "project-volume")
        attrs = volume.attrs if volume is not None else {}
        labels = attrs.get("Labels") or {}
        if volume is None or attrs.get("Name") != name or any(
            labels.get(key) != value for key, value in expected.items()
        ):
            raise CellIdentityConflict("adaptation target code volume identity changed")
        return volume

    @staticmethod
    def _require_target(
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> None:
        expected = ActivationPreparedTarget(
            workspace_id=request.source_workspace_id,
            fencing_epoch=request.target_fencing_epoch,
            code_volume=DockerRestorationAdaptationActivationEffects._target_code_volume(request),
            code_digest=request.candidate_code_digest,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
        )
        if target != expected:
            raise CellIdentityConflict("adaptation target identity changed")

    def _require_candidate_files(
        self,
        request: RestorationAdaptationActivationRequest,
        files: dict[str, str],
        source_files: dict[str, bytes],
    ) -> None:
        if (
            _workspace_revision(files) != request.candidate_workspace_revision
            or self._candidate_artifact_digest(files) != request.candidate_artifact_digest
            or source_manifest_digest(source_files)
            != request.candidate_source_manifest_digest
        ):
            raise CellIdentityConflict("adaptation candidate code identity changed")

    @staticmethod
    def _candidate_artifact_digest(files: dict[str, str]) -> str:
        digest = hashlib.sha256()
        for path, content in sorted(files.items()):
            path_bytes = path.encode("utf-8")
            content_bytes = content.encode("utf-8")
            digest.update(len(path_bytes).to_bytes(8, "big"))
            digest.update(path_bytes)
            digest.update(len(content_bytes).to_bytes(8, "big"))
            digest.update(content_bytes)
        return digest.hexdigest()

    @staticmethod
    def _target_code_volume(request: RestorationAdaptationActivationRequest) -> str:
        return (
            f"omnia-machine-{request.source_workspace_id.hex}"
            f"-code-{request.activation_id.hex}"
        )

    @staticmethod
    def _file_digest(path: Path) -> str:
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict("adaptation sealed code archive is unavailable")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
