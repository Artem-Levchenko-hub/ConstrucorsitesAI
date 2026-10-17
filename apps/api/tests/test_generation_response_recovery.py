from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from yleum_api.services import llm_client
from yleum_api.services.generation import response_auth
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

IDENTITY = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)


@pytest.mark.parametrize("edge", ["complete", "sse", "native"])
@pytest.mark.parametrize("outcome", ["stored", "pending", "indeterminate"])
async def test_resumed_logical_provider_step_reads_without_any_completion_post(
    monkeypatch, edge, outcome
):
    import asyncio
    import json
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    from yleum_api.services import agent_native
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation import recovery_reconciliation as rec
    from yleum_api.services.generation.recovery_contract import ContinuationClaim

    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
        llm_gateway_url="http://gateway.test",
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=IDENTITY)
    )
    body = (
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "src/Привет.ts"},
                }
            ],
            "stop_reason": "tool_use",
        }
        if edge == "native"
        else {"choices": [{"message": {"content": "Привет"}, "finish_reason": "stop"}]}
    )
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"src/Привет.ts"}',
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    result = (
        {"format": "sse", "events": [json.dumps(chunk), "[DONE]"]}
        if edge == "sse"
        else {"format": "json", "status": 200, "body": body}
    )
    requests = []

    def reply(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json=body)
        response = {"version": 1, "identity": IDENTITY.to_wire(), "state": outcome}
        if outcome == "stored":
            response["response"] = result
        return httpx.Response(200, json=response)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw)
    )
    claim = ContinuationClaim(
        run_id=IDENTITY.run_id,
        token=UUID(int=9),
        attempt_seq=1,
        worker_id="restart",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    cursor = rec.ProviderEffectCursor(claim, 3, 7, reconcile_only=True)

    # A server-owned cursor restored from a committed logical step. No model
    # field or HTTP metadata can select this mode.
    async def invoke():
        payload = {"model": "claude-sonnet-5.5", "messages": [], "metadata": {}}
        if edge == "complete":
            return (
                await llm_client._chat_post(payload, "http://gateway.test/v1/chat/completions", 3)
            ).json()
        if edge == "sse":
            return [
                v
                async for v in llm_client._chat_stream_data(
                    payload,
                    "http://gateway.test/v1/chat/completions",
                    IDENTITY,
                    asyncio.get_running_loop().time() + 3,
                )
            ]
        async with httpx.AsyncClient() as client:
            return await agent_native._call_messages(
                client, "http://gateway.test/v1/messages", [], "sys"
            )

    with rec.provider_effect_scope(cursor):
        if outcome == "stored":
            actual = await invoke()
            assert actual == ([chunk] if edge == "sse" else body)
        else:
            with pytest.raises(llm_client.LLMError) as failure:
                await invoke()
            assert failure.value.retry_safe is False
    assert requests == [("GET", f"/_internal/generation-responses/{IDENTITY.attempt_id}")]


@pytest.mark.parametrize("outcome", ["confirmed", "indeterminate", "foreign"])
async def test_cold_held_response_uses_separate_reconcile_purpose_without_model_request(
    monkeypatch, outcome
):
    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
        llm_gateway_url="http://gateway.test",
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    requests = []
    body = {
        "content": [{"id": "exact-tool", "name": "write_file", "input": {"path": "src/a.ts"}}],
        "stop_reason": "tool_use",
    }

    def reply(request):
        requests.append((request.method, request.url.path))
        purpose = (
            "generation_response.reconcile"
            if request.method == "POST"
            else "generation_response.read"
        )
        assert (
            response_auth.verify_generation_response_token(
                request.headers[response_auth.HEADER],
                purpose=purpose,
            )
            == IDENTITY
        )
        assert (
            "/v1/messages" not in request.url.path and "/chat/completions" not in request.url.path
        )
        if len(requests) == 1:
            return httpx.Response(409)
        if request.method == "POST":
            assert request.url.path.endswith("/reconcile")
            if outcome == "indeterminate":
                return httpx.Response(409)
            return httpx.Response(
                200,
                json={
                    "version": 1,
                    "identity": IDENTITY.to_wire() if outcome != "foreign" else {},
                    "state": "confirmed",
                },
            )
        return httpx.Response(
            200,
            json={
                "version": 1,
                "identity": IDENTITY.to_wire(),
                "state": "stored",
                "response": {"format": "json", "status": 200, "body": body},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw)
    )
    if outcome == "confirmed":
        assert await llm_client.recover_generation_response(IDENTITY) == body
        assert [method for method, _ in requests] == ["GET", "POST", "GET"]
    else:
        with pytest.raises(llm_client.LLMError) as error:
            await llm_client.recover_generation_response(IDENTITY)
        assert error.value.provider_charge_ambiguous and not error.value.retry_safe
        assert [method for method, _ in requests] == ["GET", "POST"]


def test_record_header_preserves_runner_auth_and_default_none(monkeypatch):
    settings = SimpleNamespace(generation_response_retention_enabled=False)
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    assert response_auth.record_headers(IDENTITY) is None
    settings.generation_response_retention_enabled = True
    settings.generation_response_signing_secret = SecretStr("synthetic-test-secret-32-bytes-long")
    result = response_auth.record_headers(IDENTITY, {"Authorization": "runner-token"})
    assert result["Authorization"] == "runner-token"
    assert (
        response_auth.verify_generation_response_token(
            result[response_auth.HEADER], purpose="generation_response.record"
        )
        == IDENTITY
    )


@pytest.mark.parametrize("state", ["pending", "indeterminate", "missing", "wrong_binding"])
async def test_recovery_none_only_from_explicit_bound_pending(monkeypatch, state):
    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
        llm_gateway_url="http://gateway.test",
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)

    def reply(request):
        assert request.method == "GET"
        assert (
            response_auth.verify_generation_response_token(
                request.headers[response_auth.HEADER], purpose="generation_response.read"
            )
            == IDENTITY
        )
        if state == "missing":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "version": 1,
                "identity": IDENTITY.to_wire() if state != "wrong_binding" else {},
                "state": state,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw)
    )
    if state == "pending":
        assert await llm_client.recover_generation_response(IDENTITY) is None
    else:
        with pytest.raises(llm_client.LLMError) as exc:
            await llm_client.recover_generation_response(IDENTITY)
        assert exc.value.provider_charge_ambiguous is True and not exc.value.retry_safe


@pytest.mark.parametrize("edge", ["complete", "sse", "native"])
async def test_actual_callers_send_exact_record_purpose_without_output_cap(monkeypatch, edge):
    import asyncio
    import json
    from unittest.mock import AsyncMock

    from yleum_api.services import agent_native
    from yleum_api.services.generation import provider_attempts

    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=IDENTITY)
    )
    sent = []

    def reply(request):
        payload = json.loads(request.content)
        assert "max_tokens" not in payload
        assert (
            response_auth.verify_generation_response_token(
                request.headers[response_auth.HEADER], purpose="generation_response.record"
            )
            == IDENTITY
        )
        assert payload["metadata"]["generation_provider_identity"] == IDENTITY.to_wire()
        sent.append(payload)
        if edge == "sse":
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            )
        if edge == "native":
            assert request.headers["Authorization"] == "synthetic-runner"
            return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw)
    )
    payload = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "metadata": {},
    }
    if edge == "native":
        async with httpx.AsyncClient() as client:
            await agent_native._call_messages(
                client,
                "http://gateway.test/v1/messages",
                [],
                "synthetic",
                model="claude-sonnet-5.5",
                headers={"Authorization": "synthetic-runner"},
            )
    elif edge == "sse":
        assert [
            event
            async for event in llm_client._chat_stream_data(
                payload,
                "http://gateway.test/v1/chat/completions",
                IDENTITY,
                asyncio.get_running_loop().time() + 30,
            )
        ]
    else:
        assert (
            await llm_client._chat_post(payload, "http://gateway.test/v1/chat/completions", 30)
        ).status_code == 200
    assert len(sent) == 1


@pytest.mark.parametrize(
    "events", [[], ["partial"], ["[DONE]", "tail"], [{"dict": "not event"}, "[DONE]"]]
)
async def test_recovery_rejects_incomplete_or_malformed_sse(monkeypatch, events):
    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
        llm_gateway_url="http://gateway.test",
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    original = httpx.AsyncClient
    reply = httpx.Response(
        200,
        json={
            "version": 1,
            "identity": IDENTITY.to_wire(),
            "state": "stored",
            "response": {"format": "sse", "events": events},
        },
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(lambda _: reply), **kw),
    )
    with pytest.raises(llm_client.LLMError) as exc:
        await llm_client.recover_generation_response(IDENTITY)
    assert exc.value.provider_charge_ambiguous and not exc.value.retry_safe

@pytest.mark.parametrize("edge", ["native", "legacy"])
async def test_committed_handoff_bypasses_loop_error_repair_and_tools(monkeypatch, edge):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.services import agent_builder, agent_native
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    yielded = GenerationYielded(
        run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
    )
    call = AsyncMock(side_effect=yielded)
    execute = AsyncMock()
    if edge == "native":
        monkeypatch.setattr(agent_native, "_call_messages", call)
        run = agent_native._run_native_segment(
            system="synthetic",
            task="synthetic",
            model="synthetic",
            execute=execute,
            max_steps=1,
        )
    else:
        run = agent_builder._run_agent_segment(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="synthetic",
            execute=execute,
            complete=call,
            max_steps=1,
        )
    with pytest.raises(GenerationYielded) as caught:
        await run
    assert caught.value is yielded
    assert call.await_count == 1
    execute.assert_not_awaited()


@pytest.mark.parametrize("expire_on_resume", [False, True])
async def test_native_cold_frame_continues_at_saved_turn_without_replaying_prior_tool(
    monkeypatch,
    expire_on_resume,
):
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from yleum_api.services import agent_native
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    frames = []
    calls = []
    effects = []
    cold = False

    async def provider(frame, invoke):
        frames.append(deepcopy(frame))
        return await invoke()

    def restore(kind):
        assert kind == "native"
        return deepcopy(frames[-1]["value"]) if cold else None

    continuation = SimpleNamespace(provider=provider, restore_frame=restore)
    deadline = datetime.now(UTC) + timedelta(seconds=300)

    async def model(*args, **kwargs):
        calls.append(deepcopy(args[2]))
        if len(calls) == 2:
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
            )
        path = "first.ts" if len(calls) == 1 else "second.ts"
        return {
            "content": [
                {"type": "tool_use", "id": path, "name": "read_file", "input": {"path": path}}
            ],
            "stop_reason": "tool_use",
        }

    async def execute(action):
        effects.append((action.name, action.path))
        return {"ok": True, "content": "synthetic"}

    monkeypatch.setattr(agent_native, "_call_messages", model)

    async def run():
        return await agent_native._run_native_segment(
            system="synthetic",
            task="original task",
            model="synthetic",
            execute=execute,
            max_steps=2,
            continuation=continuation,
            edit_deadline=deadline,
        )

    with pytest.raises(GenerationYielded):
        await run()
    assert effects == [("read_file", "first.ts")]
    cold = True
    if expire_on_resume:

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return deadline + timedelta(seconds=1)

        monkeypatch.setattr(agent_native, "datetime", Clock)
    result = await run()
    assert calls[2] == calls[1]
    assert effects.count(("read_file", "first.ts")) == 1
    assert effects.count(("read_file", "second.ts")) == (0 if expire_on_resume else 1)
    assert result.steps == (1 if expire_on_resume else 2)
    assert frames[-1]["value"]["step"] == 1


@pytest.mark.parametrize("progressive_expired", [False, True])
async def test_native_outer_restart_keeps_completed_segment_steps_and_history(
    monkeypatch, progressive_expired,
):
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from yleum_api.services import agent_native
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation.recovery_execution import GenerationYielded
    deadline = datetime.now(UTC) + timedelta(seconds=300)
    driver = SimpleNamespace(context={"active_frame": None})
    calls = []
    saved = None

    async def segment(task, check, files):
        nonlocal saved
        calls.append((task, dict(files)))
        if len(calls) == 2:
            saved = deepcopy(driver.context)
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
            )
        return AgentResult(
            done=len(calls) == 3,
            summary="saved",
            files={"a.ts": "A"} if len(calls) == 1 else {"b.ts": "B"},
            steps=2 if len(calls) == 1 else 3,
            stop_reason="max_steps" if len(calls) == 1 else "done",
            transcript=[{"role": "assistant", "content": "first" if len(calls) == 1 else "second"}],
        )

    async def run():
        return await agent_native._run_native_segments(
            task="original",
            completion_check=None,
            max_segments=None if progressive_expired else 2,
            run_segment=segment,
            continuation=driver,
            edit_deadline=deadline if progressive_expired else None,
            snapshot_files=snapshot if progressive_expired else None,
        )

    async def snapshot():
        return {"a.ts": "A"} if calls else {}

    with pytest.raises(GenerationYielded):
        await run()
    driver.context = saved
    if progressive_expired:
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return deadline + timedelta(seconds=1)
        monkeypatch.setattr(agent_native, "datetime", Clock)
    result = await run()
    assert len(calls) == 3
    assert calls[2] == calls[1]
    assert result.steps == 5 and result.segments == 2
    assert result.files == {"a.ts": "A", "b.ts": "B"}
    assert [item["content"] for item in result.transcript] == ["first", "second"]

@pytest.mark.parametrize(
    "expire_on_resume,retry_after_read", [(False, False), (True, False), (False, True)],
)
async def test_legacy_cold_frame_keeps_turn_transcript_and_does_not_reexecute_first_tool(
    monkeypatch, expire_on_resume, retry_after_read
):
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.services import agent_builder, llm_client
    monkeypatch.setattr(agent_builder.asyncio, "sleep", AsyncMock())
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    frames, calls, effects = [], [], []
    cold = False
    deadline = datetime.now(UTC) + timedelta(seconds=300)

    async def provider(frame, invoke):
        frames.append(deepcopy(frame))
        assert datetime.fromisoformat(frame["value"]["call_deadline"]).utcoffset() is not None
        return await invoke()

    def restore(kind):
        assert kind == "legacy"
        return deepcopy(frames[-1]["value"]) if cold else None

    driver = SimpleNamespace(provider=provider, restore_frame=restore)

    async def complete(messages, *args, **kwargs):
        calls.append(deepcopy(messages))
        if len(calls) == 2:
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
            )
        if retry_after_read and len(calls) == 3:
            raise llm_client.LLMError(
                "confirmed refusal", retry_safe=True, provider_charge_ambiguous=False,
            )
        path = "first.ts" if len(calls) == 1 else "second.ts"
        return '<omnia:action name="read_file">{"path":"' + path + '"}</omnia:action>'

    async def execute(action):
        effects.append((action.name, action.path))
        return {"ok": True, "content": "synthetic"}

    async def run():
        return await agent_builder._run_agent_segment(
            system_prompt="synthetic",
            user_prompt="original task",
            model="synthetic",
            execute=execute,
            complete=complete,
            max_steps=2,
            continuation=driver,
            edit_deadline=deadline,
        )

    with pytest.raises(GenerationYielded):
        await run()
    assert effects == [("read_file", "first.ts")]
    cold = True
    if expire_on_resume:

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return deadline + timedelta(seconds=1)

        monkeypatch.setattr(agent_builder, "datetime", Clock)
    result = await run()
    assert calls[2] == calls[1]
    assert effects.count(("read_file", "first.ts")) == 1
    assert effects.count(("read_file", "second.ts")) == (0 if expire_on_resume else 1)
    assert result.steps == (1 if expire_on_resume else 2)
    assert frames[-1]["value"]["step"] == 1

    assert frames[-1]["value"]["attempt"] == (1 if retry_after_read else 0)


async def test_legacy_outer_restart_keeps_completed_segments(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.services import agent_builder
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    driver = SimpleNamespace(context={"active_frame": None})
    calls, saved = [], None

    async def segment(**kwargs):
        nonlocal saved
        calls.append((kwargs["user_prompt"], dict(kwargs["initial_written"])))
        if len(calls) == 2:
            saved = deepcopy(driver.context)
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
            )
        result = AgentResult(
            done=len(calls) == 3,
            summary="saved",
            files={"a.ts": "A"} if len(calls) == 1 else {"b.ts": "B"},
            steps=2 if len(calls) == 1 else 3,
            stop_reason="max_steps" if len(calls) == 1 else "done",
            transcript=[{"role": "assistant", "content": "first" if len(calls) == 1 else "second"}],
        )
        await kwargs["on_progress"](result)
        return result

    monkeypatch.setattr(agent_builder, "_run_agent_segment", segment)

    async def run():
        return await agent_builder.run_agent_build(
            system_prompt="synthetic",
            user_prompt="original",
            model="synthetic",
            execute=AsyncMock(),
            max_segments=2,
            continuation=driver,
        )

    with pytest.raises(GenerationYielded):
        await run()
    driver.context = saved
    result = await run()
    assert len(calls) == 3 and calls[2] == calls[1]
    assert result.steps == 5 and result.segments == 2
    assert result.files == {"a.ts": "A", "b.ts": "B"}
    assert [x["content"] for x in result.transcript] == ["first", "second"]


@pytest.mark.parametrize("edge", ["native", "legacy"])
async def test_actual_loop_committed_tool_handoff_precedes_executor(monkeypatch, edge):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.services import agent_builder, agent_native
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    yielded = GenerationYielded(
        run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
    )
    keys = []

    async def tool(key, action, invoke):
        keys.append((key, action.name, action.path))
        raise yielded

    async def provider(frame, invoke):
        return await invoke()

    continuation = SimpleNamespace(provider=provider, restore_frame=lambda kind: None, tool=tool)
    execute = AsyncMock(return_value={"ok": True})
    if edge == "native":
        model = AsyncMock(
            return_value={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "owned-read",
                        "name": "read_file",
                        "input": {"path": "src/a.ts"},
                    }
                ],
                "stop_reason": "tool_use",
            }
        )
        monkeypatch.setattr(agent_native, "_call_messages", model)
        invoke = agent_native._run_native_segment(
            system="synthetic",
            task="synthetic",
            model="synthetic",
            execute=execute,
            max_steps=1,
            continuation=continuation,
        )
    else:
        model = AsyncMock(
            return_value='<omnia:action name="read_file">{"path":"src/a.ts"}</omnia:action>'
        )
        invoke = agent_builder._run_agent_segment(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="synthetic",
            execute=execute,
            complete=model,
            max_steps=1,
            continuation=continuation,
        )
    with pytest.raises(GenerationYielded) as result:
        await invoke
    assert result.value is yielded
    assert keys == [
        ("owned-read" if edge == "native" else "legacy-action", "read_file", "src/a.ts")
    ]
    model.assert_awaited_once()
    execute.assert_not_awaited()
