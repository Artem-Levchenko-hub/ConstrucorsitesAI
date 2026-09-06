"""Exercise actual trusted TypeScript proxy and platform assertion boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tests.test_app_integrations_api import _max_init_data
from tests.test_integration_runtime_contracts import connect
from tests.test_integration_runtime_contracts import upstream as provider_http

_REPO = Path(__file__).resolve().parents[3]
_HARNESS = r"""
const fs = require('node:fs'); const vm = require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {stripTypeScriptTypes}=require('node:module');
let cookie; const requests=[]; const cache={};
const env={OMNIA_PROJECT_ID:input.project,MAX_BOT_TOKEN:'test-max-token',
           MAX_SESSION_SECRET:'test-session-secret'};
function load(file) {
 if(cache[file]) return cache[file];
 const ctx={exports:{},Buffer,URL,URLSearchParams,Date,AbortSignal,process:{env},
 fetch:async(url,options)=>{requests.push({url,...options});
 if(input.failure) throw new Error('synthetic-private-token '+input.failure);
 return new Response('{}');},
 require:name=>{
  if(name==='node:crypto') return require(name);
  if(name==='next/server') return {NextResponse:Response};
  if(name==='next/headers') return {cookies:async()=>({get:()=>cookie?{value:cookie}:undefined}),
                                   headers:async()=>new Headers(input.headers||{})};
  if(name.startsWith('@/')) return load(input.template+'/'+name.slice(2)+'.ts');
  throw new Error('Unexpected import '+name);
 }};
 // Execute trusted source using Node's builtin erasure, adapting only module syntax.
 let source=stripTypeScriptTypes(fs.readFileSync(file,'utf8'));
 const exported=[];
 source=source.replace(/import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?/g,
   (_,bindings,name)=>`const {${bindings.replace(/\bas\b/g,':')}}=require('${name}');`);
 source=source.replace(/export\s+((?:async\s+)?(?:function|class|const|let|var)\s+(\w+))/g,
   (_,declaration,name)=>{exported.push(name);return declaration;});
 source+='\nObject.assign(exports,{'+exported.join(',')+'});';
 vm.runInNewContext(source,ctx);cache[file]=ctx.exports;return ctx.exports;
}
const session=load(input.template+'/lib/max/session.ts');
if(input.user!==null) cookie=session.createMaxSession({id:input.user,firstName:'Test'}).value;
if(input.corruptCookie) cookie+='tampered';
const route=load(input.template+'/app/api/omnia/integrations/[...path]/route.ts');
const request=new Request('https://core.test/api/omnia/integrations/'+input.operation,{
 method:'POST',headers:input.headers,body:input.rawBody ?? JSON.stringify(input.body)});
route.POST(request,{params:Promise.resolve({path:[input.operation]})}).then(async result=>{
 console.log(JSON.stringify({status:result.status,body:await result.text(),requests}));
}).catch(e=>{console.error(e);process.exitCode=1});
"""


def proxy(
    project="00000000-0000-0000-0000-000000000001",
    user="42",
    operation="status",
    body=None,
    headers=None,
    corrupt_cookie=False,
    raw_body=None,
    failure=None,
):
    result = subprocess.run(
        ["node", "-e", _HARNESS],
        input=json.dumps(
            {
                "template": str(_REPO / "apps/orchestrator/templates/max-miniapp-nextjs/src"),
                "project": project,
                "user": user,
                "operation": operation,
                "body": body or {},
                "headers": headers or {},
                "corruptCookie": corrupt_cookie,
                "rawBody": raw_body,
                "failure": failure,
            }
        ),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def assertion(project, path, method="GET", body=b"", **overrides):
    now = int(time.time())
    claims = {
        "project_id": project,
        "max_user_id": "42",
        "iat": now,
        "exp": now + 60,
        "method": method,
        "path": path,
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    claims.update(overrides)
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    key = hmac.digest(b"test-max-token", b"omnia:integration-assertion:v1", "sha256")
    return f"v1.{encoded}." + hmac.new(key, f"v1.{encoded}".encode(), "sha256").hexdigest()


def test_actual_core_proxy_restores_signed_cookie_without_initdata():
    result = proxy(headers={"X-Omnia-Integration-Assertion": "attacker-assertion"})
    assert result["status"] == 200
    sent = result["requests"][0]
    assert sent["headers"]["X-Omnia-Integration-Assertion"].startswith("v1.")
    assert "attacker-assertion" not in json.dumps(sent)
    assert sent["method"] == "GET"


@pytest.mark.parametrize("user", [None, "preview-user", "0", "-42", "1.5"])
def test_proxy_rejects_untrusted_or_preview_identity(user):
    result = proxy(user=user, headers={"X-Omnia-Integration-Assertion": "forged"})
    assert result["status"] == 401
    assert result["requests"] == []


def test_proxy_rejects_tampered_cookie():
    result = proxy(corrupt_cookie=True)
    assert result["status"] == 401
    assert result["requests"] == []


async def test_signed_core_assertion_authenticates_restored_session(
    client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    result = proxy(project=project)
    assert result["status"] == 200
    upstream = result["requests"][0]
    response = await client.get(
        f"/api/runtime/projects/{project}/integrations",
        headers=upstream["headers"],
    )
    assert response.status_code == 200
    assert response.json()["providers"] == ["yookassa"]


@pytest.mark.parametrize(
    "change",
    [
        "project",
        "user",
        "expired",
        "future",
        "ttl",
        "path",
        "method",
        "body",
        "signature",
        "version",
    ],
)
async def test_assertion_rejects_tampering_and_wrong_scope(
    client,
    db_session,
    monkeypatch,
    change,
):
    project = await connect(client, db_session, monkeypatch)
    path = f"/api/runtime/projects/{project}/integrations"
    now = int(time.time())
    overrides = {
        "project": {"project_id": "00000000-0000-0000-0000-000000000099"},
        "user": {"max_user_id": "preview-user"},
        "expired": {"iat": now - 100, "exp": now - 40},
        "future": {"iat": now + 100, "exp": now + 160},
        "ttl": {"exp": now + 3600},
        "path": {"path": path + "/other"},
        "method": {"method": "POST"},
        "body": {"body_sha256": hashlib.sha256(b"different").hexdigest()},
    }.get(change, {})
    claims_path = overrides.pop("path", path)
    claims_method = overrides.pop("method", "GET")
    value = assertion(project, claims_path, method=claims_method, **overrides)
    if change == "signature":
        value = value[:-1] + ("0" if value[-1] != "0" else "1")
    if change == "version":
        value = value.replace("v1.", "v2.", 1)
    response = await client.get(path, headers={"X-Omnia-Integration-Assertion": value})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "max_init_data_invalid"


async def test_assertion_binds_exact_post_bytes(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    result = proxy(
        project=project,
        operation="payments",
        body={
            "payload": {
                "amount": "12.00",
                "description": "Test",
                "idempotency_key": "synthetic-payment-12345",
                "return_url": "https://app.test/return",
            }
        },
    )
    assert result["status"] == 200
    upstream = result["requests"][0]
    provider_http(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={
                "id": "payment-12345",
                "status": "pending",
                "confirmation": {"confirmation_url": "https://payments.test/confirm"},
            },
        ),
    )
    accepted = await client.post(
        f"/api/runtime/projects/{project}/payments",
        content=upstream["body"],
        headers=upstream["headers"],
    )
    assert accepted.status_code == 200
    response = await client.post(
        f"/api/runtime/projects/{project}/payments",
        content=upstream["body"] + " ",
        headers=upstream["headers"],
    )
    assert response.status_code == 401


async def test_raw_max_initdata_remains_compatible(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    response = await client.get(
        f"/api/runtime/projects/{project}/integrations",
        headers={"X-MAX-Init-Data": _max_init_data("test-max-token", 42)},
    )
    assert response.status_code == 200


async def test_proxy_raw_initdata_fallback_authenticates(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    result = proxy(
        project=project,
        user=None,
        body={
            "initData": _max_init_data("test-max-token", 42),
        },
    )
    assert result["status"] == 200
    headers = result["requests"][0]["headers"]
    assert "X-Omnia-Integration-Assertion" not in headers
    response = await client.get(f"/api/runtime/projects/{project}/integrations", headers=headers)
    assert response.status_code == 200


@pytest.mark.parametrize("raw_body", ["null", "[]", "42", "{broken"])
def test_proxy_rejects_nonobject_json(raw_body):
    result = proxy(raw_body=raw_body)
    assert result["status"] == 400
    assert result["requests"] == []


@pytest.mark.parametrize("failure", ["network", "timeout"])
def test_proxy_sanitizes_upstream_transport_failure(failure):
    result = proxy(failure=failure)
    assert result["status"] == 503
    assert "synthetic-private-token" not in result["body"]
    assert "signal" in result["requests"][0]
