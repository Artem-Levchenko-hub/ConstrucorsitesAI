"""Read-only legacy lead recovery through actual SDK and signed managed proxy."""

import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "apps/orchestrator/templates/max-miniapp-nextjs/src"
HARNESS = Path(__file__).with_name("fixtures") / "lead_receipt_contract_harness.cjs"
KEY = "synthetic-legacy-client-key"
DIGEST = "a" * 64
RECEIPT = {
    "provider": "amocrm",
    "id": "2964609",
    "status": "succeeded",
    "idempotency_key": KEY,
    "request_digest": DIGEST,
    "details_status": "recorded",
    "warning": None,
}


def run(mode, **data):
    path = (
        "lib/omnia/integration-client.ts"
        if mode == "sdk"
        else "app/api/omnia/integrations/[...path]/route.ts"
    )
    completed = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"mode": mode, "source": str(TEMPLATE / path), **data}),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("expectations", "expected"),
    [
        (None, {"idempotency_key": KEY}),
        (
            {"requestDigest": DIGEST, "leadId": "2964609"},
            {
                "idempotency_key": KEY,
                "expected_request_digest": DIGEST,
                "expected_lead_id": "2964609",
            },
        ),
    ],
)
def test_sdk_lookup_never_creates_or_touches_write_storage(expectations, expected):
    result = run("sdk", key=KEY, expectations=expectations, result=RECEIPT)
    assert result["value"] == RECEIPT
    assert len(result["sent"]) == 1
    call = result["sent"][0]
    assert call["url"] == "/api/omnia/integrations/lead-receipt"
    assert call["method"] == "POST" and call["credentials"] == "same-origin"
    assert json.loads(call["body"]) == {"initData": "", "payload": expected}
    assert KEY not in call["url"] and "name" not in call["body"]


@pytest.mark.parametrize("status", [404, 409, 422, 503])
def test_sdk_unknown_mismatch_and_rejection_stay_errors_without_replay(status):
    result = run(
        "sdk",
        key=KEY,
        status=status,
        result={"error": {"code": "integration_receipt_unavailable", "message": "Unavailable"}},
    )
    assert len(result["sent"]) == 1 and "value" not in result
    assert result["error"] == {
        "name": "YleumIntegrationError",
        "message": "Unavailable",
        "code": "integration_receipt_unavailable",
        "status": status,
    }


def test_proxy_exact_body_path_and_signed_actor_scope_without_credentials():
    payload = {
        "idempotency_key": KEY,
        "expected_request_digest": DIGEST,
        "expected_lead_id": "2964609",
    }
    result = run("proxy", payload=payload, project="synthetic-project", actor="42", result=RECEIPT)
    assert result["status"] == 200 and result["result"] == RECEIPT
    assert len(result["sent"]) == 1
    call = result["sent"][0]
    path = "/api/runtime/projects/synthetic-project/leads/receipt"
    assert call["url"] == f"https://yleum.ru{path}" and KEY not in call["url"]
    assert call["method"] == "POST" and call["cache"] == "no-store"
    assert json.loads(call["body"]) == payload
    assert set(call["headers"]) == {"Content-Type", "X-Omnia-Integration-Assertion"}
    version, encoded, signature = call["headers"]["X-Omnia-Integration-Assertion"].split(".")
    assertion = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert assertion["project_id"] == "synthetic-project" and assertion["max_user_id"] == "42"
    assert assertion["method"] == "POST" and assertion["path"] == path
    assert assertion["body_sha256"] == hashlib.sha256(call["body"].encode()).hexdigest()
    key = hmac.digest(b"synthetic-max-token", b"omnia:integration-assertion:v1", "sha256")
    assert signature == hmac.digest(key, f"{version}.{encoded}".encode(), "sha256").hex()


@pytest.mark.parametrize("actor", [None, "preview-actor", "0", "-42"])
def test_proxy_denies_missing_or_preview_actor_without_upstream_request(actor):
    result = run(
        "proxy", project="synthetic-project", actor=actor, payload={"idempotency_key": KEY}
    )
    assert result["status"] == 401 and result["sent"] == []


def test_proxy_preserves_genuine_init_data_authentication_fallback():
    result = run(
        "proxy",
        project="synthetic-project",
        actor=None,
        initData="synthetic-signed-init-data",
        payload={"idempotency_key": KEY},
        result=RECEIPT,
    )
    assert result["status"] == 200 and len(result["sent"]) == 1
    assert result["sent"][0]["headers"] == {
        "Content-Type": "application/json",
        "X-MAX-Init-Data": "synthetic-signed-init-data",
    }


def test_proxy_preview_actor_cannot_override_identity_with_init_data():
    result = run(
        "proxy",
        project="synthetic-project",
        actor="preview-actor",
        initData="untrusted",
        payload={"idempotency_key": KEY},
    )
    assert result["status"] == 401 and result["sent"] == []


@pytest.mark.parametrize("status", [404, 409, 422, 503])
def test_proxy_preserves_receipt_denial_without_fallback_creation(status):
    error = {"error": {"code": "integration_receipt_unavailable", "message": "Unavailable"}}
    result = run(
        "proxy",
        project="synthetic-project",
        actor="42",
        payload={"idempotency_key": KEY},
        status=status,
        result=error,
    )
    assert result["status"] == status and result["result"] == error
    assert len(result["sent"]) == 1
    assert result["sent"][0]["url"].endswith("/leads/receipt")
