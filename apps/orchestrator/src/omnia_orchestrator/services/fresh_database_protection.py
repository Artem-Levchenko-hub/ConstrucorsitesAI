"""Admit protection only before a project's first PostgreSQL volume exists.

The caller holds the workspace operation lock. A durable intent precedes policy
creation and all Docker effects; a failed installation never falls back to admin
credentials. Existing databases retain their explicit migration/recovery path.
"""

from pathlib import Path
from typing import Any

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
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


def prepare_new_database(backend: Any, epoch: int) -> None:
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
        write_controller_json(_path(backend), journal)
    stage_policy(backend, DataContract(version=1), journal["epoch"], blocked_deletes=[])


def finish_new_database(backend: Any, epoch: int) -> None:
    """Install controller grants after PG readiness, before the first guest."""
    journal = _journal(backend)
    if journal is None or journal["state"] == "ready":
        return
    prepare_new_database(backend, epoch)
    install_policy(backend)
    write_controller_json(_path(backend), {**journal, "state": "ready"})
