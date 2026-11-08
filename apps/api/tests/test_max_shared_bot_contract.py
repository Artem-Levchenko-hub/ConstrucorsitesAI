"""Router behavior with isolated session/provider boundaries, without live MAX."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.routers import max_integrations as router
from yleum_api.routers import max_studio
from yleum_api.schemas.max_integration import MaxConnectRequest
from yleum_api.schemas.max_studio import MaxProjectConfigPayload, MaxUrlAttachedPayload
from yleum_api.services import max_client


@pytest.fixture
def context(monkeypatch):
    owner = SimpleNamespace(id=uuid4())
    project = SimpleNamespace(id=uuid4(), owner_id=owner.id, template="max_miniapp", name="App")
    integration = MaxIntegration(
        id=uuid4(), project_id=project.id, owner_id=owner.id, bot_id="same-bot",
        bot_token_enc=encrypt_strong("revoked-old-token"),
        webhook_secret_enc=encrypt_strong("project-hook-secret"), status="active",
        app_url="https://project.example", webhook_url="https://project.example/api/max/webhook",
    )
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[True, uuid4()]), add=lambda value: None,
        flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock(),
        get=AsyncMock(return_value=None), delete=AsyncMock(), execute=AsyncMock(),
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


def _confirmed_config(project):
    config = MaxProjectConfigPayload.default_for(project.name)
    return MaxProjectConfig(
        project_id=project.id, owner_id=project.owner_id, config_version=3,
        config={**config.model_dump(mode="json"), "max_url_attached": True},
    )


async def test_valid_token_connects_without_global_bot_claim(context):
    owner, project, integration, session = context
    config = _confirmed_config(project)
    session.get.return_value = config
    result = await router.connect_max(
        project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
    )
    assert result.connected and result.bot_id == "same-bot"
    assert decrypt_strong(integration.bot_token_enc) == "validated-new-token"
    assert config.config["max_url_attached"] is True
    assert config.config_version == 3
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
    config = _confirmed_config(project)
    session.get.return_value = config
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
    assert config.config["max_url_attached"] is True
    assert config.config_version == 3
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("previously_connected", [True, False])
async def test_new_bot_clears_historical_confirmation_before_commit(
    context, monkeypatch, previously_connected,
):
    owner, project, integration, session = context
    integration.bot_id = "different-bot"
    router._integration.return_value = integration if previously_connected else None
    config = _confirmed_config(project)
    previous = dict(config.config)
    session.get.return_value = config

    async def check_commit():
        assert config.config == {**previous, "max_url_attached": False}
        assert config.config_version == 4

    session.commit.side_effect = check_commit
    result = await router.connect_max(
        project.id, MaxConnectRequest(token="validated-new-token"), session, owner,
    )
    assert result.bot_id == "same-bot"
    session.commit.assert_awaited_once()
    monkeypatch.setattr(max_studio, "_lock_owned_max_project", AsyncMock(return_value=project))
    with pytest.raises(ApiError) as error:
        await max_studio.put_max_config(
            project.id, MaxProjectConfigPayload.model_validate(previous), session, owner,
            x_max_config_version=3,
        )
    assert error.value.details == {"reason": "config_version_conflict"}


@pytest.mark.parametrize("provider_fails", [True, False])
async def test_disconnect_clears_confirmation_only_when_successful(context, provider_fails):
    owner, project, integration, session = context
    config = _confirmed_config(project)
    session.get.return_value = config
    if provider_fails:
        max_client.unsubscribe.side_effect = max_client.MaxApiUnavailable("MAX недоступен")
        with pytest.raises(ApiError, match="MAX недоступен"):
            await router.disconnect_max(project.id, session, owner)
        assert config.config["max_url_attached"] is True
        assert config.config_version == 3
        session.commit.assert_not_awaited()
    else:
        await router.disconnect_max(project.id, session, owner)
        assert config.config["max_url_attached"] is False
        assert config.config_version == 4
        session.delete.assert_awaited_once_with(integration)
        session.commit.assert_awaited_once()


async def test_old_tab_cannot_confirm_the_replacement_bot(context, monkeypatch):
    owner, project, integration, session = context
    monkeypatch.setattr(max_studio, "_owned_max_project", AsyncMock(return_value=project))
    session.scalar = AsyncMock(return_value=integration)
    config = _confirmed_config(project)
    config.config["max_url_attached"] = False
    session.get.return_value = config
    with pytest.raises(ApiError) as error:
        await max_studio.patch_max_url_attached(
            project.id, MaxUrlAttachedPayload(attached=True, expected_bot_id="old-bot"),
            session, owner,
        )
    assert error.value.status_code == 409
    assert config.config["max_url_attached"] is False
    session.commit.assert_not_awaited()

    saved = await max_studio.patch_max_url_attached(
        project.id, MaxUrlAttachedPayload(attached=True, expected_bot_id="same-bot"),
        session, owner,
    )
    assert saved.config.max_url_attached is True
