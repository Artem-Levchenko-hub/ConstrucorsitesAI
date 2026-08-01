"""Vision-audit parsing + fail-soft behaviour (Phase 11, Sprint 2.1)."""

from omnia_api.services import vision_audit
from omnia_api.services.vision_audit import _parse


def test_parse_clean_json():
    v = _parse('{"verdict":"beautiful","score":9,"issues":[]}')
    assert v.verdict == "beautiful"
    assert v.score == 9
    assert v.issues == ()
    assert v.skipped is False


def test_parse_fenced_with_leading_prose():
    raw = (
        "Вот моя оценка:\n```json\n"
        '{"verdict":"generic","score":5,"issues":["мало контраста в hero"]}\n```'
    )
    v = _parse(raw)
    assert v.verdict == "generic"
    assert v.score == 5
    assert "мало контраста в hero" in v.issues


def test_parse_garbage_is_skipped():
    assert _parse("totally not json").skipped is True


def test_score_is_clamped():
    assert _parse('{"verdict":"broken","score":99}').score == 10
    assert _parse('{"verdict":"broken","score":-4}').score == 0


def test_unknown_verdict_defaults_to_generic():
    assert _parse('{"verdict":"meh","score":4}').verdict == "generic"


async def test_audit_skips_in_mock(monkeypatch):
    monkeypatch.setattr(vision_audit, "get_settings", lambda: type("S", (), {"mock_llm": True})())
    v = await vision_audit.audit_screenshots({1440: b"PNG"}, prompt_context="x")
    assert v.skipped is True


async def test_audit_parses_real_verdict(monkeypatch):
    captured = {}

    async def fake_complete(messages, model, **kw):
        captured["content"] = messages[1]["content"]
        return '{"verdict":"beautiful","score":8,"issues":[]}'

    monkeypatch.setattr(vision_audit, "get_settings", lambda: type("S", (), {"mock_llm": False})())
    monkeypatch.setattr(vision_audit, "complete_chat", fake_complete)
    v = await vision_audit.audit_screenshots(
        {1440: b"PNGWIDE", 375: b"PNGMOBILE"},
        prompt_context="лендинг кофейни",
        model="claude-sonnet-4-6",
    )
    assert v.verdict == "beautiful"
    assert v.score == 8
    # The user content must be a multimodal block list with at least one image.
    content = captured["content"]
    assert isinstance(content, list)
    assert any(b.get("type") == "image_url" for b in content)


async def test_max_audit_uses_mobile_product_rubric_and_phone_widths(monkeypatch):
    captured = {}

    async def fake_complete(messages, model, **kw):
        captured["system"] = messages[0]["content"]
        captured["content"] = messages[1]["content"]
        return '{"verdict":"beautiful","score":9,"issues":[]}'

    monkeypatch.setattr(vision_audit, "get_settings", lambda: type("S", (), {"mock_llm": False})())
    monkeypatch.setattr(vision_audit, "complete_chat", fake_complete)

    verdict = await vision_audit.audit_screenshots(
        {1440: b"DESKTOP", 390: b"PHONE", 360: b"COMPACT"},
        prompt_context="фитнес-тренер внутри MAX",
        product_kind="max_miniapp",
        model="gemini-test",
    )

    assert verdict.verdict == "beautiful"
    assert "MAX Mini Apps" in captured["system"]
    assert "safe-area" in captured["system"]
    assert "лендинг" in captured["system"]  # explicit anti-landing rule
    assert "Awwwards" not in captured["system"]
    text = "\n".join(
        str(block.get("text", "")) for block in captured["content"] if block.get("type") == "text"
    )
    assert "390px" in text and "360px" in text
    assert "1440px" not in text
    assert sum(block.get("type") == "image_url" for block in captured["content"]) == 2
