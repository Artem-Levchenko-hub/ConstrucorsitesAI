"""Typed ingestion and a private, stable owner activity feed."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tests.test_integration_runtime_auth import assertion
from tests.test_integration_runtime_contracts import connect
from tests.test_max_analytics import send
from yleum_api.models.max_analytics import MaxAnalyticsEvent
from yleum_api.models.project import Project
from yleum_api.routers.max_analytics import AnalyticsEventRequest


@pytest.mark.parametrize(
    "valid_kinds,event_type",
    [
        (("open",), "app_open"),
        (("event",), "screen_view"),
        (("event",), "item_view"),
        (("event",), "button_click"),
        (("event",), "cart_add"),
        (("event",), "checkout_start"),
        (("action", "event"), "form_submit"),
        (("action", "event"), "order_created"),
        (("action", "event"), "booking_created"),
        (("action", "event"), "lead_created"),
    ],
)
def test_typed_ingestion_contract(valid_kinds, event_type):
    for kind in valid_kinds:
        payload = AnalyticsEventRequest(event_id=uuid4(), kind=kind, event_type=event_type)
        assert payload.event_type == event_type
        assert AnalyticsEventRequest(event_id=uuid4(), kind=kind).event_type is None
    for wrong_kind in {"open", "action", "event"} - set(valid_kinds):
        with pytest.raises(ValidationError):
            AnalyticsEventRequest(event_id=uuid4(), kind=wrong_kind, event_type=event_type)


@pytest.mark.parametrize(
    "extra", [{"event_type": "purchase"}, {"properties": {}}, {"event_type": ["app_open"]}]
)
def test_typed_ingestion_rejects_unknown_types_and_extra_data(extra):
    with pytest.raises(ValidationError):
        AnalyticsEventRequest(event_id=uuid4(), kind="open", **extra)


async def typed_send(client, project, event_id, kind, event_type, *, tamper=False):
    path = f"/api/runtime/projects/{project}/analytics"
    body = json.dumps({"event_id": str(event_id), "kind": kind, "event_type": event_type}).encode()
    signature = assertion(project, path, method="POST", body=body, max_user_id="42")
    if tamper:
        body = body.replace(b"button_click", b"screen_view")
    return await client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Omnia-Integration-Assertion": signature,
        },
    )


async def test_typed_replays_are_idempotent_and_body_is_authenticated(
    client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    event_id = uuid4()
    assert (await typed_send(client, project, event_id, "event", "button_click")).status_code == 204
    assert (await typed_send(client, project, event_id, "event", "button_click")).status_code == 204
    assert (await send(client, project, event_id, "event")).status_code == 204
    assert (await typed_send(client, project, event_id, "event", "screen_view")).status_code == 409
    assert (await typed_send(client, project, event_id, "action", "form_submit")).status_code == 409
    assert (
        await typed_send(client, project, uuid4(), "event", "button_click", tamper=True)
    ).status_code == 401
    legacy = uuid4()
    assert (await send(client, project, legacy, "event")).status_code == 204
    assert (await typed_send(client, project, legacy, "event", "screen_view")).status_code == 204
    rows = list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    assert len(rows) == 2
    assert {row.event_id: row.event_type for row in rows} == {
        event_id: "button_click",
        legacy: None,
    }


async def test_client_business_events_keep_unconfirmed_kind_and_replay_identity(
    client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    client_event, confirmed_event = uuid4(), uuid4()
    for _ in range(2):
        assert (
            await typed_send(client, project, client_event, "event", "order_created")
        ).status_code == 204
    assert (
        await typed_send(client, project, client_event, "action", "order_created")
    ).status_code == 409
    assert (
        await typed_send(client, project, client_event, "event", "lead_created")
    ).status_code == 409
    assert (
        await typed_send(client, project, confirmed_event, "action", "order_created")
    ).status_code == 204
    aggregate = (await client.get(f"/api/projects/{project}/max/analytics")).json()
    assert aggregate["events"] == 2 and aggregate["actions"] == 1
    feed = (
        await client.get(f"/api/projects/{project}/max/analytics/events?event_type=order_created")
    ).json()
    assert {item["kind"] for item in feed["items"]} == {"event", "action"}


async def test_feed_keyset_filters_legacy_and_project_scope(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    owner_project = await db_session.get(Project, UUID(project))
    second = Project(
        owner_id=owner_project.owner_id, name="Other", slug="feed-other", template="max_miniapp"
    )
    db_session.add(second)
    await db_session.flush()
    now = datetime.now(UTC)
    rows = []
    for number, (kind, event_type) in enumerate(
        [
            ("open", None),
            ("action", None),
            ("event", "screen_view"),
            ("event", "button_click"),
            ("action", "order_created"),
        ],
        1,
    ):
        row = MaxAnalyticsEvent(
            id=UUID(int=number),
            project_id=UUID(project),
            actor_key="a" * 64,
            event_id=uuid4(),
            kind=kind,
            event_type=event_type,
            occurred_at=now,
        )
        db_session.add(row)
        rows.append(row)
    db_session.add(
        MaxAnalyticsEvent(
            project_id=second.id, actor_key="b" * 64, event_id=uuid4(), kind="open", occurred_at=now
        )
    )
    db_session.add(
        MaxAnalyticsEvent(
            project_id=UUID(project),
            actor_key="a" * 64,
            event_id=uuid4(),
            kind="open",
            occurred_at=now - timedelta(days=60),
        )
    )
    await db_session.commit()
    url = f"/api/projects/{project}/max/analytics/events"
    first = await client.get(url, params={"limit": 2})
    assert first.status_code == 200 and first.headers["cache-control"] == "private, no-store"
    data = first.json()
    assert set(data) == {
        "days",
        "from_date",
        "to_date",
        "timezone",
        "items",
        "next_cursor",
        "observed_at",
    }
    assert data["days"] == 30 and data["timezone"] == "Europe/Moscow"
    items = data["items"]
    while data["next_cursor"]:
        data = (await client.get(url, params={"limit": 2, "cursor": data["next_cursor"]})).json()
        items += data["items"]
    assert [row["id"] for row in items] == [str(row.id) for row in reversed(rows)]
    assert all(set(row) == {"id", "kind", "event_type", "occurred_at"} for row in items)
    assert items[-1]["event_type"] == "app_open" and items[-2]["event_type"] is None
    assert len((await client.get(url, params={"kind": "action"})).json()["items"]) == 2
    assert (await client.get(url, params={"event_type": "app_open"})).json()["items"] == [items[-1]]
    assert (await client.get(url, params={"event_type": "screen_view"})).json()["items"] == [
        items[2]
    ]
    aggregate = (await client.get(f"/api/projects/{project}/max/analytics")).json()
    assert aggregate["events"] == 5
    assert (data["from_date"], data["to_date"]) == (aggregate["from_date"], aggregate["to_date"])


async def test_feed_bounds_cursor_and_owner_guard(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    url = f"/api/projects/{project}/max/analytics/events"
    for params in [
        {"limit": 0},
        {"limit": 101},
        {"days": 0},
        {"days": 91},
        {"kind": "unknown"},
        {"event_type": "unknown"},
        {"cursor": "garbage"},
        {"cursor": "x" * 513},
    ]:
        assert (await client.get(url, params=params)).status_code == 422
    assert (await client.get(url)).json()["items"] == []
    assert (await client.get(f"/api/projects/{uuid4()}/max/analytics/events")).status_code == 404
    stored = await db_session.get(Project, UUID(project))
    stored.template = "landing"
    await db_session.commit()
    assert (await client.get(url)).status_code == 409
    client.cookies.clear()
    assert (await client.get(url)).status_code == 401


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        ["2026-10-29", str(uuid4())],
        ["2026-10-29T12:00:00", str(uuid4())],
        ["2026-10-29T12:00:00+00:00", "invalid"],
        ["0001-01-01T00:00:00+23:00", str(uuid4())],
    ],
)
def test_cursor_rejects_malformed_or_naive_values(value):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.max_analytics import decode_cursor

    cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    with pytest.raises(ApiError) as error:
        decode_cursor(cursor)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "scope,expected", [("owner", 200), ("foreign", 404), ("missing", 404), ("web", 409)]
)
async def test_feed_http_owner_guard_without_database(monkeypatch, scope, expected):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from yleum_api.core.db import get_session
    from yleum_api.core.deps import get_current_user
    from yleum_api.core.errors import ApiError, api_error_handler
    from yleum_api.routers import max_analytics

    owner = uuid4()
    project_id = uuid4()
    project = (
        None
        if scope == "missing"
        else SimpleNamespace(
            owner_id=uuid4() if scope == "foreign" else owner,
            template="landing" if scope == "web" else "max_miniapp",
        )
    )
    session = AsyncMock()
    session.get.return_value = project
    listing = AsyncMock(return_value={"items": [], "next_cursor": None})
    monkeypatch.setattr(max_analytics, "list_events", listing)
    app = FastAPI()
    app.include_router(max_analytics.router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get(f"/api/projects/{project_id}/max/analytics/events")
        assert result.status_code == expected
        if expected != 200:
            listing.assert_not_called()
            return
        assert result.headers["cache-control"] == "private, no-store"
        assert listing.await_args.kwargs == {
            "kind": None,
            "event_type": None,
            "limit": 30,
            "cursor": None,
        }
        for params in [
            {"limit": 0},
            {"limit": 101},
            {"days": 91},
            {"kind": "invalid"},
            {"event_type": "unknown"},
            {"cursor": "x" * 513},
        ]:
            assert (
                await client.get(f"/api/projects/{project_id}/max/analytics/events", params=params)
            ).status_code == 422
        # Restore the real auth dependency: an absent session must be rejected before listing.
        del app.dependency_overrides[get_current_user]
        assert (
            await client.get(f"/api/projects/{project_id}/max/analytics/events")
        ).status_code == 401
