from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.core.errors import ApiError
from yleum_api.routers import publication_migrations as routes
from yleum_api.schemas.publication_migrations import MigrationConfirmation, MigrationPreparation
from yleum_api.services import publication_migrations as migrations


def status(project_id, **changes):
    return {
        "operation_id": str(uuid4()), "project_id": str(project_id),
        "snapshot_id": str(uuid4()), "state": "prepared", "plan_sha256": "a" * 64,
        "fields": [{"table": "orders", "name": "pickup_at", "type": "pg_catalog.timestamptz"}],
        "backup_verified": True, "error_code": None, "expires_at": None,
        "effect_possible": False, **changes,
    }


@pytest.mark.asyncio
async def test_status_refuses_other_project_and_private_payload(monkeypatch):
    project_id, owner_id = uuid4(), uuid4()
    upstream = AsyncMock(return_value={"migration": status(uuid4())})
    monkeypatch.setattr(migrations.orchestrator_client, "get_publication_migration", upstream)
    with pytest.raises(ApiError) as exc:
        await migrations.get_status(project_id, owner_id)
    assert exc.value.code == "orchestrator_unavailable"
    upstream.return_value = {"migration": status(project_id, sql="SELECT private_values")}
    with pytest.raises(ApiError):
        await migrations.get_status(project_id, owner_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("state,effect", [("preparing", False), ("applying", False),
                                          ("reconciling", True), ("failed", True),
                                          ("blocked", True)])
async def test_admission_blocks_active_and_unknown_effects(monkeypatch, state, effect):
    project_id = uuid4()
    workspace = SimpleNamespace(owner_id=uuid4())
    session = SimpleNamespace(scalar=AsyncMock(return_value=workspace))
    monkeypatch.setattr(migrations, "get_status", AsyncMock(return_value=
        migrations.MigrationStatus.model_validate(status(project_id, state=state,
                                                         effect_possible=effect))))
    with pytest.raises(ApiError) as exc:
        await migrations.assert_no_active_migration(session, project_id)
    assert exc.value.code == "publication_migration_active"


@pytest.mark.asyncio
async def test_admission_ignores_legacy_and_allows_prepared_plan_to_become_stale(monkeypatch):
    project_id = uuid4()
    upstream = AsyncMock(return_value=None)
    monkeypatch.setattr(migrations, "get_status", upstream)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    await migrations.assert_no_active_migration(session, project_id)
    upstream.assert_not_awaited()
    session.scalar.return_value = SimpleNamespace(owner_id=uuid4())
    upstream.return_value = migrations.MigrationStatus.model_validate(status(project_id))
    await migrations.assert_no_active_migration(session, project_id)


def test_confirmation_requires_real_explicit_intent_and_pinned_plan():
    with pytest.raises(ValidationError):
        MigrationConfirmation(plan_sha256="a" * 64, explicit_intent="yes")
    with pytest.raises(ValidationError):
        MigrationConfirmation(plan_sha256="a" * 64, explicit_intent=True,
                              snapshot_id=str(uuid4()))


def test_only_verified_current_receipt_releases_the_old_migration_failure():
    project_id, snapshot_id = uuid4(), uuid4()
    record = status(project_id, state="applied", snapshot_id=str(snapshot_id))
    current = migrations.MigrationStatus.model_validate(record)
    assert migrations.applied_to_snapshot(current, project_id, snapshot_id)
    for changes in ({"project_id": str(uuid4())}, {"snapshot_id": str(uuid4())},
                    {"state": "prepared"}, {"effect_possible": True},
                    {"backup_verified": False}, {"plan_sha256": None}):
        operation = migrations.MigrationStatus.model_validate({**record, **changes})
        assert not migrations.applied_to_snapshot(operation, project_id, snapshot_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "prepare", "confirm"])
async def test_owner_gate_precedes_controller_access(monkeypatch, action):
    project_id, owner_id = uuid4(), uuid4()
    owned = AsyncMock(side_effect=ApiError("not_found", "project not found", 404))
    upstream = AsyncMock()
    monkeypatch.setattr(routes, "_owned_max_project", owned)
    monkeypatch.setattr(routes.service, "get_status", upstream)
    monkeypatch.setattr(routes.service, "publication_evidence", upstream)
    session, owner = object(), SimpleNamespace(id=owner_id)
    with pytest.raises(ApiError) as exc:
        if action == "status":
            await routes.status(project_id, session, owner)
        elif action == "prepare":
            await routes.prepare(project_id, MigrationPreparation(idempotency_key=uuid4()),
                                 session, owner)
        else:
            await routes.confirm(project_id, uuid4(),
                                 MigrationConfirmation(plan_sha256="a" * 64, explicit_intent=True),
                                 session, owner)
    assert exc.value.status_code == 404
    owned.assert_awaited_once_with(session, project_id, owner_id)
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_forwards_only_current_server_evidence(monkeypatch):
    project_id, owner_id, snapshot_id, operation_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(id=project_id, owner_id=owner_id, current_snapshot_id=snapshot_id)
    evidence = {"snapshot_id": str(snapshot_id), "source_revision": "b" * 64}
    monkeypatch.setattr(routes, "_owned_max_project", AsyncMock(return_value=project))
    monkeypatch.setattr(routes.service, "publication_evidence", AsyncMock(return_value=evidence))
    upstream = AsyncMock(return_value=status(project_id, snapshot_id=str(snapshot_id),
                                             operation_id=str(operation_id), state="applying"))
    monkeypatch.setattr(routes.orchestrator_client, "confirm_publication_migration", upstream)
    result = await routes.confirm(project_id, operation_id,
                                  MigrationConfirmation(plan_sha256="a" * 64, explicit_intent=True),
                                  object(), SimpleNamespace(id=owner_id))
    assert result.operation_id == operation_id
    upstream.assert_awaited_once_with(project_id, operation_id, {
        "publication": evidence, "plan_sha256": "a" * 64, "explicit_intent": True,
    })
