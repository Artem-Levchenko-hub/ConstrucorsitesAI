"""Confirmed truncation replans a new control step without applying partial actions."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from yleum_api.services import agent_builder, llm_client
from yleum_api.services.generation.recovery_actions import ResponseRecovery

from .test_legacy_provider_error_policy import install_transport


def response(content, *, reason="stop", completion_tokens=120):
    return httpx.Response(200, json={
        "choices": [{"finish_reason": reason, "message": {"content": content}}],
        "usage": {"completion_tokens": completion_tokens},
    })


def action(name, **arguments):
    return f'<omnia:action name="{name}">{json.dumps(arguments)}</omnia:action>'


@pytest.mark.parametrize("tokens,expected", [(120, 120), (0, 0), (True, None),
                                            (-1, None), ("PRIVATE", None), ([], None)])
async def test_client_distinguishes_known_truncation_with_safe_metadata(
    monkeypatch, tokens, expected,
):
    install_transport(monkeypatch, lambda request: response("PRIVATE", reason="length",
                                                          completion_tokens=tokens))
    with pytest.raises(llm_client.LLMError) as caught:
        await llm_client.complete_chat([], "m", max_tokens=4096)
    failure = caught.value
    assert failure.code == "provider_output_truncated"
    assert failure.retry_safe is False
    assert failure.finish_reason == "length"
    assert failure.requested_max_tokens == 4096
    assert failure.completion_tokens == expected
    assert "PRIVATE" not in str(failure)


async def test_truncated_action_is_discarded_then_smaller_change_is_checked_and_saved(
    monkeypatch, capsys,
):
    payloads, actions, saved, events = [], [], [], []
    files = {"a.ts": "old"}
    replies = iter([
        response(action("bash", cmd="PRIVATE_NEVER_EXECUTE"), reason="length"),
        response(action("read_file", path="a.ts")),
        response(action("write_file", path="a.ts", content="new")),
        response(action("build")), response(action("done")),
    ])

    def handler(request):
        payloads.append(json.loads(request.content))
        return next(replies)

    async def snapshot():
        return dict(files)

    async def execute(command):
        actions.append(command.name)
        if command.name == "write_file":
            files[command.path] = command.args["content"]
        return {"ok": True, "content": files.get(command.path, "")}

    async def checkpoint(candidate):
        saved.append(dict(candidate.files))

    async def emit(event, data):
        events.append((event, data))

    install_transport(monkeypatch, handler)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="change a.ts", model="m", execute=execute,
        snapshot_files=snapshot, checkpoint=checkpoint, max_steps=5, emit=emit,
        coordinator_handoff=True,
    )
    assert result.done and result.needs_finalization
    assert actions == ["read_file", "write_file", "build"]
    assert saved == [{"a.ts": "new"}]
    assert len(payloads) == 5 and payloads[0]["messages"] != payloads[1]["messages"]
    instruction = payloads[1]["messages"][-1]["content"].lower()
    assert "not applied" in instruction and "smaller" in instruction and "read" in instruction
    assert "PRIVATE_NEVER_EXECUTE" not in json.dumps(payloads[1:])
    diagnostics = [data for event, data in events if data.get("tool") == "provider_response"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["reason"] == "output_limit"
    assert diagnostics[0]["finish_reason"] == "length"
    assert diagnostics[0]["requested_max_tokens"] == 16384
    assert diagnostics[0]["completion_tokens"] == 120
    assert "PRIVATE_NEVER_EXECUTE" not in json.dumps(events)
    assert "PRIVATE_NEVER_EXECUTE" not in capsys.readouterr().out
    assert not any(event == "agent.retry" for event, _ in events)


async def test_source_write_without_verified_receipt_does_not_renew_truncation_allowance(
    monkeypatch,
):
    calls, executed = [], []
    files = {"a.ts": "old"}
    recovery = ResponseRecovery()
    replies = iter([
        response(action("done"), reason="length"), response(action("done"), reason="length"),
        response(action("write_file", path="a.ts", content="new")),
        response(action("done"), reason="length"), response(action("done"), reason="length"),
        response(action("build")), response(action("done")),
    ])

    def handler(request):
        calls.append(request)
        return next(replies)

    async def snapshot():
        return dict(files)

    async def execute(command):
        executed.append(command.name)
        assert command.name == "write_file"
        files[command.path] = command.args["content"]
        return {"ok": True, "content": files.get(command.path, "")}

    install_transport(monkeypatch, handler)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="m", execute=execute,
        snapshot_files=snapshot, coordinator_handoff=True, max_steps=7,
        response_recovery=recovery,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "output_limit"
    assert result.files == files == {"a.ts": "new"}
    assert executed == ["write_file"]
    assert len(calls) == 4  # The third truncation stops before queued build/done.
    assert len({request.content for request in calls}) == 4  # No provider replay.
    assert recovery.budget.total == recovery.budget.since_progress == 3


@pytest.mark.parametrize("interleave", [False, True])
async def test_three_truncations_without_source_progress_stop_even_with_changing_usage(
    monkeypatch, interleave,
):
    calls, executed, events = [], [], []

    def handler(request):
        calls.append(json.loads(request.content))
        if interleave and len(calls) % 2 == 0:
            return response(action("write_file", path="a.ts", content="old"))
        return response(action("done"), reason="length", completion_tokens=len(calls))

    async def execute(command):
        executed.append(command.name)
        return {"ok": True, "content": "old"}

    async def snapshot():
        return {"a.ts": "old"}

    async def emit(event, data):
        events.append((event, data))

    install_transport(monkeypatch, handler)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="m", execute=execute, max_steps=20,
        snapshot_files=snapshot, emit=emit, max_segments=4,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "output_limit"
    assert len(calls) == (5 if interleave else 3)
    assert executed == (["write_file", "write_file"] if interleave else [])
    assert [data["recovery_attempt"] for _, data in events
            if data.get("tool") == "provider_response"] == [1, 2, 3]


@pytest.mark.parametrize("last", ["truncated", "done"])
async def test_old_green_cannot_rescue_pending_truncation_or_premature_done(monkeypatch, last):
    replies = [response(action("build")), response(action("done"), reason="length")]
    if last == "done":
        replies.append(response(action("done")))
    calls = []

    def handler(request):
        calls.append(request)
        return replies[len(calls) - 1]

    async def execute(command):
        assert command.name == "build"
        return {"ok": True}

    install_transport(monkeypatch, handler)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="m", execute=execute,
        max_steps=len(replies), coordinator_handoff=True, ship_green_on_abort=True,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "output_limit"


async def test_expired_deadline_after_truncation_does_not_start_new_post(monkeypatch):
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    clock = datetime.now(UTC)
    calls = []

    class Clock:
        @staticmethod
        def now(tz):
            return clock

    def handler(request):
        nonlocal clock
        calls.append(request)
        clock = deadline + timedelta(seconds=1)
        return response(action("bash", cmd="NEVER"), reason="length")

    async def execute(command):
        pytest.fail("no truncated action may run")

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(agent_builder, "datetime", Clock)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="m", execute=execute,
        max_steps=5, edit_deadline=deadline,
    )
    assert not result.done and len(calls) == 1
    assert result.stop_reason in {"source_deadline", "output_limit"}


async def test_cancellation_during_truncation_diagnostic_prevents_new_post(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return response(action("done"), reason="length")

    async def execute(command):
        pytest.fail("no truncated action may run")

    async def emit(event, data):
        if data.get("tool") == "provider_response":
            raise asyncio.CancelledError

    install_transport(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        await agent_builder.run_agent_build(
            system_prompt="s", user_prompt="t", model="m", execute=execute, emit=emit,
        )
    assert len(calls) == 1
