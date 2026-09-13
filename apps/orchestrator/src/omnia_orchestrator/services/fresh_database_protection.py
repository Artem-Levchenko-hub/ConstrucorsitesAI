"""Admit protection only before a project's first PostgreSQL volume exists.

The caller holds the workspace operation lock. A durable intent precedes policy
creation and all Docker effects; a failed installation never falls back to admin
credentials. Existing databases retain their explicit migration/recovery path.
"""

from pathlib import Path
from typing import Any
from uuid import UUID

from omnia_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellProtectedEnvironmentRecoveryRequired,
    CellResourceError,
)
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.cell_state import _read_plain_json_file
from omnia_orchestrator.services.project_machine import write_controller_json
from omnia_orchestrator.services.restoration_data_contract import DataContract
from omnia_orchestrator.services.restoration_database import (
    install_policy,
    load_policy,
    stage_policy,
)


def _path(backend: Any) -> Path:
    return Path(backend.root) / str(backend.workspace_id) / "initial-database.json"


def _journal(backend: Any) -> dict[str, Any] | None:
    path = _path(backend)
    if not path.exists():
        return None
    value = _read_plain_json_file(path)
    if (
        any(value.get(key) != str(getattr(backend, key))
            for key in ("workspace_id", "project_id", "owner_id"))
        or type(value.get("epoch")) is not int
        or value["epoch"] < 1
        or value.get("state") not in {"pending", "ready"}
    ):
        raise CellIdentityConflict("initial database identity mismatch")
    return value


def _initial_runtime(backend: Any, manifest: MachineManifest) -> dict[str, Any]:
    caches = {backend.pnpm_cache_volume, backend.corepack_cache_volume, backend.next_cache_volume}
    return {
        # Product tasks/routes/services may change before the first command.
        # Their validated material mounts and the admitted host envelope may not.
        # Derived cache names change when exec binds the dependency identity.
        "material_mounts": {
            name: mount for name, mount in backend.volume_mapping(manifest).items()
            if name not in caches
        },
        **{key: getattr(backend, key) for key in (
            "workspace_volume", "project_postgres_volume", "internal_network",
            "base_image", "guard_image", "postgres_image", "resource_profile_version",
            "cpu_cores", "memory_bytes", "disk_bytes", "pids",
            "project_postgres_memory_bytes", "project_postgres_cpu_cores", "namespace",
        )},
    }


def prepare_new_database(
    backend: Any, epoch: int, *, manifest: MachineManifest | None = None,
    generation_run_id: UUID | None = None,
) -> None:
    """Run before any product start, including reuse of a stopped container."""
    journal = _journal(backend)
    policy = load_policy(backend)
    if policy is not None and policy["epoch"] > epoch:
        raise CellIdentityConflict("database policy fence is newer")
    if journal is not None:
        if journal["state"] == "ready":
            if policy is None:
                raise CellIdentityConflict("initial database protection is missing")
            return
        if journal["epoch"] > epoch:
            raise CellIdentityConflict("initial database fence is newer")
        if backend._container() is not None:
            raise CellIdentityConflict("initial database already has a product")
        if policy is not None:
            if (policy["epoch"] != journal["epoch"]
                    or policy["contract"] != {"version": 1, "tables": []}):
                raise CellIdentityConflict("initial database policy changed")
            # A cancelled first generation may retry under a newer canonical
            # lease. No guest has existed, so replay the same admission and
            # credentials; ensure() still fences the physical PostgreSQL epoch.
            return
    elif policy is not None:
        return  # Established restoration protection belongs to its controller.

    volume = backend._lookup(
        backend.client.volumes, backend.project_postgres_volume, "project-volume"
    )
    if volume is not None:
        if journal is not None:
            raise CellIdentityConflict("initial database volume predates protection")
        return  # Legacy data must be inventoried, never blessed as an empty schema.
    metadata = backend._metadata()
    if (
        backend._container() is not None
        or backend._project_postgres() is not None
        or any(metadata.get(key) for key in (
            "manifest", "epoch", "restored_image", "environment_ref", "protected_rootfs_ref",
        ))
    ):
        raise CellResourceError("existing database material is missing; explicit recovery required")
    if journal is None:
        journal = {
            **{key: str(getattr(backend, key))
               for key in ("workspace_id", "project_id", "owner_id")},
            "epoch": epoch,
            "state": "pending",
        }
        if manifest is not None and generation_run_id is not None:
            journal["initial_runtime"] = _initial_runtime(backend, manifest)
            journal["generation_run_id"] = str(generation_run_id)
        write_controller_json(_path(backend), journal)
    stage_policy(backend, DataContract(version=1), journal["epoch"], blocked_deletes=[])


def validate_initial_runtime_resume(
    backend: Any, manifest: MachineManifest, epoch: int | None,
) -> bool:
    """Read-only admission under the caller's workspace lock and canonical lease.

    A legacy pending journal lacks the resource binding and cannot be upgraded
    based on absent material: it may describe data which was subsequently lost.
    """
    journal = _journal(backend)
    if journal is None or journal["state"] != "pending" or "initial_runtime" not in journal:
        return False
    try:
        UUID(journal["generation_run_id"])
    except (KeyError, TypeError, ValueError):
        return False
    policy = load_policy(backend)
    metadata = backend._metadata()
    if (
        epoch is None or epoch < journal["epoch"]
        or journal["initial_runtime"] != _initial_runtime(backend, manifest)
        or policy is None or policy["epoch"] != journal["epoch"]
        or policy["contract"] != {"version": 1, "tables": []}
        or policy["blocked_deletes"] != []
        or backend._container() is not None
        or any(metadata.get(key) for key in (
            "manifest", "epoch", "restored_image", "environment_ref", "protected_rootfs_ref",
            "active_code_volume", "restore_in_progress", "quiesce_state",
        ))
    ):
        return False
    volume = backend._lookup(
        backend.client.volumes, backend.project_postgres_volume, "project-volume",
    )
    postgres = backend._project_postgres()
    if journal.get("volume_intent") is True:
        if volume is None:
            return False
    elif volume is not None or postgres is not None:
        return False
    if postgres is not None:
        physical_epoch = int(postgres.labels.get("omnia.fencing_epoch", "0"))
        if physical_epoch < journal["epoch"] or physical_epoch > epoch:
            return False
    return True


def record_initial_volume_intent(backend: Any) -> None:
    """Never recreate a missing volume after its first creation was attempted."""
    journal = _journal(backend)
    if journal is None or journal["state"] != "pending" or "initial_runtime" not in journal:
        return
    volume = backend._lookup(
        backend.client.volumes, backend.project_postgres_volume, "project-volume",
    )
    if journal.get("volume_intent") is True:
        if volume is None:
            raise CellProtectedEnvironmentRecoveryRequired(
                "initial database material is missing; explicit recovery required"
            )
        return
    if volume is not None:
        raise CellProtectedEnvironmentRecoveryRequired(
            "initial database material predates its creation intent"
        )
    write_controller_json(_path(backend), {**journal, "volume_intent": True})


def finish_new_database(backend: Any, epoch: int) -> None:
    """Install controller grants after PG readiness, before the first guest."""
    journal = _journal(backend)
    if journal is None or journal["state"] == "ready":
        return
    prepare_new_database(backend, epoch)
    install_policy(backend)
    write_controller_json(_path(backend), {**journal, "state": "ready"})
