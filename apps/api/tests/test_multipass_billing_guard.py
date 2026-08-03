from __future__ import annotations

import asyncio
from uuid import uuid4

from omnia_api.services import multipass_generator as multipass


async def _drain(generator):
    return [event async for event in generator]


def test_ambiguous_parallel_pass_never_starts_paid_assembly() -> None:
    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        call = calls
        if call == 2:
            yield {
                "error": "response lost",
                "error_code": "paid_call_ambiguous",
            }
            return
        yield {"delta": "{}"}
        yield {"usage": {"tokens_in": 1, "tokens_out": 1, "cost_rub": 0.01}}

    original = multipass.stream_chat_completion
    multipass.stream_chat_completion = fake_stream
    try:
        events = asyncio.run(
            _drain(
                multipass.multipass_generate(
                    base_messages=[
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "build"},
                    ],
                    user_prompt="build",
                    user_id=uuid4(),
                    project_id=uuid4(),
                    message_id=uuid4(),
                )
            )
        )
    finally:
        multipass.stream_chat_completion = original

    assert calls == 3  # skeleton + the already-parallel content/visual pair
    assert not any(event.get("pass") == "assembly" for event in events)
    assert events[-1]["error_code"] == "paid_call_ambiguous"
