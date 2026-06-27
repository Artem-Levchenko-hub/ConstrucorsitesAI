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
    for key in ("Вайб:", "Холст:", "Акцент", "Скругления:", "Плотность:", "Шрифты:"):
        assert key in d
    # A concrete hex colour from the curated palette is present.
    assert "#" in d
