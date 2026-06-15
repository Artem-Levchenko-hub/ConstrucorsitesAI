"""Tests for the deterministic share-card injector (P2 — branded unfurl).

The entity template ships a static «Omnia project» <head>, so EVERY shared
`/p/<slug>` link unfurls as a generic, brand-less card — the viral first
impression (pillar 4) is dead. `share_meta` deterministically derives a small
`{title, tagline, accent}` card from the project name + prompt + palette and
injects it as `src/app/omnia-share.ts`, which the template's generateMetadata +
opengraph-image route consume. Model-independent, one lever, all niches.
"""

from __future__ import annotations

import json

from omnia_api.services.share_meta import (
    ShareCard,
    build_share_card,
    inject_share_module,
)

_MODULE_PATH = "src/app/omnia-share.ts"


def test_real_name_becomes_the_title() -> None:
    card = build_share_card(
        name="Кофейня Лофт", prompt="сайт кофейни с меню", accent_hex="#b45309"
    )
    assert card.title == "Кофейня Лофт"
    assert card.accent == "#b45309"
    # A recognised niche becomes the supporting kicker line.
    assert card.tagline


def test_generic_name_falls_back_to_niche_then_prompt() -> None:
    # «Untitled»-class names are useless on a share card → derive from the idea.
    card = build_share_card(
        name="Untitled", prompt="сайт стоматологической клиники", accent_hex="#0d9488"
    )
    assert card.title and card.title.lower() != "untitled"


def test_blank_everything_is_safe() -> None:
    card = build_share_card(name="  ", prompt="", accent_hex="not-a-color")
    assert card.title == "Omnia project"
    # Invalid accent falls back to a sane brand default, never raw garbage.
    assert card.accent.startswith("#") and len(card.accent) == 7


def test_motion_dna_derived_from_niche() -> None:
    # MOTION-half of APP-DNA (mirrors the entities --omnia-ease/--omnia-dur, but
    # here it's DETERMINISTIC — no model). The niche character picks one of three
    # entrance tempi; the drizzle `brandTokens` turns it into the CSS-var pair.
    calm = build_share_card("Галерея Лофт", "сайт галереи современного искусства", "#000000")
    assert calm.motion == "calm"  # luxe / media / content → slow, smooth entrance
    snappy = build_share_card("Кофейня", "интернет-магазин кофе с доставкой", "#b45309")
    assert snappy.motion == "snappy"  # shop / lifestyle / e-com → quick, springy
    precise = build_share_card("ФинСервис", "crm для финансовой компании", "#0d9488")
    assert precise.motion == "precise"  # fintech / b2b / saas → crisp, composed


def test_default_motion_is_precise() -> None:
    # An unclassifiable / blank brief lands on the professional default — the same
    # tempo the enterprise drizzle default ships with.
    assert build_share_card("  ", "", "not-a-color").motion == "precise"
    assert build_share_card("X", "нечто непонятное", "#111111").motion == "precise"


def test_inject_writes_a_valid_ts_module() -> None:
    card = ShareCard(
        title='Студия «Алёна"s»', tagline="салон красоты", accent="#db2777", motion="snappy"
    )
    files = inject_share_module({"src/app/page.tsx": "x"}, card)
    assert _MODULE_PATH in files
    src = files[_MODULE_PATH]
    # The payload is JSON-encoded so quotes/cyrillic/backslashes can't break the
    # TS literal; the object must round-trip exactly.
    start = src.index("{")
    end = src.rindex("}") + 1
    parsed = json.loads(src[start:end])
    assert parsed == {
        "title": 'Студия «Алёна"s»',
        "tagline": "салон красоты",
        "accent": "#db2777",
        "motion": "snappy",
    }
    assert "as const" in src
    # Original files are preserved.
    assert files["src/app/page.tsx"] == "x"


def test_inject_does_not_mutate_input() -> None:
    original = {"src/app/page.tsx": "x"}
    inject_share_module(original, build_share_card("X", "y", "#111111"))
    assert _MODULE_PATH not in original
