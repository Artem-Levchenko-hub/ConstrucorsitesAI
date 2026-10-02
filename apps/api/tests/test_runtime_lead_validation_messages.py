"""Runtime form messages retain the typed rejection contract and existing SDK."""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from yleum_api.core.errors import validation_error_handler
from yleum_api.schemas.integration_runtime import RuntimeLeadRequest, RuntimeOrderRequest

PREFIX = "/api/runtime/projects/{project_id}"
PROJECT = "00000000-0000-4000-8000-000000000001"
ORDER = {
    "idempotency_key": "QA_VALIDATION_REQUEST_20261002",
    "buyer_name": "QA_MARKER",
    "lines": [{"product_id": PROJECT, "quantity": 1}],
}


def app_fixture():
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    dispatched = []

    @app.post(PREFIX + "/leads")
    async def lead(project_id: str, payload: RuntimeLeadRequest):
        dispatched.append((project_id, payload))
        return {"accepted": True}

    @app.post(PREFIX + "/orders")
    async def order(project_id: str, payload: RuntimeOrderRequest):
        dispatched.append((project_id, payload))
        return {"accepted": True}

    return app, dispatched


@pytest.mark.parametrize(
    "operation,payload,field,message",
    [
        ("leads", {"name": "   "}, "name", "Укажите имя."),
        ("leads", {"name": "Q" * 201}, "name", "Имя должно содержать не более 200 символов."),
        (
            "leads",
            {"name": "QA_MARKER", "phone": "QA_INVALID"},
            "phone",
            "Введите корректный телефон.",
        ),
        (
            "leads",
            {"name": "QA_MARKER", "email": "QA_INVALID"},
            "email",
            "Введите корректную электронную почту.",
        ),
        ("orders", {**ORDER, "buyer_name": ""}, "buyer_name", "Укажите имя покупателя."),
        (
            "orders",
            {**ORDER, "buyer_name": "Q" * 201},
            "buyer_name",
            "Имя покупателя должно содержать не более 200 символов.",
        ),
        ("orders", {**ORDER, "phone": "Q" * 65}, "phone", "Введите корректный телефон."),
    ],
)
def test_real_schema_rejection_has_localized_field_message_without_dispatch(
    operation,
    payload,
    field,
    message,
):
    app, dispatched = app_fixture()
    with TestClient(app) as client:
        response = client.post(f"/api/runtime/projects/{PROJECT}/{operation}", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["message"] == message
    assert response.json()["error"]["details"]["errors"][0]["loc"] == ["body", field]
    assert dispatched == []


def handler_request(path, method="POST", trusted=True):
    route = (
        APIRoute(path, endpoint=lambda: None, methods=[method])
        if trusted
        else SimpleNamespace(path=path)
    )
    return Request({"type": "http", "method": method, "path": path, "headers": [], "route": route})


def rendered_error(path, errors, method="POST", trusted=True):
    response = asyncio.run(
        validation_error_handler(
            handler_request(path, method, trusted),
            RequestValidationError(errors),
        )
    )
    return response, json.loads(response.body)["error"]


@pytest.mark.parametrize(
    "field,kind,message",
    [
        ("name", "missing", "Укажите имя."),
        ("name", "string_type", "Имя должно быть текстом."),
        ("phone", "value_error", "Введите корректный телефон."),
        ("email", "string_type", "Введите корректную электронную почту."),
        ("buyer_name", "string_type", "Имя покупателя должно быть текстом."),
    ],
)
def test_details_are_unchanged_and_message_never_echoes_input_msg_or_context(field, kind, message):
    operation = "orders" if field == "buyer_name" else "leads"
    errors = [
        {
            "loc": ["body", field],
            "type": kind,
            "msg": "QA_PRIVATE_MSG<script>",
            "input": "QA_PRIVATE_INPUT",
            "ctx": {"unsafe": "QA_PRIVATE_CONTEXT"},
        }
    ]
    response, body = rendered_error(PREFIX + "/" + operation, errors)
    assert response.status_code == 422
    assert body == {"code": "validation_failed", "message": message, "details": {"errors": errors}}
    assert "QA_PRIVATE" not in body["message"]


@pytest.mark.parametrize(
    "path,method,trusted",
    [
        ("/api/auth/register", "POST", True),
        (PREFIX + "/lead-status", "POST", True),
        (PREFIX + "/leads", "GET", True),
        (PREFIX + "/leads", "POST", False),
        (PREFIX + "/orders/extra", "POST", True),
    ],
)
def test_unrelated_method_route_and_untrusted_route_keep_existing_message(path, method, trusted):
    _, body = rendered_error(
        path, [{"loc": ["body", "phone"], "type": "value_error"}], method, trusted
    )
    assert body["message"] == "request validation failed"


@pytest.mark.parametrize(
    "operation,error,message",
    [
        (
            "leads",
            {"loc": ["body", "payload", "phone"], "type": "value_error"},
            "Проверьте данные заявки и повторите отправку.",
        ),
        (
            "leads",
            {"loc": ["path", "project_id"], "type": "uuid_parsing"},
            "Проверьте данные заявки и повторите отправку.",
        ),
        (
            "orders",
            {"loc": ["body", "lines"], "type": "too_short"},
            "Проверьте данные заказа и повторите отправку.",
        ),
        (
            "leads",
            {"loc": ["body", "phone"], "type": "QA_UNKNOWN_TYPE"},
            "Проверьте данные заявки и повторите отправку.",
        ),
    ],
)
def test_unknown_fields_types_and_locations_use_static_russian_fallback(operation, error, message):
    _, body = rendered_error(PREFIX + "/" + operation, [error])
    assert body["message"] == message


def test_multiple_errors_select_bounded_deterministic_first_field():
    errors = [
        {"loc": ["body", "email"], "type": "value_error"},
        {"loc": ["body", "name"], "type": "string_too_short"},
        {"loc": ["body", "phone"], "type": "value_error"},
    ] * 100
    _, first = rendered_error(PREFIX + "/leads", errors)
    _, second = rendered_error(PREFIX + "/leads", list(reversed(errors)))
    assert first["message"] == second["message"] == "Укажите имя."
    assert len(first["message"]) < 100


def test_valid_lead_and_order_still_dispatch_normally():
    app, dispatched = app_fixture()
    with TestClient(app) as client:
        assert (
            client.post(
                f"/api/runtime/projects/{PROJECT}/leads",
                json={"name": "QA_VALID", "phone": "+7 (900) 000-00-00"},
            ).status_code
            == 200
        )
        assert client.post(f"/api/runtime/projects/{PROJECT}/orders", json=ORDER).status_code == 200
    assert len(dispatched) == 2


def test_compiled_unchanged_sdk_exposes_localized_API_message(tmp_path):
    """No managed SDK upgrade: it already forwards the API's human message."""
    app, _ = app_fixture()
    with TestClient(app) as client:
        envelope = client.post(
            f"/api/runtime/projects/{PROJECT}/leads",
            json={"name": "QA_VALID", "phone": "QA_INVALID"},
        ).json()
    repo = Path(__file__).resolve().parents[3]
    sdk = (
        repo / "apps/orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/integration-client.ts"
    )
    typescript = Path(
        os.environ.get("QA_TYPESCRIPT_MODULE", str(repo / "apps/web/node_modules/typescript"))
    )
    if not shutil.which("node") or not typescript.exists():
        pytest.skip("Node/TypeScript unavailable for offline compiled SDK proof")
    script = tmp_path / "sdk.cjs"
    script.write_text("""
const fs=require('fs'),vm=require('vm'),ts=require(process.argv[2]);
const options={module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022};
const compiled=ts.transpileModule(fs.readFileSync(process.argv[3],'utf8'),
{compilerOptions:options}).outputText;
const body=JSON.parse(fs.readFileSync(0,'utf8'));let calls=0;
const context={exports:{},URLSearchParams,require:()=>({getMaxWebApp:()=>null}),
fetch:async()=>{calls++;return{ok:false,status:422,json:async()=>body};}};
vm.runInNewContext(compiled,context);
context.exports.createYleumLead({name:'QA_VALID',idempotency_key:'QA_TEST_VALIDATION_MARKER'}).then(()=>process.exit(2)).catch(error=>{
process.stdout.write(JSON.stringify({message:error.message,code:error.code,status:error.status,calls}));});
""")
    run = subprocess.run(
        ["node", str(script), str(typescript), str(sdk)],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(run.stdout) == {
        "message": "Введите корректный телефон.",
        "code": "validation_failed",
        "status": 422,
        "calls": 1,
    }
