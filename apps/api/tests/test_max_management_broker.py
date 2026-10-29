"""Security regressions exercise the broker with a real HTTP transport."""

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.max_management import OwnerRecordsQuery, OwnerRecordUpdate
from yleum_api.services.max_management_contract import build_management_contract


class Session:
    def __init__(self):
        self.owner = uuid4()
        self.project = NS(
            id=uuid4(),
            owner_id=self.owner,
            template="max_miniapp",
            slug="shop",
            current_snapshot_id=uuid4(),
        )
        self.snapshot = NS(id=uuid4(), project_id=self.project.id, commit_sha="a" * 40)
        self.config = NS(owner_id=self.owner, config={"management_modules": ["leads"]})
        self.integration = NS(
            project_id=self.project.id,
            owner_id=self.owner,
            status="active",
            bot_token_enc="encrypted",
        )
        self.reads = []

    async def get(self, model, key, **kwargs):
        self.reads.append(model)
        return {Project: self.project, Snapshot: self.snapshot, MaxProjectConfig: self.config}.get(
            model
        )

    async def execute(self, statement):
        return NS(scalar_one_or_none=lambda: self.integration)


@pytest.fixture
def broker(monkeypatch):
    from yleum_api.services import max_management as service

    session = Session()
    contract = build_management_contract(["catalog", "orders"])
    publication = uuid4()
    active = {
        "release_id": str(publication),
        "snapshot_id": str(session.snapshot.id),
        "commit_sha": "a" * 40,
        "prod_url": "https://shop.app.test",
    }
    deploy = AsyncMock(
        return_value={
            "project_id": str(session.project.id),
            "phase": "failed",
            "active_publication": active,
        }
    )
    monkeypatch.setattr(service.orchestrator_client, "get_deploy", deploy)
    source_reads, requests, responses = [], [], []

    def read_file(*args):
        source_reads.append(args)
        return contract.model_dump_json().encode()

    monkeypatch.setattr(service.repo, "read_file", read_file)
    monkeypatch.setattr(
        service, "get_settings", lambda: NS(owner_management_apps_domain="app.test")
    )
    monkeypatch.setattr(service, "decrypt_strong", lambda value: "private-bot-token")

    async def handle(request):
        requests.append(request)
        if responses:
            reply = responses.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return httpx.Response(
            200,
            json={"publication_id": str(publication), "contract": contract.model_dump(mode="json")},
        )

    monkeypatch.setattr(
        service,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False),
    )
    return NS(
        service=service,
        session=session,
        contract=contract,
        active=active,
        deploy=deploy,
        requests=requests,
        responses=responses,
        publication=publication,
        source_reads=source_reads,
    )


async def overview(b):
    return await b.service.get_overview(b.session, b.session.project.id, b.session.owner)


async def listing(b):
    return await b.service.list_records(
        b.session, b.session.project.id, b.session.owner, OwnerRecordsQuery(resource="orders")
    )


def describe(b):
    return httpx.Response(
        200,
        json={"publication_id": str(b.publication), "contract": b.contract.model_dump(mode="json")},
    )


def record(**patch):
    return {
        "id": str(uuid4()),
        "resource": "orders",
        "created_at": "2026-10-29T09:00:00Z",
        "revision": "b" * 64,
        "status": "new",
        "customer_name": "Анна",
        "items": [],
        **patch,
    }


def test_assertion_matches_independent_crypto_and_exact_body():
    from yleum_api.services.max_management_assertion import sign_owner_assertion

    ids = [
        UUID("11111111-1111-4111-8111-111111111111"),
        UUID("22222222-2222-4222-8222-222222222222"),
        UUID("33333333-3333-4333-8333-333333333333"),
    ]
    body = '{"op":"list","query":{"resource":"leads","query":"Анна"}}'.encode()
    token = sign_owner_assertion("fixture-token", *ids, "a" * 64, body, now=1793264400)
    version, encoded, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert version == "1"
    assert claims == dict(
        project_id=str(ids[0]),
        owner_id=str(ids[1]),
        publication_id=str(ids[2]),
        contract_hash="a" * 64,
        method="POST",
        path="/api/omnia/owner",
        body_sha256=hashlib.sha256(body).hexdigest(),
        iat=1793264400,
        exp=1793264460,
    )
    derived = hmac.digest(b"fixture-token", b"yleum:owner-management:v1", "sha256")
    expected = (
        base64.urlsafe_b64encode(hmac.digest(derived, f"1.{encoded}".encode(), "sha256"))
        .decode()
        .rstrip("=")
    )
    assert signature == expected
    assert "fixture-token" not in token


@pytest.mark.parametrize("change,status", [("foreign", 404), ("missing", 404), ("other", 409)])
async def test_access_denial_precedes_private_or_runtime_reads(broker, change, status):
    b = broker
    if change == "foreign":
        b.session.project.owner_id = uuid4()
    elif change == "missing":
        b.session.project = None
    else:
        b.session.project.template = "nextjs"
    with pytest.raises(ApiError) as caught:
        await b.service.get_overview(b.session, uuid4(), b.session.owner)
    assert caught.value.status_code == status
    assert b.session.reads == [Project] and b.requests == []


async def test_active_snapshot_and_contract_are_independent_of_current_config(broker):
    b = broker
    result = await overview(b)
    assert result.state == "active" and result.configured_modules == ["leads"]
    assert result.active_modules == ["catalog", "orders"] and result.publication_id == b.publication
    assert b.source_reads == [(b.session.project.id, "a" * 40, ".omnia/management.json")]
    assert str(b.requests[0].url) == "https://shop.app.test/api/omnia/owner"
    assert b.requests[0].content == b'{"op":"describe"}'


async def test_no_active_publication_has_no_last_success_fallback(broker):
    broker.deploy.return_value.update(
        active_publication=None, phase="done", prod_url="https://shop.app.test"
    )
    result = await overview(broker)
    assert result.state == "not_published" and result.resources == []
    assert broker.requests == [] and broker.source_reads == []


@pytest.mark.parametrize("manifest", [None, b"{}", b'{"protocol":2}'])
async def test_legacy_or_unregistered_schema_is_unsupported(broker, monkeypatch, manifest):
    monkeypatch.setattr(broker.service.repo, "read_file", lambda *args: manifest)
    assert (await overview(broker)).state == "unsupported"
    assert broker.requests == []


async def test_catalog_only_contract_does_not_grant_business_access(broker, monkeypatch):
    monkeypatch.setattr(
        broker.service.repo,
        "read_file",
        lambda *args: build_management_contract(["catalog"]).model_dump_json().encode(),
    )
    assert (await overview(broker)).state == "unsupported"
    with pytest.raises(ApiError) as caught:
        await listing(broker)
    assert caught.value.status_code == 409 and broker.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.app.test",
        "https://evil.test",
        "https://shop.app.test.evil.test",
        "https://user:pw@shop.app.test",
        "https://shop.app.test:443",
        "https://shop.app.test/?x=1",
        "https://shop.app.test/#x",
        "https://127.0.0.1",
        "https://shop.app.test/other",
    ],
)
async def test_destination_validation_prevents_secret_egress(broker, url):
    broker.active["prod_url"] = url
    assert (await overview(broker)).state == "unavailable"
    assert broker.requests == []


@pytest.mark.parametrize("patch", [{"project_id": uuid4()}, {"commit_sha": "c" * 40}])
async def test_active_snapshot_must_match_owned_database_snapshot(broker, patch):
    for key, value in patch.items():
        setattr(broker.session.snapshot, key, value)
    assert (await overview(broker)).state == "unavailable" and broker.requests == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://evil.test"}),
        httpx.Response(200, content=b"broken"),
        httpx.Response(200, content=b"x" * (512 * 1024 + 1)),
        httpx.Response(500, json={"private": "private-bot-token"}),
        httpx.ReadTimeout("private-bot-token"),
    ],
)
async def test_private_transport_failures_are_unavailable_and_never_retried(broker, response):
    broker.responses.append(response)
    assert (await overview(broker)).state == "unavailable" and len(broker.requests) == 1


@pytest.mark.parametrize("mismatch", ["publication", "contract", "extra"])
async def test_describe_must_match_pinned_publication_and_contract(broker, mismatch):
    b = broker
    payload = {"publication_id": str(b.publication), "contract": b.contract.model_dump(mode="json")}
    if mismatch == "publication":
        payload["publication_id"] = str(uuid4())
    elif mismatch == "contract":
        payload["contract"] = build_management_contract(["leads"]).model_dump(mode="json")
    else:
        payload["bot_token"] = "private-bot-token"
    b.responses.append(httpx.Response(200, json=payload))
    assert (await overview(b)).state == "unavailable"


async def test_valid_list_reparses_typed_records(broker):
    b = broker
    b.responses.extend(
        [describe(b), httpx.Response(200, json={"records": [record()], "next_cursor": "page2"})]
    )
    page = await listing(b)
    assert page.records[0].customer_name == "Анна" and page.next_cursor == "page2"
    assert json.loads(b.requests[1].content)["query"]["resource"] == "orders"


@pytest.mark.parametrize(
    "bad",
    [{"payload": {"secret": 1}}, {"max_user_id": 1}, {"resource": "leads"}, {"revision": "bad"}],
)
async def test_list_rejects_unknown_private_or_wrong_resource_records(broker, bad):
    b = broker
    b.responses.extend([describe(b), httpx.Response(200, json={"records": [record(**bad)]})])
    with pytest.raises(ApiError) as caught:
        await listing(b)
    assert caught.value.status_code == 503 and "secret" not in caught.value.message


async def test_publication_change_after_rpc_discards_result(broker):
    b = broker
    original = dict(b.deploy.return_value)
    changed = {**original, "active_publication": {**b.active, "release_id": str(uuid4())}}
    b.deploy.side_effect = [original, original, changed]
    assert (await overview(b)).state == "unavailable"


@pytest.mark.parametrize(
    "code,status",
    [
        ("revision_conflict", 409),
        ("operation_conflict", 409),
        ("not_found", 404),
        ("unauthorized", 401),
        ("unsupported", 409),
        ("invalid_request", 400),
    ],
)
async def test_update_preserves_known_error_code_and_hides_private_message(broker, code, status):
    b = broker
    b.responses.extend(
        [
            describe(b),
            httpx.Response(status, json={"error": {"code": code, "message": "private-bot-token"}}),
        ]
    )
    update = OwnerRecordUpdate(expected_revision="b" * 64, operation_key=uuid4(), note="")
    with pytest.raises(ApiError) as caught:
        await b.service.update_record(
            b.session, b.session.project.id, b.session.owner, uuid4(), "orders", update
        )
    assert caught.value.code == code and caught.value.status_code == status
    assert "private-bot-token" not in caught.value.message and len(b.requests) == 2
    assert json.loads(b.requests[1].content)["update"] == update.model_dump(
        mode="json", exclude_unset=True
    )


async def test_private_orchestrator_error_is_sanitized_for_records(broker):
    b = broker
    b.deploy.side_effect = ApiError(
        "orchestrator_unavailable", "private-bot-token", 503, details={"token": "private-bot-token"}
    )
    with pytest.raises(ApiError) as caught:
        await listing(b)
    assert caught.value.code == "unavailable"
    assert caught.value.details is None and "private-bot-token" not in caught.value.message


async def test_owner_is_rechecked_before_loading_private_credential(broker, monkeypatch):
    b = broker
    calls = []

    def read_file(*args):
        b.session.project.owner_id = uuid4()
        return b.contract.model_dump_json().encode()

    def decrypt(value):
        calls.append(value)
        return "private-bot-token"

    monkeypatch.setattr(b.service.repo, "read_file", read_file)
    monkeypatch.setattr(b.service, "decrypt_strong", decrypt)
    with pytest.raises(ApiError) as caught:
        await listing(b)
    assert caught.value.status_code == 404 and calls == [] and b.requests == []


async def test_catalog_only_overview_retains_actual_active_modules(broker, monkeypatch):
    monkeypatch.setattr(
        broker.service.repo,
        "read_file",
        lambda *a: build_management_contract(["catalog"]).model_dump_json().encode(),
    )
    result = await overview(broker)
    assert result.state == "unsupported" and result.active_modules == ["catalog"]
    assert result.publication_id == broker.publication and result.contract_hash is not None


@pytest.mark.parametrize("cause", ["timeout", "changed_before", "changed_after"])
async def test_ambiguous_update_is_never_automatically_retried(broker, cause):
    b = broker
    record_id = uuid4()
    update = OwnerRecordUpdate(expected_revision="b" * 64, operation_key=uuid4(), note="")
    b.responses.append(describe(b))
    if cause == "timeout":
        b.responses.append(httpx.ReadTimeout("private-bot-token"))
    else:
        original = dict(b.deploy.return_value)
        changed = {**original, "active_publication": {**b.active, "release_id": str(uuid4())}}
        b.deploy.side_effect = [original] * (3 if cause == "changed_before" else 4) + [changed]
        b.responses.append(httpx.Response(200, json=record(id=str(record_id))))
    with pytest.raises(ApiError) as caught:
        await b.service.update_record(
            b.session, b.session.project.id, b.session.owner, record_id, "orders", update
        )
    assert caught.value.status_code == 503
    operations = [json.loads(request.content)["op"] for request in b.requests]
    assert operations == (["describe"] if cause == "changed_before" else ["describe", "update"])
    if len(b.requests) == 2:
        assert json.loads(b.requests[1].content)["update"]["operation_key"] == str(
            update.operation_key
        )


@pytest.mark.parametrize(
    "field,value", [("owner_id", "foreign"), ("project_id", "foreign"), ("status", "error")]
)
async def test_wrong_integration_never_decrypts_or_reaches_runtime(
    broker, monkeypatch, field, value
):
    b = broker
    setattr(b.session.integration, field, uuid4() if value == "foreign" else value)
    calls = []
    monkeypatch.setattr(b.service, "decrypt_strong", lambda value: calls.append(value))
    assert (await overview(b)).state == "unavailable" and calls == [] and b.requests == []


async def test_update_rejects_different_record_even_if_other_fields_are_valid(broker):
    b = broker
    b.responses.extend([describe(b), httpx.Response(200, json=record())])
    with pytest.raises(ApiError) as caught:
        await b.service.update_record(
            b.session,
            b.session.project.id,
            b.session.owner,
            uuid4(),
            "orders",
            OwnerRecordUpdate(expected_revision="a" * 64, operation_key=uuid4(), note="test"),
        )
    assert caught.value.status_code == 503


def test_fresh_python_assertion_is_verified_by_node_crypto():
    import shutil
    import subprocess

    from yleum_api.services.max_management_assertion import sign_owner_assertion

    body = '{"op":"list","query":{"resource":"leads","query":"Анна"}}'.encode()
    token = sign_owner_assertion(
        "fixture-token",
        UUID("11111111-1111-4111-8111-111111111111"),
        UUID("22222222-2222-4222-8222-222222222222"),
        UUID("33333333-3333-4333-8333-333333333333"),
        "a" * 64,
        body,
        now=1793264400,
    )
    script = r"""
const fs = require('node:fs'); const crypto = require('node:crypto');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const [version, encoded, signature] = data.token.split('.');
const key = crypto.createHmac('sha256', 'fixture-token')
  .update('yleum:owner-management:v1').digest();
const expected = crypto.createHmac('sha256', key).update(version + '.' + encoded).digest();
if (!crypto.timingSafeEqual(expected, Buffer.from(signature, 'base64url')))
  throw Error('signature');
const claims = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8'));
const digest = crypto.createHash('sha256')
  .update(Buffer.from(data.body, 'base64')).digest('hex');
if (claims.body_sha256 !== digest || claims.exp - claims.iat !== 60) throw Error('claims');
process.stdout.write('verified');
"""
    node = shutil.which("node")
    assert node is not None, "Node is required for the cross-language assertion regression"
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps({"token": token, "body": base64.b64encode(body).decode()}),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout == "verified"


@pytest.mark.parametrize("domain", ["", "app.localhost", "127.0.0.1"])
async def test_missing_or_local_apps_domain_cannot_authorize_destination(
    broker, monkeypatch, domain
):
    monkeypatch.setattr(
        broker.service, "get_settings", lambda: NS(owner_management_apps_domain=domain)
    )
    broker.active["prod_url"] = f"https://shop.{domain}"
    assert (await overview(broker)).state == "unavailable" and broker.requests == []


async def test_total_rpc_deadline_stops_unresponsive_transport(broker, monkeypatch):
    import asyncio

    b = broker
    calls = []

    async def stalled(request):
        calls.append(request)
        await asyncio.Event().wait()
        raise AssertionError("must be cancelled")

    monkeypatch.setattr(b.service, "RPC_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(
        b.service,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stalled), follow_redirects=False),
    )
    with pytest.raises(ApiError) as caught:
        await listing(b)
    assert caught.value.status_code == 503 and len(calls) == 1


async def test_update_returns_only_validated_snapshot_and_empty_note(broker):
    b = broker
    record_id = uuid4()
    b.responses.extend([describe(b), httpx.Response(200, json=record(id=str(record_id), note=""))])
    update = OwnerRecordUpdate(expected_revision="b" * 64, operation_key=uuid4(), note="")
    result = await b.service.update_record(
        b.session, b.session.project.id, b.session.owner, record_id, "orders", update
    )
    assert result.id == record_id and result.note == "" and result.status == "new"
    assert "private-bot-token" not in result.model_dump_json()


async def test_legacy_config_modules_stay_empty_instead_of_auto_enabling(broker):
    broker.session.config.config = {"app_name": "Legacy"}
    result = await overview(broker)
    assert result.configured_modules == [] and result.active_modules == ["catalog", "orders"]
