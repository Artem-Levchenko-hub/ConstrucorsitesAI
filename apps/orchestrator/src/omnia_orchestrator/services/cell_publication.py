"""Durable publication of a full Project Cell into an independent production identity.

Publication never aliases editable volumes and never restores business data on update.
Secrets are stored only in controller-private files and excluded from status/history.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
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
from omnia_orchestrator.services.k8s_placement import KubernetesPlacement
from omnia_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
)
from omnia_orchestrator.services.project_machine import (
    machine_budget,
    machine_effect,
    machine_remaining_seconds,
    write_controller_json,
)
from omnia_orchestrator.services.publication_identity import (
    classify_publication,
    fingerprint_key,
    release_fingerprint,
    source_identity,
)
from omnia_orchestrator.services.publication_trace import (
    FORMAT_VERSION,
    PublicationTrace,
    reason_code,
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
# Service commands that may resolve packages at startup and therefore still need
# the package-manager stores; a plain `pnpm start` runs from node_modules alone.
_STORE_USERS = re.compile(
    r"(?:^|[\s/;&|])(?:npx|pnpx)\b"  # always fetch/resolve packages
    r"|(?:^|[\s/;&|])(?:pnpm|npm|yarn|corepack)\b"
    r"[^;&|]*?\b(?:install|add|i|dlx|exec|rebuild|approve-builds)\b"
)


def runtime_needs_package_stores(manifest: MachineManifest) -> bool:
    """P06/P08: a warm release moves the pnpm/corepack stores only when a
    service startup command could touch them (install/exec/dlx). Node modules
    themselves live in the workspace and are always moved."""
    for service in manifest.services:
        if _STORE_USERS.search(" ".join(service.argv)):
            return True
    return False


def warm_volume_names(source: Any, manifest: MachineManifest) -> tuple[str, ...]:
    """Volumes a warm (data already seeded) publication must carry."""
    names = [source.workspace_volume, source.stem + "-home"]
    if runtime_needs_package_stores(manifest):
        names.extend([source.pnpm_cache_volume, source.corepack_cache_volume])
    return tuple(names)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def publication_root(settings: Any) -> Path:
    return Path(settings.cell_state_path).parent / "cell-publications"


class _SealedArtifactReady(Exception):
    """Internal control flow: a sealed artifact replaces the capture block."""


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
        self.heartbeat_seconds = 1.0
        # Phase 3 / stage A: published apps may live in the runtime cluster instead
        # of the Docker bundle on this host. Source capture, journal, locks and the
        # idempotency envelope are shared; only the target side differs.
        self._k8s: KubernetesPlacement | None = None
        self.placement: KubernetesPlacement | None = (
            self._kubernetes()
            if getattr(settings, "publication_backend", "docker") == "kubernetes"
            else None
        )

    def _kubernetes(self) -> KubernetesPlacement:
        """The cluster adapter: the default target when the backend is kubernetes,
        and always available for releases already journaled as placed there."""
        if self._k8s is None:
            self._k8s = KubernetesPlacement(self.settings, root=self.root)
        return self._k8s

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
            if request.restoration_operation_id is None:
                wire.pop("restoration_operation_id")  # Preserve durable legacy retry digests.
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
            # P04: the desired release, not the request, decides the amount of work.
            effective = self._effective_request(request)
            decision = classify_publication(
                effective,
                saved.get("active_release"),
                data_seeded=bool(saved.get("data_seeded")),
                key=fingerprint_key(self.root),
            )
            detail: str | None = None
            if decision.kind == "already_current":
                started = time.monotonic()
                if await self._serving_current(request, saved["active_release"]):
                    return self._record_shortcut(request, digest, "already_current", started)
                # Reconcile through a full release, never a fake success.
                detail = "serving_unhealthy"
            elif decision.kind == "config_only":
                started = time.monotonic()
                self._configure_locked(
                    request.project_id,
                    request.owner_id,
                    runtime_env=effective.runtime_env,
                    business_config=effective.business_config,
                    business_config_version=effective.business_config_version,
                )
                await self._refresh_public_configuration(request.project_id)
                self._stamp_active_release(request.project_id)
                return self._record_shortcut(request, digest, "config_only", started)
            run_id = str(uuid4())
            response = DeployResponse(
                project_id=request.project_id,
                run_id=run_id,
                snapshot_id=request.snapshot_id,
                commit_sha=request.commit_sha,
                phase="queued",
                detail=detail,
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

    def _record_shortcut(
        self, request: CellDeployRequest, digest: str, detail: str, started: float
    ) -> DeployResponse:
        """A finished publication that needed no release: journaled like any
        other run (same idempotency envelope) so replays and history stay honest."""
        saved = self._read(request.project_id)
        active = saved.get("active_release") or {}
        elapsed = round((time.monotonic() - started) * 1000)
        now = _now()
        response = DeployResponse(
            project_id=request.project_id,
            run_id=str(uuid4()),
            snapshot_id=request.snapshot_id,
            commit_sha=request.commit_sha,
            phase="done",
            prod_url=active.get("prod_url"),
            image_tag=active.get("image_id"),
            detail=detail,
            started_at=now,
            finished_at=now,
            can_cancel=False,
            logs=[f"{detail}=1", f"total_ms={elapsed}"],
            format_version=FORMAT_VERSION,
            heartbeat_at=now,
            metrics={"total_ms": elapsed, detail: 1},
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
        self._write(request.project_id, saved)
        return response

    async def _serving_current(self, request: CellDeployRequest, active: dict[str, Any]) -> bool:
        """True only when the active release provably serves right now: the app
        container runs the release image, the gateway is up and the public HTTPS
        address answers. Any doubt means a full publication."""
        if KubernetesPlacement.placed(active):
            return await self._serving_kubernetes(request, active)
        if self.placement is not None:
            return False  # a Docker release is never "current" once the target is the cluster
        try:
            manager = self._production_manager(request.workspace_id)
            state = manager.state_store.load(self.production_identity(request))
            if state is None:
                return False
            backend = self._backend(manager, state, active)

            def observe() -> bool:
                app = backend._container()
                if app is None:
                    return False
                app.reload()
                if app.status != "running" or app.attrs.get("Image") != active.get("image_id"):
                    return False
                gateway = backend._lookup(
                    backend.client.containers, backend.stem + "-gateway", "max-gateway"
                )
                if gateway is None:
                    return False
                gateway.reload()
                return bool(gateway.status == "running")

            with machine_budget(20):
                if not await machine_effect(observe):
                    return False
            import httpx

            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                probe = await client.get(active["prod_url"], headers={"Accept": "text/html"})
            return probe.status_code == 200
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    def _data_seeded(self, saved: dict[str, Any]) -> bool:
        """Business data already lives at the target. Moving a Docker release into
        the cluster starts cold: the cluster has nothing yet, whatever the host has."""
        if not saved.get("data_seeded"):
            return False
        if self.placement is None:
            return True
        return KubernetesPlacement.placed(saved.get("active_release"))

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
        started = time.monotonic()
        trace = PublicationTrace()
        trace.flush = lambda: self._touch(request.project_id, run_id, trace)
        heartbeat = asyncio.create_task(self._heartbeat(request.project_id, run_id, trace))
        try:
            with machine_budget(870):
                async with asyncio.timeout(870):
                    self._phase(request.project_id, run_id, "building", **trace.snapshot())
                    release = await self._prepare(request, run_id, trace)
                    prepared = time.monotonic()
                    trace.end_stage()
                    trace.metrics["prepare_ms"] = round((prepared - started) * 1000)
                    self._phase(
                        request.project_id,
                        run_id,
                        "swapping",
                        logs=[f"prepare_ms={round((prepared - started) * 1000)}"],
                        **trace.snapshot(),
                    )
                    await self._activate(request, release, trace)
                    activated = time.monotonic()
                    trace.end_stage()
                    trace.metrics["activate_ms"] = round((activated - prepared) * 1000)
                    trace.metrics["total_ms"] = round((activated - started) * 1000)
                    self._phase(
                        request.project_id,
                        run_id,
                        "done",
                        prod_url=release["prod_url"],
                        image_tag=release["image_id"],
                        finished_at=_now(),
                        logs=[
                            f"prepare_ms={round((prepared - started) * 1000)}",
                            f"activate_ms={round((activated - prepared) * 1000)}",
                            f"total_ms={round((activated - started) * 1000)}",
                        ],
                        **trace.snapshot(),
                    )
        except BaseException as exc:
            # Raw Docker/SQL exceptions may contain credentials or project data.
            import structlog

            failed_stage = trace.current_stage()
            structlog.get_logger("cell_publication").warning(
                "public_release_failed",
                project_id=str(request.project_id),
                run_id=run_id,
                error_type=type(exc).__name__,
                stage=failed_stage,
                reason_code=reason_code(exc),
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
            trace.end_stage()
            trace.metrics["total_ms"] = round((time.monotonic() - started) * 1000)
            self._phase(
                request.project_id,
                run_id,
                "failed",
                error=error,
                finished_at=_now(),
                error_stage=failed_stage,
                reason_code=reason_code(exc),
                **trace.snapshot(),
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            heartbeat.cancel()

    async def _heartbeat(self, project_id: UUID, run_id: str, trace: PublicationTrace) -> None:
        """Prove liveness while a long stage runs: write progress at most once per
        interval and a plain heartbeat at least every five intervals."""
        last = time.monotonic()
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            if trace.dirty() or time.monotonic() - last >= self.heartbeat_seconds * 5:
                self._touch(project_id, run_id, trace)
                last = time.monotonic()

    def _touch(self, project_id: UUID, run_id: str, trace: PublicationTrace) -> None:
        """Write the trace snapshot into the run's durable response while it is
        still active; a heartbeat must never break the publication it describes."""
        try:
            saved = self._read(project_id)
            item = next(
                (item for item in saved["history"] if item["response"].get("run_id") == run_id),
                None,
            )
            if item is None or item["response"].get("phase") not in _ACTIVE:
                return
            item["response"].update(trace.snapshot())
            self._write(project_id, saved)
        except (OSError, ValueError, KeyError, CellIdentityConflict):
            return

    async def _prepare(
        self, request: CellDeployRequest, run_id: str, trace: PublicationTrace | None = None
    ) -> dict[str, Any]:
        async with self._submission_lock.hold(request.project_id):
            if self._read(request.project_id).get("disabled"):
                raise CellIdentityConflict("publication disabled")
            if trace is None:
                return await self._prepare_locked(request, run_id)
            return await self._prepare_locked(request, run_id, trace)

    async def _prepare_locked(
        self, request: CellDeployRequest, run_id: str, trace: PublicationTrace | None = None
    ) -> dict[str, Any]:
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

        trace = trace or PublicationTrace()
        trace.stage("preflight")
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
            self._verify_restoration_source(request, source)
            manifest = MachineManifest.model_validate(machine.state()["manifest"])
            seeded = self._data_seeded(self._read(request.project_id))
            sealed = self._checkpoint_seal(request, source, adapter, manifest) if seeded else None
            if sealed is not None:
                # P12: the accepted version was packaged at finalization; the
                # editor is not stopped, woken or touched for this publication.
                trace.stage("sealed_artifact")
                source_schema = sealed["schema_digest"]
                reference = sealed["reference"]
                await self._preflight_before_capture(
                    request, manifest, source, adapter, source_schema, trace
                )
                store = MachineEnvironmentStore(
                    adapter.root / "artifacts",
                    source.workspace_id,
                    source,
                    max_bytes=source.disk_bytes,
                )
                store.observer = trace
                await machine_effect(store.validate, reference, manifest_digest=manifest.digest())
            else:
                preview = adapter.preview(source_state)
                if preview is None or preview[0] != "running":
                    trace.stage("source_wake")
                    await adapter.resume_preview(source_state)
                trace.stage("source_schema")
                source_schema = await machine_effect(PublishedMachineBackend.schema_digest, source)
                await self._preflight_before_capture(
                    request, manifest, source, adapter, source_schema, trace
                )
            try:
                if sealed is not None:
                    raise _SealedArtifactReady()
                # Business data already belongs to the live production identity
                # after first publication. A warm code update needs the accepted
                # workspace (with node_modules) and home; package-manager stores
                # only when a startup command may resolve packages. Exporting
                # Postgres, uploads and the disposable Next cache again adds
                # minutes and those archives are deliberately discarded.
                warm_volumes = warm_volume_names(source, manifest)
                capture_volumes = warm_volumes if seeded else None
                reference = await adapter.checkpoint(
                    source_state,
                    volumes=capture_volumes,
                    persist=not seeded,
                    observer=trace,
                )
                if reference is None:
                    raise CellResourceError("publication environment capture missing")
                store = MachineEnvironmentStore(
                    adapter.root / "artifacts",
                    source.workspace_id,
                    source,
                    max_bytes=source.disk_bytes,
                )
                trace.stage(
                    "verify_artifacts",
                    bytes_total=reference.size + sum(volume.size for volume in reference.volumes),
                )
                store.observer = trace
                await machine_effect(store.validate, reference, manifest_digest=manifest.digest())
                if seeded:
                    if reference.workspace_id != source.workspace_id or {
                        volume.name for volume in reference.volumes
                    } != set(warm_volumes):
                        raise CellIdentityConflict("publication workspace capture mismatch")
                else:
                    source.validate_restore_reference(reference)
            except _SealedArtifactReady:
                pass
            finally:
                if sealed is None and not adapter.recovery_required(source_state):
                    trace.stage("resume_source")
                    await adapter.resume_preview(source_state)
        if self.placement is not None:
            return await self._prepare_kubernetes(
                request, run_id, trace, manifest, source, reference, store, source_schema
            )
        trace.stage("prepare_target")
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
            # P04: desired-release identity for the no-op / config-only decision.
            "fingerprint": release_fingerprint(
                self._effective_request(request), fingerprint_key(self.root)
            ),
            "source_identity": source_identity(request),
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
        trace.stage("seed_data")
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
            trace.add_files()
        if seeded:
            await machine_effect(backend.ensure_release_runtime_volumes, manifest)
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

    # ---- P05/P12: sealed release artifact = the last persisted checkpoint ----

    def _checkpoint_seal(
        self, request: CellDeployRequest, source: Any, adapter: Any, manifest: MachineManifest
    ) -> dict[str, Any] | None:
        """The halt after a generation checkpoints the whole environment and
        records the source revision and database schema it captured. When that
        revision is exactly the accepted one being published, the checkpoint's
        warm volumes are the release artifact: no stop, no wake, no export.
        Any doubt (other revision/manifest, missing archive) → capture path."""
        metadata = source._metadata() if hasattr(source, "_metadata") else {}
        raw = metadata.get("environment_ref")
        schema = metadata.get("environment_schema_digest")
        if not raw or not schema or metadata.get("environment_revision") != request.source_revision:
            return None
        try:
            reference = MachineEnvironmentRef.model_validate(raw)
        except ValueError:
            return None
        if (
            reference.workspace_id != source.workspace_id
            or reference.manifest_digest != manifest.digest()
        ):
            return None
        warm = warm_volume_names(source, manifest)
        by_name = {volume.name: volume for volume in reference.volumes}
        if not set(warm) <= set(by_name):
            return None
        base = adapter.root / "artifacts" / str(source.workspace_id)
        refs = [reference.artifact_ref, *(by_name[name].artifact_ref for name in warm)]
        for ref in refs:
            artifact = base / ref
            if artifact.is_symlink() or not artifact.is_file():
                return None
        return {
            "reference": reference.model_copy(
                update={"volumes": tuple(by_name[name] for name in warm)}
            ),
            "schema_digest": str(schema),
        }

    async def _preflight_before_capture(
        self,
        request: CellDeployRequest,
        manifest: MachineManifest,
        source: Any,
        adapter: Any,
        source_schema: str,
        trace: PublicationTrace,
    ) -> None:
        """P03: cheap checks before any export. A release that can no longer be
        applied, or a host that cannot hold the archives, fails here instead of
        after minutes of I/O. Read-only observation: the same compatibility
        comparison is repeated under the activation locks before any effect."""
        trace.stage("preflight_target")
        self._preflight_disk(adapter.root, source)
        saved = self._read(request.project_id)
        old = saved.get("active_release")
        if not old or not saved.get("data_seeded"):
            return
        if self.placement is not None:
            if not KubernetesPlacement.placed(old):
                return  # moving from the host: the cold seed carries its own schema
            actual = await self._kubernetes_schema_digest(request.project_id)
            if actual is not None:
                assert_compatible_update(
                    {**old, "schema_digest": actual},
                    {
                        "schema_digest": source_schema,
                        "data_contract_digest": data_contract_digest(manifest),
                    },
                )
            return
        manager = self._production_manager(request.workspace_id)
        state = manager.state_store.load(self.production_identity(request))
        if state is None:
            return  # no production identity yet: the existing path decides
        actual = await machine_effect(self._backend(manager, state, old).schema_digest)
        assert_compatible_update(
            {**old, "schema_digest": actual},
            {
                "schema_digest": source_schema,
                "data_contract_digest": data_contract_digest(manifest),
            },
        )

    @staticmethod
    def _preflight_disk(root: Path, source: Any) -> None:
        """Export, validation and import need roughly twice the previous package
        on the artifacts filesystem; an unknown package size falls back to 2 GiB."""
        required = 2 * 1024**3
        metadata = source._metadata() if hasattr(source, "_metadata") else {}
        saved_ref = metadata.get("environment_ref")
        if saved_ref:
            try:
                previous = MachineEnvironmentRef.model_validate(saved_ref)
            except ValueError:
                previous = None
            if previous is not None:
                package = previous.size + sum(volume.size for volume in previous.volumes)
                required = max(required, 2 * package)
        probe = root if root.exists() else root.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError:
            return  # cannot measure here; the store's own byte budget still applies
        if free < required:
            raise CellResourceError("publication needs free disk space")

    def _verify_restoration_source(self, request: CellDeployRequest, backend: Any) -> None:
        if request.restoration_operation_id is None:
            return
        from omnia_orchestrator.services.cell_state import _read_plain_json_file

        expected = {
            "operation_id": str(request.restoration_operation_id),
            "workspace_id": str(request.workspace_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
            "candidate_id": str(request.candidate_id),
            "source_commit_sha": request.commit_sha,
            "source_revision": request.source_revision,
            "fencing_epoch": request.accepted_fencing_epoch,
        }
        path = (
            Path(self.settings.cell_state_path).parent
            / "code-restoration-artifacts"
            / str(request.restoration_operation_id)
            / "activation.json"
        )
        if not path.is_file() or path.is_symlink():
            raise CellIdentityConflict("restoration publication activation missing")
        active = _read_plain_json_file(path)
        proof = backend._metadata().get("restoration_proof", {})
        if (
            active.get("state") != "active"
            or not isinstance(proof, dict)
            or any(
                active.get(key) != value or proof.get(key) != value
                for key, value in expected.items()
            )
        ):
            raise CellIdentityConflict("restoration publication activation changed")

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
        trace: PublicationTrace | None = None,
    ) -> PublishedMachineBackend:
        request = self._effective_request(request)
        if trace is not None:
            trace.stage("start_app")
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
        if trace is not None:
            trace.stage("verify_runtime")
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
            **({"observer": trace} if trace is not None else {}),
        )
        return backend

    async def _activate(
        self,
        request: CellDeployRequest,
        release: dict[str, Any],
        trace: PublicationTrace | None = None,
    ) -> None:
        # Activation/configuration/recovery share one serialization boundary.
        # A revoke racing first publish cannot return while stale credentials
        # are still about to become public. Lock order: project, then production.
        async with self._submission_lock.hold(request.project_id):
            if trace is None:
                await self._activate_locked(request, release)
            else:
                await self._activate_locked(request, release, trace)

    async def _activate_locked(
        self,
        request: CellDeployRequest,
        release: dict[str, Any],
        trace: PublicationTrace | None = None,
    ) -> None:
        if self._read(request.project_id).get("disabled"):
            raise CellIdentityConflict("publication disabled")
        trace = trace or PublicationTrace()
        trace.stage("activate")
        if self.placement is not None:
            await self._activate_kubernetes(request, release, trace)
            return
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
                backend = await self._start(
                    manager, state, release, request, switch=True, trace=trace
                )
                gateway = backend._lookup(
                    backend.client.containers, backend.stem + "-gateway", "max-gateway"
                )
                if gateway is None:
                    raise CellResourceError("public gateway missing after readiness")
                gateway.reload()
                address = gateway.attrs["NetworkSettings"]["Networks"][backend.internal_network][
                    "IPAddress"
                ]
                trace.stage("tls")
                host = nginx_writer.prod_host(request.slug)
                if old is None:
                    await nginx_writer.publish_http(host, 3000, upstream_host=address)
                if not await nginx_writer.ensure_tls(host, 3000, upstream_host=address):
                    raise CellResourceError("public HTTPS activation failed")
                trace.stage("observe")
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
            release = saved.get("active_release") or saved.get("prepared_release")
            if KubernetesPlacement.placed(release):
                if not saved.get("deletion_completed"):
                    # Ingress and workloads go; data claims stay retained.
                    with machine_budget(120):
                        await machine_effect(self._kubernetes().runtime.retire, project_id)
                    saved = self._read(project_id)
                    saved["deletion_completed"] = True
                    self._write(project_id, saved)
                return
            await nginx_writer.unpublish(nginx_writer.prod_host(slug))
            if saved.get("deletion_completed"):
                return
            await self._retire_docker_production(project_id, saved, release)
            saved = self._read(project_id)
            saved["deletion_completed"] = True
            self._write(project_id, saved)

    async def _retire_docker_production(
        self, project_id: UUID, saved: dict[str, Any], release: dict[str, Any] | None
    ) -> None:
        """Delete the host-side serving compute of a publication and release its
        admission; retained business volumes are deliberately kept."""
        source_id = saved.get("source_workspace_id")
        production_id = saved.get("production_workspace_id")
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
            applied = self._configure_locked(
                project_id,
                owner_id,
                runtime_env=runtime_env,
                business_config=business_config,
                business_config_version=business_config_version,
            )
            if applied:
                await self._refresh_public_configuration(project_id)
                self._stamp_active_release(project_id)
                return {"applied": True}
            return {"applied": False}

    def _stamp_active_release(self, project_id: UUID) -> None:
        """The configuration just applied is part of the serving identity (P04):
        recompute the active release's fingerprint from its durable request plus
        the current configuration, so an identical request is a no-op next time."""
        saved = self._read(project_id)
        active = saved.get("active_release")
        if not active or not active.get("release_id"):
            return
        path = self.root / str(project_id) / "requests" / f"{active['release_id']}.json"
        if not path.is_file() or path.is_symlink():
            return
        request = self._effective_request(
            CellDeployRequest.model_validate_json(path.read_text(encoding="utf-8"))
        )
        active["fingerprint"] = release_fingerprint(request, fingerprint_key(self.root))
        active["source_identity"] = source_identity(request)
        self._write(project_id, saved)

    def _configure_locked(
        self,
        project_id: UUID,
        owner_id: UUID,
        *,
        runtime_env: dict[str, str] | None,
        business_config: dict[str, Any] | None,
        business_config_version: int | None,
    ) -> bool:
        """Durable configuration write under the project lock; returns whether a
        live release exists that must now be refreshed."""
        saved = self._read(project_id)
        if saved.get("disabled"):
            raise CellIdentityConflict("publication disabled")
        if saved.get("owner_id") not in {None, str(owner_id)}:
            raise CellIdentityConflict("publication owner mismatch")
        if not saved.get("history") and not saved.get("active_release"):
            return False
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
        return bool(saved.get("active_release"))

    async def _refresh_public_configuration(self, project_id: UUID) -> None:
        saved = self._read(project_id)
        if KubernetesPlacement.placed(saved.get("active_release")):
            await self._refresh_kubernetes(project_id)
            return
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
        if KubernetesPlacement.placed(release) or (release is None and self.placement is not None):
            return await self._reconcile_kubernetes(project_id, path)
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

    # ---- Phase 3 / stage A: the release lives in the runtime cluster ----------

    async def _kubernetes_schema_digest(self, project_id: UUID) -> str | None:
        """Live schema of the published project database, None when nothing is placed."""
        placement = self._kubernetes()
        runtime = placement.runtime
        status = await machine_effect(runtime.status, project_id)
        if not status["present"]:
            return None
        password = placement.project_password(project_id)
        return cast(
            str,
            await machine_effect(runtime.schema_digest, placement.namespace(project_id), password),
        )

    async def _prepare_kubernetes(
        self,
        request: CellDeployRequest,
        run_id: str,
        trace: PublicationTrace,
        manifest: MachineManifest,
        source: Any,
        reference: Any,
        store: Any,
        source_schema: str,
    ) -> dict[str, Any]:
        """Target side of `_prepare_locked` for the cluster: no host admission, no
        Docker volumes. The release image is pushed now; the warm archives stay on
        this host and are handed to the seeding init containers at activation."""
        placement = self._kubernetes()
        trace.stage("prepare_target")
        saved = self._read(request.project_id)
        seeded = self._data_seeded(saved)
        production_id = self.production_identity(request)
        release: dict[str, Any] = {
            "release_id": run_id,
            "image_id": reference.image_id,
            "manifest": manifest.model_dump(mode="json"),
            "layout": {},
            "schema_digest": source_schema,
            "data_contract_digest": data_contract_digest(manifest),
            "epoch": len(saved["history"]),
            "prod_url": placement.prod_url(request.slug),
            "source_revision": request.source_revision,
            "snapshot_id": str(request.snapshot_id),
            "resource_profile": {"backend": "kubernetes"},
            "fingerprint": release_fingerprint(
                self._effective_request(request), fingerprint_key(self.root)
            ),
            "source_identity": source_identity(request),
            "needs_password_rotation": False,
        }
        active = saved.get("active_release")
        if KubernetesPlacement.placed(active):
            actual = await self._kubernetes_schema_digest(request.project_id)
            if actual is None:
                raise CellResourceError("production volume missing; explicit recovery required")
            assert_compatible_update({**dict(active or {}), "schema_digest": actual}, release)
        elif active is None:
            write_controller_json(
                self.root / "identities" / f"{production_id}.json",
                {
                    "project_id": str(request.project_id),
                    "owner_id": str(request.owner_id),
                    "production_workspace_id": str(production_id),
                    "kind": "public-production",
                    "placement": "kubernetes",
                },
            )
        trace.stage("push_image")
        images = await machine_effect(
            placement.push_release_images,
            source.client,
            reference,
            provenance=source.labels("environment"),
            archive=store.artifact_path(reference.artifact_ref),
            project_id=request.project_id,
            release_id=run_id,
        )
        trace.stage("seed_data")
        plan = placement.seed_plan(source, manifest, reference, store, seeded=seeded)
        # Source secret never enters public/status journal; retained privately.
        placement.project_password(request.project_id, None if seeded else source)
        release["placement"] = {
            "backend": "kubernetes",
            "namespace": placement.namespace(request.project_id),
            "public_host": placement.public_host(request.slug),
            **images,
            "seeds": plan,
        }
        saved = self._read(request.project_id)
        saved["prepared_release"] = release
        self._write(request.project_id, saved)
        return release

    def _kubernetes_spec(
        self, request: CellDeployRequest, release: dict[str, Any], *, present: bool
    ) -> Any:
        placement = self._kubernetes()
        request = self._effective_request(request)
        manager = self._manager(request.workspace_id)
        adapter = manager.machine_runtime
        if adapter is None:
            raise CellResourceError("portable machine provider unavailable")
        production_id = self.production_identity(request)
        return placement.build_spec(
            request,
            release,
            seeds=placement.seeds(release["placement"]["seeds"], present=present),
            boundary_secret=placement.auth_secret(
                adapter, production_id, dict(request.runtime_env)
            ),
            project_postgres_password=placement.project_password(request.project_id),
            core_postgres_password=manager.credential_store.load_or_create(
                production_id
            ).postgres_password,
        )

    async def _probe_public(self, url: str, *, timeout_seconds: float) -> None:
        """The cluster issues the certificate after the ingress appears; keep
        asking until the public address answers 200 or the budget is gone."""
        import httpx

        deadline = time.monotonic() + timeout_seconds
        last = "no response"
        while True:
            try:
                async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                    probe = await client.get(url, headers={"Accept": "text/html"})
                if probe.status_code == 200:
                    return
                last = f"HTTP {probe.status_code}"
            except httpx.HTTPError as error:
                last = type(error).__name__
            if time.monotonic() >= deadline:
                raise CellResourceError(
                    "public HTTPS bootstrap readiness failed"
                ) from RuntimeError(last)
            await asyncio.sleep(5)

    async def _activate_kubernetes(
        self, request: CellDeployRequest, release: dict[str, Any], trace: PublicationTrace
    ) -> None:
        runtime = self._kubernetes().runtime
        project_id = request.project_id
        saved = self._read(project_id)
        old = saved.get("active_release")
        if KubernetesPlacement.placed(old):
            actual = await self._kubernetes_schema_digest(project_id)
            if actual is None:
                raise CellResourceError("production volume missing; explicit recovery required")
            assert_compatible_update({**dict(old or {}), "schema_digest": actual}, release)
        saved["activation_pending"] = release["release_id"]
        self._write(project_id, saved)
        try:
            spec = self._kubernetes_spec(request, release, present=False)
            trace.stage("start_app")
            await machine_effect(runtime.publish, spec)
            trace.stage("verify_runtime")
            actual = await machine_effect(
                runtime.schema_digest, spec.namespace, spec.project_postgres_password
            )
            if actual != release["schema_digest"]:
                raise CellResourceError("publication startup changed database schema")
            trace.stage("observe")
            await self._probe_public(
                release["prod_url"], timeout_seconds=machine_remaining_seconds(300)
            )
            saved = self._read(project_id)
            saved["active_release"] = release
            saved["prepared_release"] = None
            saved["activation_pending"] = None
            saved["recovery_required"] = False
            saved["data_seeded"] = True
            self._write(project_id, saved)
            # The cluster release is live already: what follows is housekeeping
            # (logged, never a reason to roll it back).
            if old is not None and not KubernetesPlacement.placed(old):
                # The app moved from this host into the cluster: its Docker serving
                # compute is retired now (data volumes retained), the vhost released.
                try:
                    await nginx_writer.unpublish(nginx_writer.prod_host(request.slug))
                    with machine_budget(120):
                        await self._retire_docker_production(project_id, saved, None)
                except Exception as error:
                    import structlog

                    structlog.get_logger("cell_publication").warning(
                        "docker_release_not_retired_after_move",
                        project_id=str(project_id),
                        error_type=type(error).__name__,
                    )
            # Code claims of retired releases — and of interrupted earlier attempts.
            try:
                await machine_effect(
                    runtime.prune_release_volumes, project_id, release["release_id"]
                )
            except Exception as error:
                import structlog

                structlog.get_logger("cell_publication").warning(
                    "k8s_release_volumes_not_pruned",
                    project_id=str(project_id),
                    error_type=type(error).__name__,
                )
        except BaseException as error:
            recovery_required = str(error) == "publication startup changed database schema"
            failed = self._read(project_id)
            failed["recovery_required"] = recovery_required
            self._write(project_id, failed)
            # Rolling the cluster back re-applies the previous release's objects and
            # waits for them; it needs its own reserve beyond the work deadline.
            with machine_budget(180):
                await self._rollback_kubernetes(old, request)
            failed = self._read(project_id)
            failed["activation_pending"] = None
            self._write(project_id, failed)
            raise

    async def _rollback_kubernetes(
        self, old: dict[str, Any] | None, request: CellDeployRequest
    ) -> None:
        runtime = self._kubernetes().runtime
        if not KubernetesPlacement.placed(old):
            # Nothing was ever live in the cluster (a Docker release, if any, is
            # still serving on this host): seeded data would shadow the next attempt.
            await machine_effect(runtime.destroy, request.project_id)
            return
        assert old is not None
        old_request = CellDeployRequest.model_validate_json(
            (
                self.root / str(request.project_id) / "requests" / f"{old['release_id']}.json"
            ).read_text(encoding="utf-8")
        )
        await machine_effect(runtime.publish, self._kubernetes_spec(old_request, old, present=True))

    async def _refresh_kubernetes(self, project_id: UUID) -> None:
        saved = self._read(project_id)
        release = saved.get("active_release")
        if not release:
            return
        request = CellDeployRequest.model_validate_json(
            (self.root / str(project_id) / "requests" / f"{release['release_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        # Secrets and rollout annotations change; the app's claims and image do not,
        # so only the core and the boundary restart.
        with machine_budget(240):
            await machine_effect(
                self._kubernetes().runtime.publish,
                self._kubernetes_spec(request, release, present=True),
            )

    async def _serving_kubernetes(self, request: CellDeployRequest, active: dict[str, Any]) -> bool:
        adapter = self._kubernetes()
        try:
            placement = active.get("placement") or {}
            if placement.get("public_host") != adapter.public_host(request.slug):
                return False  # public suffix changed: the app must move to its new address
            with machine_budget(20):
                status = await machine_effect(adapter.runtime.status, request.project_id)
            if (
                not status["present"]
                or not all(status["ready"].values())
                or status["app_image"] != placement.get("app_image")
            ):
                return False
            import httpx

            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                probe = await client.get(active["prod_url"], headers={"Accept": "text/html"})
            return probe.status_code == 200
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    async def _reconcile_kubernetes(self, project_id: UUID, path: Path) -> dict[str, str] | None:
        """Caller holds the project lock. The cluster restarts pods by itself; this
        only restores objects that vanished and settles an interrupted activation."""
        runtime = self._kubernetes().runtime
        saved = self._read(project_id)
        release = saved.get("active_release")
        if release is None:
            if saved.get("activation_pending"):
                with machine_budget(120):
                    await machine_effect(runtime.destroy, project_id)
                saved["activation_pending"] = None
                self._write(project_id, saved)
            return None
        if saved.get("recovery_required"):
            return {"project_id": str(project_id), "state": "recovery_required"}
        with machine_budget(870):
            status = await machine_effect(runtime.status, project_id)
            placement = release.get("placement") or {}
            settled = (
                status["present"]
                and all(status["ready"].values())
                and status["app_image"] == placement.get("app_image")
            )
            if not settled or saved.get("activation_pending"):
                request = CellDeployRequest.model_validate_json(
                    (path.parent / "requests" / f"{release['release_id']}.json").read_text()
                )
                await machine_effect(
                    runtime.publish, self._kubernetes_spec(request, release, present=True)
                )
                await machine_effect(
                    runtime.prune_release_volumes, project_id, release["release_id"]
                )
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
