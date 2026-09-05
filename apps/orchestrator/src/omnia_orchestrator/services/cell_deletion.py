"""Durable source deletion fence independent of the API project's lifetime."""

from pathlib import Path
from uuid import UUID

from omnia_orchestrator.core.cell_resources import CellFenceRejected, LifecycleMutation
from omnia_orchestrator.services.cell_state import _fsync_directory
from omnia_orchestrator.services.project_machine import write_controller_json


def deletion_marker_path(state_path: str | Path, workspace_id: UUID) -> Path:
    return Path(state_path).parent / "cell-deletions" / f"{workspace_id}.json"


def is_workspace_deleted(state_path: str | Path, workspace_id: UUID) -> bool:
    path = deletion_marker_path(state_path, workspace_id)
    return path.exists() or path.is_symlink()


def require_workspace_not_deleted(state_path: str | Path, workspace_id: UUID) -> None:
    if is_workspace_deleted(state_path, workspace_id):
        raise CellFenceRejected("workspace deletion is permanent; mutation is blocked")


def mark_workspace_deleted(
    state_path: str | Path,
    workspace_id: UUID,
    mutation: LifecycleMutation,
) -> None:
    """Caller holds the workspace operation lock and has passed lifecycle fencing."""
    path = deletion_marker_path(state_path, workspace_id)
    if path.is_symlink():
        raise CellFenceRejected("unsafe workspace deletion marker")
    if path.exists():
        return
    write_controller_json(
        path,
        {
            "workspace_id": str(workspace_id),
            "operation_id": str(mutation.operation_id),
            "fencing_epoch": mutation.fencing_epoch,
        },
    )
    _fsync_directory(path.parent)
