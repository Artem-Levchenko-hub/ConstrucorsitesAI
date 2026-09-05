"""Durable publication of a full Project Cell into an independent production identity.

Publication never aliases editable volumes and never restores business data on update.
Secrets are stored only in controller-private files and excluded from status/history.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from omnia_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.schemas.cell_publication import CellDeployRequest
from omnia_orchestrator.schemas.runtime import DeployResponse
from omnia_orchestrator.services import nginx_writer
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.machine_environment import MachineEnvironmentStore
from omnia_orchestrator.services.project_machine import (
    machine_budget,
    machine_effect,
    write_controller_json,
)
from omnia_orchestrator.services.published_machine_backend import (
    PublicationRecoveryRequired,
    PublishedMachineBackend,
    assert_compatible_update,
    data_contract_digest,
    ensure_managed_infrastructure,
    release_volume_mapping,
)

_ACTIVE = {"queued", "building", "swapping"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def publication_root(settings: Any) -> Path:
    return Path(settings.cell_state_path).parent / "cell-publications"


def is_production_workspace(settings: Any, workspace_id: UUID) -> bool:
    """Lifecycle/recovery hook: production reservations cannot be reclaimed as drafts."""
    return (publication_root(settings) / "identities" / f"{workspace_id}.json").is_file()


class CellPublicationService:
    def __init__(
        self, settings: Any = None, *, root: Path | None = None, manager_factory: Any = None
    ) -> None:
        if settings is None:
            from omnia_orchestrator.core.config import get_settings

            settings = get_settings()
        self.settings = settings
        self.root = root if root is not None else publication_root(settings)
        self.manager_factory = manager_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._submission_lock = WorkspaceOperationLock(self.root)

    def _project_path(self, project_id: UUID) -> Path:
        return self.root / str(project_id) / "publication.json"

    def _read(self, project_id: UUID) -> dict[str, Any]:
        path = self._project_path(project_id)
        if not path.exists():
            return {"project_id": str(project_id), "history": [], "active_release": None}
        if path.is_symlink():
            raise CellIdentityConflict("unsafe publication journal")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("project_id") != str(project_id):
            raise CellIdentityConflict("publication project identity mismatch")
        return cast(dict[str, Any], value)

    def _write(self, project_id: UUID, value: dict[str, Any]) -> None:
        write_controller_json(self._project_path(project_id), value)

    def production_identity(self, request: CellDeployRequest) -> UUID:
        return uuid5(NAMESPACE_URL, f"omnia:public-cell:{request.project_id}")

    def get(self, project_id: UUID) -> DeployResponse | None:
        history = self._read(project_id)["history"]
        return DeployResponse.model_validate(history[-1]["response"]) if history else None

    def history(self, project_id: UUID) -> list[DeployResponse]:
        return [
            DeployResponse.model_validate(item["response"])
            for item in reversed(self._read(project_id)["history"])
        ]

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()))

    async def submit(self, request: CellDeployRequest) -> DeployResponse:
        async with self._submission_lock.hold(request.project_id):
            saved = self._read(request.project_id)
            if saved.get("disabled"):
                raise CellIdentityConflict("publication disabled")
            wire = request.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(wire, sort_keys=True).encode()).hexdigest()
            for item in saved["history"]:
                if item["idempotency_key"] == request.idempotency_key:
                    if item["request_digest"] != digest:
                        raise CellIdentityConflict("publication idempotency envelope mismatch")
                    return DeployResponse.model_validate(item["response"])
            if saved["history"] and saved["history"][-1]["response"]["phase"] in _ACTIVE:
                raise CellResourceError("another publication is active")
            if saved.get("owner_id") not in (None, str(request.owner_id)):
                raise CellIdentityConflict("publication owner mismatch")
            if saved.get("slug") not in (None, request.slug):
                raise CellIdentityConflict(
                    "publication hostname change requires explicit migration"
                )
            run_id = str(uuid4())
            response = DeployResponse(
                project_id=request.project_id,
                run_id=run_id,
                snapshot_id=request.snapshot_id,
                commit_sha=request.commit_sha,
                phase="queued",
                started_at=_now(),
                can_cancel=False,
            )
            saved.update(
                owner_id=str(request.owner_id),
                source_workspace_id=str(request.workspace_id),
                production_workspace_id=str(self.production_identity(request)),
                slug=request.slug,
            )
            saved["history"].append(
                {
                    "idempotency_key": request.idempotency_key,
                    "request_digest": digest,
                    "response": response.model_dump(mode="json"),
                }
            )
            # Private durable request is necessary to recover after daemon restart.
            write_controller_json(
                self.root / str(request.project_id) / "requests" / f"{run_id}.json", wire
            )
            self._write(request.project_id, saved)
            task = asyncio.create_task(self._execute(request, run_id))
            self._tasks[run_id] = task
            task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))
            return response

    def _phase(self, project_id: UUID, run_id: str, phase: str, **values: Any) -> None:
        saved = self._read(project_id)
        item = next(item for item in saved["history"] if item["response"]["run_id"] == run_id)
        item["response"].update(phase=phase, **values)
        self._write(project_id, saved)

    def _manager(self, workspace_id: UUID) -> Any:
        if self.manager_factory is not None:
            return self.manager_factory(workspace_id)
        from omnia_orchestrator.routers.workspace import (
            _require_docker_resource_manager,
            _workspace_provider,
        )

        return _require_docker_resource_manager(_workspace_provider(workspace_id))

    def _production_manager(self, source_workspace_id: UUID) -> Any:
        from omnia_orchestrator.services.cell_publication_capacity import production_manager

        return production_manager(self._manager(source_workspace_id), self.settings)

    async def _execute(self, request: CellDeployRequest, run_id: str) -> None:
        try:
            with machine_budget(870):
                async with asyncio.timeout(870):
                    self._phase(request.project_id, run_id, "building")
                    release = await self._prepare(request, run_id)
                    self._phase(request.project_id, run_id, "swapping")
                    await self._activate(request, release)
                    self._phase(
                        request.project_id,
                        run_id,
                        "done",
                        prod_url=release["prod_url"],
                        image_tag=release["image_id"],
                        finished_at=_now(),
                    )
        except BaseException as exc:
            # Raw Docker/SQL exceptions may contain credentials or project data.
            import structlog

            structlog.get_logger("cell_publication").warning(
                "public_release_failed",
                project_id=str(request.project_id),
                run_id=run_id,
                error_type=type(exc).__name__,
                frames=[
                    {
                        "module": Path(frame.filename).name,
                        "function": frame.name,
                        "line": frame.lineno,
                    }
                    for frame in traceback.extract_tb(exc.__traceback__)[-12:]
                ],
            )
            error = (
                "publication_migration_required"
                if str(exc) == "publication_migration_required"
                else f"publication failed ({type(exc).__name__}); retained data were not restored"
            )
            self._phase(request.project_id, run_id, "failed", error=error, finished_at=_now())
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _prepare(self, request: CellDeployRequest, run_id: str) -> dict[str, Any]:
        async with self._submission_lock.hold(request.project_id):
            if self._read(request.project_id).get("disabled"):
                raise CellIdentityConflict("publication disabled")
            return await self._prepare_locked(request, run_id)

    async def _prepare_locked(self, request: CellDeployRequest, run_id: str) -> dict[str, Any]:
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

        manager = self._manager(request.workspace_id)
        adapter = manager.machine_runtime
        if adapter is None:
            raise CellResourceError("portable machine provider unavailable")
        async with manager.operation_lock.hold(request.workspace_id):
            from omnia_orchestrator.services.cell_deletion import require_workspace_not_deleted

            require_workspace_not_deleted(manager.profile.state_path, request.workspace_id)
            source_state = manager.state_store.load(request.workspace_id)
            if (
                source_state is None
                or source_state.project_id != request.project_id
                or source_state.owner_id != request.owner_id
                or source_state.fencing_epoch != request.fencing_epoch
                or source_state.active_generation_run_id is not None
                or source_state.phase != "completed"
                or source_state.bundle_state != "resources_ready"
            ):
                raise CellIdentityConflict("publication source identity or fence changed")
            machine, source = adapter.parts(source_state)
            expected_epoch = request.accepted_fencing_epoch or request.fencing_epoch
            if machine.state().get("epoch") != expected_epoch:
                raise CellIdentityConflict("publication accepted machine epoch changed")
            files = await _read_agent_workspace_files(manager, source.workspace_volume)
            if _workspace_revision(files) != request.source_revision:
                raise CellIdentityConflict("publication source revision changed")
            manifest = MachineManifest.model_validate(machine.state()["manifest"])
            preview = adapter.preview(source_state)
            if preview is None or preview[0] != "running":
                await adapter.resume_preview(source_state)
            source_schema = await machine_effect(PublishedMachineBackend.schema_digest, source)
            try:
                reference = await adapter.checkpoint(source_state)
                if reference is None:
                    raise CellResourceError("publication environment capture missing")
                store = MachineEnvironmentStore(
                    adapter.root / "artifacts",
                    source.workspace_id,
                    source,
                    max_bytes=source.disk_bytes,
                )
                await machine_effect(store.validate, reference, manifest_digest=manifest.digest())
                source.validate_restore_reference(reference)
            finally:
                if not adapter.recovery_required(source_state):
                    await adapter.resume_preview(source_state)
        manager = self._production_manager(request.workspace_id)
        saved = self._read(request.project_id)
        contract = data_contract_digest(manifest)
        release_id = UUID(run_id)
        production_id = self.production_identity(request)
        layout = release_volume_mapping(
            production_id,
            release_id,
            [mount.volume for service in manifest.services for mount in service.mounts],
            namespace=manager.namespace,
        )
        release = {
            "release_id": run_id,
            "image_id": reference.image_id,
            "manifest": manifest.model_dump(mode="json"),
            "layout": layout,
            "schema_digest": source_schema,
            "data_contract_digest": contract,
            "epoch": len(saved["history"]),
            "prod_url": nginx_writer.prod_url(request.slug),
            "source_revision": request.source_revision,
            "snapshot_id": str(request.snapshot_id),
            "resource_profile": asdict(manager.profile),
        }
        active = saved.get("active_release")
        if active is not None:
            state = manager.state_store.load(production_id)
            old = self._backend(manager, state, active)
            actual = await machine_effect(old.schema_digest)
            assert_compatible_update({**active, "schema_digest": actual}, release)
        else:
            marker = {
                "project_id": str(request.project_id),
                "owner_id": str(request.owner_id),
                "production_workspace_id": str(production_id),
                "kind": "public-production",
            }
            write_controller_json(self.root / "identities" / f"{production_id}.json", marker)
            spec = WorkspaceSpec(
                workspace_id=production_id,
                project_id=request.project_id,
                owner_id=request.owner_id,
                profile_version=manager.profile.profile_version,
            )
            operation = LifecycleMutation(
                uuid5(release_id, "admission"), 1, hashlib.sha256(run_id.encode()).hexdigest()
            )
            # Durable independent reservation; never re-admit/release it through a draft lease.
            retained = manager.state_store.load(production_id)
            if retained is None:
                await manager.ensure(spec, operation)
            elif (
                retained.project_id != request.project_id
                or retained.owner_id != request.owner_id
                or retained.bundle_state != "resources_ready"
            ):
                raise CellIdentityConflict("retained production admission requires recovery")
        state = manager.state_store.load(production_id)
        backend = self._backend(manager, state, release)
        metadata = backend._metadata()
        metadata.update(manifest=manifest.model_dump(mode="json"))
        write_controller_json(backend.metadata_path, metadata)
        await machine_effect(
            backend.adopt_source_image,
            reference,
            source,
            store.artifact_path(reference.artifact_ref),
        )
        source_binds = {
            name: bind["bind"] for name, bind in source.volume_mapping(manifest).items()
        }
        target_binds = {
            bind["bind"]: name for name, bind in backend.volume_mapping(manifest).items()
        }
        seeded = bool(saved.get("data_seeded"))
        mapping = {name: target_binds[bind] for name, bind in source_binds.items()}
        mapping[source.project_postgres_volume] = backend.project_postgres_volume
        for volume in reference.volumes:
            target = mapping.get(volume.name)
            if target is None:
                raise CellIdentityConflict("publication artifact has unmapped volume")
            business_data = target == backend.project_postgres_volume or target in [
                value for key, value in layout.items() if key.startswith("data:")
            ]
            if seeded and business_data:
                continue
            await machine_effect(
                backend.seed_volume, target, store.artifact_path(volume.artifact_ref)
            )
        if not seeded and source.project_postgres_volume not in {v.name for v in reference.volumes}:
            raise CellResourceError("publication requires captured dedicated project database")
        release["needs_password_rotation"] = not seeded
        # Source secret never enters public/status journal. Retained privately for first activation.
        if not seeded:
            write_controller_json(
                self.root / str(request.project_id) / "seed-secret.json",
                {"password": source.project_postgres_password},
            )
        saved = self._read(request.project_id)
        saved["prepared_release"] = release
        self._write(request.project_id, saved)
        return release

    def _backend(
        self, manager: Any, state: Any, release: dict[str, Any] | None
    ) -> PublishedMachineBackend:
        if state is None:
            raise CellResourceError("production resource identity missing")
        if release is not None and release.get("resource_profile") != asdict(manager.profile):
            raise CellResourceError("production resource profile changed; readmission required")
        _, template = manager.machine_runtime.parts(state)
        kwargs = {item.name: getattr(template, item.name) for item in fields(DockerMachineBackend)}
        if release is None:
            # An interrupted first seed may own helpers before a release is
            # journaled. Cleanup needs stable cell identity, not a code layout.
            return PublishedMachineBackend(**kwargs)
        kwargs["workspace_volume"] = release["layout"]["workspace"]
        return PublishedMachineBackend(
            **kwargs, release_id=UUID(release["release_id"]), release_layout=release["layout"]
        )

    async def _start(
        self,
        manager: Any,
        state: Any,
        release: dict[str, Any],
        request: CellDeployRequest,
        *,
        switch: bool,
        check_schema: bool = True,
    ) -> PublishedMachineBackend:
        request = self._effective_request(request)
        await ensure_managed_infrastructure(manager, state)
        backend = self._backend(manager, state, release)
        manifest = MachineManifest.model_validate(release["manifest"])
        target_password = backend.project_postgres_password
        if release.get("needs_password_rotation"):
            secret = json.loads(
                (self.root / str(request.project_id) / "seed-secret.json").read_text()
            )
            backend.project_postgres_password = secret["password"]
        method = backend.switch_code if switch else backend.ensure_published
        await machine_effect(method, manifest, release["image_id"], release["epoch"])
        if release.get("needs_password_rotation"):
            source_password = backend.project_postgres_password
            backend.project_postgres_password = target_password
            await machine_effect(backend.rotate_seeded_postgres, source_password)
            # Machine environment initially received source password; recreate after rotation.
            await machine_effect(
                backend.switch_code, manifest, release["image_id"], release["epoch"]
            )
            release["needs_password_rotation"] = False
            saved = self._read(request.project_id)
            saved["data_seeded"] = True
            saved["prepared_release"] = release
            self._write(request.project_id, saved)
        for name in manifest.service_order():
            service = next(service for service in manifest.services if service.name == name)
            await machine_effect(backend.start_service, service, release["epoch"])
            status = await machine_effect(backend.service_status, service, release["epoch"])
            if not status["ready"]:
                raise CellResourceError("public product service readiness failed")
        if check_schema and await machine_effect(backend.schema_digest) != release["schema_digest"]:
            raise CellResourceError("publication startup changed database schema")
        env = {**request.runtime_env, "OMNIA_PUBLIC_APP_ORIGIN": release["prod_url"]}
        await machine_effect(
            manager.machine_runtime._start_boundary,
            state,
            manifest,
            backend,
            release["epoch"],
            public_mode=True,
            runtime_env=env,
            business_config_override=request.business_config,
        )
        return backend

    async def _activate(self, request: CellDeployRequest, release: dict[str, Any]) -> None:
        # Activation/configuration/recovery share one serialization boundary.
        # A revoke racing first publish cannot return while stale credentials
        # are still about to become public. Lock order: project, then production.
        async with self._submission_lock.hold(request.project_id):
            await self._activate_locked(request, release)

    async def _activate_locked(self, request: CellDeployRequest, release: dict[str, Any]) -> None:
        if self._read(request.project_id).get("disabled"):
            raise CellIdentityConflict("publication disabled")
        manager = self._production_manager(request.workspace_id)
        production_id = self.production_identity(request)
        async with manager.operation_lock.hold(production_id):
            saved = self._read(request.project_id)
            old = saved.get("active_release")
            state = manager.state_store.load(production_id)
            if old:
                old_backend = self._backend(manager, state, old)
                actual = await machine_effect(old_backend.schema_digest)
                assert_compatible_update({**old, "schema_digest": actual}, release)
            saved["activation_pending"] = release["release_id"]
            self._write(request.project_id, saved)
            try:
                # The old gateway shares a stable guard IP. Retire it before explicit
                # republish so an unready candidate cannot receive public requests.
                predecessor = self._backend(manager, state, old or release)
                gateway = predecessor._lookup(
                    predecessor.client.containers, predecessor.stem + "-gateway", "max-gateway"
                )
                if gateway is not None:
                    await machine_effect(gateway.remove, force=True)
                backend = await self._start(manager, state, release, request, switch=True)
                gateway = backend._lookup(
                    backend.client.containers, backend.stem + "-gateway", "max-gateway"
                )
                if gateway is None:
                    raise CellResourceError("public gateway missing after readiness")
                gateway.reload()
                address = gateway.attrs["NetworkSettings"]["Networks"][backend.internal_network][
                    "IPAddress"
                ]
                host = nginx_writer.prod_host(request.slug)
                if old is None:
                    await nginx_writer.publish_http(host, 3000, upstream_host=address)
                if not await nginx_writer.ensure_tls(host, 3000, upstream_host=address):
                    raise CellResourceError("public HTTPS activation failed")
                import httpx

                async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                    probe = await client.get(release["prod_url"], headers={"Accept": "text/html"})
                if probe.status_code != 200:
                    raise CellResourceError("public HTTPS bootstrap readiness failed")
                saved = self._read(request.project_id)
                saved["active_release"] = release
                saved["prepared_release"] = None
                saved["activation_pending"] = None
                saved["recovery_required"] = False
                self._write(request.project_id, saved)
            except BaseException as error:
                recovery_required = isinstance(error, PublicationRecoveryRequired) or (
                    str(error) == "publication startup changed database schema"
                )
                failed = self._read(request.project_id)
                failed["recovery_required"] = recovery_required
                self._write(request.project_id, failed)
                if isinstance(error, PublicationRecoveryRequired):
                    # The guest's quiesce contract failed. Its retained machine is
                    # the only evidence of unflushed data; never remove it to roll back.
                    raise
                # A separate 30s cleanup reserve keeps the total request budget at
                # 900s and permits rollback after the 870s work deadline expires.
                with machine_budget(30):
                    await self._rollback_code(
                        manager, state, old, request, recovery_required=recovery_required
                    )
                failed = self._read(request.project_id)
                failed["activation_pending"] = None
                self._write(request.project_id, failed)
                raise

    async def _rollback_code(
        self,
        manager: Any,
        state: Any,
        old: dict[str, Any] | None,
        request: CellDeployRequest,
        *,
        recovery_required: bool,
    ) -> None:
        if old is None:
            await nginx_writer.unpublish(nginx_writer.prod_host(request.slug))
            return
        old_request_path = (
            self.root / str(request.project_id) / "requests" / f"{old['release_id']}.json"
        )
        old_request = CellDeployRequest.model_validate_json(old_request_path.read_text())
        backend = await self._start(
            manager, state, old, old_request, switch=True, check_schema=not recovery_required
        )
        gateway = backend._lookup(
            backend.client.containers, backend.stem + "-gateway", "max-gateway"
        )
        if gateway is None:
            raise CellResourceError("public gateway missing after rollback")
        gateway.reload()
        address = gateway.attrs["NetworkSettings"]["Networks"][backend.internal_network][
            "IPAddress"
        ]
        await nginx_writer.publish_http(
            nginx_writer.prod_host(request.slug), 3000, upstream_host=address
        )

    async def disable(self, project_id: UUID, slug: str) -> None:
        """Retire public ingress durably before API may delete the project row.

        Retained business volumes are deliberately not deleted or restored here.
        Failed cleanup remains retryable; recovery can never re-open the tombstone.
        """
        async with self._submission_lock.hold(project_id):
            if not self._project_path(project_id).exists():
                return
            saved = self._read(project_id)
            if saved.get("slug") != slug:
                raise CellIdentityConflict("publication hostname mismatch")
            saved["disabled"] = True
            saved["disabled_at"] = _now()
            self._write(project_id, saved)
            await nginx_writer.unpublish(nginx_writer.prod_host(slug))
            if saved.get("deletion_completed"):
                return
            source_id = saved.get("source_workspace_id")
            production_id = saved.get("production_workspace_id")
            release = saved.get("active_release") or saved.get("prepared_release")
            if source_id and production_id:
                manager = self._production_manager(UUID(source_id))
                async with manager.operation_lock.hold(UUID(production_id)):
                    state = manager.state_store.load(UUID(production_id))
                    if state is None:
                        await manager.assert_uncreated_workspace(UUID(production_id))
                    else:
                        saved = self._read(project_id)
                        deletion = saved.get("deletion_mutation")
                        if deletion is None:
                            deletion = {
                                "operation_id": str(uuid4()),
                                "fencing_epoch": state.fencing_epoch + 1,
                                "request_digest": hashlib.sha256(
                                    f"public-delete:{project_id}:{production_id}".encode()
                                ).hexdigest(),
                            }
                            saved["deletion_mutation"] = deletion
                            self._write(project_id, saved)
                        mutation = LifecycleMutation(
                            UUID(deletion["operation_id"]),
                            deletion["fencing_epoch"],
                            deletion["request_digest"],
                        )
                        # The preview adapter must never inspect/restore public data.
                        cleanup = replace(manager, machine_runtime=None)
                        await cleanup.prepare_control_operation(
                            UUID(production_id),
                            mutation,
                            kind="destroy",
                        )
                        backend = self._backend(manager, state, release)
                        await machine_effect(backend.retire_compute)
                        await cleanup.destroy_compute_without_lock(
                            UUID(production_id),
                            mutation,
                            checkpoint_ref=None,
                            record_operation=False,
                            capture=False,
                        )
                        cleanup.state_store.complete(
                            UUID(production_id),
                            mutation,
                            phase="completed",
                            provider_ref=state.provider_ref,
                            bundle_state="retained",
                        )
            saved = self._read(project_id)
            saved["deletion_completed"] = True
            self._write(project_id, saved)

    def _effective_request(self, request: CellDeployRequest) -> CellDeployRequest:
        path = self.root / str(request.project_id) / "configuration.json"
        if not path.exists():
            return request
        if path.is_symlink():
            raise CellIdentityConflict("unsafe public configuration")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("owner_id") != str(request.owner_id):
            raise CellIdentityConflict("public configuration owner mismatch")
        update = {}
        if "runtime_env" in value:
            update["runtime_env"] = value["runtime_env"]
        if value.get("business_config_version", 0) >= request.business_config_version:
            update.update(
                business_config=value["business_config"],
                business_config_version=value["business_config_version"],
            )
        return CellDeployRequest.model_validate({**request.model_dump(), **update})

    async def configure(
        self,
        project_id: UUID,
        owner_id: UUID,
        *,
        runtime_env: dict[str, str] | None = None,
        business_config: dict[str, Any] | None = None,
        business_config_version: int | None = None,
    ) -> dict[str, bool]:
        async with self._submission_lock.hold(project_id):
            saved = self._read(project_id)
            if saved.get("disabled"):
                raise CellIdentityConflict("publication disabled")
            if saved.get("owner_id") not in {None, str(owner_id)}:
                raise CellIdentityConflict("publication owner mismatch")
            if not saved.get("history") and not saved.get("active_release"):
                return {"applied": False}
            path = self.root / str(project_id) / "configuration.json"
            if path.is_symlink():
                raise CellIdentityConflict("unsafe public configuration")
            value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if value.get("owner_id") not in {None, str(owner_id)}:
                raise CellIdentityConflict("public configuration owner mismatch")
            value["owner_id"] = str(owner_id)
            if runtime_env is not None:
                value["runtime_env"] = CellDeployRequest.validate_runtime_env(runtime_env)
            if business_config is not None:
                if (
                    business_config_version is None
                    or business_config_version < 1
                    or business_config_version < value.get("business_config_version", 0)
                ):
                    raise CellIdentityConflict("public configuration version is stale")
                if business_config_version == value.get(
                    "business_config_version"
                ) and business_config != value.get("business_config"):
                    raise CellIdentityConflict("public configuration version conflicts")
                value.update(
                    business_config=business_config, business_config_version=business_config_version
                )
            # Independent of immutable code release. Reconciliation and code rollback
            # always use the latest authorized bot/config, including an empty revoke.
            write_controller_json(path, value)
            if saved.get("active_release"):
                await self._refresh_public_configuration(project_id)
                return {"applied": True}
            return {"applied": False}

    async def _refresh_public_configuration(self, project_id: UUID) -> None:
        saved = self._read(project_id)
        source_id = UUID(saved["source_workspace_id"])
        manager = self._production_manager(source_id)
        production_id = UUID(saved["production_workspace_id"])
        async with manager.operation_lock.hold(production_id):
            saved = self._read(project_id)
            release = saved.get("active_release")
            if not release:
                return
            request = self._effective_request(
                CellDeployRequest.model_validate_json(
                    (
                        self.root / str(project_id) / "requests" / f"{release['release_id']}.json"
                    ).read_text(encoding="utf-8")
                )
            )
            state = manager.state_store.load(production_id)
            backend = self._backend(manager, state, release)
            manifest = MachineManifest.model_validate(release["manifest"])
            with machine_budget(240):
                await machine_effect(
                    manager.machine_runtime._start_boundary,
                    state,
                    manifest,
                    backend,
                    release["epoch"],
                    public_mode=True,
                    runtime_env={
                        **request.runtime_env,
                        "OMNIA_PUBLIC_APP_ORIGIN": release["prod_url"],
                    },
                    business_config_override=request.business_config,
                )
                gateway = backend._lookup(
                    backend.client.containers, backend.stem + "-gateway", "max-gateway"
                )
                if gateway is None:
                    raise CellResourceError("public gateway missing after configuration")
                gateway.reload()
                address = gateway.attrs["NetworkSettings"]["Networks"][backend.internal_network][
                    "IPAddress"
                ]
                await nginx_writer.publish_http(
                    nginx_writer.prod_host(request.slug),
                    3000,
                    upstream_host=address,
                    private_cell=True,
                )

    async def reconcile(self) -> list[dict[str, str]]:
        """Restart live publication without source restore, data imports or agent leases."""
        outcomes = []
        for path in self.root.glob("*/publication.json"):
            project_id = UUID(path.parent.name)
            try:
                async with self._submission_lock.hold(project_id):
                    outcome = await self._reconcile_project(path)
                if outcome is not None:
                    outcomes.append(outcome)
            except Exception:
                outcomes.append({"project_id": str(project_id), "state": "recovery_required"})
        return outcomes

    async def _reconcile_project(self, path: Path) -> dict[str, str] | None:
        """Caller holds project lock, matching activation/configuration lock order."""
        project_id = UUID(path.parent.name)
        saved = self._read(project_id)
        if saved.get("disabled") or any(
            item["response"].get("run_id") in self._tasks for item in saved["history"]
        ):
            return None
        for item in saved["history"]:
            if item["response"]["phase"] in _ACTIVE:
                item["response"].update(
                    phase="failed",
                    finished_at=_now(),
                    error="publication interrupted; retained public release reconciled",
                )
        self._write(project_id, saved)
        release = saved.get("active_release")
        if release is None:
            if saved.get("activation_pending"):
                await nginx_writer.unpublish(nginx_writer.prod_host(saved["slug"]))
                saved["activation_pending"] = None
                self._write(project_id, saved)
            return None
        if saved.get("recovery_required"):
            return {"project_id": str(project_id), "state": "recovery_required"}
        request = CellDeployRequest.model_validate_json(
            (path.parent / "requests" / f"{release['release_id']}.json").read_text()
        )
        with machine_budget(870):
            manager = self._production_manager(request.workspace_id)
            production_id = UUID(saved["production_workspace_id"])
            async with manager.operation_lock.hold(production_id):
                state = manager.state_store.load(production_id)
                backend = await self._start(
                    manager, state, release, request, switch=bool(saved.get("activation_pending"))
                )
                gateway = backend._lookup(
                    backend.client.containers, backend.stem + "-gateway", "max-gateway"
                )
                if gateway is None:
                    raise CellResourceError("public gateway missing after recovery")
                gateway.reload()
                address = gateway.attrs["NetworkSettings"]["Networks"][backend.internal_network][
                    "IPAddress"
                ]
                if not await nginx_writer.ensure_tls(
                    nginx_writer.prod_host(request.slug), 3000, upstream_host=address
                ):
                    raise CellResourceError("public HTTPS recovery failed")
                saved = self._read(project_id)
                saved["activation_pending"] = None
                self._write(project_id, saved)
        return {"project_id": str(project_id), "state": "ready"}


_service: CellPublicationService | None = None


def get_cell_publication_service() -> CellPublicationService:
    global _service
    if _service is None:
        _service = CellPublicationService()
    return _service


def start_publication_recovery() -> asyncio.Task[None]:
    """Background liveness; app startup and unrelated projects remain responsive."""

    async def run() -> None:
        import structlog

        log = structlog.get_logger("cell_publication.recovery")
        service = get_cell_publication_service()
        while True:
            try:
                outcomes = await service.reconcile()
                for outcome in outcomes:
                    if outcome["state"] != "ready":
                        log.warning("public_runtime_recovery_required", **outcome)
            except Exception as exc:
                # Exception bodies can include Docker command arguments.
                log.warning("public_runtime_recovery_failed", error_type=type(exc).__name__)
            await asyncio.sleep(30)

    return asyncio.create_task(run(), name="cell-publication-recovery")
