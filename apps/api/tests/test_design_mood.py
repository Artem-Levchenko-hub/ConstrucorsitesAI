"""Per-project design mood: every container app must look UNIQUE.

Owner «дизайн минимализма всегда одинаковый» — container apps got only an
accent+radius override (inert on the hardcoded realtime template). The mood
directive feeds a full seeded curated palette + font + density into the agent's
build prompt so each app is built in a distinct look.
"""

from __future__ import annotations

from omnia_api.services.design_dna import design_mood_directive


def test_mood_is_stable_per_project() -> None:
    assert design_mood_directive("proj-a") == design_mood_directive("proj-a")


def test_mood_varies_across_projects() -> None:
    moods = {design_mood_directive(f"proj-{i}") for i in range(16)}
    # Distinct curated moods per project — not one repeated minimalist look.
    assert len(moods) >= 8


def test_mood_carries_palette_font_and_density() -> None:
    d = design_mood_directive("proj-x")
    for key in (
        "Вайб:",
        "Токены холста",
        "Токены акцента",
        "Скругления:",
        "Плотность:",
        "Шрифты:",
    ):
        assert key in d
    # A concrete hex colour from the curated palette is present.
    assert "#" in d


def test_mood_steers_tokens_not_hardcoded_hex() -> None:
    # The directive must tell the agent to EDIT token VALUES and use tokens in
    # components — never hardcode hex / inline styles (the 2026-07-09 regression).
    d = design_mood_directive("proj-x")
    assert "globals.css" in d
    assert "--primary" in d and "--background" in d
    assert "bg-[#hex]" in d  # the explicit prohibition
    assert "bg-primary" in d


def test_mood_honors_dark_brief() -> None:
    d = design_mood_directive("proj-x", industry_hint="Сделай премиум ТЁМНЫЙ мессенджер")
    assert "Тема: ТЁМНАЯ" in d
    # A dark background token value, not a seeded light one.
    assert "--background: #0f1115" in d


def test_mood_honors_light_brief() -> None:
    d = design_mood_directive("proj-x", industry_hint="светлый минималистичный дашборд")
    assert "Тема: СВЕТЛАЯ" in d
    assert "--background: #ffffff" in d


def test_mood_no_cue_keeps_seeded_theme() -> None:
    # No explicit dark/light word → no forced theme line, seeded palette stands.
    d = design_mood_directive("proj-x", industry_hint="приложение для заявок")
    assert "Тема: ТЁМНАЯ" not in d
    assert "Тема: СВЕТЛАЯ" not in d
