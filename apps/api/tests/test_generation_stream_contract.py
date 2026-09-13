"""Actual model attempts preserve replay state across a failed pass and fallback."""

from types import SimpleNamespace
from uuid import uuid4

from omnia_api.services.generation import stream_attempt
from omnia_api.services.generation.contracts import GenerationIds, ProjectGenerationFacts
from omnia_api.services.generation.progress import MessageStream


async def test_model_attempt_fallback_preserves_message_sequence_and_replay(monkeypatch):
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    facts = ProjectGenerationFacts(
        template="code",
        slug="stream-fixture",
        name="Stream fixture",
        design_preset_id=None,
        discovery_spec=None,
        image_gen_enabled=False,
        language="en",
        is_imported=False,
        memory_context="",
        restoration_context="",
    )
    stream = stream_attempt.ModelStream(
        ids=ids,
        project_info=facts,
        prompt_text="Change calculation",
        model_id="fixture-primary",
        force_model=None,
        orchestrate=False,
        generation_mode="freeform",
        messages=[{"role": "user", "content": "Change calculation"}],
        multipass_set=frozenset(),
        pub=MessageStream(seq=7, content="earlier:"),
    )
    calls, published, replay = [], [], []

    async def provider(_messages, model, *_ids):
        calls.append(model)
        assert stream.last_attempt.text == ""
        assert stream.last_attempt.usage is stream.last_attempt.error is None
        if model == "fixture-primary":
            yield {"delta": "A"}
            yield {"usage": {"input_tokens": 9}}
            yield {"error": "synthetic retryable error"}
            raise AssertionError("failed attempt must stop consuming after its error")
        assert model == "fixture-fallback"
        yield {"delta": "B"}
        yield {"delta": "C"}
        yield {"usage": {"input_tokens": 4, "output_tokens": 2}}

    async def publish(project_id, event_type, payload):
        assert project_id == ids.project_id and event_type == "llm.chunk"
        published.append(payload)

    async def save_replay(project_id, message_id, content, seq):
        assert project_id == ids.project_id and message_id == ids.assistant_message_id
        replay.append((content, seq))
        if seq == 8:
            raise RuntimeError("synthetic replay store outage")

    monkeypatch.setattr(
        stream_attempt,
        "get_settings",
        lambda: SimpleNamespace(
            use_section_catalog=False,
        ),
    )
    monkeypatch.setattr(stream_attempt, "stream_chat_completion", provider)
    monkeypatch.setattr(stream_attempt, "publish_event", publish)
    monkeypatch.setattr(stream_attempt, "set_stream_state", save_replay)
    await stream.run("fixture-primary", force_single_shot=True)
    assert stream.last_attempt.text == "A"
    assert stream.last_attempt.error == "synthetic retryable error"
    assert stream.last_attempt.usage == {"input_tokens": 9}
    await stream.run("fixture-fallback", force_single_shot=True)
    assert calls == ["fixture-primary", "fixture-fallback"]
    assert stream.last_attempt.text == "BC" and stream.last_attempt.error is None
    assert stream.last_attempt.usage == {"input_tokens": 4, "output_tokens": 2}
    assert stream.pub.seq == 10 and stream.pub.content == "earlier:ABC"
    assert replay == [("earlier:A", 8), ("earlier:AB", 9), ("earlier:ABC", 10)]
    assert published == [
        {"message_id": str(ids.assistant_message_id), "delta": delta, "seq": seq}
        for delta, seq in (("A", 8), ("B", 9), ("C", 10))
    ]
