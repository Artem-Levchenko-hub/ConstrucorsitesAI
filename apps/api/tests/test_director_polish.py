"""Smoke tests for Director→Polish 2-pass generator (Phase L7).

Stubs out the LLM gateway via monkeypatching ``stream_chat_completion``
so we can verify the orchestration logic (pass ordering, IR passthrough,
usage aggregation, error propagation) without spending API tokens.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from omnia_api.services import director_polish


# ─── Test helpers ───────────────────────────────────────────────────────


class _StubStreamer:
    """Records call args + returns a scripted sequence of events."""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        self.scripts = scripts
        self.calls: list[list[dict[str, str]]] = []
        self.call_count = 0

    def __call__(
        self,
        messages: list[dict[str, str]],
        model: str,
        user_id: str,
        project_id: str,
        message_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        idx = self.call_count
        self.call_count += 1
        self.calls.append(list(messages))
        script = self.scripts[idx] if idx < len(self.scripts) else []

        async def _gen() -> AsyncIterator[dict[str, Any]]:
            for event in script:
                yield event

        return _gen()


# ─── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_director_polish_emits_pass_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: two passes, both emit progress markers."""
    director_chunks: list[dict[str, Any]] = [
        {"delta": '{"meta":{"title":"X","description":"Y"},'},
        {"delta": '"theme":{"primary":"#0F172A"},"sections":[]}'},
        {"usage": {"tokens_in": 100, "tokens_out": 30, "cost_rub": 1.0}},
    ]
    polish_chunks: list[dict[str, Any]] = [
        {"delta": '{"meta":{"title":"Real","description":"Real desc"},'},
        {"delta": '"theme":{"primary":"#0F172A"},"sections":[]}'},
        {"usage": {"tokens_in": 150, "tokens_out": 80, "cost_rub": 2.5}},
    ]
    stub = _StubStreamer([director_chunks, polish_chunks])
    monkeypatch.setattr(director_polish, "stream_chat_completion", stub)

    events: list[dict[str, Any]] = []
    async for event in director_polish.director_polish_generate(
        base_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ],
        user_prompt="Сайт для кофейни",
        director_model="claude-opus-4-7",
        polish_model="claude-opus-4-7",
        user_id=uuid4(),
        project_id=uuid4(),
        message_id=uuid4(),
    ):
        events.append(event)

    # Two passes, four progress markers (start+end each), final usage.
    # Markers may carry extra keys (e.g. "model") — assert on pass/stage only.
    pass_markers = [e for e in events if "pass" in e and "stage" in e]
    assert len(pass_markers) == 4
    assert pass_markers[0]["pass"] == "director"
    assert pass_markers[0]["stage"] == "start"
    assert pass_markers[1]["pass"] == "director"
    assert pass_markers[1]["stage"] == "end"
    assert pass_markers[2]["pass"] == "polish"
    assert pass_markers[2]["stage"] == "start"
    assert pass_markers[3]["pass"] == "polish"
    assert pass_markers[3]["stage"] == "end"


@pytest.mark.asyncio
async def test_only_polish_deltas_are_yielded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Director's deltas must NOT leak to the caller — only Polish
    streams to the user-visible chunk channel."""
    director_chunks = [{"delta": "DIRECTOR_PLACEHOLDER"}]
    polish_chunks = [{"delta": "REAL_CONTENT"}]
    stub = _StubStreamer([director_chunks, polish_chunks])
    monkeypatch.setattr(director_polish, "stream_chat_completion", stub)

    deltas: list[str] = []
    async for event in director_polish.director_polish_generate(
        base_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ],
        user_prompt="x",
        director_model="claude-opus-4-7",
        polish_model="claude-opus-4-7",
        user_id=uuid4(),
        project_id=uuid4(),
        message_id=uuid4(),
    ):
        if "delta" in event:
            deltas.append(event["delta"])

    assert deltas == ["REAL_CONTENT"]
    assert "DIRECTOR_PLACEHOLDER" not in deltas


@pytest.mark.asyncio
async def test_usage_is_summed_across_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    director_chunks = [
        {"delta": "x"},
        {"usage": {"tokens_in": 100, "tokens_out": 30, "cost_rub": 1.5}},
    ]
    polish_chunks = [
        {"delta": "y"},
        {"usage": {"tokens_in": 200, "tokens_out": 80, "cost_rub": 4.0}},
    ]
    stub = _StubStreamer([director_chunks, polish_chunks])
    monkeypatch.setattr(director_polish, "stream_chat_completion", stub)

    usage_events: list[dict[str, Any]] = []
    async for event in director_polish.director_polish_generate(
        base_messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        user_prompt="x",
        director_model="claude-opus-4-7",
        polish_model="claude-opus-4-7",
        user_id=uuid4(),
        project_id=uuid4(),
        message_id=uuid4(),
    ):
        if "usage" in event:
            usage_events.append(event["usage"])

    assert len(usage_events) == 1
    final = usage_events[0]
    assert final["tokens_in"] == 300
    assert final["tokens_out"] == 110
    assert final["cost_rub"] == pytest.approx(5.5)
    assert final["passes"] == 2


@pytest.mark.asyncio
async def test_director_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    director_chunks = [{"error": "boom"}]
    polish_chunks: list[dict[str, Any]] = []  # never reached
    stub = _StubStreamer([director_chunks, polish_chunks])
    monkeypatch.setattr(director_polish, "stream_chat_completion", stub)

    events: list[dict[str, Any]] = []
    async for event in director_polish.director_polish_generate(
        base_messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        user_prompt="x",
        director_model="claude-opus-4-7",
        polish_model="claude-opus-4-7",
        user_id=uuid4(),
        project_id=uuid4(),
        message_id=uuid4(),
    ):
        events.append(event)

    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1
    assert "director pass failed" in error_events[0]["error"]
    # Polish never called.
    assert stub.call_count == 1


@pytest.mark.asyncio
async def test_polish_messages_include_director_ir(monkeypatch: pytest.MonkeyPatch) -> None:
    director_chunks = [{"delta": '{"DIRECTOR":"IR"}'}]
    polish_chunks = [{"delta": "ok"}]
    stub = _StubStreamer([director_chunks, polish_chunks])
    monkeypatch.setattr(director_polish, "stream_chat_completion", stub)

    async for _ in director_polish.director_polish_generate(
        base_messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        user_prompt="промпт",
        director_model="claude-opus-4-7",
        polish_model="claude-opus-4-7",
        user_id=uuid4(),
        project_id=uuid4(),
        message_id=uuid4(),
    ):
        pass

    assert stub.call_count == 2
    polish_messages = stub.calls[1]
    polish_last_user = polish_messages[-1]
    # Director's IR must be embedded in the polish user turn.
    assert "DIRECTOR" in polish_last_user["content"]
    # Polish instruction sentinel must be present.
    assert "Polish: второй проход" in polish_last_user["content"]


@pytest.mark.asyncio
async def test_empty_director_output_yields_error(monkeypatch: pytest.MonkeyPatch) -> None:
    director_chunks: list[dict[str, Any]] = []  # truly empty stream
    polish_chunks: list[dict[str, Any]] = []
    stub = _StubStreamer([director_chunks, polish_chunks])
    monkeypatch.setattr(director_polish, "stream_chat_completion", stub)

    events: list[dict[str, Any]] = []
    async for event in director_polish.director_polish_generate(
        base_messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        user_prompt="x",
        director_model="claude-opus-4-7",
        polish_model="claude-opus-4-7",
        user_id=uuid4(),
        project_id=uuid4(),
        message_id=uuid4(),
    ):
        events.append(event)

    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1
    assert "empty" in error_events[0]["error"]
