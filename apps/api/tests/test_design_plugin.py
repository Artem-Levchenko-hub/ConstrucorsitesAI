"""Owner-led MAX guidance with compatible legacy design memory."""

import json
from dataclasses import asdict, fields

import pytest
from pydantic import TypeAdapter

from yleum_api.services import design_plugin
from yleum_api.services.design_plugin import (
    KNOWLEDGE_SOURCE,
    PLUGIN_ID,
    PLUGIN_VERSION,
    DesignContract,
    build_design_contract,
    classify_product_archetype,
    seed_design_memory,
)
from yleum_api.services.design_presets import PRESETS
from yleum_api.services.typography import TypographyDecision, existing_typography_context


def _contract(brief: str, **kwargs: str):
    result = build_design_contract(
        project_id=kwargs.pop("project_id", "project-1"),
        project_name="Test",
        template=kwargs.pop("template", "max_miniapp"),
        brief=brief,
        preset_id=kwargs.pop("preset_id", None),
    )
    assert result is not None
    return result


@pytest.mark.parametrize("brief", [
    "Фитнес: статистика и разбор тренировок, шаблоны для спортсмена",
    "Ассистент нутрициолог. Не магазин, каталог или заказы",
    "Сервис бронирования консультаций",
])
def test_max_guidance_does_not_choose_the_product_structure(brief: str) -> None:
    result = _contract(brief)
    assert result.plugin_id == PLUGIN_ID
    assert result.version == PLUGIN_VERSION
    assert result.knowledge_source == KNOWLEDGE_SOURCE
    assert result.archetype == "owner-led"
    assert result.preset_id == ""
    for surface in (result.prompt_block, result.design_markdown, result.vision_context):
        for automatic in (
            "3–5", "bottom-tabs", "Information architecture:", "Главный путь:",
            "Retail Product", "корзина", "--app-bg:", "--app-brand:",
        ):
            assert automatic not in surface
    assert "задачи, аудитории" in result.prompt_block
    assert "loading, empty, error, success" in result.prompt_block
    assert "touch target ≥44px" in result.prompt_block
    assert "safe-area" in result.prompt_block
    assert "MaxAppProvider" in result.prompt_block
    assert len(result.prompt_block) < 3000


def test_max_branch_never_calls_inferred_design_advisers(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("MAX must not infer a preset, domain flow or seeded design")

    for name in (
        "_pick_pattern", "_pick_preset", "_matched_skill_brief",
        "classify_preset_sync", "design_mood_directive",
    ):
        monkeypatch.setattr(design_plugin, name, unexpected)
    assert _contract("Каталог тренировок и статистика").archetype == "owner-led"


def test_metadata_classifier_remains_available_to_advisors() -> None:
    assert classify_product_archetype("Трекер привычек для спортсмена") == "fitness-health"
    assert classify_product_archetype("Магазин товаров и корзина") == "commerce"


def test_max_keeps_supplied_preset_only_as_inert_metadata() -> None:
    ordinary = _contract("Рабочее приложение")
    supplied = _contract("Рабочее приложение", preset_id="festival-brutalist")
    assert supplied.preset_id == "festival-brutalist"
    assert supplied.prompt_block == ordinary.prompt_block
    assert supplied.design_markdown == ordinary.design_markdown
    assert supplied.vision_context == ordinary.vision_context
    assert "Festival Brutalist" not in supplied.prompt_block


def test_owner_visual_request_reaches_typography_unchanged_without_inference(monkeypatch) -> None:
    brief = "Редакционный стиль: Lora и Onest, один экран без вкладок, без фиолетового"
    calls = []

    def choose(project_id, *, brief, preset_id, infer_defaults):
        calls.append((project_id, brief, preset_id, infer_defaults))
        return TypographyDecision(1, "editorial", "Lora", "Onest", ("lora", "onest"))

    monkeypatch.setattr(design_plugin, "choose_typography", choose)
    result = _contract(brief, preset_id="retail-product")
    assert calls == [("project-1", brief, None, False)]
    assert "heading — Lora; body — Onest" in result.prompt_block
    assert "Полный запрос владельца" in result.prompt_block
    assert "явно выбранное им оформление имеют приоритет" in result.prompt_block
    assert "сохрани сложившийся дизайн, функции и данные" in result.prompt_block
    assert "без hero" not in result.prompt_block


def test_default_max_fonts_do_not_depend_on_project_or_domain() -> None:
    first = _contract("Ассистент нутрициолога", project_id="project-1")
    second = _contract("Каталог кофейни", project_id="other-project")
    assert first.prompt_block == second.prompt_block
    assert first.design_markdown == second.design_markdown


def test_max_persistent_memory_never_copies_raw_owner_text() -> None:
    raw = 'Шрифт «sk-test-secret». Внутренняя заметка OWNER_PRIVATE_SENTINEL'
    result = _contract(raw)
    assert result.design_markdown.startswith("# Product design contract")
    assert f"`{PLUGIN_ID}` `{PLUGIN_VERSION}`" in result.design_markdown
    for surface in (result.design_markdown, result.vision_context):
        assert "sk-test-secret" not in surface
        assert "OWNER_PRIVATE_SENTINEL" not in surface
        assert raw not in surface
    assert "НЕ установлен" in result.prompt_block
    decision = result.design_markdown.split("<!-- typography-decision-v1\n", 1)[1]
    assert json.loads(decision.split("\n-->", 1)[0])["custom_request"] is None


def test_max_seed_has_typography_and_survives_existing_continuation_stripping() -> None:
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files

    result = _contract("Сервис консультаций")
    starter = render_max_starter_files(
        MaxProjectConfigPayload(app_name="Test", app_type="custom", summary="Test"),
    )
    seeded = seed_design_memory(starter, result)
    assert seeded["DESIGN.md"] == result.design_markdown
    assert "typography.json" in seeded and "src/app/typography.css" in seeded
    context = existing_typography_context("existing seed\n\n" + result.prompt_block, seeded)
    assert "OMNIA DESIGN PRO" not in context
    assert "existing seed" in context and "Прочитай typography.json, DESIGN.md" in context


@pytest.mark.parametrize("existing", [
    {"DESIGN.md": "owner-maintained design"},
    {"typography.json": "legacy font choice"},
])
def test_seed_never_replaces_existing_design_memory(existing) -> None:
    assert seed_design_memory(existing, _contract("Новый запрос")) is existing
    assert seed_design_memory(existing, None) is existing


def test_legacy_contract_wire_and_font_seed_remain_readable() -> None:
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files

    typography = TypographyDecision(1, "balanced", "Onest", "Onest", ("onest",))
    memory = "Old owner design\n<!-- typography-decision-v1\n" + json.dumps(
        asdict(typography),
    ) + "\n-->\n"
    old = DesignContract(
        PLUGIN_ID, "1.1.1", KNOWLEDGE_SOURCE, "commerce", "retail-product",
        "legacy prompt", "legacy metadata", memory,
    )
    restored = TypeAdapter(DesignContract).validate_json(json.dumps(asdict(old)), strict=True)
    assert restored == old
    assert {field.name for field in fields(DesignContract)} == {
        "plugin_id", "version", "knowledge_source", "archetype", "preset_id",
        "prompt_block", "vision_context", "design_markdown",
    }
    starter = render_max_starter_files(
        MaxProjectConfigPayload(app_name="Legacy", app_type="custom", summary="Legacy"),
    )
    seeded = seed_design_memory(starter, restored)
    assert seeded["DESIGN.md"] == memory
    assert json.loads(seeded["typography.json"])["heading"] == "Onest"


@pytest.mark.parametrize("template", ["fullstack", "nextjs_entities", "spa", "realtime"])
def test_non_max_legacy_contract_keeps_direction_and_product_flow(template: str) -> None:
    result = _contract("CRM для заявок и задач", template=template)
    block = result.prompt_block
    assert "не создавай\nотдельный дизайн-этап" in block
    assert "loading, empty, error, success" in block
    assert "touch target ≥44px" in block
    assert "ДИЗАЙН-НАСТРОЕНИЕ ЭТОГО ПРОЕКТА" in block
    assert PRESETS[result.preset_id].one_liner in block
    assert "Information architecture:" in result.design_markdown


def test_non_max_explicit_valid_preset_wins() -> None:
    result = _contract("Рабочее приложение", template="fullstack", preset_id="festival-brutalist")
    assert result.preset_id == "festival-brutalist"
    assert "Festival Brutalist" in result.prompt_block


def test_max_vision_metadata_is_bounded_and_contains_no_request() -> None:
    result = _contract("OWNER_PRIVATE_SENTINEL " * 100)
    assert len(result.vision_context) <= 300
    assert result.vision_context.startswith(f"{PLUGIN_ID} {PLUGIN_VERSION}")
    assert "OWNER_PRIVATE_SENTINEL" not in result.vision_context
    assert "IA:" not in result.vision_context


def test_non_ui_stack_opts_out() -> None:
    assert build_design_contract(
        project_id="p", project_name="API", template="api", brief="REST API",
    ) is None


def test_api_only_max_request_adds_no_design_or_font_seed(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("API-only work must not select UI typography")

    monkeypatch.setattr(design_plugin, "choose_typography", unexpected)
    assert build_design_contract(
        project_id="p", project_name="API", template="max_miniapp",
        brief="API only: implement GET /api/health. No UI changes.",
    ) is None


def test_contract_is_deterministic() -> None:
    first = _contract("Сервис бронирования консультаций")
    assert first == _contract("Сервис бронирования консультаций")
