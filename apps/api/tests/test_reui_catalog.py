"""Pinned ReUI metadata and bounded composition retrieval."""

from omnia_api.services.reui_catalog import (
    REUI_COMMIT,
    REUI_LICENSE,
    format_reui_prompt,
    load_reui_catalog,
    select_reui_patterns,
)


def test_snapshot_contains_full_pinned_reui_metadata_catalog() -> None:
    patterns = load_reui_catalog()

    assert len(patterns) == 1072
    assert len({pattern.id for pattern in patterns}) == len(patterns)
    assert all(REUI_COMMIT in pattern.source_url for pattern in patterns)
    assert REUI_LICENSE == "MIT"


def test_fitness_retrieval_prefers_stat_history_and_profile_compositions() -> None:
    patterns = select_reui_patterns(
        brief="Фитнес: статистика тренировок, история действий и профиль спортсмена",
        archetype="fitness-health",
        mobile=True,
    )

    assert tuple(pattern.id for pattern in patterns) == (
        "card/c-card-15",
        "timeline/c-timeline-11",
        "sheet/c-sheet-1",
    )


def test_mobile_table_keeps_reference_but_requires_stacked_rows() -> None:
    patterns = select_reui_patterns(
        brief="CRM: таблица заявок, фильтры и история статусов",
        archetype="operations",
        mobile=True,
    )
    prompt = format_reui_prompt(patterns, mobile=True)

    assert "data-grid/c-data-grid-22" in tuple(pattern.id for pattern in patterns)
    assert "turn wide rows into stacked items" in prompt


def test_retrieval_is_bounded_deterministic_and_never_installs_reui() -> None:
    kwargs = {
        "brief": "Запись к врачу: календарь, форма, профиль, фильтры и пустой список",
        "archetype": "booking-service",
        "mobile": True,
    }
    first = select_reui_patterns(**kwargs)
    second = select_reui_patterns(**kwargs)
    prompt = format_reui_prompt(first, mobile=True)

    assert first == second
    assert 1 <= len(first) <= 3
    assert "DO NOT run shadcn/ReUI CLI" in prompt
    assert "install packages" in prompt
    assert "npx " not in prompt
    assert "npm install" not in prompt
    assert len(prompt) < 2200
