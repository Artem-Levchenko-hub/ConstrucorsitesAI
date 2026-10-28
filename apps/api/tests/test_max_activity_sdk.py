"""Exercise real SDK, authenticated route and signing without stock tables or live traffic."""

import json
import subprocess
from pathlib import Path

import pytest


def test_detailed_activity_sdk_and_authenticated_schema_independent_delivery():
    harness = Path(__file__).with_name("fixtures") / "max_activity_sdk.test.mjs"
    result = subprocess.run(
        ["node", "--test", str(harness)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "route,name,kind,event_type",
    [
        ("omnia/events", "order_created", "event", "order_created"),
        ("omnia/events", "cart_add", "event", "cart_add"),
        ("omnia/events", "checkout", "event", None),
        ("omnia/actions", "order_created", "action", "order_created"),
        ("omnia/actions", "booking_created", "action", "booking_created"),
        ("omnia/actions", "booking", "action", None),
        ("omnia/actions", "screen_view", "action", None),
    ],
)
def test_local_routes_enrich_only_exact_codes_without_duplicating_receipts(
    tmp_path, route, name, kind, event_type
):
    from tests.test_max_analytics_routes import run

    receipt = "22222222-2222-4222-8222-222222222222"
    body = (
        {"eventName": name, "properties": {"private": "app-local"}, "eventId": receipt}
        if route == "omnia/events"
        else {"actionType": name, "payload": {"private": "app-local"}, "operationKey": receipt}
    )
    result = run(tmp_path, route, body)
    assert result["status"] in {201, 204}
    assert len(result["sent"]) == 1
    forwarded = json.loads(result["sent"][0]["body"])
    assert forwarded["kind"] == kind
    assert forwarded.get("event_type") == event_type
    assert "app-local" not in result["sent"][0]["body"]
    persisted = "persisted:maxAnalyticsEvents" if kind == "event" else "persisted:maxAuditLog"
    assert result["trace"].index(persisted) < result["trace"].index("forward")
