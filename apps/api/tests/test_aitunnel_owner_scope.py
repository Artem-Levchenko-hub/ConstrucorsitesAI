from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from yleum_api.core import crypto
from yleum_api.core.crypto import decrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.routers import app_integrations
from yleum_api.schemas.app_integration import NutritionAIKeyUpdate, PlatformAIState
from yleum_api.services.integration_generation import generation_context


@pytest.fixture
def owner_scope(monkeypatch):
    test_cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(crypto, "_fernet_strong", lambda: test_cipher)
    owner = SimpleNamespace(id=UUID(int=1))
    project = SimpleNamespace(
        id=UUID(int=2),
        owner_id=owner.id,
        template="max_miniapp",
        runtime_ai_enabled=False,
        runtime_aitunnel_ai_enabled=False,
        runtime_nutrition_ai_key_enc=None,
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=project), commit=AsyncMock(), rollback=AsyncMock(),
    )
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _user: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    monkeypatch.setattr(app_integrations, "verify_nutrition_key", AsyncMock())
    return owner, project, session


async def test_owner_enables_existing_key_for_app_ai_and_disconnect_clears_scope(owner_scope):
    owner, project, session = owner_scope
    key = "sk-aitunnel-test-only-key-for-scope"
    saved = await app_integrations.set_nutrition_ai(
        project.id, NutritionAIKeyUpdate(api_key=SecretStr(key)), session, owner,
    )
    assert saved.enabled
    encrypted = project.runtime_nutrition_ai_key_enc
    assert encrypted != key
    assert decrypt_strong(encrypted) == key
    assert project.runtime_aitunnel_ai_enabled is False

    enabled = await app_integrations.set_aitunnel_ai(
        project.id, PlatformAIState(enabled=True), session, owner,
    )
    assert enabled.enabled is True
    assert project.runtime_aitunnel_ai_enabled is True
    assert project.runtime_nutrition_ai_key_enc == encrypted
    assert project.runtime_ai_enabled is False

    await app_integrations.set_nutrition_ai(
        project.id, NutritionAIKeyUpdate(api_key=None), session, owner,
    )
    assert project.runtime_nutrition_ai_key_enc is None
    assert project.runtime_aitunnel_ai_enabled is False


async def test_key_upload_can_explicitly_enable_app_ai_and_rotation_resets_it(owner_scope):
    owner, project, session = owner_scope
    await app_integrations.set_nutrition_ai(
        project.id,
        NutritionAIKeyUpdate(
            api_key=SecretStr("sk-aitunnel-test-key-app-ai"), allow_app_ai=True,
        ),
        session, owner,
    )
    assert project.runtime_aitunnel_ai_enabled is True
    await app_integrations.set_nutrition_ai(
        project.id,
        NutritionAIKeyUpdate(api_key=SecretStr("sk-aitunnel-test-key-new")),
        session, owner,
    )
    assert project.runtime_aitunnel_ai_enabled is False


async def test_app_ai_scope_rejects_missing_key_and_another_account(owner_scope):
    owner, project, session = owner_scope
    with pytest.raises(ApiError) as missing:
        await app_integrations.set_aitunnel_ai(
            project.id, PlatformAIState(enabled=True), session, owner,
        )
    assert missing.value.code == "ai_integration_required"

    with pytest.raises(ApiError) as foreign:
        await app_integrations.set_aitunnel_ai(
            project.id, PlatformAIState(enabled=True), session, SimpleNamespace(id=UUID(int=3)),
        )
    assert foreign.value.status_code == 404
    assert project.runtime_aitunnel_ai_enabled is False
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("aitunnel_enabled", [False, True])
@pytest.mark.parametrize("platform_enabled", [False, True])
async def test_builder_uses_the_real_aitunnel_scope(aitunnel_enabled, platform_enabled):
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        scalar=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=SimpleNamespace(
            one_or_none=lambda: (platform_enabled, aitunnel_enabled, True),
        )),
    )
    guide = await generation_context(session, UUID(int=1))
    assert "nutrition_estimate" in guide
    assert ('provider: "aitunnel"' in guide) is aitunnel_enabled
    assert ("requestYleumAI({message" in guide) is platform_enabled
    if aitunnel_enabled:
        assert "mocked" in guide
        assert "real" in guide
