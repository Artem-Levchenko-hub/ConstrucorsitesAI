"""Tests for the deterministic contrast guard (services/contrast_guard.py).

Pins the prod bug 2026-06-01: a freeform page declared a readable cream/ink
palette in :root but hardcoded a dark body background, shipping near
black-on-black. The guard must repair that to WCAG-AA readable, leave already
readable pages untouched, and never raise.
"""

from __future__ import annotations

from omnia_api.sections.palettes import contrast_ratio
from omnia_api.services.contrast_guard import (
    _body_colors,
    _parse_root_vars,
    enforce_contrast,
    repair_html,
)


def _effective_body_contrast(html: str) -> float:
    root = _parse_root_vars(html)
    start = html.index("body {")
    inner = html[start + 6 : html.index("}", start)]
    fg, bg = _body_colors(inner, root)
    return contrast_ratio(fg or "#000000", bg or "#ffffff")


# The exact prod failure: cream/ink palette declared, dark bg hardcoded.
_SUSHI = """<!DOCTYPE html><html><head><style>
:root { --bg: #F5F1E8; --fg: #1F1F1F; --brand: #DC2626; }
body { font-family: 'DM Sans'; color: var(--fg); background: #0F1419; }
</style></head><body><h1>Сакура</h1></body></html>"""


def test_dark_on_dark_is_repaired_to_aa() -> None:
    assert _effective_body_contrast(_SUSHI) < 4.5  # precondition: broken
    fixed, changed = repair_html(_SUSHI)
    assert changed is True
    assert _effective_body_contrast(fixed) >= 4.5  # now readable


def test_repair_prefers_declared_palette() -> None:
    # Should reuse the model's own --bg (#F5F1E8), not invent a colour.
    fixed, _ = repair_html(_SUSHI)
    start = fixed.index("body {")
    inner = fixed[start + 6 : fixed.index("}", start)]
    fg, bg = _body_colors(inner, _parse_root_vars(fixed))
    assert bg == "#f5f1e8"
    assert fg == "#1f1f1f"


def test_already_readable_is_untouched() -> None:
    good = (
        "<html><head><style>body { color: #111827; background: #ffffff; }"
        "</style></head><body>hi</body></html>"
    )
    fixed, changed = repair_html(good)
    assert changed is False
    assert fixed == good


def test_light_on_light_is_repaired() -> None:
    bad = (
        "<html><head><style>body { color: #F8FAFC; background: #FFFFFF; }"
        "</style></head><body>hi</body></html>"
    )
    fixed, changed = repair_html(bad)
    assert changed is True
    assert _effective_body_contrast(fixed) >= 4.5


def test_gradient_bg_is_left_alone() -> None:
    # Cannot read a gradient → stay conservative, do not touch.
    grad = (
        "<html><head><style>body { color: #222; "
        "background: linear-gradient(135deg,#fff,#eee); }"
        "</style></head><body>hi</body></html>"
    )
    _, changed = repair_html(grad)
    assert changed is False


def test_no_body_rule_is_noop() -> None:
    html = "<html><head><style>.x{color:#000}</style></head><body>hi</body></html>"
    fixed, changed = repair_html(html)
    assert changed is False
    assert fixed == html


def test_idempotent() -> None:
    once, _ = repair_html(_SUSHI)
    twice, changed2 = repair_html(once)
    assert changed2 is False
    assert twice == once


def test_enforce_contrast_only_touches_html() -> None:
    files = {
        "index.html": _SUSHI,
        "assets/app.css": "body { color: #111; background: #000; }",
        "data.json": "{}",
    }
    out = enforce_contrast(files)
    assert out["index.html"] != _SUSHI  # html repaired
    assert out["assets/app.css"] == files["assets/app.css"]  # css untouched
    assert out["data.json"] == "{}"


def test_enforce_contrast_never_raises_on_garbage() -> None:
    out = enforce_contrast({"index.html": "<body {{{ color:::"})
    assert isinstance(out, dict)
