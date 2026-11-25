from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy import select

from yleum_api.core.deps import get_admin_user, get_current_user
from yleum_api.main import app
from yleum_api.models.project import Project
from yleum_api.models.studio_activity import StudioActivity
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client
from yleum_api.services.monitoring.catalog import METRICS
from yleum_api.services.monitoring.database import DATABASE_METRICS
from yleum_api.services.monitoring.prometheus import (
    PLATFORM_VPS,
    PROMETHEUS_METRICS,
    metric_query,
)

BASE = "/api/admin/monitoring"


def test_numbering_has_all_original_metrics_and_query_templates():
    assert set(METRICS) == set(range(1, 101)) - {3, 4, 18, 22, 23, 24, 42}
    assert not set(DATABASE_METRICS) & set(PROMETHEUS_METRICS)
    vps = uuid4()
    for metric in PROMETHEUS_METRICS.values():
        compiled = metric_query(metric, period_hours=24, client=None, vps=vps)
        placeholders = ("<scope>", "<labels>", "<probe_labels>", "<period>")
        assert all(token not in compiled for token in placeholders)


def test_platform_host_filter_uses_existing_exporter_and_probe_labels():
    host = next(id_ for id_, name in PLATFORM_VPS.items() if name == "core")
    cpu = metric_query(PROMETHEUS_METRICS[9], period_hours=24, client=None, vps=host)
    assert 'vps="core"' in cpu
    assert "deploy_target_id" not in cpu
    probe = metric_query(PROMETHEUS_METRICS[2], period_hours=24, client=None, vps=host)
    assert 'target_vps="core"' in probe
    client_vps = uuid4()
    vps_probe = metric_query(
        PROMETHEUS_METRICS[2], period_hours=24, client=None, vps=client_vps
    )
    assert f'deploy_target_id="{client_vps}"' in vps_probe
    assert 'target_vps="core"' not in vps_probe


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
    coverage = (await client.get(BASE + "/metrics/100")).json()
    assert coverage["state"] == "not_applicable"
    assert coverage["points"] == []
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


async def test_publication_metrics_use_terminal_controller_facts(client, db_session, monkeypatch):
    operator = User(email="monitoring-publish@example.test", password_hash="test")
    db_session.add(operator)
    await db_session.flush()
    project = Project(
        owner_id=operator.id, name="Monitor", slug="status-monitor", template="max_miniapp",
    )
    db_session.add(project)
    await db_session.commit()
    app.dependency_overrides[get_admin_user] = lambda: operator

    end = datetime.now(UTC) - timedelta(minutes=5)
    start = end - timedelta(seconds=22)
    source = {
        "facts": [
            {
                "project_id": str(project.id), "run_id": "ready", "phase": "done",
                "started_at": start.isoformat(), "finished_at": end.isoformat(),
            },
            {
                "project_id": str(project.id), "run_id": "failed", "phase": "failed",
                "started_at": None, "finished_at": end.isoformat(),
            },
        ],
        "first_success": {str(project.id): end.isoformat()},
    }
    load = AsyncMock(return_value=source)
    monkeypatch.setattr(orchestrator_client, "get_monitoring_publication_history", load)
    for metric, value in ((53, 50), (54, 22), (56, 0)):
        result = (await client.get(BASE + f"/metrics/{metric}?project={project.id}")).json()
        assert result["state"] == "measured", result
        assert result["points"][0]["value"] == value
    assert load.call_args.args[0] == [project.id]
