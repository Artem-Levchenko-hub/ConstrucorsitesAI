from types import SimpleNamespace
from uuid import uuid4

from omnia_api.services import hero_media_planner


def _valid_plan(*, recommended_focus: str = "interface") -> str:
    raw = """
    {
      "plan_kind": "product-demo",
      "confidence": 0.91,
      "explanation": "Интерфейс лучше объясняет пользу продукта, чем рекламное видео.",
      "recommended_focus": "__RECOMMENDED_FOCUS__",
      "recommended_tone": "спокойный",
      "brand_fit_note": "Чистая продуктовая подача соответствует B2B-аудитории.",
      "performance_note": "Лёгкая демонстрация интерфейса быстро загружается.",
      "accessibility_note": "Статичные состояния доступны без движения.",
      "requires_confirmation": true,
      "hero_headline": "Все диалоги команды в одном окне",
      "hero_subheadline": "Покажите продукт сразу, без лишнего рекламного ролика.",
      "primary_cta_label": "Попробовать",
      "visual_style": "clean product-led interface composition",
      "storyboard": [
        {
          "label": "hero-interface",
          "purpose": "Показать ключевой сценарий продукта",
          "primary_asset_index": 0,
          "secondary_asset_index": null,
          "use_source_as_poster": true,
          "still_prompt": null,
          "motion_prompt": null,
          "first_frame_prompt": null,
          "last_frame_prompt": null
        }
      ]
    }
    """
    return raw.replace("__RECOMMENDED_FOCUS__", recommended_focus)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        hero_media_stub_mode=False,
        mock_llm=False,
        use_video_gen=True,
        hero_media_max_assets=3,
    )


async def test_planner_sends_structured_output_system_prompt(monkeypatch) -> None:
    calls: list[list[dict[str, object]]] = []

    async def fake_complete_chat(messages, model, **kwargs):
        calls.append(messages)
        return _valid_plan()

    monkeypatch.setattr(hero_media_planner, "get_settings", _settings)
    monkeypatch.setattr(hero_media_planner, "model_for_role", lambda role: "test-model")
    monkeypatch.setattr(hero_media_planner, "complete_chat", fake_complete_chat)

    decision = await hero_media_planner.plan_hero_media(
        owner_id=uuid4(),
        project_id=uuid4(),
        prompt="B2B messenger for support teams",
        business_type="SaaS",
        style_preference="clean",
        focus_preference="interface",
        motion_preference="calm",
        asset_urls=["https://cdn.example.com/interface.png"],
    )

    assert decision.plan_kind == "product-demo"
    assert calls[0][0]["role"] == "system"
    assert "Верни СТРОГО JSON" in str(calls[0][0]["content"])


async def test_planner_repairs_schema_invalid_json_with_system_prompt(monkeypatch) -> None:
    calls: list[list[dict[str, object]]] = []

    async def fake_complete_chat(messages, model, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '{"plan_kind":"product-demo"}'
        return _valid_plan()

    monkeypatch.setattr(hero_media_planner, "get_settings", _settings)
    monkeypatch.setattr(hero_media_planner, "model_for_role", lambda role: "test-model")
    monkeypatch.setattr(hero_media_planner, "complete_chat", fake_complete_chat)

    decision = await hero_media_planner.plan_hero_media(
        owner_id=uuid4(),
        project_id=uuid4(),
        prompt="B2B messenger for support teams",
        business_type="SaaS",
        style_preference="clean",
        focus_preference="interface",
        motion_preference="calm",
        asset_urls=[],
    )

    assert decision.plan_kind == "product-demo"
    assert len(calls) == 2
    assert calls[1][0]["role"] == "system"
    assert "СТРОГИЙ JSON" in str(calls[1][0]["content"])


async def test_planner_normalizes_overlong_presentational_fields(monkeypatch) -> None:
    calls: list[list[dict[str, object]]] = []

    async def fake_complete_chat(messages, model, **kwargs):
        calls.append(messages)
        return _valid_plan(recommended_focus="Реальный интерфейс поддержки " * 8)

    monkeypatch.setattr(hero_media_planner, "get_settings", _settings)
    monkeypatch.setattr(hero_media_planner, "model_for_role", lambda role: "test-model")
    monkeypatch.setattr(hero_media_planner, "complete_chat", fake_complete_chat)

    decision = await hero_media_planner.plan_hero_media(
        owner_id=uuid4(),
        project_id=uuid4(),
        prompt="B2B messenger for support teams",
        business_type="SaaS",
        style_preference="clean",
        focus_preference="interface",
        motion_preference="calm",
        asset_urls=[],
    )

    assert decision.plan_kind == "product-demo"
    assert len(decision.recommended_focus) <= 80
    assert len(calls) == 1
