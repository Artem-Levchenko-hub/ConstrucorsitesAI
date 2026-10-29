"""Explicit legacy opt-in reuses the owner config CAS; it never publishes code."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from yleum_api.core.db import get_session
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError, api_error_handler
from yleum_api.routers import max_management, max_studio
from yleum_api.schemas.max_studio import MaxProjectConfigPayload, MaxProjectConfigPublic
from yleum_api.services import max_legacy_order_management as adoption


@pytest.fixture
def context(monkeypatch):
    application = FastAPI()
    application.include_router(max_management.router)
    application.add_exception_handler(ApiError, api_error_handler)
    owner = SimpleNamespace(id=uuid4(), is_anon=False)
    session = SimpleNamespace()
    project_id = uuid4()
    config = MaxProjectConfigPayload(
        app_name="Сохранённое приложение", app_type="catalog", summary="Исходное описание",
        content=[{"id": "saved-item", "title": "Исторический товар", "price": "250"}],
    )
    current = MaxProjectConfigPublic(project_id=project_id, config=config, config_version=7)
    read = AsyncMock(return_value=current)

    state = SimpleNamespace(legacy=None)

    async def save(pid, payload, db, user, *, x_max_config_version, legacy_orders_adoption=None):
        assert pid == project_id and db is session and user is owner
        assert x_max_config_version == 7
        assert payload.legacy_orders_table == state.legacy == legacy_orders_adoption
        assert payload.content == config.content and payload.summary == config.summary
        assert [module.value for module in payload.management_modules] == [
            "catalog", "orders", "leads",
        ]
        return MaxProjectConfigPublic(project_id=pid, config=payload, config_version=8)

    write = AsyncMock(side_effect=save)
    monkeypatch.setattr(max_studio, "get_max_config", read)
    monkeypatch.setattr(max_studio, "_put_max_config", write, raising=False)
    lock = AsyncMock()
    monkeypatch.setattr(max_studio, "_lock_owned_max_project", lock, raising=False)
    legacy_read = AsyncMock(return_value=None)
    monkeypatch.setattr(adoption, "read_legacy_order_table", legacy_read)
    application.dependency_overrides[get_current_user] = lambda: owner
    application.dependency_overrides[get_session] = lambda: session
    return SimpleNamespace(
        app=application, owner=owner, current=current, read=read, write=write,
        legacy_read=legacy_read, state=state, lock=lock,
        url=f"/api/projects/{project_id}/max/management/enable",
    )


async def request(ctx, body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ctx.app), base_url="https://test",
    ) as client:
        return await client.post(ctx.url, json=body)


async def test_explicit_activation_preserves_content_and_uses_owner_config_cas(context):
    response = await request(context, {"expected_config_version": 7})
    assert response.status_code == 200
    assert response.json()["config_version"] == 8
    assert context.current.config.management_modules is None
    context.read.assert_awaited_once()
    context.write.assert_awaited_once()


async def test_only_the_explicit_action_can_bind_a_checked_legacy_table(context):
    context.legacy_read.return_value = context.state.legacy = "sever_orders"
    response = await request(context, {"expected_config_version": 7})
    assert response.status_code == 200
    assert response.json()["config"]["legacy_orders_table"] == "sever_orders"
    context.legacy_read.assert_awaited_once()


async def test_head_change_without_config_version_change_is_checked_after_project_lock(context):
    head = {"locked": False, "table": "sever_orders"}

    async def lock(*_args):
        head.update(locked=True, table="fresh_orders")

    async def inspect(*_args):
        # A completed generation/draft commit changes the head, not config_version.
        table = head["table"]
        if not head["locked"]:
            head["table"] = "fresh_orders"
        return table

    async def save(pid, payload, *_args, **_kw):
        return MaxProjectConfigPublic(project_id=pid, config=payload, config_version=8)

    context.lock.side_effect = lock
    context.legacy_read.side_effect = inspect
    context.write.side_effect = save
    response = await request(context, {"expected_config_version": 7})
    assert response.status_code == 200
    assert response.json()["config"]["legacy_orders_table"] == "fresh_orders"
    context.lock.assert_awaited_once()


def test_private_writer_requires_the_exact_approved_table_and_cannot_rebind():
    current = MaxProjectConfigPayload.default_for("Старое приложение")
    desired = current.model_copy(update={
        "management_modules": ["catalog", "orders", "leads"],
        "legacy_orders_table": "sever_orders",
    })
    result = max_studio._prepare_config_change(
        current, desired, current_version=7, expected_version=7,
        legacy_orders_adoption="sever_orders",
    )
    assert result.legacy_orders_table == "sever_orders"
    for existing, approved in [
        (current, None), (current, "other_orders"), (desired, "other_orders"),
    ]:
        changed = (
            desired.model_copy(update={"legacy_orders_table": "other_orders"})
            if existing is desired else desired
        )
        with pytest.raises(ApiError):
            max_studio._prepare_config_change(
                existing, changed, current_version=7, expected_version=7,
                legacy_orders_adoption=approved,
            )


@pytest.mark.parametrize("body", [
    {}, {"expected_config_version": True}, {"expected_config_version": "7"},
    {"expected_config_version": -1}, {"expected_config_version": 2**53},
    {"expected_config_version": 7, "owner_id": "other"},
])
async def test_activation_payload_is_strict(context, body):
    response = await request(context, body)
    assert response.status_code == 422
    context.read.assert_not_awaited()
    context.write.assert_not_awaited()


@pytest.mark.parametrize("state", ["anonymous", "missing_session", "foreign", "stale"])
async def test_denial_and_conflicts_never_write_an_old_snapshot(context, state):
    expected = 401
    if state == "anonymous":
        context.owner.is_anon = True
    elif state == "missing_session":
        context.app.dependency_overrides.pop(get_current_user)
    elif state == "foreign":
        expected = 404
        context.read.side_effect = ApiError("not_found", "Не найдено", 404)
    else:
        expected = 409
        context.current.config_version = 8
    response = await request(context, {"expected_config_version": 7})
    assert response.status_code == expected
    context.write.assert_not_awaited()
