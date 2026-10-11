"""Real API/DB consent enforcement; no live credentials or provider writes."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from tests.test_amocrm_native_api import DATA, connected
from tests.test_integration_runtime_auth import assertion
from tests.test_integration_runtime_contracts import headers, upstream
from yleum_api.core.errors import ApiError
from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.routers import integration_runtime as runtime
from yleum_api.services.integration_consent import record_consent, set_policy


async def policy(session, project, version="v2"):
    await set_policy(
        session, UUID(project), enabled=True, policy_version=version, policy_hash="a" * 64
    )


async def consent(session, project, *, granted=True, revision=0):
    return await record_consent(
        session,
        UUID(project),
        42,
        event_id=uuid4(),
        expected_revision=revision,
        granted=granted,
        policy_version="v2",
        policy_hash="a" * 64,
    )


def forbid_credentials(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Denied or replayed request must not refresh provider credentials")

    monkeypatch.setattr(runtime, "_secrets", forbidden)


@pytest.mark.parametrize("state", ["absent", "revoked", "outdated"])
@pytest.mark.parametrize("signed_core", [False, True])
async def test_direct_initdata_cannot_bypass_consent_or_refresh_credentials(
    client, db_session, monkeypatch, state, signed_core
):
    project, _ = await connected(client, db_session, monkeypatch)
    await policy(db_session, project)
    if state != "absent":
        await consent(db_session, project, granted=state != "revoked")
    if state == "outdated":
        await policy(db_session, project, "v3")
    forbid_credentials(monkeypatch)
    path = f"/api/runtime/projects/{project}/leads"
    if signed_core:
        body = json.dumps(DATA, separators=(",", ":")).encode()
        result = await client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Omnia-Integration-Assertion": assertion(project, path, "POST", body),
            },
        )
    else:
        result = await client.post(path, headers=headers(), json=DATA)
    assert result.status_code == 403, result.text
    assert (
        result.json()["error"]["code"]
        == {
            "absent": "consent_required",
            "revoked": "consent_revoked",
            "outdated": "consent_policy_outdated",
        }[state]
    )
    assert (await db_session.scalars(select(IntegrationOperation))).all() == []


async def test_consent_requires_core_assertion_and_old_signed_grant_cannot_resurrect(
    client, db_session, monkeypatch
):
    project, _ = await connected(client, db_session, monkeypatch)
    await policy(db_session, project)
    path = f"/api/runtime/projects/{project}/consents"
    payload = dict(
        event_id=str(uuid4()),
        expected_revision=0,
        policy_version="v2",
        policy_hash="a" * 64,
        granted=True,
    )
    assert (await client.post(path, headers=headers(), json=payload)).status_code == 403

    async def signed(data):
        body = json.dumps(data, separators=(",", ":")).encode()
        return await client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Omnia-Integration-Assertion": assertion(project, path, "POST", body),
            },
        )

    first = await signed(payload)
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    revoked = await signed(
        {**payload, "event_id": str(uuid4()), "expected_revision": 1, "granted": False}
    )
    assert revoked.status_code == 200 and revoked.json()["revision"] == 2
    assert (await signed(payload)).json() == first.json()
    current = await client.get(
        path,
        headers={
            "X-Omnia-Integration-Assertion": assertion(project, path),
        },
    )
    assert current.json()["granted"] is False and current.json()["revision"] == 2
    forbid_credentials(monkeypatch)
    refused = await client.post(
        f"/api/runtime/projects/{project}/leads", headers=headers(), json=DATA
    )
    assert refused.status_code == 403


async def test_one_lead_note_and_read_only_replay_after_revocation(client, db_session, monkeypatch):
    project, _ = await connected(client, db_session, monkeypatch)
    await policy(db_session, project)
    await consent(db_session, project)
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/complex"):
            return httpx.Response(200, json=[{"id": 123}])
        return httpx.Response(200, json={"_embedded": {"notes": [{"id": 456}]}})

    upstream(monkeypatch, provider)
    path = f"/api/runtime/projects/{project}/leads"
    first = await client.post(path, headers=headers(), json=DATA)
    assert first.status_code == 200, first.text
    assert first.json()["details_status"] == "recorded"
    await consent(db_session, project, granted=False, revision=1)
    forbid_credentials(monkeypatch)
    replay = await client.post(path, headers=headers(), json=DATA)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert calls == ["/api/v4/leads/complex", "/api/v4/leads/123/notes"]
    fresh = await client.post(
        path, headers=headers(), json={**DATA, "idempotency_key": "different-intent-key"}
    )
    assert fresh.status_code == 403


async def test_other_actor_does_not_inherit_grant(client, db_session, monkeypatch):
    project, _ = await connected(client, db_session, monkeypatch)
    await policy(db_session, project)
    await consent(db_session, project)
    forbid_credentials(monkeypatch)
    response = await client.post(
        f"/api/runtime/projects/{project}/leads", headers=headers(99), json=DATA
    )
    assert response.status_code == 403


async def test_credentials_failure_before_dispatch_can_retry_same_intent(
    client, db_session, monkeypatch
):
    project, _ = await connected(client, db_session, monkeypatch)
    await policy(db_session, project)
    await consent(db_session, project)
    original = runtime._secrets
    attempts = 0

    async def flaky(session, connection):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ApiError(
                "integration_provider_unavailable", "Refresh temporarily unavailable", 503
            )
        return await original(session, connection)

    monkeypatch.setattr(runtime, "_secrets", flaky)
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/complex"):
            return httpx.Response(200, json=[{"id": 123}])
        return httpx.Response(200, json={"_embedded": {"notes": [{"id": 456}]}})

    upstream(monkeypatch, provider)
    path = f"/api/runtime/projects/{project}/leads"
    assert (await client.post(path, headers=headers(), json=DATA)).status_code == 503
    assert calls == []
    assert (await db_session.scalars(select(IntegrationOperation))).all() == []
    assert (await client.post(path, headers=headers(), json=DATA)).status_code == 200
    assert len(calls) == 2


async def test_revocation_blocks_first_note_but_preserves_successful_lead(
    client, db_session, monkeypatch
):
    project, _ = await connected(client, db_session, monkeypatch)
    await policy(db_session, project)
    await consent(db_session, project)

    def provider(request):
        if request.url.path.endswith("/complex"):
            return httpx.Response(200, json=[{"id": 123}])
        return httpx.Response(200, json={"_embedded": {"notes": [{"id": 456}]}})

    upstream(monkeypatch, provider)
    path = f"/api/runtime/projects/{project}/leads"
    assert (await client.post(path, headers=headers(), json=DATA)).status_code == 200
    # Disposable DB reproduces a lead whose note has never been dispatched.
    await db_session.execute(
        delete(IntegrationOperation).where(IntegrationOperation.kind == "lead_note")
    )
    await db_session.commit()
    await consent(db_session, project, granted=False, revision=1)
    forbid_credentials(monkeypatch)
    replay = await client.post(path, headers=headers(), json=DATA)
    assert replay.status_code == 200 and replay.json()["id"] == "123"
    assert replay.json()["details_status"] == "unknown"
    receipts = (await db_session.scalars(select(IntegrationOperation))).all()
    assert len(receipts) == 1 and receipts[0].status == "succeeded"
