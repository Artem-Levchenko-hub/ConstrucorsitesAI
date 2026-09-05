from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError, LifecycleMutation
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.project_machine import write_controller_json
from tests.test_docker_cell_resources import _make_manager


def test_public_budget_is_separate_and_does_not_resize_editor(tmp_path):
    from omnia_orchestrator.services.cell_publication_capacity import production_manager

    manager, _, _, _ = _make_manager(tmp_path)
    manager.profile = replace(manager.profile, profile_version="docker-owner-cell-resources-v2")
    before = manager.profile
    public = production_manager(manager, SimpleNamespace())
    assert manager.profile is before
    assert public.profile.full_quota.cpu_cores == pytest.approx(1.8)
    assert public.profile.helper_cpu_cores >= 0.2
    assert public.profile.full_quota.memory_bytes == 4160749568
    assert public.profile.host_memory_reserve_bytes == before.host_memory_reserve_bytes
    assert public.profile.host_cpu_reserve_cores == before.host_cpu_reserve_cores
    assert public.capacity_reservations is manager.capacity_reservations
    assert public.admission_gate.profile is public.profile
    assert public.machine_runtime.manager is public


async def test_ordinary_provider_cannot_turn_public_workspace_into_an_editor(tmp_path):
    manager, _, _, _ = _make_manager(tmp_path)
    workspace_id, project_id, owner_id = uuid4(), uuid4(), uuid4()
    path = tmp_path / "runtime-state" / "cell-publications" / "identities" / f"{workspace_id}.json"
    write_controller_json(path, {"production_workspace_id": str(workspace_id)})
    provider = DockerOwnerCanaryProvider(resource_manager=manager)
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        profile_version=manager.profile.profile_version,
    )
    mutation = LifecycleMutation(uuid4(), 1, "a" * 64)
    with pytest.raises(CellResourceError, match="publication"):
        await provider.ensure(spec, mutation)
    assert manager.state_store.load(workspace_id) is None
