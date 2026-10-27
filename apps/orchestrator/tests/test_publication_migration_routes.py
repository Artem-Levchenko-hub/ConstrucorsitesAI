from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests.test_cell_publication import request
from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.routers import publication_migration as routes
from yleum_orchestrator.schemas.publication_migration import (
    PublicationMigrationConfirm,
    PublicationMigrationPrepare,
)


@pytest.mark.parametrize("endpoint", ["prepare", "confirm"])
async def test_route_project_mismatch_never_dispatches(monkeypatch, endpoint):
    dispatch = AsyncMock()
    monkeypatch.setattr(routes, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(
        routes,
        "get_publication_migration_service",
        lambda: SimpleNamespace(prepare=dispatch, confirm=dispatch),
    )
    with pytest.raises(OrchestratorError) as failure:
        if endpoint == "prepare":
            await routes.prepare_migration(
                UUID(int=77),
                PublicationMigrationPrepare(publication=request(), idempotency_key=uuid4()),
                "internal",
            )
        else:
            await routes.confirm_migration(
                UUID(int=77),
                uuid4(),
                PublicationMigrationConfirm(
                    publication=request(), plan_sha256="a" * 64, explicit_intent=True
                ),
                "internal",
            )
    assert failure.value.status_code == 409
    dispatch.assert_not_awaited()


async def test_get_is_pure_projection_and_sanitizes_conflict(monkeypatch):
    get = Mock(return_value=None)
    monkeypatch.setattr(routes, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(
        routes, "get_publication_migration_service", lambda: SimpleNamespace(get=get)
    )
    assert (await routes.get_migration(request().project_id, request().owner_id)).migration is None
    get.assert_called_once_with(request().project_id, request().owner_id)
    get.side_effect = CellIdentityConflict("secret canary SQL SELECT")
    with pytest.raises(OrchestratorError) as failure:
        await routes.get_migration(request().project_id, request().owner_id)
    assert "canary" not in str(failure.value)


def test_request_rejects_client_sql_and_runtime_secrets():
    with pytest.raises(ValidationError):
        PublicationMigrationPrepare(publication=request(), idempotency_key=uuid4(), sql="SELECT 1")
    with pytest.raises(ValidationError):
        PublicationMigrationPrepare(
            publication=request(runtime_env={"MAX_BOT_TOKEN": "secret"}), idempotency_key=uuid4()
        )


def test_confirmation_requires_a_literal_boolean_intent():
    with pytest.raises(ValidationError):
        PublicationMigrationConfirm(
            publication=request(), plan_sha256="a" * 64, explicit_intent="yes"
        )
