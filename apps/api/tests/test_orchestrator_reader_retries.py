"""Transient reader denial must not replay commands or lose source CAS fences."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from yleum_api.services import orchestrator_client as client


@pytest.fixture
def transport(monkeypatch):
    clock = [0.0]
    pauses = []
    requests = []

    async def pause(seconds):
        pauses.append(seconds)
        clock[0] += seconds

    async def base_url(*_args):
        return "http://controller.test"

    monkeypatch.setattr(
        client,
        "get_settings",
        lambda: SimpleNamespace(
            orchestrator_internal_token=SimpleNamespace(get_secret_value=lambda: "unit-token"),
        ),
    )
    monkeypatch.setattr(client, "_base_url", base_url)
    monkeypatch.setattr(client, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client, "sleep", pause)
    real_client = httpx.AsyncClient

    def install(respond):
        async def handler(request):
            requests.append(request)
            return await respond(request)

        monkeypatch.setattr(
            client.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

    return SimpleNamespace(clock=clock, pauses=pauses, requests=requests, install=install)


def denied(message="reader inventory is incompatible or stale", *, code="workspace_busy"):
    return httpx.Response(503, json={"error": {"code": code, "message": message}})


def envelope():
    return {"generation_run_id": str(uuid4()), "fencing_epoch": 7}


@pytest.mark.parametrize("operation", ["identity", "write-files"])
async def test_transient_denial_preserves_fenced_request_and_host(transport, operation):
    binding = envelope()
    body = {**binding, "expected_revision": "a" * 64, "files": {"app.ts": "one"}}
    original = json.loads(json.dumps(body))

    async def respond(request):
        assert request.url.host == "controller.test"
        assert request.headers["X-Internal-Token"] == "unit-token"
        if len(transport.requests) == 1:
            body["files"]["app.ts"] = "changed during wait"
            binding["fencing_epoch"] = 99
            return denied()
        return httpx.Response(200, json={"ready": True})

    transport.install(respond)
    path = f"/internal/workspaces/{uuid4()}/agent/{operation}"
    result = await client._request(
        "GET" if operation == "identity" else "POST",
        path,
        params=binding if operation == "identity" else None,
        json=body if operation == "write-files" else None,
    )
    assert result == {"ready": True}
    assert transport.pauses == [1]
    assert len(transport.requests) == 2
    if operation == "identity":
        assert [r.url.params["fencing_epoch"] for r in transport.requests] == ["7", "7"]
    else:
        assert [json.loads(r.content) for r in transport.requests] == [original, original]
    assert transport.requests[0].extensions["timeout"]["read"] == 30
    assert transport.requests[1].extensions["timeout"]["read"] == 29


@pytest.mark.parametrize("message", sorted(client._READER_RETRY_MESSAGES))
async def test_every_known_temporary_inventory_denial_can_recover(transport, message):
    async def respond(_request):
        return denied(message) if len(transport.requests) == 1 else httpx.Response(200, json={})

    transport.install(respond)
    await client._request(
        "GET", f"/internal/workspaces/{uuid4()}/agent/identity", params=envelope()
    )
    assert len(transport.requests) == 2


@pytest.mark.parametrize("outcome", ["conflict", "other_busy", "bad_json", "network", "cancelled"])
async def test_non_readiness_failures_and_lost_lease_are_not_retried(transport, outcome):
    async def respond(request):
        if outcome == "conflict":
            return httpx.Response(
                409, json={"error": {"code": "conflict", "message": "stale lease"}}
            )
        if outcome == "other_busy":
            return denied("controller incarnation is not admitted")
        if outcome == "bad_json":
            return httpx.Response(503, text="not JSON")
        if outcome == "network":
            raise httpx.ReadTimeout("ambiguous outcome", request=request)
        raise asyncio.CancelledError

    transport.install(respond)
    error = (
        client.OrchestratorBadRequest
        if outcome == "conflict"
        else asyncio.CancelledError
        if outcome == "cancelled"
        else client.OrchestratorUnavailable
    )
    with pytest.raises(error):
        await client._request(
            "GET", f"/internal/workspaces/{uuid4()}/agent/identity", params=envelope()
        )
    assert len(transport.requests) == 1
    assert transport.pauses == []


@pytest.mark.parametrize("operation", ["exec", "bootstrap", "control", "restore"])
async def test_commands_and_lifecycle_are_never_replayed(transport, operation):
    async def respond(_request):
        return denied()

    transport.install(respond)
    body = {**envelope(), "operation_id": str(uuid4()), "expected_revision": "a" * 64}
    with pytest.raises(client.OrchestratorUnavailable):
        await client._request(
            "POST", f"/internal/workspaces/{uuid4()}/agent/{operation}", json=body
        )
    assert len(transport.requests) == 1


@pytest.mark.parametrize("missing", ["generation_run_id", "fencing_epoch", "expected_revision"])
async def test_source_write_without_complete_cas_binding_is_not_replayed(transport, missing):
    async def respond(_request):
        return denied()

    transport.install(respond)
    body = {**envelope(), "expected_revision": "a" * 64, "files": {"app.ts": "one"}}
    body.pop(missing)
    with pytest.raises(client.OrchestratorUnavailable):
        await client._request(
            "POST", f"/internal/workspaces/{uuid4()}/agent/write-files", json=body
        )
    assert len(transport.requests) == 1


@pytest.mark.parametrize("request_timeout", [3.0, 300.0])
async def test_persistent_denial_has_bounded_readiness_and_request_budgets(
    transport, request_timeout
):
    async def respond(_request):
        return denied()

    transport.install(respond)
    with pytest.raises(client.OrchestratorUnavailable):
        await client._request(
            "GET",
            f"/internal/workspaces/{uuid4()}/agent/identity",
            params=envelope(),
            timeout=request_timeout,
        )
    assert len(transport.requests) <= client._READER_RETRY_ATTEMPTS
    assert transport.clock[0] < min(request_timeout, client._READER_RETRY_SECONDS)


async def test_fence_rejection_after_readiness_wait_stops_immediately(transport):
    async def respond(_request):
        if len(transport.requests) == 1:
            return denied()
        return httpx.Response(409, json={"error": {"code": "conflict", "message": "stale epoch"}})

    transport.install(respond)
    with pytest.raises(client.OrchestratorBadRequest):
        await client._request(
            "GET", f"/internal/workspaces/{uuid4()}/agent/identity", params=envelope()
        )
    assert len(transport.requests) == 2


async def test_cancellation_while_waiting_does_not_send_a_second_request(transport, monkeypatch):
    async def respond(_request):
        return denied()

    async def cancel(_seconds):
        raise asyncio.CancelledError

    transport.install(respond)
    monkeypatch.setattr(client, "sleep", cancel)
    with pytest.raises(asyncio.CancelledError):
        await client._request(
            "GET", f"/internal/workspaces/{uuid4()}/agent/identity", params=envelope()
        )
    assert len(transport.requests) == 1


async def test_expired_budget_after_scheduler_delay_does_not_send_another_request(
    transport, monkeypatch,
):
    async def respond(_request):
        return denied()

    async def delayed(_seconds):
        transport.clock[0] += 40

    transport.install(respond)
    monkeypatch.setattr(client, "sleep", delayed)
    with pytest.raises(client.OrchestratorUnavailable):
        await client._request(
            "GET", f"/internal/workspaces/{uuid4()}/agent/identity", params=envelope(),
        )
    assert len(transport.requests) == 1
