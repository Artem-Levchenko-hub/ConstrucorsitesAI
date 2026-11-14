"""Actual managed proxy signatures reach actor-scoped API, with no provider calls."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from tests.test_managed_order_receipts import seed

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "apps/orchestrator/templates/max-miniapp-nextjs/src"
HARNESS = Path(__file__).with_name("fixtures") / "managed_order_contract_harness.cjs"


def run(mode, **data):
    path = (
        "lib/omnia/integration-client.ts"
        if mode in {"sdk", "checkout-sdk"}
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


ORDER_ID = "11111111-1111-4111-8111-111111111111"
CHECKOUT_RECEIPT = {
    "order_id": ORDER_ID, "status": "pending", "confirmation_url": "https://checkout.example/pay",
    "test": False, "paid_at": None,
}


def test_checkout_sdk_reuses_canonical_id_and_recovers_status_from_redirect():
    create = run("checkout-sdk", orderId=ORDER_ID, returnUrl="https://app.test/",
                 search="", result=CHECKOUT_RECEIPT)
    read = run("checkout-sdk", action="status", search=f"?checkout_order_id={ORDER_ID}",
               result=CHECKOUT_RECEIPT)
    assert create["result"] == read["result"] == {**CHECKOUT_RECEIPT, "failure_reason": None}
    assert read["restored"] == ORDER_ID
    assert [row["url"] for row in create["sent"] + read["sent"]] == [
        "/api/omnia/integrations/order-checkout", "/api/omnia/integrations/order-checkout-status",
    ]
    assert json.loads(create["sent"][0]["body"]) == {
        "expectedActor": "42", "payload": {"order_id": ORDER_ID, "return_url": "https://app.test/"},
    }
    assert json.loads(read["sent"][0]["body"]) == {
        "expectedActor": "42", "payload": {"order_id": ORDER_ID},
    }


@pytest.mark.parametrize("patch", [
    {"order_id": "22222222-2222-4222-8222-222222222222"},
    {"confirmation_url": "javascript:alert(1)"},
    {"status": "succeeded", "confirmation_url": None, "test": None},
    {"failure_reason": "receipt_required"},
    {"status": "needs_review", "confirmation_url": None, "failure_reason": "private detail"},
])
def test_checkout_sdk_fails_closed_on_wrong_or_unverified_receipt(patch):
    result = run("checkout-sdk", orderId=ORDER_ID, search="", result={**CHECKOUT_RECEIPT, **patch})
    assert result["error"] == "unavailable" and len(result["sent"]) == 1


def test_checkout_sdk_exposes_receipt_required_only_as_a_safe_reason():
    receipt = {
        **CHECKOUT_RECEIPT, "status": "needs_review", "confirmation_url": None,
        "failure_reason": "receipt_required",
    }
    result = run("checkout-sdk", orderId=ORDER_ID, search="",
                 result={**receipt, "provider_error": "private provider message"})
    assert result["result"] == receipt


def test_checkout_sdk_drops_late_response_after_switching_actor():
    result = run("checkout-sdk", orderId=ORDER_ID, search="", result=CHECKOUT_RECEIPT,
                 switchActor=True)
    assert result["error"] == "actor_changed" and "result" not in result


def test_checkout_sdk_does_not_request_a_status_for_an_ambiguous_return_id():
    result = run("checkout-sdk", action="status",
                 search=f"?checkout_order_id={ORDER_ID}&checkout_order_id={ORDER_ID}")
    assert result == {"error": "invalid_request", "restored": None, "sent": []}


@pytest.mark.parametrize("operation,tail", [
    ("order-checkout", "checkout"), ("order-checkout-status", "checkout/status"),
    ("order-checkout-receipt", "checkout/receipt"),
])
def test_checkout_proxy_signs_only_the_managed_order_path_and_payload(operation, tail):
    response = run("proxy", operation=operation, payload={"order_id": ORDER_ID},
                   project=ORDER_ID, actor="42", expectedActor="42")
    assert response["status"] == 200 and len(response["sent"]) == 1
    sent = response["sent"][0]
    assert sent["url"] == f"https://yleum.ru/api/runtime/projects/{ORDER_ID}/orders/{tail}"
    assert sent["method"] == "POST" and json.loads(sent["body"]) == {"order_id": ORDER_ID}
    assert "X-Omnia-Integration-Assertion" in sent["headers"]


@pytest.mark.parametrize("operation,tail,payload", [
    ("payments", "payments", {"amount": 12, "description": "Generic payment"}),
    ("payment-status", "payments/status", {"payment_id": "generic-payment-1"}),
])
def test_unrelated_generic_payments_keep_their_existing_wire_contract(operation, tail, payload):
    response = run("proxy", operation=operation, payload=payload, project=ORDER_ID, actor="42")
    assert response["status"] == 200
    sent = response["sent"][0]
    assert sent["url"] == f"https://yleum.ru/api/runtime/projects/{ORDER_ID}/{tail}"
    assert sent["method"] == "POST" and json.loads(sent["body"]) == payload


@pytest.mark.parametrize("operation", ["order-checkout", "order-checkout-receipt"])
@pytest.mark.parametrize("expected,actor,payload,status", [
    ("42", None, {"order_id": ORDER_ID}, 401),
    ("42", "43", {"order_id": ORDER_ID}, 409),
    (None, "42", {"order_id": ORDER_ID}, 409),
    ("42", "42", {"order_id": ORDER_ID, "amount": 1}, 400),
    ("42", "42", {"order_id": ORDER_ID, "status": "succeeded"}, 400),
])
def test_checkout_proxy_rejects_actor_switch_or_injected_amount_and_status(
    operation, expected, actor, payload, status,
):
    response = run("proxy", operation=operation, payload=payload, project=ORDER_ID,
                   actor=actor, expectedActor=expected, initData="raw-fallback")
    assert response == {"status": status, "sent": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "pickup", "yookassa"])
async def test_generation_uses_linked_checkout_only_for_explicit_online_staff_mode(mode):
    from yleum_api.services.integration_generation import generation_context

    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: ["yookassa"])),
        scalar=AsyncMock(side_effect=[False, mode]),
    )
    context = await generation_context(session, UUID(ORDER_ID))
    assert ("createYleumOrderCheckout(order.id)" in context) is (mode == "yookassa")
    assert ("getReturnedYleumCheckoutOrderId()" in context) is (mode == "yookassa")
    assert ("createYleumPayment({amount" in context) is (mode is None)
    assert ("STAFF PICKUP ORDERS" in context) is (mode == "pickup")
    assert ("failure_reason=receipt_required" in context) is (mode == "yookassa")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,handler", [
    ("order-checkout", "create_staff_checkout"),
    ("order-checkout-status", "get_staff_checkout"),
])
async def test_real_checkout_proxy_signature_pins_platform_body_and_actor(
    client, db_session, monkeypatch, operation, handler,
):
    from yleum_api.routers import max_staff_checkout

    project, _ = await seed(client, db_session, monkeypatch)
    payment = AsyncMock(return_value=SimpleNamespace(
        order_id=UUID(ORDER_ID), status="pending",
        confirmation_url="https://checkout.example/pay", is_test=False, failure_reason=None,
    ))
    monkeypatch.setattr(max_staff_checkout, handler, payment)
    forwarded = run("proxy", operation=operation, payload={"order_id": ORDER_ID},
                    project=project, actor="42", expectedActor="42")["sent"][0]
    path = forwarded["url"].removeprefix("https://yleum.ru")
    body = forwarded["body"]
    response = await client.post(path, headers=forwarded["headers"], content=body)
    assert response.status_code == 200
    assert response.json() == {**CHECKOUT_RECEIPT, "failure_reason": None}
    payment.assert_awaited_once()
    assert payment.await_args.args[1:4] == (UUID(project), UUID(ORDER_ID), 42)
    tampered = await client.post(path, headers=forwarded["headers"], content=body + " ")
    assert tampered.status_code == 401
    assert payment.await_count == 1


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
