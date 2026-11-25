from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from yleum_api.core.deps import get_admin_user, get_current_user
from yleum_api.main import app
from yleum_api.models.project import Project
from yleum_api.models.studio_activity import StudioActivity
from yleum_api.models.user import User
from yleum_api.services.monitoring.catalog import METRICS
from yleum_api.services.monitoring.database import DATABASE_METRICS
from yleum_api.services.monitoring.prometheus import PROMETHEUS_METRICS, metric_query

BASE = "/api/admin/monitoring"


def test_numbering_has_all_original_metrics_and_query_templates():
    assert set(METRICS) == set(range(1, 101)) - {3, 4, 18, 22, 23, 24, 42}
    assert not set(DATABASE_METRICS) & set(PROMETHEUS_METRICS)
    vps = uuid4()
    for metric in PROMETHEUS_METRICS.values():
        compiled = metric_query(metric, period_hours=24, client=None, vps=vps)
        assert all(token not in compiled for token in ("<scope>", "<labels>", "<period>"))


async def test_admin_queries_execute_on_postgres_and_degrade_explicitly(client, db_session):
    assert (await client.get(BASE + "/catalog")).status_code == 401
    assert (await client.get(BASE + "/metrics/1")).status_code == 401
    assert (await client.get("/internal/monitoring/metrics")).status_code == 404
    operator = User(email="monitoring-admin@example.test", password_hash="test")
    db_session.add(operator)
    await db_session.commit()
    app.dependency_overrides[get_admin_user] = lambda: operator

    response = await client.get(BASE + "/catalog")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert {metric["id"] for metric in response.json()} == set(METRICS)

    for number in sorted(DATABASE_METRICS):
        result = await client.get(BASE + f"/metrics/{number}")
        assert result.status_code == 200
        assert result.json()["state"] in {"measured", "no_data"}, (number, result.text)
    result = await client.get(BASE + "/metrics/1")
    assert result.json()["points"][0]["value"] == 0
    assert (await client.get(BASE + "/metrics/3")).status_code == 404
    assert (await client.get(BASE + "/metrics/1?period=168h")).status_code == 422


async def test_studio_receipts_deduplicate_and_keep_customer_scope(client, db_session):
    owner = User(email="monitoring-actor@example.test", password_hash="test")
    other = User(email="monitoring-other@example.test", password_hash="test")
    db_session.add_all([owner, other])
    await db_session.flush()
    project = Project(
        owner_id=owner.id, name="Studio", slug="studio-monitor", template="max_miniapp"
    )
    foreign = Project(
        owner_id=other.id, name="Foreign", slug="foreign-monitor", template="max_miniapp"
    )
    db_session.add_all([project, foreign])
    await db_session.commit()

    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_admin_user] = lambda: owner
    for kind in ("open", "active", "error", "active"):
        receipt = {"project_id": str(project.id), "kind": kind, "release": "abc1234"}
        assert (await client.post("/api/studio/activity", json=receipt)).status_code == 204
    response = await client.post(
        "/api/studio/activity", json={**receipt, "project_id": str(foreign.id)}
    )
    assert response.status_code == 404

    rows = list((await db_session.scalars(select(StudioActivity))).all())
    assert len(rows) == 1
    assert rows[0].active and rows[0].error

    db_session.add(
        StudioActivity(
            actor_id=owner.id,
            workspace_id=UUID(int=0),
            minute=datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(days=1, hours=1),
            release="abc1234",
            active=True,
            error=False,
        )
    )
    await db_session.commit()
    durations = (await client.get(BASE + f"/metrics/28?project={project.id}")).json()
    assert durations["points"][0]["value"] == 1
    weekly = (await client.get(BASE + f"/metrics/27?client={owner.id}")).json()
    assert weekly["points"][0]["value"] == 2
    foreign_scope = (await client.get(BASE + f"/metrics/94?project={foreign.id}")).json()
    assert foreign_scope["state"] == "no_data"
