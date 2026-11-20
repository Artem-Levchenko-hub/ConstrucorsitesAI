from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.schemas.message import PromptRequest
from yleum_api.services import agent_builder, agent_native, entitlements, llm_client
from yleum_api.services.generation import (
    agent_generation,
    provider_attempts,
    recovery_execution,
    recovery_reconciliation,
    response_auth,
)
from yleum_api.services.generation.acceptance import PromptAcceptance
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

KIMI = "kimi-k3.1-pro"


@pytest.mark.parametrize(
    "case, expected",
    [
        ("eligible", KIMI),
        ("internal_qa", KIMI),
        ("expired_qa", None),
        ("unrelated_plan", None),
        ("untrusted_role", None),
        ("outside", "ordinary-model"),
        ("no_units", None),
        ("previous", "ordinary-model"),
        ("prior_with_kimi_override", "ordinary-model"),
        ("prior_with_llmgw_kimi_override", "ordinary-model"),
        ("edited", "ordinary-model"),
        ("secret", None),
        ("wrong_template", None),
        ("wrong_owner", None),
        ("preflight_disabled", None),
        ("recovery_disabled", None),
        ("worker_disabled", None),
        ("soft_windows_disabled", None),
        ("missing_signing_key", None),
        ("short_signing_key", None),
        ("images", None),
    ],
)
async def test_single_fresh_funded_or_internal_qa_build_in_owned_pilot_project(
    monkeypatch, case, expected,
):
    project_id, user_id = uuid4(), uuid4()
    override = {
        "prior_with_kimi_override": KIMI,
        "prior_with_llmgw_kimi_override": "moonshotai/" + KIMI,
    }.get(case)
    settings = get_settings().model_copy(update={
        "max_kimi_pilot_project_ids": "" if case == "outside" else str(project_id),
        "force_model": override or "ordinary-model",
        "generation_response_retention_enabled": True,
        "use_max_finalization_coordinator": True,
        "use_generation_worker": case != "worker_disabled",
        "generation_recovery_soft_windows_enabled": case != "soft_windows_disabled",
        "generation_provider_attempts_enabled": case != "preflight_disabled",
        "generation_response_signing_secret": (
            None if case == "missing_signing_key" else
            SecretStr("s" if case == "short_signing_key" else "s" * 40)
        ),
    })
    monkeypatch.setattr(
        recovery_execution, "execution_enabled", lambda _: case != "recovery_disabled",
    )
    has_prior_build = case == "previous" or override is not None
    previous = uuid4() if has_prior_build else None
    if override is not None:
        monkeypatch.setattr(
            "yleum_api.services.generation.acceptance.model_for_role",
            lambda _role: "ordinary-model",
        )
    session = SimpleNamespace(scalar=AsyncMock(return_value=previous))
    qa_cases = {"internal_qa", "expired_qa", "unrelated_plan", "untrusted_role"}
    account_id, plan_id = uuid4(), uuid4()
    plan = SimpleNamespace(
        id=plan_id, code="free" if case == "unrelated_plan" else "qa_internal",
        is_active=False, entitlements={"qa_unlimited_generations": True},
    )
    expired = datetime.now(UTC) - timedelta(seconds=1) if case == "expired_qa" else None
    subscription = SimpleNamespace(
        user_id=user_id, billing_account_id=account_id, plan_id=plan_id, status="active",
        current_period_end=expired,
    )
    account = SimpleNamespace(id=account_id, personal_user_id=user_id)
    plan_context = entitlements.PlanContext(account, subscription, plan)
    monkeypatch.setattr(
        entitlements, "load_plan_context", AsyncMock(return_value=plan_context),
    )
    role = "admin" if case in qa_cases - {"untrusted_role"} else "user"
    admission = PromptAcceptance(
        project_id=project_id, session=session,
        current_user=SimpleNamespace(id=user_id, role=role),
        project=SimpleNamespace(
            id=project_id,
            owner_id=uuid4() if case == "wrong_owner" else user_id,
            template="nextjs" if case == "wrong_template" else "max_miniapp",
            is_imported=False,
        ),
        payload=PromptRequest(
            prompt="Build the verified Notes app",
            generation_engine="claude",  # Exercise the existing legacy pilot branch explicitly.
            reference_image_ids=[uuid4()] if case == "images" else [],
        ),
    )
    admission.settings = settings
    admission.generation_run = SimpleNamespace(id=uuid4(), user_id=user_id, project_id=project_id)
    admission.is_first_build = case != "edited"
    admission.is_free = case in qa_cases or case == "no_units"
    admission.unit_billing = not admission.is_free
    admission.credential_redirect = case == "secret"
    admission.explain_failed_build = False
    admission.selected_dump = None
    admission.font_selections = []
    admission.resolved_fonts = []
    admission.effective_prompt = admission.payload.prompt
    if expected is None:
        with pytest.raises(ApiError) as error:
            await admission.prepare_model_pilot()
        assert error.value.code == "conflict"
        return

    await admission.prepare_model_pilot()
    await admission.select_intent_and_model()
    assert admission.routing_model == expected
    assert admission.force_model == (None if override is not None else expected)
    assert session.scalar.await_count == (expected == KIMI or has_prior_build)
    if expected != KIMI:
        return
    await admission.conduct_interview()
    assert not admission.do_clarify
    assert not admission.async_onboarding
    assert admission.discovery_result is None


@pytest.mark.parametrize("repair", [False, True])
async def test_kimi_keeps_tool_pair_and_reasoning_in_next_request(monkeypatch, repair):
    settings = get_settings().model_copy(update={"use_max_finalization_coordinator": False})
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    path = "src/app/page.tsx"
    carrier = {"type": "provider_reasoning", "token": "synthetic-sealed-data"}
    requests, actions = [], []

    async def provider(_client, url, convo, _system, **kwargs):
        requests.append(deepcopy(convo))
        assert url.endswith("/v1/messages")
        assert kwargs["model"] == KIMI
        assert (kwargs["stage"] == "source_repair") is repair
        tool = {
            "type": "tool_use", "id": "read-one", "name": "read_file", "input": {"path": path},
        }
        if len(requests) == 1:
            assert "read_file" in {item["name"] for item in kwargs["tools"]}
            if repair:
                tools = {tool["name"]: tool for tool in kwargs["tools"]}
                assert set(tools) == {"read_file", "edit_file", "write_file", "build", "done"}
                assert tools["read_file"]["input_schema"]["properties"]["path"]["enum"] == [path]
            return {"stop_reason": "tool_use", "content": [carrier, tool]}
        assert requests[-1][-2] == {"role": "assistant", "content": [carrier, tool]}
        result = requests[-1][-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "read-one"
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Unfinished"}]}

    async def execute(action):
        actions.append(action.name)
        return {"ok": True, "content": "export const title = 'Old';"}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE",
        task="Rename the title without changing the app",
        model=KIMI,
        execute=execute,
        initial_files={
            path: "export const title = 'Old';", "src/app/other.tsx": "export const x = 1",
        },
        directed_path=path,
        source_repair=repair,
        kimi_repair=repair,
        kimi_repair_paths=(path,) if repair else (),
        completion_check=lambda *_: "Expected a real source edit",
        max_steps=2,
    )
    assert len(requests) == 2 and actions[0] == "read_file"
    assert not result.done


async def test_frozen_kimi_uses_native_without_changing_default(monkeypatch):
    settings = get_settings().model_copy(update={"use_native_agent": False})
    monkeypatch.setattr(agent_generation, "get_settings", lambda: settings)
    result = agent_builder.AgentResult(
        done=True, summary="stub", files={"src/app/page.tsx": "candidate"}, steps=1,
        stop_reason="done_green",
    )
    native = AsyncMock(return_value=result)
    legacy = AsyncMock(side_effect=AssertionError("Kimi cannot use the text provider route"))
    monkeypatch.setattr(agent_native, "run_native_build", native)
    monkeypatch.setattr(agent_builder, "run_agent_build", legacy)

    observed, provider_failure = await agent_generation.execute_agent_turn(
        _agent_res=None,
        _is_edit=False,
        _max_has_generated_snapshot=False,
        _max_seed_files={},
        _max_shell_enabled=False,
        baseline=SourceBaseline(None, None, {}),
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        is_free=False,
        project_info=SimpleNamespace(template="max_miniapp"),
        prompt_text="Build a QA notes app",
        runtime=SimpleNamespace(
            coordinator=None, handle=None, legacy_coordinator_handoff=lambda: False,
        ),
        plan=SimpleNamespace(
            model=KIMI, user="Build a QA notes app", stack_guide="", skills=None, steps=8,
        ),
        operations=SimpleNamespace(execute=AsyncMock(), emit=None),
    )
    assert observed is result and provider_failure is None
    assert native.await_count == 1
    assert native.await_args.kwargs["model"] == KIMI
    assert native.await_args.kwargs["continuation"] is None
    assert callable(native.await_args.kwargs["completion_check"])
    legacy.assert_not_called()


async def test_kimi_after_a_green_recheck_hands_off_without_new_provider_or_tool(monkeypatch):
    provider = AsyncMock(side_effect=AssertionError("Kimi source authority is gone"))
    execute = AsyncMock()
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        model=KIMI, system="MAX VERIFICATION OVERRIDE", task="Fix saved draft", execute=execute,
        source_repair=True, kimi_repair=True, kimi_repair_paths=(),
    )
    assert result.needs_finalization and not result.done
    provider.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.parametrize("stored", [False, True])
async def test_kimi_lost_source_authority_reads_only_the_owned_receipt(monkeypatch, stored):
    identity = GenerationProviderIdentity(uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "b" * 64)
    monkeypatch.setattr(recovery_reconciliation, "provider_reconciliation_requested", lambda: True)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=identity),
    )
    resume = AsyncMock()
    monkeypatch.setattr(provider_attempts, "resume_generation_wire", resume)
    body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Saved"}]}
    retained = (
        AsyncMock(return_value=body) if stored else AsyncMock(side_effect=llm_client.LLMError(
            "No stored response yet", code="generation_response_pending", retry_safe=True,
        ))
    )
    monkeypatch.setattr(llm_client, "require_recovered_response", retained)
    client = SimpleNamespace(stream=AsyncMock(side_effect=AssertionError("Paid POST is forbidden")))
    request = dict(model=KIMI, stage="source_repair", retained_only=True)
    if stored:
        assert await agent_native._call_messages(
            client, "https://gateway.test/v1/messages", [], "system", **request,
        ) == body
    else:
        with pytest.raises(llm_client.LLMError, match="No stored response"):
            await agent_native._call_messages(
                client, "https://gateway.test/v1/messages", [], "system", **request,
            )
    retained.assert_awaited_once_with(identity, kimi_native=True)
    resume.assert_not_awaited()
    client.stream.assert_not_called()


@pytest.mark.parametrize("warm,receipt", [
    (warm, receipt)
    for warm in (False, True) for receipt in ("settled", "foreign", "different_error", "pending")
] + [
    (True, "contradictory"), (False, "different_model"),
    (True, "live_missing_message"), (True, "live_missing_type"),
    (True, "live_retry_safe_null"), (True, "live_extra_fields"), (True, "live_wrong_status"),
])
async def test_kimi_unusable_output_requires_exact_owned_receipt(monkeypatch, warm, receipt):
    from pydantic import SecretStr

    settings = get_settings().model_copy(update={
        "llm_gateway_url": "https://gateway.test",
        "generation_response_retention_enabled": True,
        "generation_response_signing_secret": SecretStr("s" * 40),
    })
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    monkeypatch.setattr(
        recovery_reconciliation, "provider_reconciliation_requested", lambda: not warm,
    )
    identity = GenerationProviderIdentity(
        uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "b" * 64,
    )
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=identity),
    )
    resume = AsyncMock(return_value=None)
    monkeypatch.setattr(provider_attempts, "resume_generation_wire", resume)
    marker = {"type": "error", "error": {
        "type": "provider_output_invalid", "code": "provider_output_invalid",
        "message": "Provider response did not contain a usable assistant turn", "retry_safe": False,
    }}
    requests = []

    def reply(request):
        requests.append(request.method)
        if request.method == "POST":
            assert request.url.path == "/v1/messages"
            body = {"type": "error", "error": dict(marker["error"])}
            if receipt == "live_missing_message":
                del body["error"]["message"]
            elif receipt == "live_missing_type":
                del body["type"]
            elif receipt == "live_retry_safe_null":
                body["error"]["retry_safe"] = None
            elif receipt == "live_extra_fields":
                body["error"]["unexpected"] = "synthetic-private-error"
            return httpx.Response(503 if receipt == "live_wrong_status" else 502, json=body)
        assert request.method == "GET"
        assert request.url.path == f"/_internal/generation-responses/{identity.attempt_id}"
        assert response_auth.verify_generation_response_token(
            request.headers[response_auth.HEADER], purpose="generation_response.read",
        ) == identity
        envelope = {
            "version": 1,
            "identity": {} if receipt == "foreign" else identity.to_wire(),
            "state": "pending" if receipt == "pending" else "stored",
        }
        if receipt != "pending":
            body = ({"type": "error", "error": {**marker["error"], "retry_safe": 0}}
                    if receipt == "different_error" else marker)
            if receipt == "contradictory":
                body = {"detail": {"error": {"code": "model_unavailable", "details": {
                    "provider_failure_kind": "preconnect",
                    "provider_charge_ambiguous": False,
                }}}}
            envelope["response"] = {"format": "json", "status": 502, "body": body}
        return httpx.Response(200, json=envelope)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs),
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(llm_client.LLMError) as outcome:
            await agent_native._call_messages(
                client, "https://gateway.test/v1/messages", [], "system",
                model="claude-sonnet-5" if receipt == "different_model" else KIMI,
            )
    assert outcome.value.retry_safe is False
    assert (outcome.value.provider_charge_ambiguous is False) == (receipt == "settled")
    if receipt == "settled":
        assert outcome.value.code == "provider_output_invalid"
        assert str(outcome.value).startswith("PROVIDER_OUTPUT_INVALID:")
        resume.assert_not_awaited()
    if receipt.startswith("live_"):
        assert outcome.value.code == "generation_response_indeterminate"
        assert "synthetic-private-error" not in str(outcome.value)
        resume.assert_not_awaited()
        assert requests == ["POST"]
    else:
        assert requests == (["POST", "GET"] if warm else ["GET"])
