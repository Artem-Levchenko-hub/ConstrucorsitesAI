"""Admitted, resumable protection of the CURRENT database before adaptive tools.

Caller holds the canonical workspace lock. No historical schema/data is imported;
the journal keeps the original generation lease until protection is installed.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.cell_state import _read_plain_json_file
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.project_machine import machine_effect, write_controller_json
from omnia_orchestrator.services.protected_machine_lifecycle import (
    require_current_generation,
    validate_retained_runtime,
)
from omnia_orchestrator.services.restoration_catalog import catalog_contract
from omnia_orchestrator.services.restoration_data_contract import DataContract, assess_contract
from omnia_orchestrator.services.restoration_database import (
    install_policy,
    load_policy,
    stage_policy,
)


def protection_path(backend: Any) -> Path:
    return Path(backend.root) / str(backend.workspace_id) / "database-protection.json"


def protection_journal(backend: Any) -> dict[str, Any] | None:
    path = protection_path(backend)
    if not path.exists():
        return None
    value = _read_plain_json_file(path)
    if (
        value.get("workspace_id") != str(backend.workspace_id)
        or value.get("project_id") != str(backend.project_id)
        or value.get("owner_id") != str(backend.owner_id)
        or value.get("state") not in {"prepared", "rootfs_saved", "policy_staged", "ready"}
    ):
        raise CellIdentityConflict("database protection journal identity/state mismatch")
    return value


def pending_protection(backend: Any) -> dict[str, Any] | None:
    value = protection_journal(backend)
    return value if value is not None and value["state"] != "ready" else None


def require_protection_ready(adapter: Any, state: Any) -> None:
    if adapter is not None and adapter.exists(state.workspace_id):
        require_backend_protection_ready(adapter.parts(state)[1])


def require_backend_protection_ready(backend: Any) -> None:
    if isinstance(backend, DockerMachineBackend) and pending_protection(backend) is not None:
        raise CellResourceError("database protection pending; retry bootstrap before tools")


def require_supported_driver(backend: Any) -> None:
    # Reading through Docker executes no guest interpreter/dependency while the
    # old owner credential still exists. Intermediate pnpm symlinks are resolved
    # by Docker; the final package metadata must be one bounded ordinary file.
    try:
        chunks, _ = backend._container().get_archive("/workspace/node_modules/pg/package.json")
        payload = bytearray()
        for chunk in chunks:
            payload.extend(chunk)
            if len(payload) > 131072:
                raise ValueError("driver metadata archive too large")
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            entries = archive.getmembers()
            if (
                len(entries) != 1
                or not entries[0].isfile()
                or entries[0].name != "package.json"
                or entries[0].size > 65536
            ):
                raise ValueError("driver metadata is not one ordinary file")
            member = archive.extractfile(entries[0])
            if member is None:
                raise ValueError("driver metadata missing")
            package = json.loads(member.read(65537))
        version = re.fullmatch(r"8\.(\d+)\.(\d+)", str(package.get("version", "")))
        if package.get("name") != "pg" or version is None or int(version[1]) < 22:
            raise ValueError("unsupported driver version")
    except Exception:
        raise CellResourceError(
            "adaptive database protection requires installed pg >=8.22,<9"
        ) from None


async def protect_current_database(
    adapter: Any,
    state: Any,
    request: Any,
    files: dict[str, str],
    *,
    releasing: bool = False,
) -> None:
    from omnia_orchestrator.routers.runtime import _workspace_revision
    from omnia_orchestrator.services.code_restoration_engine import validate_supported_runtime

    require_current_generation(adapter.manager, state, request)
    if not adapter.exists(state.workspace_id):
        raise CellResourceError("adaptive protection requires an existing portable runtime")
    machine, backend = adapter.parts(state)
    journal = pending_protection(backend)
    policy = load_policy(backend)
    if journal is None and policy is not None:
        from omnia_orchestrator.services.protected_machine_lifecycle import (
            reconcile_generation_migration,
        )

        if policy["epoch"] > request.fencing_epoch:
            raise CellIdentityConflict("database protection policy is newer than the generation")
        manifest = MachineManifest.model_validate(machine.state()["manifest"])
        await machine_effect(validate_retained_runtime, backend, manifest)
        await reconcile_generation_migration(adapter, state)
        return
    manifest = validate_supported_runtime(files)
    current_manifest = MachineManifest.model_validate(machine.state()["manifest"])
    if manifest.digest() != current_manifest.digest():
        raise CellIdentityConflict("adaptive protection current manifest changed")
    source_revision = _workspace_revision(files)
    envelope = {
        "workspace_id": str(state.workspace_id),
        "project_id": str(state.project_id),
        "owner_id": str(state.owner_id),
        "generation_run_id": str(request.generation_run_id),
        "fencing_epoch": request.fencing_epoch,
        "source_revision": source_revision,
        "manifest": manifest.model_dump(mode="json"),
        "volumes": sorted(backend.environment_volume_names(manifest)),
    }
    if journal is not None and any(journal.get(key) != value for key, value in envelope.items()):
        raise CellIdentityConflict("database protection admitted envelope changed")
    await machine_effect(validate_retained_runtime, backend, manifest)
    if journal is None:
        # Canonical ensure may have paused the current machine. Reuse the retained
        # image/volumes to expose PG to the controller; no guest command is executed.
        await machine_effect(backend.ensure, manifest, request.fencing_epoch)
        await machine_effect(require_supported_driver, backend)
        current_contract, blockers = await machine_effect(catalog_contract, backend)
        blockers.extend(assess_contract(current_contract, current_contract).blockers)
        if blockers:
            raise CellResourceError(
                "current database requires explicit protection support: " + ", ".join(blockers)
            )
        journal = {
            **envelope,
            "state": "prepared",
            "contract": current_contract.model_dump(mode="json"),
        }
        write_controller_json(protection_path(backend), journal)

    def save(phase: str) -> None:
        journal["state"] = phase
        write_controller_json(protection_path(backend), journal)

    contract = DataContract.model_validate(journal["contract"])
    if journal["state"] == "prepared":
        require_current_generation(adapter.manager, state, request)
        product = backend._container()
        if product is not None:
            # The entire PID namespace stops, including daemonized writers.
            await machine_effect(product.stop, timeout=10)
            await machine_effect(product.reload)
            if product.status == "running":
                raise CellResourceError("current database writers did not stop")
        reference = await adapter.checkpoint(state, volumes=(), persist=False)
        if reference is None:
            raise CellResourceError("database protection cannot retain the current rootfs")
        metadata = backend._metadata()
        metadata.update(
            restored_image=reference.image_id,
            environment_ref=None,
            protected_rootfs_ref=reference.model_dump(mode="json"),
        )
        write_controller_json(backend.metadata_path, metadata)
        save("rootfs_saved")

    if journal["state"] == "rootfs_saved":
        require_current_generation(adapter.manager, state, request)
        policy = load_policy(backend)
        if policy is None:
            await machine_effect(validate_retained_runtime, backend, manifest)
            await machine_effect(backend.ensure, manifest, request.fencing_epoch)
            product = backend._container()
            if product is not None:
                await machine_effect(product.remove, force=True)
            current_contract, blockers = await machine_effect(catalog_contract, backend)
            if blockers or current_contract != contract:
                raise CellIdentityConflict("database schema changed before protection")
            # No former application process/session survives the credential switch.
            await machine_effect(backend.remove)
            await machine_effect(
                stage_policy,
                backend,
                contract,
                request.fencing_epoch,
                blocked_deletes=assess_contract(contract, contract).blocked_deletes,
            )
        elif (
            policy["contract"] != contract.model_dump(mode="json")
            or policy["epoch"] != request.fencing_epoch
        ):
            raise CellIdentityConflict("database policy changed during protection")
        save("policy_staged")

    if journal["state"] != "policy_staged":
        raise CellIdentityConflict("database protection recovery phase invalid")
    require_current_generation(adapter.manager, state, request)
    await machine_effect(validate_retained_runtime, backend, manifest)
    # A lost response can leave some declared services already running. Restart
    # from the captured rootfs with the same volumes before reinstalling policy;
    # no previous writer may race grant/RLS installation on a replay.
    await machine_effect(backend.remove)
    await machine_effect(backend.ensure, manifest, request.fencing_epoch)
    await machine_effect(install_policy, backend)
    if not releasing:
        for name in manifest.service_order():
            service = next(item for item in manifest.services if item.name == name)
            await machine_effect(backend.start_service, service, request.fencing_epoch)
            status = await machine_effect(
                backend.service_status, service, request.fencing_epoch, include_logs=False
            )
            if not status["ready"]:
                raise CellResourceError("protected current service not ready: " + name)
        await machine_effect(
            adapter._start_boundary, state, manifest, backend, request.fencing_epoch
        )
    saved = machine.state()
    saved["epoch"] = request.fencing_epoch
    write_controller_json(machine.path, saved)
    save("ready")


async def reconcile_generation_protection(adapter: Any, state: Any) -> None:
    from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

    _, backend = adapter.parts(state)
    journal = pending_protection(backend)
    if journal is None:
        return
    files = await _read_agent_workspace_files(adapter.manager, backend.workspace_volume)
    request = SimpleNamespace(
        generation_run_id=UUID(journal["generation_run_id"]), fencing_epoch=journal["fencing_epoch"]
    )
    await protect_current_database(adapter, state, request, files, releasing=True)
