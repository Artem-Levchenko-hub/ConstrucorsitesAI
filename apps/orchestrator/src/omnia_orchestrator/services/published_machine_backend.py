"""Independent production machine with immutable release code and durable live data.

Full environment restore belongs to disaster recovery, never publication or wake.
The only archive-import entry point requires fresh unpublished target volumes.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from omnia_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    identity_labels,
)
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.machine_environment import MachineEnvironmentRef
from omnia_orchestrator.services.project_machine import (
    machine_remaining_seconds,
    write_controller_json,
)


class PublicationRecoveryRequired(CellResourceError):
    """Live data safety is uncertain; retain runtime and require explicit recovery."""


def release_volume_mapping(
    production_id: UUID,
    release_id: UUID,
    data_names: list[str],
    *,
    namespace: str = "prod",
) -> dict[str, str]:
    if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", name) is None for name in data_names):
        raise CellIdentityConflict("invalid declared publication volume")
    prefix = "omnia-machine-test" if namespace == "test" else "omnia-machine"
    stem = f"{prefix}-{production_id.hex}"
    release = f"{stem}-release-{release_id.hex}"
    return {
        "workspace": release + "-source",
        "home": release + "-home",
        "pnpm": release + "-pnpm",
        "corepack": release + "-corepack",
        "next": release + "-next",
        "postgres": stem + "-app-postgres-data",
        **{"data:" + name: stem + "-data-" + name for name in sorted(set(data_names))},
    }


def data_contract_digest(manifest: MachineManifest) -> str:
    value = {
        "mounts": sorted(
            (mount.volume, mount.target)
            for service in manifest.services
            for mount in service.mounts
        ),
        "stores": [store.model_dump(mode="json") for store in manifest.data_stores],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def assert_compatible_update(previous: dict[str, Any], candidate: dict[str, Any]) -> None:
    if any(
        previous.get(key) != candidate.get(key) for key in ("schema_digest", "data_contract_digest")
    ):
        raise CellResourceError("publication_migration_required")


async def ensure_managed_infrastructure(manager: Any, state: Any) -> None:
    """Recreate owned compute/networks; never initialize or restore live volumes."""
    names = state.resource_names
    if names is None:
        raise CellResourceError("production managed resource identity missing")
    spec = WorkspaceSpec(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        owner_id=state.owner_id,
        profile_version=state.profile_version,
    )
    for kind, name in (("postgres", names.postgres_volume), ("redis", names.redis_volume)):
        volume = await manager.docker.get_volume(name)
        if volume is None:
            raise CellResourceError("production managed volume missing; explicit recovery required")
        manager._verify_volume_record(volume, identity_labels(spec, kind))
    for kind, name in (("internal", names.internal_network), ("egress", names.egress_network)):
        await manager._ensure_network(name, identity_labels(spec, kind), internal=True)
    for container_spec in (
        manager._steady_postgres_spec(spec, names),
        manager._steady_redis_spec(spec, names),
    ):
        existing = await manager.docker.get_container(container_spec.name)
        if existing is None:
            await manager.docker.create_container(container_spec)
        else:
            manager._verify_container_record(existing, container_spec)
        if existing is None or existing.state != "running":
            await manager.docker.start_container(container_spec.name)


def send_exec_stdin(client: Any, container: Any, argv: list[str], payload: bytes) -> None:
    execution = client.api.exec_create(container.id, argv, stdin=True)
    connection = client.api.exec_start(execution["Id"], socket=True)
    try:
        connection._sock.settimeout(machine_remaining_seconds(60))
        connection._sock.sendall(payload)
        connection._sock.shutdown(socket.SHUT_WR)
        while connection._sock.recv(65536):
            connection._sock.settimeout(machine_remaining_seconds(60))
    finally:
        connection.close()
    result = client.api.exec_inspect(execution["Id"])
    if result.get("Running") or result.get("ExitCode") != 0:
        raise CellResourceError("trusted publication database operation failed")


@dataclass
class PublishedMachineBackend(DockerMachineBackend):
    release_id: UUID | None = None
    release_layout: dict[str, str] = field(default_factory=dict)

    def retire_compute(self) -> None:
        """Delete serving compute after durable disable; keep all data/backups."""
        for suffix, kind in (("gateway", "max-gateway"), ("max-core", "managed-max-core")):
            container = self._lookup(self.client.containers, self.stem + "-" + suffix, kind)
            if container is not None:
                container.remove(force=True)
        self._reconcile_recovery_helpers()
        self.quiesce_current()
        self.stop()
        self.remove()
        for suffix, kind in (("guard", "namespace-guard"), ("proxy", "egress-proxy")):
            container = self._lookup(self.client.containers, self.stem + "-" + suffix, kind)
            if container is not None:
                container.remove(force=True)
        network = self._lookup(self.client.networks, self.stem + "-public", "public-egress")
        if network is not None:
            network.remove()
        # Include unknown machine/helper kinds: do not release admission while
        # any compute in this production identity survives a partial deletion.
        selector = [
            "omnia.project_machine=true",
            f"omnia.workspace_id={self.workspace_id}",
            f"omnia.namespace={self.namespace}",
        ]
        if self.client.containers.list(all=True, filters={"label": selector}):
            raise CellResourceError("production compute removal was not confirmed")

    @property
    def metadata_path(self) -> Path:
        if self.release_id is None:
            raise CellIdentityConflict("publication release identity missing")
        return (
            self.root / str(self.workspace_id) / "releases" / str(self.release_id) / "docker.json"
        )

    def container_options(
        self, manifest: MachineManifest, namespace_id: str, epoch: int
    ) -> dict[str, Any]:
        options = super().container_options(manifest, namespace_id, epoch)
        options["labels"]["omnia.public_release_id"] = str(self.release_id)
        return options

    def restart_infrastructure(self) -> None:
        for suffix, kind in (
            ("proxy", "egress-proxy"),
            ("guard", "namespace-guard"),
            ("project-postgres", "project-postgres"),
        ):
            container = self._lookup(self.client.containers, self.stem + "-" + suffix, kind)
            if container is None:
                continue
            container.reload()
            if container.status == "running":
                continue
            started_at = datetime.now(UTC)
            container.start()
            if kind == "namespace-guard":
                expected = ("POLICY_READY=" + container.labels["omnia.policy_digest"]).encode()
                deadline = time.monotonic() + machine_remaining_seconds(30)
                while time.monotonic() < deadline:
                    container.reload()
                    if container.status != "running":
                        raise CellResourceError("public namespace guard restart failed")
                    if expected in container.logs(since=started_at, tail=10):
                        break
                    time.sleep(0.05)
                else:
                    raise CellResourceError("public namespace guard restart readiness failed")
            elif kind == "project-postgres":
                self._wait_project_postgres_ready(container)

    def quiesce_current(self) -> None:
        current = self._container()
        if current is None:
            return
        current.reload()
        try:
            release_id = UUID(current.attrs["Config"]["Labels"]["omnia.public_release_id"])
        except (KeyError, ValueError) as exc:
            raise CellIdentityConflict("running production release identity missing") from exc
        old = replace(self, release_id=release_id)
        metadata = old._metadata()
        layout = metadata.get("release_layout")
        if not isinstance(layout, dict) or not isinstance(layout.get("workspace"), str):
            raise CellIdentityConflict("running production release layout missing")
        old.workspace_volume = layout["workspace"]
        old.release_layout = layout
        # A cold machine awaiting credential rotation has no product processes.
        if metadata.get("services"):
            try:
                old.prepare_capture()
            except Exception as exc:
                raise PublicationRecoveryRequired("production data quiesce failed") from exc
        old.stop()

    def volume_mapping(self, manifest: MachineManifest) -> dict[str, dict[str, str]]:
        layout = self.release_layout
        if not layout or layout.get("workspace") != self.workspace_volume:
            raise CellIdentityConflict("production release layout is missing")
        result = {
            layout["workspace"]: {"bind": "/workspace", "mode": "rw"},
            layout["home"]: {"bind": "/root", "mode": "rw"},
            layout["pnpm"]: {"bind": "/pnpm/store", "mode": "rw"},
            layout["corepack"]: {"bind": "/root/.cache/node/corepack", "mode": "rw"},
            layout["next"]: {"bind": "/workspace/.next/cache", "mode": "rw"},
        }
        for service in manifest.services:
            for mount in service.mounts:
                name = layout["data:" + mount.volume]
                if name in result and result[name]["bind"] != mount.target:
                    raise CellIdentityConflict("production volume mount conflict")
                result[name] = {"bind": mount.target, "mode": "rw"}
        return result

    def adopt_source_image(
        self, reference: MachineEnvironmentRef, source: DockerMachineBackend, artifact_path: Path
    ) -> None:
        if (
            source.project_id != self.project_id
            or source.owner_id != self.owner_id
            or source.workspace_id == self.workspace_id
            or reference.workspace_id != source.workspace_id
        ):
            raise CellIdentityConflict("publication source identity mismatch")
        with artifact_path.open("rb") as handle:
            images = self.client.images.load(handle)
        if len(images) != 1 or images[0].id != reference.image_id:
            raise CellIdentityConflict("publication image digest mismatch")
        config = images[0].attrs.get("Config") or {}
        if config.get("Env") or config.get("Entrypoint") or config.get("Cmd"):
            raise CellIdentityConflict("publication image contains runtime configuration")
        labels = config.get("Labels") or {}
        if any(labels.get(key) != value for key, value in source.labels("environment").items()):
            raise CellIdentityConflict("publication image provenance mismatch")

    def seed_volume(self, destination: str, artifact_path: Path) -> None:
        """Only caller-owned fresh staging volumes; repeated seed never clears data."""
        if destination not in self.release_layout.values():
            raise CellIdentityConflict("publication volume not in controller layout")
        if self._lookup(self.client.volumes, destination, "project-volume") is not None:
            raise CellIdentityConflict("publication seed destination already exists")
        self._volume(destination)
        self.import_volume(destination, artifact_path)

    def assert_live_volumes(self, manifest: MachineManifest) -> None:
        for name in self.environment_volume_names(manifest):
            if self._lookup(self.client.volumes, name, "project-volume") is None:
                raise CellResourceError("production volume missing; explicit recovery required")

    def switch_code(self, manifest: MachineManifest, image_id: str, epoch: int) -> None:
        self.assert_live_volumes(manifest)
        # Do not call checkpoint/restore here: a public write may postdate any snapshot.
        self.quiesce_current()
        self.remove()
        metadata = self._metadata()
        metadata.update(
            manifest=manifest.model_dump(mode="json"),
            restored_image=image_id,
            epoch=epoch,
            services={},
            exec_logs={},
            exec_pids={},
            quiesce_state=None,
            restore_in_progress=False,
            release_layout=self.release_layout,
        )
        write_controller_json(self.metadata_path, metadata)
        self.restart_infrastructure()
        self.ensure(manifest, epoch)

    def ensure_published(self, manifest: MachineManifest, image_id: str, epoch: int) -> None:
        self.assert_live_volumes(manifest)
        self.restart_infrastructure()
        container = self._container()
        if container is not None:
            container.reload()
            expected_mounts = {
                name: (value["bind"], value["mode"] == "rw")
                for name, value in self.volume_mapping(manifest).items()
            }
            expected_mounts[self.stem + "-logs"] = ("/run/omnia-logs", True)
            actual_mounts = {
                value.get("Name"): (value.get("Destination"), value.get("RW"))
                for value in container.attrs.get("Mounts", [])
                if value.get("Name")
            }
            if (
                container.attrs.get("Image") != image_id
                or container.attrs.get("Config", {}).get("Labels", {}).get("omnia.fencing_epoch")
                != str(epoch)
                or actual_mounts != expected_mounts
            ):
                raise CellIdentityConflict(
                    "production physical runtime differs from accepted release"
                )
            metadata = self._metadata()
            if (
                metadata.get("epoch") != epoch
                or metadata.get("restored_image") != image_id
                or metadata.get("manifest") != manifest.model_dump(mode="json")
            ):
                raise CellIdentityConflict("production runtime differs from accepted release")
            if container.status == "running":
                return
        else:
            metadata = self._metadata()
            metadata.update(
                manifest=manifest.model_dump(mode="json"),
                restored_image=image_id,
                epoch=epoch,
                quiesce_state=None,
                restore_in_progress=False,
                release_layout=self.release_layout,
            )
            write_controller_json(self.metadata_path, metadata)
        self.ensure(manifest, epoch)

    def rotate_seeded_postgres(self, source_password: str) -> None:
        """New physical copy carries source roles; rotate admin before public activation."""
        actual_password = self.project_postgres_password
        try:
            self.project_postgres_password = source_password
            postgres = self._project_postgres()
            if postgres is None:
                raise CellResourceError("seeded production postgres is not running")
            sql = "ALTER ROLE postgres PASSWORD '" + actual_password.replace("'", "''") + "';\n"
            # Credentials are passed through stdin, never command arguments or logs.
            # postgres images do not promise Python. Use pgpass via exec environment;
            # SQL still travels only over the attached stdin stream.
            execution = self.client.api.exec_create(
                postgres.id,
                [
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                ],
                stdin=True,
                environment={"PGPASSWORD": source_password},
            )
            connection = self.client.api.exec_start(execution["Id"], socket=True)
            try:
                connection._sock.settimeout(machine_remaining_seconds(30))
                connection._sock.sendall(sql.encode())
                connection._sock.shutdown(socket.SHUT_WR)
                while connection._sock.recv(8192):
                    pass
            finally:
                connection.close()
            outcome = self.client.api.exec_inspect(execution["Id"])
            if outcome.get("Running") or outcome.get("ExitCode") != 0:
                raise CellResourceError("production database credential rotation failed")
        finally:
            self.project_postgres_password = actual_password
        self._wait_project_postgres_ready(postgres)

    def schema_digest(self) -> str:
        postgres = self._project_postgres()
        if postgres is None:
            raise CellResourceError("production schema probe requires postgres")
        result = postgres.exec_run(
            [
                "pg_dumpall",
                "--schema-only",
                "--no-role-passwords",
                "--no-owner",
                "--no-privileges",
                "-h",
                "127.0.0.1",
                "-U",
                "postgres",
            ],
            environment={"PGPASSWORD": self.project_postgres_password},
        )
        if result.exit_code != 0 or not result.output:
            raise CellResourceError("publication schema inspection failed")
        # pg_dump 17.6+ random restriction keys are not schema changes.
        lines = [
            line
            for line in result.output.decode().splitlines()
            if not line.startswith(("\\restrict ", "\\unrestrict ", "--"))
        ]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()
