"""Real signed events, durable retries, privacy and owner isolation."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from tests.test_integration_runtime_auth import assertion
from tests.test_integration_runtime_contracts import connect
from yleum_api.models.project import Project


async def send(client, project, event_id, kind="open", actor="42", signed_project=None):
    path = f"/api/runtime/projects/{project}/analytics"
    body = json.dumps({"event_id": str(event_id), "kind": kind}).encode()
    return await client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Omnia-Integration-Assertion": assertion(
                signed_project or project, path, method="POST", body=body, max_user_id=actor
            ),
        },
    )


async def test_signed_events_aggregate_without_actor_or_payload(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    event_id = uuid4()
    for _ in range(2):
        assert (await send(client, project, event_id)).status_code == 204
    assert (await send(client, project, uuid4(), "action")).status_code == 204
    assert (await send(client, project, uuid4(), "open", "99")).status_code == 204
    assert (await send(client, project, uuid4(), "event", "99")).status_code == 204
    result = await client.get(f"/api/projects/{project}/max/analytics?days=7")
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "private, no-store"
    data = result.json()
    assert {key: data[key] for key in ("opens", "users", "actions", "events")} == {
        "opens": 2,
        "users": 2,
        "actions": 1,
        "events": 4,
    }
    assert len(data["daily"]) == 7
    assert sum(row["events"] for row in data["daily"]) == 4
    assert "max_user_id" not in result.text and "actor" not in result.text
    assert "test-max-token" not in result.text
    from yleum_api.models.max_analytics import MaxAnalyticsEvent

    rows = list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    assert len(rows) == 4
    assert all(len(row.actor_key) == 64 and row.actor_key not in {"42", "99"} for row in rows)
    rows[0].occurred_at = datetime.now(UTC) - timedelta(days=10)
    await db_session.commit()
    assert (await client.get(f"/api/projects/{project}/max/analytics?days=7")).json()["events"] == 3


async def test_authentication_owner_project_and_actor_isolation(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    route = f"/api/runtime/projects/{project}/analytics"
    assert (
        await client.post(route, json={"event_id": str(uuid4()), "kind": "open"})
    ).status_code == 401
    assert (await send(client, project, uuid4(), signed_project=str(uuid4()))).status_code == 401
    assert (await send(client, project, uuid4(), actor="preview")).status_code == 401
    foreign = Project(owner_id=uuid4(), name="foreign", slug="foreign", template="max_miniapp")
    # The foreign project belongs to a real second owner, never an arbitrary query owner.
    from yleum_api.models.user import User

    other = User(id=foreign.owner_id, email="foreign@example.test", password_hash="test")
    db_session.add(other)
    await db_session.flush()
    db_session.add(foreign)
    await db_session.commit()
    assert (await client.get(f"/api/projects/{foreign.id}/max/analytics")).status_code == 404
    assert (await client.get(f"/api/projects/{project}/max/analytics?days=0")).status_code == 422
    client.cookies.clear()
    assert (await client.get(f"/api/projects/{project}/max/analytics")).status_code == 401


async def test_same_event_id_is_scoped_by_actor_and_project(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    event_id = uuid4()
    assert (await send(client, project, event_id)).status_code == 204
    assert (await send(client, project, event_id, actor="99")).status_code == 204
    result = (await client.get(f"/api/projects/{project}/max/analytics")).json()
    assert result["opens"] == 2 and result["users"] == 2
    assert (await send(client, project, event_id, "action")).status_code == 409
    from yleum_api.core.crypto import encrypt_strong
    from yleum_api.models.max_integration import MaxIntegration

    owner = await db_session.get(Project, UUID(project))
    second = Project(owner_id=owner.owner_id, name="Second", slug="second", template="max_miniapp")
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        MaxIntegration(
            project_id=second.id,
            owner_id=second.owner_id,
            bot_token_enc=encrypt_strong("test-max-token"),
            webhook_secret_enc=encrypt_strong("test"),
        )
    )
    await db_session.commit()
    assert (await send(client, str(second.id), event_id)).status_code == 204
    assert (await client.get(f"/api/projects/{second.id}/max/analytics")).json()["events"] == 1
    assert (await client.get(f"/api/projects/{project}/max/analytics")).json()["events"] == 2


async def test_moscow_midnight_buckets_and_period_cutoff(client, db_session, monkeypatch):
    from zoneinfo import ZoneInfo

    from yleum_api.models.max_analytics import MaxAnalyticsEvent

    project = await connect(client, db_session, monkeypatch)
    before, after = uuid4(), uuid4()
    assert (await send(client, project, before)).status_code == 204
    assert (await send(client, project, after)).status_code == 204
    midnight = (
        datetime.now(ZoneInfo("Europe/Moscow"))
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        .astimezone(UTC)
    )
    for row in (await db_session.scalars(select(MaxAnalyticsEvent))).all():
        row.occurred_at = midnight - timedelta(seconds=1) if row.event_id == before else midnight
    await db_session.commit()
    data = (await client.get(f"/api/projects/{project}/max/analytics?days=2")).json()
    assert data["timezone"] == "Europe/Moscow"
    assert [row["opens"] for row in data["daily"]] == [1, 1]
    one_day = (await client.get(f"/api/projects/{project}/max/analytics?days=1")).json()
    assert one_day["opens"] == 1
