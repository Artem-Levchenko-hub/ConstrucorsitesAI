"""Operator-only adoption of the historical pending-before-first-machine defect.

No API calls this service. A temporary isolated read-only source helper is the only
Docker effect, including during dry-run. It never creates application resources,
rotates credentials, installs SQL, or restores source/data.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.cell_state import _read_plain_json_file
from omnia_orchestrator.services.docker_machine_backend import _PIN
from omnia_orchestrator.services.fresh_database_protection import _initial_runtime, _journal
from omnia_orchestrator.services.project_machine import machine_effect, write_controller_json
from omnia_orchestrator.services.restoration_database import load_policy


class InitialDatabaseRecoveryRejected(RuntimeError):
    """Only fixed reason identifiers are exposed to the operator."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InitialDatabaseRecoveryRejected(reason)


async def _source_manifest(manager: Any, backend: Any, state: Any) -> MachineManifest:
    volume = await machine_effect(backend.client.volumes.get, state.resource_names.workspace_volume)
    labels = volume.attrs.get("Labels") or {}
    _require(
        all(
            labels.get("omnia." + key) == str(getattr(state, key))
            for key in ("workspace_id", "project_id", "owner_id")
        )
        and labels.get("omnia.resource_kind") == "workspace",
        "source_identity",
    )
    _require(_PIN.fullmatch(manager.docker.helper_image) is not None, "helper_image")
    # Existing reader: read-only volume, network none, bounded archive/tmpfs,
    # auto-remove helper with 300s lifetime and finally cleanup. Never read the
    # root-owned Docker mountpoint by weakening controller file ownership checks.
    async with asyncio.timeout(330):
        files = await manager.docker.read_workspace_source_files(
            state.resource_names.workspace_volume
        )
    helpers = await machine_effect(
        backend.client.containers.list,
        all=True,
        filters={
            "label": [
                f"omnia.workspace_id={state.workspace_id}",
                "omnia.resource_kind=workspace-source-read",
            ]
        },
    )
    _require(not helpers, "source_helper_cleanup")
    raw = files.get(".omnia/cell.json")
    _require(isinstance(raw, bytes) and len(raw) <= 256 * 1024, "source_manifest")
    return MachineManifest.model_validate_json(raw)


def _assess(
    adapter: Any, state: Any, initial_operation_id: UUID
) -> tuple[dict[str, Any], Path, Any]:
    # parts() normally may create a missing credential. Recovery must never do so.
    secret = adapter.root / "project-postgres-secrets" / f"{state.workspace_id}.json"
    _require(secret.is_file() and not secret.is_symlink(), "existing_secret_required")
    credentials = _read_plain_json_file(secret)
    _require(
        isinstance(credentials.get("postgres_password"), str)
        and bool(credentials["postgres_password"]),
        "existing_secret_required",
    )
    machine, backend = adapter.parts(state)
    _require(
        all(
            getattr(backend, key) == getattr(state, key)
            for key in ("workspace_id", "project_id", "owner_id")
        )
        and backend.workspace_volume == state.resource_names.workspace_volume
        and backend.internal_network == state.resource_names.internal_network
        and backend.resource_profile_version == state.profile_version,
        "backend_identity",
    )
    journal = _journal(backend)
    _require(journal is not None and journal.get("state") == "pending", "initial_state")
    assert journal is not None
    _require(
        set(journal) == {"workspace_id", "project_id", "owner_id", "epoch", "state"},
        "legacy_journal_required",
    )
    operation = state.operation(initial_operation_id)
    _require(
        operation is not None
        and operation.kind == "ensure"
        and operation.status == "completed"
        and operation.fencing_epoch == journal["epoch"]
        and operation.generation_run_id is not None,
        "initial_operation",
    )
    _require(
        all(
            x.status == "completed"
            and (x.operation_id == initial_operation_id or x.kind == "release")
            for x in state.operations
        ),
        "operation_history",
    )
    policy = load_policy(backend)
    _require(
        policy is not None
        and policy["epoch"] == journal["epoch"]
        and policy["contract"] == {"version": 1, "tables": []}
        and policy["blocked_deletes"] == [],
        "initial_policy",
    )
    _require(not machine.path.exists() and not backend.metadata_path.exists(), "machine_metadata")
    selector = {"label": ["omnia.project_machine=true", f"omnia.workspace_id={state.workspace_id}"]}
    _require(
        not backend.client.containers.list(all=True, filters=selector)
        and not backend.client.volumes.list(filters=selector)
        and not backend.client.networks.list(filters=selector),
        "machine_material",
    )
    _require(
        backend._container() is None
        and backend._project_postgres() is None
        and backend._lookup(
            backend.client.volumes, backend.project_postgres_volume, "project-volume"
        )
        is None,
        "machine_material",
    )
    assert operation is not None
    updated = {
        **journal,
        "generation_run_id": str(operation.generation_run_id),
    }
    return updated, machine.path.parent / "initial-database.json", backend


async def recover_initial_database(
    *,
    manager: Any,
    workspace_id: UUID,
    expected_epoch: int,
    initial_operation_id: UUID,
    apply: bool = False,
    expected_journal_digest: str | None = None,
    expected_admission_digest: str | None = None,
) -> dict[str, Any]:
    """Default dry-run; explicit apply changes only one existing controller journal.

    The trusted pending journal is positive provenance: policy installation marks
    it ready before the first guest can start. Adoption requires that provenance,
    its original successful resource operation, and no machine material at all.
    It deliberately excludes partial PostgreSQL installation and all retained apps.
    """
    async with manager.operation_lock.hold(workspace_id):
        state = manager.state_store.load(workspace_id)
        _require(
            state is not None
            and state.workspace_id == workspace_id
            and state.fencing_epoch == expected_epoch,
            "canonical_epoch",
        )
        _require(
            state.active_generation_run_id is None
            and state.active_generation_fencing_epoch is None,
            "generation_active",
        )
        _require(
            state.project_id is not None
            and state.owner_id is not None
            and state.resource_names is not None
            and manager.machine_runtime is not None,
            "workspace_identity",
        )
        updated, path, backend = await machine_effect(
            _assess, manager.machine_runtime, state, initial_operation_id
        )
        manifest = await _source_manifest(manager, backend, state)
        updated["initial_runtime"] = _initial_runtime(backend, manifest)
        before = path.read_bytes()
        _require(
            json.loads(before)
            == {
                key: updated[key]
                for key in (
                    "workspace_id",
                    "project_id",
                    "owner_id",
                    "epoch",
                    "state",
                )
            },
            "journal_changed",
        )
        digest = hashlib.sha256(before).hexdigest()
        admission_digest = hashlib.sha256(
            json.dumps(
                {
                    "journal_digest": digest,
                    "initial_runtime": updated["initial_runtime"],
                    "canonical_epoch": expected_epoch,
                    "initial_operation_id": str(initial_operation_id),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        receipt = {
            "status": "eligible",
            "workspace_id": str(workspace_id),
            "canonical_epoch": expected_epoch,
            "initial_epoch": updated["epoch"],
            "initial_operation_id": str(initial_operation_id),
            "journal_digest": digest,
            "admission_digest": admission_digest,
        }
        if not apply:
            return receipt
        _require(expected_journal_digest == digest, "journal_changed")
        _require(expected_admission_digest == admission_digest, "admission_changed")
        _require(manager.state_store.load(workspace_id) == state, "canonical_state_changed")
        updated["operator_adoption"] = {
            "version": 1,
            "canonical_epoch": expected_epoch,
            "initial_operation_id": str(initial_operation_id),
            "previous_journal_digest": digest,
        }
        await machine_effect(write_controller_json, path, updated)
        return {**receipt, "status": "adopted"}
