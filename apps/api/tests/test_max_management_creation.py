from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.routers import projects
from yleum_api.schemas.project import ProjectCreate


async def test_creation_saves_owner_modules_in_same_project_transaction(monkeypatch):
    owner = SimpleNamespace(id=uuid4())
    added = []

    async def flush():
        for item in added:
            if isinstance(item, Project) and item.id is None:
                item.id = uuid4()

    session = SimpleNamespace(add=added.append, flush=flush)
    monkeypatch.setattr(projects, "require_max_studio_access", AsyncMock())
    monkeypatch.setattr(projects, "assert_can_create_project", AsyncMock())
    monkeypatch.setattr(projects, "admit_new_project_cell", lambda _owner: True)
    monkeypatch.setattr(projects, "classify_preset_sync", lambda **_kw: None)
    monkeypatch.setattr(projects.repo_svc, "init_repo", Mock(return_value="a" * 40))

    async def commit(_session, project, _sha):
        record = next(item for item in added if isinstance(item, MaxProjectConfig))
        assert record.project_id == project.id and record.owner_id == owner.id
        assert record.config["management_modules"] == ["catalog", "orders"]
        assert record.config_version == 1
        return project

    monkeypatch.setattr(projects, "_commit_first_snapshot", commit)
    payload = ProjectCreate.model_validate(
        {
            "name": "Мастерская",
            "max_config": {
                "app_name": "Мастерская",
                "app_type": "catalog",
                "summary": "Заказ услуг",
                "management_modules": ["catalog", "orders"],
            },
        }
    )
    await projects.create_project(payload, session, owner)
