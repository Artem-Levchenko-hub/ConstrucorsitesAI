"""Retained-data startup and fenced declarative migrations for protected cells.

All async entry points run inside the caller's canonical workspace operation lock.
No database/home checkpoint import is a startup or migration recovery operation.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.services.cell_state import _read_plain_json_file
from omnia_orchestrator.services.project_machine import machine_effect, write_controller_json
from omnia_orchestrator.services.restoration_data_contract import DataContract

MIGRATION_COMMAND = "omnia-db apply .omnia/data-contract.json"


def migration_path(backend: Any) -> Path:
    return Path(backend.root) / str(backend.workspace_id) / "database-migration.json"


def migration_journal(backend: Any) -> dict[str, Any] | None:
    path = migration_path(backend)
    if not path.exists():
        return None
    value = _read_plain_json_file(path)
    if (
        value.get("workspace_id") != str(backend.workspace_id)
        or value.get("project_id") != str(backend.project_id)
        or value.get("owner_id") != str(backend.owner_id)
    ):
        raise CellIdentityConflict("database migration journal identity mismatch")
    return value


def pending_migration(backend: Any) -> dict[str, Any] | None:
    value = migration_journal(backend)
    return value if value is not None and value.get("state") != "ready" else None


def validate_retained_runtime(backend: Any, manifest: Any) -> None:
    """Missing material state requires recovery; never replace it with an archive."""
    metadata = backend._metadata()
    if (
        metadata.get("restore_in_progress")
        or metadata.get("restore_target")
        or metadata.get("pending_image")
        or metadata.get("quiesce_state") in {"pending", "failed"}
    ):
        raise CellResourceError("protected runtime requires explicit recovery")
    image_ref = metadata.get("restored_image") or backend.base_image
    is_image_id = isinstance(image_ref, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image_ref)
    trusted_base = image_ref == backend.base_image
    pinned_base = (
        trusted_base
        and isinstance(image_ref, str)
        and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image_ref) is not None
    )
    if not is_image_id and not pinned_base:
        raise CellIdentityConflict("protected runtime image is not immutable")
    image = backend.client.images.get(image_ref)
    # A repository manifest digest resolves to a distinct image config digest.
    if (
        not isinstance(image.id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image.id) is None
        or (is_image_id and image.id != image_ref)
    ):
        raise CellIdentityConflict("protected runtime image changed")
    if not trusted_base:
        config = image.attrs.get("Config", {})
        labels = config.get("Labels") or {}
        if any(labels.get(key) != value for key, value in backend.labels("environment").items()):
            raise CellIdentityConflict("protected runtime image ownership mismatch")
        if config.get("Env") or config.get("Cmd") or config.get("Entrypoint"):
            raise CellIdentityConflict("protected runtime image has runtime configuration")
    for name in backend.environment_volume_names(manifest):
        volume = backend.client.volumes.get(name)
        attrs = volume.attrs
        labels = attrs.get("Labels") or {}
        expected = backend.labels("project-volume")
        active_code = re.fullmatch(re.escape(backend.stem) + r"-code-[0-9a-f]{32}", name)
        if (
            name == backend.workspace_volume
            and active_code is None
            and labels.get("omnia.project_cell") == "true"
        ):
            expected = {
                "omnia.managed": "true",
                "omnia.project_cell": "true",
                "omnia.workspace_id": str(backend.workspace_id),
                "omnia.project_id": str(backend.project_id),
                "omnia.owner_id": str(backend.owner_id),
                "omnia.provider": "docker_owner_canary",
                "omnia.resource_kind": "workspace",
                "omnia.profile_version": backend.resource_profile_version,
            }
        if (
            any(labels.get(key) != value for key, value in expected.items())
            or attrs.get("Name") != name
            or attrs.get("Driver") != "local"
            or attrs.get("Options")
            or attrs.get("Scope") != "local"
        ):
            raise CellIdentityConflict("protected retained volume identity mismatch")


def require_current_generation(manager: Any, state: Any, request: Any) -> None:
    current = manager.state_store.load(state.workspace_id)
    if (
        current is None
        or current.project_id != state.project_id
        or current.owner_id != state.owner_id
        or current.active_generation_run_id != request.generation_run_id
        or current.active_generation_fencing_epoch != request.fencing_epoch
        or current.fencing_epoch != request.fencing_epoch
    ):
        raise CellIdentityConflict("database migration generation lease changed")


async def read_desired_contract(adapter: Any, backend: Any, request: Any) -> dict[str, Any]:
    from omnia_orchestrator.routers.runtime import _workspace_revision
    from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

    files = await _read_agent_workspace_files(adapter.manager, backend.workspace_volume)
    if _workspace_revision(files) != request.expected_revision:
        raise CellIdentityConflict("database migration source revision changed")
    raw = files.get(".omnia/data-contract.json")
    if raw is None:
        raise CellResourceError("migration requires .omnia/data-contract.json")
    return DataContract.model_validate_json(raw).model_dump(mode="json")


async def execute_generation_migration(
    adapter: Any,
    state: Any,
    manifest: Any,
    request: Any,
    *,
    releasing: bool = False,
) -> dict[str, Any]:
    """Caller holds the canonical operation lock; only this controller performs DDL.

    The private intent survives tool timeouts. Every replay binds the same source,
    manifest, generation and fence. No recovery operation imports a volume archive.
    """
    from omnia_orchestrator.services.restoration_catalog import catalog_contract
    from omnia_orchestrator.services.restoration_database import (
        install_policy,
        load_policy,
        recover_policy,
    )
    from omnia_orchestrator.services.restoration_migrations import (
        apply_additive_contract,
        plan_additive_migration,
    )

    require_current_generation(adapter.manager, state, request)
    machine, backend = adapter.parts(state)
    policy = load_policy(backend)
    if policy is None or policy["epoch"] > request.fencing_epoch:
        raise CellIdentityConflict("migration requires current protected database policy")
    persisted = pending_migration(backend) if releasing else None
    if releasing and persisted is None:
        raise CellIdentityConflict("release has no admitted database migration")
    desired_value = (
        persisted["desired"]
        if persisted is not None
        else await read_desired_contract(adapter, backend, request)
    )
    desired = DataContract.model_validate(desired_value)
    envelope = {
        "workspace_id": str(state.workspace_id),
        "project_id": str(state.project_id),
        "owner_id": str(state.owner_id),
        "generation_run_id": str(request.generation_run_id),
        "fencing_epoch": request.fencing_epoch,
        "source_revision": request.expected_revision,
        "manifest_digest": manifest.digest(),
        "manifest": manifest.model_dump(mode="json"),
        "desired": desired_value,
    }
    journal = migration_journal(backend)
    if journal is not None and journal.get("state") == "ready":
        if all(journal.get(key) == value for key, value in envelope.items()):
            return dict(journal["proof"])
        journal = None
    if journal is not None:
        if any(journal.get(key) != value for key, value in envelope.items()):
            raise CellIdentityConflict("pending database migration envelope changed")
    else:
        trusted = DataContract.model_validate(policy["contract"])
        current, blockers = await machine_effect(
            catalog_contract, backend, trusted_contract=trusted
        )
        if blockers:
            raise CellResourceError("migration_unsupported:catalog_requires_review")
        plan_additive_migration(
            current, desired
        )  # Reject unsupported work before stopping writers.
        journal = {
            **envelope,
            "operation_id": str(request.operation_id),
            "state": "prepared",
            "blocked_deletes": policy["blocked_deletes"],
        }
        write_controller_json(migration_path(backend), journal)

    def save(phase: str, **values: Any) -> None:
        journal.update(state=phase, **values)
        write_controller_json(migration_path(backend), journal)

    if journal["state"] == "prepared":
        # Preserve rootfs-installed dependencies, without checkpointing/restoring business volumes.
        reference = await adapter.checkpoint(state, volumes=(), persist=False)
        if reference is None:
            raise CellResourceError("migration cannot preserve current runtime image")
        metadata = backend._metadata()
        metadata.update(
            restored_image=reference.image_id,
            protected_rootfs_ref=reference.model_dump(mode="json"),
        )
        write_controller_json(backend.metadata_path, metadata)
        save("ddl_pending")

    if journal["state"] == "ddl_pending":
        require_current_generation(adapter.manager, state, request)
        await machine_effect(validate_retained_runtime, backend, manifest)
        # A checkpoint stops PG too. Ensure the current volumes are running before controller SQL.
        await machine_effect(backend.ensure, manifest, request.fencing_epoch)
        product = backend._container()
        if product is not None:
            await machine_effect(product.remove, force=True)
        proof = await machine_effect(
            apply_additive_contract,
            backend,
            desired,
            expected_epoch=request.fencing_epoch,
        )
        save("ddl_applied", proof=proof)

    if journal["state"] == "ddl_applied":
        require_current_generation(adapter.manager, state, request)
        await machine_effect(backend.remove)
        await machine_effect(
            recover_policy,
            backend,
            desired,
            request.fencing_epoch,
            blocked_deletes=journal["blocked_deletes"],
            operation_id="migration:" + journal["operation_id"],
        )
        save("policy_staged")

    if journal["state"] != "policy_staged":
        raise CellIdentityConflict("database migration journal phase invalid")
    require_current_generation(adapter.manager, state, request)
    await machine_effect(validate_retained_runtime, backend, manifest)
    await machine_effect(backend.ensure, manifest, request.fencing_epoch)
    await machine_effect(install_policy, backend)
    if not releasing:
        for name in manifest.service_order():
            service = next(item for item in manifest.services if item.name == name)
            await machine_effect(backend.start_service, service, request.fencing_epoch)
            status = await machine_effect(
                backend.service_status,
                service,
                request.fencing_epoch,
                include_logs=False,
            )
            if not status["ready"]:
                raise CellResourceError("migration runtime service not ready: " + name)
        await machine_effect(
            adapter._start_boundary, state, manifest, backend, request.fencing_epoch
        )
    saved = machine.state()
    saved["epoch"] = request.fencing_epoch
    write_controller_json(machine.path, saved)
    save("ready")
    return dict(journal["proof"])


async def reconcile_generation_migration(adapter: Any, state: Any) -> None:
    """Finish an admitted declaration before canonical release relinquishes its lease."""
    from omnia_orchestrator.core.project_machine import MachineManifest

    _, backend = adapter.parts(state)
    journal = pending_migration(backend)
    if journal is None:
        return
    request = SimpleNamespace(
        generation_run_id=UUID(journal["generation_run_id"]),
        operation_id=UUID(journal["operation_id"]),
        fencing_epoch=journal["fencing_epoch"],
        expected_revision=journal["source_revision"],
    )
    manifest = MachineManifest.model_validate(journal["manifest"])
    await execute_generation_migration(adapter, state, manifest, request, releasing=True)
