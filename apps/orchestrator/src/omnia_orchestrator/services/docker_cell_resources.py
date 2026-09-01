"""Docker Project Cell resource manager over an injected backend."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellIdentityConflict,
    CellIndeterminateOperation,
    CellResourceError,
    CellResourceNames,
    CellResourceProfile,
    LifecycleMutation,
    identity_labels,
)
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.services.cell_admission import CellAdmissionGate, DockerHostCapacityReader
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.cell_state import (
    CellCredentialStore,
    CellStateStore,
    CellWorkspaceState,
)

_ALLOWED_HELPER_KINDS = frozenset(
    {
        "postgres-ownership",
        "postgres-init",
        "postgres-maintenance",
        "volume-read",
        "volume-write",
        "volume-delete",
        "volume-promote",
        "volume-clear",
    }
)
_VOLUME_HELPER_PURPOSES = frozenset(
    {"volume-read", "volume-write", "volume-delete", "volume-promote", "volume-clear"}
)
_SENSITIVE_ENV_KEY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "DATABASE_URL",
)


def _is_sensitive_env_key(key: str) -> bool:
    normalized = key.upper()
    return any(marker in normalized for marker in _SENSITIVE_ENV_KEY_MARKERS)


@dataclass(frozen=True, slots=True)
class DockerResourceRecord:
    resource_id: str
    name: str
    labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class DockerNetworkRecord(DockerResourceRecord):
    internal: bool


@dataclass(frozen=True, slots=True)
class DockerVolumeRecord(DockerResourceRecord):
    files: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class DockerContainerRecord(DockerResourceRecord):
    image: str
    user: str
    cap_add: list[str]
    cap_drop: list[str]
    read_only: bool
    privileged: bool
    security_opt: list[str]
    ports: dict[str, str]
    env: dict[str, str]
    volumes: tuple[str, ...]
    mounts: tuple[str, ...]
    network_names: tuple[str, ...]
    state: str
    helper: bool
    removed_in_finally: bool = False
    tmpfs: tuple[str, ...] = ("/tmp", "/run")
    pids_limit: int = 128
    memory_limit_bytes: int = 0
    cpu_quota: float = 0.0


@dataclass(frozen=True, slots=True)
class DockerContainerSpec:
    name: str
    image: str
    labels: dict[str, str]
    user: str
    cap_add: list[str]
    cap_drop: list[str]
    read_only: bool
    privileged: bool
    security_opt: list[str]
    ports: dict[str, str]
    env: dict[str, str]
    volumes: tuple[str, ...]
    mounts: tuple[str, ...]
    network_names: tuple[str, ...]
    helper: bool
    tmpfs: tuple[str, ...] = ("/tmp", "/run")
    pids_limit: int = 128
    memory_limit_bytes: int = 0
    cpu_quota: float = 0.0


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    exit_code: int
    output: str
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class CellBundleHandle:
    workspace_id: UUID
    provider_ref: str
    state: str
    fencing_epoch: int
    resource_names: CellResourceNames


@dataclass(frozen=True, slots=True)
class CellBundleObservation:
    state: str
    identity_valid: bool
    containers: dict[str, bool]
    networks: dict[str, bool]
    volumes: dict[str, bool]
    detail: str


@dataclass(frozen=True, slots=True)
class CellInventorySnapshot:
    retained_volume_names: tuple[str, ...]
    helper_container_ids: tuple[str, ...]
    secret_staging_volume_ids: tuple[str, ...]
    persistent_container_env_secret_matches: tuple[str, ...]


class CellDockerBackend(Protocol):
    async def begin_operation(self, operation_id: UUID) -> None: ...

    async def get_volume(self, name: str) -> DockerVolumeRecord | None: ...

    async def create_volume(self, name: str, labels: dict[str, str]) -> DockerVolumeRecord: ...

    async def remove_volume(self, name: str) -> None: ...

    async def list_workspace_volumes(self, workspace_id: UUID) -> list[DockerVolumeRecord]: ...

    async def read_volume_files(self, name: str) -> dict[str, bytes]: ...

    async def write_volume_files(self, name: str, files: dict[str, bytes]) -> None: ...

    async def delete_volume_paths(self, name: str, paths: tuple[str, ...]) -> None: ...

    async def promote_volume_directory(
        self,
        name: str,
        staging_prefix: str,
        final_prefix: str,
    ) -> None: ...

    async def clear_volume(self, name: str) -> None: ...

    async def get_network(self, name: str) -> DockerNetworkRecord | None: ...

    async def create_network(
        self,
        name: str,
        labels: dict[str, str],
        *,
        internal: bool,
    ) -> DockerNetworkRecord: ...

    async def remove_network(self, name: str) -> None: ...

    async def list_workspace_networks(self, workspace_id: UUID) -> list[DockerNetworkRecord]: ...

    async def get_container(self, name: str) -> DockerContainerRecord | None: ...

    async def create_container(self, spec: DockerContainerSpec) -> DockerContainerRecord: ...

    async def start_container(self, name: str) -> DockerContainerRecord: ...

    async def stop_container(self, name: str) -> None: ...

    async def remove_container(self, name: str) -> None: ...

    async def list_workspace_containers(
        self, workspace_id: UUID
    ) -> list[DockerContainerRecord]: ...

    async def postgres_dump(self, container_name: str, password: str) -> bytes: ...

    async def postgres_restore(self, container_name: str, dump: bytes, password: str) -> None: ...

    async def postgres_smoke_query(self, container_name: str, password: str) -> bool: ...

    async def run_workspace_command(
        self,
        *,
        workspace_volume_name: str,
        agent_home_volume_name: str,
        labels: dict[str, str],
        image: str,
        command: str,
        internal_network_name: str,
        egress_network_name: str,
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> DockerCommandResult: ...


@dataclass(slots=True)
class DockerCellResourceManager:
    profile: CellResourceProfile
    docker: CellDockerBackend
    admission_gate: CellAdmissionGate
    capacity_reader: DockerHostCapacityReader
    credential_store: CellCredentialStore
    state_store: CellStateStore
    operation_lock: WorkspaceOperationLock
    namespace: str = "prod"

    async def ensure(self, spec: WorkspaceSpec, mutation: LifecycleMutation) -> CellBundleHandle:
        return await self._upsert_bundle(spec, mutation, wake_only=False)

    async def wake(self, workspace_id: UUID, mutation: LifecycleMutation) -> CellBundleHandle:
        state = self._require_state(workspace_id, active_operation_id=mutation.operation_id)
        if state.bundle_state == "degraded":
            raise CellResourceError("degraded bundle cannot wake")
        spec = self._spec_from_state(state)
        return await self._upsert_bundle(spec, mutation, wake_only=True)

    async def pause_services(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        checkpoint_ref: str | None = None,
    ) -> None:
        async with self.operation_lock.hold(workspace_id):
            await self._pause_services_locked(
                workspace_id,
                mutation,
                checkpoint_ref=checkpoint_ref,
                record_operation=True,
            )

    async def pause_services_without_lock(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        checkpoint_ref: str | None,
        record_operation: bool,
    ) -> None:
        await self._pause_services_locked(
            workspace_id,
            mutation,
            checkpoint_ref=checkpoint_ref,
            record_operation=record_operation,
        )

    async def destroy_compute(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        checkpoint_ref: str | None = None,
    ) -> None:
        async with self.operation_lock.hold(workspace_id):
            await self._destroy_compute_locked(
                workspace_id,
                mutation,
                checkpoint_ref=checkpoint_ref,
                record_operation=True,
            )

    async def destroy_compute_without_lock(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        checkpoint_ref: str | None,
        record_operation: bool,
    ) -> None:
        await self._destroy_compute_locked(
            workspace_id,
            mutation,
            checkpoint_ref=checkpoint_ref,
            record_operation=record_operation,
        )

    async def inspect_by_project(self, project_id: UUID) -> CellBundleObservation:
        state = next(
            (item for item in self.state_store.all_states() if item.project_id == project_id),
            None,
        )
        if state is None or state.resource_names is None:
            return CellBundleObservation(
                state="retained",
                identity_valid=False,
                containers={},
                networks={},
                volumes={},
                detail="workspace state not found",
            )
        return await self._observe_state(state)

    async def reconcile(
        self, workspace_id: UUID, mutation: LifecycleMutation
    ) -> CellBundleObservation:
        async with self.operation_lock.hold(workspace_id):
            state = self._prepared_state(workspace_id)
            if state is None:
                return CellBundleObservation(
                    state="retained",
                    identity_valid=False,
                    containers={},
                    networks={},
                    volumes={},
                    detail="workspace state not found",
                )
            self._assert_profile_version(state.profile_version)
            self._reject_unless_allowed(state, mutation, allow_reconcile=True)
            await self.stateful_begin_or_replay(
                self._spec_from_state(state),
                mutation,
                kind="reconcile",
                names=state.resource_names,
            )
            observation = await self._observe_state(state)
            if observation.identity_valid is False:
                raise CellIdentityConflict(observation.detail)
            leaked_resources = await self._remove_leaked_ephemera(
                workspace_id, state.resource_names
            )
            detail = observation.detail
            if leaked_resources:
                detail = "leaked ephemera removed"
            if state.bundle_state == "resources_ready" and observation.state != "resources_ready":
                self.state_store.mark_degraded(
                    workspace_id,
                    mutation,
                    kind="reconcile",
                    detail=detail or "ready bundle missing compute",
                )
                return CellBundleObservation(
                    state="degraded",
                    identity_valid=True,
                    containers=observation.containers,
                    networks=observation.networks,
                    volumes=observation.volumes,
                    detail=detail or "ready bundle missing compute",
                )
            self.state_store.complete(
                workspace_id,
                mutation,
                phase="completed",
                provider_ref=self._provider_ref(workspace_id),
                bundle_state=observation.state,
                detail=detail,
            )
            return observation

    async def _upsert_bundle(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        *,
        wake_only: bool,
    ) -> CellBundleHandle:
        async with self.operation_lock.hold(spec.workspace_id):
            self._assert_profile_version(spec.profile_version)
            state = self._prepared_state(
                spec.workspace_id,
                active_operation_id=mutation.operation_id,
            )
            if state is not None:
                self._verify_spec_matches_state(state, spec)
            names = self._resource_names_for_spec(spec, state)
            await self._preflight_named_resources(spec, names)
            if state is not None:
                replay = self._replay_if_completed(state, mutation)
                if replay is not None:
                    return replay
                self._reject_unless_allowed(state, mutation, allow_reconcile=False)
            existing_bundle = await self._bundle_exists(names)
            running_bundle = await self._bundle_running(names)
            decision = self.admission_gate.check(
                self.capacity_reader.read(),
                existing_bundle=existing_bundle,
                running_bundle=running_bundle,
            )
            if decision.allowed is False:
                raise CellResourceError(decision.reason)

            credentials = self.credential_store.load_or_create(spec.workspace_id)
            await self.stateful_begin_or_replay(
                spec, mutation, kind="wake" if wake_only else "ensure", names=names
            )
            provider_ref = self._provider_ref(spec.workspace_id)
            try:
                await self.docker.begin_operation(mutation.operation_id)
                await self._ensure_volumes(spec, mutation, names)
                await self._ensure_networks(spec, mutation, names)
                await self._ensure_postgres_initialized(
                    spec, mutation, names, credentials.postgres_password
                )
                await self._ensure_sidecars(spec, mutation, names)
                completed = self.state_store.complete(
                    spec.workspace_id,
                    mutation,
                    phase="completed",
                    provider_ref=provider_ref,
                    bundle_state="resources_ready",
                )
                return CellBundleHandle(
                    workspace_id=spec.workspace_id,
                    provider_ref=provider_ref,
                    state="resources_ready",
                    fencing_epoch=mutation.fencing_epoch,
                    resource_names=completed.resource_names or names,
                )
            except asyncio.CancelledError:
                self.state_store.mark_indeterminate(
                    spec.workspace_id,
                    mutation=mutation,
                    detail="bundle mutation cancelled",
                )
                raise
            except Exception as exc:
                self.state_store.mark_indeterminate(
                    spec.workspace_id,
                    mutation=mutation,
                    detail=str(exc),
                )
                raise

    async def stateful_begin_or_replay(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        *,
        kind: str,
        names: CellResourceNames | None = None,
        checkpoint_ref: str | None = None,
    ) -> CellWorkspaceState:
        self._assert_profile_version(spec.profile_version)
        state = self._prepared_state(
            spec.workspace_id,
            active_operation_id=mutation.operation_id,
        )
        if state is not None:
            self._verify_spec_matches_state(state, spec)
            replay = self._replay_if_completed(state, mutation)
            if replay is not None:
                return state
        return self.state_store.begin(
            spec,
            mutation,
            kind=kind,
            phase="planned",
            resource_names=names
            or CellResourceNames.for_workspace(
                spec.workspace_id,
                namespace="test" if self.namespace == "test" else "prod",
            ),
            expected_resources=self._expected_resources(
                names
                or CellResourceNames.for_workspace(
                    spec.workspace_id,
                    namespace="test" if self.namespace == "test" else "prod",
                )
            ),
            checkpoint_ref=checkpoint_ref,
        )

    async def state_store_advance(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        phase: str,
        detail: str | None = None,
    ) -> CellWorkspaceState:
        state = self._require_state(workspace_id, active_operation_id=mutation.operation_id)
        names = state.resource_names
        return self.state_store.advance(
            workspace_id,
            mutation,
            phase=phase,
            observed_resources=self._expected_resources(names) if names is not None else None,
            bundle_state=state.bundle_state,
            detail=detail,
        )

    async def _ensure_volumes(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        names: CellResourceNames,
    ) -> None:
        for kind, volume_name in (
            ("workspace", names.workspace_volume),
            ("agent-home", names.agent_home_volume),
            ("postgres", names.postgres_volume),
            ("redis", names.redis_volume),
            ("checkpoints", names.checkpoint_volume),
        ):
            await self._ensure_volume(volume_name, identity_labels(spec, kind))
        await self.state_store_advance(spec.workspace_id, mutation, phase="volumes_created")

    async def _ensure_networks(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        names: CellResourceNames,
    ) -> None:
        await self._ensure_network(
            names.internal_network, identity_labels(spec, "internal"), internal=True
        )
        await self._ensure_network(
            names.egress_network, identity_labels(spec, "egress"), internal=True
        )
        await self.state_store_advance(spec.workspace_id, mutation, phase="networks_created")

    async def _ensure_postgres_initialized(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        names: CellResourceNames,
        password: str,
    ) -> None:
        await self._require_volume(names.postgres_volume)
        postgres_files = await self.docker.read_volume_files(names.postgres_volume)
        legacy_secret_path = "PGDATA/postgres-password.txt"
        if legacy_secret_path in postgres_files:
            await self.docker.delete_volume_paths(names.postgres_volume, (legacy_secret_path,))
            postgres_files.pop(legacy_secret_path, None)
        if postgres_files:
            await self.state_store_advance(
                spec.workspace_id, mutation, phase="postgres_initialized"
            )
            return

        temp_secret_volume = names.secret_staging_volume_name(
            mutation.operation_id, "postgres-init"
        )
        await self._ensure_volume(temp_secret_volume, identity_labels(spec, "secret-staging"))
        helper_names = [
            names.helper_container_name("postgres-ownership", mutation.operation_id),
            names.helper_container_name("postgres-init", mutation.operation_id),
        ]
        try:
            await self.docker.write_volume_files(
                temp_secret_volume, {"postgres-password.txt": password.encode("utf-8")}
            )
            await self.docker.create_container(
                DockerContainerSpec(
                    name=helper_names[0],
                    image=self.profile.postgres_image,
                    labels=identity_labels(spec, "postgres-ownership"),
                    user="0:0",
                    cap_add=["CHOWN"],
                    cap_drop=["ALL"],
                    read_only=True,
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
            await self.docker.create_container(
                DockerContainerSpec(
                    name=helper_names[1],
                    image=self.profile.postgres_image,
                    labels=identity_labels(spec, "postgres-init"),
                    user="postgres",
                    cap_add=[],
                    cap_drop=["ALL"],
                    read_only=True,
                    privileged=False,
                    security_opt=["no-new-privileges:true"],
                    ports={},
                    env={},
                    volumes=(names.postgres_volume, temp_secret_volume),
                    mounts=("/run/secrets/postgres-password.txt",),
                    network_names=(),
                    helper=True,
                )
            )
        finally:
            for helper_name in helper_names:
                await self._remove_container_if_present(helper_name)
            await self._remove_volume_if_present(temp_secret_volume)
        await self.state_store_advance(spec.workspace_id, mutation, phase="postgres_initialized")

    async def _ensure_sidecars(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
        names: CellResourceNames,
    ) -> None:
        postgres = await self.docker.get_container(names.postgres_container)
        if postgres is None:
            await self.docker.create_container(
                self._steady_postgres_spec(spec, names),
            )
        else:
            self._verify_container_record(postgres, self._steady_postgres_spec(spec, names))
        redis = await self.docker.get_container(names.redis_container)
        if redis is None:
            await self.docker.create_container(
                self._steady_redis_spec(spec, names),
            )
        else:
            self._verify_container_record(redis, self._steady_redis_spec(spec, names))
        await self.docker.start_container(names.postgres_container)
        await self.docker.start_container(names.redis_container)
        await self.state_store_advance(spec.workspace_id, mutation, phase="sidecars_started")

    async def _observe_state(self, state: CellWorkspaceState) -> CellBundleObservation:
        names = state.resource_names
        if names is None:
            return CellBundleObservation(
                state="retained",
                identity_valid=False,
                containers={},
                networks={},
                volumes={},
                detail="resource names missing",
            )
        identity_valid = True
        identity_errors: list[str] = []
        volume_presence: dict[str, bool] = {}
        network_presence: dict[str, bool] = {}
        container_presence: dict[str, bool] = {}

        for kind, volume_name in (
            ("workspace", names.workspace_volume),
            ("agent-home", names.agent_home_volume),
            ("postgres", names.postgres_volume),
            ("redis", names.redis_volume),
            ("checkpoints", names.checkpoint_volume),
        ):
            volume_record = await self.docker.get_volume(volume_name)
            volume_presence[kind] = volume_record is not None
            if volume_record is not None:
                try:
                    self._verify_volume_record(volume_record, self._state_labels(state, kind))
                except CellIdentityConflict:
                    identity_valid = False
                    identity_errors.append(f"volume:{kind}")

        for kind, network_name in (
            ("internal", names.internal_network),
            ("egress", names.egress_network),
        ):
            network_record = await self.docker.get_network(network_name)
            network_presence[kind] = network_record is not None
            if network_record is not None:
                try:
                    self._verify_network_record(
                        network_record,
                        self._state_labels(state, kind),
                        internal=True,
                    )
                except CellIdentityConflict:
                    identity_valid = False
                    identity_errors.append(f"network:{kind}")

        for kind, container_name in (
            ("postgres", names.postgres_container),
            ("redis", names.redis_container),
        ):
            container_record = await self.docker.get_container(container_name)
            container_presence[kind] = (
                container_record is not None and container_record.state == "running"
            )
            if container_record is not None:
                expected = (
                    self._steady_postgres_spec(self._spec_from_state(state), names)
                    if kind == "postgres"
                    else self._steady_redis_spec(self._spec_from_state(state), names)
                )
                try:
                    self._verify_container_record(container_record, expected)
                except CellIdentityConflict as exc:
                    identity_valid = False
                    identity_errors.append(f"container:{kind}:{exc}")

        if identity_valid is False:
            return CellBundleObservation(
                state="partial",
                identity_valid=False,
                containers=container_presence,
                networks=network_presence,
                volumes=volume_presence,
                detail=(
                    "resource identity mismatch: " + ",".join(identity_errors)
                    if identity_errors
                    else "resource identity mismatch"
                ),
            )

        if (
            all(container_presence.values())
            and all(network_presence.values())
            and all(volume_presence.values())
        ):
            return CellBundleObservation(
                state="resources_ready",
                identity_valid=True,
                containers=container_presence,
                networks=network_presence,
                volumes=volume_presence,
                detail="bundle ready",
            )
        if all(volume_presence.values()) and not any(container_presence.values()):
            state_name = "retained" if not any(network_presence.values()) else "resources_paused"
            return CellBundleObservation(
                state=state_name,
                identity_valid=True,
                containers=container_presence,
                networks=network_presence,
                volumes=volume_presence,
                detail="compute missing or paused",
            )
        return CellBundleObservation(
            state="partial",
            identity_valid=True,
            containers=container_presence,
            networks=network_presence,
            volumes=volume_presence,
            detail="bundle partially materialized",
        )

    async def inventory_for_workspace(self, workspace_id: UUID) -> CellInventorySnapshot:
        volumes = await self.docker.list_workspace_volumes(workspace_id)
        containers = await self.docker.list_workspace_containers(workspace_id)
        helper_ids = tuple(
            item.resource_id
            for item in containers
            if item.helper and item.removed_in_finally is False
        )
        staging_ids = tuple(
            item.resource_id
            for item in volumes
            if item.labels.get("omnia.resource_kind") == "secret-staging"
        )
        env_secret_matches: list[str] = []
        for item in containers:
            if item.helper:
                continue
            for key in item.env:
                if _is_sensitive_env_key(key):
                    env_secret_matches.append(item.name)
                    break
        retained = tuple(
            item.name
            for item in volumes
            if item.labels.get("omnia.resource_kind")
            in {"workspace", "agent-home", "postgres", "redis", "checkpoints"}
        )
        return CellInventorySnapshot(
            retained_volume_names=retained,
            helper_container_ids=helper_ids,
            secret_staging_volume_ids=staging_ids,
            persistent_container_env_secret_matches=tuple(env_secret_matches),
        )

    async def _remove_leaked_ephemera(
        self,
        workspace_id: UUID,
        names: CellResourceNames | None,
    ) -> list[str]:
        removed: list[str] = []
        state = self.state_store.load(workspace_id)
        spec = (
            self._spec_from_state(state)
            if state is not None and state.project_id is not None and state.owner_id is not None
            else None
        )
        for record in await self.docker.list_workspace_containers(workspace_id):
            if not record.helper:
                continue
            if (
                spec is None
                or names is None
                or self._is_expected_helper_container(record, spec, names) is False
            ):
                continue
            await self.docker.remove_container(record.name)
            removed.append(record.name)
        for volume_record in await self.docker.list_workspace_volumes(workspace_id):
            if (
                spec is None
                or names is None
                or self._is_expected_secret_staging_volume(volume_record, spec, names) is False
            ):
                continue
            await self.docker.remove_volume(volume_record.name)
            removed.append(volume_record.name)
        return removed

    async def _bundle_exists(self, names: CellResourceNames) -> bool:
        targets = (
            list(names.retained_volumes)
            + list(names.networks)
            + [
                names.postgres_container,
                names.redis_container,
            ]
        )
        for name in targets:
            if await self.docker.get_volume(name) is not None:
                return True
            if await self.docker.get_network(name) is not None:
                return True
            if await self.docker.get_container(name) is not None:
                return True
        return False

    async def _bundle_running(self, names: CellResourceNames) -> bool:
        postgres = await self.docker.get_container(names.postgres_container)
        redis = await self.docker.get_container(names.redis_container)
        return bool(
            postgres is not None
            and redis is not None
            and postgres.state == "running"
            and redis.state == "running"
        )

    async def _ensure_volume(self, name: str, labels: dict[str, str]) -> DockerVolumeRecord:
        existing = await self.docker.get_volume(name)
        if existing is not None:
            self._verify_volume_record(existing, labels)
            return existing
        return await self.docker.create_volume(name, labels)

    async def _ensure_network(
        self,
        name: str,
        labels: dict[str, str],
        *,
        internal: bool,
    ) -> DockerNetworkRecord:
        existing = await self.docker.get_network(name)
        if existing is not None:
            self._verify_network_record(existing, labels, internal=internal)
            return existing
        return await self.docker.create_network(name, labels, internal=internal)

    async def _require_volume(self, name: str) -> DockerVolumeRecord:
        record = await self.docker.get_volume(name)
        if record is None:
            raise CellResourceError(f"missing volume: {name}")
        return record

    async def _stop_if_present(self, name: str) -> None:
        if await self.docker.get_container(name) is not None:
            await self.docker.stop_container(name)

    async def _remove_container_if_present(self, name: str) -> None:
        if await self.docker.get_container(name) is not None:
            await self.docker.remove_container(name)

    async def _remove_volume_if_present(self, name: str) -> None:
        if await self.docker.get_volume(name) is not None:
            await self.docker.remove_volume(name)

    async def _remove_network_if_present(self, name: str) -> None:
        if await self.docker.get_network(name) is not None:
            await self.docker.remove_network(name)

    def _replay_if_completed(
        self,
        state: CellWorkspaceState,
        mutation: LifecycleMutation,
    ) -> CellBundleHandle | None:
        operation = state.operation(mutation.operation_id)
        if operation is None:
            return None
        if operation.status != "completed":
            return None
        if operation.replay_completed_same_envelope(
            mutation.request_digest, mutation.fencing_epoch
        ):
            names = state.resource_names
            if names is None:
                raise CellResourceError("completed operation missing resource names")
            return CellBundleHandle(
                workspace_id=state.workspace_id,
                provider_ref=state.provider_ref or self._provider_ref(state.workspace_id),
                state=state.bundle_state,
                fencing_epoch=state.fencing_epoch,
                resource_names=names,
            )
        raise CellFenceRejected("replay envelope mismatch")

    def _reject_unless_allowed(
        self,
        state: CellWorkspaceState,
        mutation: LifecycleMutation,
        *,
        allow_reconcile: bool,
    ) -> None:
        if mutation.fencing_epoch < state.fencing_epoch:
            raise CellFenceRejected("stale fencing epoch")
        if state.phase == "indeterminate" and allow_reconcile is False:
            raise CellIndeterminateOperation("indeterminate operation requires reconcile")
        if (
            state.phase == "indeterminate"
            and allow_reconcile
            and mutation.fencing_epoch <= state.fencing_epoch
        ):
            raise CellFenceRejected("reconcile requires higher fencing epoch")
        if mutation.fencing_epoch == state.fencing_epoch and state.last_operation_id is not None:
            if mutation.operation_id != state.last_operation_id:
                raise CellFenceRejected("epoch already consumed by another operation")

    def _prepared_state(
        self,
        workspace_id: UUID,
        *,
        active_operation_id: UUID | None = None,
    ) -> CellWorkspaceState | None:
        state = self.state_store.load(workspace_id)
        if state is None:
            return None
        latest = (
            state.operation(state.last_operation_id)
            if state.last_operation_id is not None
            else None
        )
        if (
            latest is not None
            and latest.status == "running"
            and latest.operation_id == active_operation_id
        ):
            return state
        if latest is not None and latest.status == "running":
            return self.state_store.mark_indeterminate(
                workspace_id,
                detail="running phase recovered as indeterminate",
            )
        return state

    def _require_state(
        self,
        workspace_id: UUID,
        *,
        active_operation_id: UUID | None = None,
    ) -> CellWorkspaceState:
        state = self._prepared_state(
            workspace_id,
            active_operation_id=active_operation_id,
        )
        if state is None:
            raise CellResourceError(f"workspace state missing: {workspace_id}")
        return state

    def _expected_resources(self, names: CellResourceNames | None) -> dict[str, str] | None:
        if names is None:
            return None
        return {
            "internal_network": names.internal_network,
            "egress_network": names.egress_network,
            "workspace_volume": names.workspace_volume,
            "agent_home_volume": names.agent_home_volume,
            "postgres_volume": names.postgres_volume,
            "redis_volume": names.redis_volume,
            "checkpoint_volume": names.checkpoint_volume,
            "postgres_container": names.postgres_container,
            "redis_container": names.redis_container,
        }

    def _steady_postgres_spec(
        self,
        spec: WorkspaceSpec,
        names: CellResourceNames,
    ) -> DockerContainerSpec:
        return DockerContainerSpec(
            name=names.postgres_container,
            image=self.profile.postgres_image,
            labels=identity_labels(spec, "postgres"),
            user="postgres",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=(names.postgres_volume,),
            mounts=(),
            network_names=(names.internal_network,),
            helper=False,
            memory_limit_bytes=self.profile.bundle_memory_bytes // 2,
            cpu_quota=max(self.profile.bundle_cpu_cores / 2.0, 0.5),
        )

    def _steady_redis_spec(
        self,
        spec: WorkspaceSpec,
        names: CellResourceNames,
    ) -> DockerContainerSpec:
        return DockerContainerSpec(
            name=names.redis_container,
            image=self.profile.redis_image,
            labels=identity_labels(spec, "redis"),
            user="redis",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=(names.redis_volume,),
            mounts=(),
            network_names=(names.internal_network,),
            helper=False,
            memory_limit_bytes=self.profile.bundle_memory_bytes // 4,
            cpu_quota=max(self.profile.bundle_cpu_cores / 4.0, 0.25),
        )

    def _provider_ref(self, workspace_id: UUID) -> str:
        return f"docker-owner-canary:{workspace_id}"

    def _verify_spec_matches_state(
        self,
        state: CellWorkspaceState,
        spec: WorkspaceSpec,
    ) -> None:
        self._assert_profile_version(state.profile_version)
        self._assert_profile_version(spec.profile_version)
        if state.matches_spec_identity(spec) is False:
            raise CellIdentityConflict("workspace immutable identity mismatch")

    def _resource_names_for_spec(
        self,
        spec: WorkspaceSpec,
        state: CellWorkspaceState | None,
    ) -> CellResourceNames:
        names = CellResourceNames.for_workspace(
            spec.workspace_id,
            namespace="test" if self.namespace == "test" else "prod",
        )
        if state is not None and state.resource_names is not None and state.resource_names != names:
            raise CellIdentityConflict("workspace resource_names mismatch")
        if state is not None and state.resource_names is not None:
            return state.resource_names
        return names

    def _spec_from_state(self, state: CellWorkspaceState) -> WorkspaceSpec:
        if state.project_id is None or state.owner_id is None:
            raise CellResourceError("workspace state missing immutable identity")
        return WorkspaceSpec(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            profile_version=state.profile_version,
        )

    def _state_labels(self, state: CellWorkspaceState, resource_kind: str) -> dict[str, str]:
        spec = self._spec_from_state(state)
        return identity_labels(spec, resource_kind)

    @staticmethod
    def _helper_labels(spec: WorkspaceSpec, resource_kind: str) -> dict[str, str]:
        labels = identity_labels(spec, resource_kind)
        labels["omnia.helper"] = "true"
        return labels

    async def _pause_services_locked(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        checkpoint_ref: str | None,
        record_operation: bool,
    ) -> None:
        state = self._prepared_state(
            workspace_id,
            active_operation_id=mutation.operation_id,
        )
        if state is None:
            return
        names = state.resource_names
        assert names is not None
        spec = self._spec_from_state(state)
        self._assert_profile_version(spec.profile_version)
        await self._preflight_named_resources(spec, names)
        self._reject_unless_allowed(state, mutation, allow_reconcile=False)
        if record_operation:
            await self.stateful_begin_or_replay(
                spec,
                mutation,
                kind="pause",
                checkpoint_ref=checkpoint_ref,
            )
        try:
            if record_operation:
                await self.state_store_advance(workspace_id, mutation, phase="planned")
            await self._stop_if_present(names.postgres_container)
            await self._stop_if_present(names.redis_container)
            if record_operation:
                self.state_store.complete(
                    workspace_id,
                    mutation,
                    phase="completed",
                    provider_ref=self._provider_ref(workspace_id),
                    bundle_state="resources_paused",
                )
            else:
                self.state_store.set_bundle_state(
                    workspace_id,
                    bundle_state="resources_paused",
                )
        except asyncio.CancelledError:
            if record_operation:
                self.state_store.mark_indeterminate(
                    workspace_id, mutation=mutation, detail="pause cancelled"
                )
            raise
        except Exception as exc:
            if record_operation:
                self.state_store.mark_indeterminate(
                    workspace_id, mutation=mutation, detail=str(exc)
                )
            raise

    async def _destroy_compute_locked(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        checkpoint_ref: str | None,
        record_operation: bool,
    ) -> None:
        state = self._prepared_state(
            workspace_id,
            active_operation_id=mutation.operation_id,
        )
        if state is None:
            return
        spec = self._spec_from_state(state)
        self._assert_profile_version(spec.profile_version)
        self._reject_unless_allowed(state, mutation, allow_reconcile=False)
        names = state.resource_names
        assert names is not None
        await self._preflight_named_resources(spec, names)
        if record_operation:
            await self.stateful_begin_or_replay(
                spec,
                mutation,
                kind="destroy",
                checkpoint_ref=checkpoint_ref,
            )
        try:
            if record_operation:
                await self.state_store_advance(
                    workspace_id,
                    mutation,
                    phase="containers_removed",
                )
            await self._remove_container_if_present(names.postgres_container)
            await self._remove_container_if_present(names.redis_container)
            if record_operation:
                await self.state_store_advance(
                    workspace_id,
                    mutation,
                    phase="networks_removed",
                )
            await self._remove_network_if_present(names.internal_network)
            await self._remove_network_if_present(names.egress_network)
            if record_operation:
                self.state_store.complete(
                    workspace_id,
                    mutation,
                    phase="completed",
                    provider_ref=self._provider_ref(workspace_id),
                    bundle_state="retained",
                )
        except asyncio.CancelledError:
            if record_operation:
                self.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail="destroy cancelled",
                )
            raise
        except Exception as exc:
            if record_operation:
                self.state_store.mark_indeterminate(
                    workspace_id, mutation=mutation, detail=str(exc)
                )
            raise

    async def _preflight_named_resources(
        self,
        spec: WorkspaceSpec,
        names: CellResourceNames,
    ) -> None:
        for kind, volume_name in (
            ("workspace", names.workspace_volume),
            ("agent-home", names.agent_home_volume),
            ("postgres", names.postgres_volume),
            ("redis", names.redis_volume),
            ("checkpoints", names.checkpoint_volume),
        ):
            volume = await self.docker.get_volume(volume_name)
            if volume is not None:
                self._verify_volume_record(volume, identity_labels(spec, kind))
        for kind, network_name in (
            ("internal", names.internal_network),
            ("egress", names.egress_network),
        ):
            network = await self.docker.get_network(network_name)
            if network is not None:
                self._verify_network_record(
                    network,
                    identity_labels(spec, kind),
                    internal=True,
                )
        postgres = await self.docker.get_container(names.postgres_container)
        if postgres is not None:
            self._verify_container_record(postgres, self._steady_postgres_spec(spec, names))
        redis = await self.docker.get_container(names.redis_container)
        if redis is not None:
            self._verify_container_record(redis, self._steady_redis_spec(spec, names))

    def _verify_labels(self, actual: dict[str, str], expected: dict[str, str]) -> None:
        if not self._labels_match(actual, expected):
            raise CellIdentityConflict("resource identity mismatch")

    def _verify_volume_record(
        self,
        record: DockerVolumeRecord,
        labels: dict[str, str],
    ) -> None:
        self._verify_labels(record.labels, labels)

    def _verify_network_record(
        self,
        record: DockerNetworkRecord,
        labels: dict[str, str],
        *,
        internal: bool,
    ) -> None:
        self._verify_labels(record.labels, labels)
        if record.internal is not internal:
            raise CellIdentityConflict("resource identity mismatch")

    def _verify_container_record(
        self,
        record: DockerContainerRecord,
        expected: DockerContainerSpec,
    ) -> None:
        self._verify_labels(record.labels, expected.labels)
        if record.image != expected.image:
            raise CellIdentityConflict("resource identity mismatch:image")
        if record.user != expected.user:
            raise CellIdentityConflict("resource identity mismatch:user")
        if sorted(record.cap_add) != sorted(expected.cap_add):
            raise CellIdentityConflict("resource identity mismatch:cap_add")
        if sorted(record.cap_drop) != sorted(expected.cap_drop):
            raise CellIdentityConflict("resource identity mismatch:cap_drop")
        if record.read_only is not expected.read_only:
            raise CellIdentityConflict("resource identity mismatch:read_only")
        if record.privileged is not expected.privileged:
            raise CellIdentityConflict("resource identity mismatch:privileged")
        if sorted(record.security_opt) != sorted(expected.security_opt):
            raise CellIdentityConflict("resource identity mismatch:security_opt")
        if record.ports != expected.ports:
            raise CellIdentityConflict("resource identity mismatch:ports")
        if any(record.env.get(key) != value for key, value in expected.env.items()):
            raise CellIdentityConflict("resource identity mismatch:env")
        unexpected_sensitive_env = set(record.env) - set(expected.env)
        if any(_is_sensitive_env_key(key) for key in unexpected_sensitive_env):
            raise CellIdentityConflict("resource identity mismatch:env")
        if sorted(record.volumes) != sorted(expected.volumes):
            raise CellIdentityConflict("resource identity mismatch:volumes")
        # ``record.mounts`` contains host bind destinations only. Project Cell
        # resources are allowed named volumes and tmpfs, never host bind mounts.
        if record.mounts:
            raise CellIdentityConflict("resource identity mismatch:bind_mounts")
        if sorted(record.network_names) != sorted(expected.network_names):
            raise CellIdentityConflict("resource identity mismatch:network_names")
        if record.helper is not expected.helper:
            raise CellIdentityConflict("resource identity mismatch:helper")
        expected_tmpfs = set(expected.tmpfs)
        if expected.labels.get("omnia.resource_kind", "").startswith("postgres"):
            expected_tmpfs.update({"/var/run/postgresql", "/var/lib/postgresql/data"})
        if set(record.tmpfs) != expected_tmpfs:
            raise CellIdentityConflict("resource identity mismatch:tmpfs")
        if record.pids_limit != expected.pids_limit:
            raise CellIdentityConflict("resource identity mismatch:pids_limit")
        if record.memory_limit_bytes != expected.memory_limit_bytes:
            raise CellIdentityConflict("resource identity mismatch:memory_limit")
        if abs(record.cpu_quota - expected.cpu_quota) > 0.001:
            raise CellIdentityConflict("resource identity mismatch:cpu_quota")

    def _assert_profile_version(self, profile_version: str) -> None:
        if profile_version != self.profile.profile_version:
            raise CellIdentityConflict("workspace profile version mismatch")

    def _is_expected_helper_container(
        self,
        record: DockerContainerRecord,
        spec: WorkspaceSpec,
        names: CellResourceNames,
    ) -> bool:
        kind = record.labels.get("omnia.resource_kind")
        if kind not in _ALLOWED_HELPER_KINDS:
            return False
        if self._labels_match(record.labels, self._helper_labels(spec, kind)) is False:
            return False
        stem = names.postgres_container.rsplit("-postgres", 1)[0]
        if kind in {"postgres-ownership", "postgres-init", "postgres-maintenance"}:
            return self._matches_operation_name(record.name, f"{stem}-{kind}-")
        for source_name in names.retained_volumes:
            prefix = source_name[:48].rstrip("-")
            if record.name == f"{prefix}-{kind}":
                return True
        return False

    def _is_expected_secret_staging_volume(
        self,
        record: DockerVolumeRecord,
        spec: WorkspaceSpec,
        names: CellResourceNames,
    ) -> bool:
        if self._labels_match(record.labels, identity_labels(spec, "secret-staging")) is False:
            return False
        stem = names.postgres_container.rsplit("-postgres", 1)[0]
        return self._matches_operation_name(record.name, f"{stem}-secret-")

    @staticmethod
    def _matches_operation_name(name: str, prefix: str) -> bool:
        if name.startswith(prefix) is False:
            return False
        suffix = name.rsplit("-", 1)[-1]
        return len(suffix) == 12 and all(char in "0123456789abcdef" for char in suffix)

    @staticmethod
    def _labels_match(actual: dict[str, str], expected: dict[str, str]) -> bool:
        return all(actual.get(key) == value for key, value in expected.items())
