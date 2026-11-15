import json
from dataclasses import replace
from uuid import uuid4

import pytest

from yleum_gateway.routers.messages_native import _anthropic_response, _openai_messages
from yleum_gateway.services.generation_identity import GenerationProviderIdentity


@pytest.fixture
def owner():
    return GenerationProviderIdentity(uuid4(), uuid4(), uuid4(), uuid4(), "a"*64, "b"*64)


@pytest.fixture
def session(owner):
    from yleum_gateway.services.kimi_native import KimiNativeSession

    return KimiNativeSession(
        identity=owner, route="/v1/messages", provider_scope="llmgw", secret="x"*64,
    )


def _response(provider_scope, *, finish="tool_calls"):
    actual = "moonshotai/kimi-k3.1-pro" if provider_scope == "llmgw" else "kimi-k3.1-pro"
    reasoning = (
        {"reasoning_content": "reasoning-is-private"} if provider_scope == "llmgw" else
        {"reasoning": "reasoning-is-private", "reasoning_details": [{"text": "содержимое", "index": 0}]}
    )
    return {
        "id": "chatcmpl-example",
        "model": actual,
        "choices": [{
            "finish_reason": finish,
            "message": {
                "role": "assistant",
                "content": "visible explanation",
                "tool_calls": [{
                    "type": "function", "id": "call_1",
                    "function": {"name": "inspect", "arguments": '{"path":"app.ts","depth":2}'},
                }],
                **reasoning,
            },
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8},
    }


def _history(content):
    return {"messages": [
        {"role": "user", "content": "Inspect file"},
        {"role": "assistant", "content": content},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "call_1", "content": "ok",
        }]},
    ]}


@pytest.mark.parametrize("scope", ["llmgw", "aitunnel"])
def test_native_kimi_tool_roundtrip_for_same_owned_run(session, scope):
    session = replace(session, provider_scope=scope)
    upstream = _response(scope)
    response = _anthropic_response(upstream, "kimi-k3.1-pro", kimi=session)
    assert response["stop_reason"] == "tool_use"
    assert response["usage"]["output_tokens"] == 8
    assert "reasoning-is-private" not in json.dumps(response)
    assert response["content"][0] == {"type": "text", "text": "visible explanation"}
    assert [item["type"] for item in response["content"]] == [
        "text", "tool_use", "provider_reasoning",
    ]
    assert response["content"][-1].keys() == {"type", "token"}
    next_owner = replace(session.identity, attempt_id=uuid4(), request_sha256="c"*64)
    messages = _openai_messages(_history(response["content"]), kimi=replace(session, identity=next_owner))
    original = upstream["choices"][0]["message"]
    fields = {key: value for key, value in original.items() if key.startswith("reasoning")}
    assert {key: messages[1][key] for key in fields} == fields
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {
        "depth": 2, "path": "app.ts",
    }
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}


@pytest.mark.parametrize("mutation", [
    "model", "provider", "route", "run", "owner", "project", "prompt",
    "tool_id", "tool_arguments", "secret", "tamper", "missing", "duplicate",
])
def test_native_kimi_reasoning_rejects_mismatched_context(session, mutation):
    from yleum_gateway.services.native_request_validation import NativeRequestValidationError

    response = _anthropic_response(_response("llmgw"), "kimi-k3.1-pro", kimi=session)
    content = response["content"]
    if mutation == "model":
        session = None
    elif mutation == "provider":
        session = replace(session, provider_scope="aitunnel")
    elif mutation == "route":
        session = replace(session, route="/v1/project-cell/messages")
    elif mutation in {"run", "owner", "project", "prompt"}:
        session = replace(session, identity=replace(session.identity, **{
            {"run": "run_id", "owner": "user_id", "project": "project_id", "prompt": "prompt_hash"}[mutation]:
                "d"*64 if mutation == "prompt" else uuid4(),
        }))
    elif mutation == "secret":
        session = replace(session, secret="y"*64)
    elif mutation == "tool_id":
        content[1]["id"] = "call_99"
    elif mutation == "tool_arguments":
        content[1]["input"]["depth"] = 3
    elif mutation == "missing":
        content.pop()
    elif mutation == "duplicate":
        content.append(content[-1])
    else:
        token = content[-1]["token"]
        content[-1]["token"] = ("A" if token[0] != "A" else "B") + token[1:]
    with pytest.raises(NativeRequestValidationError):
        _openai_messages(_history(content), kimi=session)


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_native_kimi_incomplete_completion_never_exposes_partial_tool_or_reasoning(session, finish):
    response = _anthropic_response(_response("llmgw", finish=finish), "kimi-k3.1-pro", kimi=session)
    assert response["provider_finish_reason"] == finish
    expected = [{"type": "text", "text": "visible explanation"}] if finish == "length" else []
    assert response["content"] == expected


@pytest.mark.parametrize("damage", ["wrong_model", "missing_reasoning", "invalid_args", "wrong_stop", "two_choices"])
def test_native_kimi_rejects_unconfirmed_tool_receipt(session, damage):
    response = _response("llmgw")
    choice = response["choices"][0]
    if damage == "wrong_model":
        response["model"] = "kimi-k3.1-pro"
    elif damage == "missing_reasoning":
        choice["message"].pop("reasoning_content")
    elif damage == "invalid_args":
        choice["message"]["tool_calls"][0]["function"]["arguments"] = '{"path":'
    elif damage == "wrong_stop":
        choice["finish_reason"] = "stop"
    else:
        response["choices"].append(choice.copy())
    with pytest.raises(ValueError, match="Kimi"):
        _anthropic_response(response, "kimi-k3.1-pro", kimi=session)


@pytest.mark.asyncio
async def test_kimi_llmgw_catalog_estimate_and_reported_cost_are_distinct():
    from decimal import Decimal

    from yleum_gateway.services.pricing import ReportedCost, resolve_request_cost

    usage = {"tokens_in": 2000, "tokens_out": 1000, "cache_read_tokens": 500}
    estimate, receipt = await resolve_request_cost("kimi-k3.1-pro", **usage)
    assert estimate == Decimal("2.2169")
    assert receipt["basis"] == "provider_catalog_estimate"
    assert receipt["reported_cost_rub"] is None
    actual, receipt = await resolve_request_cost(
        "kimi-k3.1-pro", **usage, reported=ReportedCost(Decimal("3.5"), rub_source="usage:cost_rub"),
    )
    assert actual == Decimal("3.5")
    assert receipt["basis"] == "provider_reported_rub"
    assert receipt["calculated_cost_rub"] == str(estimate)
    assert receipt["reported_cost_rub"] == str(actual)


@pytest.mark.asyncio
async def test_kimi_accounting_rejects_unverified_provider_tariff_and_cache_writes():
    from yleum_gateway.core.errors import ModelNotFoundError
    from yleum_gateway.services.pricing import resolve_request_cost

    with pytest.raises(ModelNotFoundError):
        await resolve_request_cost("kimi-k3.1-pro", tokens_in=10, tokens_out=8, provider_scope="aitunnel")
    with pytest.raises(ValueError, match="tariff"):
        await resolve_request_cost("kimi-k3.1-pro", tokens_in=10, tokens_out=8, cache_write_tokens=3)


def test_legacy_native_usage_keeps_cache_clamping_policy():
    data = _response("llmgw")
    data["usage"]["prompt_tokens_details"] = {"cached_tokens": 10}
    data["usage"]["cache_creation_input_tokens"] = 3

    result = _anthropic_response(data, "claude-sonnet-5.5")

    assert result["usage"] == {
        "input_tokens": 10,
        "output_tokens": 8,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.parametrize("gate", [
    "off", "foreign_project", "no_unit", "forged_signature", "missing_signature", "valid", "valid_prose",
    "provider_mismatch", "unknown_upstream", "tampered_history", "signed_edit",
    "wrong_frozen_model", "wrong_dispatch_owner", "edit_hold", "malformed_paid",
    "reasoning_only", "missing_usage", "zero_usage", "zero_cost", "paid_cache_write", "unknown_cache_write",
    "cache_read_exceeds_input", "cache_writes_clipped_to_zero", "cache_alias_conflict", "malformed_cache",
    "qa_valid", "qa_no_plan", "qa_bad_role", "qa_foreign_subscription", "qa_foreign_owner", "qa_expired",
    "qa_public_plan", "qa_wrong_plan", "qa_paused", "qa_no_entitlement",
    "qa_missing_attempt", "qa_forged_signature", "qa_conflicting_hold",
    "qa_unit_policy", "qa_wrong_stage", "qa_metadata_spoof",
    "unit_message", "qa_message", "unit_foreign_message", "qa_foreign_message", "unit_invalid_message",

])
def test_kimi_native_route_requires_configured_owned_billing_policy(monkeypatch, owner, gate):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tests.test_generation_response_routes import SettledLedger
    from tests.test_generation_response_store import Objects
    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.core.generation_response_auth import HEADER, mint_generation_response_token
    from yleum_gateway.routers import messages_native
    from yleum_gateway.services import generation_attempts, generation_response_capture
    from yleum_gateway.services.generation_identity import request_digest

    message_ids = {"user": uuid4(), "assistant": uuid4(), "foreign": uuid4()}
    supplied_message = {
        "unit_message": str(message_ids["assistant"]),
        "qa_message": str(message_ids["user"]),
        "unit_foreign_message": str(message_ids["foreign"]),
        "qa_foreign_message": str(message_ids["foreign"]),
        "unit_invalid_message": "invalid",
    }.get(gate)
    qa_request = gate.startswith("qa_")
    qa_policy = qa_request and gate != "qa_unit_policy"
    allowed = uuid4() if gate == "foreign_project" else owner.project_id
    monkeypatch.setenv("MAX_KIMI_PILOT_PROJECT_IDS", "" if gate == "off" else str(allowed))
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_RETENTION_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_SIGNING_SECRET", "x"*64)
    monkeypatch.setenv("LLMGW_API_KEY", "test-only")
    monkeypatch.setenv("LLMGW_BASE_URL", "https://llmgw.example.invalid/v1")
    reset_settings_cache()
    captures = {}

    async def preflight(identity):
        captures[identity.attempt_id] = (Objects(), SettledLedger())
        return captures[identity.attempt_id]

    async def admit(identity, **kwargs):
        await generation_response_capture.before_dispatch(identity)
        return uuid4()

    monkeypatch.setattr(generation_response_capture, "preflight_response_store", preflight)
    dispatch = AsyncMock(side_effect=admit)
    finish = AsyncMock()
    charge = AsyncMock()
    monkeypatch.setattr(generation_attempts, "dispatch", dispatch)
    monkeypatch.setattr(generation_attempts, "finish", finish)

    class Connection:
        async def fetchrow(self, query, *args):
            if query.startswith("SELECT p.version"):
                return {
                    "version": "accepted-build-v1" if qa_policy or gate == "no_unit" else "accepted-units-v2",
                    "is_free": qa_policy, "user_id": owner.user_id, "project_id": owner.project_id,
                    "project_owner": uuid4() if gate == "qa_foreign_owner" else owner.user_id,
                    "status": "running", "billing_account_id": None if qa_policy else owner.run_id,
                    "hold_user": owner.user_id, "hold_project": owner.project_id,
                    "kind": "edit" if gate == "edit_hold" else "initial"
                    if not qa_policy or gate == "qa_conflicting_hold" else None,
                    "state": None if qa_policy else "held", "units": None if qa_policy else 1,
                    "allocations": [{"grant_id": str(owner.run_id), "units": 1}],
                    "response_mode": "edit" if gate == "signed_edit" else "build",
                    "assistant_message_id": message_ids["assistant"], "user_message_id": message_ids["user"],
                    "project_template": "max_miniapp", "run_prompt_hash": owner.prompt_hash,
                    "frozen_model": "claude-sonnet-5.5" if gate == "wrong_frozen_model" else "kimi-k3.1-pro",
                    "dispatch_user": str(uuid4() if gate == "wrong_dispatch_owner" else owner.user_id),
                    "dispatch_project": str(owner.project_id), "dispatch_policy_eligible": True,
                }
            if query.startswith("SELECT request_sha256"):
                assert args == (submitted[-1].attempt_id, owner.run_id, owner.project_id, owner.user_id)
                if gate == "qa_missing_attempt":
                    return None
                return {"request_sha256": submitted[-1].request_sha256, "prompt_hash": owner.prompt_hash}
            if query.startswith("SELECT u.role"):
                assert args == (owner.user_id,)
                if gate == "qa_no_plan":
                    return None
                return {
                    "role": "user" if gate == "qa_bad_role" else "admin",
                    "subscriber": uuid4() if gate == "qa_foreign_subscription" else owner.user_id,
                    "subscription_status": "paused" if gate == "qa_paused" else "active",
                    "plan_code": "free" if gate == "qa_wrong_plan" else "qa_internal",
                    "plan_active": gate == "qa_public_plan",
                    "qa_unlimited": gate != "qa_no_entitlement",
                    "in_period": gate != "qa_expired",
                }
            assert query.startswith("SELECT bucket")
            return {"bucket": "edit" if gate == "edit_hold" else "initial", "units": 1}

        async def fetchval(self, query, *args):
            assert query.startswith("SELECT personal_user_id")
            return owner.user_id

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Connection()

    submitted = []
    monkeypatch.setattr(messages_native.billing, "get_pool", Pool)
    monkeypatch.setattr(messages_native.billing, "charge", charge)
    wallet = AsyncMock(side_effect=AssertionError("Kimi cannot use a wallet precheck"))
    monkeypatch.setattr(messages_native.billing, "precheck_balance", wallet)

    posts = []

    @asynccontextmanager
    async def timed_transport(url, payload, headers, *, deadline, evidence, provider_scope):
        assert provider_scope == "llmgw"
        assert url == "https://llmgw.example.invalid/v1/chat/completions"
        assert payload["model"] == "moonshotai/kimi-k3.1-pro"
        assert payload["stream"] is False
        assert all("cache_control" not in item for item in [*payload["messages"], *payload["tools"]])
        assert payload["messages"][0] == {"role": "system", "content": "instructions"}
        posts.append(payload)
        if gate == "unknown_upstream":
            evidence["transport_stage"] = "body"
            raise httpx.ReadTimeout("modeled upstream body timeout")
        data = _response("aitunnel" if gate == "provider_mismatch" else "llmgw")
        if gate in {"valid_prose", "reasoning_only"}:
            data["choices"][0]["finish_reason"] = "stop"
            data["choices"][0]["message"].pop("tool_calls")
            if gate == "reasoning_only":
                data["choices"][0]["message"]["content"] = "  "
        if gate == "malformed_paid":
            data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{"
        if gate == "missing_usage":
            del data["usage"]
        if gate == "zero_usage":
            data["usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
        if gate == "zero_cost":
            data["choices"] = []
            data["usage"]["cost_rub"] = "0"
        if gate in {"paid_cache_write", "unknown_cache_write", "cache_writes_clipped_to_zero"}:
            data["usage"]["cache_creation_input_tokens"] = 3
            if gate == "paid_cache_write":
                data["usage"]["cost_rub"] = "0.03"
                data["usage"]["prompt_tokens_details"] = {"cached_tokens": 2, "cache_write_tokens": 3}
            elif gate == "cache_writes_clipped_to_zero":
                data["usage"]["prompt_tokens_details"] = {"cached_tokens": 10}
        if gate == "cache_read_exceeds_input":
            data["usage"]["prompt_tokens_details"] = {"cached_tokens": 11}
        if gate == "cache_alias_conflict":
            data["usage"]["prompt_tokens_details"] = {"cache_write_tokens": 0}
            data["usage"]["cache_creation_input_tokens"] = 3
        if gate == "malformed_cache":
            data["usage"]["cache_creation_input_tokens"] = True
        yield httpx.Response(200, json=data)

    monkeypatch.setattr(messages_native, "open_timed_response", timed_transport)
    monkeypatch.setattr(messages_native, "_post_llmgw", lambda *args, **kwargs: pytest.fail("untimed POST"))

    cache_control = {"type": "ephemeral", "ttl": "5m"}
    body = {"model": "moonshotai/kimi-k3.1-pro",
            "system": [{"type": "text", "text": "instructions", "cache_control": cache_control}],
            "messages": [{"role": "user", "content": "test"}],
            "tools": [{"name": "inspect", "cache_control": cache_control}],
            "metadata": {"user_id": str(owner.user_id), "project_id": str(owner.project_id),
                         "run_id": str(owner.run_id),
                         "stage": "director" if gate == "qa_wrong_stage" else "build_plan",
                         "free": "true" if gate == "qa_metadata_spoof" else qa_request}}

    def submit(client, body):
        identity = replace(owner, attempt_id=uuid4(), request_sha256=request_digest("/v1/messages", body))
        submitted.append(identity)
        body["metadata"]["generation_provider_identity"] = identity.to_wire()
        auth = mint_generation_response_token(
            identity, purpose="generation_response.record",
            secret="y"*64 if gate in {"forged_signature", "qa_forged_signature"} else "x"*64,
        )
        headers = {} if gate == "missing_signature" else {HEADER: auth}
        if supplied_message is not None:
            # The request's semantic digest deliberately excludes transport metadata.
            body["metadata"]["message_id"] = supplied_message
            assert request_digest("/v1/messages", body) == identity.request_sha256
        return client.post("/v1/messages", json=body, headers=headers)

    app = FastAPI()
    app.include_router(messages_native.router)
    with TestClient(app) as client:
        result = submit(client, body)
        if gate in {"valid", "valid_prose", "tampered_history"}:
            assert result.status_code == 200, result.json()
            answer = result.json()
            assert answer["metadata"]["cost_provenance"]["basis"] == "provider_catalog_estimate"
            assert answer["metadata"]["actual_model"] == "kimi-k3.1-pro"
            assert "reasoning-is-private" not in result.text
            # Exactly one signed new logical request can continue the tool turn.
            body["messages"] = _history(answer["content"])["messages"]
            if gate == "valid_prose":
                body["messages"][-1]["content"] = "Continue"
            else:
                body["messages"][-1]["content"][0]["cache_control"] = cache_control
            if gate == "tampered_history":
                body["messages"][1]["content"][-1]["token"] = "invalid history"
            result = submit(client, body)
            assert result.status_code == (400 if gate == "tampered_history" else 200), result.json()
        elif gate in {"qa_valid", "unit_message", "qa_message"}:
            assert result.status_code == 200, result.json()
            assert result.json()["metadata"]["cost_provenance"]["basis"] == "provider_catalog_estimate"
        elif gate == "paid_cache_write":
            assert result.status_code == 200, result.json()
            provenance = result.json()["metadata"]["cost_provenance"]
            assert provenance["basis"] == "provider_reported_rub"
            assert provenance["reported_cost_rub"] == "0.03"
            assert provenance["calculated_cost_rub"] is None
            assert provenance["tariff"]["cache_write_factor"] is None
            assert provenance["cache_write_tokens"] == 3
            assert provenance["cache_read_tokens"] == 2
        else:
            assert result.status_code == {
                "off": 409, "foreign_project": 409, "no_unit": 409, "forged_signature": 403,
                "missing_signature": 403, "provider_mismatch": 409, "unknown_upstream": 503,
                "signed_edit": 409, "wrong_frozen_model": 409, "wrong_dispatch_owner": 409, "edit_hold": 409,
                "malformed_paid": 502, "reasoning_only": 502, "missing_usage": 503,
                "zero_usage": 503, "zero_cost": 503, "unknown_cache_write": 503,
                "cache_read_exceeds_input": 503, "cache_writes_clipped_to_zero": 503,
                "cache_alias_conflict": 503, "malformed_cache": 503,
                "qa_no_plan": 409, "qa_bad_role": 409, "qa_foreign_subscription": 409,
                "qa_foreign_owner": 409, "qa_expired": 409, "qa_public_plan": 409, "qa_no_entitlement": 409,
                "qa_missing_attempt": 409, "qa_forged_signature": 403, "qa_unit_policy": 409,
                "qa_wrong_stage": 409, "qa_metadata_spoof": 409, "qa_wrong_plan": 409,
                "qa_paused": 409, "qa_conflicting_hold": 409,
                "unit_foreign_message": 409, "qa_foreign_message": 409, "unit_invalid_message": 409,
            }[gate], result.json()

    wallet.assert_not_awaited()
    if gate in {"valid", "valid_prose"}:
        assert len(posts) == dispatch.await_count == finish.await_count == charge.await_count == 2
        prior = next(item for item in posts[1]["messages"] if item["role"] == "assistant")
        assert prior["reasoning_content"] == "reasoning-is-private"
    elif gate in {"qa_valid", "unit_message", "qa_message"}:
        assert len(posts) == dispatch.await_count == finish.await_count == charge.await_count == 1
        assert dispatch.await_args.kwargs["free"] is qa_request
        assert charge.await_args.kwargs["free"] is qa_request
        if supplied_message is not None:
            assert str(dispatch.await_args.kwargs["message_id"]) == supplied_message
            assert str(charge.await_args.kwargs["message_id"]) == supplied_message
        assert charge.await_args.kwargs["cost_rub"] > 0
        assert charge.await_args.kwargs["provider_binding"].identity == submitted[-1]
        assert captures[submitted[-1].attempt_id][1].reconciled_count == 1
    elif gate in {"tampered_history", "paid_cache_write"}:
        assert len(posts) == dispatch.await_count == finish.await_count == charge.await_count == 1
    elif gate in {"malformed_paid", "reasoning_only"}:
        assert len(posts) == dispatch.await_count == finish.await_count == charge.await_count == 1
        assert finish.await_args.kwargs.get("status") != "ambiguous"
        assert result.json() == {"type": "error", "error": {
            "type": "provider_output_invalid", "code": "provider_output_invalid",
            "message": "Provider response did not contain a usable assistant turn", "retry_safe": False,
        }}
        objects, ledger = captures[submitted[-1].attempt_id]
        assert ledger.committed is not None and ledger.reconciled_count == 1
        retained = json.loads(b"".join(
            objects.data[part["key"]] for part in ledger.parts if part["kind"] == "chunks"
        ))
        assert retained == {
            "version": 1, "format": "json", "status": 502,
            "body": result.json(), "identity": submitted[-1].to_wire(),
        }
    elif gate in {"provider_mismatch", "unknown_upstream", "missing_usage", "zero_usage",
                  "zero_cost", "unknown_cache_write", "cache_read_exceeds_input",
                  "cache_writes_clipped_to_zero", "cache_alias_conflict", "malformed_cache"}:
        assert len(posts) == dispatch.await_count == finish.await_count == 1
        assert finish.await_args.kwargs["status"] == "ambiguous"
        charge.assert_not_awaited()
        assert captures[submitted[-1].attempt_id][1].reconciled_count == 0
    else:
        assert not posts
        dispatch.assert_not_awaited()
        finish.assert_not_awaited()
        charge.assert_not_awaited()


@pytest.mark.parametrize("scope", ["llmgw", "aitunnel"])
@pytest.mark.parametrize("visible", ["  exact narration\n"])
def test_kimi_clean_assistant_turn_preserves_its_original_reasoning(session, scope, visible):
    session = replace(session, provider_scope=scope)
    data = _response(scope, finish="stop")
    message = data["choices"][0]["message"]
    message["content"] = visible
    message.pop("tool_calls")
    adapted = _anthropic_response(data, "kimi-k3.1-pro", kimi=session)
    assert adapted["stop_reason"] == "end_turn"
    assert "reasoning-is-private" not in json.dumps(adapted)
    assert adapted["content"][-1]["type"] == "provider_reasoning"
    body = {"messages": [
        {"role": "user", "content": "Start"},
        {"role": "assistant", "content": adapted["content"]},
        {"role": "user", "content": "Continue"},
    ]}
    prior = _openai_messages(body, kimi=session)[1]
    assert prior == message


@pytest.mark.parametrize("mutation", ["text", "missing", "nonassistant", "new_tool", "foreign_model", "foreign_block"])
@pytest.mark.parametrize("has_tools", [False, True])
def test_kimi_requires_owned_exact_assistant_turn(session, mutation, has_tools):
    from yleum_gateway.services.native_request_validation import NativeRequestValidationError

    data = _response("llmgw", finish="tool_calls" if has_tools else "stop")
    if not has_tools:
        data["choices"][0]["message"].pop("tool_calls")
    adapted = _anthropic_response(data, "kimi-k3.1-pro", kimi=session)
    body = _history(adapted["content"])
    if not has_tools:
        body["messages"][-1]["content"] = "Continue"

    assistant = body["messages"][1]
    if mutation == "text":
        assistant["content"][0]["text"] += " altered"
    elif mutation == "missing":
        assistant["content"].pop()
    elif mutation == "nonassistant":
        assistant["role"] = "user"
    elif mutation == "foreign_block":
        assistant["content"].append({"type": "thinking", "thinking": "foreign text"})
    elif mutation == "new_tool":
        assistant["content"].append({"type": "tool_use", "id": "call_2", "name": "inspect", "input": {}})
    else:
        session = None
    with pytest.raises(NativeRequestValidationError):
        _openai_messages(body, kimi=session)


@pytest.mark.parametrize("bad_config", ["arbitrary", "upper", "braces", "duplicate", "three", "trailing"])
def test_kimi_pilot_allowlist_rejects_invalid_environment(monkeypatch, bad_config):
    from pydantic import ValidationError

    from yleum_gateway.core.config import Settings

    valid = "aaaaaaaa-bbbb-4ccc-addd-eeeeeeeeeeee"
    monkeypatch.setenv("MAX_KIMI_PILOT_PROJECT_IDS", {
        "arbitrary": "some-project",
        "upper": valid.upper(),
        "braces": "{" + valid + "}",
        "duplicate": f"{valid},{valid}",
        "three": f"{valid},{uuid4()},{uuid4()}",
        "trailing": valid + ",",
    }[bad_config])
    with pytest.raises(ValidationError, match="max_kimi_pilot_project_ids"):
        Settings(_env_file=None)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_kimi_pilot_allowlist_reads_blank_and_exact_uuid_csv(monkeypatch, count):
    from yleum_gateway.core.config import Settings

    projects = tuple(uuid4() for _ in range(count))
    monkeypatch.setenv("MAX_KIMI_PILOT_PROJECT_IDS", ", ".join(map(str, projects)))
    assert Settings(_env_file=None).max_kimi_pilot_project_ids == projects
