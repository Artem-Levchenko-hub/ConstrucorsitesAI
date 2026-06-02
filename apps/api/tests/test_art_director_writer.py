"""Art-Director → Writer 2-pass freeform generator (owner directive 2026-06-01).

Self-contained: the gateway stream is faked and the async generator is drained
via ``asyncio.run`` so no pytest-asyncio config is required.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import omnia_api.services.art_director_writer as adw
from omnia_api.services.art_director_writer import (
    _build_art_director_messages,
    _build_writer_messages,
    art_director_writer_generate,
)

_BASE = [
    {"role": "system", "content": "SYS PROMPT (palette anchor + kit)"},
    {"role": "user", "content": "сделай лендинг кофейни"},
]


def _make_fake_stream(*, fail_brief: bool = False):
    """Fake gateway: brief deltas for the Opus (art-director) call, HTML for the
    DeepSeek (writer) call — told apart by 'opus' in the model id."""

    async def fake(msgs, model, *a, **k):
        # Tell the passes apart by the instruction marker, not the model id —
        # the art_director model is retunable (Opus → Gemini → …) and the writer
        # is too, so keying on a model substring is brittle. "проход 1 из 2"
        # appears only in the art-director directive.
        last = msgs[-1]["content"] if msgs else ""
        if "проход 1 из 2" in last:  # art-director pass
            if fail_brief:
                yield {"error": "boom"}
                return
            yield {"delta": "BRIEF-CONTENT"}
            yield {"usage": {"tokens_in": 10, "tokens_out": 5, "cost_rub": 0.1}}
        else:  # writer pass
            yield {"delta": "<html>PAGE</html>"}
            yield {"usage": {"tokens_in": 50, "tokens_out": 200, "cost_rub": 0.2}}

    return fake


async def _drain(gen):
    return [ev async for ev in gen]


def test_writer_messages_inject_brief() -> None:
    msgs = _build_writer_messages(_BASE, "PROMPT", "MY-BRIEF", "deepseek-chat")
    assert len(msgs) == 2  # system kept; only the last user turn is rewritten
    assert "MY-BRIEF" in msgs[-1]["content"]


def test_writer_messages_empty_brief_degrades_to_base() -> None:
    # R-10 fail-soft: no brief → the writer runs on the base prompt alone.
    msgs = _build_writer_messages(_BASE, "PROMPT", "", "deepseek-chat")
    assert msgs[-1]["content"].startswith("PROMPT")
    assert "БРИФ" not in msgs[-1]["content"]


def test_art_director_messages_carry_brief_directive() -> None:
    msgs = _build_art_director_messages(_BASE, "PROMPT", "claude-opus-4-7")
    assert "АРТ-ДИРЕКТОР" in msgs[-1]["content"]


def test_two_pass_only_writer_streams() -> None:
    # The brief (pass 1) is silent; only the writer's HTML (pass 2) reaches the
    # caller as deltas, and usage sums both passes.
    adw.stream_chat_completion = _make_fake_stream()
    events = asyncio.run(
        _drain(
            art_director_writer_generate(
                base_messages=_BASE,
                user_prompt="PROMPT",
                user_id=uuid4(),
                project_id=uuid4(),
                message_id=uuid4(),
            )
        )
    )
    deltas = [e["delta"] for e in events if "delta" in e]
    usage = [e["usage"] for e in events if "usage" in e]
    assert deltas == ["<html>PAGE</html>"]
    assert usage[-1]["passes"] == 2
    assert usage[-1]["tokens_out"] == 205


def test_brief_failure_is_fail_soft() -> None:
    # An art-director error must NOT abort the build — the writer carries the
    # page on the base prompt instead.
    adw.stream_chat_completion = _make_fake_stream(fail_brief=True)
    events = asyncio.run(
        _drain(
            art_director_writer_generate(
                base_messages=_BASE,
                user_prompt="PROMPT",
                user_id=uuid4(),
                project_id=uuid4(),
                message_id=uuid4(),
            )
        )
    )
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas == ["<html>PAGE</html>"]
    assert not any("error" in e for e in events)
