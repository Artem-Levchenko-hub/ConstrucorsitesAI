"""Public builder payloads frozen before removing the unused catalog builder."""

import hashlib
import json
from pathlib import Path

import pytest

from omnia_api.core import config
from omnia_api.services import prompt_builder

FIXTURE = Path(__file__).with_name("fixtures") / "prompt_routing_baseline.json"
TEMPLATES = ("blank", "landing", "code", "tgbot", "api", "max_miniapp")
MODES = ("catalog", "freeform", "plain", "edit")


def build_payload(monkeypatch, mode: str, template: str, language: str):
    monkeypatch.setattr(config, "generation_mode", lambda *_args: mode)
    monkeypatch.setattr(prompt_builder, "_compute_skill_brief", lambda *_args: None)
    return prompt_builder.build_messages(
        current_files={"index.html": "<h1>Сохранённый текст / Existing text</h1>"},
        history=[
            {"role": "user", "content": "Сделай сайт / Build a website"},
            {"role": "assistant", "content": "Предыдущий ответ / Previous response"},
        ],
        user_prompt="Добавь расписание / Add a schedule",
        template=template,
        selected_elements=[{"selector": "h1", "text": "Сохранённый текст"}],
        model_id="claude-opus-4-7",
        edit_mode=mode == "edit",
        language=language,
        project_memory_context="Keep the existing schedule.",
    )


def payload_digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("language", ("ru", "en"))
def test_prompt_routing_preserves_complete_payload(monkeypatch, mode, template, language):
    expected = json.loads(FIXTURE.read_text("utf-8"))
    payload = build_payload(monkeypatch, mode, template, language)
    assert payload_digest(payload) == expected[f"{mode}/{template}/{language}"]
    assert payload[0]["role"] == "system"
    assert payload[-1]["role"] == "user"
    assert "Добавь расписание / Add a schedule" in payload[-1]["content"]
