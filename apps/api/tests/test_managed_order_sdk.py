"""Actual managed proxy signatures reach actor-scoped API, with no provider calls."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_managed_order_receipts import seed

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "apps/orchestrator/templates/max-miniapp-nextjs/src"
HARNESS = Path(__file__).with_name("fixtures") / "managed_order_contract_harness.cjs"


def run(mode, **data):
    path = (
        "lib/omnia/integration-client.ts"
        if mode == "sdk"
        else "app/api/omnia/integrations/[...path]/route.ts"
    )
    result = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"mode": mode, "source": str(TEMPLATE / path), **data}),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def test_actual_sdk_reads_never_invoke_create():
    calls = run("sdk")["sent"]
    assert [row["url"] for row in calls] == [
        f"/api/omnia/integrations/{name}"
        for name in ["order-list", "order-status", "order-details"]
    ]
    assert json.loads(calls[1]["body"])["payload"] == {"idempotency_key": "qa-stable-order-intent"}
    assert json.loads(calls[2]["body"])["payload"] == {
        "order_id": "00000000-0000-0000-0000-000000000009"
    }
    assert all(row["credentials"] == "same-origin" for row in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["order-list", "order-status", "order-details"])
async def test_real_signed_proxy_to_api_and_foreign_actor(
    client, db_session, monkeypatch, operation
):
    project, row = await seed(client, db_session, monkeypatch)
    payload = (
        {"idempotency_key": row.client_key}
        if operation == "order-status"
        else {"order_id": row.result["id"]}
    )
    for actor in ["42", "43"]:
        forwarded = run("proxy", operation=operation, payload=payload, project=project, actor=actor)
        assert forwarded["status"] == 200 and len(forwarded["sent"]) == 1
        call = forwarded["sent"][0]
        assert call["method"] == ("GET" if operation == "order-list" else "POST")
        response = await client.request(
            call["method"],
            call["url"].removeprefix("https://yleum.ru"),
            headers=call["headers"],
            content=call.get("body"),
        )
        if actor == "42":
            assert response.status_code == 200 and row.result["id"] in response.text
        elif operation == "order-list":
            assert response.status_code == 200 and response.json()["items"] == []
        else:
            assert response.status_code == 404
        tampered = await client.request(
            call["method"],
            call["url"].removeprefix("https://yleum.ru"),
            headers=call["headers"],
            content=(call.get("body") or "") + " ",
        )
        assert tampered.status_code == 401


@pytest.mark.parametrize("actor", [None, "preview-actor"])
def test_managed_reads_reject_missing_or_preview_max_identity(actor):
    result = run("proxy", operation="order-list", project="synthetic-project", actor=actor)
    assert result["status"] == 401 and result["sent"] == []
