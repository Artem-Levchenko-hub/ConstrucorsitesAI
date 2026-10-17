"""Scoped helper compatibility for an exact controller-selected restored code volume."""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from yleum_orchestrator.core.cell_resources import CellIdentityConflict, identity_labels
from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
from yleum_orchestrator.services.adaptation_recovery_barrier import (
    require_candidate_writers_allowed,
)
from yleum_orchestrator.services.cell_state import _read_plain_json_file
from yleum_orchestrator.services.machine_identity import machine_identity_root

_PATTERN = re.compile(r"omnia-machine-(?:(test)-)?([0-9a-f]{32})-code-[0-9a-f]{32}")
_ACTIONS = {
    "read_volume_files",
    "read_workspace_source_files",
    "write_volume_files",
    "delete_volume_paths",
    "source_volume_identity",
}


@dataclass(frozen=True)
class _Authority:
    manager: Any
    state: Any
    name: str
    namespace: str
    incarnation: str | None = None

    def validate(self) -> dict[str, str]:
        current = self.manager.state_store.load(self.state.workspace_id)
        if current != self.state or self.manager.namespace != self.namespace:
            raise CellIdentityConflict("active source controller authority changed")
        require_candidate_writers_allowed(self.manager.state_store.root, self.state.workspace_id)
        metadata = _read_plain_json_file(
            machine_identity_root(self.manager.state_store.root, self.state.workspace_id)
            / "docker.json"
        )
        if metadata.get("active_code_volume") != self.name:
            raise CellIdentityConflict("active source selection changed")
        return {
            "omnia.managed": "true",
            "omnia.project_machine": "true",
            "omnia.workspace_id": str(current.workspace_id),
            "omnia.project_id": str(current.project_id),
            "omnia.owner_id": str(current.owner_id),
            "omnia.resource_kind": "project-volume",
            "omnia.namespace": self.namespace,
        }


_authority: ContextVar[_Authority | None] = ContextVar("active_source_authority", default=None)


def active_source_identity(name: str) -> str | None:
    authority = _authority.get()
    if authority is None or authority.name != name:
        return None
    authority.validate()
    return authority.incarnation


def active_source_helper_labels(name: str, labels: dict[str, str]) -> dict[str, str]:
    # Normal workspace labels retain the existing strict validator and requirements.
    if labels.get("omnia.project_cell") == "true":
        return labels
    authority = _authority.get()
    if authority is None or authority.name != name:
        raise CellIdentityConflict("restored source helper requires controller authority")
    expected = authority.validate()
    if any(labels.get(key) != value for key, value in expected.items()):
        raise CellIdentityConflict("restored source volume ownership changed")
    state = authority.state
    return {
        **labels,
        **identity_labels(
            WorkspaceSpec(
                workspace_id=state.workspace_id,
                project_id=state.project_id,
                owner_id=state.owner_id,
                profile_version=state.profile_version,
            ),
            "workspace",
        ),
    }


async def source_volume_call(manager: Any, name: str, action: str, *args: Any) -> Any:
    if action not in _ACTIONS:
        raise CellIdentityConflict("unsupported scoped source operation")
    from yleum_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend

    # This adapter supplies authority only to DockerPy's strict helper-label path.
    # Other implementations retain their own source-volume protocol unchanged.
    if not isinstance(manager.docker, DockerPyCellBackend):
        return await getattr(manager.docker, action)(name, *args)
    match = _PATTERN.fullmatch(name)
    if match is None:
        return await getattr(manager.docker, action)(name, *args)
    namespace = "test" if match[1] else "prod"
    state = manager.state_store.load(UUID(hex=match[2]))
    if (
        state is None
        or state.project_id is None
        or state.owner_id is None
        or manager.namespace != namespace
        or state.phase == "indeterminate"
    ):
        raise CellIdentityConflict("active source controller identity unavailable")
    if action in {"write_volume_files", "delete_volume_paths"} and (
        state.active_generation_run_id is None or state.active_generation_fencing_epoch is None
    ):
        raise CellIdentityConflict("active source write requires current generation lease")
    authority = _Authority(manager, state, name, namespace)
    authority.validate()
    token = _authority.set(authority)
    try:
        volume = await manager.docker.get_volume(name)
        if volume is None or volume.name != name:
            raise CellIdentityConflict("active source volume is missing")
        active_source_helper_labels(name, volume.labels)
        incarnation = await manager.docker.source_volume_identity(name)
        if action == "source_volume_identity":
            authority.validate()
            return incarnation
        _authority.set(replace(authority, incarnation=incarnation))
        result = await getattr(manager.docker, action)(name, *args)
        authority.validate()
        if await manager.docker.source_volume_identity(name) != incarnation:
            raise CellIdentityConflict("active source volume incarnation changed")
        return result
    finally:
        _authority.reset(token)
