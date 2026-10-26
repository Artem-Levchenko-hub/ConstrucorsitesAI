from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.schemas.message import PromptRequest
from yleum_api.services import phone_verification
from yleum_api.services.generation.acceptance import PromptAcceptance
from yleum_api.services.max_access import require_existing_max_account_access


@pytest.mark.parametrize("verified,enabled", [(False, True), (True, True), (False, False)])
async def test_generation_phone_policy_runs_before_any_reservation(monkeypatch, verified, enabled):
    monkeypatch.setattr(
        phone_verification,
        "get_settings",
        lambda: SimpleNamespace(phone_verification_enabled=enabled),
    )
    user = User(
        id=uuid4(),
        email="person@example.test",
        is_anon=False,
        phone_e164="+79991234567" if verified else None,
        phone_verified_at=object() if verified else None,
    )
    project = Project(id=uuid4(), owner_id=user.id, template="max-miniapp-nextjs")
    acceptance = PromptAcceptance(
        project_id=project.id,
        payload=PromptRequest(prompt="Измени заголовок"),
        session=AsyncMock(),
        current_user=user,
        project=project,
    )
    reserve = AsyncMock(return_value="existing receipt")
    monkeypatch.setattr(acceptance, "reserve_or_replay", reserve)
    if enabled and not verified:
        with pytest.raises(ApiError) as raised:
            await acceptance.accept()
        assert raised.value.code == "phone_verification_required"
        reserve.assert_not_awaited()
    else:
        assert await acceptance.accept() == "existing receipt"
        reserve.assert_awaited_once()


@pytest.mark.parametrize(
    "email_verified,phone_verified,enabled,reason",
    [
        (False, False, False, "email_verification_required"),
        (True, False, True, "phone_verification_required"),
        (False, True, False, None),
        (False, True, True, None),
    ],
)
def test_existing_integrations_preserve_proof_during_rollout(
    monkeypatch,
    email_verified,
    phone_verified,
    enabled,
    reason,
):
    monkeypatch.setattr(
        phone_verification,
        "get_settings",
        lambda: SimpleNamespace(phone_verification_enabled=enabled),
    )
    user = User(
        id=uuid4(),
        email="owner@example.test",
        is_anon=False,
        email_verified_at=object() if email_verified else None,
        phone_e164="+79991234567" if phone_verified else None,
        phone_verified_at=object() if phone_verified else None,
    )
    if reason:
        with pytest.raises(ApiError) as raised:
            require_existing_max_account_access(user)
        assert raised.value.code == reason
    else:
        require_existing_max_account_access(user)
