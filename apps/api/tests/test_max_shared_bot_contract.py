"""Router behavior with isolated session/provider boundaries, without live MAX."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.routers import max_integrations as router
from yleum_api.schemas.max_integration import MaxConnectRequest
from yleum_api.services import max_client


@pytest.fixture
def context(monkeypatch):
    owner = SimpleNamespace(id=uuid4())
    project = SimpleNamespace(id=uuid4(), owner_id=owner.id, template="max_miniapp")
    integration = MaxIntegration(
        id=uuid4(), project_id=project.id, owner_id=owner.id, bot_id="same-bot",
        bot_token_enc=encrypt_strong("revoked-old-token"),
        webhook_secret_enc=encrypt_strong("project-hook-secret"), status="active",
        app_url="https://project.example", webhook_url="https://project.example/api/max/webhook",
    )
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[True, uuid4()]), add=lambda value: None,
        flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock(),
    )
    monkeypatch.setattr(router, "_owned_project", AsyncMock(return_value=project))
    monkeypatch.setattr(router, "_integration", AsyncMock(return_value=integration))
    monkeypatch.setattr(router, "_lock_public_cell_auth", AsyncMock(return_value=False))
    monkeypatch.setattr(router, "_sync_public_cell_auth", AsyncMock())
    monkeypatch.setattr(
        max_client, "get_me", AsyncMock(return_value=max_client.MaxBot("same-bot", "Bot", "bot")),
    )
    monkeypatch.setattr(max_client, "unsubscribe", AsyncMock())
    return owner, project, integration, session


async def test_valid_token_connects_without_global_bot_claim(context):
    owner, project, integration, session = context
    result = await router.connect_max(
        project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
    )
    assert result.connected and result.bot_id == "same-bot"
    assert decrypt_strong(integration.bot_token_enc) == "validated-new-token"
    # No cross-project reservation/query: ownership and locks belong to the project.
    session.scalar.assert_not_awaited()


@pytest.mark.parametrize("previous,expected", [
    ("active", "active"), ("verified", "verified"), ("error", "verified"),
])
async def test_same_token_reconnect_preserves_only_confirmed_active_status(
    context, previous, expected,
):
    owner, project, integration, session = context
    integration.status = previous
    before = (integration.bot_token_enc, integration.webhook_secret_enc, integration.webhook_url)
    result = await router.connect_max(
        project.id, MaxConnectRequest(token="revoked-old-token"), session, owner,
    )
    assert result.status == expected
    assert (
        integration.bot_token_enc, integration.webhook_secret_enc, integration.webhook_url
    ) == before
    max_client.unsubscribe.assert_not_awaited()


@pytest.mark.parametrize("previous_bot", ["same-bot", "different-bot"])
async def test_rotation_uses_authority_of_the_corresponding_bot(context, previous_bot):
    owner, project, integration, session = context
    integration.bot_id = previous_bot
    session.scalar.side_effect = [True, None]
    await router.connect_max(
        project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
    )
    expected_token = "validated-new-token" if previous_bot == "same-bot" else "revoked-old-token"
    max_client.unsubscribe.assert_awaited_once_with(
        expected_token, "https://project.example/api/max/webhook",
    )
    assert integration.webhook_url is None
    assert decrypt_strong(integration.bot_token_enc) == "validated-new-token"


@pytest.mark.parametrize("previous_bot,allowed", [("different-bot", True), ("same-bot", False)])
async def test_only_replaced_bot_revoked_token_can_be_ignored(context, previous_bot, allowed):
    owner, project, integration, session = context
    integration.bot_id = previous_bot
    session.scalar.side_effect = [True, None]
    max_client.unsubscribe.side_effect = max_client.MaxTokenInvalid("MAX отклонил токен бота")
    if allowed:
        result = await router.connect_max(
            project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
        )
        assert result.connected and result.bot_id == "same-bot"
    else:
        with pytest.raises(ApiError) as error:
            await router.connect_max(
                project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
            )
        assert error.value.code == "max_token_invalid"
        assert decrypt_strong(integration.bot_token_enc) == "revoked-old-token"


async def test_network_failure_keeps_existing_project_credentials(context):
    owner, project, integration, session = context
    integration.bot_id = "different-bot"
    session.scalar.side_effect = [True, None]
    before = (integration.bot_id, integration.bot_token_enc, integration.webhook_secret_enc,
              integration.app_url, integration.webhook_url, integration.status)
    max_client.unsubscribe.side_effect = max_client.MaxApiUnavailable("MAX недоступен")
    with pytest.raises(ApiError) as error:
        await router.connect_max(
            project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
        )
    assert error.value.code == "max_api_unavailable"
    assert (integration.bot_id, integration.bot_token_enc, integration.webhook_secret_enc,
            integration.app_url, integration.webhook_url, integration.status) == before
    session.commit.assert_not_awaited()
