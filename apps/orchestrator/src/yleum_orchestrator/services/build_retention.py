"""Current lifecycle roots for rebuildable Project Cell materials.

An unrecognised or unresolved lifecycle keeps all envelopes. Completed operation
history is evidence, not a perpetual owner of every former environment archive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from yleum_orchestrator.core.cell_resources import validate_checkpoint_ref
from yleum_orchestrator.services.cell_state import CellWorkspaceState

LIFECYCLE_KINDS = frozenset(
    {
        "ensure",
        "wake",
        "pause",
        "checkpoint",
        "restore",
        "destroy",
        "release",
        "reconcile",
        "restoration_rejection",
        "code_restore",
    }
)
TERMINAL_LIFECYCLE_PHASES = frozenset({"completed", "failed", "rejected"})


@dataclass(frozen=True, slots=True)
class CheckpointSelection:
    volume: str
    workspace_id: str
    refs: frozenset[str] | None
    optional_missing: bool = False


def _references(operation: Any) -> frozenset[str]:
    refs = {operation.checkpoint_ref} if operation.checkpoint_ref else set()
    for name in ("checkpoint_ref", "deletion_checkpoint_ref"):
        value = (operation.observed_resources or {}).get(name)
        if value:
            refs.add(value)
    for ref in refs:
        validate_checkpoint_ref(ref)
    return frozenset(refs)


def _confirmed_deleted(state: CellWorkspaceState, marker: object | None) -> bool:
    if not isinstance(marker, dict) or set(marker) != {
        "workspace_id",
        "operation_id",
        "fencing_epoch",
    }:
        return False
    if (
        marker["workspace_id"] != str(state.workspace_id)
        or type(marker["fencing_epoch"]) is not int
        or not 0 < marker["fencing_epoch"] <= state.fencing_epoch
    ):
        return False
    try:
        marker_id = UUID(marker["operation_id"])
    except (ValueError, TypeError, AttributeError):
        return False
    if str(marker_id) != marker["operation_id"]:
        return False
    operation = state.operation(marker_id)
    current = state.operation(state.last_operation_id)
    return bool(
        operation
        and operation.kind == "destroy"
        and operation.fencing_epoch == marker["fencing_epoch"]
        and current
        and current.kind == "destroy"
        and current.status == "completed"
        and current.fencing_epoch == state.fencing_epoch
        and state.bundle_state == "retained"
    )


def checkpoint_selection(
    root: Path,
    path: Path,
    payload: object,
    deletion_marker: object | None = None,
) -> CheckpointSelection | None:
    """Choose only proven current envelopes, never guess from mtime or filename."""
    if (
        path.parent != root / "project-cells"
        or not isinstance(payload, dict)
        or set(payload) != {"version", "workspace"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["workspace"], dict)
    ):
        return None
    try:
        state = CellWorkspaceState.from_dict(payload["workspace"])
    except (ValueError, TypeError, RuntimeError, KeyError):
        return None
    names = state.resource_names
    if (
        path.name != f"{state.workspace_id}.json"
        or names is None
        or names.workspace_id != state.workspace_id
        or state.project_id is None
        or state.owner_id is None
    ):
        return None
    all_envelopes = CheckpointSelection(names.checkpoint_volume, str(state.workspace_id), None)
    epochs = [op.fencing_epoch for op in state.operations]
    if (
        epochs != sorted(epochs)
        or any(epoch > state.fencing_epoch for epoch in epochs)
        or len({op.operation_id for op in state.operations}) != len(state.operations)
        or any(op.kind not in LIFECYCLE_KINDS for op in state.operations)
    ):
        return all_envelopes
    current = state.operation(state.last_operation_id)
    if (
        current is None
        or state.phase not in TERMINAL_LIFECYCLE_PHASES
        or current.phase not in TERMINAL_LIFECYCLE_PHASES
    ):
        return all_envelopes
    if _confirmed_deleted(state, deletion_marker):
        return CheckpointSelection(names.checkpoint_volume, str(state.workspace_id), None, True)
    if (
        state.active_generation_run_id is not None
        or state.active_generation_fencing_epoch is not None
        or state.bundle_state not in {"resources_ready", "resources_paused"}
        or current.fencing_epoch != state.fencing_epoch
        or any(op.status not in {"completed", "failed"} for op in state.operations)
        or (current.kind == "restore" and current.status == "failed")
    ):
        return all_envelopes
    # A duplicate completed command must not revive its historical checkpoint.
    # Select the latest sealed/current target in the durable lifecycle journal.
    for operation in reversed(state.operations):
        refs = _references(operation)
        if operation.status == "completed" and refs:
            if operation.kind not in {"checkpoint", "pause", "wake", "restore", "destroy"}:
                return all_envelopes
            return CheckpointSelection(names.checkpoint_volume, str(state.workspace_id), refs)
    return all_envelopes
