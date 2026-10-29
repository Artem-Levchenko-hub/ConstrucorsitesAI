from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.routers import projects
from yleum_api.schemas.project import ProjectCreate


@pytest.fixture
def creation_context(monkeypatch):
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
    monkeypatch.setattr(projects, "_commit_first_snapshot", AsyncMock())
    return SimpleNamespace(owner=owner, added=added, session=session)


async def test_creation_saves_owner_modules_in_same_project_transaction(
    monkeypatch, creation_context,
):
    owner, added, session = (
        creation_context.owner, creation_context.added, creation_context.session,
    )

    async def commit(_session, project, _sha):
        record = next(item for item in added if isinstance(item, MaxProjectConfig))
        assert record.project_id == project.id and record.owner_id == owner.id
        assert record.config["management_modules"] == ["catalog", "orders", "leads"]
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


@pytest.mark.parametrize(
    ("app_type", "modules", "expected"),
    [
        (None, None, ["catalog", "leads"]),
        ("custom", None, ["catalog", "leads"]),
        ("event", [], ["catalog", "leads"]),
        ("education", ["leads"], ["catalog", "leads"]),
        ("catalog", None, ["catalog", "orders", "leads"]),
        ("catalog", ["catalog", "leads"], ["catalog", "leads"]),
        ("loyalty", [], ["catalog", "orders", "leads"]),
        ("booking", None, ["catalog", "leads", "bookings"]),
        ("custom", ["catalog", "bookings"], ["catalog", "leads", "bookings"]),
    ],
)
async def test_every_new_project_has_management_even_from_older_clients(
    creation_context, app_type, modules, expected,
):
    owner, added, session = (
        creation_context.owner, creation_context.added, creation_context.session,
    )
    request = {"name": "Новый проект"}
    if app_type is not None:
        request["max_config"] = {
            "app_name": "Новый проект", "app_type": app_type, "summary": "Работа с клиентами",
        }
        if modules is not None:
            request["max_config"]["management_modules"] = modules
    await projects.create_project(ProjectCreate.model_validate(request), session, owner)
    config = next((item for item in added if isinstance(item, MaxProjectConfig)), None)
    assert config is not None, "Creation must persist a management contract before the first build"
    assert config.config["management_modules"] == expected
    assert config.owner_id == owner.id and config.config_version == 1
