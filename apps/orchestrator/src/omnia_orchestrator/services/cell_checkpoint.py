"""Private checkpoint creation and restore for Project Cell resources."""

from __future__ import annotations

import hashlib
import io
import tarfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellRestoreFailed,
    LifecycleMutation,
    identity_labels,
    validate_checkpoint_ref,
)
from omnia_orchestrator.services.cell_state import CellCredentialStore, CellStateStore
from omnia_orchestrator.services.docker_cell_resources import (
    CellDockerBackend,
    DockerContainerSpec,
)


class CheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: UUID
    project_id: UUID
    profile_version: str
    fencing_epoch: int
    checkpoint_ref: str
    created_at: datetime
    artifacts: dict[str, str]
    postgres_image: str
    redis_policy: str = "clear_on_restore"


class CellCheckpointManager:
    def __init__(
        self,
        *,
        profile_version: str,
        postgres_image: str,
        docker: CellDockerBackend,
        credential_store: CellCredentialStore,
        state_store: CellStateStore,
    ) -> None:
        self.profile_version = profile_version
        self.postgres_image = postgres_image
        self.docker = docker
        self.credential_store = credential_store
        self.state_store = state_store
        self.machine_runtime: Any | None = None

    async def create(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
        *,
        record_operation: bool = True,
    ) -> CheckpointManifest:
        validate_checkpoint_ref(checkpoint_ref)
        state = self._require_state(workspace_id)
        names = self._require_names(state)
        if record_operation:
            replay = await self._replay_completed_manifest_or_reject(
                workspace_id=workspace_id,
                state=state,
                names=names,
                mutation=mutation,
                kind="checkpoint",
                checkpoint_ref=checkpoint_ref,
            )
            if replay is not None:
                return replay
            self._begin_if_missing(
                state,
                mutation,
                kind="checkpoint",
                checkpoint_ref=checkpoint_ref,
            )
        artifacts = await self._capture_checkpoint_artifacts(
            workspace_id=workspace_id,
            names=names,
            postgres_container_name=names.postgres_container,
        )
        manifest = self._build_manifest(
            workspace_id=workspace_id,
            state=state,
            checkpoint_ref=checkpoint_ref,
            mutation=mutation,
            artifacts=artifacts,
        )
        await self._store_checkpoint_artifacts(
            checkpoint_volume=names.checkpoint_volume,
            checkpoint_ref=checkpoint_ref,
            mutation=mutation,
            manifest=manifest,
            artifacts=artifacts,
        )
        if record_operation:
            self.state_store.advance(
                workspace_id,
                mutation,
                phase="checkpoint_sealed",
                observed_resources={"checkpoint_ref": checkpoint_ref},
                bundle_state=state.bundle_state,
            )
            self.state_store.complete(
                workspace_id,
                mutation,
                phase="completed",
                provider_ref=state.provider_ref,
                bundle_state=state.bundle_state,
            )
        return manifest

    async def restore(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
        *,
        require_paused_state: bool = True,
    ) -> CheckpointManifest:
        validate_checkpoint_ref(checkpoint_ref)
        state = self._require_state(workspace_id)
        names = self._require_names(state)
        replay = await self._replay_completed_manifest_or_reject(
            workspace_id=workspace_id,
            state=state,
            names=names,
            mutation=mutation,
            kind="restore",
            checkpoint_ref=checkpoint_ref,
        )
        if replay is not None:
            return replay
        manifest = await self._load_manifest(names.checkpoint_volume, checkpoint_ref)
        self._validate_restore_manifest(
            workspace_id=workspace_id,
            state=state,
            checkpoint_ref=checkpoint_ref,
            manifest=manifest,
        )
        await self._require_restore_paused(
            state,
            names,
            require_paused_state=require_paused_state,
        )
        artifacts = await self._load_artifacts(
            names.checkpoint_volume, checkpoint_ref, manifest
        )
        if self.machine_runtime is not None:
            validate = getattr(self.machine_runtime, "validate_restore_payload", None)
            if validate is not None:
                await validate(state, artifacts.get("machine.json"))
        elif "machine.json" in artifacts:
            raise CellRestoreFailed("portable environment provider is unavailable")
        self._begin_if_missing(
            state,
            mutation,
            kind="restore",
            checkpoint_ref=checkpoint_ref,
        )

        pre_restore_ref = f"pre-restore-{mutation.operation_id}"
        helper_name = names.helper_container_name("postgres-maintenance", mutation.operation_id)
        pre_restore_artifacts: dict[str, bytes] | None = None
        live_restore_started = False
        try:
            await self._start_maintenance_postgres(
                state=state,
                names=names,
                helper_name=helper_name,
            )
            recovery = self.machine_runtime is not None and getattr(
                self.machine_runtime, "recovery_required", lambda state: False
            )(state)
            if recovery:
                # The failed current state cannot honestly be sealed as a rollback
                # checkpoint. An explicit restore may replace it with the already
                # identity/hash-verified requested envelope. Failed recovery stays
                # fenced/degraded; this never certifies the failed source/data.
                pre_restore_artifacts = dict(artifacts)
            else:
                pre_restore_artifacts = await self._capture_checkpoint_artifacts(
                    workspace_id=workspace_id,
                    names=names,
                    postgres_container_name=helper_name,
                )
            pre_restore = self._build_manifest(
                workspace_id=workspace_id,
                state=state,
                checkpoint_ref=pre_restore_ref,
                mutation=mutation,
                artifacts=pre_restore_artifacts,
            )
            await self._store_checkpoint_artifacts(
                checkpoint_volume=names.checkpoint_volume,
                checkpoint_ref=pre_restore_ref,
                mutation=mutation,
                manifest=pre_restore,
                artifacts=pre_restore_artifacts,
            )
            live_restore_started = True
            await self._apply_restore(
                workspace_id=workspace_id,
                names=names,
                postgres_container_name=helper_name,
                artifacts=artifacts,
            )
            self.state_store.complete(
                workspace_id,
                mutation,
                phase="completed",
                provider_ref=state.provider_ref,
                bundle_state="resources_paused",
            )
            return manifest
        except Exception as exc:
            if live_restore_started is False or pre_restore_artifacts is None:
                self.state_store.mark_failed(
                    workspace_id,
                    mutation,
                    phase="failed",
                    provider_ref=state.provider_ref,
                    bundle_state="resources_paused",
                    detail=str(exc),
                )
                raise CellRestoreFailed(str(exc)) from exc
            try:
                await self._apply_restore(
                    workspace_id=workspace_id,
                    names=names,
                    postgres_container_name=helper_name,
                    artifacts=pre_restore_artifacts,
                )
                self.state_store.mark_failed(
                    workspace_id,
                    mutation,
                    phase="failed",
                    provider_ref=state.provider_ref,
                    bundle_state="resources_paused",
                    detail=str(exc),
                )
            except Exception as rollback_exc:
                self.state_store.mark_degraded(
                    workspace_id,
                    mutation,
                    kind="restore",
                    detail=str(rollback_exc),
                )
                raise CellRestoreFailed(str(rollback_exc)) from rollback_exc
            raise CellRestoreFailed(str(exc)) from exc
        finally:
            await self._remove_container_if_present(helper_name)

    def _require_state(self, workspace_id: UUID) -> Any:
        state = self.state_store.load(workspace_id)
        if state is None or state.project_id is None:
            raise CellRestoreFailed("workspace state missing")
        return state

    @staticmethod
    def _require_names(state: Any) -> Any:
        if state.resource_names is None:
            raise CellRestoreFailed("resource names missing")
        return state.resource_names

    async def _load_manifest(
        self, checkpoint_volume: str, checkpoint_ref: str
    ) -> CheckpointManifest:
        files = await self.docker.read_volume_files(checkpoint_volume)
        payload = files.get(f"{checkpoint_ref}/manifest.json")
        if payload is None:
            raise CellRestoreFailed("checkpoint manifest missing")
        return CheckpointManifest.model_validate_json(payload)

    async def _load_artifacts(
        self,
        checkpoint_volume: str,
        checkpoint_ref: str,
        manifest: CheckpointManifest,
    ) -> dict[str, bytes]:
        files = await self.docker.read_volume_files(checkpoint_volume)
        artifacts: dict[str, bytes] = {}
        for filename, digest in manifest.artifacts.items():
            payload = files.get(f"{checkpoint_ref}/{filename}")
            if payload is None:
                raise CellRestoreFailed(f"checkpoint artifact missing: {filename}")
            if _sha256_hex(payload) != digest:
                raise CellRestoreFailed(f"checkpoint artifact hash mismatch: {filename}")
            artifacts[filename] = payload
        return artifacts

    async def _postgres_password(self, workspace_id: UUID) -> str:
        return self.credential_store.load_or_create(workspace_id).postgres_password

    async def _capture_checkpoint_artifacts(
        self,
        *,
        workspace_id: UUID,
        names: Any,
        postgres_container_name: str,
    ) -> dict[str, bytes]:
        machine_payload = None
        if self.machine_runtime is not None:
            machine_payload = await self.machine_runtime.checkpoint_payload(
                self._require_state(workspace_id)
            )
        workspace_tar = _archive_bytes(await self.docker.read_volume_files(names.workspace_volume))
        agent_home_tar = _archive_bytes(
            await self.docker.read_volume_files(names.agent_home_volume)
        )
        postgres_dump = await self.docker.postgres_dump(
            postgres_container_name,
            await self._postgres_password(workspace_id),
        )
        artifacts = {
            "workspace.tar": workspace_tar,
            "agent-home.tar": agent_home_tar,
            "postgres.dump": postgres_dump,
        }
        if machine_payload is not None:
            artifacts["machine.json"] = machine_payload
        return artifacts

    def _build_manifest(
        self,
        *,
        workspace_id: UUID,
        state: Any,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
        artifacts: dict[str, bytes],
    ) -> CheckpointManifest:
        return CheckpointManifest(
            workspace_id=workspace_id,
            project_id=state.project_id,
            profile_version=state.profile_version or self.profile_version,
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=checkpoint_ref,
            created_at=datetime.now(UTC),
            artifacts={
                filename: _sha256_hex(payload) for filename, payload in artifacts.items()
            },
            postgres_image=self.postgres_image,
        )

    async def _store_checkpoint_artifacts(
        self,
        *,
        checkpoint_volume: str,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
        manifest: CheckpointManifest,
        artifacts: dict[str, bytes],
    ) -> None:
        stage = f"{checkpoint_ref}.tmp-{mutation.operation_id}"
        payloads = {
            f"{stage}/{filename}": payload for filename, payload in artifacts.items()
        }
        payloads[f"{stage}/manifest.json"] = manifest.model_dump_json().encode("utf-8")
        await self.docker.write_volume_files(checkpoint_volume, payloads)
        await self.docker.promote_volume_directory(
            checkpoint_volume,
            stage,
            checkpoint_ref,
        )

    async def _require_restore_paused(
        self,
        state: Any,
        names: Any,
        *,
        require_paused_state: bool,
    ) -> None:
        if state.bundle_state == "degraded":
            raise CellRestoreFailed("degraded bundle cannot restore")
        if require_paused_state and state.bundle_state != "resources_paused":
            raise CellRestoreFailed("bundle must be paused before restore")
        for kind, container_name in (
            ("postgres", names.postgres_container),
            ("redis", names.redis_container),
        ):
            record = await self.docker.get_container(container_name)
            if record is not None and record.state == "running":
                raise CellRestoreFailed(f"{kind} sidecar still running")

    def _validate_restore_manifest(
        self,
        *,
        workspace_id: UUID,
        state: Any,
        checkpoint_ref: str,
        manifest: CheckpointManifest,
    ) -> None:
        expected_profile = state.profile_version or self.profile_version
        if manifest.workspace_id != workspace_id:
            raise CellRestoreFailed("checkpoint workspace mismatch")
        if manifest.project_id != state.project_id:
            raise CellRestoreFailed("checkpoint project mismatch")
        if manifest.profile_version != expected_profile:
            raise CellRestoreFailed("checkpoint profile mismatch")
        if manifest.checkpoint_ref != checkpoint_ref:
            raise CellRestoreFailed("checkpoint ref mismatch")
        if manifest.postgres_image != self.postgres_image:
            raise CellRestoreFailed("checkpoint postgres image mismatch")
        if manifest.redis_policy != "clear_on_restore":
            raise CellRestoreFailed("checkpoint redis policy unsupported")

    async def _start_maintenance_postgres(
        self,
        *,
        state: Any,
        names: Any,
        helper_name: str,
    ) -> None:
        if await self.docker.get_container(helper_name) is not None:
            await self.docker.remove_container(helper_name)
        await self.docker.create_container(
            DockerContainerSpec(
                name=helper_name,
                image=self.postgres_image,
                labels=identity_labels(self._spec_from_state(state), "postgres-maintenance"),
                user="postgres",
                cap_add=[],
                cap_drop=["ALL"],
                # Docker's archive API rejects writes to a read-only rootfs even
                # when /tmp is tmpfs. This trusted, short-lived helper needs one
                # local dump file for pg_restore; it has no network or extra caps.
                read_only=False,
                privileged=False,
                security_opt=["no-new-privileges:true"],
                ports={},
                env={},
                volumes=(names.postgres_volume,),
                mounts=(),
                network_names=(),
                helper=True,
            )
        )
        await self.docker.start_container(helper_name)

    async def _apply_restore(
        self,
        *,
        workspace_id: UUID,
        names: Any,
        postgres_container_name: str,
        artifacts: dict[str, bytes],
    ) -> None:
        await self._replace_volume_files(
            names.workspace_volume,
            _extract_archive(artifacts["workspace.tar"]),
        )
        await self._replace_volume_files(
            names.agent_home_volume,
            _extract_archive(artifacts["agent-home.tar"]),
        )
        password = await self._postgres_password(workspace_id)
        await self.docker.postgres_restore(
            postgres_container_name,
            artifacts["postgres.dump"],
            password,
        )
        if await self.docker.postgres_smoke_query(postgres_container_name, password) is False:
            raise CellRestoreFailed("postgres smoke query failed")
        await self.docker.clear_volume(names.redis_volume)
        if self.machine_runtime is not None:
            await self.machine_runtime.restore_payload(
                self._require_state(workspace_id), artifacts.get("machine.json")
            )
        elif "machine.json" in artifacts:
            raise CellRestoreFailed("portable environment provider is unavailable")

    async def _replace_volume_files(self, volume_name: str, files: dict[str, bytes]) -> None:
        current = await self.docker.read_volume_files(volume_name)
        to_delete = tuple(path for path in current if path not in files)
        if to_delete:
            await self.docker.delete_volume_paths(volume_name, to_delete)
        await self.docker.write_volume_files(volume_name, files)

    async def _remove_container_if_present(self, name: str) -> None:
        if await self.docker.get_container(name) is not None:
            await self.docker.remove_container(name)

    def _begin_if_missing(
        self,
        state: Any,
        mutation: LifecycleMutation,
        *,
        kind: str,
        checkpoint_ref: str | None = None,
    ) -> None:
        if state.operation(mutation.operation_id) is not None:
            return
        self.state_store.begin(
            self._spec_from_state(state),
            mutation,
            kind=kind,
            phase="planned",
            resource_names=state.resource_names,
            expected_resources={"checkpoint_ref": checkpoint_ref or ""},
            checkpoint_ref=checkpoint_ref,
        )

    async def _replay_completed_manifest_or_reject(
        self,
        *,
        workspace_id: UUID,
        state: Any,
        names: Any,
        mutation: LifecycleMutation,
        kind: str,
        checkpoint_ref: str,
    ) -> CheckpointManifest | None:
        operation = state.operation(mutation.operation_id)
        if operation is None:
            return None
        if operation.matches_replay_envelope(
            kind=kind,
            request_digest=mutation.request_digest,
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=checkpoint_ref,
        ) is False:
            raise CellFenceRejected("replay envelope mismatch")
        if operation.status == "failed":
            raise CellRestoreFailed(operation.detail or "restore failed")
        if operation.status != "completed":
            raise CellFenceRejected("operation replay unavailable")
        manifest = await self._load_manifest(names.checkpoint_volume, checkpoint_ref)
        if kind == "restore":
            self._validate_restore_manifest(
                workspace_id=workspace_id,
                state=state,
                checkpoint_ref=checkpoint_ref,
                manifest=manifest,
            )
        return manifest

    @staticmethod
    def _spec_from_state(state: Any) -> Any:
        from omnia_orchestrator.core.workspace_provider import WorkspaceSpec

        return WorkspaceSpec(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            profile_version=state.profile_version,
        )


def _archive_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for raw_path, content in sorted(files.items()):
            path = _normalize_archive_path(raw_path)
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _extract_archive(payload: bytes) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile() is False:
                continue
            path = _normalize_archive_path(member.name)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise CellRestoreFailed(f"checkpoint archive entry unreadable: {path}")
            result[path] = extracted.read()
    return result


def _normalize_archive_path(path: str) -> str:
    normalized = PurePosixPath(str(path)).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    normalized_path = PurePosixPath(normalized)
    if (
        normalized in {"", "."}
        or normalized_path.is_absolute()
        or ".." in normalized_path.parts
    ):
        raise CellRestoreFailed(f"checkpoint archive path invalid: {path}")
    return normalized_path.as_posix()


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
