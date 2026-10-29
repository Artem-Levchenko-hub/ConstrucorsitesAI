"""HTTP owner routes: ordinary account session, exact DTO and private boundary."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from yleum_api.core.db import get_session
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError, api_error_handler


@pytest.fixture
def app(monkeypatch):
    from yleum_api.routers import max_management as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.add_exception_handler(ApiError, api_error_handler)
    owner = NS(id=uuid4(), is_anon=False)
    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_session] = lambda: NS()
    return app, routes, owner


async def test_missing_session_does_not_reach_broker(app, monkeypatch):
    application, routes, _ = app
    application.dependency_overrides.pop(get_current_user)
    calls = []
    monkeypatch.setattr(
        routes.service, "get_overview", AsyncMock(side_effect=lambda *a: calls.append(a))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        response = await client.get(f"/api/projects/{uuid4()}/max/management")
    assert response.status_code == 401 and calls == []


async def test_anonymous_principal_is_not_owner_management_session(app, monkeypatch):
    application, routes, owner = app
    owner.is_anon = True
    calls = []
    monkeypatch.setattr(
        routes.service, "get_overview", AsyncMock(side_effect=lambda *a: calls.append(a))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        response = await client.get(f"/api/projects/{uuid4()}/max/management")
    assert response.status_code == 401 and calls == []


async def test_query_is_strictly_typed_and_unknown_keys_rejected(app, monkeypatch):
    application, routes, _ = app
    received = []

    async def listing(*args):
        received.append(args[-1])
        return {"records": [], "next_cursor": None}

    monkeypatch.setattr(routes.service, "list_records", listing)
    url = f"/api/projects/{uuid4()}/max/management/records"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        response = await client.get(
            url, params={"resource": "leads", "limit": "10", "query": "Анна"}
        )
        assert response.status_code == 200
        assert received[0].limit == 10 and received[0].query == "Анна"
        for params in [
            {"resource": "custom"},
            {"resource": "leads", "limit": "101"},
            {"resource": "leads", "limit": "true"},
            {"resource": "leads", "sql": "SELECT"},
            {"resource": "leads", "created_from": "123"},
        ]:
            response = await client.get(url, params=params)
            assert response.status_code == 422
    assert len(received) == 1


async def test_patch_carries_resource_and_preserves_operation_key_and_omitted_note(
    app, monkeypatch
):
    application, routes, _ = app
    captured = []
    record_id, key = uuid4(), uuid4()

    async def update(*args):
        captured.append(args)
        raise ApiError("revision_conflict", "Обновите данные", 409)

    monkeypatch.setattr(routes.service, "update_record", update)
    url = f"/api/projects/{uuid4()}/max/management/records/{record_id}?resource=orders"
    body = {"operation_key": str(key), "expected_revision": "a" * 64, "status": "in_progress"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        response = await client.patch(url, json=body)
        assert response.status_code == 409
        response = await client.patch(url, json={**body, "payload": {"private": True}})
        assert response.status_code == 422
        response = await client.patch(url + "&destination=https://evil.test", json=body)
        assert response.status_code == 422
    assert captured[0][-1].model_dump(mode="json", exclude_unset=True) == body
    assert str(captured[0][-3]) == str(record_id) and captured[0][-2] == "orders"
    assert len(captured) == 1


async def test_real_application_registers_owner_management_route(monkeypatch):
    from yleum_api.core.config import get_settings

    # App construction validates settings; ASGITransport never starts its DB
    # lifespan. These isolated values cannot reach a real database or service.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://qa:qa@127.0.0.1:1/qa")
    monkeypatch.setenv("JWT_SECRET", "owner-management-registration-fixture")
    get_settings.cache_clear()
    from yleum_api.main import create_app
    from yleum_api.services import max_management

    application = create_app()
    get_settings.cache_clear()
    owner = NS(id=uuid4(), is_anon=False)
    application.dependency_overrides[get_current_user] = lambda: owner
    application.dependency_overrides[get_session] = lambda: NS()
    monkeypatch.setattr(
        max_management,
        "get_overview",
        AsyncMock(
            return_value={
                "state": "not_published",
                "configured_modules": ["catalog"],
                "active_modules": [],
                "resources": [],
                "publication_id": None,
                "contract_hash": None,
                "catalog_limit": 100,
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        response = await client.get(f"/api/projects/{uuid4()}/max/management")
    assert response.status_code == 200 and response.json()["state"] == "not_published"
