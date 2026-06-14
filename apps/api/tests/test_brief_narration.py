"""Tests for the baked brief-narration injector (v2.21 #1A — pillar 3+4).

The most-shared public surface (freeform static /p/<slug>) was born SILENT: a
colleague pasting the link saw a finished page, none of the "AI is designing
this" reveal that hooks the viral loop. `brief_narration` bakes the
art-director brief + a self-contained reveal into index.html at commit time so
the shared link plays the SAME birth narration for a stranger.

The narration LINES are the same copy as apps/web brief-narration.ts and the
template omnia-brief-narration.js — pinned here so the Russian copy can't drift.
"""

from __future__ import annotations

import json

from omnia_api.services.brief_narration import brief_lines, inject_brief_narration

_FULL_BRIEF = {
    "palette": {
        "Акцент": "#b45309",
        "Primary": "#1c1917",
        "Фон": "#fafaf9",
        "extra": "not-a-hex",
    },
    "fonts": {"display": "Playfair Display", "text": "Inter"},
    "motion": "плавное появление секций при скролле с лёгким параллаксом фона",
    "sections": [
        {"id": "hero", "name": "Геро"},
        {"id": "menu", "name": "Меню"},
        {"id": "about", "name": "О нас"},
        {"id": "gallery", "name": "Галерея"},
        {"id": "contact", "name": "Контакты"},
    ],
}


def test_brief_lines_carry_brief_values_in_order() -> None:
    """Each line literally carries a brief value, in the art-director's order:
    palette → font → sections → motion. This is the falsifiable proof the brief
    surfaced (a hardcoded list would not change with the brief)."""
    lines = brief_lines(_FULL_BRIEF)
    assert lines == [
        "Подбираю палитру — #b45309 и #1c1917",  # accent → primary, role-ordered, hex only
        "Беру шрифт «Playfair Display» для заголовков",
        "Компоную секции: Геро → Меню → О нас → Галерея …",  # 4 shown + " …"
        "Оживляю движением — плавное появление секций при скролле с лёгким…",  # short_motion cut on word boundary
    ]


def test_empty_or_null_brief_yields_no_lines() -> None:
    assert brief_lines(None) == []
    assert brief_lines({}) == []
    assert brief_lines({"palette": {"x": "nope"}, "sections": []}) == []


def test_text_font_fallback_when_no_display() -> None:
    lines = brief_lines({"fonts": {"text": "Inter"}})
    assert lines == ["Беру шрифт «Inter» для текста"]


def test_inject_adds_payload_and_reveal_before_head_close() -> None:
    html = "<!doctype html><html><head><title>X</title></head><body>hi</body></html>"
    out = inject_brief_narration(html, _FULL_BRIEF)
    # Brief baked as JSON onto window.__omniaBrief, before </head>.
    assert "window.__omniaBrief=" in out
    assert '<script id="omnia-brief-narration">' in out
    head_close = out.index("</head>")
    assert out.index("window.__omniaBrief=") < head_close
    # The baked payload round-trips back to the original brief.
    start = out.index("window.__omniaBrief=") + len("window.__omniaBrief=")
    end = out.index(";</script>", start)
    assert json.loads(out[start:end]) == _FULL_BRIEF
    # Original markup preserved.
    assert "<title>X</title>" in out and "hi" in out


def test_inject_is_idempotent() -> None:
    html = "<html><head></head><body></body></html>"
    once = inject_brief_narration(html, _FULL_BRIEF)
    twice = inject_brief_narration(once, _FULL_BRIEF)
    assert once == twice
    assert once.count('id="omnia-brief-narration"') == 1


def test_inject_noop_on_empty_brief() -> None:
    html = "<html><head></head><body></body></html>"
    assert inject_brief_narration(html, None) == html
    assert inject_brief_narration(html, {}) == html
    # A brief with no narratable fields ships the page silent.
    assert inject_brief_narration(html, {"palette": {"x": "bad"}}) == html


def test_inject_escapes_script_terminator_in_payload() -> None:
    """A section name containing '</script>' must not terminate the tag early."""
    brief = {"sections": [{"id": "s", "name": "a</script>b"}]}
    out = inject_brief_narration(html := "<head></head>", brief)
    assert out != html  # was injected (the section line is non-empty)
    assert "</script>b" not in out.split("window.__omniaBrief=")[1].split(";</script>")[0]
    assert "<\\/script>" in out


def test_inject_appends_when_no_head_or_body() -> None:
    out = inject_brief_narration("<div>bare</div>", _FULL_BRIEF)
    assert out.startswith("<div>bare</div>")
    assert "window.__omniaBrief=" in out
