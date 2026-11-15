"""Paid native content filtering may verify the retained source without model replay."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from tests.test_generation_recovery_deadlines import _state
from yleum_api.services import agent_native, llm_client
from yleum_api.services.agent_builder import FILTERED_PROVIDER_OUTPUT, SOURCE_FINALIZATION_HANDOFF
from yleum_api.services.generation import provider_attempts, recovery_execution
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
from yleum_api.services.generation.recovery_contract import ContinuationClaim

_FILTER = {
    "type": "message", "stop_reason": None, "provider_finish_reason": "content_filter",
    "content": [],
}
_LIVE = {
    "type": "error", "error": {
        "type": "provider_output_filtered", "code": "provider_output_filtered",
        "message": "private upstream text", "retry_safe": False,
    },
}


class _Waited(Exception):
    pass


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("evidence", ["owned", "missing", "chat", "wrong_type", "wrong_reason"])
async def test_native_filter_requires_owned_messages_response(monkeypatch, cold, evidence):
    state = _state()
    owner = state.identity
    identity = GenerationProviderIdentity(
        owner.run_id, owner.project_id, owner.user_id, uuid4(), owner.prompt_hash, "a" * 64,
    )
    claim = ContinuationClaim(
        run_id=owner.run_id, token=uuid4(), attempt_seq=state.attempt_seq,
        worker_id="test-worker", expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    frame = {"version": 1, "kind": "native", "value": {}}
    state = state.model_copy(update={"continuation_claim": claim})
    context = {}
    if cold:
        state = state.model_copy(update={
            "execution": state.execution.model_copy(update={"substep": "response_wait"}),
        })
        context = {"active_frame": frame}

    @asynccontextmanager
    async def session():
        yield object()

    driver = recovery_execution.ExecutionDriver(session, state, context, AsyncMock())
    driver.soft_boundary = AsyncMock()
    driver.save = AsyncMock()
    driver.wait = AsyncMock(side_effect=_Waited)
    attempt = SimpleNamespace(
        run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
        attempt_id=identity.attempt_id, prompt_hash=owner.prompt_hash, request_sha256="a" * 64,
    )
    monkeypatch.setattr(
        recovery_execution, "_retained_completion_attempt",
        AsyncMock(return_value=attempt if evidence != "missing" else None),
    )
    confirmed = {
        "owned": _FILTER,
        "missing": _FILTER,
        "chat": {"choices": [{"finish_reason": "content_filter"}]},
        "wrong_type": {**_FILTER, "type": "error"},
        "wrong_reason": {**_FILTER, "provider_finish_reason": "stop"},
    }[evidence]
    read = AsyncMock(side_effect=[_FILTER, confirmed] if cold else [confirmed])
    monkeypatch.setattr(llm_client, "require_recovered_response", read)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=identity),
    )
    reserve = AsyncMock()
    monkeypatch.setattr(provider_attempts, "reserve_generation_wire", reserve)

    posts = []

    async def transport(request):
        posts.append(request.method)
        return httpx.Response(502, json=_LIVE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        async def invoke():
            return await agent_native._call_messages(
                client, "https://synthetic.invalid/v1/messages",
                [{"role": "user", "content": "synthetic"}], "system", tools=[],
            )

        if evidence == "owned":
            with pytest.raises(llm_client.LLMError) as caught:
                await driver.provider(frame, invoke)
            assert caught.value.code == "provider_output_filtered"
            assert caught.value.finish_reason == "content_filter"
            assert caught.value.provider_charge_ambiguous is False
            assert caught.value.retry_safe is False
            assert "private upstream text" not in str(caught.value)
            driver.wait.assert_not_awaited()
        else:
            with pytest.raises(_Waited):
                await driver.provider(frame, invoke)
            driver.wait.assert_awaited_once_with()

    assert posts == ([] if cold else ["POST"])
    assert reserve.await_count == int(not cold)
    assert read.await_count == int(cold) + int(evidence != "missing")
    assert all(call.args == (identity,) for call in read.await_args_list)


@pytest.mark.parametrize(
    "allow_handoff,source_repair,settled",
    [(True, False, True), (False, False, True), (True, True, True), (True, False, False)],
)
async def test_native_filter_handoff_never_repeats_model_or_accepts_unverified_source(
    monkeypatch, allow_handoff, source_repair, settled,
):
    settings = agent_native.get_settings().model_copy(update={
        "use_max_finalization_coordinator": True,
    })
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    path = "src/app/page.tsx"
    response = {"stop_reason": "tool_use", "content": [{
        "type": "tool_use", "name": "write_file", "id": "original-write",
        "input": {"path": path, "content": "draft"},
    }]}
    failure = llm_client.LLMError(
        "PROVIDER_OUTPUT_FILTERED: fixed", code=FILTERED_PROVIDER_OUTPUT,
        finish_reason="content_filter" if settled else None,
        provider_charge_ambiguous=False if settled else None,
    )
    execute = AsyncMock(return_value={"ok": True, "content": "draft"})
    effects_before_filter = []

    async def model_response(*_args, **_kwargs):
        if complete.await_count == 1:
            return response
        effects_before_filter.append(execute.await_count)
        raise failure

    complete = AsyncMock(side_effect=model_response)
    monkeypatch.setattr(agent_native, "_call_messages", complete)

    async def provider(_frame, invoke):
        return await invoke()

    async def tool(_key, _action, invoke):
        return await invoke()

    driver = SimpleNamespace(
        state=_state(), context={}, restore_frame=lambda kind: None,
        provider=provider, tool=tool,
    )
    result = await agent_native.run_native_build(
        model="claude-sonnet-5.5", system="MAX VERIFICATION OVERRIDE",
        task="build an app", execute=execute, max_steps=4, max_segments=2,
        portable_cell=True, continuation=driver, source_repair=source_repair,
        allow_filtered_source_handoff=allow_handoff,
    )

    assert complete.await_count == 2
    assert effects_before_filter == [execute.await_count]
    assert sum(call.args[0].name == "write_file" for call in execute.await_args_list) == 1
    assert result.files == {path: "draft"}
    assert result.done is False and result.proof_checkpoint is None
    assert result.continuation_reason == (FILTERED_PROVIDER_OUTPUT if settled else "")
    if allow_handoff and not source_repair and settled:
        assert result.requires_verification_only
        assert result.stop_reason == SOURCE_FINALIZATION_HANDOFF
    else:
        assert not result.needs_finalization
        assert result.stop_reason == "provider_error"
