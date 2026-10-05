"""Real SDK receipts and signed route→platform PG aggregate; no live MAX/provider."""

import json
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.test_integration_runtime_contracts import connect
from tests.test_max_analytics_routes import run
from yleum_api.models.project import Project
from yleum_api.models.user import User

_REPO = Path(__file__).resolve().parents[3]
_HARNESS = Path(__file__).with_name("fixtures") / "max_analytics_sdk_harness.cjs"
_CLIENT = _REPO / "apps/orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/client.ts"


def sdk(**kwargs):
    result = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"client": str(_CLIENT), **kwargs}),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    assert "SYNTHETIC_PRIVATE" not in result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("outcome", ["network", 503, 429])
def test_sdk_delivery_retry_reuses_one_generated_receipt(outcome):
    result = sdk(outcomes=[outcome, 204])
    assert result["error"] is None and len(result["calls"]) == 2
    assert result["calls"][0]["body"] == result["calls"][1]["body"]
    body = json.loads(result["calls"][0]["body"])
    assert str(UUID(body["eventId"])) == body["eventId"]
    assert set(body) == {"eventName", "properties", "eventId"} and body["properties"] == {}
    assert all(
        row["url"] == "/api/omnia/events" and row["credentials"] == "include"
        for row in result["calls"]
    )
    assert result["timeouts"] == [5000, 5000] and len(result["cleared"]) == 2


def test_sdk_retry_preserves_body_snapshot_when_caller_properties_change():
    result = sdk(outcomes=["network", 204], properties={"flow": "initial"}, mutateProperties=True)
    assert result["error"] is None and len(result["calls"]) == 2
    assert result["calls"][0]["body"] == result["calls"][1]["body"]
    assert json.loads(result["calls"][1]["body"])["properties"] == {"flow": "initial"}


def test_sdk_explicit_receipt_supports_same_logical_event_replay():
    key = "ABCDEFAB-1234-4123-8123-123456789ABC"
    first = sdk(options={"eventId": key}, outcomes=["network", "network"])
    assert first["error"] == "Analytics event failed"
    second = sdk(options={"eventId": key})
    assert second["error"] is None
    assert first["calls"][0]["body"] == first["calls"][1]["body"] == second["calls"][0]["body"]
    assert json.loads(second["calls"][0]["body"])["eventId"] == key.lower()


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_sdk_definitive_rejection_does_not_retry(status):
    result = sdk(outcomes=[status, 204])
    assert len(result["calls"]) == 1 and len(result["cleared"]) == 1
    assert result["error"] == (None if status == 401 else "Analytics event failed")


def test_sdk_timeout_is_bounded_and_reuses_receipt():
    result = sdk(mode="timeout")
    assert result["error"] == "Analytics event failed" and len(result["calls"]) == 2
    assert result["calls"][0]["body"] == result["calls"][1]["body"]
    assert result["timeouts"] == [5000, 5000] and len(result["cleared"]) == 2


@pytest.mark.parametrize("event_id", ["not-a-uuid", 42, "", None])
def test_sdk_invalid_explicit_receipt_rejected_without_dispatch(event_id):
    result = sdk(options={"eventId": event_id})
    assert result["error"] == "Analytics event failed" and result["calls"] == []


async def test_sdk_retry_signed_core_receipt_reaches_only_project_owner_aggregate(
    tmp_path, client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    emitted = sdk(outcomes=["network", 204])
    assert emitted["error"] is None and len(emitted["calls"]) == 2
    forwarded = []
    for call in emitted["calls"]:
        result = run(tmp_path, "omnia/events", json.loads(call["body"]), project=UUID(project))
        assert result["status"] == 204 and result["trace"].index(
            "persisted:maxAnalyticsEvents"
        ) < result["trace"].index("forward")
        forwarded.extend(result["sent"])
    assert forwarded[0]["body"] == forwarded[1]["body"]
    assert set(json.loads(forwarded[0]["body"])) == {"event_id", "kind"}
    for event in forwarded:
        response = await client.post(
            f"/api/runtime/projects/{project}/analytics",
            content=event["body"],
            headers=event["headers"],
        )
        assert response.status_code == 204
    own = await client.get(f"/api/projects/{project}/max/analytics?days=7")
    assert own.status_code == 200 and own.headers["Cache-Control"] == "private, no-store"
    assert {key: own.json()[key] for key in ["users", "opens", "actions", "events"]} == {
        "users": 1,
        "opens": 0,
        "actions": 0,
        "events": 1,
    }
    assert sum(day["events"] for day in own.json()["daily"]) == 1
    assert all(
        value not in own.text
        for value in ["max_user_id", "actor_key", "test-max-token", "checkout"]
    )
    foreign = User(id=uuid4(), email="analytics-foreign@example.test", password_hash="test")
    db_session.add(foreign)
    await db_session.flush()
    other = Project(
        owner_id=foreign.id, name="foreign", slug="analytics-foreign", template="max_miniapp"
    )
    db_session.add(other)
    await db_session.commit()
    assert (await client.get(f"/api/projects/{other.id}/max/analytics")).status_code == 404
    client.cookies.clear()
    assert (await client.get(f"/api/projects/{project}/max/analytics")).status_code == 401
