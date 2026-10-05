from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.routers import max_studio


async def readiness(monkeypatch, deployment):
    owner = SimpleNamespace(id=UUID(int=1))
    snapshot = SimpleNamespace(
        id=UUID(int=3), commit_sha="a" * 40, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    project = SimpleNamespace(
        id=UUID(int=2), owner_id=owner.id, template="max_miniapp", current_snapshot_id=snapshot.id
    )

    class Session:
        async def get(self, model, identity):
            if model is Project:
                return project
            if model is Snapshot:
                return snapshot
            assert model is MaxProjectConfig
            return None

        async def execute(self, statement):
            if statement.column_descriptions[0].get("entity") is Project:
                params = statement.compile().params
                assert params["id_1"] == project.id and params["owner_id_1"] == owner.id
                return SimpleNamespace(scalar_one_or_none=lambda: project)
            return SimpleNamespace(scalar_one_or_none=lambda: None, scalar_one=lambda: 1)

    async def get_deploy(project_id):
        assert project_id == project.id
        return deployment

    async def selection(*args, **kwargs):
        return SimpleNamespace(selected=False)

    monkeypatch.setattr(max_studio.orchestrator_client, "get_deploy", get_deploy)
    monkeypatch.setattr(
        max_studio.project_cell_runtime, "resolve_project_cell_public_selection", selection
    )
    return await max_studio.get_max_readiness(project.id, Session(), owner)


@pytest.mark.asyncio
async def test_current_migration_failure_blocks_server_build_readiness_not_only_frontend(
    monkeypatch,
):
    result = await readiness(
        monkeypatch,
        {
            "phase": "failed",
            "reason_code": "migration_required",
            "snapshot_id": str(UUID(int=3)),
            "commit_sha": "a" * 40,
        },
    )
    assert next(v for v in result.items if v.id == "build").done is False
    assert result.publication_migration.status == "verification_required"
    assert result.publication_migration.execution_available is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deployment",
    [
        {"phase": "failed", "reason_code": "migration_required", "snapshot_id": str(UUID(int=9))},
        {
            "phase": "failed",
            "reason_code": "service_readiness_failed",
            "snapshot_id": str(UUID(int=3)),
        },
        {"phase": "building", "reason_code": "migration_required", "snapshot_id": str(UUID(int=3))},
    ],
)
async def test_historical_other_failure_or_active_attempt_does_not_poison_new_build(
    monkeypatch, deployment
):
    result = await readiness(monkeypatch, deployment)
    assert next(v for v in result.items if v.id == "build").done is True
    assert result.publication_migration is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deployment",
    [
        {"phase": "failed", "reason_code": "migration_required"},
        {
            "phase": "failed",
            "reason_code": "migration_required",
            "snapshot_id": str(UUID(int=3)),
            "commit_sha": "b" * 40,
        },
    ],
)
async def test_missing_or_conflicting_failure_identity_blocks_with_unknown_stage(
    monkeypatch, deployment
):
    result = await readiness(monkeypatch, deployment)
    assert next(v for v in result.items if v.id == "build").done is False
    assert result.publication_migration.status == "identity_unconfirmed"
    assert result.publication_migration.execution_available is False
