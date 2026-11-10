"""Real adapter serialization and failure handling, with synthetic HTTP only."""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.errors import UpstreamProviderError
from yleum_gateway.main import create_app
from yleum_gateway.providers import llmgw
from yleum_gateway.routers import messages_native
from yleum_gateway.services import billing, provider_calls
from yleum_gateway.services import provider_failure_diagnostics as diagnostics
from yleum_gateway.services.provider_failure_store import StoreResult
from yleum_gateway.services.provider_validation_diagnostics import provider_validation_scope

_KEY = "synthetic-transport-key-not-a-real-credential"
_ERROR = {"error": {"type": "invalid_request_error", "message": "messages.1.tool_calls: missing tool result"}}


@pytest.fixture
def private_packets(monkeypatch):
    packets = []

    def store(packet):
        packets.append(json.loads(json.dumps(packet)))
        return StoreResult(state="stored", artifact_id=str(uuid4()), reason=None)

    monkeypatch.setattr(diagnostics, "store_provider_failure", store)
    return packets


def _transport(monkeypatch, respond):
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda *a, **kw: httpx.MockTransport(respond))


@pytest.mark.parametrize("mode", ["json", "collected_stream", "public_stream"])
async def test_real_chat_422_retains_reason_without_an_arm_or_replay(monkeypatch, mode, private_packets):
    monkeypatch.setenv("LLMGW_API_KEY", _KEY)
    reset_settings_cache()
    requests = []

    def reject(request):
        requests.append(request)
        return httpx.Response(422, json=_ERROR, headers={"x-request-id": "synthetic-422"})

    _transport(monkeypatch, reject)
    project, run, call = uuid4(), uuid4(), uuid4()
    with (
        capture_logs() as logs,
        provider_validation_scope(project_id=project, run_id=run, call_id=call),
        pytest.raises(UpstreamProviderError) as raised,
    ):
        if mode == "public_stream":
            async for _ in llmgw.astream("claude-sonnet-5.5", [{"role": "user", "content": "synthetic prompt"}], retry_preconnect=False):
                pass
        else:
            await llmgw.acompletion(
                model="claude-sonnet-5.5", messages=[{"role": "user", "content": "synthetic prompt"}],
                max_tokens=None if mode == "collected_stream" else 100, retry_preconnect=False,
            )
    events = [row for row in logs if row["event"] == "provider.http_failure"]
    assert len(requests) == len(events) == len(private_packets) == 1
    event = events[0]
    assert event["upstream_http_status"] == raised.value.details["upstream_http_status"] == 422
    assert event["provider_scope"] == "llmgw"
    assert (event["project_id"], event["run_id"], event["call_id"]) == tuple(map(str, (project, run, call)))
    assert event["request_body_sha256"] == hashlib.sha256(requests[0].content).hexdigest()
    assert json.loads(private_packets[0]["response_body"]["utf8"]) == _ERROR
    assert "utf8" not in event["response_body"]
    assert "missing tool result" not in json.dumps(logs)
    assert event["response_body"]["sha256"] == hashlib.sha256(httpx.Response(422, json=_ERROR).content).hexdigest()
    assert "synthetic prompt" not in json.dumps(event) and _KEY not in json.dumps(event)
    assert "missing tool result" not in str(raised.value.details)
    payload = json.loads(requests[0].content)
    assert payload["model"] == "anthropic/claude-sonnet-5.5"
    assert payload["stream"] is (mode != "json")
    assert ("max_tokens" in payload) is (mode == "json")
    if mode != "json":
        assert payload["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize("backup", [False, True])
def test_native_agent_failure_is_recorded_before_fallback_and_not_exposed(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, backup, private_packets,
):
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: (_KEY, "https://api.llmgw.ru/v1"))
    monkeypatch.setattr(messages_native.text_failover, "enabled", lambda model: backup)
    monkeypatch.setattr(messages_native.text_failover, "credentials", lambda scope: ("synthetic-backup-key", "https://backup.test/v1"))
    requests = []

    def reject(request):
        requests.append(request)
        if request.url.host == "api.llmgw.ru":
            return httpx.Response(422, json=_ERROR, headers={"x-request-id": "synthetic-primary"})
        return httpx.Response(402, json={"error": {"code": "insufficient_balance", "message": "synthetic-backup-key"}})

    _transport(monkeypatch, reject)
    project, run = uuid4(), uuid4()
    with TestClient(create_app()) as client, capture_logs() as logs:
        response = client.post("/v1/messages", json={
            "model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "synthetic native prompt"}],
            "metadata": {"project_id": str(project), "run_id": str(run), "stage": "verification"},
        })
    events = [row for row in logs if row["event"] == "provider.http_failure"]
    assert len(requests) == len(events) == len(private_packets) == provider_calls.finish_call.await_count == (2 if backup else 1)
    assert response.status_code == (402 if backup else 422)
    assert [row["upstream_http_status"] for row in events] == ([422, 402] if backup else [422])
    assert json.loads(private_packets[0]["response_body"]["utf8"]) == _ERROR
    assert all("utf8" not in row["response_body"] for row in events)
    assert "missing tool result" not in json.dumps(logs)
    assert all(row["project_id"] == str(project) and row["run_id"] == str(run) for row in events)
    assert [row["call_id"] for row in events] == [str(call.args[0]) for call in provider_calls.finish_call.await_args_list]
    assert len({row["call_id"] for row in events}) == len(events)
    assert all(row["request_body_sha256"] == hashlib.sha256(req.content).hexdigest() for row, req in zip(events, requests, strict=True))
    public = response.text
    assert "missing tool result" not in public and _KEY not in public
    assert "synthetic native prompt" not in json.dumps(events) and "synthetic-backup-key" not in json.dumps(events)
    payload = json.loads(requests[0].content)
    assert payload["model"] == "anthropic/claude-sonnet-5.5"
    assert payload["stream"] is False and "max_tokens" not in payload
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("messages", [
    [{"role": "assistant", "content": [{"type": "thinking", "thinking": "private marker"}]}],
    [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "orphan-private", "content": "private marker"}]}],
    [{"role": "assistant", "content": [{"type": "tool_use", "id": "pending-private", "name": "read_file", "input": {}}]}],
], ids=["thinking-only", "orphan-result", "unresolved-call"])
def test_invalid_native_history_is_rejected_before_admission_and_provider_io(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, messages,
):
    admitted = AsyncMock()
    monkeypatch.setattr(messages_native.generation_attempts, "dispatch", admitted)
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: (_KEY, "https://api.llmgw.ru/v1"))
    with TestClient(create_app()) as client:
        response = client.post("/v1/messages", json={"model": "claude-sonnet-5.5", "messages": messages})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid native provider request"
    assert "private" not in response.text
    assert admitted.await_count == billing.charge.await_count == provider_calls.start_call.await_count == 0


def test_production_failure_store_is_dedicated_and_survives_container_replacement():
    gateway = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((gateway / "deploy/full/docker-compose.yml").read_text(encoding="utf-8"))
    mount = "gateway-provider-failures:/var/lib/yleum/provider-failures"
    assert "gateway-provider-failures" in compose["volumes"]
    assert compose["services"]["gateway"]["volumes"].count(mount) == 1
    assert all(
        not any("gateway-provider-failures" in str(item) for item in service.get("volumes", []))
        for name, service in compose["services"].items() if name != "gateway"
    )
    assert "RUN install -d -m 0700 /var/lib/yleum/provider-failures" in (gateway / "Dockerfile").read_text(encoding="utf-8")
