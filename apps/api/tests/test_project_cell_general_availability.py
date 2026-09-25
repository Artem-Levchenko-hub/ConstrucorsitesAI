import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.config import Settings, get_settings
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services import project_cell_access as access


@pytest.fixture(autouse=True)
def unit_settings(monkeypatch):
    fallback = False
    for key, value in (
        ("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1/unused"),
        ("JWT_SECRET", "test-only-general-availability-signing-key"),
    ):
        if not os.environ.get(key):
            monkeypatch.setenv(key, value)
            fallback = True
    yield
    if fallback:
        get_settings.cache_clear()


def user(**changes):
    values = dict(
        id=uuid4(),
        email="new-owner@example.test",
        status="active",
        is_anon=False,
        email_verified_at=datetime.now(UTC),
        password_hash="unused",
    )
    return User(**(values | changes))


def test_general_availability_defaults_off_and_does_not_reclassify_legacy():
    settings = Settings(project_cell_general_availability_enabled=True)
    assert hasattr(settings, "project_cell_general_availability_enabled")
    assert Settings.model_fields["project_cell_general_availability_enabled"].default is False
    assert access.admit_new_project_cell(user(), settings) is True
    decision = access.decide_project_cell_selection(user(), settings=settings)
    assert decision.enabled is False


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "suspended"},
        {"is_anon": True},
        {"email_verified_at": None},
        {"email": None},
    ],
)
def test_general_availability_rejects_ineligible_new_admission(changes):
    settings = Settings(project_cell_general_availability_enabled=True)
    assert access.admit_new_project_cell(user(**changes), settings) is False


@pytest.mark.parametrize("marker,workspace", [(True, False), (False, True)])
def test_assigned_provider_survives_admission_switch_rollback(marker, workspace):
    result = access.decide_project_cell_selection(
        user(),
        project_cell_enabled=marker,
        has_workspace=workspace,
        settings=Settings(),
    )
    assert result.enabled is True
    assert result.provider == "docker_owner_canary"


def test_existing_canary_selection_remains_available():
    settings = Settings(
        project_cell_docker_canary_enabled=True, project_cell_canary_emails="new-owner@example.test"
    )
    assert access.decide_project_cell_selection(user(), settings=settings).enabled
    assert access.admit_new_project_cell(user(), settings) is False


async def test_admitted_project_gets_pending_cell_without_remote_or_legacy_start(monkeypatch):
    from yleum_api.services import project_cell_runtime as runtime

    owner = user()
    project = Project(id=uuid4(), owner_id=owner.id, name="new", slug="new", template="max_miniapp")
    project.project_cell_enabled = True
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    monkeypatch.setattr(runtime, "_active_generation", AsyncMock(return_value=None))
    status = await runtime.load_project_cell_runtime_status(session, project, owner=owner)
    assert status is not None


async def test_ga_does_not_change_existing_legacy_runtime(monkeypatch):
    from yleum_api.services import project_cell_runtime as runtime

    owner = user()
    project = Project(id=uuid4(), owner_id=owner.id, name="old", slug="old", template="max_miniapp")
    project.project_cell_enabled = False
    project.current_snapshot_id = uuid4()
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    monkeypatch.setattr(
        access,
        "get_settings",
        lambda: Settings(
            project_cell_general_availability_enabled=True,
        ),
    )
    result = await runtime.resolve_project_cell_public_selection(session, project, owner=owner)
    assert result.selected is False


def test_provider_marker_cannot_be_set_through_public_project_payloads():
    from yleum_api.schemas.project import ProjectCreate, ProjectUpdate

    assert "project_cell_enabled" not in ProjectCreate.model_fields
    assert "project_cell_enabled" not in ProjectUpdate.model_fields


@pytest.mark.parametrize("enabled", [True, False])
async def test_normal_create_persists_server_decision_without_provisioning(monkeypatch, enabled):
    from yleum_api.routers import projects
    from yleum_api.schemas.project import ProjectCreate

    monkeypatch.setattr(
        access,
        "get_settings",
        lambda: Settings(
            project_cell_general_availability_enabled=enabled,
        ),
    )
    inserted = []

    def add(value):
        value.id = uuid4()
        value.created_at = datetime.now(UTC)
        inserted.append(value)

    session = SimpleNamespace(add=add, flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(projects.repo_svc, "init_repo", lambda *_: "a" * 40)
    monkeypatch.setattr(projects, "enqueue_preview", lambda _: None)
    monkeypatch.setattr(projects, "publish_event", AsyncMock())
    # Plan limits read the billing tables; this test owns a stub session.
    monkeypatch.setattr(projects, "assert_can_create_project", AsyncMock())
    created = await projects.create_project(
        ProjectCreate.model_validate(
            {"name": "Test", "template": "max_miniapp", "project_cell_enabled": True}
        ),
        session,
        user(),
    )
    assert created.project_cell_enabled is enabled
    assert created in inserted
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("admitted", [False, True])
async def test_deploy_selection_does_not_reclassify_legacy_when_ga_enabled(monkeypatch, admitted):
    from yleum_api.services import deploy_attestation

    owner = user()
    project = Project(
        id=uuid4(),
        owner_id=owner.id,
        name="test",
        slug="test",
        template="max_miniapp",
        project_cell_enabled=admitted,
    )
    monkeypatch.setattr(
        access,
        "get_settings",
        lambda: Settings(
            project_cell_general_availability_enabled=True,
        ),
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None), get=AsyncMock(return_value=owner)
    )
    result = await deploy_attestation.resolve_deploy_proof(session, project, None)
    assert result.reason == ("project_cell_publish_unavailable" if admitted else "snapshot_missing")


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "suspended"}, "account_ineligible"),
        ({"email_verified_at": None}, "email_unverified"),
    ],
)
async def test_assigned_project_cannot_generate_for_ineligible_account(
    monkeypatch, changes, reason
):
    from yleum_api.services import project_cell_control as control

    remote = AsyncMock()
    monkeypatch.setattr(control, "get_project_cell_capabilities", remote)
    result = await control.inspect_project_cell_control(
        user(**changes), uuid4(), project_cell_enabled=True
    )
    assert result.selected is True and result.ready is False
    assert result.reason == reason
    remote.assert_not_awaited()


async def test_assigned_project_still_enforces_existing_cross_owner_gate():
    from yleum_api.core.errors import ApiError
    from yleum_api.routers.runtime import _project_owned_by

    owner, stranger = user(), user()
    project = Project(
        id=uuid4(),
        owner_id=owner.id,
        name="test",
        slug="test",
        template="max_miniapp",
        project_cell_enabled=True,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    with pytest.raises(ApiError, match="project not found"):
        await _project_owned_by(session, project.id, stranger.id)
