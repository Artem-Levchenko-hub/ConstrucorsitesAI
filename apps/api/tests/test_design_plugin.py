"""Omnia Design Pro: bounded brief → author/auditor contract."""

from omnia_api.services.design_plugin import (
    KNOWLEDGE_SOURCE,
    PLUGIN_ID,
    PLUGIN_VERSION,
    build_design_contract,
    seed_design_memory,
)
from omnia_api.services.design_presets import PRESETS


def _contract(brief: str, **kwargs: str):
    result = build_design_contract(
        project_id="project-1",
        project_name="Test",
        template=kwargs.pop("template", "max_miniapp"),
        brief=brief,
        preset_id=kwargs.pop("preset_id", None),
    )
    assert result is not None
    return result


def test_fitness_max_gets_product_ux_and_mobile_contract() -> None:
    result = _contract("Фитнес: статистика и разбор тренировок, шаблоны для спортсмена")

    assert result.plugin_id == PLUGIN_ID
    assert result.version == PLUGIN_VERSION
    assert result.knowledge_source == KNOWLEDGE_SOURCE
    assert "keenthemes/reui@0daf79dff3ebe0ede7fa05bedcaefeaac93a8949" in result.knowledge_source
    assert result.archetype == "fitness-health"
    assert result.preset_id == "wellness-casual"
    assert "данные тренировки → понятный анализ" in result.prompt_block
    assert "3–5 нижних вкладок" in result.prompt_block
    assert "safe-area" in result.prompt_block
    assert "ui-ux-pro-max@2.13.0+8a1a6d85" in result.prompt_block
    assert "UX RULES" in result.prompt_block
    assert "nav_style:        bottom-tabs (mobile primary)" in result.prompt_block
    assert "CHARTS" in result.prompt_block
    assert "lucide-react only" in result.prompt_block
    assert "do not import an uninstalled chart package" in result.prompt_block
    assert result.reui_pattern_ids == (
        "card/c-card-15",
        "chart/c-chart-13",
        "progress/c-progress-4",
    )
    assert "REUI COMPOSITION REFERENCES" in result.prompt_block
    assert "DO NOT run shadcn/ReUI CLI" in result.prompt_block
    assert "install packages" in result.prompt_block
    assert "@phosphor-icons" not in result.prompt_block
    assert "--app-brand:" in result.prompt_block
    assert "не добавляй\n  `@theme`" in result.prompt_block
    assert "ДИЗАЙН-НАСТРОЕНИЕ ЭТОГО ПРОЕКТА" not in result.prompt_block


def test_habit_catalog_stays_wellness_instead_of_becoming_commerce() -> None:
    result = _contract("Трекер ежедневных привычек: экран Сегодня, статистика и каталог привычек")

    assert result.archetype == "fitness-health"
    assert "сводка прогресса → тренировки/планы" in result.design_markdown
    assert "корзина" not in result.design_markdown


def test_max_contract_persists_secret_free_design_memory() -> None:
    result = _contract("Фитнес-тренер, ключ sk-test-secret")

    assert result.design_markdown.startswith("# Product design contract")
    assert f"`{PLUGIN_ID}` `{PLUGIN_VERSION}`" in result.design_markdown
    assert "--app-bg" in result.design_markdown
    assert "@maxhub/max-ui@0.2.0" in result.design_markdown
    assert "## ReUI composition references" in result.design_markdown
    assert "keenthemes/reui@0daf79dff3ebe0ede7fa05bedcaefeaac93a8949" in result.design_markdown
    assert "sk-test-secret" not in result.design_markdown

    seeded = seed_design_memory({"package.json": "{}"}, result)
    assert seeded["DESIGN.md"] == result.design_markdown
    assert seeded["package.json"] == "{}"


def test_contract_is_single_pass_and_carries_quality_floor() -> None:
    result = _contract("CRM для заявок и задач", template="fullstack")
    block = result.prompt_block

    assert "не создавай\nотдельный дизайн-этап" in block
    assert "loading, empty, error, success" in block
    assert "touch target ≥44px" in block
    assert "без одинаковой сетки из карточек" in block
    assert "ДИЗАЙН-НАСТРОЕНИЕ ЭТОГО ПРОЕКТА" in block
    assert PRESETS[result.preset_id].one_liner in block
    assert "постоянный `DESIGN.md`" not in block


def test_marketing_landing_pattern_is_not_injected_into_product() -> None:
    block = _contract("Мобильное фитнес приложение со статистикой").prompt_block
    assert "LANDING PATTERN" not in block
    assert "App Store Style Landing" not in block


def test_explicit_valid_preset_wins() -> None:
    result = _contract("Рабочее приложение", preset_id="festival-brutalist")
    assert result.preset_id == "festival-brutalist"
    assert "Festival Brutalist" in result.prompt_block


def test_vision_context_is_bounded_and_design_first() -> None:
    result = _contract("Очень длинный запрос " * 100)
    assert len(result.vision_context) <= 300
    assert result.vision_context.startswith(f"{PLUGIN_ID} {PLUGIN_VERSION}")
    assert "IA:" in result.vision_context


def test_non_ui_stack_opts_out() -> None:
    assert (
        build_design_contract(
            project_id="p",
            project_name="API",
            template="api",
            brief="REST API",
        )
        is None
    )


def test_contract_is_deterministic() -> None:
    first = _contract("Сервис бронирования консультаций")
    second = _contract("Сервис бронирования консультаций")
    assert first == second
