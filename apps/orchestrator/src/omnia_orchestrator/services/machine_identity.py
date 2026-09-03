"""Sticky controller identity is independent of guest files and rollout flags."""

from pathlib import Path
from uuid import UUID


def machine_identity_root(state_root: Path, workspace_id: UUID) -> Path:
    return state_root.parent / "project-machines" / str(workspace_id)


def is_portable_workspace(state_root: Path, workspace_id: UUID) -> bool:
    root = machine_identity_root(state_root, workspace_id)
    return (root / "portable.json").is_file() or (root / "machine.json").is_file()
