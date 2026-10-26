from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.user import User
from yleum_api.routers import restorations as routes
from yleum_api.services import phone_verification


@pytest.mark.parametrize("method", ["prepare_restoration", "apply_restoration"])
async def test_phone_gate_rejects_new_restoration_before_effects(monkeypatch, method):
    monkeypatch.setattr(
        phone_verification, "get_settings", lambda: SimpleNamespace(phone_verification_enabled=True)
    )
    monkeypatch.setattr(routes, "_enabled_owned", AsyncMock())
    effect = AsyncMock(return_value=SimpleNamespace(applied_snapshot_id=None))
    monkeypatch.setattr(
        routes.restorations,
        "create_restoration" if method == "prepare_restoration" else "apply_restoration",
        effect,
    )
    monkeypatch.setattr(routes, "_with_snapshot", AsyncMock(return_value="result"))
    user = User(id=uuid4(), email="owner@example.test", is_anon=False)
    kwargs = dict(
        project_id=uuid4(), body=SimpleNamespace(), session=AsyncMock(), current_user=user
    )
    if method == "apply_restoration":
        kwargs["operation_id"] = uuid4()
    with pytest.raises(ApiError) as raised:
        await getattr(routes, method)(**kwargs)
    assert raised.value.code == "phone_verification_required"
    effect.assert_not_awaited()
