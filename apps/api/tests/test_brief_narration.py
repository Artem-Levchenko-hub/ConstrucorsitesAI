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

from omnia_api.services.brief_narration import (
    BRIEF_MODULE_PATH,
    brief_lines,
    build_brief_payload,
    inject_brief_module,
    inject_brief_narration,
)

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


# ── Baked brief module (v2.21 #1A) — the ENTITY/container analogue ──────────
# A container project's /p/<slug> 302-redirects to the LIVE app on another
# origin, so the brief can't be injected into served HTML; it has to ride into
# the app's own source as src/app/omnia-brief.ts (like share_meta bakes
# omnia-share.ts), which layout.tsx turns into window.__omniaBrief.


def test_build_brief_payload_trims_to_narration_fields() -> None:
    """Only palette / fonts(display,text) / section names / motion survive — and
    the trimmed payload narrates IDENTICALLY to the full brief."""
    payload = build_brief_payload(_FULL_BRIEF)
    assert payload is not None
    assert set(payload) <= {"palette", "fonts", "sections", "motion"}
    assert payload["palette"] == _FULL_BRIEF["palette"]
    assert payload["fonts"] == {"display": "Playfair Display", "text": "Inter"}
    # sections reduced to {name} only (the `id` is dropped), order preserved.
    assert payload["sections"] == [
        {"name": n} for n in ("Геро", "Меню", "О нас", "Галерея", "Контакты")
    ]
    assert payload["motion"] == _FULL_BRIEF["motion"]
    # Falsifiable: the trimmed carrier yields the same lines as the full brief.
    assert brief_lines(payload) == brief_lines(_FULL_BRIEF)


def test_build_brief_payload_none_when_nothing_to_narrate() -> None:
    assert build_brief_payload(None) is None
    assert build_brief_payload({}) is None
    assert build_brief_payload({"palette": {"x": "not-a-hex"}, "sections": []}) is None


def test_inject_brief_module_writes_consumable_ts_module() -> None:
    files = {"src/app/page.tsx": "export default function Page(){return null}"}
    out = inject_brief_module(files, _FULL_BRIEF)
    assert BRIEF_MODULE_PATH in out
    mod = out[BRIEF_MODULE_PATH]
    assert "export const brief: Record<string, unknown> | null = " in mod
    # The literal is valid JSON equal to the trimmed payload.
    json_text = mod.split(" = ", 1)[1].rsplit(";", 1)[0].strip()
    assert json.loads(json_text) == build_brief_payload(_FULL_BRIEF)
    # Untouched sibling files carry through.
    assert out["src/app/page.tsx"] == files["src/app/page.tsx"]


def test_inject_brief_module_is_side_effect_free() -> None:
    files = {"a.ts": "x"}
    out = inject_brief_module(files, _FULL_BRIEF)
    assert BRIEF_MODULE_PATH not in files  # input dict not mutated
    assert BRIEF_MODULE_PATH in out


def test_inject_brief_module_noop_keeps_template_default() -> None:
    """No narration lines → module NOT written (the template's null default,
    which renders the reveal inert, is left in place)."""
    files = {"a.ts": "x"}
    assert inject_brief_module(files, None) == files
    assert inject_brief_module(files, {"palette": {"x": "bad"}}) == files
    assert BRIEF_MODULE_PATH not in inject_brief_module(files, {})
