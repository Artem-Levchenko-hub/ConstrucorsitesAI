"""Observed stop metadata survives progress without retaining provider text."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services.generation.progress import GenerationProgress


async def capture(monkeypatch, event, data):
    progress = GenerationProgress(AsyncMock(), uuid4(), uuid4(), uuid4())
    record = AsyncMock()
    monkeypatch.setattr(progress, "record_agent_step", record)
    await progress.emit_agent_event(event, data)
    return record.await_args.args[0]


@pytest.mark.parametrize("event,action", [
    ("agent.provider_error", ""),
    ("agent.step", "provider_response"),
])
async def test_safe_stop_metadata_is_retained(monkeypatch, event, action):
    row = await capture(monkeypatch, event, {
        "action": action, "human": "Разбиваю изменение на меньшие шаги",
        "reason": "output_limit", "recovery_attempt": 1,
        "finish_reason": "length", "requested_max_tokens": 128000,
        "completion_tokens": 128000,
        "raw_response": "PRIVATE_RESPONSE_NEVER_PERSIST",
    })
    assert row["finish_reason"] == "length"
    assert row["requested_max_tokens"] == 128000
    assert row["completion_tokens"] == 128000
    assert "PRIVATE_RESPONSE_NEVER_PERSIST" not in str(row)
    if action:
        assert row["reason"] == "output_limit"
        assert row["recovery_attempt"] == 1


@pytest.mark.parametrize("invalid", [True, -1, "128000", 10**100, None])
async def test_stop_metadata_rejects_untrusted_types_and_text(monkeypatch, invalid):
    row = await capture(monkeypatch, "agent.step", {
        "action": "provider_response", "human": "Проверяю ответ",
        "finish_reason": "PRIVATE_PROVIDER_TEXT",
        "requested_max_tokens": invalid, "completion_tokens": invalid,
    })
    assert "finish_reason" not in row
    assert "requested_max_tokens" not in row
    assert "completion_tokens" not in row
    assert "PRIVATE_PROVIDER_TEXT" not in str(row)
