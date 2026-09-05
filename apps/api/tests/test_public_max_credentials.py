from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.routers import max_integrations as routes


@pytest.mark.parametrize("deleted", [False, True])
async def test_delayed_connect_sync_uses_locked_current_record_not_stale_credentials(
    monkeypatch,
    deleted,
):
    from omnia_api.services import cell_publication

    project = SimpleNamespace(id=uuid4(), owner_id=uuid4())
    stale = SimpleNamespace(bot_token_enc="stale", owner_id=project.owner_id)
    current = (
        None if deleted else SimpleNamespace(bot_token_enc="current", owner_id=project.owner_id)
    )
    events = []

    async def lock(session, project_id):
        events.append("lock")

    async def read(statement):
        assert events == ["lock"]
        assert statement.get_execution_options().get("populate_existing") is True
        events.append("read")
        return current

    session = SimpleNamespace(scalar=AsyncMock(side_effect=read))
    selection = SimpleNamespace(selected=True)
    monkeypatch.setattr(
        routes.project_cell_runtime,
        "resolve_project_cell_public_selection",
        AsyncMock(return_value=selection),
    )
    monkeypatch.setattr(routes.project_cell_runtime, "_try_preview_project_lock", lock)
    update = AsyncMock()
    monkeypatch.setattr(cell_publication, "update_public_credentials", update)
    await routes._sync_public_cell_auth(session, project, stale)
    assert events == ["lock", "read"]
    update.assert_awaited_once_with(project.id, project.owner_id, current)
