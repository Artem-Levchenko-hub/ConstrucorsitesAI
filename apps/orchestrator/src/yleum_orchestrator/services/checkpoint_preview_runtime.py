"""Build and serve a saved source tree in a private, separately fenced Project Cell."""

from __future__ import annotations

import json
import shlex
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from yleum_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
from yleum_orchestrator.routers import workspace as ws
from yleum_orchestrator.routers.runtime import _workspace_revision
from yleum_orchestrator.schemas.workspace import (
    WorkspaceAgentExecRequest,
    WorkspaceAgentPrepareRequest,
    WorkspaceDraftPreviewSessionResponse,
)
from yleum_orchestrator.services import nginx_writer
from yleum_orchestrator.services.active_source_volume import source_volume_call
from yleum_orchestrator.services.app_business_config import app_config_envelope
from yleum_orchestrator.services.cell_state import (
    CellWorkspaceState,
    _ensure_secure_dir,
    _read_plain_json_file,
    _require_regular_file_path,
)
from yleum_orchestrator.services.docker_cell_resources import DockerCellResourceManager
from yleum_orchestrator.services.machine_adapter import MachineAdapter
from yleum_orchestrator.services.project_machine import (
    MachineOperationResult,
    machine_effect,
    write_controller_json,
)


class PreviewProcessStopped(CellResourceError):
    pass


async def _migrate_empty_database(
    runtime: MachineAdapter,
    state: CellWorkspaceState,
    manifest: MachineManifest,
    request: WorkspaceAgentExecRequest,
) -> None:
    """The migration journal requires its own fenced parent, even for a script-only app."""
    machine, _ = runtime.parts(state)
    mutation = LifecycleMutation(
        request.operation_id, request.fencing_epoch, runtime._request_digest(manifest, request)
    )
    await machine.ensure(manifest, mutation)
    result = await machine.request_start(
        mutation,
        phase="preview-migrations",
        deadline_at=datetime.now(UTC) + timedelta(seconds=request.timeout_seconds),
    )
    if result is None:
        try:
            await runtime._project_migrations(state, request, verify_applied=False)
        except CellResourceError:
            await machine.request_finish(
                mutation,
                MachineOperationResult(
                    operation_id=str(request.operation_id),
                    state="completed",
                    exit_code=1,
                    output="isolated draft migration failed",
                ),
            )
            raise
        result = await machine.request_finish(
            mutation,
            MachineOperationResult(
                operation_id=str(request.operation_id),
                state="completed",
                exit_code=0,
            ),
        )
    if result.exit_code or result.timed_out:
        raise CellResourceError("isolated migration failed")


def check_candidate(
    state: CellWorkspaceState | None, identity: UUID, project: UUID, owner: UUID
) -> CellWorkspaceState:
    # The logical product ID authenticates generated routes. Physical names,
    # credentials and data volumes are all derived from the isolated cell UUID.
    if not state or (state.workspace_id, state.project_id, state.owner_id) != (
        identity,
        project,
        owner,
    ):
        raise CellIdentityConflict("isolated preview identity changed")
    return state


def _seed_config(files: dict[str, str]) -> dict[str, object]:
    # Parse the public JSON literal emitted by max_project_kit; never evaluate
    # guest JS or load configuration/secrets from the production workspace.
    source = files.get("src/lib/omnia/max-config.ts", "")
    for marker in (
        "export const omniaMaxConfig: YleumMaxConfig = ",
        "export const omniaMaxConfig = ",
    ):
        _, found, rest = source.partition(marker)
        if found:
            try:
                value, _ = json.JSONDecoder().raw_decode(rest.lstrip())
                if isinstance(value, dict):
                    app_config_envelope(value, 1)
                    return value
            except ValueError:
                pass
    return {}


def _build_commands(manifest: MachineManifest, files: dict[str, str]) -> list[str]:
    # A portable service may run source directly. Only the declared build
    # belongs here; final tests and unrelated acceptance steps are excluded.
    builds = [task for task in manifest.tasks if task.role == "build"]
    if not builds:
        builds = [
            task for task in manifest.tasks if task.role == "full_build" and task.name == "build"
        ]
    if not builds and "package.json" in files:
        package = json.loads(files["package.json"])
        if (
            isinstance(package, dict)
            and isinstance(package.get("scripts"), dict)
            and package["scripts"].get("build")
        ):
            return ["pnpm run build"]
    return ["cd " + shlex.quote(task.cwd) + " && " + shlex.join(task.argv) for task in builds]


async def materialize(
    manager: DockerCellResourceManager,
    candidate: UUID,
    project: UUID,
    owner: UUID,
    lease: UUID,
    revision: str,
    files: dict[str, str],
) -> None:
    normalized = ws._normalize_agent_write_files(files)
    if normalized != files or _workspace_revision(normalized) != revision:
        raise CellIdentityConflict("draft source identity changed")
    ws._require_agent_workspace_budget(normalized)
    manifest = MachineManifest.from_files(files)
    if manifest is None:
        raise CellResourceError("draft does not have a portable runtime manifest")
    member = manager.state_store.root.parent / "owner-checkpoint-members" / f"{candidate}.json"
    state = manager.state_store.load(candidate)
    if state is not None and not member.is_file():
        raise CellIdentityConflict("existing cell is not a checkpoint preview")
    previous = check_candidate(state, candidate, project, owner) if state else None
    if previous and previous.active_generation_run_id not in (None, lease):
        raise CellIdentityConflict("isolated preview lease changed")
    ensure_id = uuid5(lease, "ensure")
    previous_ensure = previous.operation(ensure_id) if previous else None
    epoch = (
        previous_ensure.fencing_epoch
        if previous_ensure
        else (previous.fencing_epoch + 1 if previous else 1)
    )
    if not member.is_file():
        write_controller_json(member, {"workspace_id": str(candidate)}, durable=True)
    await manager.ensure(
        WorkspaceSpec(candidate, project, owner, manager.profile.profile_version, lease),
        LifecycleMutation(ensure_id, epoch, revision),
    )
    runtime = ws._require_portable_runtime(manager)
    async with manager.operation_lock.hold(candidate):
        state, volume = await ws._workspace_volume_identity(manager, candidate)
        check_candidate(state, candidate, project, owner)
        ws._require_generation_lease_match(state, generation_run_id=lease, fencing_epoch=epoch)
        current = await ws._read_agent_workspace_files(manager, volume)
        if current and current != files:
            raise CellIdentityConflict("isolated source tree is already occupied")
        await ws._prepare_portable_write(manager, state, files, resume_migration_sources=False)
        if not current:
            await source_volume_call(
                manager,
                volume,
                "write_volume_files",
                {path: value.encode() for path, value in files.items()},
            )
        stored = await ws._read_agent_workspace_files(manager, volume)
        if stored != files:
            raise CellIdentityConflict("isolated source copy is incomplete")
        runtime.seed_owner_business_config(state, version=1, config=_seed_config(files))

    await ws.prepare_bound_workspace_environment(
        candidate,
        WorkspaceAgentPrepareRequest(
            project_id=project,
            owner_id=owner,
            generation_run_id=lease,
            fencing_epoch=epoch,
            expected_revision=revision,
            manifest_digest=manifest.digest(),
        ),
    )
    async with manager.operation_lock.hold(candidate):
        state = check_candidate(manager.state_store.load(candidate), candidate, project, owner)
        ws._require_generation_lease_match(state, generation_run_id=lease, fencing_epoch=epoch)
        request = WorkspaceAgentExecRequest(
            generation_run_id=lease,
            fencing_epoch=epoch,
            expected_revision=revision,
            cmd="omnia:bootstrap",
            task_role="bootstrap",
            operation_id=uuid5(lease, "install"),
            timeout_seconds=900,
        )
        if any(task.role == "bootstrap" for task in manifest.tasks):
            result = await runtime.guarded_execute(state, manifest, request)
            if result.exit_code or result.timed_out:
                raise CellResourceError("isolated dependency installation failed")
        await _migrate_empty_database(
            runtime,
            state,
            manifest,
            request.model_copy(
                update={
                    "cmd": "omnia:owner-preview-migrations",
                    "task_role": None,
                    "operation_id": uuid5(lease, "migrations"),
                }
            ),
        )
        for i, command in enumerate(_build_commands(manifest, files)):
            command_request = request.model_copy(
                update={
                    "cmd": command,
                    "task_role": None,
                    "operation_id": uuid5(lease, f"build-{i}"),
                }
            )
            result = await runtime.guarded_execute(state, manifest, command_request)
            if result.exit_code or result.timed_out:
                raise CellResourceError("isolated application build failed")

    # Standard release clears the build lease before serving. Normal owner
    # resume consumes only this candidate's own runtime and database.
    await manager.release_generation(
        candidate,
        LifecycleMutation(uuid5(lease, "release"), epoch + 1, revision),
        generation_run_id=lease,
    )
    await resume_candidate(manager, candidate, project, owner)


async def resume_candidate(
    manager: DockerCellResourceManager,
    candidate: UUID,
    project: UUID,
    owner: UUID,
    *,
    repair: bool = False,
) -> None:
    async with manager.operation_lock.hold(candidate):
        state = check_candidate(manager.state_store.load(candidate), candidate, project, owner)
        if state.phase != "completed" or state.active_generation_run_id is not None:
            raise CellIdentityConflict("preview build lease is still active")
        runtime = ws._require_portable_runtime(manager)
        serving, current = ws._owner_resume_epochs(runtime, state)
        running = runtime.preview(state)
        if repair or not running or running[0] != "running" or (serving and serving != current):
            await runtime.resume_preview(state, epoch=serving)
        await ws._publish_draft_preview(manager, candidate)
        _serving_key(runtime, state)


def _serving_key(runtime: MachineAdapter, state: CellWorkspaceState) -> str:
    try:
        secret = _read_plain_json_file(
            runtime.root / "boundary-secrets" / f"{state.workspace_id}.json"
        ).get("postgres_password")
        if not isinstance(secret, str) or not secret:
            raise PreviewProcessStopped("isolated signing key is unavailable")
        running = runtime.preview(state, signing_secret=secret)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PreviewProcessStopped("isolated signing boundary is unavailable") from exc
    if running is None or running[0] != "running" or not running[1]:
        raise PreviewProcessStopped("isolated preview is not serving this identity")
    return secret


async def signed_session(
    manager: DockerCellResourceManager,
    candidate: UUID,
    project: UUID,
    owner: UUID,
) -> WorkspaceDraftPreviewSessionResponse:
    async with manager.operation_lock.hold(candidate):
        state = check_candidate(manager.state_store.load(candidate), candidate, project, owner)
        if state.phase != "completed" or state.active_generation_run_id is not None:
            raise CellIdentityConflict("candidate lease is active")
        runtime = ws._require_portable_runtime(manager)
        secret = _serving_key(runtime, state)
        return ws._sign_draft_preview_session(
            candidate, project, ws._draft_preview_url(candidate), secret
        )


async def retire_candidate(
    manager: DockerCellResourceManager,
    candidate: UUID,
    project: UUID,
    owner: UUID,
    lease: UUID,
    revision: str,
) -> None:
    member = manager.state_store.root.parent / "owner-checkpoint-members" / f"{candidate}.json"
    state = manager.state_store.load(candidate)
    if state is None:
        if member.is_file():
            # Admission can stop after reserving capacity/credentials but
            # before the first state record. Native reconciliation inspects
            # physical resources before removing that reservation.
            await manager.reconcile(
                candidate, LifecycleMutation(uuid5(lease, "retire-reservation"), 1, revision)
            )
        await manager.assert_uncreated_workspace(candidate)
        if member.is_file():
            runtime = ws._require_portable_runtime(manager)
            await machine_effect(_retire_controller_files, manager, runtime.root, candidate)
            member.unlink()
        return
    if not member.is_file():
        raise CellIdentityConflict("existing cell is not a checkpoint preview")
    state = check_candidate(state, candidate, project, owner)
    if state.active_generation_run_id not in (None, lease):
        raise CellIdentityConflict("cannot retire somebody else's preview")
    await nginx_writer.unpublish(ws._draft_preview_host(candidate))
    latest = state.operation(state.last_operation_id) if state.last_operation_id else None
    if state.phase == "indeterminate" or (latest and latest.status == "running"):
        next_epoch = state.fencing_epoch + 1
        observed = await manager.reconcile(
            candidate,
            LifecycleMutation(
                uuid5(lease, f"retire-observe-{next_epoch}"),
                next_epoch,
                revision,
            ),
        )
        if not observed.identity_valid:
            raise CellIdentityConflict("disposable workspace resources need reconciliation")
    async with manager.operation_lock.hold(candidate):
        state = check_candidate(manager.state_store.load(candidate), candidate, project, owner)
        if state.active_generation_run_id not in (None, lease):
            raise CellIdentityConflict("isolated preview lease changed")
        # A disposable failed preparation owns this lease. Destroy it under
        # its fence; taking another rootfs/DB snapshot can consume the same
        # disk reserve that interrupted the build.
        last = state.operation(state.last_operation_id) if state.last_operation_id else None
        destroyed = bool(
            last
            and last.kind == "destroy"
            and last.status == "completed"
            and last.operation_id == uuid5(lease, f"retire-{last.fencing_epoch}")
        )
        if not destroyed:
            epoch = state.fencing_epoch + 1
            await manager.destroy_compute_without_lock(
                candidate,
                LifecycleMutation(uuid5(lease, f"retire-{epoch}"), epoch, revision),
                checkpoint_ref=None,
                record_operation=True,
                capture=False,
            )
        volumes = await manager.docker.list_workspace_volumes(candidate)
        for volume in volumes:
            labels = volume.labels
            if (
                labels.get("omnia.workspace_id"),
                labels.get("omnia.project_id"),
                labels.get("omnia.owner_id"),
            ) != (
                str(candidate),
                str(project),
                str(owner),
            ):
                raise CellIdentityConflict("isolated volume identity changed")
        for volume in volumes:
            await manager.docker.remove_volume(volume.name)
        if any(
            (
                await manager.docker.list_workspace_containers(candidate),
                await manager.docker.list_workspace_volumes(candidate),
                await manager.docker.list_workspace_networks(candidate),
            )
        ):
            raise CellResourceError("isolated preview still has Docker resources")
        runtime = ws._require_portable_runtime(manager)
        # parts() may allocate credentials; never call it after purging files.
        if runtime.exists(candidate):
            _, backend = runtime.parts(state)
            await machine_effect(_retire_environment_images, backend)
        await machine_effect(_retire_controller_files, manager, runtime.root, candidate)
        await manager.assert_uncreated_workspace(candidate)
        member.unlink(missing_ok=True)


def _retire_environment_images(backend: object) -> None:
    from yleum_orchestrator.services.docker_machine_backend import DockerMachineBackend

    if not isinstance(backend, DockerMachineBackend):
        raise CellIdentityConflict("expected private Docker machine")
    expected = backend.labels("environment")
    images = backend.client.images.list(
        filters={"label": [f"{k}={v}" for k, v in expected.items()]}
    )
    for image in images:
        if any(image.labels.get(key) != value for key, value in expected.items()):
            raise CellIdentityConflict("isolated image ownership changed")
        backend.client.images.remove(image.id, force=False, noprune=True)


def _retire_controller_files(
    manager: DockerCellResourceManager, root: Path, candidate: UUID
) -> None:
    # All paths are controller-owned. Only remove this exact cell's records
    # while its native lock is held and the parent slot still names it.
    identity = str(candidate)
    for parent in (root / "artifacts", root):
        path = parent / identity
        if path.exists() or path.is_symlink():
            _ensure_secure_dir(path, create=False)
            shutil.rmtree(path)
    paths = [
        root / name / f"{identity}.json"
        for name in (
            "core-runtime-credentials",
            "boundary-secrets",
            "project-postgres-secrets",
            "project-db-credentials",
            "owner-boundary-runtime",
            "public-boundary-runtime",
            "public-boundary-auth",
        )
    ]
    paths.extend(
        [
            root / "project-db-credentials" / f"{identity}.lock",
            manager.credential_store.root / f"{identity}.json",
            manager.state_store.workspace_path(candidate),
        ]
    )
    for path in paths:
        if path.parent.exists() or path.parent.is_symlink():
            _ensure_secure_dir(path.parent, create=False)
            if _require_regular_file_path(path, missing_ok=True) is not None:
                path.unlink()
