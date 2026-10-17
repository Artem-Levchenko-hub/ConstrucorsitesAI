"""Durable candidate writer barrier, stored in the existing controller machine record.

Reading never creates portable state. Unknown barrier formats fail closed. Only the
recovery coordinator changes this record under the candidate operation lock.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.services.cell_state import _read_plain_json_file, _require_regular_file_path


@dataclass(frozen=True)
class _RecoveryTransition:
    path: Path
    transition_id: str
    phase: str


_transition: ContextVar[_RecoveryTransition | None] = ContextVar(
    "candidate_recovery_transition", default=None
)


@contextmanager
def recovery_transition(path: Path, transition_id: UUID, *, phase: str) -> Iterator[None]:
    saved = _read_plain_json_file(path)
    barrier = saved.get("adaptation_recovery_barrier")
    if (
        phase not in {"hold", "rebind", "cleanup"}
        or not isinstance(barrier, dict)
        or barrier.get("version") != 1
        or barrier.get("transition_id") != str(transition_id)
        or barrier.get("phase") != phase
    ):
        raise CellIdentityConflict("recovery lifecycle transition binding changed")
    token = _transition.set(_RecoveryTransition(path, str(transition_id), phase))
    try:
        yield
    finally:
        _transition.reset(token)


def require_machine_writers_allowed(machine_path: Path, *, action: str | None = None) -> None:
    if action is not None:
        from yleum_orchestrator.services.command_receipts import require_machine_commands_clear

        require_machine_commands_clear(machine_path)
    if _require_regular_file_path(machine_path, missing_ok=True) is None:
        return
    saved = _read_plain_json_file(machine_path)
    # Presence is authoritative, including malformed/unknown future versions.
    if "adaptation_recovery_barrier" in saved:
        permit = _transition.get()
        barrier = saved["adaptation_recovery_barrier"]
        permitted_actions = {
            "hold": {"quiesce", "capture", "remove", "exec"},
            "rebind": {"ensure", "restore", "quiesce"},
            "cleanup": {"quiesce", "capture", "remove", "exec", "release", "destroy"},
        }
        if (
            action is not None
            and permit is not None
            and permit.path == machine_path
            and isinstance(barrier, dict)
            and barrier.get("version") == 1
            and barrier.get("transition_id") == permit.transition_id
            and barrier.get("phase") == permit.phase
            and action in permitted_actions[permit.phase]
        ):
            return
        raise CellIdentityConflict("candidate recovery barrier blocks writer authority")


def require_candidate_writers_allowed(
    state_root: Path, workspace_id: UUID, *, action: str | None = None
) -> None:
    from yleum_orchestrator.services.machine_identity import machine_identity_root

    require_machine_writers_allowed(
        machine_identity_root(state_root, workspace_id) / "machine.json", action=action
    )


def save_machine_recovery_state(path: Path, state: dict[str, Any]) -> None:
    from yleum_orchestrator.services.project_machine import write_controller_json
    from yleum_orchestrator.services.source_write_journal import _sync_directory

    write_controller_json(path, state)
    _sync_directory(path.parent)
